import math
from pathlib import Path
import sys
import tempfile
import time
import unittest

from boris_pearl.load import DutyLimiter
from boris_pearl.safety import Guard,Telemetry
from boris_pearl.telemetry import TelemetryMonitor,ac_state


class LoadTests(unittest.TestCase):
    def test_duty_limit_uses_work_wall_time_and_waits_in_cancellable_slices(self):
        now=[0.0]
        waits=[]
        def wait(n):
            waits.append(n)
            now[0]+=n
        limiter=DutyLimiter(.2,clock=lambda:now[0],wait=wait)
        now[0]=.1
        limiter.record(0)
        self.assertIsNone(limiter.ready(lambda:None))
        self.assertAlmostEqual(now[0],.5)
        self.assertTrue(all(n<=.02 for n in waits))
        self.assertAlmostEqual(limiter.busy_seconds,.1)
        self.assertAlmostEqual(limiter.waited_seconds,.4)

    def test_duty_wait_cancels_without_waiting_for_cooldown(self):
        now=[0.0]
        limiter=DutyLimiter(.1,clock=lambda:now[0],wait=lambda n:now.__setitem__(0,now[0]+n))
        now[0]=1
        limiter.record(0)
        self.assertEqual(limiter.ready(lambda:"manual stop"),"manual stop")
        self.assertEqual(now[0],1)

    def test_invalid_duty_or_relaxed_thermal_ceiling_rejected(self):
        for value in (True,0,-1,1.1,float("nan"),float("inf")):
            with self.assertRaises(ValueError):
                DutyLimiter(value)
        for value in (81,float("nan"),True,20):
            with self.assertRaises(ValueError):
                Guard(stop_at=10,max_watts=75,min_free_bytes=1000,max_gpu_c=value)

    def test_busy_unknown_util_and_lower_temperature_pause(self):
        guard=Guard(stop_at=100,max_watts=75,min_free_bytes=1000,max_gpu_c=70,max_gpu_util=80)
        guard.start()
        for util in (None,float("nan"),80,101,-1):
            self.assertEqual(guard.reason(10,Telemetry(10,60,50,2000,gpu_util_percent=util)),"GPU busy or utilization unavailable")
        self.assertEqual(guard.reason(10,Telemetry(10,70,50,2000,gpu_util_percent=20)),"GPU temperature")
        self.assertEqual(guard.reason(10,Telemetry(10,60,50,2000,gpu_util_percent=20)),"stopped")
        self.assertTrue(guard.stopped)

    def test_linux_ac_is_confirmed_or_unknown_without_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.assertIsNone(ac_state("linux",root))
            ac=root/"AC";ac.mkdir()
            (ac/"type").write_text("Mains")
            (ac/"online").write_text("1")
            self.assertIs(ac_state("linux",root),False)
            (ac/"online").write_text("0")
            self.assertIs(ac_state("linux",root),True)
            (ac/"online").write_text("unknown")
            self.assertIsNone(ac_state("linux",root))


class SensorProcessTests(unittest.TestCase):
    def monitor(self,mode):
        m=TelemetryMonitor(interval=.1,timeout=.25,argv=[sys.executable,str(Path(__file__).with_name("fault_sensor.py")),mode])
        self.addCleanup(m.close)
        m.start()
        self.assertTrue(m.updated.wait(2))
        return m

    def test_slow_crashed_and_malformed_sensors_fail_closed_and_reap(self):
        for mode in ("hang","crash","malformed"):
            m=self.monitor(mode)
            m.close()
            self.assertIsNone(m.sample())
            self.assertIsNotNone(m.last_error)
            self.assertIsNotNone(m.worker.process.poll())

    def test_identity_change_retires_last_reading(self):
        m=self.monitor("identity-change")
        self.assertIsNotNone(m.sample())
        end=time.monotonic()+2
        while m.last_error is None and time.monotonic()<end:
            time.sleep(.01)
        self.assertIn("device changed",m.last_error)
        self.assertIsNone(m.sample())

    def test_samples_are_nonblocking_copy_safe_and_age_by_monotonic_clock(self):
        m=self.monitor("ok")
        start=time.monotonic()
        self.assertIsNotNone(m.sample())
        self.assertLess(time.monotonic()-start,.01)
        details=m.details();details["device"].clear()
        self.assertIn("uuid",m.details()["device"])
        with m.lock:
            m.received_at=time.monotonic()-3
        self.assertIsNone(m.sample())
        m.close()
        self.assertIsNone(m.sample())
        self.assertIsNotNone(m.worker.process.poll())
        with self.assertRaises(ValueError):
            m.start()
