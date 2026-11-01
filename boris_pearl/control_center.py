# SPDX-License-Identifier: Apache-2.0
"""Owned local-device control for explicit bounded tests; construction is idle."""
from collections import deque
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time

from .devices import discover,validate_uuid
from .load_profiles import validate_request
from .local_search import LocalSearch
from .telemetry import TelemetryMonitor

ROOT=Path(__file__).resolve().parent.parent


def human_error(error):
    text=str(error)
    rules=[("board/OEM","Для GB10 ещё нужен проверенный датчик платы. Запуск заблокирован."),
        ("GB10","Для GB10 требуется проверка на самом Spark."),
        ("reviewed build","Вычислительный модуль не совпадает с проверенной сборкой."),
        ("Build/review","Сначала нужна проверенная сборка вычислительного модуля."),
        ("power limit","Драйвер не предоставил проверенный лимит питания."),
        ("battery","Подключите питание от сети: состояние батареи неизвестно или небезопасно."),
        ("AC state","Нужно подтверждённое питание от сети."),
        ("telemetry","Нет свежих данных датчиков. Работа остановлена."),
        ("temperature","Работа остановлена из-за температуры."),
        ("memory pressure","Недостаточно свободной памяти GPU."),
        ("power threshold","Превышен настроенный порог потребления."),
        ("backend unavailable","Это ядро недоступно для выбранной карты."),
        ("not visible","Выбранное устройство сейчас недоступно."),
        ("Stale start","Этот запуск отменён более поздней командой «Стоп»."),
        ("P2TR","Нужен публичный адрес PRL (prl1p…) с верной контрольной суммой."),
        ("stratum+","Адрес пула: stratum+ssl://адрес:порт, без логина и пароля."),
        ("pool port","Адрес пула: stratum+ssl://адрес:порт, без логина и пароля."),
        ("nonzero fee","Для комиссии нужны публичный адрес и пул разработчика. Пока они не заданы, оставьте 0%."),
        ("Pool fee","Комиссия пула: от 0 до 100% или пустое поле."),
        ("could not convert","Комиссия пула: от 0 до 100% или пустое поле."),
        ("timed out","Компьютер не отвечает. Проверьте, включён ли агент и VPN."),
        ("10060","Компьютер не отвечает. Проверьте, включён ли агент и VPN."),
        ("10061","Компьютер отказал в подключении: агент выключен."),
        ("refused","Компьютер отказал в подключении: агент выключен."),
        ("10051","Нет сети до компьютера. Проверьте VPN."),
        ("10065","Нет сети до компьютера. Проверьте VPN."),
        ("10048","Порт агента занят: возможно, агент уже запущен в другом окне.")]
    for needle,message in rules:
        if needle.lower() in text.lower():return message
    return text[:700]


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+".new")
    with open(tmp,"w",encoding="utf-8") as stream:
        json.dump(value,stream,indent=2,ensure_ascii=False,allow_nan=False)
        stream.flush();os.fsync(stream.fileno())
    os.replace(tmp,path)


class DeviceLock:
    """OS-held per-GPU lock across panel and agent windows; released on exit or crash."""
    def __init__(self,key):
        folder=Path(tempfile.gettempdir())/"boris-pearl-locks";folder.mkdir(exist_ok=True)
        self.stream=open(folder/(sha256(key.encode()).hexdigest()[:32]+".lock"),"a+b")
        try:
            if os.name=="nt":
                import msvcrt
                self.stream.seek(0);msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.stream.close();raise ValueError("На этой карте уже идёт тест в другом окне PearlMine.") from None
    def release(self):self.stream.close()


