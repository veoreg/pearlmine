"""Controlled Rust/CUDA worker adapter. Default mode permits only local fixtures."""
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import time

from .ipc import ProcessWorker, WorkerError, WorkerInterrupted
from .official_bridge import ProofContext

FIXED_NONCE = 0x4B41545F6E6F6E63


def compact_target(target):
    value = int(target, 16)
    if not 0 < value < 1 << 256:
        raise ValueError("Target outside U256")
    size = (value.bit_length() + 7) // 8
    word = value << (8 * (3 - size)) if size <= 3 else value >> (8 * (size - 3))
    if word & 0x800000:
        word >>= 8
        size += 1
    return (size << 24) | word


@dataclass(frozen=True)
class WorkerJob:
    header76: str
    target: str
    m: int = 256
    n: int = 256
    k: int = 2048
    device_index: int = 0
    nonce: int = FIXED_NONCE
    backend: str = "dp4a"

    def validate(self, job):
        header = bytes.fromhex(self.header76)
        if len(header) != 76 or sha256(header).hexdigest() != job.header_hash:
            raise ValueError("Worker header differs from originating job")
        if (len(self.target) != 64 or any(c not in "0123456789abcdef" for c in self.target) or
                type(self.device_index) is not int or not 0 <= self.device_index <= 31 or
                any(type(v) is not int or not 64 <= v <= 131072 or v % 64 for v in (self.m, self.n)) or
                type(self.k) is not int or not 2048 <= self.k <= 4096 or self.k % 128):
            raise ValueError("Worker shape/target/device invalid")
        compact_target(self.target)
        if self.backend not in ("dp4a","wmma-int8","mma-int8"):
            raise ValueError("Unknown explicit CUDA backend")


@dataclass(frozen=True)
class LiveRunConsent:
    """Reserved for an explicit future operator Start; never created by automated tests."""
    config_identity: str
    device_index: int
    stop_at: float


@dataclass(frozen=True)
class ChunkResult:
    active_ns: int
    proofs: list[bytes]
    stats: dict | None = None


