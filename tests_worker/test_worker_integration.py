"""Bounded, fixed-nonce GPU fixtures and loopback TLS only. Never --live/search.

These tests deliberately do not infer production throughput, income or cooling.
"""
import base64
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests_portable"))
from mock_pool import HEADER, TARGET, MockPool, notify, eventually, x509
from test_core import fixture_address

from boris_pearl.config import Config
from boris_pearl.controller import Controller
from boris_pearl.core import Job, NS, Router, Session
from boris_pearl.cuda_worker import CudaWorker, WorkerJob, FIXED_NONCE
from boris_pearl.ipc import WorkerError
from boris_pearl.official_bridge import OfficialVerifierCallback
from boris_pearl.pool import PoolAccess, PoolClient, PoolError
from boris_pearl.runtime import PoolCoordinator
from boris_pearl.safety import Guard, Telemetry

WORKER = ROOT / "production_worker/target/release" / ("boris-pearl-worker.exe" if sys.platform == "win32" else "boris-pearl-worker")
VERIFIER = ROOT / "proof_bridge/target/release" / ("boris-official-proof-check.exe" if sys.platform == "win32" else "boris-official-proof-check")
WR = ROOT / "docs/boris/evidence/worker/worker-build.json"
VR = ROOT / "docs/boris/evidence/proof-lab/official-build.json"


@unittest.skipUnless(WORKER.is_file() and VERIFIER.is_file() and WR.is_file() and VR.is_file() and x509 is not None,
                     "Build/audit both binaries and supply optional TLS test dependency first")
