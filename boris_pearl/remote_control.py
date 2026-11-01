# SPDX-License-Identifier: Apache-2.0
"""Explicit HTTPS pairing for owned devices. Only bounded local tests, no shell/mining."""
from dataclasses import asdict,dataclass,field
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import hmac
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import socket
import sys
import threading
import time
from urllib.parse import urlsplit

from .control_center import DeviceService,atomic_json
from .devices import validate_uuid
from .load_profiles import validate_request

PROTOCOL="boris-control/1"


@dataclass(frozen=True)
class Pairing:
    name:str
    url:str
    fingerprint:str
    token:str=field(repr=False)

    def __post_init__(self):
        if not isinstance(self.name,str) or not 1<=len(self.name.strip())<=80:raise ValueError("Укажите имя компьютера.")
        p=urlsplit(self.url)
        if p.scheme!="https" or not p.hostname or p.username or p.password or p.path not in ("","/") or p.query or p.fragment or any(c.isspace() for c in self.url):
            raise ValueError("Нужен HTTPS-адрес агента без пароля и дополнительных путей.")
        try:port=p.port
        except ValueError:raise ValueError("Неверный порт агента.")
        if port is not None and not 1<=port<=65535:raise ValueError("Неверный порт агента.")
        if any(not isinstance(x,str) or re.fullmatch("[0-9a-f]{64}",x) is None for x in (self.fingerprint,self.token)):
            raise ValueError("Нужен полный код подключения, созданный на другом устройстве.")

    @classmethod
    def parse(cls,text):
        if not isinstance(text,str) or len(text)>4096:raise ValueError("Слишком большой код подключения.")
        try:return cls(**json.loads(text))
        except (TypeError,json.JSONDecodeError):raise ValueError("Нужен полный код подключения, созданный на другом устройстве.") from None


class RemoteClient:
    def __init__(self,pairing,*,timeout=3):self.pairing=pairing;self.timeout=timeout

    def request(self,path,body=None):
        if path not in ("status","refresh","lease","start","stop"):raise ValueError("Недоступная команда агента.")
        p=urlsplit(self.pairing.url)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);ctx.minimum_version=ssl.TLSVersion.TLSv1_2
        ctx.check_hostname=False;ctx.verify_mode=ssl.CERT_NONE  # Exact out-of-band certificate pin is verified BEFORE any credentials/data are sent.
        conn=http.client.HTTPSConnection(p.hostname,p.port or 443,timeout=self.timeout,context=ctx)
        try:
            conn.connect();actual=sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
            if not hmac.compare_digest(actual,self.pairing.fingerprint):raise ValueError("Сертификат устройства изменился. Код подключения нужно проверить на самом устройстве.")
            payload=None if body is None else json.dumps(body,separators=(",",":"),allow_nan=False).encode()
            if payload and len(payload)>65536:raise ValueError("Команда слишком большая.")
            conn.request("GET" if body is None else "POST","/v1/"+path,body=payload,
                headers={"Authorization":"Bearer "+self.pairing.token,"Content-Type":"application/json"})
            response=conn.getresponse();raw=response.read(1_000_001)
            if len(raw)>1_000_000:raise ValueError("Слишком большой ответ агента.")
            data=json.loads(raw)
            if response.status!=200 or data.get("protocol")!=PROTOCOL:raise ValueError(data.get("error","Агент отказал в команде."))
            return data["result"]
        finally:conn.close()


