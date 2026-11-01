#!/usr/bin/env python3
"""Explicit artificial complete-grid experiment; no pool connection or live work."""
import argparse
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from boris_pearl.local_search import LocalSearch


def run(size,seconds,duty,max_watts,device,output,check_all=False):
    session=LocalSearch(seconds=seconds,duty=duty,max_watts=max_watts,device_index=device,
        backend="mma-int8",full_grid_size=size,full_grid_check_all=check_all)
    status="incomplete";error=None
    try:
        session.start()
        progress=time.monotonic()
        while not session.controller.stopped.is_set():
            response=session.tick()
            if response.get("state")=="stopped":
                break
            if session.stats and session.stats.get("complete"):
                reason=session.controller.pacer.ready(session.controller._permit)
                session.stop_work(reason or "local full grid complete")
                status="passed" if reason is None else "incomplete"
                break
            if response.get("state")=="paused":time.sleep(.02)
            if time.monotonic()-progress>15:
                print(json.dumps(dict(elapsed=round(time.monotonic()-session.begun_at,1),stats=session.stats)),flush=True)
                progress=time.monotonic()
    except Exception as exc:
        error=repr(exc);status="failed"
    finally:
        session.stop_work("local grid test finished")
        report=session.report()
        report.update(status=status,error=error,
            completion_note="One complete artificial nonce plus next-nonce prefix required for PASS",
            performance_note="Physical local arithmetic/verification throughput only; no accepted-share hashrate or income estimate")
        stats=report.get("stats")
        if stats:
            report["measured_tiles_per_wall_second"]=stats["executed_tiles"]/report["elapsed_seconds"]
            report["full_nonce_coverage_fraction"]=min(1,stats["executed_tiles"]/stats["tiles_per_nonce"])
        output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps({k:v for k,v in report.items() if k!="sensors"},indent=2),flush=True)
        session.close()
    return 0 if status=="passed" else 3


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--size",type=int,choices=(64,512,131072),default=64)
    p.add_argument("--seconds",type=int,default=300)
    p.add_argument("--duty",type=float,default=.1)
    p.add_argument("--max-watts",type=float,default=125)
    p.add_argument("--device",type=int,default=0)
    p.add_argument("--check-all",action="store_true",help="Every tile CPU-compared on small fixtures only")
    p.add_argument("--output",default="docs/boris/evidence/full-size/local-grid-report.json")
    a=p.parse_args()
    return run(a.size,a.seconds,a.duty,a.max_watts,a.device,a.output,a.check_all)


if __name__=="__main__":raise SystemExit(main())
