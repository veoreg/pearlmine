# SPDX-License-Identifier: Apache-2.0
"""Owner-started real mining on one GPU: real pool jobs, officially verified proofs, visible totals.

Created only by an explicit Start with the disclosed payout, fee, pool and stop deadline.
Never starts by itself, never restarts after a safety stop, changes no GPU settings.
"""
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

from .config import is_kryptex
from .controller import Controller
from .core import Router,Session
from .cuda_worker import CudaWorker,LiveRunConsent
from .devices import discover,select_device
from .load import DutyLimiter
from .official_bridge import OfficialVerifierCallback
from .pool import PoolAccess,PoolClient,PoolError
from .runtime import PoolCoordinator
from .safety import Guard
from .telemetry import SensorError,TelemetryMonitor

ROOT=Path(__file__).resolve().parent.parent
FAST_CARDS={(12,0):"tma"}  # Exact compute capability the fast worker is compiled and tested for.
THROTTLE_FROM_C=75
RAMP_SECONDS=90
DUTIES=(.25,.5,1)
LAPTOP_DUTY=.1
MAX_SECONDS=7*86400


def pool_dialect(url):
    return "kryptex" if is_kryptex(url) else "object"


def worker_name():
    """Pool-side label, visible to anyone who looks up the address: never the computer's real name."""
    return "pearlmine"


