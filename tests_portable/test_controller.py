import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest

from boris_pearl.controller import Controller
from boris_pearl.core import Job, NS, Planner, Router, Session
from boris_pearl.ipc import ProcessWorker, WorkerError, WorkerInterrupted, read_frame, transport_selftest, write_frame
from boris_pearl.safety import Guard, Telemetry
from test_core import config


def fault(mode):
    return ProcessWorker([sys.executable, str(Path(__file__).with_name("fault_worker.py")), mode])


class PipeTests(unittest.TestCase):
    def test_roundtrip_and_input_limits(self):
        stream = io.BytesIO()
        write_frame(stream, {"type": "check", "value": 5})
        stream.seek(0)
        self.assertEqual(read_frame(stream), {"type": "check", "value": 5})
        for data in (b"x", b"\xff" * 4, struct.pack(">I", 4) + b"nope",
                     struct.pack(">I", 3) + b"{}", struct.pack(">I", 2) + b"[]"):
            with self.assertRaises(WorkerError):
                read_frame(io.BytesIO(data))

    def test_real_child_selftest_and_cleanup(self):
        result = transport_selftest()
        self.assertTrue(result["proof_matches"])
        self.assertIn("no GPU or pool", result["mode"])

    def test_rejects_live_handshake_and_oversized_frame(self):
        for mode in ("handshake", "oversize"):
            worker = fault(mode)
            with self.assertRaises(WorkerError):
                worker.start()
            self.assertIsNotNone(worker.process.poll())

    def test_timeout_kills_only_owned_child(self):
        with fault("hang") as worker:
            start = time.monotonic()
            with self.assertRaises(WorkerError):
                worker.execute(("user", 1, "job", "a" * 64), NS, timeout=0.15)
            self.assertLess(time.monotonic() - start, 2)
            self.assertIsNotNone(worker.process.poll())

    def test_manual_cancel_during_response_wait(self):
        with fault("hang") as worker:
            started = time.monotonic()
            with self.assertRaises(WorkerInterrupted):
                worker.execute(("user", 1, "job", "a" * 64), NS,
                               permit=lambda: "manual stop" if time.monotonic() - started > 0.05 else None)
            self.assertIsNotNone(worker.process.poll())

    def test_crash_response_identity_and_time_are_checked(self):
        for mode in ("crash", "wrong-id", "wrong-job", "overcharge"):
            with fault(mode) as worker:
                with self.assertRaises(WorkerError):
                    worker.execute(("user", 1, "job", "a" * 64), NS)
                self.assertIsNotNone(worker.process.poll())


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = config()
        self.now = 10.0
        self.mono = 10.0
        self.temperature = 60
        self.router = Router(self.config, lambda job, proof: len(proof) == 72 and proof[:64] == job.header_hash.encode())
        self.user = Session("user", 1, "user", self.config.user_pool, self.config.user_wallet)
        self.dev = Session("dev", 1, "developer", self.config.developer_pool, self.config.developer_wallet)
        self.router.authorize(self.user)
        self.router.authorize(self.dev)
        self.uj = Job(self.user, "same", "a" * 64)
        self.dj = Job(self.dev, "same", "b" * 64)
        self.router.register(self.uj)
        self.router.register(self.dj)

    def make(self, **kwargs):
        guard = Guard(stop_at=100, max_watts=75, min_free_bytes=1000)
        controller = Controller(self.config, self.router, guard, Path(self.tmp.name) / "state.json",
                                clock=lambda: self.now, monotonic=lambda: self.mono,
                                sample=lambda: Telemetry(self.now, self.temperature, 50, 2000), **kwargs)
        self.addCleanup(controller.stop)
        return controller

    def test_explicit_policy_acknowledgment_and_stopped_default(self):
        c = self.make()
        self.assertEqual(c.tick(self.uj, self.dj)["state"], "paused")
        self.assertIsNone(c.worker.process)
        with self.assertRaises(ValueError):
            c.start("not-the-disclosed-policy")
        c.start(self.config.identity)
        self.assertEqual(c.tick(self.uj, self.dj)["owner"], "user")

    def test_full_user_dev_flow_preserves_owner_and_durable_ledger(self):
        c = self.make()
        c.start(self.config.identity)
        owners = [c.tick(self.uj, self.dj)["owner"] for _ in range(20)]
        self.assertEqual(owners[:3], ["user", "developer", "user"])
        self.assertEqual(owners.count("developer"), 10)
        self.assertEqual(c.planner.user_ns, 10 * NS)
        self.assertLessEqual(c.planner.dev_ns * 9800, c.planner.user_ns * 200)
        saved = json.loads(c.state_path.read_text())
        self.assertIsNone(saved["pending"])
        self.assertEqual(saved["dev_ns"], c.planner.dev_ns)
        c.stop()
        self.assertIsNotNone(c.worker.process.poll())

    def test_hot_gpu_and_missing_user_session_never_dispatch(self):
        c = self.make()
        c.start(self.config.identity)
        self.temperature = 90
        self.assertEqual(c.tick(self.uj, self.dj)["reason"], "GPU temperature")
        self.temperature = 60
        self.assertTrue(c.stopped.is_set())
        c = self.make()
        c.start(self.config.identity)
        self.router.disconnect("user")
        self.assertEqual(c.tick(self.uj, self.dj)["reason"], "no authorized user job")
        self.assertIsNone(c.worker.process)

    def test_write_ahead_failure_prevents_worker_start(self):
        def failing(*args):
            raise OSError("simulated disk full")
        c = self.make(persist=failing)
        c.start(self.config.identity)
        result = c.tick(self.uj, self.dj)
        self.assertEqual(result["state"], "stopped")
        self.assertIn("disk full", result["reason"])
        self.assertIsNone(c.worker.process)
        self.assertEqual(c.planner.user_ns + c.planner.dev_ns, 0)

    def test_worker_crash_waives_pending_dev_and_latches_stop(self):
        c = self.make(worker=fault("crash"))
        c.planner.user_ns = NS
        c.planner.debt = NS * 200
        c.start(self.config.identity)
        self.assertEqual(c.tick(self.uj, self.dj)["state"], "stopped")
        self.assertEqual(c.worker.request_id, 1)
        self.assertGreater(c.planner.uncertain_dev_ns, 0)
        self.assertIsNone(c.planner.pending)
        with self.assertRaises(ValueError):
            c.start(self.config.identity)

    def test_deadline_during_blocked_child_cancels_process(self):
        c = self.make(worker=fault("hang"))
        c.start(self.config.identity)
        started = time.monotonic()
        old_sample = c.sample
        def sample():
            if time.monotonic() - started > 0.15:
                self.now = 101
            return old_sample()
        c.sample = sample
        self.assertEqual(c.tick(self.uj, self.dj)["state"], "stopped")
        self.assertEqual(c.worker.request_id, 1)
        self.assertIsNotNone(c.worker.process.poll())
        self.assertEqual(c.planner.user_ns + c.planner.dev_ns, 0)

    def test_wall_clock_rollback_does_not_extend_deadline(self):
        c = self.make()
        c.start(self.config.identity)
        self.now = 1
        self.mono = 101
        self.assertEqual(c.tick(self.uj, self.dj)["reason"], "stop deadline")
        self.assertTrue(c.stopped.is_set())

    def test_restart_recovers_but_never_autostarts(self):
        c = self.make()
        c.start(self.config.identity)
        self.assertEqual(c.tick(self.uj, self.dj)["state"], "offline-result")
        c.stop()
        restored = self.make()
        self.assertEqual(restored.planner.user_ns, NS)
        self.assertEqual(restored.tick(self.uj, self.dj)["reason"], "stopped")
        self.assertIsNone(restored.worker.process)

    def test_session_loss_while_waiting_cancels_owned_worker(self):
        c = self.make(worker=fault("hang"))
        c.start(self.config.identity)
        started = time.monotonic()
        original_sample = c.sample
        def sample():
            if time.monotonic() - started > 0.15:
                self.router.disconnect("user")
            return original_sample()
        c.sample = sample
        result = c.tick(self.uj, self.dj)
        self.assertEqual(result["state"], "stopped")
        self.assertIn("user session", result["reason"])
        self.assertIsNotNone(c.worker.process.poll())


    def test_clean_job_change_retires_work_within_existing_explicit_session(self):
        c=self.make(worker=fault("hang"))
        c.start(self.config.identity)
        started=time.monotonic()
        original=c.sample
        changed=[]
        def sample():
            if not changed and time.monotonic()-started>.1:
                self.router.register(Job(self.user,"replacement","c"*64),clean=True)
                changed.append(True)
            return original()
        c.sample=sample
        result=c.tick(self.uj,self.dj)
        self.assertEqual(result["state"],"job-retired")
        self.assertFalse(c.stopped.is_set())
        self.assertTrue(c.started)
        self.assertIsNone(c.worker.process)
        self.assertIsNone(c.planner.pending)

if __name__ == "__main__":
    unittest.main()
