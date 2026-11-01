"""Bounded Stratum/TLS transport. External connections/submissions require explicit consent.

Default policy only permits the exact literal loopback endpoint selected by a
local test. No call to this module opens a connection at import or app startup.
"""
import base64
from collections import deque
from dataclasses import dataclass
from hashlib import sha256
import gzip
import ipaddress
import json
from queue import Empty, Full, Queue
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from .config import pool_url
from .core import Job, Session
from .cuda_worker import compact_target
from .official_bridge import ProofContext

MAX_LINE = 2_000_000


class PoolError(RuntimeError):
    pass


@dataclass(frozen=True)
class PoolAccess:
    """Only explicit operator consent may enable a non-loopback endpoint later."""
    endpoint: str
    config_identity: str
    allow_external: bool = False
    allow_submit: bool = False

    def check(self, config, url, submit=False):
        if self.endpoint != url or self.config_identity != config.identity:
            raise PermissionError("Pool access does not match disclosed configuration/endpoint")
        host = urlsplit(url).hostname
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if not local and not self.allow_external:
            raise PermissionError("External pool access requires separate explicit operator consent")
        if submit and not self.allow_submit:
            raise PermissionError("Submissions require explicit endpoint-scoped permission")
        if not local and urlsplit(url).scheme != "stratum+ssl":
            raise PermissionError("External pool transport requires validated TLS")


@dataclass(frozen=True)
class PoolJob:
    job: Job
    wire_job_id: str
    header76: str
    target: str
    nbits_share: int


def parse_notify(message):
    if message.get("method") != "mining.notify":
        return None
    p = message.get("params")
    if not isinstance(p, dict):
        raise PoolError("Unsupported notify shape")
    raw, header, target, cert = (p.get(k) for k in ("job_id", "header", "target", "cert_version"))
    if not isinstance(raw, str) or not 1 <= len(raw) <= 128:
        raise PoolError("Invalid job ID")
    if (not isinstance(header, str) or len(header) != 152 or
            any(c not in "0123456789abcdefABCDEF" for c in header)):
        raise PoolError("Invalid header")
    if type(cert) is not int or cert != 3:
        raise PoolError("Update required: unknown/missing certificate version")
    if not isinstance(target, str):
        raise PoolError("Invalid target")
    target = target.removeprefix("0x").lower()
    if not 1 <= len(target) <= 64 or any(c not in "0123456789abcdef" for c in target) or int(target, 16) == 0:
        raise PoolError("Invalid target")
    return raw, header.lower(), target.zfill(64), cert