class LiveMining:
    def __init__(self,config,*,seconds,duty,profile="standard",device_index,binary=None,receipt=None,
                 backend=None,shape=(131072,131072,4096),tls_contexts=None,state_dir=None,max_watts=None):
        if not config.user_wallet or not config.user_pool:raise ValueError("Сначала сохраните кошелёк и пул.")
        if type(seconds) not in (int,float) or not 1<=seconds<=MAX_SECONDS:raise ValueError("Срок майнинга: до 7 дней.")
        if profile=="laptop":
            if duty!=LAPTOP_DUTY:raise ValueError("На ноутбуке майнинг ограничен 10% рабочего времени.")
        elif profile!="standard" or duty not in DUTIES:raise ValueError("Нагрузка: 25, 50 или 100%.")
        self.config,self.seconds,self.duty,self.profile=config,seconds,duty,profile
        self.device_request,self.backend,self.shape=device_index,backend,shape
        self.tls_contexts=tls_contexts or {}
        name="pearlmine-tma-worker.exe" if sys.platform=="win32" else "pearlmine-tma-worker"
        self.binary=Path(binary) if binary else ROOT/"tma_worker/target/release"/name
        self.receipt=Path(receipt) if receipt else ROOT/"docs/boris/evidence/worker/tma-worker-build.json"
        self.state_dir=Path(state_dir) if state_dir else ROOT/"state/mining"
        self.max_watts=max_watts
        self.monitor=self.controller=self.worker=self.coordinator=self.user_client=None
        self.stats=None;self.stop_report=None;self.begun_at=None
        self.shares=dict(accepted=0,rejected=0,uncertain=0,stale=0)
        self.first_share_seconds=None;self.reconnects=0;self.failures=0;self.next_connect=0;self.throttled_seconds=0;self.finished_job_tiles=0

    def _client(self,owner,pool,wallet,router):
        access=PoolAccess(pool,self.config.identity,allow_external=True,allow_submit=True)
        return PoolClient(self.config,router,Session(owner,1,owner,pool,wallet),access,worker_name=worker_name(),
            dialect=pool_dialect(pool),tls_context=self.tls_contexts.get(owner),timeout=10,on_retire=self.worker.retire)

    def start(self):
        if self.begun_at is not None:raise ValueError("Нужен новый явный запуск.")
        try:
            expected=json.loads(self.receipt.read_text())["binary_sha256"]
            selected=select_device(discover(),self.device_request)
            if selected.requires_gb10_guards:raise SensorError("GB10 board/OEM sensor is unqualified")
            self.backend=FAST_CARDS.get((selected.cc_major,selected.cc_minor))
            if not self.backend:raise ValueError(selected.name+": быстрый режим для этой карты ещё не готов. Майнинг на ней пока не имеет смысла.")
            self.monitor=TelemetryMonitor(device_uuid=selected.uuid);self.monitor.start();self.monitor.updated.wait(3)
            details,sample=self.monitor.details(),self.monitor.sample()
            if not details or sample is None:raise SensorError(self.monitor.last_error or "Fresh NVML telemetry unavailable")
            if details["device"]["uuid"].lower()!=selected.uuid.lower():raise SensorError("Selected CUDA identity differs from NVML sensor identity")
            limit=sample.power_limit_watts
            if type(limit) not in (int,float) or limit<=0:raise SensorError("Driver did not report a power limit")
            # Software stops only: the driver's own limit stays in charge, nothing is written to the card.
            watts=self.max_watts or (min(50,limit*.8) if self.profile=="laptop" else limit*1.15)
            stop_at=time.time()+self.seconds
            guard=Guard(stop_at=stop_at,max_watts=watts,min_free_bytes=1<<30,laptop=self.profile=="laptop",max_gpu_c=70 if self.profile=="laptop" else 83,ceiling_c=83)
            vname="boris-official-proof-check.exe" if sys.platform=="win32" else "boris-official-proof-check"
            verifier=OfficialVerifierCallback(ROOT/"proof_bridge/target/release"/vname,
                json.loads((ROOT/"docs/boris/evidence/proof-lab/official-build.json").read_text())["binary_sha256"])
            router=Router(self.config,verifier)
            consent=LiveRunConsent(self.config.identity,selected.cuda_index,stop_at)
            self.worker=CudaWorker(self.binary,expected,verifier,config_identity=self.config.identity,live_consent=consent,
                expected_device_uuid=selected.uuid,cpu_threads=16,graceful_job_change=True)
            self.state_dir.mkdir(parents=True,exist_ok=True)
            ledger=self.state_dir/("ledger-"+sha256((self.config.identity+selected.uuid).encode()).hexdigest()[:24]+".json")
            self.controller=Controller(self.config,router,guard,ledger,worker=self.worker,sample=self.monitor.sample,
                pacer=None if self.duty==1 else DutyLimiter(self.duty))
            self.user_client=self._client("user",self.config.user_pool,self.config.user_wallet,router)
            developer=self._client("developer",self.config.developer_pool,self.config.developer_wallet,router) if self.config.effective_bps else None
            self.coordinator=PoolCoordinator(self.controller,self.user_client,developer,shape=self.shape,device_index=selected.cuda_index,backend=self.backend)
            self.coordinator.start(self.config.identity)
            self.begun_at=time.monotonic()
            return self
        except Exception:
            self.close("startup failed")
            raise

    def _reconnect(self):
        """A dropped pool pauses work; bounded backoff, never a resend of an uncertain share."""
        client=self.user_client
        if not client.closed.is_set() or time.monotonic()<self.next_connect:return
        try:client.connect();self.failures=0;self.reconnects+=1
        except (OSError,PoolError):
            self.failures+=1;self.next_connect=time.monotonic()+min(60,2**min(self.failures,6))

    def tick(self):
        if self.begun_at is None:raise ValueError("Explicit start required")
        self._reconnect()
        result=self.coordinator.tick(submit=True)
        if result.get("work_stats"):
            # The worker counts per job; keep a running total across job changes.
            total=result["work_stats"]["total_tiles"]
            if self.stats and total<self.stats["total_tiles"]:self.finished_job_tiles+=self.stats["total_tiles"]
            self.stats=result["work_stats"]
        self._cool_down()
        outcome=result.get("pool_result")
        if outcome in self.shares:
            self.shares[outcome]+=1
            if self.first_share_seconds is None:self.first_share_seconds=time.monotonic()-self.begun_at
        if result.get("state")=="paused":
            time.sleep(.05)
            if result.get("reason")=="no authorized user job":result["reason"]="Жду задание от пула"
        return result

    @property
    def mined_tiles(self):
        return self.finished_job_tiles+(self.stats["total_tiles"] if self.stats else 0)

    def _cool_down(self):
        """Rest proportionally to the heat, well before the hard 80 C stop would end the session."""
        sample=self.monitor.sample();pause=0
        ramp=1-(time.monotonic()-self.begun_at)/RAMP_SECONDS
        # The card heats within seconds, its fans take longer: come up to full load gradually.
        if ramp>0:pause=.6*ramp
        if sample and sample.gpu_c is not None and sample.gpu_c>=THROTTLE_FROM_C:
            pause+=.03*(sample.gpu_c-THROTTLE_FROM_C+1)**2
        if pause:
            deadline=time.monotonic()+pause;self.throttled_seconds+=pause
            while time.monotonic()<deadline and not self.controller.stopped.is_set():time.sleep(.02)

    def stop_work(self,reason="manual stop"):
        if self.stop_report is None:
            if self.coordinator:self.coordinator.stop()
            elif self.controller:self.controller.stop(reason)
            elif self.worker:self.worker.close()
            self.stop_report=dict(reason=self.controller.stop_reason if self.controller else reason,
                worker_reaped=bool(self.worker and self.worker.process and self.worker.process.poll() is not None))
        return self.stop_report

    def report(self):
        ledger=self.controller.planner if self.controller else None
        return dict(scope="live mining",backend=self.backend,duty=self.duty,shares=dict(self.shares),stats=self.stats,
            first_share_seconds=self.first_share_seconds,reconnects=self.reconnects,stop=self.stop_report,throttled_seconds=self.throttled_seconds,
            elapsed_seconds=None if self.begun_at is None else time.monotonic()-self.begun_at,
            user_active_ns=ledger.user_ns if ledger else None,developer_active_ns=ledger.dev_ns if ledger else None,
            last_error=self.coordinator.last_error if self.coordinator else None,
            pool_error=self.user_client.last_error if self.user_client else None)

    def close(self,reason="manual stop"):
        self.stop_work(reason)
        if self.monitor:self.monitor.close()
