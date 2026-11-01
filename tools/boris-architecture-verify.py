#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build Turing/Ada profiles and verify only bounded artificial work on the actual installed GPU."""
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from boris_pearl.config import Config
from boris_pearl.core import Job,Router,Session
from boris_pearl.cuda_worker import CudaWorker,WorkerJob
from boris_pearl.devices import discover,select_device
from boris_pearl.ipc import ProcessWorker,WorkerError
from boris_pearl.local_search import HEADER,FIXTURE_ADDRESS
from boris_pearl.official_bridge import OfficialVerifierCallback
from boris_pearl.safety import Guard
from boris_pearl.telemetry import TelemetryMonitor

OUT=ROOT/"docs/boris/evidence/architectures"
BIN=ROOT/"production_worker/target/release"/("boris-pearl-worker.exe" if sys.platform=="win32" else "boris-pearl-worker")
OUTPUT=ROOT/"native/out"
ENV=dict(os.environ,CARGO_HOME=str(ROOT/".cargo-proof-offline"),CARGO_TARGET_DIR=str(ROOT/"production_worker/target"))


def build(profile):
    env=dict(ENV,BORIS_CUDA_PROFILE=profile)
    cmd=["cargo","+stable","build","--locked","--offline","--release","--manifest-path","production_worker/Cargo.toml"]
    r=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=180)
    (OUT/(profile+"-build.txt")).write_text(r.stdout+r.stderr,encoding="utf-8")
    if r.returncode:raise RuntimeError(f"{profile} build failed; see exact log")
    copied=OUTPUT/(f"boris-worker-{profile}"+(".exe" if sys.platform=="win32" else ""))
    shutil.copyfile(BIN,copied)
    return copied,sha256(copied.read_bytes()).hexdigest()


def inspect(binary,profile):
    result={}
    for option in ("--list-elf","--list-ptx"):
        r=subprocess.run(["cuobjdump",option,str(binary)],capture_output=True,text=True,timeout=30)
        if r.returncode:raise RuntimeError("cuobjdump failed:"+r.stderr)
        result[option]=r.stdout.splitlines()
    expected="sm_75" if profile=="turing75" else "sm_89"
    assert result["--list-elf"] and all(expected in x for x in result["--list-elf"])
    assert result["--list-ptx"] and all(expected in x for x in result["--list-ptx"])
    r=subprocess.run(["cuobjdump","--dump-sass",str(binary)],capture_output=True,text=True,timeout=30)
    assert r.returncode==0
    result["tensor_opcodes"]=sorted(set(line[line.index("IMMA."):].split(" ")[0] for line in r.stdout.splitlines() if "IMMA." in line))
    assert result["tensor_opcodes"]
    return result


