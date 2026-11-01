"""Bounded local process pipes; portable to Windows and Linux, no sockets/services."""
import json
from queue import Empty, Full, Queue
import struct
import subprocess
import sys
import threading
import time

MAX_FRAME = 2_000_000


class WorkerError(RuntimeError):
    pass


class WorkerInterrupted(WorkerError):
    pass


def read_frame(stream):
    header = stream.read(4)
    if not header:
        raise EOFError("Worker pipe closed")
    if len(header) != 4:
        raise WorkerError("Truncated frame header")
    size = struct.unpack(">I", header)[0]
    if not 1 <= size <= MAX_FRAME:
        raise WorkerError("Worker frame exceeds size limit")
    payload = stream.read(size)
    if len(payload) != size:
        raise WorkerError("Truncated worker frame")
    try:
        value = json.loads(payload)
    except (UnicodeError, ValueError) as exc:
        raise WorkerError("Invalid worker JSON") from exc
    if not isinstance(value, dict):
        raise WorkerError("Worker frame must be an object")
    return value


def write_frame(stream, value):
    payload = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if not 1 <= len(payload) <= MAX_FRAME:
        raise WorkerError("Outgoing worker frame exceeds size limit")
    stream.write(struct.pack(">I", len(payload)) + payload)
    stream.flush()


class ProcessWorker:
    """Only the child's inherited pipes carry commands; no listening endpoint.

    Synthetic and reviewed CUDA workers have distinct exact handshakes.
    An explicit CUDA adapter is required; there is no automatic live fallback.
    """

    def __init__(self, argv=None, *, expected_mode="offline-synthetic", env=None):
        self.argv = argv or [sys.executable, "-m", "boris_pearl.offline_worker"]
        self.env = env
        self.process = None
        self.messages = Queue(maxsize=8)
        self.reader = None
        self.request_id = 0
        self.closed = False
        self.close_lock = threading.RLock()
        if expected_mode not in ("offline-synthetic", "cuda-baseline", "telemetry-nvml", "opencl-lab"):
            raise ValueError("Unsupported worker mode")
        self.expected_mode = expected_mode

    def _read(self):
        try:
            while True:
                self.messages.put_nowait(read_frame(self.process.stdout))
        except (EOFError, OSError, WorkerError, Full) as exc:
            try:
                self.messages.put_nowait(exc)
            except Full:
                pass

    def _receive(self, timeout, permit=None):
        end = time.monotonic() + timeout
        while True:
            if permit:
                reason = permit()
                if reason:
                    self.close()
                    raise WorkerInterrupted(reason)
            remaining = end - time.monotonic()
            if remaining <= 0:
                self.close()
                raise WorkerError("Worker response timeout")
            try:
                result = self.messages.get(timeout=min(0.025, remaining))
            except Empty:
                continue
            if permit:
                reason=permit()
                if reason:
                    self.close()
                    raise WorkerInterrupted(reason)
            if isinstance(result, BaseException):
                self.close()
                raise WorkerError(str(result)) from result
            return result

    def start(self, timeout=3, permit=None):
        if self.closed or self.process is not None:
            raise WorkerError("Worker already started or permanently closed")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.process = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, creationflags=flags, env=self.env)
        self.reader = threading.Thread(target=self._read, name="boris-worker-pipe", daemon=True)
        self.reader.start()
        ready = self._receive(timeout, permit)
        if ready != {"type": "ready", "protocol": 1, "mode": self.expected_mode}:
            self.close()
            raise WorkerError("Unsupported worker handshake; live mining is disabled")

    def request(self, command, params, *, timeout=5, permit=None):
        if self.closed or not self.process or self.process.poll() is not None:
            raise WorkerError("Worker is not available")
        self.request_id += 1
        request_id = self.request_id
        try:
            write_frame(self.process.stdin, {"type": command, "id": request_id, "params": params})
        except OSError as exc:
            self.close()
            raise WorkerError("Worker pipe write failed") from exc
        reply = self._receive(timeout, permit)
        if (reply.get("type") != "reply" or reply.get("mode") != self.expected_mode or
                type(reply.get("id")) is not int or reply["id"] != request_id):
            self.close()
            raise WorkerError("Worker reply identity mismatch")
        if reply.get("error") is not None:
            raise WorkerError(str(reply["error"]))
        if not isinstance(reply.get("result"), dict):
            self.close()
            raise WorkerError("Invalid worker result")
        return reply["result"]

    def execute(self, job, budget_ns, *, timeout=3, permit=None):
        if self.closed or not self.process or self.process.poll() is not None:
            raise WorkerError("Worker is not available")
        self.request_id += 1
        request_id = self.request_id
        write_frame(self.process.stdin, {"type": "execute", "id": request_id,
                    "job_key": list(job), "budget_ns": budget_ns})
        response = self._receive(timeout, permit)
        if (response.get("type") != "result" or response.get("mode") != "offline-synthetic" or
                type(response.get("id")) is not int or response["id"] != request_id or
                response.get("job_key") != list(job)):
            self.close()
            raise WorkerError("Worker response does not match the requested job")
        active = response.get("active_ns")
        if type(active) is not int or not 0 <= active <= budget_ns:
            self.close()
            raise WorkerError("Worker activity exceeds the reserved budget")
        if not isinstance(response.get("proof_hex"), str) or len(response["proof_hex"]) > 4096:
            self.close()
            raise WorkerError("Malformed synthetic proof")
        try:
            proof = bytes.fromhex(response["proof_hex"])
        except ValueError as exc:
            self.close()
            raise WorkerError("Malformed proof encoding") from exc
        return active, proof

    def close(self):
        with self.close_lock:
            self._close()

    def reset_for_job_change(self):
        """Normal job retirement in an existing explicitly started session only."""
        argv,mode,env=self.argv,self.expected_mode,self.env
        self.close()
        self.__init__(argv,expected_mode=mode,env=env)

    def _close(self):
        self.closed = True
        process = self.process
        if process:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            if process.stdin:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if self.reader and self.reader is not threading.current_thread():
                self.reader.join(timeout=1)
            if process.stdout:
                process.stdout.close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.close()


def transport_selftest():
    with ProcessWorker() as worker:
        job = ("offline-selftest", 1, "synthetic", "a" * 64)
        active, proof = worker.execute(job, 1_000_000)
        return {"mode": "offline-synthetic; no GPU or pool", "child_pid": worker.process.pid,
                "simulated_active_ns": active, "proof_matches": len(proof) == 72 and proof[:64] == b"a" * 64}
