"""Read-only inventory. Architecture eligibility is never a compatibility claim."""
import csv
import io
import platform
import subprocess
from dataclasses import asdict
from .devices import discover

MATRIX = [
    {"model": "MSI EdgeXpert / GB10", "cc": "12.1", "os": "Linux ARM64", "backend": "gb10 native ARM64 dense CUDA build plan; upstream TMA reference separate", "status": "7 Rust policy tests and pure safety fixtures; CUDA121/ARM64 build blocked on Windows; physical/OEM tests absent"},
    {"model": "RTX 5090", "cc": "12.0", "os": "Windows x64", "backend": "DP4A/WMMA/MMA + artificial resident grid + official verifier + NVML", "status": "fixed/full-size proof + bounded artificial nonce walk + real sensors/stops PASS; production/live mining unqualified"},
    {"model": "RTX 5090", "cc": "12.0", "os": "Linux x64", "backend": "future Blackwell worker", "status": "not built or tested"},
    {"model": "RTX 4090", "cc": "8.9", "os": "Windows/Linux x64", "backend": "ada89 DP4A/WMMA/MMA build + UUID selection", "status": "Windows binary compiled; not physically tested; Linux build absent"},
    {"model": "RTX 2060 Laptop", "cc": "7.5", "os": "Windows/Linux x64", "backend": "turing75 DP4A/WMMA build; MMA refused; optional laptop guard", "status": "Windows binary compiled; not physically tested; laptop AC, VRAM and thermal profile required"},
    {"model": "other NVIDIA", "cc": "per model", "os": "per target", "backend": "future CUDA adapters", "status": "unqualified; do not infer from family name"},
    {"model": "AMD", "cc": "per model", "os": "Windows x64", "backend": "original scalar OpenCL1.2 fixed-fixture prototype; no live adapter", "status": "compiled/executed on NVIDIA only; no AMD GPU/compiler/telemetry qualification; AMD execution refused"},
    {"model": "AMD", "cc": "per model", "os": "Linux x64", "backend": "OpenCL source/API route; future HIP and AMD SMI", "status": "no Linux/AMD build or execution; authorized host and physical telemetry required"},
]


def inventory():
    result = {"system": platform.system(), "machine": platform.machine(), "devices": [], "mining_enabled": False}
    try:
        result["devices"]=[dict(asdict(d),eligible_backends=list(d.eligible_backends),
            index_namespace="CUDA logical index; NVML joined by UUID",qualification="detected/ISA eligible only; physical qualification separate") for d in discover()]
        result["inventory_source"]="reviewed CUDA worker; no GPU kernels"
        return result
    except (OSError,ValueError,RuntimeError,KeyError) as exc:
        result["cuda_probe_error"]=str(exc)
    try:
        probe = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,uuid,driver_version,compute_cap,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8, check=True)
        names = ("index", "name", "uuid", "driver", "compute_capability", "memory_mib")
        for row in csv.reader(io.StringIO(probe.stdout)):
            if len(row) == len(names):
                device = dict(zip(names, (v.strip() for v in row)))
                device["qualification"] = "NVML detection only; unavailable CUDA ordinal must not be inferred"
                device["index_namespace"]="nvidia-smi/NVML index; not a CUDA selector"
                result["devices"].append(device)
    except (OSError, subprocess.SubprocessError) as exc:
        result["probe_error"] = type(exc).__name__
    return result
