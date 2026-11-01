#!/usr/bin/env python3
"""Screenshots of every screen with synthetic devices; no GPU, network or real settings."""
import ctypes
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from PIL import ImageGrab
from boris_pearl.agent_ui import AgentWindow
from boris_pearl.control_center import DeviceService
from boris_pearl.dashboard import Dashboard
from tests_portable.test_control_center import devices,FakeSession
from tests_portable.test_dashboard import PassiveFleet
from tests_portable.test_devices import U1,U2

ctypes.windll.shcore.SetProcessDpiAwareness(1)


def shot(window,path):
    window.attributes("-topmost",True);window.deiconify();window.lift()
    for _ in range(25):window.update();time.sleep(.02)
    x,y,w,h=window.winfo_rootx(),window.winfo_rooty(),window.winfo_width(),window.winfo_height()
    ImageGrab.grab(bbox=(x,y,x+w,y+h),all_screens=True).save(path)
    window.attributes("-topmost",False)


def main(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="boris-shots-") as tmp:
        root=tk.Tk();root.geometry("+40+40")
        service=DeviceService(inventory_provider=devices,session_factory=FakeSession,probe_sensors=False);fleet=PassiveFleet()
        ui=Dashboard(root,service=service,fleet=fleet,auto_discover=False,settings_path=Path(tmp)/"settings.json")
        service.refresh()
        while service.snapshot()["refreshing"]:root.update();time.sleep(.01)
        with service.lock:
            service.records[U1].update(temperature=41,watts=62.0,free_bytes=29<<30,updated_at=time.time())
            service.records[U2].update(temperature=38,watts=18.0,free_bytes=5<<30,updated_at=time.time())
        status=dict(active=0,allow_tests=True,devices={"GPU-spark":dict(name="NVIDIA GB10 (Spark)",state="idle",eligible=False,temperature=46,watts=38.0,note="Нужна проверка Spark")})
        fleet.rows={"a"*64:dict(name="Spark",url="https://100.64.0.2:8431",status=status,error=None,notice=None,updated_at=time.time()),
            "b"*64:dict(name="Ноутбук",url="https://100.64.0.3:8431",status=None,error="Компьютер не отвечает. Проверьте, включён ли агент и VPN.",notice=None,updated_at=None)}
        ui.checked={U1};ui.current_peer="a"*64
        for page in ("home","fleet","settings","logs","help"):
            ui.page(page);fleet.rows["a"*64]["updated_at"]=time.time();shot(root,out/f"{page}.png")
        root.geometry("1060x760")
        for page in ("home","settings","fleet"):
            ui.page(page);fleet.rows["a"*64]["updated_at"]=time.time();shot(root,out/f"min-{page}.png")
        ui.fee.set("2%");ui.page("settings");shot(root,out/"min-settings-fee.png");ui.fee.set("0% — свои тесты")
        top=tk.Toplevel(root);top.geometry("+120+80");agent=AgentWindow(top);shot(top,out/"agent.png");agent.close()
        ui.close()
        deadline=time.monotonic()+5
        try:
            while time.monotonic()<deadline:root.update();time.sleep(.01);root.winfo_exists()
        except tk.TclError:pass
    print("saved to",out)


if __name__=="__main__":main(sys.argv[1])
