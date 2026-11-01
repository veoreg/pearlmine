"""Explicit bounded artificial-job search test; no pool client, sockets or live mode.

The Rust test mode rejects real headers, other shapes/targets, and stops at600s
or2million tiles. It advances nonce only on this synthetic64x64x4096 job. The
placeholder public witness program below is a fixture, never a payout address.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

from .config import Config
from .controller import Controller
from .core import Job,Router,Session
from .cuda_worker import CudaWorker,WorkerJob
from .load import DutyLimiter
from .official_bridge import OfficialVerifierCallback
from .safety import Guard
from .telemetry import SensorError,TelemetryMonitor
from .devices import discover,select_device
from .load_profiles import validate_request,resolve_profile

ROOT=Path(__file__).resolve().parent.parent
HEADER=((0x20000000).to_bytes(4,"little")+b"K"*32+b"A"*32+
        (0x66666666).to_bytes(4,"little")+(0x1c7fffff).to_bytes(4,"little")).hex()
FIXTURE_ADDRESS="prl1pqvpsxqcrqvpsxqcrqvpsxqcrqvpsxqcrqvpsxqcrqvpsxqcrqvps59j0s8"


class LocalSearch:
    def __init__(self, *, seconds=300,duty=None,max_watts=None,device_index=0,require_ac=True,
                 binary=None,receipt=None,backend="dp4a",full_grid_size=None,full_grid_check_all=False,profile="standard"):
        maximum_seconds=1800 if full_grid_size is not None else 600
        if type(seconds) not in (int,float) or not .2<=seconds<=maximum_seconds:
            raise ValueError(f"Local test needs0.2..{maximum_seconds}s")
        duty=validate_request(profile,duty,max_watts,require_ac)
        self.profile_name,self.requested_watts=profile,max_watts
        self.device_request,self.selected_device=device_index,None
        if backend not in ("dp4a","wmma-int8","mma-int8"):
            raise ValueError("Unknown explicit CUDA backend")
        if full_grid_size is not None and (type(full_grid_size) is not int or full_grid_size not in (64,512,131072) or
                backend!="mma-int8" or type(full_grid_check_all) is not bool or (full_grid_check_all and full_grid_size>512)):
            raise ValueError("Full local grid size/backend/check limits")
        self.full_grid_size,self.full_grid_check_all=full_grid_size,full_grid_check_all
        self.backend=backend
        self.seconds,self.duty,self.max_watts=seconds,duty,max_watts
        self.require_ac,self.device_index=require_ac,device_index
        name="boris-pearl-worker.exe" if sys.platform=="win32" else "boris-pearl-worker"
        self.binary=Path(binary) if binary else ROOT/"production_worker/target/release"/name
        self.receipt=Path(receipt) if receipt else ROOT/"docs/boris/evidence/worker/worker-build.json"
        self.monitor=None
        self.controller=self.worker=self.tmp=None
        self.samples=[]
        self.begun_at=None
        self.job=None
        self.stats=None
        self.stop_report=None
        self.pause_counts={}
        self.stopped_at=None

    def start(self):
        if self.begun_at is not None:
            raise ValueError("Local test requires a new explicit session")
        try:
            expected=json.loads(self.receipt.read_text())["binary_sha256"]
            selected=select_device(discover(self.binary,expected),self.device_request)
            selected.require_backend(self.backend)
            self.selected_device,self.device_index=selected,selected.cuda_index
            self.monitor=TelemetryMonitor(device_uuid=selected.uuid)
            self.monitor.start()
            self.monitor.updated.wait(3)
            details=self.monitor.details()
            sample=self.monitor.sample()
            if not details or sample is None:
                raise SensorError(self.monitor.last_error or "Fresh NVML telemetry unavailable")
            if details["device"]["uuid"].lower()!=selected.uuid.lower():
                raise SensorError("Selected CUDA identity differs from NVML sensor identity")
            is_gb10=selected.requires_gb10_guards or "GB10" in details["device"]["name"].upper()
            profile=resolve_profile(self.profile_name,self.duty,self.requested_watts,self.require_ac,
                sample.power_limit_watts,gb10=is_gb10)
            self.max_watts=profile.max_watts
            if is_gb10 and sample.board_c is None:
                raise SensorError("GB10 board/OEM sensor is unqualified; run read-only gb10_preflight on the physical device before any GPU test")
            if self.full_grid_size and (sample.free_bytes is None or sample.free_bytes < 2*self.full_grid_size*(4096+128)+2*(1<<20)+(1<<30)):
                raise SensorError("Resident local grid requires allocation plus1GiB free reserve")
            guard=Guard(stop_at=time.time()+self.seconds,max_watts=self.max_watts,
                        min_free_bytes=1<<30,max_gpu_c=profile.max_gpu_c,max_gpu_util=85,laptop=self.require_ac,gb10=is_gb10)
            config=Config(FIXTURE_ADDRESS,"stratum+tcp://127.0.0.1:1") # never connected
            vname="boris-official-proof-check.exe" if sys.platform=="win32" else "boris-official-proof-check"
            verifier=OfficialVerifierCallback(ROOT/"proof_bridge/target/release"/vname,
                        json.loads((ROOT/"docs/boris/evidence/proof-lab/official-build.json").read_text())["binary_sha256"])
            router=Router(config,verifier)
            session=Session("artificial-local-test",1,"user",config.user_pool,config.user_wallet)
            router.authorize(session)
            self.job=Job(session,"fixed-hard-artificial-job",hashlib.sha256(bytes.fromhex(HEADER)).hexdigest())
            router.register(self.job)
            expected=json.loads(self.receipt.read_text())["binary_sha256"]
            if self.full_grid_size:
                from .full_grid import FullGridWorker
                self.worker=FullGridWorker(self.binary,expected,verifier,size=self.full_grid_size,
                    check_all=self.full_grid_check_all,expected_device_uuid=selected.uuid)
            else:
                self.worker=CudaWorker(self.binary,expected,verifier,local_search_test=True,chunk_tiles=32,
                    expected_device_uuid=selected.uuid,cpu_threads=2)
            size=self.full_grid_size or 64
            self.worker.bind_job(self.job,WorkerJob(HEADER,f"{1:064x}",m=size,n=size,k=4096,device_index=self.device_index,backend=self.backend))
            self.tmp=tempfile.TemporaryDirectory(prefix="boris-local-test-")
            self.controller=Controller(config,router,guard,Path(self.tmp.name)/"state.json",worker=self.worker,
                    sample=self.monitor.sample,pacer=DutyLimiter(self.duty))
            self.controller.start(config.identity)
            self.begun_at=time.monotonic()
            if self.controller._permit():
                raise SensorError(self.controller._permit())
            return self
        except Exception:
            self.close("startup failed")
            raise

    def tick(self):
        if self.begun_at is None:
            raise ValueError("Explicit local test start required")
        result=self.controller.tick(self.job)
        if result.get("state")=="paused":
            reason=result.get("reason","unknown")
            self.pause_counts[reason]=self.pause_counts.get(reason,0)+1
        if result.get("verified_proofs"):
            self.close("unexpected candidate on fixed hard test target")
            raise RuntimeError("Unexpected candidate; this local search test never submits")
        if result.get("work_stats"):
            self.stats=result["work_stats"]
        sample=self.monitor.sample()
        if sample and (not self.samples or sample.observed_at!=self.samples[-1]["observed_at"]):
            self.samples.append(asdict(sample))
            #30-minute resident tests need the full0.5s sampling window; ordinary tests keep their old bound.
            self.samples=self.samples[-(4000 if self.full_grid_size else 1300):]
        return result

    def stop_work(self,reason="manual stop"):
        if self.stop_report is not None:
            return self.stop_report
        start=time.perf_counter()
        self.stopped_at=time.monotonic()
        stop_wall=time.time()
        pid=self.worker.process.pid if self.worker and self.worker.process else None
        if self.controller:
            self.controller.stop(reason)
        elif self.worker:
            self.worker.close()
        latency=time.perf_counter()-start
        visible=None
        fresh=False
        end=time.monotonic()+2
        while pid and time.monotonic()<end:
            details=self.monitor.details() if self.monitor else None
            if not details or details["compute_pids"] is None:
                break
            if details["sample"]["observed_at"]<stop_wall:
                time.sleep(.02)
                continue
            fresh=True
            visible=pid in details["compute_pids"]
            if not visible:
                break
            time.sleep(.02)
        self.stop_report={"reason":self.controller.stop_reason if self.controller else reason,
            "stop_call_seconds":latency,"worker_reaped":bool(self.worker and self.worker.process and self.worker.process.poll() is not None),
            "worker_still_listed_by_nvml":visible,"nvml_after_stop_fresh":fresh}
        return self.stop_report

    def report(self):
        samples=self.samples
        details=self.monitor.details() if self.monitor else None
        device=None if not details else {k:v for k,v in details["device"].items() if k!="uuid"}
        elapsed=None if self.begun_at is None else (self.stopped_at or time.monotonic())-self.begun_at
        busy=None if not self.controller or self.controller.pacer is None else self.controller.pacer.busy_seconds
        return {"scope":f"bounded artificial{self.full_grid_size or 64}x{self.full_grid_size or 64}x4096 hard-target nonce lifecycle; no pool/live mode",
            "full_grid_mode":bool(self.full_grid_size),"shape":[self.full_grid_size or 64,self.full_grid_size or 64,4096],
            "official_audits":getattr(self.worker,"audits",[]),
            "device":device,"selected_cuda_device":None if not self.selected_device else self.selected_device.public(),
            "load_profile":self.profile_name,"elapsed_seconds":elapsed,
            "requested_seconds":self.seconds,"backend":self.backend,"duty_limit":self.duty,"stop_threshold_watts":self.max_watts,
            "power_settings_changed":False,"cpu_worker_threads":2,"stats":self.stats,"stop":self.stop_report,
            "worker_binary_sha256":None if not self.worker else self.worker.expected_sha256,
            "work_wall_seconds":busy,"software_busy_fraction":None if not elapsed or busy is None else busy/elapsed,
            "pause_counts":self.pause_counts,
            "ledger":None if not self.controller else {"user_active_ns":self.controller.planner.user_ns,
                    "developer_active_ns":self.controller.planner.dev_ns},
            "sensor_retention_limit":4000 if self.full_grid_size else 1300,
            "sensor_window_start":None if not samples else samples[0]["observed_at"],
            "sensor_window_end":None if not samples else samples[-1]["observed_at"],
            "sensor_samples":len(samples),"max_gpu_c":max((s["gpu_c"] for s in samples if s["gpu_c"] is not None),default=None),
            "max_watts":max((s["watts"] for s in samples if s["watts"] is not None),default=None),
            "max_gpu_util":max((s["gpu_util_percent"] for s in samples if s["gpu_util_percent"] is not None),default=None),
            "min_free_bytes":min((s["free_bytes"] for s in samples if s["free_bytes"] is not None),default=None),
            "sensors":samples,"live_mode":False,"external_pool_connections":0,"real_submissions":0}

    def close(self,reason="manual stop"):
        self.stop_work(reason)
        if self.monitor:self.monitor.close()
        if self.tmp:
            self.tmp.cleanup()


def run_local(seconds=300,output=ROOT/"docs/boris/evidence/load/local-search-report.json",duty=None,
              max_watts=None,device_index=0,require_ac=True,backend="dp4a",profile="standard"):
    session=LocalSearch(seconds=seconds,duty=duty,max_watts=max_watts,device_index=device_index,require_ac=require_ac,backend=backend,profile=profile)
    status="incomplete"
    try:
        session.start()
        progress=time.monotonic()
        while not session.controller.stopped.is_set():
            result=session.tick()
            if result.get("state")=="stopped":
                break
            if result.get("state")=="paused":
                time.sleep(.02)
            if time.monotonic()-progress>=15:
                print(json.dumps({"local_test_elapsed":round(time.monotonic()-session.begun_at,1),"stats":session.stats}),flush=True)
                progress=time.monotonic()
        session.stop_work("stop deadline")
        unexpected_pauses=set(session.pause_counts)-{"stop deadline","stopped"}
        status="passed" if session.controller.stop_reason=="stop deadline" and session.stats and session.stats["nonce_changes"]>0 and not unexpected_pauses else "stopped early"
        report=session.report();report["status"]=status
        path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps({k:v for k,v in report.items() if k!="sensors"},indent=2),flush=True)
        return 0 if status=="passed" else 3
    finally:
        session.close("local test finished")
