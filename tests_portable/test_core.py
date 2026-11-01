import json
from pathlib import Path
import random
import tempfile
import unittest

from boris_pearl.config import CHARSET, Config, polymod, pool_url, valid_address
from boris_pearl.core import Job, NS, Planner, Router, Session, save_state
from boris_pearl.safety import Guard, Telemetry


def fixture_address(marker):
    # Synthetic public witness program, no private key or wallet. Never use for payouts.
    program = bytes([marker]) * 32
    acc = bits = 0
    data = [1]
    for byte in program:
        acc = (acc << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            data.append((acc >> bits) & 31)
    if bits:
        data.append((acc << (5 - bits)) & 31)
    hrp = [ord(c) >> 5 for c in "prl"] + [0] + [ord(c) & 31 for c in "prl"]
    checksum = polymod(hrp + data + [0] * 6) ^ 0x2BC830A3
    return "prl1" + "".join(CHARSET[v] for v in data + [(checksum >> (5 * (5 - i))) & 31 for i in range(6)])


def config(bps=200):
    return Config(fixture_address(3), "stratum+ssl://user.invalid:1200", bps,
                  fixture_address(7), "stratum+ssl://developer.invalid:1200")


def step(planner, *, ready=True, active=None):
    grant = planner.reserve(running=True, user_ready=True, dev_ready=ready)
    planner.complete(grant, grant.budget_ns if active is None else active)
    return grant


class ConfigurationTests(unittest.TestCase):
    def test_default_has_no_fee_or_destination(self):
        c = Config()
        self.assertEqual(c.effective_bps, 0)
        self.assertEqual(c.developer_wallet, "")
        self.assertIn("Pool fee, separate: unknown", c.disclosure())

    def test_nonzero_fee_needs_public_destination(self):
        with self.assertRaises(ValueError):
            Config(fee_bps=200)

    def test_checksum_network_case_and_padding(self):
        good = fixture_address(3)
        self.assertTrue(valid_address(good))
        self.assertTrue(valid_address(good.upper()))
        for bad in (good[:-1] + ("q" if good[-1] != "q" else "p"), "btc" + good[3:],
                    good[:5].upper() + good[5:], "private key", good + " "):
            self.assertFalse(valid_address(bad), bad)

    def test_self_mining_has_no_fee(self):
        c = config()
        self.assertEqual(Config(c.developer_wallet, c.user_pool, 200, c.developer_wallet, c.developer_pool).effective_bps, 0)

    def test_fee_and_pool_fee_validation(self):
        for bps in (-1, True, 201, 10000, 2.0):
            with self.assertRaises(ValueError):
                Config(fee_bps=bps)
        for value in (float("nan"), float("inf"), -1, 101, True):
            with self.assertRaises(ValueError):
                Config(pool_fee_percent=value)
        for key in ("user_wallet", "user_pool", "developer_wallet", "developer_pool"):
            with self.assertRaises(ValueError):
                Config(**{key: False})

    def test_endpoint_rejects_secrets_and_wrong_schemes(self):
        for bad in ("https://pool.invalid", "stratum+tcp://secret@pool.invalid:1",
                    "stratum+ssl://pool.invalid:99999", "stratum+ssl://pool.invalid:1/path",
                    "stratum+tcp://pool.invalid:1?wallet=x"):
            with self.assertRaises(ValueError):
                pool_url(bad)
        self.assertEqual(pool_url("stratum+ssl://[::1]:1200"), "stratum+ssl://[::1]:1200")


class FeeTests(unittest.TestCase):
    def test_zero_never_schedules_developer(self):
        p = Planner(Config())
        for _ in range(10_000):
            self.assertEqual(step(p).owner, "user")
        self.assertEqual(p.dev_ns, 0)
        self.assertEqual(p.debt, 0)

    def test_exact_two_percent_and_one_point_five(self):
        for bps in (150, 200):
            p = Planner(config(bps))
            remaining = 10000 * NS
            while remaining:
                g = p.reserve(running=True, user_ready=True, dev_ready=True)
                active = min(remaining, g.budget_ns)
                p.complete(g, active)
                remaining -= active
            self.assertLessEqual(p.dev_ns * 10000, (p.user_ns + p.dev_ns) * bps)
            self.assertLess((p.user_ns + p.dev_ns) * bps - p.dev_ns * 10000, NS * 10000)

    def test_idle_pause_no_user_pool_no_accounting(self):
        p = Planner(config())
        for running, ready in ((False, True), (True, False), (False, False)):
            before = p.snapshot()
            self.assertIsNone(p.reserve(running=running, user_ready=ready, dev_ready=True))
            self.assertEqual(p.snapshot(), before)

    def test_zero_activity_is_never_charged(self):
        p = Planner(config())
        for _ in range(100):
            step(p, active=0)
        self.assertEqual(p.user_ns + p.dev_ns + p.debt, 0)

    def test_developer_outage_waives_backlog(self):
        p = Planner(config())
        step(p)
        self.assertGreater(p.debt, 0)
        for _ in range(1000):
            self.assertEqual(step(p, ready=False).owner, "user")
        self.assertEqual(p.debt, 0)
        self.assertEqual(step(p).owner, "user")

    def test_duplicate_or_oversized_activity_rejected(self):
        p = Planner(config())
        g = p.reserve(running=True, user_ready=True, dev_ready=True)
        with self.assertRaises(ValueError):
            p.reserve(running=True, user_ready=True, dev_ready=True)
        for bad in (g.budget_ns + 1, -1, True):
            with self.assertRaises(ValueError):
                p.complete(g, bad)
        p.complete(g, 100)
        with self.assertRaises(ValueError):
            p.complete(g, 100)

    def test_restart_preserves_fraction_and_total(self):
        p = Planner(config())
        for _ in range(300):
            step(p)
            before = p.snapshot()
            p = Planner.restore(config(), json.loads(json.dumps(before)))
            self.assertEqual(p.snapshot(), before)

    def test_crash_after_dev_reservation_does_not_charge_it_twice(self):
        p = Planner(config())
        step(p)
        g = p.reserve(running=True, user_ready=True, dev_ready=True)
        self.assertEqual(g.owner, "developer")
        restored = Planner.restore(config(), p.snapshot())
        self.assertIsNone(restored.pending)
        self.assertEqual(restored.dev_ns, 0)
        self.assertEqual(restored.uncertain_dev_ns, g.budget_ns)
        self.assertEqual(step(restored).owner, "user")

    def test_state_rejects_other_wallet_and_corruption(self):
        state = Planner(config()).snapshot()
        with self.assertRaises(ValueError):
            Planner.restore(Config(), state)
        for key, value in (("version", 2), ("debt", 10**30), ("user_ns", -1), ("sequence", True)):
            with self.assertRaises(ValueError):
                Planner.restore(config(), dict(state, **{key: value}))

    def test_random_activity_restart_outage_never_overcharges(self):
        r = random.Random(131)
        p = Planner(config())
        for _ in range(20_000):
            ready = r.random() > 0.15
            g = p.reserve(running=True, user_ready=True, dev_ready=ready)
            p.complete(g, r.randrange(g.budget_ns + 1))
            if r.random() < 0.1:
                p = Planner.restore(config(), p.snapshot())
            self.assertLessEqual(p.dev_ns * 9800, p.user_ns * 200)

    def test_atomic_state_roundtrip(self):
        p = Planner(config())
        step(p)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            save_state(path, p)
            restored = Planner.restore(config(), json.loads(path.read_text()))
            self.assertEqual(restored.snapshot(), p.snapshot())
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        c = config()
        self.router = Router(c, lambda job, proof: proof == job.header_hash.encode())
        self.user = Session("user", 1, "user", c.user_pool, c.user_wallet)
        self.dev = Session("dev", 1, "developer", c.developer_pool, c.developer_wallet)
        self.router.authorize(self.user)
        self.router.authorize(self.dev)
        self.uj = Job(self.user, "same-job-id", "a" * 64)
        self.dj = Job(self.dev, "same-job-id", "b" * 64)
        self.router.register(self.uj)
        self.router.register(self.dj)

    def test_shares_never_follow_current_fee_destination(self):
        self.assertEqual(self.router.route(self.uj, b"a" * 64), self.user)
        self.assertEqual(self.router.route(self.dj, b"b" * 64), self.dev)

    def test_wrong_proof_and_duplicate_are_rejected(self):
        with self.assertRaises(ValueError):
            self.router.route(self.uj, b"b" * 64)
        self.router.route(self.uj, b"a" * 64)
        with self.assertRaises(ValueError):
            self.router.route(self.uj, b"a" * 64)

    def test_reconnect_and_clean_invalidate_old_work(self):
        self.router.authorize(Session("user", 2, "user", config().user_pool, fixture_address(3)))
        with self.assertRaises(ValueError):
            self.router.route(self.uj, b"a" * 64)
        self.router.register(Job(self.dev, "replacement", "c" * 64), clean=True)
        with self.assertRaises(ValueError):
            self.router.route(self.dj, b"b" * 64)

    def test_session_spoof_unknown_fork_reused_header_rejected(self):
        for bad in (Job(self.user, "v4", "c" * 64, 4),
                    Job(self.user, self.uj.job_id, "c" * 64),
                    Job(Session("foreign", 1, "user", "x", "x"), "x", "c" * 64)):
            with self.assertRaises(ValueError):
                self.router.register(bad)

    def test_disconnected_epoch_and_undisclosed_wallet_rejected(self):
        self.router.disconnect("user")
        with self.assertRaises(ValueError):
            self.router.authorize(self.user)
        with self.assertRaises(ValueError):
            self.router.authorize(Session("user", 2, "user", self.user.pool, self.dev.wallet))

    def test_truthy_non_boolean_verifier_is_rejected(self):
        self.router.verify = lambda job, proof: "unverified"
        with self.assertRaises(ValueError):
            self.router.route(self.uj, b"a" * 64)

    def test_equivalent_envelopes_cannot_repeat_the_same_canonical_share(self):
        class SyntheticCanonicalVerifier:
            def __call__(self, job, proof):
                return True
            def canonical_bytes(self, job, original):
                return b"one-synthetic-wire-proof"
        self.router.verify = SyntheticCanonicalVerifier()
        self.assertEqual(self.router.route(self.uj, b"first-json-envelope"), self.user)
        with self.assertRaises(ValueError):
            self.router.route(self.uj, b"same-proof-with-different-json-formatting")


class SafetyTests(unittest.TestCase):
    def guard(self, **kwargs):
        guard = Guard(stop_at=100, max_watts=75, min_free_bytes=1000, **kwargs)
        guard.start()
        return guard

    def telemetry(self, **kwargs):
        return Telemetry(**dict(dict(observed_at=10, gpu_c=60, watts=50, free_bytes=2000,
                                     board_c=70, on_battery=False), **kwargs))

    def test_stale_missing_hot_overpower_memory(self):
        guard = self.guard(gb10=True)
        self.assertIsNone(guard.reason(10, self.telemetry()))
        for t in (None, self.telemetry(observed_at=7), self.telemetry(watts=None),
                  self.telemetry(watts="unavailable"), self.telemetry(watts=True),
                  self.telemetry(watts=float("nan")), self.telemetry(gpu_c=83),
                  self.telemetry(watts=75), self.telemetry(free_bytes=500),
                  self.telemetry(board_c=None), self.telemetry(board_c=95)):
            self.assertIsNotNone(guard.reason(10, t))

    def test_laptop_needs_confirmed_ac(self):
        for ac in (None, True):
            guard = self.guard(laptop=True)
            self.assertIsNotNone(guard.reason(10, self.telemetry(on_battery=ac)))
            self.assertTrue(guard.stopped)
            with self.assertRaises(ValueError):
                guard.start()
        self.assertIsNone(self.guard(laptop=True).reason(10, self.telemetry()))

    def test_day_work_deadline_latches_stop(self):
        guard = self.guard()
        self.assertEqual(guard.reason(100, self.telemetry(observed_at=100)), "stop deadline")
        with self.assertRaises(ValueError):
            guard.start()
        self.assertEqual(guard.reason(10, self.telemetry()), "stopped")

    def test_restart_is_stopped_and_gb10_ceiling_cannot_be_raised(self):
        guard = Guard(stop_at=100, max_watts=75, min_free_bytes=1000)
        self.assertEqual(guard.reason(10, self.telemetry()), "stopped")
        with self.assertRaises(ValueError):
            Guard(stop_at=100, max_watts=90, min_free_bytes=1000, gb10=True)


if __name__ == "__main__":
    unittest.main()
