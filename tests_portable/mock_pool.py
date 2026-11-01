"""Loopback-only Stratum fixture. Synthetic public addresses, never a real pool.

Ephemeral TLS keys protect this test server only; they are not wallet keys and
are deleted with TemporaryDirectory. cryptography is an optional TEST dependency.
"""
from datetime import datetime, timedelta, timezone
import ipaddress
import json
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import time

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:
    x509 = None

HEADER = ((0x20000000).to_bytes(4, "little") + b"K" * 32 + b"A" * 32 +
          (0x66666666).to_bytes(4, "little") + (0x1C7FFFFF).to_bytes(4, "little")).hex()
TARGET = f"{((1 << 256) - 1) >> 18:064x}"


def notify(*, job_id="local-fixture", header=HEADER, target=TARGET, cert=3):
    return {"method": "mining.notify", "params": {"job_id": job_id, "header": header,
            "target": target, "cert_version": cert}}


def eventually(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Local fixture condition timed out")


class MockPool:
    def __init__(self, *, tls=True, matching_cert=True, auth=True, early=False,
                 ack_type=None, submit="accept", first_job=None):
        self.tls, self.auth, self.early = tls, auth, early
        self.ack_type, self.submit_mode = ack_type, submit
        self.first_job = first_job or notify()
        self.received, self.errors = [], []
        self.done = threading.Event()
        self.send_lock = threading.Lock()
        self.connection = None
        self.tmp = tempfile.TemporaryDirectory(prefix="boris-loopback-tls-")
        self.client_context = None
        self.server_context = None
        if tls:
            if x509 is None:
                raise RuntimeError("Optional test dependency cryptography is absent")
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "boris-loopback-test")])
            now = datetime.now(timezone.utc)
            names = [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))] if matching_cert else [x509.DNSName("wrong.invalid")]
            cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                    .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                    .not_valid_after(now + timedelta(hours=1)).add_extension(x509.SubjectAlternativeName(names), False)
                    .sign(key, hashes.SHA256()))
            root = Path(self.tmp.name)
            (root / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            (root / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            self.server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.server_context.load_cert_chain(root / "cert.pem", root / "key.pem")
            self.client_context = ssl.create_default_context(cafile=str(root / "cert.pem"))
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.endpoint = f"stratum+{'ssl' if tls else 'tcp'}://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self._run, name="boris-loopback-mock", daemon=True)
        self.thread.start()

    def send(self, value):
        self.raw(json.dumps(value, separators=(",", ":")).encode() + b"\n")

    def raw(self, data):
        with self.send_lock:
            self.connection.sendall(data)

    def _run(self):
        try:
            while not self.done.is_set():
                try:
                    conn, _ = self.listener.accept()
                    break
                except socket.timeout:
                    continue
            else:
                return
            conn.settimeout(2)
            self.connection = conn
            if self.server_context:
                self.connection = self.server_context.wrap_socket(conn, server_side=True)
            self.connection.settimeout(0.1)
            buffer = b""
            while not self.done.is_set():
                try:
                    data = self.connection.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    break
                buffer += data
                while b"\n" in buffer:
                    line, _, buffer = buffer.partition(b"\n")
                    message = json.loads(line)
                    self.received.append(message)
                    method = message.get("method")
                    if method == "mining.authorize":
                        if self.early:
                            self.send(self.first_job)
                        if self.auth == "silent":
                            continue
                        reply = {"id": message["id"], "result": bool(self.auth), "error": None}
                        if self.ack_type:
                            reply["type"] = self.ack_type
                        self.send(reply)
                        if self.auth and not self.early:
                            self.send(self.first_job)
                    elif method == "mining.submit":
                        if self.submit_mode == "lose-ack":
                            self.connection.shutdown(socket.SHUT_RDWR)
                            return
                        if self.submit_mode != "silent":
                            self.send({"id": message["id"], "result": self.submit_mode == "accept", "error": None})
        except (OSError, ValueError) as exc:
            if not self.done.is_set():
                self.errors.append(type(exc).__name__)
        finally:
            if self.connection:
                self.connection.close()

    def close(self):
        self.done.set()
        self.listener.close()
        if self.connection:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def accept_reconnect(self):
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise AssertionError("Previous mock connection has not closed")
        self.thread = threading.Thread(target=self._run, name="boris-loopback-reconnect", daemon=True)
        self.thread.start()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
