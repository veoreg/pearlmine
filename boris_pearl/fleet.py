# SPDX-License-Identifier: Apache-2.0
"""Explicitly paired remote devices; no external broker or developer master key."""
from copy import deepcopy
from dataclasses import dataclass
import re
import secrets
import subprocess
import sys
import threading
import time
from .remote_control import Pairing,RemoteClient
from .control_center import human_error


QUERY="nvidia-smi --query-gpu=uuid,name,temperature.gpu,power.draw,utilization.gpu --format=csv,noheader,nounits -l 3"


@dataclass(frozen=True)
class SshPeer:
    """A computer that is only looked at: one long SSH connection reads nvidia-smi. Nothing is installed or started there."""
    name:str
    target:str
    identity:str=""

    def __post_init__(self):
        if not 1<=len(self.name.strip())<=80:raise ValueError("Укажите имя компьютера.")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(@[A-Za-z0-9._-]+)?",self.target) is None:raise ValueError("Адрес: имя из настроек SSH или пользователь@адрес.")

    @property
    def url(self):return "ssh://"+self.target
    @property
    def key(self):return "ssh:"+self.target


def number(text):
    try:return float(text)
    except ValueError:return None


def parse_ssh_line(line):
    parts=[p.strip() for p in line.split(",")]
    if len(parts)!=5 or not parts[0].startswith("GPU-"):return None
    usage=number(parts[4])
    return parts[0],dict(name=parts[1],state="viewing",eligible=False,temperature=number(parts[2]),watts=number(parts[3]),
        note="Только просмотр"+("" if usage is None else f" · занята на {usage:.0f}%"))


