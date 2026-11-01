# SPDX-License-Identifier: Apache-2.0
"""Construction and policy only: no GPU, sockets, pool or worker process."""
from types import SimpleNamespace
import unittest

from boris_pearl.config import Config
from boris_pearl.live import LiveMining,pool_dialect
from boris_pearl.pool import PoolError
from boris_pearl.runtime import PoolCoordinator
from tests_portable.test_core import fixture_address


class LivePolicyTests(unittest.TestCase):
    def config(self):return Config(fixture_address(3),"stratum+ssl://prl.kryptex.network:8048")
    def test_dialect_only_for_the_exact_kryptex_domain(self):
        self.assertEqual(pool_dialect("stratum+ssl://prl-sg.kryptex.network:8048"),"kryptex-v2")
        self.assertEqual(pool_dialect("stratum+ssl://kryptex.network.example.com:8048"),"object")
    def test_needs_saved_destination_and_bounded_explicit_options(self):
        with self.assertRaisesRegex(ValueError,"кошелёк"):LiveMining(Config(),seconds=60,duty=1,device_index="0")
        for options in (dict(seconds=60,duty=.3),dict(seconds=8*86400,duty=1),dict(seconds=60,duty=1,profile="laptop")):
            with self.assertRaises(ValueError):LiveMining(self.config(),device_index="0",**options)
    def test_constructing_opens_nothing(self):
        session=LiveMining(self.config(),seconds=60,duty=1,device_index="0")
        self.assertIsNone(session.worker);self.assertIsNone(session.user_client);self.assertIsNone(session.coordinator)
        session.close();self.assertIsNone(session.begun_at)
    def test_share_for_a_retired_job_is_not_sent_and_does_not_stop_mining(self):
        config=self.config();router=object()
        class Client:
            lock=__import__("threading").Lock();authorized=True;current=None;closed=SimpleNamespace(is_set=lambda:False)
            template=SimpleNamespace(owner="user")
            def __init__(self):self.config,self.router=config,router
            def submit(self,job):raise PoolError("Cannot submit a stale/foreign job")
            def close(self):pass
        controller=SimpleNamespace(config=config,router=router,worker=None,stopped=SimpleNamespace(is_set=lambda:False),
            tick=lambda user,developer:dict(state="offline-result",owner="user",verified_proofs=1),_permit=lambda:None,stop=lambda:None)
        coordinator=PoolCoordinator(controller,Client());coordinator.started=True;coordinator._job=lambda owner:object()
        result=coordinator.tick(submit=True)
        self.assertEqual(result["pool_result"],"stale");self.assertFalse(result["submitted"]);self.assertTrue(coordinator.started)
