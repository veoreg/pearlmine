"""Real loopback sockets/TLS; transport tests use an explicitly synthetic verifier."""
import base64
import gzip
import ssl
import unittest
from unittest.mock import patch

from boris_pearl.config import Config
from boris_pearl.core import Job, Router, Session, Planner, NS
from boris_pearl.pool import PoolAccess, PoolClient, PoolError, PoolFailover, parse_notify
from mock_pool import MockPool, eventually, notify, TARGET, x509
from test_core import fixture_address, config


class TransportVerifier:
    """Test double only; official verifier is required by worker integration tests."""
    def __init__(self):
        self.verified_wire = {}
        self.contexts = {}

    def bind(self, job, context):
        self.contexts[job] = context

    def retire(self, job):
        self.contexts.pop(job, None)
        self.verified_wire.pop(job, None)

    def __call__(self, job, proof):
        if job not in self.contexts or proof != b"transport-test-candidate":
            return False
        self.verified_wire[job] = b"local mock canonical proof"
        return True

    def canonical_bytes(self, job, proof):
        return self.verified_wire[job]


class PolicyTests(unittest.TestCase):
    def test_external_access_is_denied_before_any_dns_or_socket(self):
        c = config()
        r = Router(c, TransportVerifier())
        s = Session("u", 1, "user", c.user_pool, c.user_wallet)
        client = PoolClient(c, r, s, PoolAccess(c.user_pool, c.identity))
        with patch("socket.create_connection") as dial:
            with self.assertRaises(PermissionError):
                client.connect()
            dial.assert_not_called()

    def test_session_destination_and_tls_cannot_be_silently_changed(self):
        c = config()
        r = Router(c, TransportVerifier())
        with self.assertRaises(ValueError):
            PoolClient(c, r, Session("u", 1, "user", c.user_pool, c.developer_wallet), PoolAccess(c.user_pool, c.identity))
        s = Session("u", 1, "user", c.user_pool, c.user_wallet)
        ctx = ssl._create_unverified_context()
        client = PoolClient(c, r, s, PoolAccess(c.user_pool, c.identity, allow_external=True), tls_context=ctx)
        with patch("socket.create_connection") as dial:
            with self.assertRaises(PermissionError):
                client.connect()
            dial.assert_not_called()

    def test_notify_requires_v3_and_exact_bounded_fields(self):
        for field, value in (("cert_version", 4), ("cert_version", None), ("cert_version", True),
                             ("target", "00"), ("target", "f" * 65), ("header", "a" * 151),
                             ("job_id", ""), ("job_id", "a" * 129)):
            message = notify()
            message["params"][field] = value
            with self.assertRaises(PoolError):
                parse_notify(message)

    def test_session_retired_during_verification_is_rejected(self):
        c = config()
        def verifier(job, proof):
            r.disconnect(job.session.identifier)
            return True
        r = Router(c, verifier)
        s = Session("u", 1, "user", c.user_pool, c.user_wallet)
        r.authorize(s)
        j = Job(s, "j", "a" * 64)
        r.register(j)
        with self.assertRaisesRegex(ValueError, "stale during"):
            r.route(j, b"candidate")
        self.assertFalse(r.seen)

    def test_minimum_developer_tile_accumulates_without_overcharging(self):
        p = Planner(config())
        for _ in range(4):
            g = p.reserve(running=True, user_ready=True, dev_ready=True, min_developer_ns=100_000_000)
            self.assertEqual(g.owner, "user")
            p.complete(g, NS)
        g = p.reserve(running=True, user_ready=True, dev_ready=True, min_developer_ns=100_000_000)
        self.assertEqual(g.owner, "user")
        p.complete(g, NS)
        g = p.reserve(running=True, user_ready=True, dev_ready=True, min_developer_ns=100_000_000)
        self.assertEqual(g.owner, "developer")
        self.assertLessEqual(g.budget_ns * p.den, p.user_ns * p.bps)


