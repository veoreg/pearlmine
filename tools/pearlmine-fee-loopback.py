#!/usr/bin/env python3
"""Real GPU and worker with a real 2% fee split between two pools on 127.0.0.1. Nothing leaves this computer."""
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

EASY=f"{(1<<224)//65536-1:064x}"  # some shares, so both destinations are seen within minutes


def job(name):
    header=((0x20000000).to_bytes(4,"little")+secrets.token_bytes(64)+int(time.time()).to_bytes(4,"little")+(0x1c7fffff).to_bytes(4,"little")).hex()
    return notify(job_id=name,header=header,target=EASY)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds",type=int,default=240)
    parser.add_argument("--burst",type=int,default=2,help="fee turn, seconds; shorter than in real use so that it is seen quickly")
    args=parser.parse_args()
    with MockPool(first_job=job("user_job")) as user,MockPool(first_job=job("developer_job")) as developer,tempfile.TemporaryDirectory(prefix="pearlmine-fee-") as tmp:
        config=Config(fixture_address(3),user.endpoint,200,fixture_address(7),developer.endpoint)
        session=LiveMining(config,seconds=args.seconds,duty=1,device_index="0",tls_contexts=dict(user=user.client_context,developer=developer.client_context),state_dir=tmp)
        started=time.monotonic();last=started
        try:
            session.start();session.worker.fee_burst_ns=args.burst*1_000_000_000
            while not session.controller.stopped.is_set():
                if session.tick().get("state")=="stopped":break
                if time.monotonic()-last>=30:
                    last=time.monotonic();print(json.dumps(dict(t=round(last-started),shares=session.shares,user=sum(m.get("method")=="mining.submit" for m in user.received),developer=sum(m.get("method")=="mining.submit" for m in developer.received))),flush=True)
            report=session.report()
        finally:
            session.close("fee loopback finished")
        user_ns,developer_ns=report.get("user_active_ns") or 0,report.get("developer_active_ns") or 0
        print(json.dumps(dict(stop=session.stop_report,shares=session.shares,user_active_s=round(user_ns/1e9,1),developer_active_s=round(developer_ns/1e9,1),
            developer_percent=round(100*developer_ns/max(1,user_ns+developer_ns),2),
            user_submits=sum(m.get("method")=="mining.submit" for m in user.received),developer_submits=sum(m.get("method")=="mining.submit" for m in developer.received)),ensure_ascii=False,indent=2))


if __name__=="__main__":main()
