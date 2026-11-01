# SPDX-License-Identifier: Apache-2.0
"""Synthetic concurrent devices; never GPU/NVML, real mining or external endpoints."""
import threading
import time
import unittest
from types import SimpleNamespace
from boris_pearl.control_center import DeviceService
from boris_pearl.devices import parse_devices
from tests_portable.test_devices import row,U1,U2


def devices():
    return parse_devices(dict(devices=[row(0,U1,(8,9),name="4090 fixture"),row(1,U2,(7,5),name="2060 Laptop fixture")]))


def wait_for(condition,timeout=2):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if condition():return
        time.sleep(.005)
    raise AssertionError("Owned synthetic work did not reach expected state")


class FakeSession:
    def __init__(self,**kwargs):
        self.options=kwargs;self.ticks=0;self.closed=False;self.stats={}
        self.controller=SimpleNamespace(stopped=threading.Event())
        self.controller.stop=lambda reason:self.controller.stopped.set()
        self.monitor=SimpleNamespace(sample=lambda:SimpleNamespace(gpu_c=30,watts=20,free_bytes=2<<30))
    def start(self):return self
    def tick(self):
        self.ticks+=1;self.stats={"total_tiles":self.ticks}
        self.controller.stopped.wait(.005)
        return dict(state="offline-result")
    def stop_work(self,reason):self.controller.stop(reason)
    def report(self):return dict(stop={"worker_reaped":True},sensors=["excluded"])
    def close(self,reason):self.closed=True;self.controller.stop(reason)


class DeviceControlTests(unittest.TestCase):
    def setUp(self):
        self.sessions=[]
        def factory(**kwargs):
            session=FakeSession(**kwargs);self.sessions.append(session);return session
        self.service=DeviceService(inventory_provider=devices,session_factory=factory,probe_sensors=False)
        self.addCleanup(self.finish)
        self.service.refresh();wait_for(lambda:not self.service.snapshot()["refreshing"])
    def finish(self):
        self.service.close()
        wait_for(lambda:not self.service.alive())
    def test_construction_refresh_and_settings_never_start_work(self):
        self.assertEqual(self.sessions,[])
        self.assertEqual(len(self.service.snapshot()["devices"]),2)
        self.assertFalse(self.service.snapshot()["mining_enabled"])
    def test_two_devices_stop_independently_then_close_reaps_both(self):
        self.service.start([U1,U2],seconds=5)
        wait_for(lambda:len(self.sessions)==2 and all(s.ticks for s in self.sessions))
        self.service.stop([U1])
        wait_for(lambda:self.sessions[0].closed)
        self.assertFalse(self.sessions[1].controller.stopped.is_set())
        self.service.stop();wait_for(lambda:all(s.closed for s in self.sessions))
        self.assertEqual({s.options["device_index"] for s in self.sessions},{U1,U2})
    def test_atomic_validation_prevents_partial_start_and_duplicate_work(self):
        with self.assertRaises(ValueError):self.service.start([U1,U2],seconds=5,backend="mma-int8")
        self.assertEqual(self.sessions,[])
        with self.assertRaises(ValueError):self.service.start([U1,U1],seconds=5)
        self.service.start([U1],seconds=5);wait_for(lambda:bool(self.sessions))
        with self.assertRaises(ValueError):self.service.start([U1],seconds=5)
    def test_laptop_boundaries_preserved(self):
        with self.assertRaises(ValueError):self.service.start([U2],seconds=5,profile="laptop",duty=.2)
        self.service.start([U2],seconds=5,profile="laptop",duty=.1,backend="wmma-int8")
        wait_for(lambda:bool(self.sessions))
        self.assertEqual(self.sessions[0].options["profile"],"laptop")
        self.assertEqual(self.sessions[0].options["duty"],.1)
    def test_cancel_during_startup_never_ticks(self):
        entered=threading.Event();release=threading.Event()
        class Slow(FakeSession):
            def start(self):entered.set();release.wait(1);return self
        def factory(**kwargs):
            session=Slow(**kwargs);self.sessions.append(session);return session
        self.service.session_factory=factory;self.service.start([U1],seconds=5)
        self.assertTrue(entered.wait(1));self.service.stop();release.set()
        wait_for(lambda:self.sessions[0].closed)
        self.assertEqual(self.sessions[0].ticks,0)
    def test_second_window_cannot_load_the_same_card_until_the_first_finishes(self):
        other=DeviceService(inventory_provider=devices,session_factory=FakeSession,probe_sensors=False)
        self.addCleanup(lambda:(other.close(),wait_for(lambda:not other.alive())))
        other.refresh();wait_for(lambda:not other.snapshot()["refreshing"])
        self.service.start([U1],seconds=5);wait_for(lambda:bool(self.sessions))
        with self.assertRaisesRegex(ValueError,"другом окне"):other.start([U2,U1],seconds=5)
        self.assertEqual(other.snapshot()["active"],0)
        other.start([U2],seconds=5);other.stop();wait_for(lambda:other.snapshot()["active"]==0)
        self.service.stop();wait_for(lambda:self.service.snapshot()["active"]==0)
        other.start([U1],seconds=5);other.stop();wait_for(lambda:other.snapshot()["active"]==0)
    def test_snapshot_and_export_are_detached_and_redact_identity(self):
        snapshot=self.service.snapshot();snapshot["devices"][U1]["name"]="mutated"
        self.assertNotEqual(self.service.snapshot()["devices"][U1]["name"],"mutated")
        self.service.log("device "+U1)
        report=self.service.export()
        self.assertIsInstance(report["devices"],list)
        self.assertNotIn(U1,str(report))