class PoolClient:
    def __init__(self, config, router, session, access, *, worker_name="boris", dialect="object",
                 tls_context=None, timeout=3, on_retire=lambda job: None):
        if dialect not in ("object", "kryptex", "kryptex-v2"):
            raise ValueError("Unsupported pool dialect")
        if not isinstance(worker_name, str) or not 1 <= len(worker_name) <= 64 or any(c in worker_name for c in "\r\n"):
            raise ValueError("Invalid worker name")
        pool_url(session.pool)
        expected = ((config.user_pool, config.user_wallet) if session.owner == "user" else
                    (config.developer_pool, config.developer_wallet) if session.owner == "developer" else None)
        if expected is None or (session.pool, session.wallet.lower()) != (expected[0], expected[1].lower()):
            raise ValueError("Pool session must match the disclosed owner destination before connecting")
        if type(timeout) not in (int, float) or not 0.25 <= timeout <= 30:
            raise ValueError("Pool timeout must be 0.25 to 30 seconds")
        self.config, self.router, self.template, self.access = config, router, session, access
        self.worker_name, self.dialect, self.tls_context = worker_name, dialect, tls_context
        self.timeout, self.on_retire = timeout, on_retire
        self.session = session
        self.epoch = session.epoch - 1
        self.sequence = self.generation = 0
        self.socket = self.reader = None
        self.closed = threading.Event()
        self.closed.set()
        self.authorized = False
        self.current = None
        self.encoding = "plain"
        self.lock = threading.RLock()
        self.pending = {}
        self.outgoing = Queue(maxsize=8)
        self.jobs = Queue(maxsize=1)
        self.early = deque(maxlen=8)
        self.last_error = None
        self.attempted = set()

    def _disconnect(self, reason):
        self.closed.set()
        self.last_error = str(reason)
        with self.lock:
            self.authorized = False
            if self.current:
                self.on_retire(self.current.job)
            self.current = None
            self.router.disconnect(self.session.identifier)
            for queue in self.pending.values():
                try:
                    queue.put_nowait(PoolError(str(reason)))
                except Full:
                    pass
        if self.socket:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.socket.close()

    def close(self):
        self._disconnect("closed")
        if self.reader and self.reader is not threading.current_thread():
            self.reader.join(timeout=1)

    def _adopt(self, parts):
        raw, header, target, cert = parts
        with self.lock:
            if not self.authorized or self.closed.is_set():
                return
            if self.current:
                self.on_retire(self.current.job)
                self.router.verify.retire(self.current.job) if hasattr(self.router.verify, "retire") else None
            self.generation += 1
            # Internal generation changes even if a pool reuses its wire job ID.
            job = Job(self.session, f"{raw}#{self.generation}", sha256(bytes.fromhex(header)).hexdigest(), cert)
            self.router.register(job, clean=True)
            nbits = compact_target(target)
            if hasattr(self.router.verify, "bind"):
                self.router.verify.bind(job, ProofContext(header, nbits, cert))
            self.current = PoolJob(job, raw, header, target, nbits)
            try:
                self.jobs.get_nowait()
            except Empty:
                pass
            self.jobs.put_nowait(self.current)

    def _dispatch(self, value):
        parts = parse_notify(value)
        if parts:
            if self.authorized:
                self._adopt(parts)
            else:
                if len(self.early) == self.early.maxlen:
                    raise PoolError("Too many unauthenticated jobs")
                self.early.append(parts)
        elif "method" not in value and type(value.get("id")) is int:
            with self.lock:
                queue = self.pending.get(value["id"])
            if queue:
                try:
                    queue.put_nowait(value)
                except Full:
                    pass

    def _loop(self):
        buffer = bytearray()
        try:
            while not self.closed.is_set():
                try:
                    frame, event, errors, before_send = self.outgoing.get_nowait()
                    try:
                        # Recheck session and canonical proof at the actual dispatch boundary.
                        with self.lock:
                            if before_send:
                                before_send()
                            self.socket.sendall(frame)
                    except PoolError as exc:
                        errors.append(exc)
                    except OSError as exc:
                        errors.append(exc)
                        raise
                    finally:
                        event.set()
                except Empty:
                    pass
                try:
                    chunk = self.socket.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    raise PoolError("Pool connection lost")
                buffer.extend(chunk)
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    line = line.rstrip(b"\r")
                    if len(line) > MAX_LINE:
                        raise PoolError("Pool line exceeds size limit")
                    if line.strip():
                        value = json.loads(line)
                        if not isinstance(value, dict):
                            raise PoolError("Pool frame must be an object")
                        self._dispatch(value)
                if len(buffer) > MAX_LINE:
                    raise PoolError("Unterminated pool line exceeds limit")
        except (OSError, ValueError, PoolError, Full, RecursionError) as exc:
            self._disconnect(exc)

    def _request(self, method, params, *, jsonrpc=False, before_send=None):
        if self.closed.is_set():
            raise PoolError("Pool disconnected")
        with self.lock:
            if len(self.pending) >= 32:
                raise PoolError("Too many pending pool requests")
            self.sequence += 1
            identifier = self.sequence
            queue = Queue(maxsize=1)
            self.pending[identifier] = queue
        value = {"id": identifier, "method": method, "params": params}
        if jsonrpc:
            value["jsonrpc"] = "2.0"
        frame = json.dumps(value, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        if len(frame) > MAX_LINE:
            self.pending.pop(identifier, None)
            raise PoolError("Outgoing pool line exceeds limit")
        sent = threading.Event()
        errors = []
        try:
            self.outgoing.put_nowait((frame, sent, errors, before_send))
            if not sent.wait(self.timeout) or errors:
                raise PoolError("Pool write failed or timed out")
            response = queue.get(timeout=self.timeout)
            if isinstance(response, Exception):
                raise response
            return response
        except (Empty, Full, OSError, PoolError) as exc:
            self._disconnect(exc)
            raise PoolError("Pool response/write timeout or disconnect; do not automatically resend") from exc
        finally:
            with self.lock:
                self.pending.pop(identifier, None)

    @staticmethod
    def accepted(reply):
        return reply.get("error") is None and reply.get("result") is True

    def connect(self):
        self.access.check(self.config, self.template.pool)
        if self.reader and self.reader.is_alive():
            raise PoolError("Old pool reader must finish before reconnect")
        url = urlsplit(self.template.pool)
        if url.scheme == "stratum+ssl":
            context = self.tls_context or ssl.create_default_context()
            if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
                raise PermissionError("TLS certificate and hostname verification are mandatory")
        else:
            context = None
        self.epoch += 1
        self.session = Session(self.template.identifier, self.epoch, self.template.owner,
                               self.template.pool, self.template.wallet)
        self.pending.clear()
        self.outgoing = Queue(maxsize=8)
        self.jobs = Queue(maxsize=1)
        self.early.clear()
        self.attempted.clear()
        self.closed.clear()
        self.last_error = None
        try:
            raw = socket.create_connection((url.hostname, url.port), timeout=self.timeout)
            try:
                self.socket = context.wrap_socket(raw, server_hostname=url.hostname) if context else raw
            except Exception:
                raw.close()
                raise
            self.socket.settimeout(0.2)
            self.reader = threading.Thread(target=self._loop, name="boris-pool-reader", daemon=True)
            self.reader.start()
            if self.dialect == "kryptex":
                # Subscribe can be silent; send it without waiting for an ack, as captured.
                sent = threading.Event()
                self.sequence += 1
                frame = json.dumps({"jsonrpc":"2.0","id":self.sequence,"method":"mining.subscribe","params":["boris-pearl/0.2"]}).encode()+b"\n"
                self.outgoing.put_nowait((frame, sent, [], None))
                params = [f"{self.session.wallet}.{self.worker_name}", "x"]
            elif self.dialect == "kryptex-v2":
                params = {"wallet": f"{self.session.wallet}.{self.worker_name}", "agent":"boris-pearl/0.2", "password":"x", "type":"v2"}
            else:
                params = {"wallet":self.session.wallet,"worker":self.worker_name,"agent":"boris-pearl/0.2"}
            reply = self._request("mining.authorize", params, jsonrpc=self.dialect != "object")
            if not self.accepted(reply):
                raise PoolError("Pool authorization rejected")
            self.encoding = "gzip" if self.dialect == "kryptex-v2" and reply.get("type") == "v2" else "plain"
            with self.lock:
                if self.closed.is_set():
                    raise PoolError("Pool dropped during authorization")
                self.router.authorize(self.session)
                self.authorized = True
                while self.early:
                    self._adopt(self.early.popleft())
            return self.session
        except Exception:
            self.close()
            raise

    def next_job(self, timeout=None):
        try:
            job = self.jobs.get(timeout=self.timeout if timeout is None else timeout)
        except Empty as exc:
            raise PoolError("No fresh pool job before timeout") from exc
        if self.closed.is_set() or self.current != job:
            raise PoolError("Job retired before delivery")
        return job

    def submit(self, job):
        self.access.check(self.config, self.template.pool, submit=True)
        with self.lock:
            current = self.current
            if not self.authorized or self.closed.is_set() or not current or current.job != job or self.session != job.session:
                raise PoolError("Cannot submit a stale/foreign job")
            wire = getattr(self.router.verify, "verified_wire", {}).get(job)
            if not wire or (self.router.key(job), sha256(wire).digest()) not in self.router.seen:
                raise PoolError("Only officially verified and routed canonical proofs may be submitted")
            fingerprint = (self.router.key(job), sha256(wire).digest())
            if fingerprint in self.attempted:
                raise PoolError("Canonical proof already submitted or outcome uncertain; no resend")
            payload = gzip.compress(wire, mtime=0) if self.encoding == "gzip" else wire
            worker_login=(self.worker_name if self.dialect=="object" else f"{self.session.wallet}.{self.worker_name}")
            params = {"worker":worker_login,"job_id":current.wire_job_id,
                      "plain_proof":base64.b64encode(payload).decode()}
            if self.dialect == "object":
                params = {"wallet":self.session.wallet, **params}
        def admit():
            if (self.closed.is_set() or not self.authorized or self.current != current or
                    self.session != job.session or self.router.jobs.get(self.router.key(job)) != job):
                raise PoolError("Job retired before pool dispatch")
            if fingerprint in self.attempted or len(self.attempted) >= 4096:
                raise PoolError("Repeated proof or submission registry limit")
            self.attempted.add(fingerprint)
        try:
            reply = self._request("mining.submit", params, jsonrpc=self.dialect != "object", before_send=admit)
        except PoolError:
            return {"status":"uncertain","session":job.session,"resent":False}
        return {"status":"accepted" if self.accepted(reply) else "rejected","session":job.session,"resent":False}


class PoolFailover:
    """Explicit polling, bounded backoff; no background autostart or hidden retries."""
    def __init__(self, clients):
        if not 1 <= len(clients) <= 8:
            raise ValueError("Use one to eight explicit pools")
        self.clients, self.index, self.failures, self.next_try_at = clients, 0, 0, 0
        self.active = None

    def advance(self, now):
        if self.active and self.active.authorized and not self.active.closed.is_set():
            return self.active
        if now < self.next_try_at:
            return None
        client = self.clients[self.index]
        self.index = (self.index + 1) % len(self.clients)
        try:
            client.connect()
            self.active, self.failures = client, 0
            return client
        except (PoolError, OSError):
            self.failures += 1
            self.next_try_at = now + min(30, 2 ** min(self.failures - 1, 5))
            return None
