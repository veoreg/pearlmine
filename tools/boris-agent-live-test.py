#!/usr/bin/env python3
"""Real agent process on loopback, real HTTPS, no GPU load unless --gpu is given explicitly.

Checks what unit tests cannot: a separate panel process dying must stop the agent's work,
and closing the agent must leave no owned process. Never contacts a pool or another host.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from boris_pearl.remote_control import Pairing,RemoteClient

PORT=18431
PANEL="""
import json,sys,time
from boris_pearl.fleet import Fleet
from boris_pearl.remote_control import Pairing
fleet=Fleet();key=fleet.connect(Pairing.parse(sys.argv[1]))
def ready():
    status=fleet.snapshot()[key]["status"] or {}
    return status.get("devices") and not status.get("refreshing")
while not ready():time.sleep(.1)
device=sys.argv[2]
fleet.start(key,[device],seconds=int(sys.argv[3]),duty=.2,profile="standard")
print("started",flush=True)
time.sleep(600)
"""


def wait(condition,timeout,what):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=condition()
        if value:return value
        time.sleep(.2)
    raise SystemExit("FAIL: "+what)


def children_of(pid):
    out=subprocess.run(["powershell","-NoProfile","-Command",f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}').ProcessId"],capture_output=True,text=True).stdout
    return [int(x) for x in out.split()]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu",action="store_true",help="Explicitly run a bounded 5-minute-max artificial test on the first eligible local GPU")
    parser.add_argument("--exe",help="Run the agent from this standalone PearlMine.exe instead of the current Python")
    args=parser.parse_args()
    report={"agent":args.exe or "source"}
    runner=args.exe or sys.executable
    with tempfile.TemporaryDirectory(prefix="boris-live-") as tmp:
        folder=Path(tmp)/"agent"
        subprocess.run([runner,"-m","boris_pearl.agent","init","--directory",str(folder),"--name","loopback",f"--url=https://127.0.0.1:{PORT}"],cwd=Path(tmp),check=True,capture_output=True)
        command=[runner,"-m","boris_pearl.agent","serve","--directory",str(folder),"--port",str(PORT)]+(["--allow-tests"] if args.gpu else [])
        agent=subprocess.Popen(command,cwd=(Path(args.exe).parent if args.exe else ROOT),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        panel=None
        try:
            text=(folder/"pairing.json").read_text(encoding="utf-8");client=RemoteClient(Pairing.parse(text),timeout=3)
            def status():
                try:return client.request("status")
                except OSError:return None
            wait(status,15,"agent did not answer on HTTPS")
            report["https_status"]="ok";report["allow_tests"]=client.request("status")["allow_tests"]
            client.request("refresh",{});wait(lambda:not client.request("status")["refreshing"],60,"device discovery did not finish")
            devices=client.request("status")["devices"];report["devices"]=[d["name"] for d in devices.values()]
            eligible=[k for k,d in devices.items() if d["eligible"]]
            report["eligible"]=len(eligible)
            if not args.gpu:
                try:client.request("start",dict(request_id="a"*32,sequence=1,devices=eligible[:1] or ["x"],seconds=5,duty=.2,profile="standard",backend="auto"));report["readonly_refused_start"]=False
                except ValueError:report["readonly_refused_start"]=True
            else:
                if not eligible:raise SystemExit("FAIL: no eligible GPU: "+json.dumps(report,ensure_ascii=False))
                panel=subprocess.Popen([sys.executable,"-c",PANEL,text,eligible[0],"120"],cwd=ROOT,stdout=subprocess.PIPE,text=True)
                wait(lambda:panel.stdout.readline().strip()=="started",60,"panel did not start a test")
                def running():
                    row=client.request("status")["devices"][eligible[0]]
                    if row["state"] in ("failed","stopped"):raise SystemExit("FAIL: test ended early: "+json.dumps(dict(state=row["state"],note=row.get("note"),error=row.get("error"),report=row.get("report")),ensure_ascii=False)[:1500])
                    return row["state"]=="running"
                wait(running,90,"test did not reach running: "+json.dumps(client.request("status")["devices"][eligible[0]],ensure_ascii=False)[:800])
                time.sleep(15)
                row=client.request("status")["devices"][eligible[0]]
                report["while_running"]=dict(state=row["state"],watts=row.get("watts"),temperature=row.get("temperature"),tiles=row.get("test_tiles"))
                report["lease_kept_past_10s"]=row["state"] in ("running","paused")
                workers=children_of(agent.pid);report["worker_processes_during_test"]=len(workers)
                panel.kill();panel.wait();killed=time.monotonic()
                wait(lambda:client.request("status")["active"]==0,40,"agent kept working after the panel died")
                report["stop_after_panel_death_s"]=round(time.monotonic()-killed,1)
                report["agent_log_tail"]=[r["message"] for r in client.request("status")["logs"][-3:]]
                report["workers_after_stop"]=len(children_of(agent.pid))
                # Second run: the agent itself dies abruptly. Its worker must not keep loading the card.
                panel=subprocess.Popen([sys.executable,"-c",PANEL,text,eligible[0],"120"],cwd=ROOT,stdout=subprocess.PIPE,text=True)
                wait(lambda:panel.stdout.readline().strip()=="started",60,"second panel did not start a test")
                wait(lambda:client.request("status")["devices"][eligible[0]]["state"]=="running",90,"second test did not reach running")
                workers=children_of(agent.pid);report["worker_processes_second_run"]=len(workers)
                agent.kill();agent.wait();killed=time.monotonic()
                def gone():
                    alive=subprocess.run(["powershell","-NoProfile","-Command","(Get-Process -Id "+",".join(map(str,workers))+" -ErrorAction SilentlyContinue).Id"],capture_output=True,text=True).stdout.split()
                    return not alive
                try:wait(gone,40,"orphan");report["worker_exit_after_agent_kill_s"]=round(time.monotonic()-killed,1)
                except SystemExit:
                    report["worker_exit_after_agent_kill_s"]=None
                    for pid in workers:subprocess.run(["taskkill","/PID",str(pid),"/T","/F"],capture_output=True)
        finally:
            if panel and panel.poll() is None:panel.kill()
            pid=agent.pid
            agent.terminate()
            try:agent.wait(15)
            except subprocess.TimeoutExpired:agent.kill();agent.wait();report["agent_needed_kill"]=True
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
