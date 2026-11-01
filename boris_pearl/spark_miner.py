# SPDX-License-Identifier: Apache-2.0
"""Owner-started mining on the DGX Spark that lives on the Spark and always gives way to client work.

start   remember the owner's payout and deadline, launch the supervisor
stop    stop now; nothing restarts
A supervisor takes the machine's heavy-task lock in slices, so waiting jobs get their turn; a session
ends the moment client work appears. A safety stop (power, temperature) is final until the next start.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .config import Config

ROOT=Path(__file__).resolve().parent.parent
RUN=ROOT.parent/"run"
WANT,STOP,STATUS,TOTALS,SETTINGS=RUN/"want.json",RUN/"stop",RUN/"status.json",RUN/"totals.json",RUN/"settings.json"
CLIENTS=Path("/srv/spark/run/clients_state.json")
HEAVY="/srv/spark/run/spark_heavy.py"
INCOMING=Path("/srv/spark/queue/incoming")
UNIT="pearlmine-supervisor"
SLICE_SECONDS=1800
REST_SECONDS=60
MINING_CLOCK=["sudo","-n","/usr/bin/nvidia-smi","-lgc","300,2000"]
FULL_CLOCK=["sudo","-n","/usr/bin/nvidia-smi","-rgc"]
DEFAULT_SECONDS=86400
SAFE_ENDS=("stop deadline","manual stop","pilot finished","yield",None)


def read_json(path,default=None):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError):return default


def write_status(**values):
    RUN.mkdir(parents=True,exist_ok=True)
    temporary=STATUS.with_name("status.new")
    temporary.write_text(json.dumps(dict(values,updated=time.time()),ensure_ascii=False),encoding="utf-8")
    os.replace(temporary,STATUS)


def client_work_waiting():
    state=read_json(CLIENTS,{}) or {}
    if state.get("comfy_running") or state.get("comfy_pending"):return True
    try:return any(INCOMING.iterdir())
    except OSError:return False


def lock_holder():
    state=read_json(Path("/srv/spark/run/heavy.lock"))
    return (state or {}).get("name")


def set_clock(command):
    """Only with the owner's sudo rule; without it the manual limit stays and the 80 W stop still protects the machine."""
    try:return subprocess.run(command,capture_output=True,timeout=15).returncode==0
    except (OSError,subprocess.SubprocessError):return False


def start_saved(_args):
    """The Spark's own console starts with the destination last confirmed in the panel."""
    saved=read_json(SETTINGS)
    if not saved:raise SystemExit("Сначала запустите майнинг на Spark из приложения PearlMine: там задаётся адрес и пул.")
    start(argparse.Namespace(wallet=saved["wallet"],pool=saved["pool"],seconds=DEFAULT_SECONDS))


def start(args):
    config=Config(args.wallet,args.pool)
    if config.effective_bps:raise SystemExit("A developer fee is not supported in this service yet")
    if not 60<=args.seconds<=7*86400:raise SystemExit("Deadline must be from one minute to seven days")
    RUN.mkdir(parents=True,exist_ok=True);STOP.unlink(missing_ok=True);TOTALS.unlink(missing_ok=True)
    SETTINGS.write_text(json.dumps(dict(wallet=config.user_wallet,pool=config.user_pool)),encoding="utf-8")
    WANT.write_text(json.dumps(dict(wallet=config.user_wallet,pool=config.user_pool,until=time.time()+args.seconds)),encoding="utf-8")
    write_status(state="waiting",reason="Запускаю")
    subprocess.run(["systemd-run","--user","--unit",UNIT,"--collect","--quiet","-p",f"WorkingDirectory={ROOT}",
        "/usr/bin/python3","-X","utf8","-m","boris_pearl.spark_miner","supervise"],check=True)


def stop(_args):
    RUN.mkdir(parents=True,exist_ok=True);STOP.touch();WANT.unlink(missing_ok=True)
    subprocess.run(["systemctl","--user","stop",UNIT],capture_output=True)
    set_clock(FULL_CLOCK)
    if not read_json(STATUS,{}).get("state")=="running":write_status(state="stopped",reason="Остановлено владельцем",**totals())


def totals():
    return read_json(TOTALS,dict(accepted=0,rejected=0)) or dict(accepted=0,rejected=0)


def supervise(_args):
    job=RUN/"session.sh"
    job.write_text(f"#!/bin/bash\ncd {ROOT}\nexec /usr/bin/python3 -X utf8 -m boris_pearl.spark_miner session\n",encoding="utf-8");job.chmod(0o755)
    while True:
        want=read_json(WANT)
        if not want or STOP.exists():return
        if time.time()>=want["until"]:
            WANT.unlink(missing_ok=True);write_status(state="stopped",reason="Срок майнинга закончился",**totals());return
        holder=lock_holder()
        if client_work_waiting():
            write_status(state="waiting",reason="Уступает заказам",**totals());time.sleep(20);continue
        if holder:
            write_status(state="waiting",reason="Занято: "+str(holder),**totals());time.sleep(20);continue
        subprocess.run(["/usr/bin/python3",HEAVY,"--name","pearlmine","--budget","8","--wait","run",str(job)],capture_output=True)
        status=read_json(STATUS,{}) or {}
        if status.get("state")=="stopped":return
        time.sleep(REST_SECONDS)


def session(_args):
    from .live import LiveMining
    want=read_json(WANT)
    if not want:return
    base=totals()
    seconds=min(SLICE_SECONDS,int(want["until"]-time.time()))
    if seconds<1:return
    set_clock(MINING_CLOCK)
    miner=LiveMining(Config(want["wallet"],want["pool"]),seconds=seconds,duty=1,device_index="0")
    ending=None;published=0;started=time.monotonic()
    def counts():
        return dict(accepted=base["accepted"]+miner.shares["accepted"],rejected=base["rejected"]+miner.shares["rejected"])
    try:
        miner.start()
        while not miner.controller.stopped.is_set():
            miner.tick()
            if STOP.exists():ending="manual stop";break
            if client_work_waiting():ending="yield";break
            now=time.monotonic()
            if now-published>=3:
                published=now;sample=miner.monitor.sample()
                write_status(state="running",th=round(miner.mined_tiles*524288/max(1,now-started)/1e12,1),reason=None,
                    temperature=None if sample is None else sample.gpu_c,watts=None if sample is None else sample.watts,**counts())
    except Exception as exc:
        ending=str(exc)[:300]
    finally:
        reason=ending or (miner.controller.stop_reason if miner.controller else None)
        miner.close("pilot finished")
        set_clock(FULL_CLOCK)  # Client work and training always get the full clock back.
    TOTALS.write_text(json.dumps(counts()),encoding="utf-8")
    if reason=="manual stop":
        write_status(state="stopped",reason="Остановлено владельцем",**counts())
    elif reason=="yield":
        write_status(state="waiting",reason="Уступает заказам",**counts())
    elif reason in SAFE_ENDS:
        write_status(state="waiting",reason="Перерыв для других задач",**counts())
    else:
        # A safety stop is final: the owner decides what happens next.
        WANT.unlink(missing_ok=True);write_status(state="stopped",reason=str(reason),**counts())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="command",required=True)
    begin=commands.add_parser("start");begin.add_argument("wallet");begin.add_argument("pool");begin.add_argument("seconds",type=int);begin.set_defaults(run=start)
    for name,function in (("start-saved",start_saved),("stop",stop),("supervise",supervise),("session",session)):commands.add_parser(name).set_defaults(run=function)
    args=parser.parse_args();args.run(args)


if __name__=="__main__":main()
