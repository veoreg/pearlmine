#!/usr/bin/env python3
"""Small guarded fixed-fixture timing, not mining hashrate or earnings."""
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from boris_pearl.telemetry import TelemetryMonitor
from boris_pearl.ipc import ProcessWorker
from boris_pearl.safety import Guard
from boris_pearl.local_search import HEADER


def main():
    out=ROOT/"docs/boris/evidence/load"
    out.mkdir(parents=True,exist_ok=True)
    binary=ROOT/"production_worker/target/release"/("boris-pearl-worker.exe" if sys.platform=="win32" else "boris-pearl-worker")
    receipt=json.loads((ROOT/"docs/boris/evidence/worker/worker-build.json").read_text())
    digest=hashlib.sha256(binary.read_bytes()).hexdigest()
    if digest!=receipt["binary_sha256"]:
        raise ValueError("Worker differs from recorded reviewed build")
    monitor=TelemetryMonitor();monitor.start();monitor.updated.wait(3)
    guard=Guard(stop_at=time.time()+30,max_watts=125,min_free_bytes=1<<30,max_gpu_c=75,max_gpu_util=85,laptop=True)
    guard.start()
    permit=lambda:guard.reason(time.time(),monitor.sample())
    worker=ProcessWorker([str(binary)],expected_mode="cuda-baseline",env=dict(os.environ,RAYON_NUM_THREADS="2"))
    rows=[]
    try:
        worker.start(permit=permit)
        for repeat in range(4):
            binding=f"fixed-profile-{repeat}"
            worker.request("prepare",dict(header76=HEADER,target=f"{1:064x}",m=64,n=64,k=4096,
                nonce=0x4b41545f6e6f6e63,device_index=0,binding=binding,certificate_version=3,
                job_id="local",session_id=1,generation=1),permit=permit)
            start=time.perf_counter();active=0
            for _ in range(32):
                result=worker.request("chunk",dict(binding=binding,budget_ns=1000000000),permit=permit)
                assert result["candidate"] is None and result["canary_ok"]
                active+=result["active_ns"]
            rows.append(dict(tiles=32,elapsed_seconds=time.perf_counter()-start,device_ns=active))
        report=dict(scope="fixed64x64x4096 hard artificial header/nonce; no pool or nonce progression",
            binary_sha256=digest,samples=rows)
        previous=out/"before-optimization.json"
        if previous.is_file():
            old=json.loads(previous.read_text())["samples"][1:]
            report["warm_fixed_batch_wall_speed_ratio"]=statistics.median(r["elapsed_seconds"] for r in old)/statistics.median(r["elapsed_seconds"] for r in rows[1:])
            report["warm_fixed_batch_device_speed_ratio"]=statistics.median(r["device_ns"] for r in old)/statistics.median(r["device_ns"] for r in rows[1:])
        report["limits"]="Small fixture timings, warm medians; driver clocks/background activity vary. Not production throughput."
        (out/"after-optimization.json").write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps(report,indent=2))
        return 0
    finally:
        worker.close();monitor.close()


if __name__=="__main__":
    raise SystemExit(main())
