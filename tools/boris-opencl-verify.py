#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CPU/read-only inventory by default; bounded NVIDIA OpenCL validation is opt-in."""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from boris_pearl.devices import discover,select_device
from boris_pearl.ipc import WorkerError
from boris_pearl.load import DutyLimiter
from boris_pearl.official_bridge import verify,VerificationBlocked
from boris_pearl.opencl_fixtures import arithmetic_case,golden_rows,reference_tile
from boris_pearl.opencl_validation import OpenClValidation,checked_result
from boris_pearl.proof_lab import build_candidate,fixture
from boris_pearl.telemetry import TelemetryMonitor

OUT=ROOT/"docs/boris/evidence/amd"


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assert_reaped(session):
    pid=session.worker.process.pid
    if session.worker.process.poll() is None:raise RuntimeError("Owned OpenCL child not reaped")
    listed=None
    temporary=None
    monitor=session.monitor
    if monitor and monitor.stopped.is_set():
        temporary=TelemetryMonitor(device_uuid=session.selected.nvidia_sensor_uuid())
        temporary.start();temporary.updated.wait(3);monitor=temporary
    try:
        if monitor:
            before=monitor.details()
            prior=before["sample"]["observed_at"] if before else None
            until=time.monotonic()+2
            while time.monotonic()<until:
                details=monitor.details()
                if details and (temporary or details["sample"]["observed_at"]!=prior):
                    pids=details.get("compute_pids");listed=pid in pids if pids is not None else None;break
                time.sleep(.02)
    finally:
        if temporary:temporary.close()
    if listed is True:raise RuntimeError("Owned OpenCL PID still reported by fresh NVML")
    return dict(worker_reaped=True,worker_still_listed_by_fresh_nvml=listed,
                limitation="PID check does not prove exact VRAM recovery under WDDM")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nvidia-validation",action="store_true",help="execute at most21 fixed one-tile tests on selected existing NVIDIA GPU; never AMD or live data")
    parser.add_argument("--device",default="0",help="Reviewed CUDA logical index or complete NVIDIA UUID, joined to OpenCL by UUID")
    parser.add_argument("--report",type=Path,help="Alternate local evidence JSON path; no publication")
    args=parser.parse_args();OUT.mkdir(parents=True,exist_ok=True)
    report=dict(time_utc=datetime.now(timezone.utc).isoformat(),status="incomplete",
        scope="AMD-oriented vendor-neutral OpenCL prototype; optional execution is explicitly selected NVIDIA validation only",
        system=platform.system(),machine=platform.machine(),
        amd_physical_execution=False,hip_compilation=False,linux_execution=sys.platform.startswith("linux"),
        real_mining_executions=0,pool_connections=0,real_submissions=0,fee_percent=0,
        toolchain={name:shutil.which(name) for name in ("hipcc","amdclang++","clang++","glslangValidator","glslc")},checks={})
    path=args.report if args.report else OUT/"opencl-validation.json";path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2)+"\n")
    session=None
    try:
        cpu=subprocess.run([sys.executable,"-m","unittest","discover","-s","tests_portable","-v"],cwd=ROOT,capture_output=True,text=True,timeout=45)
        (OUT/"portable-tests.txt").write_text(cpu.stdout+cpu.stderr,encoding="utf-8")
        if cpu.returncode:raise RuntimeError("Portable tests failed; no GPU validation")
        report["checks"]["portable_tests"]="passed; see portable-tests.txt"
        session=OpenClValidation(seconds=180);devices=session.inventory()
        report["inventory"]=[d.public() for d in devices]
        report["checks"]["idle_inventory"]="passed; no context, buffer or kernel"
        if not args.nvidia_validation:
            report["status"]="read-only/CPU checks passed; GPU validation not requested";return 0
        cuda=select_device(discover(),args.device);selector=cuda.uuid[4:]
        # Prepare whole CPU Merkle/proofs before monitored GPU work. Still fixed fixtures, no search.
        candidates={seed:build_candidate(seed,0) for seed in (1,2,3)}
        session.close();session=OpenClValidation(seconds=180);devices=session.inventory()
        compiled=session.compile_nvidia(devices,selector);compiled["device"].pop("uuid",None)
        report["compilation"]=compiled
        pace=DutyLimiter(.1);rows=[]
        for case_id in range(15):
            a,b,k,key=arithmetic_case(case_id);expected=reference_tile(a,b,k,key)
            reason=pace.ready(session.permit)
            if reason:raise WorkerError(reason)
            begun=time.monotonic();result=session.request("case",dict(case_id=case_id));pace.record(begun)
            checked_result(result,expected,k);rows.append(dict(case_id=case_id,pattern=case_id%5,**result,matched=True))
        report["arithmetic"]=rows
        verifier=ROOT/"proof_bridge/target/release"/("boris-official-proof-check.exe" if sys.platform=="win32" else "boris-official-proof-check")
        verifier_sha=json.loads((ROOT/"docs/boris/evidence/proof-lab/official-build.json").read_text())["binary_sha256"]
        proofs=[]
        for seed in (1,2,3):
            a,b,k,key=golden_rows(seed,0);expected=reference_tile(a,b,k,key)
            reason=pace.ready(session.permit)
            if reason:raise WorkerError(reason)
            begun=time.monotonic();result=session.request("golden",dict(seed=seed,tile_index=0));pace.record(begun)
            checked_result(result,expected,k)
            known=fixture(seed)["first_tiles"][0]
            if result["transcript"]!=known["transcript"] or result["digest"]!=known["digest"]:raise RuntimeError("Immutable golden mismatch")
            candidate=candidates[seed];candidate["expected_digest"]=result["digest"]
            verified=verify(candidate,verifier,verifier_sha)
            rejected=[]
            for kind in ("digest","leaf"):
                bad=deepcopy(candidate)
                if kind=="digest":bad["expected_digest"]=("00" if result["digest"][:2]!="00" else "01")+result["digest"][2:]
                else:bad["proof"]["a"]["proof"]["leaf_data"][0][0]^=1
                try:verify(bad,verifier,verifier_sha)
                except VerificationBlocked:rejected.append(kind)
                else:raise RuntimeError("Official verifier accepted tampered "+kind)
            proofs.append(dict(seed=seed,**result,matched=True,official_accepted=True,
                proof_bytes=len(verified.wire),wire_sha256=hashlib.sha256(verified.wire).hexdigest(),negative_cases_rejected=rejected))
        report["golden_official_proofs"]=proofs
        report["pacing"]=dict(configured_whole_request_duty=.1,busy_seconds=pace.busy_seconds,waited_seconds=pace.waited_seconds,
            scope="bounded whole-request pacing; not mining throughput or revenue")
        report["sensors"]=list(session.samples)
        session.stop();report["normal_stop"]=assert_reaped(session);session.close();session=None
        stops=[]
        for kind in ("manual","deadline_without_next_request","sensor_loss_without_next_request"):
            local=OpenClValidation(seconds=2 if kind=="deadline_without_next_request" else 15)
            try:
                ds=local.inventory();local.compile_nvidia(ds,selector)
                result=local.request("case",dict(case_id=0));checked_result(result,reference_tile(*arithmetic_case(0)),2048)
                begun=time.monotonic()
                if kind=="manual":local.stop()
                elif kind=="sensor_loss_without_next_request":local.monitor.close()
                if kind!="manual":
                    until=time.monotonic()+4
                    while local.worker.process.poll() is None and time.monotonic()<until:time.sleep(.01)
                reaped_elapsed_ms=(time.monotonic()-begun)*1000
                stopped=assert_reaped(local)
                if not local.stop_event.is_set():raise RuntimeError("OpenCL stop did not latch")
                stops.append(dict(case=kind,reaped_elapsed_ms=reaped_elapsed_ms,confirmation_elapsed_ms=(time.monotonic()-begun)*1000,**stopped))
            finally:local.close()
        report["stops"]=stops
        report["status"]=f"passed on {compiled['device']['name']} / {platform.system()} OpenCL only; AMD remains blocked/unqualified"
        report["verifier_binary_sha256"]=verifier_sha
        return 0
    except Exception as exc:
        report["status"]="failed or blocked";report["error"]=str(exc);raise
    finally:
        if session:session.close()
        sources=["native/opencl/fixed_tile.cl","boris_pearl/opencl_lab.py","boris_pearl/opencl_fixtures.py","boris_pearl/opencl_validation.py","boris_pearl/ipc.py","tests_portable/test_opencl.py","tools/boris-opencl-verify.py"]
        report["source_sha256"]={name:digest(ROOT/name) for name in sources}
        report["completed_utc"]=datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        print(report["status"])
        if "error" in report:print(report["error"])
        print("Evidence: "+str(path))


if __name__=="__main__":raise SystemExit(main())
