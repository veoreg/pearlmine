"""Bounded artificial jobs, real NVML read-only sensors and owned-process stops."""
import json
from pathlib import Path
import sys
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parent.parent
from boris_pearl.local_search import LocalSearch
from boris_pearl.ipc import WorkerError
from boris_pearl.cuda_worker import CudaWorker,WorkerJob
from boris_pearl.core import Job,Session
from boris_pearl.official_bridge import OfficialVerifierCallback
from boris_pearl.ipc import ProcessWorker
from boris_pearl.local_search import HEADER

WR=ROOT/"docs/boris/evidence/worker/worker-build.json"
WORKER=ROOT/"production_worker/target/release"/("boris-pearl-worker.exe" if sys.platform=="win32" else "boris-pearl-worker")


@unittest.skipUnless(WORKER.is_file() and WR.is_file(),"Build and record GPU worker first")
class RealSafetyTests(unittest.TestCase):
    def session(self,seconds=10):
        s=LocalSearch(seconds=seconds,duty=.2)
        self.addCleanup(s.close)
        s.start()
        return s

    def test_nonce_walk_cache_real_sensors_and_manual_stop_reap(self):
        s=self.session()
        for _ in range(4):
            r=s.tick()
            self.assertEqual(r["state"],"offline-result",r)
        self.assertGreaterEqual(s.stats["nonce_changes"],2)
        self.assertGreater(s.stats["cache_hits"],0)
        self.assertTrue(s.stats["workspace_reused"])
        self.assertEqual(s.stats["kernel_variant"],4)
        self.assertEqual(s.stats["device_bytes"],24*4096+128)
        stopped=s.stop_work("manual test stop")
        self.assertTrue(stopped["worker_reaped"])
        self.assertLess(stopped["stop_call_seconds"],1)
        self.assertIsNot(stopped["worker_still_listed_by_nvml"],True)
        with self.assertRaises(ValueError):
            s.controller.start(s.controller.config.identity)
        self.write_report("manual-stop",s)

    def test_deadline_stops_owned_worker_even_without_another_tick(self):
        s=self.session(seconds=.6)
        self.assertEqual(s.tick()["state"],"offline-result")
        self.assertTrue(s.controller.stopped.wait(2))
        end=time.monotonic()+1
        while s.worker.process.poll() is None and time.monotonic()<end:
            time.sleep(.01)
        self.assertIsNotNone(s.worker.process.poll())
        self.assertEqual(s.controller.stop_reason,"stop deadline")
        s.stop_work()
        self.write_report("deadline-stop",s)

    def test_lost_real_sensor_process_latches_stop_before_more_gpu_work(self):
        s=self.session()
        self.assertEqual(s.tick()["state"],"offline-result")
        before=s.controller.planner.user_ns
        s.monitor.close()
        r=s.tick()
        self.assertEqual(r["state"],"paused")
        self.assertIn("telemetry",r["reason"])
        self.assertTrue(s.controller.stopped.is_set())
        self.assertIsNotNone(s.worker.process.poll())
        self.assertEqual(s.controller.planner.user_ns,before)
        s.stop_work()
        self.write_report("sensor-loss-stop",s)

    def test_stop_during_a_real_batched_worker_request(self):
        s=self.session()
        s.tick()
        start=time.monotonic()
        stop_time=[]
        # Stop from another thread while a warmed batch RPC is in flight.
        def stop():
            requested=time.perf_counter()
            s.controller.stop("explicit mid-request stop")
            stop_time.append(time.perf_counter()-requested)
        timer=threading.Timer(.003,stop)
        timer.start()
        binding=s.worker.active_binding
        try:
            # Fast kernels may complete an individual RPC before the OS timer fires.
            # Keep the bounded work loop active until Stop, rather than assuming a latency.
            while not s.controller.stopped.is_set() and time.monotonic()-start<1:
                try:
                    s.worker.transport.request("chunk",dict(binding=binding,budget_ns=1000000000,max_tiles=64))
                except WorkerError:
                    break
        finally:
            timer.join(timeout=2)
        self.assertIsNotNone(s.worker.process.poll())
        self.assertTrue(stop_time)
        self.assertLess(stop_time[0],1)
        self.assertLess(time.monotonic()-start,1)
        s.stop_work("mid-request test stop")
        self.write_report("mid-request-stop",s,{"async_stop_call_seconds":stop_time[0]})

    def test_uuid_mismatch_stops_before_cuda_work(self):
        s=self.session()
        s.worker.expected_device_uuid="GPU-does-not-match-monitored-device"
        result=s.tick()
        self.assertEqual(result["state"],"stopped")
        self.assertIn("monitored",result["reason"])
        self.assertIsNotNone(s.worker.process.poll())
        self.assertEqual(s.controller.planner.user_ns,0)

    def test_local_search_mode_rejects_other_targets_headers_and_shapes(self):
        w=ProcessWorker([str(WORKER),"--local-search-test"],expected_mode="cuda-baseline")
        self.addCleanup(w.close);w.start()
        original=dict(header76=HEADER,target=f"{1:064x}",m=64,n=64,k=4096,
            nonce=0x4b41545f6e6f6e63,device_index=0,binding="mode-gate",certificate_version=3,
            job_id="artificial",session_id=1,generation=1)
        for change in ({"target":f"{2:064x}"},{"header76":"00"*76},{"m":128},{"nonce":0}):
            with self.assertRaises(WorkerError):
                w.request("prepare",dict(original,**change))

    def test_workspace_reconfiguration_and_cached_results_match_canary(self):
        s=self.session(seconds=20)
        baseline=ProcessWorker([str(WORKER)],expected_mode="cuda-baseline")
        self.addCleanup(baseline.close)
        baseline.start(permit=s.controller._permit)
        for i,shape in enumerate((2048,2176,4096,2048)):
            binding=f"cache-check-{i}"
            baseline.request("prepare",dict(header76=HEADER,target=f"{1:064x}",m=64,n=64,k=shape,
                nonce=0x4b41545f6e6f6e63,device_index=0,binding=binding,certificate_version=3,
                job_id="artificial",session_id=1,generation=1),permit=s.controller._permit)
            first=baseline.request("chunk",dict(binding=binding,budget_ns=1000000000),permit=s.controller._permit)
            second=baseline.request("chunk",dict(binding=binding,budget_ns=1000000000),permit=s.controller._permit)
            self.assertTrue(first["canary_ok"] and second["canary_ok"])
            self.assertTrue(second["workspace_reused"])
            self.assertEqual(second["device_bytes"],24*shape+128)
        baseline.close()

    def test_owner_switch_and_context_eviction_resume_acknowledged_cursor(self):
        from hashlib import sha256
        s=self.session(seconds=20)
        s.worker.chunk_tiles=1
        s.tick()
        first=s.stats["total_tiles"]
        for i in range(5):
            header=(0x20000001+i).to_bytes(4,"little").hex()+HEADER[8:]
            job=Job(s.job.session,f"branch-{i}",sha256(bytes.fromhex(header)).hexdigest())
            s.controller.router.register(job)
            s.worker.bind_job(job,WorkerJob(header,f"{1:064x}",m=64,n=64,k=4096))
            key=(job.session.identifier,job.session.epoch,job.job_id,job.header_hash)
            r=s.worker.execute(key,1000000000,permit=s.controller._permit)
            self.assertEqual(r.stats["total_tiles"],1)
        key=(s.job.session.identifier,1,s.job.job_id,s.job.header_hash)
        resumed=s.worker.execute(key,1000000000,permit=s.controller._permit)
        self.assertEqual(resumed.stats["total_tiles"],first+1)
        self.assertEqual(resumed.stats["tile"],1)
        s.worker.reset_for_job_change()
        s.worker.start(permit=s.controller._permit)
        after_restart=s.worker.execute(key,1000000000,permit=s.controller._permit)
        self.assertEqual(after_restart.stats["total_tiles"],first+2)
        self.assertEqual(after_restart.stats["tile"],2)
        s.stop_work("resume checkpoint test")
        self.write_report("checkpoint-resume",s,{"cache_eviction_resumed":True,"owned_process_replacement_resumed":True})

    def test_fresh_job_replaces_inflight_work_without_restarting_user_session(self):
        from hashlib import sha256
        s=self.session(seconds=20)
        s.tick()
        s.controller.pacer.next_at=time.monotonic()
        old=s.worker.process
        header=(0x20000010).to_bytes(4,"little").hex()+HEADER[8:]
        new=Job(s.job.session,"new-clean-job",sha256(bytes.fromhex(header)).hexdigest())
        def supersede():
            s.controller.router.register(new,clean=True)
            s.worker.retire(s.job)
            s.worker.bind_job(new,WorkerJob(header,f"{1:064x}",m=64,n=64,k=4096))
        timer=threading.Timer(.003,supersede)
        s.worker.chunk_tiles=64
        timer.start()
        try:
            r=s.tick()
        finally:
            timer.join(timeout=1)
        self.assertIn(r["state"],("job-retired","paused","offline-result"),r)
        self.assertFalse(s.controller.stopped.is_set())
        if r["state"]=="job-retired":
            self.assertIsNotNone(old.poll())
            self.assertIsNone(s.worker.process)
        s.job=new
        self.assertEqual(s.tick()["state"],"offline-result")
        s.stop_work("clean-job transition test")
        self.write_report("clean-job-transition",s,{"explicit_session_preserved":True})

    @staticmethod
    def write_report(name,s,extra=None):
        out=ROOT/"docs/boris/evidence/load"
        out.mkdir(parents=True,exist_ok=True)
        report=s.report();report["test_result"]="passed"
        report.update(extra or {})
        (out/(name+".json")).write_text(json.dumps(report,indent=2)+"\n")