class RealWorkerTests(unittest.TestCase):
    def verifier(self):
        return OfficialVerifierCallback(VERIFIER, json.loads(VR.read_text())["binary_sha256"])

    def worker(self, verifier):
        worker = CudaWorker(WORKER, json.loads(WR.read_text())["binary_sha256"], verifier)
        self.addCleanup(worker.close)
        return worker

    def test_fixed_cuda_proof_official_verify_tls_and_fee_owner(self):
        # Two different committed headers prevent accidental proof reuse across owners.
        dev_header = (0x20000001).to_bytes(4, "little").hex() + HEADER[8:]
        with MockPool(early=True) as upool, MockPool(first_job=notify(header=dev_header)) as dpool, tempfile.TemporaryDirectory() as tmp:
            config = Config(fixture_address(3), upool.endpoint, 200, fixture_address(7), dpool.endpoint)
            verifier = self.verifier()
            router = Router(config, verifier)
            worker = self.worker(verifier)
            clients = []
            for owner, pool, wallet in (("user", upool, config.user_wallet), ("developer", dpool, config.developer_wallet)):
                template = Session(owner, 1, owner, pool.endpoint, wallet)
                client = PoolClient(config, router, template, PoolAccess(pool.endpoint, config.identity, allow_submit=True),
                                    tls_context=pool.client_context, on_retire=worker.retire)
                clients.append(client)
                self.addCleanup(client.close)
                client.connect()
            uj, dj = [c.next_job() for c in clients]
            for pooljob in (uj, dj):
                worker.bind_job(pooljob.job, WorkerJob(pooljob.header76, pooljob.target))
            # Guard inputs below are TEST telemetry, not evidence of physical safety qualification.
            guard = Guard(stop_at=time.time() + 90, max_watts=100, min_free_bytes=1000)
            controller = Controller(config, router, guard, Path(tmp) / "state.json", worker=worker,
                    sample=lambda: Telemetry(time.time(), 50, 50, 1_000_000_000))
            self.addCleanup(controller.stop)
            controller.start(config.identity)
            user_result = controller.tick(uj.job, dj.job)
            self.assertEqual(user_result["state"], "offline-result", user_result)
            self.assertEqual(user_result["verified_proofs"], 1)
            self.assertGreater(user_result["active_ns"], 0)
            self.assertFalse(user_result["submitted"])
            self.assertEqual(clients[0].submit(uj.job)["status"], "accepted")
            with self.assertRaises(PoolError):
                clients[1].submit(uj.job)
            # Synthetic prior ledger credit admits one developer tile without a long GPU run.
            # This is composition testing, not a measured 2%-of-walltime experiment.
            for _ in range(5):
                grant = controller.planner.reserve(running=True, user_ready=True, dev_ready=True,
                                                   min_developer_ns=NS)
                self.assertEqual(grant.owner, "user")
                controller.planner.complete(grant, NS)
            dev_result = controller.tick(uj.job, dj.job)
            self.assertEqual(dev_result["state"], "offline-result", dev_result)
            self.assertEqual(dev_result["owner"], "developer")
            self.assertEqual(dev_result["verified_proofs"], 1)
            self.assertEqual(clients[1].submit(dj.job)["status"], "accepted")
            self.assertLessEqual(controller.planner.dev_ns * controller.planner.den,
                                 controller.planner.user_ns * controller.planner.bps)
            packets = []
            for client, pool, pj in zip(clients, (upool, dpool), (uj, dj)):
                packet = [m for m in pool.received if m["method"] == "mining.submit"][0]
                wire = base64.b64decode(packet["params"]["plain_proof"])
                self.assertEqual(wire, verifier.verified_wire[pj.job])
                self.assertEqual(packet["params"]["wallet"], pj.job.session.wallet)
                packets.append({"owner":pj.job.session.owner,"wire_sha256":sha256(wire).hexdigest(),
                                "wire_bytes":len(wire),"mock_acceptance":True})
            self.assertNotEqual(packets[0]["wire_sha256"], packets[1]["wire_sha256"])
            previous_user_wire=verifier.verified_wire[uj.job]
            returned=controller.tick(uj.job,None) # developer unavailable; return to user's saved cursor
            self.assertEqual(returned["state"],"offline-result",returned)
            self.assertEqual(returned["owner"],"user")
            self.assertEqual(returned["work_stats"]["total_tiles"],2)
            self.assertNotEqual(previous_user_wire,verifier.verified_wire[uj.job])
            self.assertEqual(clients[0].submit(uj.job)["status"],"accepted")
            self.assertEqual(sum(m["method"]=="mining.submit" for m in upool.received),2)
            controller.stop()
            self.assertIsNotNone(worker.process.poll())
            (ROOT / "docs/boris/evidence/worker/integration-report.json").write_text(json.dumps({
                "scope":"two fixed small GPU tiles, official verifier, loopback TLS mocks only",
                "user":user_result,"developer":dev_result,"returned_user":returned,"packets":packets,
                "fee_setup":"5 seconds synthetic prior active credit; not a physical fee rate measurement",
                "worker_reaped":worker.process.poll() is not None,"external_pool_connections":0,
                "real_submissions":0,"live_mode":False}, indent=2) + "\n")

    def test_seventeen_slice_non_power_of_two_merkle_full_proof(self):
        verifier=self.verifier()
        worker=self.worker(verifier)
        config=Config(fixture_address(3),"stratum+tcp://127.0.0.1:1")
        router=Router(config,verifier)
        session=Session("k17",1,"user",config.user_pool,config.user_wallet)
        router.authorize(session)
        job=Job(session,"fixed-k2176",sha256(bytes.fromhex(HEADER)).hexdigest())
        router.register(job)
        target=f"{((1<<256)-1)//(128*2176):064x}"
        worker.bind_job(job,WorkerJob(HEADER,target,m=64,n=64,k=2176))
        worker.start()
        result=worker.execute((session.identifier,1,job.job_id,job.header_hash),NS)
        self.assertEqual(len(result.proofs),1)
        self.assertEqual(router.route(job,result.proofs[0]),session)
        self.assertTrue(result.stats["kernel_variant"]==4)
        worker.close()

    def test_live_header_nonce_and_worker_binding_are_rejected_in_default_mode(self):
        v = self.verifier()
        worker = self.worker(v)
        worker.start()
        for changes in ({"header76":"00" * 76}, {"nonce":FIXED_NONCE + 1}):
            spec = WorkerJob(HEADER, TARGET)
            params = dict(spec.__dict__, binding="test", certificate_version=3, job_id="fixture", session_id=1, generation=1)
            params.update(changes)
            with self.assertRaises(WorkerError):
                worker.transport.request("prepare", params)
        with self.assertRaises(WorkerError):
            worker.transport.request("chunk", {"binding":"foreign", "budget_ns":NS})

    def test_cancelled_permission_never_dispatches_cuda(self):
        v = self.verifier()
        worker = self.worker(v)
        s = Session("u", 1, "user", "stratum+tcp://127.0.0.1:1", fixture_address(3))
        j = Job(s, "fixture", sha256(bytes.fromhex(HEADER)).hexdigest())
        worker.bind_job(j, WorkerJob(HEADER, TARGET))
        worker.start()
        with self.assertRaises(WorkerError):
            worker.execute(("u", 1, "fixture", j.header_hash), NS, permit=lambda: "cancel test")
        self.assertIsNotNone(worker.process.poll())

    def test_coordinator_developer_outage_waives_fee_and_user_loss_pauses(self):
        import socket
        with MockPool() as upool, MockPool(auth=False) as dpool, tempfile.TemporaryDirectory() as tmp:
            config = Config(fixture_address(3), upool.endpoint, 200, fixture_address(7), dpool.endpoint)
            verifier = self.verifier()
            router = Router(config, verifier)
            worker = self.worker(verifier)
            clients = []
            for owner, pool, wallet in (("user", upool, config.user_wallet), ("developer", dpool, config.developer_wallet)):
                clients.append(PoolClient(config, router, Session(owner, 1, owner, pool.endpoint, wallet),
                    PoolAccess(pool.endpoint, config.identity, allow_submit=True), tls_context=pool.client_context,
                    on_retire=worker.retire))
            controller = Controller(config, router, Guard(stop_at=time.time()+60, max_watts=100, min_free_bytes=1000),
                    Path(tmp)/"state.json", worker=worker,
                    sample=lambda: Telemetry(time.time(), 50, 50, 1_000_000_000))
            coordinator = PoolCoordinator(controller, *clients, shape=(256, 256, 2048))
            self.addCleanup(coordinator.stop)
            self.assertEqual(coordinator.tick()["reason"], "explicit start required")
            self.assertIsNone(worker.process)
            coordinator.start(config.identity)
            clients[0].next_job()
            result = coordinator.tick(submit=True)
            self.assertEqual(result["state"], "offline-result", result)
            self.assertEqual(result["pool_result"], "accepted")
            self.assertEqual(result["owner"], "user")
            self.assertEqual(controller.planner.debt, 0)
            self.assertEqual(controller.planner.dev_ns, 0)
            self.assertIn("fee waived", coordinator.last_error)
            upool.connection.shutdown(socket.SHUT_RDWR)
            eventually(clients[0].closed.is_set)
            paused = coordinator.tick(submit=True)
            self.assertEqual(paused["state"], "paused")
            self.assertEqual(paused["reason"], "no authorized user job")
            self.assertEqual(controller.planner.dev_ns, 0)
            coordinator.stop()
            self.assertIsNotNone(worker.process.poll())

    def test_full_size_fixed_fixture_exactly_one_tile_and_official_proof(self):
        verifier = self.verifier()
        worker = CudaWorker(WORKER, json.loads(WR.read_text())["binary_sha256"], verifier, large_fixed_fixture=True)
        self.addCleanup(worker.close)
        config = Config(fixture_address(3), "stratum+tcp://127.0.0.1:1")
        router = Router(config, verifier)
        session = Session("full-fixed", 1, "user", config.user_pool, config.user_wallet)
        router.authorize(session)
        job = Job(session, "single-fixed-tile", sha256(bytes.fromhex(HEADER)).hexdigest())
        router.register(job)
        target = f"{((1 << 256)-1) >> 19:064x}"
        worker.bind_job(job, WorkerJob(HEADER, target, m=131072, n=131072, k=4096))
        worker.start()
        started = time.monotonic()
        result = worker.execute((session.identifier, 1, job.job_id, job.header_hash), NS,
                permit=lambda: "bounded full-size fixture deadline" if time.monotonic()-started > 45 else None)
        self.assertEqual(len(result.proofs), 1)
        self.assertEqual(router.route(job, result.proofs[0]), session)
        wire = verifier.verified_wire[job]
        with self.assertRaisesRegex(WorkerError, "exactly one tile"):
            worker.execute((session.identifier, 1, job.job_id, job.header_hash), NS)
        worker.close()
        self.assertIsNotNone(worker.process.poll())
        (ROOT / "docs/boris/evidence/worker/full-size-fixed-report.json").write_text(json.dumps({
            "scope":"fixed synthetic full-size preparation and exactly one tile; no search/network",
            "shape":[131072,131072,4096],"fixed_nonce":FIXED_NONCE,"tiles_executed":1,
            "official_verification":"passed","wire_bytes":len(wire),"wire_sha256":sha256(wire).hexdigest(),
            "active_ns":result.active_ns,"whole_test_elapsed_seconds":time.monotonic()-started,
            "second_tile_refused":True,"worker_reaped":True,"live_mode":False,"real_submissions":0},indent=2)+"\n")
