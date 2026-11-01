# SPDX-License-Identifier: Apache-2.0
"""Read-only GB10 diagnostics. Never authorizes, builds or starts GPU work.

System memory and raw OEM thermal observations remain diagnostics; they do not
replace the existing fail-closed NVML memory/board/power requirements.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time

from .devices import UUID, validate_uuid
from .telemetry import TelemetryMonitor


def read_meminfo(path=Path("/proc/meminfo")):
    """No swap counted as GPU allocation headroom; malformed/duplicate fields fail."""
    text=Path(path).read_text(encoding="ascii")
    if len(text)>65536:raise ValueError("meminfo exceeds diagnostic bound")
    found={}
    for line in text.splitlines():
        if line.split(":",1)[0] not in ("MemTotal","MemAvailable","SwapFree"):continue
        match=re.fullmatch(r"(MemTotal|MemAvailable|SwapFree):\s+(\d+) kB",line)
        if match is None or match[1] in found:raise ValueError("Malformed/duplicate memory field")
        value=int(match[2])*1024
        if value>1<<60:raise ValueError("Memory field outside diagnostic bound")
        found[match[1]]=value
    if not {"MemTotal","MemAvailable"}<=found.keys() or not 0<found["MemTotal"] or not 0<=found["MemAvailable"]<=found["MemTotal"]:
        raise ValueError("Missing or inconsistent MemTotal/MemAvailable")
    return {"source":"/proc/meminfo","total_bytes":found["MemTotal"],
            "available_bytes":found["MemAvailable"],"swap_free_bytes":found.get("SwapFree"),
            "scope":"system shared DRAM observation; not a CUDA allocation guarantee or NVML VRAM replacement"}


def read_thermal_zones(root=Path("/sys/class/thermal")):
    zones=sorted(p for p in Path(root).iterdir() if re.fullmatch(r"thermal_zone\d+",p.name))
    if len(zones)>64:raise ValueError("Thermal inventory exceeds bound")
    result=[]
    for zone in zones:
        row={"zone":zone.name,"qualified_as_board_sensor":False}
        try:
            kind=(zone/"type").read_text(encoding="ascii").strip()
            raw=(zone/"temp").read_text(encoding="ascii").strip()
            if not 1<=len(kind)<=128 or re.fullmatch(r"[ -~]+",kind) is None or re.fullmatch(r"-?\d{1,6}",raw) is None:
                raise ValueError("Invalid thermal type/value")
            celsius=int(raw)/1000
            if not -20<=celsius<=150:raise ValueError("Thermal value outside plausible diagnostic range")
            row.update(type=kind,celsius=celsius)
        except (OSError,UnicodeError,ValueError) as exc:row["error"]=str(exc)
        result.append(row)
    return result


def command_probe(argv):
    executable=shutil.which(argv[0])
    if executable is None:return {"command":argv,"status":"missing"}
    try:
        env=dict(os.environ,RUSTUP_AUTO_INSTALL="0")
        done=subprocess.run([executable,*argv[1:]],capture_output=True,text=True,
            encoding="utf-8",errors="replace",timeout=5,env=env)
        return {"command":argv,"exit_code":done.returncode,
                "stdout":UUID.sub("GPU-REDACTED",done.stdout[:16384]),
                "stderr":UUID.sub("GPU-REDACTED",done.stderr[:4096])}
    except (OSError,subprocess.TimeoutExpired) as exc:return {"command":argv,"status":"failed","error":str(exc)}


def collect(device_uuid=None):
    if device_uuid is not None:device_uuid=validate_uuid(device_uuid)
    report={"schema":1,"time_utc_epoch":time.time(),"system":platform.system(),
            "machine":platform.machine(),"scope":"read-only preflight; no GPU kernels, allocation, pool, wallet, settings writes or install",
            "gb10_runtime_qualified":False,"launch_authorized":False,"blockers":[]}
    if report["system"]!="Linux" or report["machine"].lower() not in ("aarch64","arm64"):
        report["blockers"].append("Requires the actual GB10 Linux ARM64 host; no WSL/container/cross-compiler fallback attempted")
        return report
    for key,reader in (("system_memory",read_meminfo),("thermal_observations",read_thermal_zones)):
        try:report[key]=reader()
        except (OSError,UnicodeError,ValueError) as exc:report[key]={"error":str(exc)}
    report["tools"]=[command_probe(argv) for argv in (
        ["nvcc","--version"],["nvcc","--list-gpu-arch"],
        ["rustc","+stable","--version"],["rustup","target","list","--installed","--toolchain","stable"],
        ["nvidia-smi","--query-gpu=name,compute_cap,driver_version","--format=csv,noheader"])]
    monitor=TelemetryMonitor(device_uuid=device_uuid) if device_uuid else TelemetryMonitor(device_index=0)
    try:
        monitor.start();monitor.updated.wait(3)
        details=monitor.details();sample=monitor.sample()
        if details and sample:
            details["device"].pop("uuid",None);details.pop("compute_pids",None)
            report["nvml"]=details
        else:report["nvml"]={"error":monitor.last_error or "Fresh sensor sample unavailable"}
    finally:monitor.close()
    report["blockers"].extend([
        "Board/OEM sensor mapping, power measurement scope and conservative threshold need review on this exact MSI/Spark; raw thermal zones are not accepted as qualified sensors",
        "Unified-memory admission must be validated on hardware; /proc/MemAvailable and swap are never silently substituted for NVML GPU free memory",
        "Native ARM64 worker/verifier build, CPU/GPU/official-proof parity, stop faults and bounded thermal validation remain unverified",
        "Real mining/pool submissions remain disabled and require separate explicit authorization"])
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-uuid",help="Exact physical GPU UUID; otherwise diagnostic NVML index0 is explicitly used")
    args=parser.parse_args()
    print(json.dumps(collect(args.device_uuid),indent=2,ensure_ascii=False))
    return 2  # diagnostics cannot certify or enable an unqualified GB10 runtime


if __name__=="__main__":raise SystemExit(main())
