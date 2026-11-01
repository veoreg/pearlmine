# SPDX-License-Identifier: Apache-2.0
"""Static policy and temporary-file fixtures; none executes a GPU workload."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from boris_pearl.devices import parse_devices,select_device
from boris_pearl.gb10_preflight import collect,read_meminfo,read_thermal_zones
from boris_pearl.load_profiles import resolve_profile
from boris_pearl.safety import Guard,Telemetry
from tests_portable.test_devices import row,U1


class Gb10PreparationTests(unittest.TestCase):
    def test_gb10_build_rejects_5090_and_accepts_121_isa_only(self):
        devices=parse_devices(dict(devices=[row(0,U1,(12,0),"gb10")]))
        self.assertEqual(select_device(devices,0).eligible_backends,())
        d=parse_devices(dict(devices=[row(0,U1,(12,1),"gb10")]))[0]
        self.assertEqual(d.eligible_backends,("dp4a","wmma-int8","mma-int8"))
        self.assertIn("no physical",d.public()["qualification"])

    def test_121_oem_name_cannot_remove_board_power_guards(self):
        for name in ("OEM product","GB10"):
            d=parse_devices(dict(devices=[row(0,U1,(12,1),name=name)]))[0]
            self.assertTrue(d.requires_gb10_guards)
        self.assertFalse(parse_devices(dict(devices=[row(0,U1,(12,0))]))[0].requires_gb10_guards)

    def test_local_gb10_missing_board_stops_before_compute_worker(self):
        from boris_pearl.local_search import LocalSearch
        from boris_pearl.telemetry import SensorError
        from types import SimpleNamespace
        d=parse_devices(dict(devices=[row(0,U1,(12,1),name="OEM alias")]))[0]
        monitor=SimpleNamespace(start=lambda:None,updated=SimpleNamespace(wait=lambda t:True),
            details=lambda:dict(device=dict(uuid=U1,name="OEM alias",index=0)),
            sample=lambda:Telemetry(10,30,10,2<<30,power_limit_watts=150,on_battery=False),close=lambda:None)
        with tempfile.TemporaryDirectory() as tmp:
            receipt=Path(tmp)/"receipt.json";receipt.write_text(json.dumps(dict(binary_sha256="a"*64)))
            with patch("boris_pearl.local_search.discover",return_value=(d,)),patch("boris_pearl.local_search.TelemetryMonitor",return_value=monitor),patch("boris_pearl.local_search.CudaWorker") as worker:
                session=LocalSearch(receipt=receipt)
                with self.assertRaisesRegex(SensorError,"board/OEM"):session.start()
                worker.assert_not_called()

    def test_shared_memory_diagnostic_counts_no_swap_as_gpu_headroom(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"meminfo";p.write_text("MemTotal: 64000000 kB\nMemAvailable: 1000 kB\nSwapFree: 9000000 kB\n")
            info=read_meminfo(p);self.assertEqual(info["available_bytes"],1024000)
            for invalid in ("MemTotal: 1 kB\nMemAvailable: 2 kB\n","MemTotal: 1 kB\nMemAvailable: x kB\n","MemTotal: 1 kB\nMemAvailable: 1 kB\nMemAvailable: 1 kB\n"):
                p.write_text(invalid)
                with self.assertRaises(ValueError):read_meminfo(p)

    def test_oem_zones_remain_unqualified_and_errors_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for n,kind,value in ((0,"acpitz","48000"),(1,"gpu","bad"),(2,"other","999999")):
                z=root/f"thermal_zone{n}";z.mkdir();(z/"type").write_text(kind);(z/"temp").write_text(value)
            zones=read_thermal_zones(root)
            self.assertEqual(zones[0]["celsius"],48)
            self.assertTrue(all(not x["qualified_as_board_sensor"] for x in zones))
            self.assertTrue(all("error" in x for x in zones[1:]))

    def test_non_arm_preflight_does_not_probe_or_start_nvml(self):
        with patch("boris_pearl.gb10_preflight.platform.system",return_value="Windows"),patch("boris_pearl.gb10_preflight.platform.machine",return_value="AMD64"),patch("boris_pearl.gb10_preflight.command_probe") as command,patch("boris_pearl.gb10_preflight.TelemetryMonitor") as monitor:
            report=collect();self.assertFalse(report["launch_authorized"])
            self.assertTrue(report["blockers"]);command.assert_not_called();monitor.assert_not_called()

    def test_arm_fixture_redacts_identity_and_never_qualifies_raw_sensors(self):
        from dataclasses import asdict
        from types import SimpleNamespace
        sample=Telemetry(10,30,40,None,power_limit_watts=None)
        monitor=SimpleNamespace(start=lambda:None,updated=SimpleNamespace(wait=lambda t:True),
            details=lambda:dict(device=dict(uuid=U1,name="GB10",index=0),compute_pids=[123],sample=asdict(sample),errors=[dict(sensor="memory",nvml_code=3)]),
            sample=lambda:sample,close=lambda:None)
        with patch("boris_pearl.gb10_preflight.platform.system",return_value="Linux"),patch("boris_pearl.gb10_preflight.platform.machine",return_value="aarch64"),patch("boris_pearl.gb10_preflight.read_meminfo",return_value=dict(available_bytes=100000000)),patch("boris_pearl.gb10_preflight.read_thermal_zones",return_value=[dict(type="acpitz",celsius=45,qualified_as_board_sensor=False)]),patch("boris_pearl.gb10_preflight.command_probe",return_value=dict(status="fixture")),patch("boris_pearl.gb10_preflight.TelemetryMonitor",return_value=monitor):
            report=collect(U1)
        self.assertNotIn(U1,json.dumps(report));self.assertNotIn("compute_pids",report["nvml"])
        self.assertIsNone(report["nvml"]["sample"]["free_bytes"])
        self.assertIsNone(report["nvml"]["sample"]["board_c"])
        self.assertFalse(report["gb10_runtime_qualified"]);self.assertFalse(report["launch_authorized"])

    def test_board_power_memory_and_deadline_latch_stop(self):
        normal=Telemetry(10,30,40,2<<30,board_c=50,on_battery=False)
        for bad in (replace(normal,board_c=None),replace(normal,board_c=95),replace(normal,watts=85),replace(normal,free_bytes=None),replace(normal,free_bytes=100),replace(normal,observed_at=5)):
            guard=Guard(stop_at=20,max_watts=85,min_free_bytes=1<<30,gb10=True)
            guard.start();self.assertIsNotNone(guard.reason(10,bad));self.assertIsNotNone(guard.reason(10,normal))
        g=Guard(stop_at=10,max_watts=85,min_free_bytes=1<<30,gb10=True);g.start();self.assertEqual(g.reason(10,normal),"stop deadline")

    def test_no_power_limit_guess_or_85w_override(self):
        for limit in (None,0,float("nan")):
            with self.assertRaises(ValueError):resolve_profile("standard",None,None,True,limit,gb10=True)
        with self.assertRaises(ValueError):resolve_profile("standard",None,86,True,200,gb10=True)
        self.assertEqual(resolve_profile("standard",None,None,True,200,gb10=True).max_watts,85)
