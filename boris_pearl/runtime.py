"""Explicitly started orchestration. No UI entry point enables live mining yet.

External connections, submissions and live CUDA jobs each retain their separate
endpoint/configuration/device/deadline permission gates. Import/construct is idle.
"""
from .cuda_worker import WorkerJob, FIXED_NONCE
import secrets
from .pool import PoolError


class PoolCoordinator:
    def __init__(self, controller, user_client, developer_client=None, *, shape=(131072, 131072, 4096), device_index=0, backend="dp4a"):
        self.controller = controller
        self.clients = {"user":user_client, "developer":developer_client}
        if backend not in ("dp4a","wmma-int8","mma-int8"):
            raise ValueError("Unknown explicit CUDA backend")
        self.backend=backend
        self.shape, self.device_index = shape, device_index
        self.bound = {}
        self.started = False
        self.last_error = None
        for owner, client in self.clients.items():
            if client and (client.config.identity != controller.config.identity or
                           client.router is not controller.router or client.template.owner != owner):
                raise ValueError("Coordinator owner/router/payout policy mismatch")

    def start(self, acknowledged_policy):
        if self.started or acknowledged_policy != self.controller.config.identity:
            raise ValueError("An explicit new start must acknowledge the exact disclosed policy")
        try:
            self.controller.start(acknowledged_policy)
            self.clients["user"].connect()
            developer = self.clients["developer"]
            if developer and self.controller.config.effective_bps:
                try:
                    developer.connect()
                except (OSError, PoolError) as exc:
                    self.last_error = "Developer pool unavailable; fee waived: " + str(exc)
            self.started = True
        except Exception:
            self.stop()
            raise

    def _job(self, owner):
        client = self.clients[owner]
        if not client:
            return None
        with client.lock:
            current = client.current if client.authorized and not client.closed.is_set() else None
        previous = self.bound.get(owner)
        if previous and (not current or previous != current.job):
            self.controller.worker.retire(previous)
            self.bound.pop(owner, None)
        if current and previous != current.job:
            m, n, k = self.shape
            self.controller.worker.bind_job(current.job, WorkerJob(current.header76, current.target,
                    m=m, n=n, k=k, device_index=self.device_index,backend=self.backend,
                    nonce=secrets.randbits(63) if getattr(self.controller.worker,"live_consent",None) is not None else FIXED_NONCE))
            self.bound[owner] = current.job
        return current.job if current else None

    def tick(self, *, submit=False):
        if not self.started:
            return {"state":"paused", "reason":"explicit start required", "submitted":False}
        try:
            user = self._job("user")
            developer = self._job("developer") if self.controller.config.effective_bps else None
            result = self.controller.tick(user, developer)
            if submit and result.get("verified_proofs"):
                reason=self.controller._permit()
                if reason:
                    raise PoolError("Work permission expired before submit: "+reason)
                if result["verified_proofs"] != 1:
                    raise PoolError("This coordinator requires one canonical proof per chunk")
                job = user if result["owner"] == "user" else developer
                client = self.clients[result["owner"]]
                try:
                    result["pool_result"] = client.submit(job)["status"]
                except PoolError:
                    # The job moved on while the proof was being built: not sent, and no reason to stop mining.
                    result["pool_result"] = "stale"
                result["submitted"] = result["pool_result"] in ("accepted", "rejected", "uncertain")
            if self.controller.stopped.is_set():
                self.stop()
            return result
        except Exception as exc:
            self.last_error = str(exc)
            self.stop()
            return {"state":"stopped", "reason":self.last_error,"submitted":False}

    def stop(self):
        self.controller.stop()
        for client in self.clients.values():
            if client:
                client.close()
        for job in self.bound.values():
            self.controller.worker.retire(job)
        self.bound.clear()
        self.started = False
