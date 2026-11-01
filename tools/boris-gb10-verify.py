#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Windows-host GB10 static checks. No GPU kernels, Linux runtime or installers."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/"docs/boris/evidence/gb10"
BUILD=ROOT/"native/out/gb10-static"


def main():
    OUT.mkdir(parents=True,exist_ok=True);BUILD.mkdir(parents=True,exist_ok=True)
    records={}
    def run(name,argv,env=None):
        try:
            r=subprocess.run(argv,cwd=ROOT,env=env,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=45)
            row={"command":argv,"exit_code":r.returncode};output=r.stdout+r.stderr
        except (OSError,subprocess.TimeoutExpired) as exc:
            row={"command":argv,"exit_code":None,"error":str(exc)};output=str(exc)
        (OUT/(name+".txt")).write_text(output,encoding="utf-8");records[name]=row
        return row,output
    run("installed-rust-targets",["rustup","target","list","--installed","--toolchain","stable"])
    run("nvcc-targets",["nvcc","--list-gpu-arch"])
    compiled,_=run("build-policy-compile",["rustc","+stable","--edition=2021","--test","production_worker/build_plan.rs","-o",str(BUILD/"build-policy-tests.exe")])
    if compiled["exit_code"]==0:run("build-policy-tests",[str(BUILD/"build-policy-tests.exe")])
    csource=BUILD/"probe.cu";csource.write_text("__global__ void probe(unsigned *p) { if(threadIdx.x==0) p[0]=1; }\n")
    row,text=run("cuda121-compile-probe",["nvcc","--ptx","-arch=compute_121",str(csource),"-o",str(BUILD/"probe.ptx")])
    row["classification"]="blocked: installed CUDA12.4 lacks compute_121" if row["exit_code"]!=0 and ("Unsupported gpu architecture" in text or "not defined for option" in text and "compute_121" in text) else "inspect result; not a GB10 worker build"
    rsource=BUILD/"probe.rs";rsource.write_text("fn main() {}\n")
    row,text=run("arm64-rust-probe",["rustc","+stable","--target","aarch64-unknown-linux-gnu","--emit=metadata",str(rsource),"-o",str(BUILD/"probe.rmeta")])
    row["classification"]="blocked: ARM64 std/sysroot absent" if row["exit_code"]!=0 and "E0463" in text else "inspect result; not an ARM64 worker build"
    compiled,_=run("build-script-compile",["rustc","+stable","--edition=2021","production_worker/build.rs","-o",str(BUILD/"build-script.exe")])
    if compiled["exit_code"]==0:
        env=dict(os.environ,BORIS_CUDA_PROFILE="gb10",CARGO_MANIFEST_DIR=str(ROOT/"production_worker"),OUT_DIR=str(BUILD),CARGO_CFG_TARGET_OS="windows",CARGO_CFG_TARGET_ARCH="x86_64",HOST="x86_64-pc-windows-msvc",TARGET="x86_64-pc-windows-msvc")
        row,text=run("wrong-host-rejection",[str(BUILD/"build-script.exe")],env)
        row["expected_rejection_confirmed"]=row["exit_code"]!=0 and "gb10 requires a native aarch64-unknown-linux-gnu host" in text
    row,text=run("preflight-wrong-host",[sys.executable,"-m","boris_pearl.gb10_preflight"])
    row["expected_rejection_confirmed"]=row["exit_code"]==2 and not json.loads(text)["launch_authorized"]
    run("portable-tests",[sys.executable,"-m","unittest","discover","-s","tests_portable","-v"])
    run("rustfmt",["rustfmt","+stable","--edition","2021","--check","production_worker/build.rs","production_worker/build_plan.rs"])
    sources=["production_worker/build.rs","production_worker/build_plan.rs","native/worker_kernel.cu","boris_pearl/devices.py","boris_pearl/local_search.py","boris_pearl/gb10_preflight.py","tests_portable/test_gb10.py", "tests_portable/test_devices.py","tools/boris-gb10-verify.py"]
    report={"time_utc":datetime.now(timezone.utc).isoformat(),"scope":"Windows x64 pure/static/compiler-negative checks only; no Linux execution or GB10 GPU test", "checks":records,
        "source_sha256":{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources},
        "gb10_native_build":"blocked: no native Linux ARM64 host/sysroot; installed nvcc12.4 cannot emit compute_121",
        "prior_wsl_denial":"Wsl/EnumerateDistros/Service/E_ACCESSDENIED; not retried or bypassed",
        "real_mining_runs":0,"gpu_work_executions_this_script":0,"system_changes":0}
    (OUT/"static-verification.json").write_text(json.dumps(report,indent=2)+"\n")
    for name,row in records.items():print(name+": "+str(row.get("classification",row["exit_code"])))
    required=["build-policy-compile","build-policy-tests","build-script-compile","portable-tests","rustfmt"]
    return 0 if all(records.get(n,{}).get("exit_code")==0 for n in required) and all(records.get(n,{}).get("expected_rejection_confirmed") for n in ["wrong-host-rejection","preflight-wrong-host"]) else 1


if __name__=="__main__":raise SystemExit(main())
