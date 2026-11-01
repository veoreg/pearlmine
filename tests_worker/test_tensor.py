"""Exact cross-backend proofs, real read-only sensor guard; no pool/live jobs."""
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest

from boris_pearl.config import Config
from boris_pearl.core import Job,Router,Session
from boris_pearl.cuda_worker import CudaWorker,WorkerJob
from boris_pearl.ipc import ProcessWorker
from boris_pearl.official_bridge import OfficialVerifierCallback
from boris_pearl.local_search import HEADER,FIXTURE_ADDRESS,LocalSearch
from boris_pearl.safety import Guard
from boris_pearl.telemetry import TelemetryMonitor

ROOT=Path(__file__).resolve().parent.parent
BINARY=ROOT/"production_worker/target/release"/("boris-pearl-worker.exe" if sys.platform=="win32" else "boris-pearl-worker")
RECEIPT=ROOT/"docs/boris/evidence/worker/worker-build.json"

@unittest.skipUnless(BINARY.is_file() and RECEIPT.is_file(),"Build/audit the CUDA worker first")
class TensorProofTests(unittest.TestCase):
    def setUp(self):
        self.monitor=TelemetryMonitor();self.addCleanup(self.monitor.close)
        self.monitor.start();self.monitor.updated.wait(3)
        self.assertIsNotNone(self.monitor.sample(),self.monitor.last_error)
        self.guard=Guard(stop_at=time.time()+45,max_watts=125,min_free_bytes=1<<30,max_gpu_c=75,max_gpu_util=85,laptop=True)
        self.guard.start()
        self.sha=json.loads(RECEIPT.read_text())["binary_sha256"]
        self.rows=[]

    def permit(self):return self.guard.reason(time.time(),self.monitor.sample())

    def compute(self,backend,k,m=64,n=64,large=False):
        receipt=json.loads((ROOT/"docs/boris/evidence/proof-lab/official-build.json").read_text())
        vname="boris-official-proof-check.exe" if sys.platform=="win32" else "boris-official-proof-check"
        verifier=OfficialVerifierCallback(ROOT/"proof_bridge/target/release"/vname,receipt["binary_sha256"])
        config=Config(FIXTURE_ADDRESS,"stratum+tcp://127.0.0.1:1")
        router=Router(config,verifier)
        session=Session("tensor-proof",1,"user",config.user_pool,config.user_wallet)
        router.authorize(session)
        job=Job(session,"fixed-proof",hashlib.sha256(bytes.fromhex(HEADER)).hexdigest());router.register(job)
        worker=CudaWorker(BINARY,self.sha,verifier,large_fixed_fixture=large,
            expected_device_uuid=self.monitor.details()["device"]["uuid"])
        self.addCleanup(worker.close)
        target=f"{((1<<256)-1)//(128*k):064x}"
        worker.bind_job(job,WorkerJob(HEADER,target,m=m,n=n,k=k,backend=backend))
        worker.start(permit=self.permit)
        result=worker.execute((session.identifier,1,job.job_id,job.header_hash),1000000000,permit=self.permit)
        self.assertEqual(len(result.proofs),1)
        self.assertEqual(router.route(job,result.proofs[0]),session)
        self.assertEqual(result.stats["kernel_variant"],{"dp4a":4,"wmma-int8":16,"mma-int8":32}[backend])
        wire=verifier.verified_wire[job]
        self.rows.append(dict(backend=backend,m=m,n=n,k=k,active_ns=result.active_ns,wire_bytes=len(wire),wire_sha256=hashlib.sha256(wire).hexdigest(),official=True))
        worker.close()
        return wire

    def test_signed_wmma_and_mma_proofs_match_dp4a_for_three_k_shapes(self):
        for k in (2048,2176,4096):
            expected=self.compute("dp4a",k)
            for backend in ("wmma-int8","mma-int8"):
                self.assertEqual(self.compute(backend,k),expected)
        self.save("three-k-proof-equivalence")

    def test_non_power_of_two_merkle_dimensions_match(self):
        expected=self.compute("dp4a",2176,m=192,n=320)
        for backend in ("wmma-int8","mma-int8"):
            self.assertEqual(self.compute(backend,2176,m=192,n=320),expected)
        self.save("irregular-proof-equivalence")

    def test_full_size_single_tile_proofs_match(self):
        expected=self.compute("dp4a",4096,m=131072,n=131072,large=True)
        for backend in ("wmma-int8","mma-int8"):
            self.assertEqual(self.compute(backend,4096,m=131072,n=131072,large=True),expected)
        self.save("full-size-proof-equivalence")

    def test_signed_extremes_zero_impulses_and_random_match_cpu(self):
        worker=ProcessWorker([str(BINARY)],expected_mode="cuda-baseline")
        self.addCleanup(worker.close);worker.start(permit=self.permit)
        result=worker.request("kernel-selftest",dict(device_index=0,backends=["dp4a","wmma-int8","mma-int8"]),timeout=30,permit=self.permit)
        self.assertEqual(result["status"],"passed")
        self.assertEqual(len(result["checks"]),45)
        self.assertTrue(all(row["matched"] for row in result["checks"]))
        worker.close()
        result["binary_sha256"]=self.sha
        out=ROOT/"docs/boris/evidence/tensor/signed-arithmetic-kat.json"
        out.write_text(json.dumps(result,indent=2)+"\n")

    def test_tensor_manual_stop_and_sensor_loss_reap_worker(self):
        reports=[]
        for backend in ("wmma-int8","mma-int8"):
            for reason in ("manual","sensor-loss"):
                with self.subTest(backend=backend,reason=reason):
                    local=LocalSearch(seconds=10,duty=.2,backend=backend)
                    self.addCleanup(local.close)
                    local.start()
                    self.assertEqual(local.tick()["state"],"offline-result")
                    self.assertEqual(local.stats["kernel_variant"],{"wmma-int8":16,"mma-int8":32}[backend])
                    active_before=local.controller.planner.user_ns
                    if reason=="sensor-loss":
                        local.monitor.close()
                        reply=local.tick()
                        self.assertEqual(reply["state"],"paused")
                        self.assertIn("telemetry",reply["reason"])
                        self.assertEqual(local.controller.planner.user_ns,active_before)
                    stopped=local.stop_work("tensor explicit stop" if reason=="manual" else None)
                    self.assertTrue(stopped["worker_reaped"])
                    self.assertIsNot(stopped["worker_still_listed_by_nvml"],True)
                    self.assertTrue(local.controller.stopped.is_set())
                    report=local.report();report["test_result"]="passed";report["stop_case"]=reason
                    reports.append(report)
                    local.close()
        out=ROOT/"docs/boris/evidence/tensor/tensor-stops.json"
        out.write_text(json.dumps(dict(status="passed",binary_sha256=self.sha,cases=reports),indent=2)+"\n")

    def save(self,name):
        out=ROOT/"docs/boris/evidence/tensor";out.mkdir(parents=True,exist_ok=True)
        (out/(name+".json")).write_text(json.dumps(dict(status="passed",binary_sha256=self.sha,
            scope="artificial one-tile full proofs; no pool, no live mode",cases=self.rows),indent=2)+"\n")
