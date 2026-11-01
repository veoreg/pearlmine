"""Explicit Tk local-test controls; never enables a real pool/mining start."""
import json
from queue import Empty,Full,Queue
import threading
import time


class LocalControls:
    def __init__(self,root,parent,show,session_factory=None,inventory_provider=None):
        import tkinter as tk
        from tkinter import ttk
        from .local_search import LocalSearch
        from .devices import discover
        self.root,self.show=root,show
        self.session_factory=session_factory or LocalSearch
        self.inventory_provider=inventory_provider or discover
        self.device_map={}
        self.refresh_thread=None
        self.session=self.thread=self.stop_thread=None
        self.cancel=threading.Event()
        self.messages=Queue(maxsize=8)
        self.closing=False
        frame=ttk.LabelFrame(parent,text="Локальная GPU-проверка",padding=8)
        frame.pack(fill="x",pady=6)
        ttk.Label(frame,text="Искусственное задание · комиссия 0% · без подключения к пулу").pack(anchor="w")
        device_row=ttk.Frame(frame);device_row.pack(fill="x",pady=3)
        ttk.Label(device_row,text="CUDA index / GPU UUID:").pack(side="left")
        self.device=tk.StringVar(value="0")
        self.device_box=ttk.Combobox(device_row,state="normal",width=58,values=("0",),textvariable=self.device)
        self.device_box.pack(side="left",padx=5,fill="x",expand=True)
        self.refresh_button=ttk.Button(device_row,text="Обновить GPU",command=self.refresh_devices)
        self.refresh_button.pack(side="left")
        profile_row=ttk.Frame(frame);profile_row.pack(fill="x",pady=3)
        ttk.Label(profile_row,text="Профиль нагрузки:").pack(side="left")
        self.profile=tk.StringVar(value="standard")
        self.profile_box=ttk.Combobox(profile_row,state="readonly",width=12,values=("standard","laptop"),textvariable=self.profile)
        self.profile_box.pack(side="left",padx=5)
        self.profile_box.bind("<<ComboboxSelected>>",self.profile_changed)
        mode_row=ttk.Frame(frame);mode_row.pack(fill="x",pady=3)
        ttk.Label(mode_row,text="Вычислительное ядро теста:").pack(side="left")
        self.backend=tk.StringVar(value="dp4a")
        self.backend_box=ttk.Combobox(mode_row,state="readonly",width=18,
            values=("dp4a","wmma-int8","mma-int8"),textvariable=self.backend)
        self.backend_box.pack(side="left",padx=5)
        row=ttk.Frame(frame);row.pack(fill="x",pady=5)
        ttk.Label(row,text="Секунд:").pack(side="left")
        self.seconds=tk.StringVar(value="60")
        self.duration=ttk.Spinbox(row,from_=5,to=300,increment=5,width=6,textvariable=self.seconds)
        self.duration.pack(side="left",padx=5)
        ttk.Label(row,text="Доля рабочего времени:").pack(side="left")
        self.duty=tk.StringVar(value="20%")
        self.dutybox=ttk.Combobox(row,state="readonly",width=5,values=("10%","20%","25%"),textvariable=self.duty)
        self.dutybox.pack(side="left",padx=5)
        self.start_button=ttk.Button(row,text="Запустить локальный тест",command=self.start)
        self.start_button.pack(side="left",padx=5)
        self.stop_button=ttk.Button(row,text="Остановить тест",command=self.stop,state="disabled")
        self.stop_button.pack(side="left")
        self.status=tk.StringVar(value="Ожидание явного запуска. Настройки GPU не изменяются.")
        ttk.Label(frame,textvariable=self.status,wraplength=760).pack(anchor="w")
        root.protocol("WM_DELETE_WINDOW",self.close_window)
        root.after(100,self.poll)

    def post(self,kind,value):
        try:
            self.messages.put_nowait((kind,value))
        except Full:
            # Keep newest diagnostic state, bounded even if the UI is temporarily busy.
            try:self.messages.get_nowait()
            except Empty:pass
            self.messages.put_nowait((kind,value))

    def profile_changed(self,event=None):
        if self.profile.get()=="laptop":
            self.duty.set("10%")
            self.status.set("Ноутбук: AC обязателен, доля ≤10%, порог ≤50 W / 80% лимита, температура <70 °C. Настройки GPU не меняются.")

    def refresh_devices(self):
        if self.closing or any(t and t.is_alive() for t in (self.thread,self.stop_thread,self.refresh_thread)):return
        self.start_button.state(["disabled"]);self.refresh_button.state(["disabled"])
        def fetch():
            try:self.post("devices",self.inventory_provider())
            except Exception as exc:self.post("error",str(exc))
            finally:self.post("refresh-done",None)
        self.refresh_thread=threading.Thread(target=fetch,name="boris-device-inventory",daemon=False)
        self.refresh_thread.start()

    def start(self):
        if any(t and t.is_alive() for t in (self.thread,self.stop_thread,self.refresh_thread)) or self.closing:
            return
        try:
            seconds=int(self.seconds.get());duty=int(self.duty.get().removesuffix("%"))/100
            backend=self.backend.get()
            profile=self.profile.get()
            from .load_profiles import validate_request
            validate_request(profile,duty,None,True)
            selected=self.device_map.get(self.device.get(),self.device.get())
            if selected.isascii() and selected.isdecimal():selected=int(selected)
            if backend not in ("dp4a","wmma-int8","mma-int8"):raise ValueError
            if not 5<=seconds<=300 or duty not in (.1,.2,.25):
                raise ValueError
        except ValueError:
            self.status.set("Выберите 5–300 секунд и долю 10%, 20% или 25%.")
            return
        self.cancel.clear();self.session=None
        self.start_button.state(["disabled"]);self.stop_button.state(["!disabled"])
        self.duration.state(["disabled"]);self.dutybox.state(["disabled"]);self.backend_box.state(["disabled"])
        self.device_box.state(["disabled"]);self.profile_box.state(["disabled"]);self.refresh_button.state(["disabled"])
        self.status.set("Подготовка локального теста и проверка реальных датчиков…")
        self.thread=threading.Thread(target=self.run,args=(seconds,duty,backend,selected,profile),name="boris-local-test-ui",daemon=False)
        self.thread.start()

    def run(self,seconds,duty,backend,selected,profile):
        try:
            self.session=self.session_factory(seconds=seconds,duty=duty,backend=backend,device_index=selected,profile=profile)
            if self.cancel.is_set():
                return
            self.session.start()
            last=0
            while not self.cancel.is_set() and not self.session.controller.stopped.is_set():
                result=self.session.tick()
                now=time.monotonic()
                if result.get("state")=="stopped":
                    break
                if now-last>=.5:
                    sample=self.session.monitor.sample()
                    stats=self.session.stats or {}
                    self.post("progress",{"temperature":None if sample is None else sample.gpu_c,
                        "watts":None if sample is None else sample.watts,"tiles":stats.get("total_tiles",0),
                        "nonces":stats.get("nonce_changes",0),"state":result.get("reason",result["state"])})
                    last=now
                if result.get("state")=="paused":
                    self.cancel.wait(.02)
            self.session.stop_work("manual UI stop" if self.cancel.is_set() else "stop deadline")
            report=self.session.report()
            report.pop("sensors",None)
            self.post("result",report)
        except Exception as exc:
            self.post("error",str(exc))
        finally:
            if self.session:
                self.session.close("local UI test finished")
            self.post("done",None)

    def stop(self):
        self.cancel.set()
        self.stop_button.state(["disabled"])
        self.status.set("Останавливаю принадлежащий тесту процесс GPU…")
        if self.session and self.session.controller and not (self.stop_thread and self.stop_thread.is_alive()):
            self.stop_thread=threading.Thread(target=lambda:self.session.controller.stop("manual UI stop"),
                    name="boris-ui-stop",daemon=False)
            self.stop_thread.start()

    def poll(self):
        try:
            while True:
                kind,value=self.messages.get_nowait()
                if kind=="progress":
                    self.status.set(f"{value['temperature']} °C · {value['watts']} W · тайлов {value['tiles']} · смен nonce {value['nonces']} · {value['state']}")
                elif kind=="result":
                    self.show(json.dumps(value,indent=2,ensure_ascii=False))
                    self.status.set("Локальный тест завершён. Процесс остановлен; отчёт ниже.")
                elif kind=="error":
                    self.status.set("Локальный тест недоступен или остановлен: "+value)
                elif kind=="devices":
                    previous=self.device_map.get(self.device.get(),self.device.get())
                    self.device_map={f"CUDA{d.cuda_index} · {d.name} · CC{d.cc_major}.{d.cc_minor} · {d.uuid[-8:]}":d.uuid for d in value}
                    self.device_box["values"]=tuple(self.device_map)
                    match=next((label for label,uuid in self.device_map.items() if uuid==previous),None)
                    if match:self.device.set(match)
                    elif isinstance(previous,str) and previous.isdecimal():
                        candidate=next((d for d in value if d.cuda_index==int(previous)),None)
                        if candidate:self.device.set(next(label for label,uuid in self.device_map.items() if uuid==candidate.uuid))
                    else:self.device.set(previous)
                    self.status.set("GPU найдены. Выберите карту; наличие архитектуры не означает физическую проверку этой модели." if value else "CUDA GPU не видны. Автоматической замены на GPU0 нет.")
                elif kind=="refresh-done":
                    self.refresh_button.state(["!disabled"]);self.start_button.state(["!disabled"])
                elif kind=="done":
                    self.start_button.state(["!disabled"]);self.stop_button.state(["disabled"])
                    self.duration.state(["!disabled"]);self.dutybox.state(["!disabled","readonly"]);self.backend_box.state(["!disabled","readonly"])
                    self.device_box.state(["!disabled"]);self.profile_box.state(["!disabled","readonly"]);self.refresh_button.state(["!disabled"])
        except Empty:
            pass
        alive=any(t and t.is_alive() for t in (self.thread,self.stop_thread,self.refresh_thread))
        if self.closing and not alive:
            self.root.destroy()
            return
        self.root.after(100,self.poll)

    def close_window(self):
        self.closing=True
        self.stop()


def attach(root,parent,show):
    controls=LocalControls(root,parent,show)
    root.boris_local_controls=controls # owned until close; useful to deterministic GUI checks
    return controls
