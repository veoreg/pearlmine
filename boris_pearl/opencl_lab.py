# SPDX-License-Identifier: Apache-2.0
"""Isolated OpenCL1.2 fixed-fixture laboratory; never a live worker/backend.

Only inventory, compile and internal case IDs are exposed. The parent owns the
process/timeout/sensor guard. AMD execution is not enabled without its telemetry.
"""
import ctypes as C
from dataclasses import asdict,dataclass
import hashlib
import os
from pathlib import Path
import re
import struct
import sys
import time
import uuid as U

from .ipc import read_frame,write_frame
from .opencl_fixtures import arithmetic_case,golden_rows

ROOT=Path(__file__).resolve().parent.parent
SOURCE=ROOT/"native/opencl/fixed_tile.cl"
MODE="opencl-lab"


class OpenClError(RuntimeError):pass


def checked(code,action):
    if code:raise OpenClError(f"{action}: OpenCL error {code}")


def exact_uuid(value):
    if not isinstance(value,str) or re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",value) is None:
        raise ValueError("Complete OpenCL device UUID required; no name/index fallback")
    if U.UUID(value).int==0:raise ValueError("Zero OpenCL UUID is invalid")
    return str(U.UUID(value))


@dataclass(frozen=True)
class OpenClDevice:
    platform_index:int
    device_index:int
    name:str
    vendor:str
    vendor_id:int
    driver:str
    version:str
    opencl_c:str
    uuid:str|None
    compiler_available:bool
    available:bool
    little_endian:bool
    max_group_size:int
    local_bytes:int
    total_bytes:int

    def require_lab_eligible(self):
        if (self.uuid is None or not self.available or not self.compiler_available or not self.little_endian or
                self.max_group_size<128 or self.local_bytes<512 or self.total_bytes<1<<20):
            raise OpenClError("Fixed OpenCL lab requires UUID, available compiler, little-endian and128-item/512-byte work group; no fallback")
        if re.search(r"OpenCL C (1\.[2-9]|[23]\.)",self.opencl_c) is None:
            raise OpenClError("OpenCL C1.2 or newer compiler required")

    def nvidia_sensor_uuid(self):
        self.require_lab_eligible()
        if self.vendor_id!=0x10DE:raise OpenClError("No reviewed AMD/other-vendor telemetry adapter; GPU execution remains blocked")
        return "GPU-"+exact_uuid(self.uuid)

    def public(self):
        value=asdict(self);value.pop("uuid")
        value["qualification"]="OpenCL detection/capability only; AMD physical compatibility unverified"
        return value


def choose(devices,selector):
    wanted=exact_uuid(selector)
    matches=[d for d in devices if d.uuid==wanted]
    if len(matches)!=1:raise OpenClError("OpenCL UUID absent or ambiguous; no automatic fallback")
    matches[0].require_lab_eligible();return matches[0]


