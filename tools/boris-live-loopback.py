#!/usr/bin/env python3
"""Real GPU, real --live worker, real official verifier; the pool is a local process on 127.0.0.1.

Uses Kryptex's published static share difficulty to estimate time per share on this card.
Nothing leaves this computer and no address here receives anything.
"""
import argparse
import json
from pathlib import Path
import secrets
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT),str(ROOT/"tests_portable")]
from mock_pool import MockPool,notify
from test_core import fixture_address
from boris_pearl.config import Config
from boris_pearl.live import LiveMining

KRYPTEX_TARGET=f"{(1<<224)//2097152-1:064x}"  # docs/protocol/kryptex.md


def header():
    return ((0x20000000).to_bytes(4,"little")+secrets.token_bytes(64)+int(time.time()).to_bytes(4,"little")+(0x1c7fffff).to_bytes(4,"little")).hex()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds",type=int,default=240)
    parser.add_argument("--duty",type=float,default=1)
    parser.add_argument("--target",default=KRYPTEX_TARGET)
    parser.add_argument("--until-shares",type=int,default=1)
    parser.add_argument("--new-job-every",type=float,default=0)
    parser.add_argument("--output")
    parser.add_argument("--max-watts",type=float)
    args=parser.parse_args()
    report=dict(target=args.target)
    with MockPool(first_job=notify(job_id="loopback_2097152",header=header(),target=args.target)) as pool,tempfile.TemporaryDirectory(prefix="boris-live-loop-") as tmp:
        config=Config(fixture_address(3),pool.endpoint)
        session=LiveMining(config,seconds=args.seconds,duty=args.duty,device_index="0",max_watts=args.max_watts,
            tls_contexts=dict(user=pool.client_context),state_dir=tmp)
        started=time.monotonic()
        try:
            session.start();report["start_seconds"]=round(time.monotonic()-started,1);last=last_job=time.monotonic();peak=dict(watts=0,gpu_c=0);jobs=0;samples=[]
            while not session.controller.stopped.is_set():
                result=session.tick()
                if args.new_job_every and time.monotonic()-last_job>=args.new_job_every and pool.connection:
                    jobs+=1;last_job=time.monotonic();pool.send(notify(job_id=f"loopback{jobs}_2097152",header=header(),target=args.target))
                if result.get("state")=="stopped":report["stopped_because"]=result.get("reason");break
                sample=session.monitor.sample()
                if sample:
                    peak.update(watts=max(peak["watts"],sample.watts or 0),gpu_c=max(peak["gpu_c"],sample.gpu_c or 0))
                    samples.append((sample.watts or 0,sample.gpu_c or 0))
                if time.monotonic()-last>=20:
                    last=time.monotonic();print(json.dumps(dict(t=round(last-started),th=round(session.mined_tiles*524288/(last-started)/1e12,1),shares=session.shares,gpu_c=samples[-1][1] if samples else None,throttled=round(session.throttled_seconds),jobs=jobs)),flush=True)
                if session.shares["accepted"]>=args.until_shares:break
            report.update(session.report())
            elapsed=report["elapsed_seconds"] or 1
            report["th_per_second_from_work"]=round(session.mined_tiles*524288/elapsed/1e12,1)
            found=sum(session.shares[k] for k in ("accepted","stale"))
            report["th_per_second_from_shares"]=round(found*2097152*2**32/elapsed/1e12,1) if args.target==KRYPTEX_TARGET else None
            report.update(peak_watts=peak["watts"],peak_gpu_c=peak["gpu_c"],jobs_sent=jobs,
                mean_watts=round(sum(s[0] for s in samples)/len(samples)) if samples else None,
                mean_gpu_c=round(sum(s[1] for s in samples)/len(samples)) if samples else None)
        finally:
            session.close("loopback finished")
            report["stop"]=session.stop_report
        report["pool_received_submits"]=sum(1 for m in pool.received if m.get("method")=="mining.submit")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if args.output:Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":main()
