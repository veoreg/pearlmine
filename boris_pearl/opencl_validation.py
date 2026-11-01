# SPDX-License-Identifier: Apache-2.0
"""Owned fixed OpenCL test session. No production/controller/pool integration."""
from dataclasses import asdict
import hashlib
import re
import sys
import threading
import time
from .devices import validate_uuid,discover,select_device
from .ipc import ProcessWorker,WorkerError
from .opencl_lab import MODE,ROOT,SOURCE,OpenClDevice,choose
from .safety import Guard,finite_number
from .telemetry import TelemetryMonitor,SensorError


def checked_result(result,expected,k):
    if (not isinstance(result,dict) or set(result)!={"transcript","digest","active_ns","k","launches"} or
        type(result["active_ns"]) is not int or not 0<result["active_ns"]<=1_000_000_000 or
        type(result["k"]) is not int or result["k"]!=k or type(result["launches"]) is not int or not 1<=result["launches"]<=32 or
        not isinstance(result["transcript"],list) or len(result["transcript"])!=16 or
        any(type(x) is not int or not 0<=x<=0xffffffff for x in result["transcript"]) or
        not isinstance(result["digest"],str) or re.fullmatch(r"[0-9a-f]{64}",result["digest"]) is None):
        raise WorkerError("Invalid bounded OpenCL fixed result/event interval")
    if any(result[name]!=expected[name] for name in ("transcript","digest")):
        raise WorkerError("OpenCL result differs from independent CPU/golden expectation")
    return result


class OpenClValidation:
    def __init__(self,*,seconds=120):
        if type(seconds) not in (int,float) or not 1<=seconds<=180:raise ValueError("Explicit fixed-lab deadline must be1..180s")
        self.seconds=seconds;self.stop_event=threading.Event();self.monitor=None;self.guard=None
        self.worker=ProcessWorker([sys.executable,"-m","boris_pearl.opencl_lab","--child"],expected_mode=MODE)
        self.watchdog=None;self.deadline=time.monotonic()+seconds;self.selected=None;self.samples=[]
        self.source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        self._seen=None

    def permit(self):
        if self.stop_event.is_set():return "explicit stop"
        if time.monotonic()>=self.deadline:return "monotonic deadline"
        if self.monitor:
            sample=self.monitor.sample();details=self.monitor.details()
            if sample and details:
                if validate_uuid(details["device"]["uuid"])!=self.selected.nvidia_sensor_uuid():return "OpenCL/NVML identity changed"
                if sample.observed_at!=self._seen:
                    self._seen=sample.observed_at;self.samples.append(asdict(sample))
            if self.guard:return self.guard.reason(time.time(),sample)
        return None

    def _watch(self):
        while not self.stop_event.wait(.025):
            if self.permit():
                self.stop_event.set()
                if self.guard:self.guard.stop()
                self.worker.close();return

    def inventory(self):
        self.worker.start(timeout=5,permit=self.permit)
        reply=self.worker.request("inventory",{},timeout=5,permit=self.permit)
        if not isinstance(reply.get("devices"),list) or len(reply["devices"])>32 or reply.get("gpu_kernels_launched")!=0:
            raise WorkerError("Invalid idle OpenCL inventory")
        return tuple(OpenClDevice(**d) for d in reply["devices"])

    def compile_nvidia(self,devices,opencl_uuid):
        self.selected=choose(devices,opencl_uuid)
        uuid=self.selected.nvidia_sensor_uuid()  # refuses AMD/other vendors before monitor/context/kernel
        cuda=select_device(discover(),uuid)  # read-only identity; never infer CUDA index from OpenCL ordinal
        if cuda.requires_gb10_guards:raise SensorError("GB10 OpenCL workload blocked: OEM board/power/shared-memory safety is unqualified")
        self.monitor=TelemetryMonitor(device_uuid=uuid);self.monitor.start();self.monitor.updated.wait(3)
        details=self.monitor.details();sample=self.monitor.sample()
        if not details or sample is None or validate_uuid(details["device"]["uuid"])!=uuid:
            raise SensorError("Fresh exact OpenCL/NVML UUID join required")
        limit=sample.power_limit_watts
        if not finite_number(limit) or not 0<limit:raise SensorError("Known driver power limit required")
        self.guard=Guard(stop_at=time.time()+max(0,self.deadline-time.monotonic()),max_watts=min(125,limit*.8),min_free_bytes=1<<30,
                         max_gpu_c=75,max_gpu_util=85,laptop=True)
        self.guard.start()
        reason=self.permit()
        if reason:raise SensorError(reason)
        self.watchdog=threading.Thread(target=self._watch,name="boris-opencl-stop-watch",daemon=True);self.watchdog.start()
        result=self.worker.request("compile",dict(uuid=opencl_uuid,source_sha256=self.source_sha256),timeout=15,permit=self.permit)
        actual=OpenClDevice(**result["device"])
        if actual!=self.selected or result.get("source_sha256")!=self.source_sha256 or result.get("compiled") is not True:
            self.close();raise WorkerError("OpenCL compilation result is not bound to selected device/source")
        return result

    def request(self,command,params):
        if not self.guard or self.watchdog is None:raise WorkerError("Explicit monitored compilation required")
        return self.worker.request(command,params,timeout=10,permit=self.permit)

    def stop(self):
        self.stop_event.set()
        if self.guard:self.guard.stop()
        self.worker.close()

    def close(self):
        self.stop()
        if self.watchdog and self.watchdog is not threading.current_thread():self.watchdog.join(timeout=2)
        if self.monitor:self.monitor.close()