class DeviceService:
    def __init__(self,*,inventory_provider=None,session_factory=None,probe_sensors=True,mining_factory=None):
        self.mining_factory=mining_factory
        self.inventory_provider=inventory_provider or discover
        self.session_factory=session_factory or LocalSearch
        self.probe_sensors=probe_sensors
        self.lock=threading.RLock();self.closing=threading.Event()
        self.records={};self.devices={};self.jobs={};self.logs=deque(maxlen=400)
        self.threads=[];self.refreshing=False;self.refresh_error=None;self.monitors={};self.sensor_thread=None
        self.log("Программа открыта. Вычисления и загрузки не запускаются автоматически.")

    def log(self,message):
        with self.lock:self.logs.append(dict(time=time.strftime("%H:%M:%S"),message=str(message)[:1200]))

    def _thread(self,target,name):
        thread=threading.Thread(target=target,name=name,daemon=False)
        with self.lock:self.threads=[t for t in self.threads if t.is_alive()];self.threads.append(thread)
        thread.start();return thread

    def refresh(self):
        with self.lock:
            if self.closing.is_set() or self.refreshing:return False
            if any(j["thread"].is_alive() for j in self.jobs.values()):raise ValueError("Остановите тесты перед обновлением списка устройств.")
            self.refreshing=True;self.refresh_error=None
        def work():
            try:
                try:devices=self.inventory_provider()
                except Exception as exc:
                    devices=();self.log("CUDA: "+human_error(exc))
                with self.lock:
                    old=list(self.monitors.values());self.monitors={}
                for monitor in old:monitor.close()
                with self.lock:
                    self.devices={d.uuid:d for d in devices};self.records={}
                    for d in devices:
                        available=bool(d.eligible_backends) and not d.requires_gb10_guards
                        self.records[d.uuid]=dict(name=d.name,kind="NVIDIA CUDA",state="idle",eligible=available,
                            note=("Можно майнить" if (d.cc_major,d.cc_minor)==(12,0) else "Только тест: быстрого ядра пока нет") if available else ("Нужна проверка Spark" if d.requires_gb10_guards else "Карта пока не поддержана"),
                            backends=list(d.eligible_backends),temperature=None,watts=None,free_bytes=None,updated_at=None,
                            profile="laptop" if "LAPTOP" in d.name.upper() else "standard",report=None,error=None)
                self.log(f"Найдено CUDA-устройств: {len(devices)}.")
                if self.probe_sensors:
                    for d in devices[:8]:
                        if self.closing.is_set():break
                        monitor=TelemetryMonitor(device_uuid=d.uuid)
                        try:
                            monitor.start();monitor.updated.wait(3);sample=monitor.sample()
                            if sample:
                                with self.lock:
                                    self.records[d.uuid].update(temperature=sample.gpu_c,watts=sample.watts,free_bytes=sample.free_bytes,updated_at=time.time())
                            with self.lock:
                                if not self.closing.is_set():self.monitors[d.uuid]=monitor
                                else:monitor.close()
                        except Exception:monitor.close()
                    if not self.sensor_thread or not self.sensor_thread.is_alive():
                        self.sensor_thread=self._thread(self._watch_sensors,"boris-control-sensors")
                if self.inventory_provider is discover and not self.closing.is_set():
                    try:
                        from .opencl_validation import OpenClValidation
                        probe=OpenClValidation(seconds=10)
                        try:self.add_opencl_inventory(probe.inventory())
                        finally:probe.close()
                    except Exception as exc:self.log("OpenCL: "+human_error(exc))
            except Exception as exc:
                with self.lock:self.refresh_error=human_error(exc)
                self.log("Устройства: "+human_error(exc))
            finally:
                with self.lock:self.refreshing=False
        self._thread(work,"boris-control-inventory");return True

    def _watch_sensors(self):
        while not self.closing.wait(.5):
            with self.lock:monitors=list(self.monitors.items())
            for key,monitor in monitors:
                sample=monitor.sample()
                with self.lock:
                    row=self.records.get(key)
                    if row and row["state"] not in ("running","paused"):
                        row.update(temperature=None if sample is None else sample.gpu_c,watts=None if sample is None else sample.watts,
                            free_bytes=None if sample is None else sample.free_bytes,updated_at=time.time() if sample else None)
        with self.lock:monitors=list(self.monitors.values());self.monitors={}
        for monitor in monitors:monitor.close()

    def add_opencl_inventory(self,descriptors):
        with self.lock:
            for d in descriptors:
                if d.uuid and "GPU-"+d.uuid in self.records:continue
                key="opencl:"+(d.uuid or f"{d.platform_index}:{d.device_index}")
                self.records[key]=dict(name=d.name,kind="OpenCL",state="idle",eligible=False,
                    note="AMD: нужны испытания и датчики" if d.vendor_id==0x1002 else "OpenCL: диагностический режим",
                    backends=[],temperature=None,watts=None,free_bytes=None,updated_at=None,profile="standard",report=None,error=None)

    def start(self,device_ids,*,seconds=60,duty=.2,profile="standard",backend="auto"):
        if type(seconds) is not int or not 5<=seconds<=300:raise ValueError("Для локального теста выберите от5 до300 секунд.")
        validate_request(profile,duty,None,True)
        if backend not in ("auto","dp4a","wmma-int8","mma-int8"):raise ValueError("Неизвестный режим вычислений.")
        self._launch(device_ids,seconds,duty,profile,backend,self.session_factory,"test",f"Явно запущен локальный тест: {{}} GPU, {seconds}с, комиссия 0%.")

    def start_mining(self,device_ids,config,*,seconds,duty,profile="standard"):
        """Real mining to the owner's saved pool and payout; only ever called from an explicit Start."""
        def default(**options):
            from .live import LiveMining
            options.pop("backend",None)
            return LiveMining(config,**options)
        self._launch(device_ids,seconds,duty,profile,"tma",self.mining_factory or default,"mining",
            f"Запущен майнинг: {{}} GPU, пул {config.user_pool}, комиссия разработчика {config.effective_bps/100:g}%.")

    def _launch(self,device_ids,seconds,duty,profile,backend,factory,kind,message):
        ids=list(device_ids)
        if not ids or len(ids)>4 or len(ids)!=len(set(ids)):raise ValueError("Выберите от 1 до 4 разных GPU.")
        with self.lock:
            if self.closing.is_set() or self.refreshing:raise ValueError("Дождитесь завершения диагностики.")
            for key in ids:
                if key not in self.devices or not self.records[key]["eligible"]:raise ValueError("Для выбранной карты тест ещё недоступен.")
                if key in self.jobs and self.jobs[key]["thread"].is_alive():raise ValueError("На этой карте уже идёт тест.")
                if backend not in ("auto","tma"):self.devices[key].require_backend(backend)
            if sum(j["thread"].is_alive() for j in self.jobs.values())+len(ids)>4:raise ValueError("Не более4 одновременных тестов.")
            locks={}
            try:
                for key in ids:locks[key]=DeviceLock(key)
            except ValueError:
                for held in locks.values():held.release()
                raise
            for key in ids:
                chosen="dp4a" if backend=="auto" else backend
                job=dict(cancel=threading.Event(),session=None,thread=None,device_lock=locks[key],kind=kind,factory=factory)
                self.jobs[key]=job;self.records[key].update(state="preparing",error=None,report=None,backend=chosen)
                target=lambda key=key,job=job,chosen=chosen:self._run(key,job,seconds,duty,profile,chosen)
                thread=threading.Thread(target=target,name="boris-test-"+key[-8:],daemon=False)
                job["thread"]=thread;self.threads.append(thread);thread.start()
        self.log(message.format(len(ids)))

    def _run(self,key,job,seconds,duty,profile,backend):
        session=None;started=time.monotonic()
        try:
            if job["cancel"].is_set():return
            session=job["factory"](seconds=seconds,duty=duty,profile=profile,backend=backend,device_index=key)
            with self.lock:job["session"]=session
            if job["cancel"].is_set():return
            session.start()
            with self.lock:self.records[key]["state"]="running"
            while not job["cancel"].is_set() and not session.controller.stopped.is_set():
                reply=session.tick();sample=session.monitor.sample();stats=session.stats or {}
                with self.lock:
                    self.records[key].update(state="stopping" if job["cancel"].is_set() else "paused" if reply.get("state")=="paused" else "running",
                        elapsed=round(time.monotonic()-started,1),temperature=None if sample is None else sample.gpu_c,
                        watts=None if sample is None else sample.watts,free_bytes=None if sample is None else getattr(sample,"free_bytes",None),
                        updated_at=time.time(),test_tiles=stats.get("total_tiles",0),
                        note=self._mining_note(session,started) if job["kind"]=="mining" else human_error(reply.get("reason","Локальная проверка")))
                if reply.get("state")=="stopped":break
                if reply.get("state")=="paused":job["cancel"].wait(.02)
            session.stop_work("control center stop" if job["cancel"].is_set() else "stop deadline")
            report=session.report();report.pop("sensors",None)
            if job["kind"]=="mining":
                why=(report.get("stop") or {}).get("reason") or "остановлено"
                note=self._mining_note(session,started)+". Причина остановки: "+human_error(why)
            else:note="Тест завершён; процесс остановлен."
            with self.lock:self.records[key].update(report=report,state="stopped",note=note)
            self.log(self.records[key]["name"]+": "+note)
        except Exception as exc:
            with self.lock:self.records[key].update(state="failed",error=human_error(exc),note=human_error(exc))
            self.log(self.records[key]["name"]+": "+human_error(exc))
        finally:
            if session:
                try:session.close("control center finished")
                except Exception as exc:self.log("Завершение процесса: "+human_error(exc))
            job["device_lock"].release()
            with self.lock:
                if self.records.get(key,{}).get("state") not in ("failed","stopped"):
                    self.records[key].update(state="stopped",note="Остановлено до запуска вычислений.")

    @staticmethod
    def _mining_note(session,started):
        shares=getattr(session,"shares",{});elapsed=max(1,time.monotonic()-started)
        rate=getattr(session,"mined_tiles",0)*524288/elapsed/1e12
        return f"Майнинг {rate:.0f} TH/s · принято {shares.get('accepted',0)}, отклонено {shares.get('rejected',0)}"

    def stop(self,device_ids=None):
        with self.lock:
            selected=[(k,j) for k,j in self.jobs.items() if device_ids is None or k in device_ids]
            for key,job in selected:
                job["cancel"].set()
                if job["thread"].is_alive():self.records[key]["state"]="stopping"
        def interrupt():
            for key,job in selected:
                session=job["session"]
                if session and session.controller:session.controller.stop("control center explicit stop")
        if selected:self._thread(interrupt,"boris-control-stop")

    def snapshot(self):
        with self.lock:return dict(devices=deepcopy(self.records),refreshing=self.refreshing,error=self.refresh_error,
            logs=list(self.logs),active=sum(j["thread"].is_alive() for j in self.jobs.values()),mining_enabled=False,downloads=[],
            mining=sum(j["thread"].is_alive() and j["kind"]=="mining" for j in self.jobs.values()))

    def export(self):
        data=self.snapshot();data["devices"]=list(data["devices"].values())
        data["logs"]=[dict(row,message=re.sub(r"GPU-[0-9a-fA-F-]{36}","GPU-REDACTED",row["message"])) for row in data["logs"]]
        return data

    def close(self):
        self.closing.set();self.stop()

    def alive(self):
        with self.lock:return any(t.is_alive() for t in self.threads)
