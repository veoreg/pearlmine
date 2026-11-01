"""Fail-closed adapter to a separately built pinned official verifier process."""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import threading

from .proof_lab import bincode_proof

REVISION = "3fe226761a139a9652b8f28a6464a4bbc25986c8"


class VerificationBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedProof:
    wire: bytes
    digest: str
    request_sha256: str


@dataclass(frozen=True)
class ProofContext:
    header76: str
    nbits_override: int
    certificate_version: int = 3


class OfficialVerifierCallback:
    """Router-compatible official gate, with immutable header/target per job.

    Future worker adapters can pass full candidate JSON to Router.route. There
    is no synthetic fallback and no network submit implementation here.
    """
    def __init__(self, executable, expected_sha256):
        self.executable = executable
        self.expected_sha256 = expected_sha256
        self.contexts = {}
        self.verified_wire = {}
        self.last_error = None
        self.lock = threading.RLock()
        self.verified_input = {}

    def bind(self, job, context):
        with self.lock:
            self._bind(job, context)

    def _bind(self, job, context):
        if (context.certificate_version != 3 or job.certificate_version != 3 or
                type(context.nbits_override) is not int or not 0 < context.nbits_override <= 0xFFFFFFFF):
            raise ValueError("Invalid immutable verifier job context")
        header = bytes.fromhex(context.header76)
        if len(header) != 76 or sha256(header).hexdigest() != job.header_hash:
            raise ValueError("Verifier context does not match the job header")
        if job in self.contexts and self.contexts[job] != context:
            raise ValueError("Cannot replace a job's verifier target/header")
        if job not in self.contexts and len(self.contexts) >= 64:
            raise ValueError("Verifier context limit reached; retire stale jobs")
        self.contexts[job] = context

    def retire(self, job):
        with self.lock:
            self.contexts.pop(job, None)
            self.verified_wire.pop(job, None)
            self.verified_input.pop(job, None)

    def canonical_bytes(self, job, original):
        with self.lock:
            if (job not in self.verified_wire or
                    self.verified_input.get(job) != sha256(original).digest()):
                raise VerificationBlocked("No officially verified wire bytes for this exact candidate/job")
            return self.verified_wire[job]

    def __call__(self, job, proof):
        self.last_error = None
        try:
            context = self.contexts.get(job)
            if context is None or not isinstance(proof, bytes) or len(proof) > 2_000_000:
                raise VerificationBlocked("No bound verifier context for this session/job")
            candidate = json.loads(proof)
            if (candidate.get("header76") != context.header76 or
                    candidate.get("nbits_override") != context.nbits_override or
                    candidate.get("certificate_version") != context.certificate_version):
                raise VerificationBlocked("Proof header/target/certificate differs from the originating job")
            result = verify(candidate, self.executable, self.expected_sha256)
            with self.lock:
                if self.contexts.get(job) != context:
                    raise VerificationBlocked("Verifier job retired while proof was being checked")
                self.verified_wire[job] = result.wire
                self.verified_input[job] = sha256(proof).digest()
            return True
        except (VerificationBlocked, ValueError, TypeError, KeyError, AttributeError) as exc:
            self.last_error = str(exc)
            return False


def request_bytes(candidate):
    fields = ("certificate_version", "header76", "nbits_override", "expected_digest", "proof")
    return json.dumps({k: candidate[k] for k in fields}, separators=(",", ":"), allow_nan=False).encode()


def accept_response(candidate, payload, returncode, reply):
    if len(reply) > 2_000_000:
        raise VerificationBlocked("Verifier response exceeds limit")
    try:
        response = json.loads(reply)
        if (returncode != 0 or response.get("accepted") is not True or
                response.get("protocol") != "boris-official-verifier/1" or
                response.get("source_revision") != REVISION or
                response.get("request_sha256") != sha256(payload).hexdigest() or
                response.get("digest") != candidate["expected_digest"]):
            raise VerificationBlocked("Official verifier rejected or returned an unbound response")
        wire = bytes.fromhex(response["proof_bincode_hex"])
        if wire != bincode_proof(candidate["proof"]):
            raise VerificationBlocked("Bincode differs from official serialization")
        return VerifiedProof(wire, response["digest"], response["request_sha256"])
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise VerificationBlocked("Malformed official verifier response") from exc


def verify(candidate, executable, expected_sha256):
    path = Path(executable)
    if not path.is_file():
        raise VerificationBlocked("Official verifier binary is absent; offline dependencies/build required")
    if sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise VerificationBlocked("Official verifier binary checksum differs from the reviewed build")
    payload = request_bytes(candidate)
    if len(payload) > 2_000_000:
        raise VerificationBlocked("Offline proof request exceeds limit")
    try:
        result = subprocess.run([str(path.resolve())], input=payload, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationBlocked("Official verifier process failed or timed out") from exc
    return accept_response(candidate, payload, result.returncode, result.stdout)