class Fleet:
    def __init__(self):self.lock=threading.RLock();self.peers={};self.closing=False;self.retired=[]

    def connect(self,pairing):
        if not isinstance(pairing,Pairing):raise ValueError("Проверьте код подключения.")
        with self.lock:
            if self.closing or len(self.peers)>=16:raise ValueError("Доступно до16 явно подключённых компьютеров.")
            key=pairing.fingerprint
            if key in self.peers:raise ValueError("Этот компьютер уже добавлен.")
            row=dict(pairing=pairing,client=RemoteClient(pairing),stop=threading.Event(),status=None,error=None,notice=None,updated_at=None,threads=[],sequence=0,send_lock=threading.Lock())
            self.peers[key]=row
        def poll():
            first=True
            while not row["stop"].is_set():
                try:
                    if first:row["client"].request("refresh",{});first=False
                    status=row["client"].request("status")
                    if status.get("active",0):row["client"].request("lease",{})
                    with self.lock:row.update(status=status,error=None,updated_at=time.time(),sequence=max(row["sequence"],status.get("sequence",0)))
                except Exception as exc:
                    with self.lock:row["error"]=human_error(exc)
                row["stop"].wait(2)
            try:
                with row["send_lock"]:
                    status=row["client"].request("status")
                    with self.lock:row["sequence"]=max(row["sequence"],status.get("sequence",0))+1;sequence=row["sequence"]
                    row["client"].request("stop",dict(sequence=sequence))
            except Exception:pass # Agent's independent10s lease stops tests on a lost connection.
        thread=threading.Thread(target=poll,name="boris-fleet-peer",daemon=False);row["threads"].append(thread);thread.start()
        return key

    def connect_ssh(self,peer):
        with self.lock:
            if self.closing or len(self.peers)>=16:raise ValueError("Доступно до 16 подключённых компьютеров.")
            if peer.key in self.peers:raise ValueError("Этот компьютер уже добавлен.")
            row=dict(pairing=peer,client=None,stop=threading.Event(),status=None,error=None,notice=None,updated_at=None,threads=[],sequence=0,send_lock=threading.Lock(),process=None)
            self.peers[peer.key]=row
        def read():
            wait=5
            while not row["stop"].is_set():
                command=["ssh","-o","BatchMode=yes","-o","ConnectTimeout=10","-o","ServerAliveInterval=15","-o","StrictHostKeyChecking=accept-new"]
                if peer.identity:command+=["-i",peer.identity]
                try:
                    process=subprocess.Popen(command+[peer.target,QUERY],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,text=True,encoding="utf-8",errors="replace",
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0)
                    row["process"]=process;devices={}
                    for line in process.stdout:
                        parsed=parse_ssh_line(line)
                        if parsed:
                            devices[parsed[0]]=parsed[1];wait=5
                            with self.lock:row.update(status=dict(devices=dict(devices),active=0,allow_tests=False,read_only=True),error=None,updated_at=time.time())
                    process.wait()
                except OSError:pass
                if row["stop"].is_set():break
                with self.lock:row["error"]="Нет связи по SSH. Повторю попытку."
                row["stop"].wait(wait);wait=min(120,wait*2)  # Rare retries: servers limit the rate of SSH connections.
        thread=threading.Thread(target=read,name="boris-fleet-ssh",daemon=False);row["threads"].append(thread);thread.start()
        return peer.key

    def _end_ssh(self,row):
        row["stop"].set()
        process=row.get("process")
        if process and process.poll() is None:process.terminate()

    def command(self,key,name,body):
        with self.lock:
            if self.closing or key not in self.peers:raise ValueError("Компьютер не подключён.")
            row=self.peers[key]
            if row["client"] is None:raise ValueError("Этот компьютер подключён только для просмотра.")
            if name!="stop" and row["status"] is None:raise ValueError("Дождитесь подтверждённого подключения к компьютеру.")
            row["threads"]=[t for t in row["threads"] if t.is_alive()]
            # A busy peer may refuse a new start, never an explicit stop.
            if len(row["threads"])>=(16 if name=="stop" else 3):raise ValueError("Дождитесь ответа компьютера.")
            if name in ("start","stop"):
                row["sequence"]+=1;body=dict(body,sequence=row["sequence"])
            row["notice"]=None
            def send():
                try:
                    with row["send_lock"]:
                        if name=="start" and row["stop"].is_set():return
                        reply=row["client"].request(name,body)
                        if name=="stop" and reply.get("stale"):
                            # Another panel moved the agent's counter on; the owner's stop must still take effect.
                            with self.lock:row["sequence"]=max(row["sequence"],reply.get("sequence",0))+1;sequence=row["sequence"]
                            row["client"].request("stop",dict(sequence=sequence))
                        status=row["client"].request("status")
                    with self.lock:row.update(status=status,error=None,updated_at=time.time(),sequence=max(row["sequence"],status.get("sequence",0)))
                except Exception as exc:
                    with self.lock:row["notice"]=human_error(exc)+" Команда не повторяется автоматически; проверьте состояние устройства."
            thread=threading.Thread(target=send,name="boris-fleet-command",daemon=False);row["threads"].append(thread);thread.start()

    def start(self,key,devices,*,seconds,duty,profile,backend="auto"):
        self.command(key,"start",dict(request_id=secrets.token_hex(16),devices=list(devices),seconds=seconds,duty=duty,profile=profile,backend=backend))

    def stop(self,key=None):
        with self.lock:keys=[k for k in ([key] if key else list(self.peers)) if k in self.peers and self.peers[k]["client"] is not None]
        for peer in keys:
            try:self.command(peer,"stop",{})
            except ValueError as exc:
                with self.lock:
                    if peer in self.peers:self.peers[peer]["notice"]=str(exc)

    def remove(self,key):
        """Forget a peer; its poll thread sends a final stop before exiting."""
        with self.lock:
            row=self.peers.pop(key,None)
            if row:
                row["stop"].set();self.retired.append(row)
                if row["client"] is None:self._end_ssh(row)

    def snapshot(self):
        with self.lock:return {k:dict(name=v["pairing"].name,url=v["pairing"].url,status=deepcopy(v["status"]),error=v["error"],notice=v["notice"],updated_at=v["updated_at"]) for k,v in self.peers.items()}

    def close(self):
        with self.lock:
            self.closing=True
            for row in self.peers.values():
                row["stop"].set()
                if row["client"] is None:self._end_ssh(row)

    def alive(self):
        with self.lock:return any(t.is_alive() for row in [*self.peers.values(),*self.retired] for t in row["threads"])
