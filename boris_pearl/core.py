"""Portable scheduling and immutable work ownership. No networking or GPU calls."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import threading
from functools import wraps

from .config import Config

NS = 1_000_000_000


def locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


def natural(value):
    return type(value) is int and 0 <= value <= (1 << 63) - 1


@dataclass(frozen=True)
class Grant:
    sequence: int
    owner: str
    budget_ns: int
    accrue: bool


class Planner:
    """At most 2% earned active time; bounded slices, no outage catch-up.

    Persist a reservation BEFORE dispatching it. Restore abandons incomplete work
    without charging it again. Completed activity must come from device events,
    never elapsed wall time. CUDA integration supplies device-event durations.
    """

    def __init__(self, config: Config, quantum_ns=NS):
        if not natural(quantum_ns) or not 1 <= quantum_ns <= NS:
            raise ValueError("Quantum must be 1 ns to 1 second")
        self.config = config
        self.quantum = quantum_ns
        self.bps = config.effective_bps
        self.den = 10_000 - self.bps
        self.debt = 0
        self.user_ns = 0
        self.dev_ns = 0
        self.uncertain_dev_ns = 0
        self.sequence = 0
        self.pending = None
        self.cap_ns = quantum_ns
        self.bursting = False

    def reserve(self, *, running, user_ready, dev_ready, min_developer_ns=0, burst_ns=0):
        """burst_ns: a worker whose job switch is expensive pays the fee in rare long turns instead of many short ones."""
        if not natural(min_developer_ns) or min_developer_ns > self.quantum:
            raise ValueError("Invalid minimum developer slice")
        if self.pending:
            raise ValueError("Complete or abandon the previous reservation first")
        if not running or not user_ready:
            return None
        if not dev_ready:
            # Lost fee time is waived; do not repay an outage as a burst.
            self.debt = 0
        self.cap_ns = max(self.cap_ns, 2 * burst_ns)
        budget = min(self.quantum, self.debt // self.den)
        due = bool(budget and budget >= min_developer_ns)
        if burst_ns:
            self.bursting = self.debt // self.den >= burst_ns or (self.bursting and due)
            due = due and self.bursting
        owner = "developer" if self.bps and dev_ready and due else "user"
        if owner == "developer":
            self.debt -= budget * self.den
        else:
            budget = self.quantum
        self.sequence += 1
        self.pending = Grant(self.sequence, owner, budget, bool(self.bps and dev_ready))
        return self.pending

    def complete(self, grant, active_ns):
        if grant != self.pending or grant is None:
            raise ValueError("Unknown or already completed reservation")
        if not natural(active_ns) or active_ns > grant.budget_ns:
            raise ValueError("Activity must fit within the reserved device time")
        if grant.owner == "developer":
            self.dev_ns += active_ns
            self.debt += (grant.budget_ns - active_ns) * self.den
        else:
            self.user_ns += active_ns
            if grant.accrue:
                self.debt = min(self.cap_ns * self.den, self.debt + active_ns * self.bps)
        self.pending = None

    def snapshot(self):
        return {"version": 1, "policy": self.config.identity, "quantum_ns": self.quantum, "cap_ns": self.cap_ns, "bursting": self.bursting,
                "debt": self.debt, "user_ns": self.user_ns, "dev_ns": self.dev_ns,
                "uncertain_dev_ns": self.uncertain_dev_ns, "sequence": self.sequence,
                "pending": asdict(self.pending) if self.pending else None}

    @classmethod
    def restore(cls, config, state):
        if state.get("version") != 1 or state.get("policy") != config.identity:
            raise ValueError("State belongs to another version or payout policy")
        obj = cls(config, state["quantum_ns"])
        for name in ("debt", "user_ns", "dev_ns", "uncertain_dev_ns", "sequence"):
            if not natural(state.get(name)):
                raise ValueError(f"Invalid state field: {name}")
            setattr(obj, name, state[name])
        obj.cap_ns = state.get("cap_ns", obj.quantum)
        if not natural(obj.cap_ns) or obj.cap_ns < obj.quantum:
            raise ValueError("Invalid fee cap")
        obj.bursting = state.get("bursting") is True
        if obj.debt > obj.cap_ns * obj.den:
            raise ValueError("Invalid fee debt")
        pending = state.get("pending")
        if pending is not None:
            grant = Grant(**pending)
            if (grant.sequence != obj.sequence or grant.owner not in ("user", "developer") or
                    not natural(grant.budget_ns) or not 1 <= grant.budget_ns <= obj.quantum or
                    type(grant.accrue) is not bool):
                raise ValueError("Invalid reservation")
            if grant.owner == "developer":
                obj.uncertain_dev_ns += grant.budget_ns
        # Never resume a GPU job or automatically start mining after a restart.
        return obj


def save_state(path, planner):
    """Atomic, flushed write; failure must prevent dispatch of the reservation."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(planner.snapshot(), stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass(frozen=True)
