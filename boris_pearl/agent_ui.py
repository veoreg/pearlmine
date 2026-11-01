# SPDX-License-Identifier: Apache-2.0
"""Visible, explicitly enabled owned-device agent; close always stops its work."""
from dataclasses import asdict
import ipaddress
import json
from queue import Empty,Queue
import socket
import threading
import time
import tkinter as tk
from tkinter import ttk
from . import theme
from .control_center import DeviceService,ROOT,atomic_json,human_error
from .remote_control import Pairing,ControlState,initialize,make_server


def local_addresses():
    """Addresses of this computer, VPN (100.64/10) first; 127.0.0.1 stays available."""
    try:found={row[4][0] for row in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET)}
    except OSError:found=set()
    rows=[a for a in found if not ipaddress.ip_address(a).is_loopback and not ipaddress.ip_address(a).is_global]
    vpn=ipaddress.ip_network("100.64.0.0/10")
    rows.sort(key=lambda a:(ipaddress.ip_address(a) not in vpn,a))
    return rows+["127.0.0.1"]


class AgentWindow:
    def __init__(self,window):
        self.window=window;self.thread=None;self.stop_thread=None;self.server=None;self.state=None;self.closing=False
        self.cancel=threading.Event();self.events=Queue();self.pairing=None;self.serving=threading.Event()
        theme.apply(window);window.title("PearlMine — подключить этот компьютер");window.geometry("740x620");window.minsize(640,560)
        panel=ttk.Frame(window,padding=22);panel.pack(fill="both",expand=True)
        ttk.Label(panel,text="Управление этим компьютером",font=("Segoe UI",18,"bold")).pack(anchor="w")
        ttk.Label(panel,text="Включите агент вручную и перенесите код в свою основную панель. Для интернета используйте адрес в вашей частной VPN-сети.",wraplength=680).pack(anchor="w",pady=(10,16))
        ttk.Label(panel,text="Имя устройства").pack(anchor="w");self.name=tk.StringVar(value=socket.gethostname());ttk.Entry(panel,textvariable=self.name).pack(fill="x",pady=(4,12))
        ttk.Label(panel,text="Локальный / VPN IP этого компьютера").pack(anchor="w");addresses=local_addresses();vpn=[a for a in addresses if ipaddress.ip_address(a) in ipaddress.ip_network("100.64.0.0/10")];self.host=tk.StringVar(value=vpn[0] if vpn else "127.0.0.1");ttk.Combobox(panel,textvariable=self.host,values=addresses).pack(fill="x",pady=(4,10))
        ttk.Label(panel,text="127.0.0.1 доступен только на этом ПК. Для другого компьютера выберите адрес частной VPN-сети, обычно 100.x.x.x. Windows может один раз спросить разрешение брандмауэра: решение за вами.",wraplength=680).pack(anchor="w")
        self.allow=tk.BooleanVar(value=False);ttk.Checkbutton(panel,text="Разрешить короткие GPU-тесты (без майнинга)",variable=self.allow).pack(anchor="w",pady=14)
        row=ttk.Frame(panel);row.pack(fill="x")
        self.start_button=ttk.Button(row,text="Включить агент",command=self.start);self.start_button.pack(side="left")
        self.stop_button=ttk.Button(row,text="Остановить агент",command=self.stop,state="disabled");self.stop_button.pack(side="left",padx=8)
        ttk.Button(row,text="Копировать код",command=self.copy).pack(side="right")
        self.status=tk.StringVar(value="Агент выключен. Никаких сетевых подключений и вычислений.")
        ttk.Label(panel,textvariable=self.status,wraplength=680).pack(anchor="w",pady=(14,8))
        self.code=tk.Text(panel,height=8,wrap="word",font=("Consolas",9),bg=theme.SURFACE,fg=theme.TEXT,insertbackground=theme.TEXT,relief="flat",highlightthickness=1,highlightbackground=theme.LINE);self.code.pack(fill="both",expand=True)
        ttk.Label(panel,text="Код даёт доступ к агенту: не публикуйте его. При закрытии окна агент и его тесты остановятся.",wraplength=680).pack(anchor="w",pady=(10,0))
        window.protocol("WM_DELETE_WINDOW",self.close);window.after(100,self.poll)

    def start(self):
        if self.closing or self.thread and self.thread.is_alive():return
        try:
            host=self.host.get().strip();address=ipaddress.ip_address(host)
            if address.is_unspecified or address.is_multicast or address.is_global:raise ValueError("Укажите конкретный локальный или VPN IP, не публичный адрес и не 0.0.0.0.")
            name=self.name.get().strip()
            if not name or len(name)>80:raise ValueError("Имя устройства должно быть от 1 до 80 символов.")
            allow=self.allow.get()
        except ValueError as exc:self.status.set(str(exc));return
        self.cancel.clear();self.start_button.state(["disabled"]);self.stop_button.state(["!disabled"]);self.status.set("Готовлю защищённое подключение…")
        def run():
            service=None
            try:
                folder=ROOT/"state/agent";url=f"https://[{host}]:8431" if ":" in host else f"https://{host}:8431"
                if (folder/"pairing.json").is_file():
                    old=Pairing.parse((folder/"pairing.json").read_text(encoding="utf-8"))
                    pairing=Pairing(name,url,old.fingerprint,old.token);atomic_json(folder/"pairing.json",asdict(pairing))
                else:pairing=initialize(folder,name,url)
                service=DeviceService();self.state=ControlState(service,pairing.token,allow_tests=allow)
                self.server=make_server((host,8431),folder/"agent-cert.pem",folder/"agent-key.pem",self.state)
                self.pairing=pairing;self.events.put(("ready",json.dumps(asdict(pairing),indent=2,ensure_ascii=False)))
                if not self.cancel.is_set():
                    self.serving.set();self.server.serve_forever(poll_interval=.1)
            except Exception as exc:self.events.put(("error",human_error(exc)))
            finally:
                self.serving.clear()
                if self.server:self.server.server_close();self.server=None
                if self.state:self.state.close();self.state=None
                if service:
                    for thread in list(service.threads):thread.join(timeout=5)
                self.events.put(("done",None))
        self.thread=threading.Thread(target=run,name="boris-visible-agent",daemon=False);self.thread.start()

    def stop(self):
        self.cancel.set();self.stop_button.state(["disabled"]);self.status.set("Останавливаю агент и его тесты…")
        if self.stop_thread and self.stop_thread.is_alive():return
        def work():
            state=self.state
            if state:state.service.stop()
            # Wait out a start in progress: shutdown() needs serve_forever() to be entered or never reached.
            while self.thread and self.thread.is_alive() and not self.serving.is_set():time.sleep(.05)
            server=self.server
            if server and self.serving.is_set():server.shutdown()
        self.stop_thread=threading.Thread(target=work,name="boris-visible-agent-stop",daemon=False);self.stop_thread.start()

    def copy(self):
        if self.pairing:self.window.clipboard_clear();self.window.clipboard_append(json.dumps(asdict(self.pairing),ensure_ascii=False));self.status.set("Код скопирован. Вставьте его только в свою основную панель.")

    def alive(self):return bool(self.thread and self.thread.is_alive() or self.stop_thread and self.stop_thread.is_alive())
    def close(self):self.closing=True;self.stop()
    def poll(self):
        try:
            while True:
                kind,value=self.events.get_nowait()
                if kind=="ready":self.code.delete("1.0","end");self.code.insert("1.0",value);self.status.set("Агент включён. Подключение владельца ожидается; GPU пока не запущены.")
                elif kind=="error":self.status.set(value)
                elif kind=="done":
                    self.start_button.state(["!disabled"]);self.stop_button.state(["disabled"]);self.pairing=None;self.code.delete("1.0","end")
                    if not self.closing:self.status.set("Агент выключен; его работа остановлена.")
        except Empty:pass
        if self.closing and not self.alive():self.window.destroy();return
        self.window.after(100,self.poll)


def main():
    root=tk.Tk();AgentWindow(root);root.mainloop()


if __name__=="__main__":main()
