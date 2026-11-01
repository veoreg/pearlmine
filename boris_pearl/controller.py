"""Offline integrated control cycle. No pool transport or real shares are submitted."""
import json
from pathlib import Path
import threading
import time

from .core import NS, Planner, save_state
from .ipc import ProcessWorker, WorkerError, WorkerInterrupted


class Controller:
    """Connect durable grants, process cancellation, safety and immutable routing.

    Synthetic and CUDA adapters share the durable lifecycle. Official proof
    verification is required by CUDA integration; pool submission is separate.
    """

    def __init__(self, config, router, guard, state_path, *, worker=None,
                 clock=time.time, monotonic=time.monotonic, sample=lambda: None, persist=save_state, pacer=None):
        if config.identity != router.config.identity:
            raise ValueError("Router and scheduler payout policies differ")
        self.config, self.router, self.guard = config, router, guard
        self.state_path = Path(state_path)
        self.worker = worker or ProcessWorker()
        self.clock, self.sample, self.persist = clock, sample, persist
        self.monotonic = monotonic
        self.monotonic_deadline = None
        self.stopped = threading.Event()
        self.started = False
        self.last_error = None
        self.pacer = pacer
        self.watchdog = None
        self.stop_reason = None
        if self.state_path.exists():
            self.planner = Planner.restore(config, json.loads(self.state_path.read_text(encoding="utf-8")))
            # Commit recovery before any new dispatch, while remaining stopped.
            self.persist(self.state_path, self.planner)
        else:
            self.planner = Planner(config)

    def start(self, acknowledged_policy):
        if self.stopped.is_set() or self.started:
            raise ValueError("Create a new explicit session after stop; no automatic restart")
        if acknowledged_policy != self.config.identity:
            raise ValueError("Acknowledge the exact disclosed payout and fee policy before start")
        if not self.config.user_wallet or not self.config.user_pool:
            raise ValueError("A user payout address and pool are required")
        if self.guard.stop_at <= self.clock():
            raise ValueError("Explicit stop deadline has already passed")
        self.guard.start()
        self.monotonic_deadline = self.monotonic() + max(0, self.guard.stop_at - self.clock())
        self.started = True
        self.watchdog = threading.Thread(target=self._watch_deadline,name="boris-stop-deadline",daemon=True)
        self.watchdog.start()

    def _watch_deadline(self):
        while not self.stopped.is_set():
            remaining=self.monotonic_deadline-self.monotonic()
            if remaining<=0:
                self.stop("stop deadline")
                return
            self.stopped.wait(min(.1,remaining))

    def stop(self, reason="manual stop"):
        if not self.stopped.is_set():
            self.stop_reason=reason
        self.stopped.set()
        self.guard.stop()
        self.worker.close()
        if self.watchdog and self.watchdog is not threading.current_thread():
            self.watchdog.join(timeout=1)

    def _permit(self):
        if self.stopped.is_set() or not self.started:
            return "stopped"
        if self.monotonic() >= self.monotonic_deadline:
            self.guard.stop()
            return "stop deadline"
        telemetry = self.sample()
        return self.guard.reason(self.clock(), telemetry)

    def _ready(self, job):
        return (job is not None and self.router.sessions.get(job.session.identifier) == job.session and
                self.router.jobs.get(self.router.key(job)) == job)

    def tick(self, user_job, developer_job=None):
        reason = self._permit()
        if reason:
            if self.guard.stopped:
                self.stop(reason)
            return {"state": "paused", "reason": reason}
        if not self._ready(user_job) or user_job.session.owner != "user":
            return {"state": "paused", "reason": "no authorized user job"}
        if self.pacer:
            reason = self.pacer.ready(self._permit)
            if reason:
                if self.guard.stopped:
                    self.stop(reason)
                return {"state":"paused","reason":reason,"submitted":False}
        developer_ready = self._ready(developer_job) and developer_job.session.owner == "developer"
        work_started = self.monotonic()
        try:
            grant = self.planner.reserve(running=True, user_ready=True, dev_ready=developer_ready,
                                         min_developer_ns=getattr(self.worker, "minimum_slice_ns", 0))
            job = developer_job if grant.owner == "developer" else user_job
            self.persist(self.state_path, self.planner)  # write-ahead: failure must prevent execute
            if self.worker.process is None:
                self.worker.start(permit=self._permit)
            reason = self._permit()
            if reason:
                raise WorkerInterrupted(reason)
            remaining_ns = int(max(0, self.guard.stop_at - self.clock()) * NS)
            budget_ns = min(grant.budget_ns, remaining_ns)
            if budget_ns <= 0:
                raise WorkerError("stop deadline")
            def permit_during_work():
                if self.router.sessions.get(user_job.session.identifier)!=user_job.session:
                    return "user session/job changed"
                if not self._ready(user_job):
                    return "user job superseded"
                if not self._ready(job):
                    return "developer job retired" if job.session.owner=="developer" else "active session/job changed"
                return self._permit()
            key = (job.session.identifier, job.session.epoch, job.job_id, job.header_hash)
            result = self.worker.execute(key, budget_ns, permit=permit_during_work)
            if hasattr(result, "proofs"):
                active, proofs = result.active_ns, result.proofs
            else:
                active, proof = result
                proofs = [proof]
            reason = permit_during_work()
            if reason:
                raise WorkerInterrupted(reason)
            self.planner.complete(grant, active)
            self.persist(self.state_path, self.planner)
            # Verification/routing is local. A separately gated coordinator may submit.
            destinations = [self.router.route(job, proof) for proof in proofs]
            reason = permit_during_work()
            if reason:
                raise WorkerInterrupted(reason)
            return {"state": "offline-result", "owner": job.session.owner,
                    "session": job.session.identifier, "active_ns": active,
                    "simulated_active_ns": active if not hasattr(result, "proofs") else None,
                    "verified_proofs": len(destinations),
                    "work_stats":getattr(result,"stats",None),
                    "submitted": False}
        except Exception as exc:
            self.last_error = str(exc)
            recoverable=(isinstance(exc,WorkerInterrupted) and str(exc) in ("user job superseded","developer job retired")
                and self.router.sessions.get(user_job.session.identifier)==user_job.session and self._permit() is None)
            self.worker.close()
            recovered=True
            # Stop child first; then waive uncertain reservations, retaining reserved debit.
            if self.planner.pending:
                self.planner = Planner.restore(self.config, self.planner.snapshot())
                try:
                    self.persist(self.state_path, self.planner)
                except (OSError, ValueError):
                    recovered=False # never dispatch unless recovery is durable
            if recoverable and recovered:
                self.worker.reset_for_job_change()
                return {"state":"job-retired","reason":str(exc),"submitted":False}
            self.stop(str(exc))
            return {"state": "stopped", "reason": self.last_error, "submitted": False}
        finally:
            if self.pacer:
                self.pacer.record(work_started)