class Session:
    identifier: str
    epoch: int
    owner: str
    pool: str
    wallet: str


@dataclass(frozen=True)
class Job:
    session: Session
    job_id: str
    header_hash: str
    certificate_version: int = 3


class Router:
    """Routes only to the originating authorized session after local verification.

    `verify(job, proof)` must be the pinned consensus verifier in a live adapter.
    Unit tests may use a synthetic verifier. Transport has its own submit gate.
    """

    def __init__(self, config, verify):
        self.config = config
        self.verify = verify
        self.sessions = {}
        self.epochs = {}
        self.jobs = {}
        self.seen = set()
        self.lock = threading.RLock()

    @locked
    def authorize(self, session):
        if (session.owner not in ("user", "developer") or not natural(session.epoch) or
                not isinstance(session.identifier, str) or not 1 <= len(session.identifier) <= 128):
            raise ValueError("Invalid session")
        wallet, pool = ((self.config.user_wallet, self.config.user_pool) if session.owner == "user"
                        else (self.config.developer_wallet, self.config.developer_pool))
        if not wallet or not pool or session.wallet.lower() != wallet.lower() or session.pool != pool:
            raise ValueError("Session destination differs from the disclosed payout policy")
        previous = self.epochs.get(session.identifier)
        if previous is not None and session.epoch <= previous:
            raise ValueError("A reconnect requires a new epoch")
        self.disconnect(session.identifier)
        self.epochs[session.identifier] = session.epoch
        self.sessions[session.identifier] = session

    @locked
    def disconnect(self, identifier):
        self.sessions.pop(identifier, None)
        retire = getattr(self.verify, "retire", None)
        if callable(retire):
            for job in list(self.jobs.values()):
                if job.session.identifier == identifier:
                    retire(job)
        self.jobs = {k: j for k, j in self.jobs.items() if j.session.identifier != identifier}
        self.seen = {key for key in self.seen if key[0][0] != identifier}

    @staticmethod
    def key(job):
        return (job.session.identifier, job.session.epoch, job.job_id)

    @locked
    def register(self, job, clean=False):
        if self.sessions.get(job.session.identifier) != job.session:
            raise ValueError("Job from an unauthorized session")
        if type(job.certificate_version) is not int or job.certificate_version != 3:
            raise ValueError("Update required: unsupported consensus certificate version")
        if (not isinstance(job.job_id, str) or not 1 <= len(job.job_id) <= 256 or
                not isinstance(job.header_hash, str) or len(job.header_hash) != 64 or
                any(c not in "0123456789abcdef" for c in job.header_hash)):
            raise ValueError("Missing job identifier/header hash")
        if clean:
            retire = getattr(self.verify, "retire", None)
            if callable(retire):
                for previous in list(self.jobs.values()):
                    if previous.session == job.session and previous != job:
                        retire(previous)
            self.jobs = {k: j for k, j in self.jobs.items() if j.session != job.session}
            self.seen = {k for k in self.seen if k[0][0] != job.session.identifier}
        key = self.key(job)
        previous = self.jobs.get(key)
        if previous and previous != job:
            raise ValueError("Reused job ID with another header")
        self.jobs[key] = job

    def route(self, job, proof):
        key = self.key(job)
        if not isinstance(proof, bytes) or not proof or len(proof) > 2_000_000:
            raise ValueError("Invalid proof size/type")
        fingerprint = (key, sha256(proof).digest())
        with self.lock:
            if self.sessions.get(job.session.identifier) != job.session or self.jobs.get(key) != job:
                raise ValueError("Stale or foreign session/job")
            if fingerprint in self.seen:
                raise ValueError("Duplicate share")
            if len(self.seen) >= 8190:
                raise ValueError("Share registry limit; retire this job before more work")
        if self.verify(job, proof) is not True:
            raise ValueError("Local proof verification failed")
        # A socket reader may retire the session/job while an external verifier runs.
        with self.lock:
            if self.sessions.get(job.session.identifier) != job.session or self.jobs.get(key) != job:
                raise ValueError("Session/job became stale during verification")
            if fingerprint in self.seen:
                raise ValueError("Duplicate share")
            canonical = getattr(self.verify, "canonical_bytes", None)
            if callable(canonical):
                wire = canonical(job, proof)
                if not isinstance(wire, bytes) or not wire or len(wire) > 2_000_000:
                    raise ValueError("Invalid canonical proof bytes")
                canonical_fingerprint = (key, sha256(wire).digest())
                if canonical_fingerprint in self.seen:
                    raise ValueError("Duplicate canonical share")
                self.seen.add(canonical_fingerprint)
            self.seen.add(fingerprint)
        return job.session  # never replace this with the currently selected fee/user session
