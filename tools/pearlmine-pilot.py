#!/usr/bin/env python3
"""Short owner-approved mining session on a real pool. Prints progress; stops at the deadline."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from boris_pearl.config import Config,valid_address
from boris_pearl.live import LiveMining


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address")
    parser.add_argument("--pool",default="stratum+ssl://prl-sg.kryptex.network:8048")
    parser.add_argument("--seconds",type=int,default=480)
    parser.add_argument("--output")
    args=parser.parse_args()
    if not valid_address(args.address):raise SystemExit("Invalid PRL address checksum")
    session=LiveMining(Config(args.address,args.pool),seconds=args.seconds,duty=1,device_index="0")
    started=time.monotonic();last=started;report={}
    try:
        session.start();print("connected: first job pending",flush=True)
        while not session.controller.stopped.is_set():
            result=session.tick()
            if result.get("state")=="stopped":report["stopped_because"]=result.get("reason");break
            now=time.monotonic()
            if now-last>=30:
                last=now;sample=session.monitor.sample()
                print(json.dumps(dict(t=round(now-started),th=round(session.mined_tiles*524288/(now-started)/1e12,1),shares=session.shares,
                    gpu_c=sample.gpu_c if sample else None,watts=round(sample.watts) if sample and sample.watts else None,
                    throttled=round(session.throttled_seconds),reconnects=session.reconnects,pool_error=session.user_client.last_error)),flush=True)
        report.update(session.report())
        elapsed=report["elapsed_seconds"] or 1
        report["th_per_second_from_work"]=round(session.mined_tiles*524288/elapsed/1e12,1)
    finally:
        session.close("pilot finished");report["stop"]=session.stop_report
    print(json.dumps({k:v for k,v in report.items() if k!="stats"},ensure_ascii=False,indent=2))
    if args.output:Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":main()
