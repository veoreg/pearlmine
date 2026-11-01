#!/usr/bin/env python3
"""Hidden UI start/stop/close lifecycle with a synthetic session, no GPU/NVML."""
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from boris_pearl.local_controls import LocalControls
from boris_pearl.devices import parse_devices


class FakeSession:
    def __init__(self,**kwargs):
        self.options=kwargs
        self.controller=SimpleNamespace(stopped=threading.Event())
        self.controller.stop=lambda reason:self.controller.stopped.set()
        self.monitor=SimpleNamespace(sample=lambda:SimpleNamespace(gpu_c=30,watts=20))
        self.stats={"total_tiles":0,"nonce_changes":0}
        self.closed=False
    def start(self):return self
    def tick(self):
        self.stats["total_tiles"]+=1
        time.sleep(.005)
        return {"state":"offline-result"}
    def stop_work(self,reason):self.controller.stop(reason)
    def report(self):return {"scope":"synthetic GUI test","stop":{"worker_reaped":True}}
    def close(self,reason):self.closed=True;self.controller.stop(reason)


root=tk.Tk();root.withdraw()
seen=[];sessions=[]
def factory(**kwargs):
    session=FakeSession(**kwargs);sessions.append(session);return session
def inventory_fixture():
    return parse_devices({"devices":[dict(cuda_index=i,name=name,uuid=f"GPU-{str(i+1)*8}-{str(i+1)*4}-{str(i+1)*4}-{str(i+1)*4}-{str(i+1)*12}",
        pci_bus_id=f"0000:0{i+1}:00.0",cc_major=major,cc_minor=minor,memory_bytes=6<<30,
        build_profile="portable",build_min_cc=75,build_backend_mask=7)
        for i,name,major,minor in [(0,"RTX4090 fixture",8,9),(1,"RTX2060 Laptop fixture",7,5)]]})
controls=LocalControls(root,ttk.Frame(root),seen.append,session_factory=factory,inventory_provider=inventory_fixture)
controls.seconds.set("5")
controls.post("devices",inventory_fixture())
def begin():
    choices=list(controls.device_map)
    assert len(choices)==2
    controls.device.set(choices[1])
    controls.profile.set("laptop");controls.profile_changed()
    controls.backend.set("wmma-int8")
    controls.start_button.invoke()
    root.after(50,controls.stop_button.invoke)
root.after(150,begin)
deadline=time.monotonic()+3
def finish():
    if time.monotonic()>deadline:
        controls.stop();root.destroy();raise AssertionError("UI worker did not stop")
    if controls.thread and not controls.thread.is_alive():
        assert sessions[0].closed
        assert sessions[0].options=={"seconds":5,"duty":.1,"backend":"wmma-int8",
            "device_index":"GPU-22222222-2222-2222-2222-222222222222","profile":"laptop"}
        controls.close_window()
    else:root.after(20,finish)
root.after(250,finish)
root.mainloop()
assert sessions and sessions[0].closed and not controls.thread.is_alive()
assert not controls.stop_thread or not controls.stop_thread.is_alive()
print("PASS: explicit local GUI start/stop/close, owned threads exited; synthetic session only")