class Runtime:
    """Child-only FFI. No install, network, wallet, settings or arbitrary-job input."""
    def __init__(self):
        if sys.platform=="win32":
            path=Path(os.environ.get("SystemRoot",r"C:\Windows"))/"System32/OpenCL.dll"
            self.lib=C.WinDLL(str(path))
        elif sys.platform.startswith("linux"):self.lib=C.CDLL("libOpenCL.so.1")
        else:raise OpenClError("Existing Windows/Linux OpenCL loader required")
        ptr=C.c_void_p;u=C.c_uint;sz=C.c_size_t;pp=C.POINTER(ptr)
        def bind(name,result,args):
            f=getattr(self.lib,name);f.restype=result;f.argtypes=args;setattr(self,name,f)
        bind("clGetPlatformIDs",C.c_int,[u,pp,C.POINTER(u)])
        bind("clGetDeviceIDs",C.c_int,[ptr,C.c_ulonglong,u,pp,C.POINTER(u)])
        bind("clGetDeviceInfo",C.c_int,[ptr,u,sz,ptr,C.POINTER(sz)])
        bind("clCreateContext",ptr,[ptr,u,pp,ptr,ptr,C.POINTER(C.c_int)])
        bind("clCreateCommandQueue",ptr,[ptr,ptr,C.c_ulonglong,C.POINTER(C.c_int)])
        bind("clCreateProgramWithSource",ptr,[ptr,u,C.POINTER(C.c_char_p),C.POINTER(sz),C.POINTER(C.c_int)])
        bind("clBuildProgram",C.c_int,[ptr,u,pp,C.c_char_p,ptr,ptr])
        bind("clGetProgramBuildInfo",C.c_int,[ptr,ptr,u,sz,ptr,C.POINTER(sz)])
        bind("clCreateKernel",ptr,[ptr,C.c_char_p,C.POINTER(C.c_int)])
        bind("clCreateBuffer",ptr,[ptr,C.c_ulonglong,sz,ptr,C.POINTER(C.c_int)])
        bind("clSetKernelArg",C.c_int,[ptr,u,sz,ptr])
        bind("clEnqueueNDRangeKernel",C.c_int,[ptr,ptr,u,C.POINTER(sz),C.POINTER(sz),C.POINTER(sz),u,pp,pp])
        bind("clWaitForEvents",C.c_int,[u,pp])
        bind("clGetEventProfilingInfo",C.c_int,[ptr,u,sz,ptr,C.POINTER(sz)])
        bind("clEnqueueReadBuffer",C.c_int,[ptr,ptr,u,sz,sz,ptr,u,pp,pp])
        for kind in ("MemObject","Event","Kernel","Program","CommandQueue","Context"):bind("clRelease"+kind,C.c_int,[ptr])
        self.devices=[];self.handles={};self.owned=[];self.kernel=None;self.selected=None;self.launches=0
        count=u();code=self.clGetPlatformIDs(0,None,C.byref(count))
        if code==-1001:return
        checked(code,"platform inventory")
        if count.value>16:raise OpenClError("Platform inventory exceeds bound")
        platforms=(ptr*count.value)();checked(self.clGetPlatformIDs(count.value,platforms,None),"platform inventory")
        for pi,p in enumerate(platforms):
            n=u();code=self.clGetDeviceIDs(p,4,0,None,C.byref(n))
            if code==-1:continue
            checked(code,"GPU inventory")
            if n.value>32 or len(self.devices)+n.value>32:raise OpenClError("GPU inventory exceeds bound")
            values=(ptr*n.value)();checked(self.clGetDeviceIDs(p,4,n.value,values,None),"GPU inventory")
            for di,d in enumerate(values):
                def number(key,typ=u):
                    result=typ();checked(self.clGetDeviceInfo(d,key,C.sizeof(result),C.byref(result),None),"device scalar")
                    return result.value
                def text(key):
                    size=sz();checked(self.clGetDeviceInfo(d,key,0,None,C.byref(size)),"device text size")
                    if not 1<=size.value<=16384:raise OpenClError("Device text exceeds bound")
                    buf=C.create_string_buffer(size.value);checked(self.clGetDeviceInfo(d,key,size,buf,None),"device text")
                    return buf.value.decode("utf-8",errors="strict")
                extensions=text(0x1030).split();uid=None
                if "cl_khr_device_uuid" in extensions:
                    value=(C.c_ubyte*16)();checked(self.clGetDeviceInfo(d,0x106A,16,value,None),"device UUID")
                    uid=exact_uuid(str(U.UUID(bytes=bytes(value))))
                desc=OpenClDevice(pi,di,text(0x102B),text(0x102C),number(0x1001),text(0x102D),text(0x102F),text(0x103D),uid,
                    bool(number(0x1028)),bool(number(0x1027)),bool(number(0x1026)),number(0x1004,sz),number(0x1023,C.c_ulonglong),number(0x101F,C.c_ulonglong))
                self.devices.append(desc);self.handles[(pi,di)]=d

    def compile(self,selector,source_sha256):
        if self.selected is not None:raise OpenClError("New explicit child required for another compilation/device")
        desc=choose(self.devices,selector);device=self.handles[(desc.platform_index,desc.device_index)]
        source=SOURCE.read_bytes()
        if hashlib.sha256(source).hexdigest()!=source_sha256:raise OpenClError("OpenCL source hash changed; no compilation")
        error=C.c_int();ds=(C.c_void_p*1)(device)
        def own(handle,kind):
            checked(error.value,"create "+kind)
            if not handle:raise OpenClError("Null OpenCL "+kind)
            self.owned.append((handle,kind));return handle
        self.context=own(self.clCreateContext(None,1,ds,None,None,C.byref(error)),"Context")
        self.queue=own(self.clCreateCommandQueue(self.context,device,2,C.byref(error)),"CommandQueue")
        strings=(C.c_char_p*1)(source);sizes=(C.c_size_t*1)(len(source))
        program=own(self.clCreateProgramWithSource(self.context,1,strings,sizes,C.byref(error)),"Program")
        options=b"-cl-std=CL1.2"
        code=self.clBuildProgram(program,1,ds,options,None,None)
        log=C.create_string_buffer(65536);self.clGetProgramBuildInfo(program,device,0x1183,len(log),log,None)
        if code:raise OpenClError(f"OpenCL compilation failed ({code}): {log.value.decode(errors='replace')}")
        self.kernel=own(self.clCreateKernel(program,b"fixed_tile",C.byref(error)),"Kernel")
        self.selected=desc
        return {"compiled":True,"options":options.decode(),"source_sha256":source_sha256,
                "device":asdict(desc),"build_log":log.value.decode(errors="replace"),"scope":"existing ICD compiler only; no GPU kernel launched"}

    def _tile(self,a,b,k,key):
        if self.kernel is None or self.launches>=32:raise OpenClError("Compile explicitly first; fixed launch limit32")
        if k not in (2048,2176,4096) or len(a)!=8*k or len(b)!=16*k or len(key)!=32:raise OpenClError("Internal fixture size invalid")
        self.selected.nvidia_sensor_uuid()  # AMD remains blocked until a reviewed physical telemetry adapter exists.
        self.launches+=1;buffers=[];event=C.c_void_p()
        try:
            args=[]
            for data in (a,b,key):
                host=C.create_string_buffer(data);error=C.c_int()
                mem=self.clCreateBuffer(self.context,4|32,len(data),host,C.byref(error));checked(error.value,"copy fixture buffer")
                if not mem:raise OpenClError("Null fixture buffer")
                buffers.append(mem);args.append(C.c_void_p(mem))
            error=C.c_int();out=self.clCreateBuffer(self.context,2,96,None,C.byref(error));checked(error.value,"output buffer")
            if not out:raise OpenClError("Null output buffer")
            buffers.append(out)
            args=[args[0],args[1],C.c_uint(k),args[2],C.c_void_p(out)]
            for i,arg in enumerate(args):checked(self.clSetKernelArg(self.kernel,i,C.sizeof(arg),C.byref(arg)),"set fixed argument")
            global_size=(C.c_size_t*1)(128);local_size=(C.c_size_t*1)(128)
            checked(self.clEnqueueNDRangeKernel(self.queue,self.kernel,1,None,global_size,local_size,0,None,C.byref(event)),"launch fixed tile")
            checked(self.clWaitForEvents(1,C.byref(event)),"fixed event wait")
            begin=C.c_ulonglong();end=C.c_ulonglong()
            for parameter,value in ((0x1282,begin),(0x1283,end)):
                checked(self.clGetEventProfilingInfo(event,parameter,C.sizeof(value),C.byref(value),None),"fixed event profile")
            if not 0<begin.value<end.value:raise OpenClError("Invalid OpenCL device event interval")
            output=C.create_string_buffer(96)
            checked(self.clEnqueueReadBuffer(self.queue,out,1,0,96,output,0,None,None),"fixed output read")
            return dict(transcript=list(struct.unpack("<16I",output.raw[:64])),digest=output.raw[64:].hex(),
                active_ns=end.value-begin.value,k=k,launches=self.launches)
        finally:
            if event.value:self.clReleaseEvent(event)
            for mem in reversed(buffers):self.clReleaseMemObject(mem)

    def case(self,case_id):return self._tile(*arithmetic_case(case_id))
    def golden(self,seed,tile_index):return self._tile(*golden_rows(seed,tile_index))

    def close(self):
        self.kernel=None
        for handle,kind in reversed(self.owned):getattr(self,"clRelease"+kind)(handle)
        self.owned=[]


