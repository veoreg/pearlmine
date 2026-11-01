"""Read-only NVML sensors in an owned, timeout-bounded subprocess. No settings API.

Windows uses the installed System32 NVML; Linux uses libnvidia-ml.so.1. No third
party Python module, driver install, listening socket, or global environment edit.
"""
import ctypes as C
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import threading
import time

from .ipc import ProcessWorker, WorkerError, read_frame, write_frame
from .safety import Telemetry
from .devices import validate_uuid


class SensorError(RuntimeError):
    pass


class Memory(C.Structure):
    _fields_ = [(n, C.c_ulonglong) for n in ("total", "free", "used")]


class Utilization(C.Structure):
    _fields_ = [("gpu", C.c_uint), ("memory", C.c_uint)]


class ProcessInfo(C.Structure):
    _fields_ = [("pid", C.c_uint), ("usedGpuMemory", C.c_ulonglong),
                ("gpuInstanceId", C.c_uint), ("computeInstanceId", C.c_uint)]


def ac_state(system=None, sysfs=Path("/sys/class/power_supply")):
    """True=battery, False=confirmed external power, None=unknown. Never infer AC."""
    system = sys.platform if system is None else system
    if system == "win32":
        class Power(C.Structure):
            _fields_ = [("ac", C.c_byte), ("flag", C.c_byte), ("percent", C.c_byte),
                        ("reserved", C.c_byte), ("life", C.c_uint), ("full", C.c_uint)]
        status = Power()
        if C.windll.kernel32.GetSystemPowerStatus(C.byref(status)):
            return {0:True, 1:False}.get(status.ac)
        return None
    if system.startswith("linux"):
        try:
            supplies = list(Path(sysfs).iterdir())[:64]
            external = []
            discharging = False
            for supply in supplies:
                kind = (supply / "type").read_text().strip()
                if kind in ("Mains", "USB", "USB_C", "USB_PD"):
                    external.append((supply / "online").read_text().strip())
                elif kind == "Battery":
                    discharging |= (supply / "status").read_text().strip() == "Discharging"
            if "1" in external:
                return False
            if discharging or external and all(v == "0" for v in external):
                return True
        except (OSError, ValueError):
            pass
    return None


class NvmlReader:
    """Child-only reader. Only explicitly listed read APIs are bound."""
    def __init__(self, index=0, *, device_uuid=None):
        if device_uuid is not None:device_uuid=validate_uuid(device_uuid)
        if type(index) is not int or not 0 <= index <= 31:
            raise ValueError("Device index outside 0..31")
        self.index = index
        if sys.platform == "win32":
            path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/nvml.dll"
            self.lib = C.CDLL(str(path))
        elif sys.platform.startswith("linux"):
            self.lib = C.CDLL("libnvidia-ml.so.1")
        else:
            raise SensorError("NVML telemetry supports Windows/Linux only")
        def bind(name, args):
            f = getattr(self.lib, name)
            f.argtypes, f.restype = args, C.c_int
            return f
        self.init = bind("nvmlInit_v2", [])
        self.shutdown = bind("nvmlShutdown", [])
        self.handle = bind("nvmlDeviceGetHandleByIndex_v2", [C.c_uint, C.POINTER(C.c_void_p)])
        self.handle_uuid = bind("nvmlDeviceGetHandleByUUID", [C.c_char_p,C.POINTER(C.c_void_p)])
        self.get_index = bind("nvmlDeviceGetIndex", [C.c_void_p,C.POINTER(C.c_uint)])
        self.name = bind("nvmlDeviceGetName", [C.c_void_p, C.c_char_p, C.c_uint])
        self.uuid = bind("nvmlDeviceGetUUID", [C.c_void_p, C.c_char_p, C.c_uint])
        self.temperature = bind("nvmlDeviceGetTemperature", [C.c_void_p, C.c_uint, C.POINTER(C.c_uint)])
        self.power = bind("nvmlDeviceGetPowerUsage", [C.c_void_p, C.POINTER(C.c_uint)])
        self.limit = bind("nvmlDeviceGetEnforcedPowerLimit", [C.c_void_p, C.POINTER(C.c_uint)])
        self.memory = bind("nvmlDeviceGetMemoryInfo", [C.c_void_p, C.POINTER(Memory)])
        self.util = bind("nvmlDeviceGetUtilizationRates", [C.c_void_p, C.POINTER(Utilization)])
        try:
            self.processes = bind("nvmlDeviceGetComputeRunningProcesses_v3", [C.c_void_p, C.POINTER(C.c_uint), C.POINTER(ProcessInfo)])
        except AttributeError:
            self.processes = None
        if self.init() != 0:
            raise SensorError("NVML initialization failed")
        self.device = C.c_void_p()
        selected=(self.handle_uuid(device_uuid.encode("ascii"),C.byref(self.device)) if device_uuid else
                  self.handle(index,C.byref(self.device)))
        if selected != 0:
            self.shutdown()
            raise SensorError("Requested NVML device unavailable")
        name, uuid = C.create_string_buffer(128), C.create_string_buffer(128)
        if self.name(self.device, name, 128) or self.uuid(self.device, uuid, 128):
            self.shutdown()
            raise SensorError("NVML device identity unavailable")
        actual_index=C.c_uint()
        if self.get_index(self.device,C.byref(actual_index)) or (device_uuid and uuid.value.decode().lower()!=device_uuid.lower()):
            self.shutdown()
            raise SensorError("Selected NVML UUID/index identity could not be confirmed")
        self.identity = {"index":actual_index.value,"name":name.value.decode(),"uuid":uuid.value.decode()}

    def sample(self):
        errors = []
        def scalar(f, label, *args):
            value = C.c_uint()
            code = f(self.device, *args, C.byref(value))
            if code:
                errors.append({"sensor":label,"nvml_code":code})
                return None
            return value.value
        gpu_c = scalar(self.temperature, "gpu_c", C.c_uint(0))
        mw, limit = scalar(self.power, "power"), scalar(self.limit, "power_limit")
        memory, util = Memory(), Utilization()
        mc, uc = self.memory(self.device, C.byref(memory)), self.util(self.device, C.byref(util))
        if mc:
            errors.append({"sensor":"memory","nvml_code":mc})
        if uc:
            errors.append({"sensor":"utilization","nvml_code":uc})
        pids = None
        if self.processes:
            count = C.c_uint(256)
            values = (ProcessInfo * 256)()
            code = self.processes(self.device, C.byref(count), values)
            if code == 0 and count.value <= 256:
                pids = [values[i].pid for i in range(count.value)]
        sample = Telemetry(time.time(), gpu_c, None if mw is None else mw/1000,
                None if mc else memory.free, on_battery=ac_state(),
                gpu_util_percent=None if uc else util.gpu, total_bytes=None if mc else memory.total,
                power_limit_watts=None if limit is None else limit/1000)
        return {"sample":asdict(sample),"device":self.identity,"errors":errors,"compute_pids":pids,
                "power_semantics":"NVML sensor; newer GPUs can report a 1-second average; not an instantaneous hardware cap"}

    def close(self):
        self.shutdown()