@unittest.skipUnless(x509 is not None, "Optional cryptography test dependency absent")
class PoolTests(unittest.TestCase):
    def make(self, server, *, dialect="object", submit=True, verifier=None, owner="user", timeout=1):
        c = Config(fixture_address(3), server.endpoint, 200, fixture_address(7), server.endpoint)
        r = Router(c, verifier or TransportVerifier())
        s = Session(owner, 1, owner, server.endpoint, c.user_wallet if owner == "user" else c.developer_wallet)
        client = PoolClient(c, r, s, PoolAccess(server.endpoint, c.identity, allow_submit=submit),
                            dialect=dialect, tls_context=server.client_context, timeout=timeout)
        self.addCleanup(client.close)
        return client, r

    def test_verified_tls_authorization_and_early_notify(self):
        with MockPool(early=True) as server:
            c, r = self.make(server)
            session = c.connect()
            job = c.next_job()
            self.assertEqual(job.job.session, session)
            self.assertEqual(r.sessions["user"], session)
            self.assertEqual(server.received[0]["params"]["wallet"], session.wallet)
            self.assertEqual(job.target, TARGET)

    def test_untrusted_or_mismatched_tls_fails_closed(self):
        for mismatch in (False, True):
            with MockPool(matching_cert=not mismatch) as server:
                c, r = self.make(server)
                if not mismatch:
                    c.tls_context = ssl.create_default_context()
                with self.assertRaises(ssl.SSLCertVerificationError):
                    c.connect()
                self.assertFalse(r.sessions)
                self.assertFalse(server.received)

    def test_auth_refusal_and_timeout_never_adopt_early_jobs(self):
        for auth in (False, "silent"):
            with MockPool(auth=auth, early=True) as server:
                c, r = self.make(server, timeout=0.3)
                with self.assertRaises(PoolError):
                    c.connect()
                self.assertFalse(r.sessions)
                self.assertFalse(r.jobs)

    def test_submit_requires_endpoint_permission_and_local_routing(self):
        with MockPool() as server:
            c, r = self.make(server, submit=False)
            c.connect()
            job = c.next_job().job
            with self.assertRaises(PermissionError):
                c.submit(job)
            c.access = PoolAccess(server.endpoint, c.config.identity, allow_submit=True)
            with self.assertRaises(PoolError):
                c.submit(job)
            self.assertEqual(r.route(job, b"transport-test-candidate"), job.session)
            self.assertEqual(c.submit(job)["status"], "accepted")
            with self.assertRaises(PoolError):
                c.submit(job)
            sent = [m for m in server.received if m["method"] == "mining.submit"]
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["params"]["wallet"], job.session.wallet)
            self.assertEqual(base64.b64decode(sent[0]["params"]["plain_proof"]), r.verify.verified_wire[job])

    def test_dialects_and_explicit_v2_gzip_ack(self):
        for dialect, ack in (("kryptex", None), ("kryptex-v2", "v2"), ("kryptex-v2", None)):
            with MockPool(ack_type=ack) as server:
                c, r = self.make(server, dialect=dialect)
                c.connect()
                job = c.next_job().job
                r.route(job, b"transport-test-candidate")
                self.assertEqual(c.submit(job)["status"], "accepted")
                packet = [m for m in server.received if m["method"] == "mining.submit"][0]
                self.assertNotIn("wallet", packet["params"])
                self.assertEqual(packet["params"]["worker"],f"{job.session.wallet}.{c.worker_name}")
                wire = base64.b64decode(packet["params"]["plain_proof"])
                if ack:
                    wire = gzip.decompress(wire)
                self.assertEqual(wire, r.verify.verified_wire[job])

    def test_same_wire_id_target_change_retires_old_proof(self):
        with MockPool() as server:
            c, r = self.make(server)
            c.connect()
            old = c.next_job()
            r.route(old.job, b"transport-test-candidate")
            server.send(notify(target=f"{int(TARGET, 16) // 2:064x}"))
            new = c.next_job()
            self.assertEqual(old.wire_job_id, new.wire_job_id)
            self.assertNotEqual(old.job.job_id, new.job.job_id)
            self.assertNotEqual(old.nbits_share, new.nbits_share)
            self.assertNotIn(old.job, r.verify.contexts)
            with self.assertRaises(PoolError):
                c.submit(old.job)

    def test_lost_ack_is_uncertain_and_never_resent(self):
        with MockPool(submit="lose-ack") as server:
            c, r = self.make(server)
            c.connect()
            job = c.next_job().job
            r.route(job, b"transport-test-candidate")
            result = c.submit(job)
            self.assertEqual(result["status"], "uncertain")
            self.assertFalse(result["resent"])
            with self.assertRaises(PoolError):
                c.submit(job)
            self.assertEqual(sum(m["method"] == "mining.submit" for m in server.received), 1)
            self.assertFalse(r.sessions)

    def test_rejection_does_not_reroute_or_resubmit(self):
        with MockPool(submit="reject") as server:
            c, r = self.make(server, owner="developer")
            c.connect()
            job = c.next_job().job
            r.route(job, b"transport-test-candidate")
            self.assertEqual(c.submit(job)["status"], "rejected")
            self.assertEqual(server.received[-1]["params"]["wallet"], c.config.developer_wallet)
            with self.assertRaises(PoolError):
                c.submit(job)

    def test_malformed_oversized_and_new_consensus_disconnect(self):
        for payload in (b"not-json\n", b"[]\n", b"[" * 2000 + b"0" + b"]" * 2000 + b"\n",
                        b"x" * 2_000_001, json_notify_v4()):
            with MockPool(tls=False) as server:
                c, r = self.make(server)
                c.connect()
                c.next_job()
                server.raw(payload)
                eventually(c.closed.is_set)
                self.assertFalse(r.sessions)
                self.assertFalse(r.jobs)

    def test_job_retired_between_submit_preparation_and_dispatch_is_never_sent(self):
        with MockPool() as server:
            c, r = self.make(server)
            c.connect()
            old = c.next_job().job
            r.route(old, b"transport-test-candidate")
            request = c._request
            def race(*args, **kwargs):
                server.send(notify(job_id="replacement"))
                c.next_job()
                return request(*args, **kwargs)
            with patch.object(c, "_request", side_effect=race):
                self.assertEqual(c.submit(old)["status"], "uncertain")
            self.assertFalse(any(m["method"] == "mining.submit" for m in server.received))

    def test_explicit_reconnect_changes_epoch_and_never_replays_old_proof(self):
        with MockPool() as server:
            c, r = self.make(server)
            c.connect()
            old = c.next_job().job
            r.route(old, b"transport-test-candidate")
            c.close()
            server.accept_reconnect()
            c.connect()
            new = c.next_job().job
            self.assertGreater(new.session.epoch, old.session.epoch)
            self.assertNotIn(old, r.verify.contexts)
            with self.assertRaises(PoolError):
                c.submit(old)
            self.assertFalse(any(m["method"] == "mining.submit" for m in server.received))

    def test_preserved_redacted_pool_captures_parse_without_connections(self):
        import json
        from pathlib import Path
        paths = list((Path(__file__).resolve().parent.parent / "tests/fixtures").glob("capture-*.jsonl"))
        self.assertEqual(len(paths), 3)
        with patch("socket.create_connection") as dial:
            total = 0
            for path in paths:
                for line in path.read_text().splitlines():
                    message = json.loads(line).get("msg", {})
                    if message.get("method") == "mining.notify":
                        self.assertEqual(parse_notify(message)[3], 3)
                        total += 1
            self.assertGreaterEqual(total, 10)
            dial.assert_not_called()

    def test_failover_backs_off_and_rotates_only_explicit_endpoints(self):
        with MockPool(auth=False) as first, MockPool() as second:
            a, _ = self.make(first)
            b, _ = self.make(second)
            f = PoolFailover([a, b])
            self.assertIsNone(f.advance(0))
            self.assertIsNone(f.advance(0.5))
            self.assertFalse(second.received)
            self.assertIs(f.advance(1), b)
            self.assertIs(f.advance(1.1), b)


def json_notify_v4():
    import json
    return json.dumps(notify(cert=4)).encode() + b"\n"
