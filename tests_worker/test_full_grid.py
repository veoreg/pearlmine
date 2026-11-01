"""Actual artificial resident matrices, every-small-tile CPU equivalence and stop controls."""
import json
from pathlib import Path
import sys
import unittest
from boris_pearl.ipc import ProcessWorker,WorkerError
from boris_pearl.local_search import LocalSearch

ROOT=Path(__file__).resolve().parent.parent
BINARY=ROOT/"production_worker/target/release"/("boris-pearl-worker.exe" if sys.platform=="win32" else "boris-pearl-worker")
RECEIPT=ROOT/"docs/boris/evidence/worker/worker-build.json"


@unittest.skipUnless(BINARY.is_file() and RECEIPT.is_file(),"Build and review CUDA worker first")
class FullGridTests(unittest.TestCase):
    def session(self,size=64,check_all=False):
        session=LocalSearch(seconds=45,duty=.1,backend="mma-int8",full_grid_size=size,full_grid_check_all=check_all)
        self.addCleanup(session.close);session.start();return session

    def test_all_small_grid_tiles_two_nonces_match_cpu_and_official_audits(self):
        s=self.session(512,True)
        while not s.controller.stopped.is_set():
            result=s.tick()
            self.assertEqual(result["state"],"offline-result",result)
            if s.stats["complete"]:break
        self.assertEqual(s.stats["executed_tiles"],4096)
        self.assertEqual(s.stats["cpu_checks"],4096)
        self.assertEqual(s.stats["nonces_fully_scanned"],2)
        self.assertEqual(s.stats["official_audits"],4)
        self.assertNotEqual(s.worker.audits[0]["wire_sha256"],s.worker.audits[2]["wire_sha256"])
        self.assertIsNone(s.controller.pacer.ready(s.controller._permit))
        s.stop_work("small complete grid")
        self.write("grid-512-all-cpu",s)

    def test_full_grid_mode_refuses_external_data_and_other_commands(self):
        for change in ({"header76":"00"*76},{"target":"ff"*32},{"nonce":1},{"resume_cursor":2},{"check_all":"false"},{"size":128},{"size":131072,"check_all":True}):
            with self.subTest(change=change):
                w=ProcessWorker([str(BINARY),"--full-grid-local-test"],expected_mode="cuda-baseline")
                self.addCleanup(w.close);w.start()
                with self.assertRaises(WorkerError):
                    w.request("full-grid-prepare",dict(size=64,device_index=0,**{k:v for k,v in change.items() if k!="size"}) if "size" not in change else dict(device_index=0,**change))
                w.close()
        w=ProcessWorker([str(BINARY),"--full-grid-local-test"],expected_mode="cuda-baseline")
        self.addCleanup(w.close);w.start()
        with self.assertRaises(WorkerError):w.request("prepare",{})
        w.close()

    def test_resident_grid_manual_and_lost_sensor_stop(self):
        for case in ("manual","sensor-loss"):
            s=self.session()
            self.assertEqual(s.tick()["state"],"offline-result")
            active=s.controller.planner.user_ns
            if case=="sensor-loss":
                s.monitor.close()
                result=s.tick()
                self.assertEqual(result["state"],"paused")
                self.assertEqual(s.controller.planner.user_ns,active)
            stop=s.stop_work("resident local manual stop" if case=="manual" else None)
            self.assertTrue(stop["worker_reaped"])
            if case=="manual":
                self.assertTrue(stop["nvml_after_stop_fresh"])
                self.assertFalse(stop["worker_still_listed_by_nvml"])
            self.write("grid-"+case+"-stop",s)
            s.close()

    def test_full_size_deadline_reaps_resident_worker_without_next_tick(self):
        s=LocalSearch(seconds=2.5,duty=.1,backend="mma-int8",full_grid_size=131072)
        self.addCleanup(s.close);s.start()
        self.assertEqual(s.tick()["state"],"offline-result")
        self.assertEqual(s.stats["device_bytes"],1107706920)
        self.assertTrue(s.controller.stopped.wait(4))
        s.worker.process.wait(timeout=2)
        stopped=s.stop_work()
        self.assertEqual(stopped["reason"],"stop deadline")
        self.assertTrue(stopped["worker_reaped"])
        self.assertTrue(stopped["nvml_after_stop_fresh"])
        self.assertFalse(stopped["worker_still_listed_by_nvml"])
        self.write("grid-full-size-deadline-stop",s)

    @staticmethod
    def write(name,s):
        output=ROOT/"docs/boris/evidence/full-size"
        output.mkdir(parents=True,exist_ok=True)
        report=s.report();report["test_result"]="passed"
        (output/(name+".json")).write_text(json.dumps(report,indent=2)+"\n")
