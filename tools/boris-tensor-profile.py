#!/usr/bin/env python3
"""Guarded same-binary warm tensor comparison; artificial jobs, no pool/mining."""
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from boris_pearl.ipc import ProcessWorker
from boris_pearl.local_search import HEADER
from boris_pearl.safety import Guard
from boris_pearl.telemetry import TelemetryMonitor


def main():
    out=ROOT/"docs/boris/evidence/tensor";out.mkdir(parents=True,exist_ok=True)
    receipt=json.loads((ROOT/"docs/boris/evidence/worker/worker-build.json").read_text())
    binary=ROOT/"production_worker/target/release"/("boris-pearl-worker.exe" if sys.platform=="win32" else "boris-pearl-worker")
    assert hashlib.sha256(binary.read_bytes()).hexdigest()==receipt["binary_sha256"]
    monitor=TelemetryMonitor();monitor.start();monitor.updated.wait(3)
    guard=Guard(stop_at=time.time()+45,max_watts=125,min_free_bytes=1<<30,max_gpu_c=75,max_gpu_util=85,laptop=True);guard.start()
    permit=lambda:guard.reason(time.time(),monitor.sample())
    worker=ProcessWorker([str(binary)],expected_mode="cuda-baseline")
    variants={"dp4a":4,"wmma-int8":16,"mma-int8":32}
    samples={name:[] for name in variants}
    try:
        worker.start(permit=permit)
        names=list(variants)
        for round_no in range(7):
            order=names[round_no%3:]+names[:round_no%3]
            for name in order:
                binding="profile-"+name
                worker.request("prepare",dict(header76=HEADER,target=f"{1:064x}",m=256,n=256,k=4096,
                    backend=name,nonce=0x4b41545f6e6f6e63,device_index=0,binding=binding,
                    certificate_version=3,job_id="artificial",session_id=1,generation=1),permit=permit)
                def tile():
                    result=worker.request("chunk",dict(binding=binding,budget_ns=1000000000),permit=permit)
                    assert result["candidate"] is None and result["canary_ok"] and result["kernel_variant"]==variants[name]
                    return result
                for _ in range(2):tile() # exclude kernel graph loading and input/caches startup
                started=time.perf_counter();active=0
                for _ in range(32):active+=tile()["active_ns"]
                samples[name].append(dict(tiles=32,wall_seconds=time.perf_counter()-started,device_ns=active))
        medians={name:dict(wall_seconds=statistics.median(r["wall_seconds"] for r in rows),
                          device_ns=statistics.median(r["device_ns"] for r in rows)) for name,rows in samples.items()}
        ratios={name:dict(wall_vs_dp4a=medians["dp4a"]["wall_seconds"]/m["wall_seconds"],
                         device_vs_dp4a=medians["dp4a"]["device_ns"]/m["device_ns"]) for name,m in medians.items()}
        report=dict(status="passed",binary_sha256=receipt["binary_sha256"],samples=samples,medians=medians,ratios=ratios,
            scope="same-binary artificial256x256x4096 fixed-nonce job; rotated7 warm32-tile batches per backend",
            limitations="Not production hashrate. Includes CPU canary/driver/RPC; excludes full-job preparation/proof/network. GPU event intervals include scheduling.",
            external_pool_connections=0,real_submissions=0,settings_changed=False)
        (out/"warm-comparison.json").write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps(dict(status=report["status"],medians=medians,ratios=ratios),indent=2))
        return 0
    finally:
        worker.close();monitor.close()


if __name__=="__main__":raise SystemExit(main())