class CudaWorker:
    minimum_slice_ns = 100_000_000

    def __init__(self, binary, expected_sha256, verifier, *, config_identity=None, live_consent=None,
                 large_fixed_fixture=False, local_search_test=False, chunk_tiles=1,
                 expected_device_uuid=None, cpu_threads=2):
        path = Path(binary)
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected_sha256:
            raise ValueError("GPU worker must match the recorded reviewed build SHA256")
        argv = [str(path.resolve())]
        self.binary,self.expected_sha256=path,expected_sha256
        self.live_consent = live_consent
        if (type(cpu_threads) is not int or not 1<=cpu_threads<=8 or type(chunk_tiles) is not int or
                not 1<=chunk_tiles<=64 or sum(bool(v) for v in (live_consent,large_fixed_fixture,local_search_test))>1):
            raise ValueError("Worker CPU/batch/mode limits invalid")
        self.chunk_tiles, self.expected_device_uuid = chunk_tiles, expected_device_uuid
        if local_search_test:
            argv.append("--local-search-test")
        if large_fixed_fixture:
            if live_consent is not None:
                raise ValueError("Large fixed test and live modes cannot be combined")
            argv.append("--large-fixed-fixture")
        if live_consent is not None:
            if not config_identity or live_consent.config_identity != config_identity:
                raise ValueError("Live run consent differs from disclosed configuration")
            if (type(live_consent.stop_at) not in (int, float) or
                    not math.isfinite(live_consent.stop_at) or live_consent.stop_at <= time.time()):
                raise ValueError("Live run consent requires an unexpired explicit stop deadline")
            if not expected_device_uuid:
                raise ValueError("Live worker must bind the actual monitored GPU UUID")
            argv.append("--live")
        self.transport = ProcessWorker(argv, expected_mode="cuda-baseline", env=dict(os.environ,RAYON_NUM_THREADS=str(cpu_threads)))
        self.verifier = verifier
        self.bindings = {}
        self.checkpoints = {}
        self.active_binding = None

    @property
    def process(self):
        return self.transport.process

    def start(self, timeout=5, permit=None):
        if sha256(self.binary.read_bytes()).hexdigest()!=self.expected_sha256:
            raise WorkerError("GPU executable changed before process start")
        self.transport.start(timeout, permit)

    def reset_for_job_change(self):
        self.transport.reset_for_job_change()
        self.active_binding=None

    def bind_job(self, job, spec):
        spec.validate(job)
        if self.live_consent is not None and spec.device_index != self.live_consent.device_index:
            raise ValueError("Device differs from explicit live consent")
        binding = sha256(json.dumps([job.session.identifier, job.session.epoch, job.job_id,
                                    job.header_hash, spec.__dict__], sort_keys=True).encode()).hexdigest()
        key = (job.session.identifier, job.session.epoch, job.job_id, job.header_hash)
        if key in self.bindings and self.bindings[key] != (job, spec, binding):
            raise ValueError("Cannot replace a worker job binding")
        if key not in self.bindings and len(self.bindings) >= 32:
            raise ValueError("Worker job registry limit; retire stale jobs")
        self.bindings[key] = (job, spec, binding)
        self.verifier.bind(job, ProofContext(spec.header76, compact_target(spec.target)))

    def retire(self, job):
        key = (job.session.identifier, job.session.epoch, job.job_id, job.header_hash)
        self.bindings.pop(key, None)
        self.checkpoints.pop(key,None)
        self.verifier.retire(job)

    def execute(self, key, budget_ns, *, timeout=10, permit=None):
        if self.live_consent is not None:
            timeout = max(timeout, 180)  # A real hit includes full proof generation inside this chunk.
        def allowed():
            if self.live_consent is not None and time.time() >= self.live_consent.stop_at:
                return "explicit live consent expired"
            return permit() if permit else None
        reason=allowed()
        if reason:
            self.close()
            raise WorkerInterrupted(reason)
        bound=self.bindings.get(key)
        if bound is None:
            reason=allowed()
            if reason:
                self.close()
                raise WorkerInterrupted(reason)
            raise WorkerError("No immutable job bound to CUDA worker")
        job,spec,binding=bound
        if budget_ns < self.minimum_slice_ns:
            return ChunkResult(0, [])
        if self.active_binding != binding:
            params = dict(spec.__dict__, binding=binding, certificate_version=job.certificate_version,
                          job_id=job.job_id, session_id=job.session.epoch, generation=job.session.epoch)
            if key in self.checkpoints:
                params.update(resume_nonce=self.checkpoints[key][0],resume_cursor=self.checkpoints[key][1])
            prepared = self.transport.request("prepare", params, timeout=120 if self.live_consent is not None else 30, permit=allowed)
            if prepared.get("binding") != binding or prepared.get("nbits_share") != compact_target(spec.target):
                raise WorkerError("Prepared job/target does not match originating session")
            if self.expected_device_uuid is not None and prepared.get("device_uuid") != self.expected_device_uuid:
                self.close()
                raise WorkerError("CUDA device differs from the GPU monitored by NVML")
            self.active_binding = binding
        result = self.transport.request("chunk", {"binding": binding, "budget_ns": budget_ns,
                    "max_tiles":self.chunk_tiles}, timeout=timeout, permit=allowed)
        active = result.get("active_ns")
        if (result.get("binding") != binding or result.get("canary_ok") is not True or
                type(active) is not int or not 0 <= active <= budget_ns or
                result.get("kernel_variant") != {"dp4a":4,"wmma-int8":16,"mma-int8":32}[spec.backend]):
            raise WorkerError("CUDA activity or canary result invalid")
        self.minimum_slice_ns = min(1_000_000_000, max(self.minimum_slice_ns, active * 2))
        total,nonce,tile,done=(result.get(k) for k in ("total_tiles","nonce","tile","tiles_this_chunk"))
        tile_count=spec.m*spec.n//128
        previous=self.checkpoints.get(key,(spec.nonce,0,0))[2]
        if (any(type(x) is not int for x in (total,nonce,tile,done)) or
                not 1<=done<=self.chunk_tiles or total!=previous+done or nonce<spec.nonce or
                not 0<=tile<tile_count or (nonce-spec.nonce)*tile_count+tile+1!=total):
            self.close()
            raise WorkerError("Worker nonce/cursor continuity mismatch")
        self.checkpoints[key]=(nonce,tile+1,total)
        candidate = result.get("candidate")
        proofs = [] if candidate is None else [json.dumps(candidate, separators=(",", ":")).encode()]
        stats = {k:result.get(k) for k in ("nonce","tile","total_tiles","tiles_this_chunk","nonce_changes",
                 "cache_hits","device_bytes","workspace_reused","kernel_variant")}
        return ChunkResult(active, proofs, stats)

    def close(self):
        self.transport.close()