def validate_command(command,params):
    fields={"inventory":set(),"compile":{"uuid","source_sha256"},"case":{"case_id"},"golden":{"seed","tile_index"}}
    if command not in fields or not isinstance(params,dict) or set(params)!=fields[command]:
        raise OpenClError("Only explicit inventory/compile/fixed-case commands; jobs, headers, targets, wallets and mining refused")
    if command=="compile":
        exact_uuid(params["uuid"])
        if not isinstance(params["source_sha256"],str) or re.fullmatch(r"[0-9a-f]{64}",params["source_sha256"]) is None:raise OpenClError("Exact source SHA256 required")
    if command=="case" and (type(params["case_id"]) is not int or not 0<=params["case_id"]<15):raise OpenClError("Fixed case ID outside0..14")
    if command=="golden" and (type(params["seed"]) is not int or params["seed"] not in (1,2,3) or type(params["tile_index"]) is not int or not 0<=params["tile_index"]<8):raise OpenClError("Golden fixture ID outside fixed bounds")


def child():
    runtime=Runtime();begun=time.monotonic();commands=0
    try:
        write_frame(sys.stdout.buffer,{"type":"ready","protocol":1,"mode":MODE})
        while True:
            try:request=read_frame(sys.stdin.buffer)
            except EOFError:return
            commands+=1
            if commands>40 or time.monotonic()-begun>180:raise OpenClError("Fixed laboratory process budget exceeded")
            command=request.get("type");params=request.get("params")
            if type(request.get("id")) is not int:raise OpenClError("Explicit request ID required")
            try:
                validate_command(command,params)
                if command=="inventory":result={"devices":[asdict(d) for d in runtime.devices],"gpu_kernels_launched":runtime.launches}
                elif command=="compile":result=runtime.compile(params["uuid"],params["source_sha256"])
                elif command=="case":result=runtime.case(params["case_id"])
                else:result=runtime.golden(params["seed"],params["tile_index"])
                reply={"type":"reply","mode":MODE,"id":request["id"],"result":result}
            except (OpenClError,ValueError,OSError) as exc:
                reply={"type":"reply","mode":MODE,"id":request["id"],"error":str(exc)[:65536]}
                write_frame(sys.stdout.buffer,reply);return
            write_frame(sys.stdout.buffer,reply)
    finally:runtime.close()


if __name__=="__main__":
    if sys.argv[1:]!=["--child"]:raise SystemExit("Internal fixed OpenCL laboratory; no default GPU action. Use reviewed test harness.")
    child()