class ControlState:
    def __init__(self,service,token,*,allow_tests=False,lease_seconds=10,clock=time.monotonic):
        if type(lease_seconds) not in (int,float) or not .1<=lease_seconds<=15:raise ValueError("Bounded control lease required")
        self.service,self.token,self.allow_tests=service,token,allow_tests
        self.lease_seconds,self.clock=lease_seconds,clock
        self.lock=threading.RLock();self.expires=None;self.receipts={};self.sequence=0;self.closed=threading.Event()
        self.watch=threading.Thread(target=self._watch,name="boris-agent-lease",daemon=False);self.watch.start()

    def _watch(self):
        while not self.closed.wait(.05):
            with self.lock:
                if self.expires is None or self.clock()<self.expires:continue
                self.expires=None
                # Under the lock: an expired lease cannot stop a run accepted a moment later. A finished test is not a lost link.
                if self.service.snapshot().get("active"):
                    self.service.stop();self.service.log("Связь с панелью потеряна. Удалённые тесты остановлены.")

    def command(self,name,body):
        if name=="status":return dict(**self.service.snapshot(),allow_tests=self.allow_tests,lease_seconds=self.lease_seconds,sequence=self.sequence)
        if not isinstance(body,dict):raise ValueError("JSON object required")
        if name=="refresh":
            if body:raise ValueError("Refresh accepts no parameters")
            # A reconnecting panel must keep renewing the lease of a running test, so this is not an error.
            if self.service.snapshot().get("active"):return dict(queued=False)
            self.service.refresh();return dict(queued=True)
        if name=="lease":
            if body:raise ValueError("Lease accepts no parameters")
            with self.lock:
                if self.expires is not None:self.expires=self.clock()+self.lease_seconds
            return dict(connected=True)
        if name=="stop":
            if set(body)!={"sequence"} or type(body["sequence"]) is not int or not 1<=body["sequence"]<=2**53:raise ValueError("Explicit control sequence required")
            with self.lock:
                if body["sequence"]<=self.sequence:return dict(stopped=False,stale=True,sequence=self.sequence)
                self.sequence=body["sequence"];self.expires=None;self.service.stop()
            return dict(stopped=True)
        if name!="start" or not self.allow_tests:raise ValueError("На этом агенте запуск тестов не разрешён. Реальный майнинг недоступен.")
        if set(body)!={"request_id","sequence","devices","seconds","duty","profile","backend"}:raise ValueError("Only fixed bounded test options are accepted")
        if type(body["seconds"]) is not int or not 5<=body["seconds"]<=300:raise ValueError("Тест должен длиться от5 до300 секунд.")
        if not isinstance(body["devices"],list) or not 1<=len(body["devices"])<=4 or len(set(body["devices"]))!=len(body["devices"]):raise ValueError("Select1..4 distinct UUIDs")
        for device in body["devices"]:validate_uuid(device)
        validate_request(body["profile"],body["duty"],None,True)
        if body["backend"] not in ("auto","dp4a","wmma-int8","mma-int8"):raise ValueError("Unknown backend")
        if type(body["sequence"]) is not int or not 1<=body["sequence"]<=2**53:raise ValueError("Explicit control sequence required")
        request_id=body["request_id"]
        if not isinstance(request_id,str) or re.fullmatch("[0-9a-f]{32}",request_id) is None:raise ValueError("Explicit unique request ID required")
        fingerprint=sha256(json.dumps(body,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
        with self.lock:
            if request_id in self.receipts:
                old,result=self.receipts[request_id]
                if old!=fingerprint:raise ValueError("A request ID cannot change its parameters")
                return dict(result,replayed=True)
            if body["sequence"]<=self.sequence:raise ValueError("Stale start intent; an earlier start cannot override a later stop")
            if len(self.receipts)>=256:raise ValueError("Agent command budget exhausted; start a new explicit agent session")
            # A lease expiry and start are serialized: an old lease cannot stop a newly accepted run.
            self.service.start(body["devices"],seconds=body["seconds"],duty=body["duty"],profile=body["profile"],backend=body["backend"])
            self.expires=self.clock()+self.lease_seconds;self.sequence=body["sequence"]
            result=dict(started=True,request_id=request_id)
            self.receipts[request_id]=(fingerprint,result)
            # Never evict a start receipt: refuse a new explicit agent session after a bounded command budget.
            return result

    def close(self):
        self.closed.set();self.service.close();self.watch.join(timeout=2)


def make_server(address,certfile,keyfile,state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version="HTTP/1.0"
        def log_message(self,*args):pass
        def setup(self):
            super().setup();self.connection.settimeout(3);self.connection.do_handshake()
        def send(self,status,result=None,error=None):
            data=json.dumps(dict(protocol=PROTOCOL,result=result,error=error),allow_nan=False,separators=(",",":")).encode()
            if len(data)>1_000_000:status=500;data=b'{"error":"response exceeds bound"}'
            self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(data)));self.end_headers()
            self.wfile.write(data)
        def handle_command(self,post):
            if self.headers.get("Origin") or self.headers.get("Transfer-Encoding"):
                self.send(403,error="Browser origins/chunked requests are not accepted");return
            supplied=self.headers.get("Authorization","")
            if not hmac.compare_digest(supplied.encode("utf-8"),("Bearer "+state.token).encode("utf-8")):self.send(401,error="Неверный код подключения.");return
            if self.path not in ("/v1/status","/v1/refresh","/v1/lease","/v1/start","/v1/stop"):
                self.send(404,error="Unknown operation");return
            name=self.path.rsplit("/",1)[1]
            if (not post and name!="status") or (post and name=="status"):
                self.send(405,error="Invalid method");return
            try:
                if post:
                    if self.headers.get("Content-Type")!="application/json":raise ValueError("JSON required")
                    size=int(self.headers.get("Content-Length","-1"))
                    if not 1<=size<=65536:raise ValueError("Bounded content length required")
                    raw=self.rfile.read(size)
                    if len(raw)!=size:raise ValueError("Truncated body")
                    body=json.loads(raw)
                else:body=None
                if name=="start" and len(state.receipts)>=256 and (not isinstance(body,dict) or body.get("request_id") not in state.receipts):raise ValueError("Agent command budget exhausted; start a new explicit agent session")
                self.send(200,result=state.command(name,body))
            except (ValueError,TypeError,KeyError) as exc:self.send(400,error=str(exc)[:600])
            except OSError:raise
            except Exception:self.send(500,error="Внутренняя ошибка агента. Команда не выполнена.")
        def do_GET(self):self.handle_command(False)
        def do_POST(self):self.handle_command(True)
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        address_family=socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        request_queue_size=8
        def __init__(self,*args,**kwargs):self.slots=threading.BoundedSemaphore(8);super().__init__(*args,**kwargs)
        def handle_error(self,request,client_address):
            if isinstance(sys.exc_info()[1],OSError):return
            super().handle_error(request,client_address)
        def process_request(self,request,address):
            if not self.slots.acquire(blocking=False):request.close();return
            try:super().process_request(request,address)
            except BaseException:self.slots.release();raise
        def process_request_thread(self,*args):
            try:super().process_request_thread(*args)
            finally:self.slots.release()
    server=Server(address,Handler)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(certfile),str(keyfile));server.socket=context.wrap_socket(server.socket,server_side=True,do_handshake_on_connect=False)
    return server


def initialize(directory,name,url):
    # Uses the already installed optional cryptography package. Never installs/fetches a package.
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except ImportError:raise ValueError("Для первого включения агента нужен пакет cryptography. В самостоятельной сборке он уже внутри.") from None
    folder=Path(directory);folder.mkdir(parents=True,exist_ok=True)
    for filename in ("agent-cert.pem","agent-key.pem","pairing.json"):
        if (folder/filename).exists():raise ValueError("Код подключения уже существует. Для нового используйте другую папку.")
    key=ec.generate_private_key(ec.SECP256R1());now=datetime.now(timezone.utc)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"PearlMine owned-device agent")])
    cert=x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365)).sign(key,hashes.SHA256())
    pairing=Pairing(name,url,cert.fingerprint(hashes.SHA256()).hex(),secrets.token_hex(32))
    for filename,data in (("agent-cert.pem",cert.public_bytes(serialization.Encoding.PEM)),("agent-key.pem",key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))):
        fd=os.open(folder/filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"wb") as stream:stream.write(data)
    fd=os.open(folder/"pairing.json",os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as stream:json.dump(asdict(pairing),stream,ensure_ascii=False,indent=2)
    return pairing