class TelemetryMonitor:
    """Nonblocking parent sample; slow/stuck driver queries cannot stall GPU stop."""
    def __init__(self, device_index=0, *, device_uuid=None, interval=0.5, timeout=1.0, argv=None):
        if not 0.1 <= interval <= 1 or not 0.2 <= timeout <= 2:
            raise ValueError("Sensor polling/timeout outside safe bounds")
        self.interval, self.timeout = interval, timeout
        if device_uuid is not None:
            if argv is not None:raise ValueError("Custom telemetry child cannot replace a UUID-bound physical reader")
            device_uuid=validate_uuid(device_uuid)
            child_argv=[sys.executable,"-m","boris_pearl.telemetry","--child-uuid",device_uuid]
        else:
            if type(device_index) is not int or not 0<=device_index<=31:raise ValueError("NVML index outside0..31")
            child_argv=[sys.executable,"-m","boris_pearl.telemetry","--child",str(device_index)]
        self.worker = ProcessWorker(argv or child_argv,expected_mode="telemetry-nvml")
        self.stopped = threading.Event()
        self.updated = threading.Event()
        self.lock = threading.Lock()
        self.latest, self.received_at, self.last_error = None, None, None
        self.thread = None

    def _run(self):
        try:
            self.worker.start(timeout=self.timeout, permit=lambda: "telemetry stopped" if self.stopped.is_set() else None)
            while not self.stopped.is_set():
                result = self.worker.request("sample", {}, timeout=self.timeout,
                    permit=lambda: "telemetry stopped" if self.stopped.is_set() else None)
                sample = Telemetry(**result["sample"])
                with self.lock:
                    if self.latest and result["device"] != self.latest[1]["device"]:
                        raise SensorError("NVML device changed during the session")
                    self.latest = (sample, result)
                    self.received_at = time.monotonic()
                self.updated.set()
                self.stopped.wait(self.interval)
        except (WorkerError, SensorError, OSError, ValueError, TypeError, KeyError) as exc:
            self.last_error = str(exc)
        finally:
            with self.lock:
                self.latest = None
            self.updated.set()
            self.worker.close()

    def start(self):
        if self.thread is not None or self.stopped.is_set():
            raise ValueError("Telemetry requires a new explicitly started monitor")
        self.thread = threading.Thread(target=self._run, name="boris-telemetry-monitor", daemon=True)
        self.thread.start()

    def sample(self):
        with self.lock:
            if self.latest and time.monotonic()-self.received_at <= 2:
                return self.latest[0]
        return None

    def details(self):
        with self.lock:
            return deepcopy(self.latest[1]) if self.latest and time.monotonic()-self.received_at <= 2 else None

    def close(self):
        self.stopped.set()
        self.worker.close()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)


def child(index=0,device_uuid=None):
    reader = NvmlReader(index,device_uuid=device_uuid)
    try:
        write_frame(sys.stdout.buffer, {"type":"ready","protocol":1,"mode":"telemetry-nvml"})
        while True:
            try:
                request = read_frame(sys.stdin.buffer)
            except EOFError:
                return
            if request.get("type") != "sample" or type(request.get("id")) is not int:
                raise SensorError("Invalid telemetry request")
            write_frame(sys.stdout.buffer, {"type":"reply","mode":"telemetry-nvml","id":request["id"],"result":reader.sample()})
    finally:
        reader.close()


if __name__ == "__main__":
    if len(sys.argv)!=3 or sys.argv[1] not in ("--child","--child-uuid"):
        raise SystemExit("Internal read-only telemetry child; use the explicit sensors command")
    child(device_uuid=sys.argv[2]) if sys.argv[1]=="--child-uuid" else child(int(sys.argv[2]))