def run_profile(profile,canonical):
    binary,digest=build(profile)
    images=inspect(binary,profile)
    child_env=dict(os.environ,RAYON_NUM_THREADS="2",CUDA_FORCE_PTX_JIT="1",CUDA_CACHE_PATH=str(OUTPUT/("ptx-cache-"+profile)))
    selected=select_device(discover(binary,digest,env=child_env),0)
    monitor=TelemetryMonitor(device_uuid=selected.uuid)
    workers=[];rows=[]
    try:
        monitor.start();monitor.updated.wait(3)
        sample=monitor.sample()
        if sample is None:raise RuntimeError("No fresh telemetry:"+str(monitor.last_error))
        if sample.power_limit_watts is None or 125>sample.power_limit_watts*.8:
            raise RuntimeError("125W test threshold exceeds80% driver limit; no automatic hardware adjustment")
        guard=Guard(stop_at=time.time()+90,max_watts=125,min_free_bytes=1<<30,max_gpu_c=75,max_gpu_util=85,laptop=True)
        guard.start()
        permit=lambda:guard.reason(time.time(),monitor.sample())
        if permit():raise RuntimeError("Actual GPU safety preflight:"+str(permit()))
        backends=list(selected.eligible_backends)
        expected=["dp4a","wmma-int8"] if profile=="turing75" else ["dp4a","wmma-int8","mma-int8"]
        assert backends==expected
        w=ProcessWorker([str(binary)],expected_mode="cuda-baseline",env=child_env);workers.append(w)
        w.start(permit=permit)
        kat=w.request("kernel-selftest",dict(device_index=selected.cuda_index,backends=backends),timeout=30,permit=permit)
        assert kat["status"]=="passed" and len(kat["checks"])==len(backends)*15
        w.close()
        vreceipt=json.loads((ROOT/"docs/boris/evidence/proof-lab/official-build.json").read_text())
        vname="boris-official-proof-check.exe" if sys.platform=="win32" else "boris-official-proof-check"
        rejected=None
        for k in (2048,2176,4096):
            for backend in backends:
                verifier=OfficialVerifierCallback(ROOT/"proof_bridge/target/release"/vname,vreceipt["binary_sha256"])
                cfg=Config(FIXTURE_ADDRESS,"stratum+tcp://127.0.0.1:1")
                router=Router(cfg,verifier);session=Session("arch",1,"user",cfg.user_pool,cfg.user_wallet)
                router.authorize(session);job=Job(session,f"{profile}-{backend}-{k}",sha256(bytes.fromhex(HEADER)).hexdigest())
                router.register(job)
                worker=CudaWorker(binary,digest,verifier,expected_device_uuid=selected.uuid)
                worker.transport.env=child_env;workers.append(worker)
                target=f"{((1<<256)-1)//(128*k):064x}"
                worker.bind_job(job,WorkerJob(HEADER,target,m=64,n=64,k=k,device_index=selected.cuda_index,backend=backend))
                worker.start(permit=permit)
                result=worker.execute((session.identifier,1,job.job_id,job.header_hash),1000000000,permit=permit)
                assert len(result.proofs)==1 and router.route(job,result.proofs[0])==session
                wire=verifier.verified_wire[job]
                if k in canonical:assert wire==canonical[k]
                else:canonical[k]=wire
                rows.append(dict(backend=backend,k=k,official=True,wire_bytes=len(wire),wire_sha256=sha256(wire).hexdigest(),active_ns=result.active_ns))
                worker.close()
        if profile=="turing75":
            w=ProcessWorker([str(binary)],expected_mode="cuda-baseline",env=child_env);workers.append(w);w.start(permit=permit)
            params=dict(header76=HEADER,target=f"{1:064x}",m=64,n=64,k=4096,device_index=selected.cuda_index,
                backend="mma-int8",nonce=0x4b41545f6e6f6e63,binding="must-refuse",certificate_version=3,job_id="fixed",session_id=1,generation=1)
            try:w.request("prepare",params,permit=permit)
            except WorkerError as exc:
                rejected=str(exc);assert "backend unavailable" in rejected
            else:raise AssertionError("Turing profile must not dispatch empty MMA implementation")
            w.close()
        report=dict(status="passed",profile=profile,binary_sha256=digest,compiled_images=images,
            actual_device=selected.public(),execution=f"CUDA_FORCE_PTX_JIT=1 on detected {selected.name} CC{selected.cc_major}.{selected.cc_minor}; target-model qualification separate",
            arithmetic_cases=kat["checks"],official_proofs=rows,rejected_mma=rejected,
            physical_target_gpu_tests=False,external_pool_connections=0,real_submissions=0,settings_changed=False)
        (OUT/(profile+"-results.json")).write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps(dict(profile=profile,status="passed",arithmetic_cases=len(kat["checks"]),official_proofs=len(rows),actual_device=selected.name)),flush=True)
        return report
    finally:
        for w in workers:w.close()
        monitor.close()


def main():
    OUT.mkdir(parents=True,exist_ok=True);OUTPUT.mkdir(parents=True,exist_ok=True)
    summary=dict(status="incomplete",time_utc=datetime.now(timezone.utc).isoformat(),profiles={})
    (OUT/"profile-verification.json").write_text(json.dumps(summary,indent=2)+"\n")
    try:
        canonical={}
        for name in ("turing75","ada89"):
            result=run_profile(name,canonical)
            summary["profiles"][name]={k:v for k,v in result.items() if k not in ("arithmetic_cases","official_proofs")}
        summary["status"]="passed"
    except Exception as exc:
        summary["status"]="failed or blocked";summary["error"]=repr(exc)
        raise
    finally:
        try:
            binary,digest=build("portable")
            summary["restored_portable_binary_sha256"]=digest
            summary["shared_receipt_note"]="Run boris-worker-verify.py to audit/update the shared portable receipt before ordinary local tests"
        except Exception as exc:
            summary["restore_error"]=repr(exc);summary["status"]="failed or blocked"
        (OUT/"profile-verification.json").write_text(json.dumps(summary,indent=2)+"\n")
    return 0 if summary["status"]=="passed" else 3


if __name__=="__main__":raise SystemExit(main())
