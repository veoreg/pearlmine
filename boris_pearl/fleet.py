# SPDX-License-Identifier: Apache-2.0
"""Explicitly paired remote devices; no external broker or developer master key."""
from copy import deepcopy
from dataclasses import dataclass
import json
import re
import shlex
import secrets
import subprocess
import sys
import threading
import time
from .remote_control import Pairing,RemoteClient
from .control_center import human_error


# One shell loop over one SSH connection. The miner lines appear only where the service is installed.
MINER_ROOTS=("/srv/spark/pearlmine","/root/pearlmine")
# The echo is what ends the loop once the panel is gone: nothing else here notices a closed connection.
QUERY=("while :; do echo PEARLMINE_ALIVE || exit; nvidia-smi --query-gpu=uuid,name,temperature.gpu,power.draw,utilization.gpu --format=csv,noheader,nounits; "
    +"".join(f"[ -f {root}/src/tools/spark-status.sh ] && bash {root}/src/tools/spark-status.sh; " for root in MINER_ROOTS)+"sleep 3; done")


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


def miner_row(miner,job=None,usage=None,hottest=None):
    """The service's report as one table row. Temperature is the machine's hottest point, as on its own console."""
    row={} if hottest is None else dict(temperature=hottest)
    miner=miner or {};state=miner.get("state")
    counts=f"принято {miner.get('accepted',0)}, отклонено {miner.get('rejected',0)}"
    busy=f"занята на {usage:.0f}%" if usage and usage>=5 else None
    other=job if job and job!="pearlmine" else None
    if state=="running":return dict(row,state="running",note=f"Майнинг {miner.get('th',0):.0f} TH/s · {counts}")
    if other or (usage or 0)>=5:row["state"]="busy"
    now=" · ".join(x for x in ("Сейчас: "+other if other else "Сейчас работает задача" if row.get("state")=="busy" else None,busy) if x)
    if state=="waiting":return dict(row,note=f"Майнинг ждёт очереди · {now or counts}")
    reason=miner.get("reason")
    if state=="stopped" and reason and reason not in ("Остановлено владельцем","Срок майнинга закончился"):
        return dict(row,note="Майнинг остановлен защитой: "+human_error(reason)+(" · "+now if now else ""))
    return dict(row,note=now+" · майнинг выключен" if now else "Майнинг выключен, можно включить")


def parse_ssh_line(line):
    parts=[p.strip() for p in line.split(",")]
    if len(parts)!=5 or not parts[0].startswith("GPU-"):return None
    usage=number(parts[4])
    return parts[0],dict(name=parts[1],state="viewing",eligible=False,temperature=number(parts[2]),watts=number(parts[3]),
        usage=usage,note="Только просмотр"+("" if usage is None else f" · занята на {usage:.0f}%"))


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
                    row["process"]=process;devices={};ready=False;miner=None;hottest=None;job=None
                    for line in process.stdout:
                        if line.startswith("PEARLMINE_READY"):ready=True;continue
                        if line.startswith("PEARLMINE_HOT"):
                            value=number(line[13:].strip());hottest=None if value is None else value/1000;continue
                        if line.startswith("PEARLMINE_JOB"):job=line[13:].strip() or None;continue
                        if line.startswith("PEARLMINE "):
                            try:miner=json.loads(line[10:])
                            except ValueError:miner=None
                            continue
                        parsed=parse_ssh_line(line)
                        if parsed:
                            devices[parsed[0]]=parsed[1];wait=5
                            shown={k:dict(v,**miner_row(miner,job,v.get("usage"),hottest)) for k,v in devices.items()} if ready else dict(devices)
                            with self.lock:row.update(status=dict(devices=shown,active=int(ready and (miner or {}).get("state")=="running"),allow_tests=False,read_only=True,miner_ready=ready,miner=miner),error=None,updated_at=time.time())
                    process.wait()
                except OSError:pass
                if row["stop"].is_set():break
                with self.lock:row["error"]="Нет связи по SSH. Повторю попытку."
                row["stop"].wait(wait);wait=min(120,wait*2)  # Rare retries: servers limit the rate of SSH connections.
        thread=threading.Thread(target=read,name="boris-fleet-ssh",daemon=False);row["threads"].append(thread);thread.start()
        return peer.key

    def miner(self,key,action,*values):
        """Start or stop the service on a computer that has it. It keeps its own state: closing the panel does not stop it."""
        with self.lock:
            row=self.peers.get(key)
            if not row or row["client"] is not None or not (row["status"] or {}).get("miner_ready"):raise ValueError("На этом компьютере нет службы майнинга.")
            peer=row["pairing"];row["notice"]=None
        arguments=" ".join(shlex.quote(str(v)) for v in values)
        remote="for r in "+" ".join(MINER_ROOTS)+"; do [ -f $r/src/boris_pearl/spark_miner.py ] && cd $r/src && exec python3 -X utf8 -m boris_pearl.spark_miner "+action+" "+arguments+"; done; echo no-service >&2; exit 1"
        def send():
            command=["ssh","-o","BatchMode=yes","-o","ConnectTimeout=10"]+(["-i",peer.identity] if peer.identity else [])+[peer.target,remote]
            try:
                done=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=40,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0)
                if done.returncode:
                    with self.lock:row["notice"]=human_error(done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "Команда не выполнена")
            except (OSError,subprocess.SubprocessError) as exc:
                with self.lock:row["notice"]=human_error(exc)
        thread=threading.Thread(target=send,name="boris-fleet-miner",daemon=False);row["threads"].append(thread);thread.start()

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
        with self.lock:
            keys=[k for k in ([key] if key else list(self.peers)) if k in self.peers and self.peers[k]["client"] is not None]
            miners=[k for k in ([key] if key else list(self.peers)) if k in self.peers and self.peers[k]["client"] is None and (self.peers[k]["status"] or {}).get("miner_ready")]
        for peer in miners:self.miner(peer,"stop")
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
