# SPDX-License-Identifier: Apache-2.0
"""Read-only CUDA discovery and exact single-device selection. ISA eligibility is not qualification."""
from dataclasses import asdict,dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

from .ipc import ProcessWorker

ROOT=Path(__file__).resolve().parent.parent
UUID=re.compile(r"GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",re.I)
PROFILES={"portable":(75,7),"turing75":(75,3),"ada89":(89,7),"gb10":(121,7)}
BACKENDS=("dp4a","wmma-int8","mma-int8")


class DeviceSelectionError(ValueError):
    pass


def validate_uuid(value):
    if not isinstance(value,str) or UUID.fullmatch(value) is None:
        raise DeviceSelectionError("Use a complete physical GPU UUID; partial IDs/MIG identifiers are unsupported")
    return "GPU-"+value[4:].lower()


@dataclass(frozen=True)
class CudaDevice:
    cuda_index:int
    name:str
    uuid:str
    pci_bus_id:str
    cc_major:int
    cc_minor:int
    memory_bytes:int
    build_profile:str
    build_min_cc:int
    build_backend_mask:int

    @property
    def requires_gb10_guards(self):
        # CC/profile identity remains conservative when an OEM product name omits GB10.
        return (self.cc_major,self.cc_minor)==(12,1) or self.build_profile=="gb10" or "GB10" in self.name.upper()

    @property
    def eligible_backends(self):
        if self.cc_major*10+self.cc_minor<max(75,self.build_min_cc):return ()
        return tuple(name for bit,name in enumerate(BACKENDS)
            if self.build_backend_mask&(1<<bit) and (name!="mma-int8" or self.cc_major>=8))

    def require_backend(self,backend):
        if backend not in self.eligible_backends:
            raise DeviceSelectionError(f"{backend} unavailable for CUDA{self.cuda_index} CC{self.cc_major}.{self.cc_minor} / {self.build_profile}; no fallback")

    def public(self):
        value=asdict(self);value.pop("uuid")
        value["eligible_backends"]=list(self.eligible_backends)
        value["qualification"]="ISA/build eligibility only; no physical compatibility or performance claim"
        return value


def parse_devices(payload):
    rows=payload.get("devices") if isinstance(payload,dict) else None
    if not isinstance(rows,list) or len(rows)>32:
        raise DeviceSelectionError("Invalid bounded CUDA inventory")
    devices=[];indices=set();uuids=set()
    for row in rows:
        try:d=CudaDevice(**row)
        except (TypeError,ValueError) as exc:raise DeviceSelectionError("Malformed CUDA descriptor") from exc
        ints=(d.cuda_index,d.cc_major,d.cc_minor,d.memory_bytes,d.build_min_cc,d.build_backend_mask)
        if (any(type(x) is not int for x in ints) or not 0<=d.cuda_index<=31 or
                not 1<=d.cc_major<=99 or not 0<=d.cc_minor<=9 or d.memory_bytes<1<<20 or
                not isinstance(d.name,str) or not 1<=len(d.name)<=255 or "\0" in d.name or
                not isinstance(d.pci_bus_id,str) or re.fullmatch(r"[0-9a-fA-F]{4,8}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]",d.pci_bus_id) is None or
                not isinstance(d.build_profile,str) or d.build_profile not in PROFILES or PROFILES[d.build_profile]!=(d.build_min_cc,d.build_backend_mask)):
            raise DeviceSelectionError("Invalid capability/build/device descriptor")
        d=CudaDevice(**dict(asdict(d),uuid=validate_uuid(d.uuid)))
        if d.cuda_index in indices or d.uuid in uuids:
            raise DeviceSelectionError("Duplicate/ambiguous device identity; no fallback")
        indices.add(d.cuda_index);uuids.add(d.uuid);devices.append(d)
    return tuple(devices)


def select_device(devices,selector=0):
    if type(selector) is int:
        if not 0<=selector<=31:raise DeviceSelectionError("CUDA logical index must be0..31")
        matches=[d for d in devices if d.cuda_index==selector]
    elif isinstance(selector,str) and selector.isascii() and selector.isdecimal():
        return select_device(devices,int(selector))
    elif isinstance(selector,str):
        uuid=validate_uuid(selector)
        matches=[d for d in devices if d.uuid==uuid]
    else:raise DeviceSelectionError("Choose a CUDA logical index or complete GPU UUID")
    if len(matches)!=1:
        raise DeviceSelectionError("Requested CUDA device not visible or ambiguous; no automatic GPU0 fallback")
    return matches[0]


def current_worker():
    name="boris-pearl-worker.exe" if sys.platform=="win32" else "boris-pearl-worker"
    binary=ROOT/"production_worker/target/release"/name
    receipt=ROOT/"docs/boris/evidence/worker/worker-build.json"
    if not receipt.is_file():raise DeviceSelectionError("Build/review CUDA worker first")
    return binary,json.loads(receipt.read_text())["binary_sha256"]


def discover(binary=None,expected_sha256=None,*,env=None,permit=None):
    if binary is None and expected_sha256 is None:binary,expected_sha256=current_worker()
    if binary is None or expected_sha256 is None:raise DeviceSelectionError("Exact binary and reviewed hash required together")
    binary=Path(binary)
    if not binary.is_file() or sha256(binary.read_bytes()).hexdigest()!=expected_sha256:
        raise DeviceSelectionError("CUDA inventory worker differs from reviewed build")
    worker=ProcessWorker([str(binary.resolve())],expected_mode="cuda-baseline",env=env)
    try:
        worker.start(timeout=3,permit=permit)
        return parse_devices(worker.request("devices",{},timeout=5,permit=permit))
    finally:worker.close()
