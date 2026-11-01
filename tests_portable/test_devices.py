# SPDX-License-Identifier: Apache-2.0
"""No hardware emulation: pure selection/ISA policy and laptop safety fixtures only."""
from dataclasses import asdict
import unittest
from boris_pearl.devices import CudaDevice,DeviceSelectionError,parse_devices,select_device,PROFILES
from boris_pearl.load_profiles import validate_request,resolve_profile
from boris_pearl.safety import Guard,Telemetry


def row(index,uuid,cc,profile="portable",name="fixture"):
    floor,mask=PROFILES[profile]
    return dict(cuda_index=index,name=name,uuid=uuid,pci_bus_id=f"0000:0{index+1}:00.0",
        cc_major=cc[0],cc_minor=cc[1],memory_bytes=6<<30,build_profile=profile,
        build_min_cc=floor,build_backend_mask=mask)

U1="GPU-11111111-1111-1111-1111-111111111111"
U2="GPU-22222222-2222-2222-2222-222222222222"


class SelectionTests(unittest.TestCase):
    def test_2060_and_4090_isa_intersect_actual_build_profile(self):
        for profile in PROFILES:
            devices=parse_devices(dict(devices=[row(0,U1,(7,5),profile),row(1,U2,(8,9),profile)]))
            expected0=() if profile in ("ada89","gb10") else ("dp4a","wmma-int8")
            expected1=() if profile=="gb10" else (("dp4a","wmma-int8") if profile=="turing75" else ("dp4a","wmma-int8","mma-int8"))
            self.assertEqual(devices[0].eligible_backends,expected0)
            self.assertEqual(devices[1].eligible_backends,expected1)
            with self.assertRaises(DeviceSelectionError):devices[0].require_backend("mma-int8")
        low=parse_devices(dict(devices=[row(0,U1,(7,0))]))[0]
        self.assertEqual(low.eligible_backends,())

    def test_uuid_survives_reordered_ordinals_without_name_guessing(self):
        first=parse_devices(dict(devices=[row(0,U1,(7,5),name="same name"),row(1,U2,(8,9),name="same name")]))
        changed=parse_devices(dict(devices=[row(1,U1,(7,5),name="same name"),row(0,U2,(8,9),name="same name")]))
        self.assertEqual(select_device(first,U1).cuda_index,0)
        self.assertEqual(select_device(changed,U1.lower()).cuda_index,1)
        self.assertEqual(select_device(changed,"0").uuid,U2)

    def test_missing_device_empty_mask_and_bad_selector_never_choose_gpu0(self):
        devices=parse_devices(dict(devices=[row(0,U1,(7,5))]))
        for selector in (1,32,-1,True,None,"","GPU-1111","fixture","0.0","-1"," 0",U2):
            with self.subTest(selector=selector),self.assertRaises(DeviceSelectionError):select_device(devices,selector)
        with self.assertRaises(DeviceSelectionError):select_device((),0)

    def test_malformed_duplicate_or_forged_build_descriptors_rejected(self):
        base=row(0,U1,(7,5))
        bads=[dict(base,cuda_index=True),dict(base,cc_minor=99),dict(base,uuid="MIG-1"),
            dict(base,memory_bytes=0),dict(base,pci_bus_id="bad"),dict(base,build_backend_mask=7,build_profile="turing75")]
        for bad in bads:
            with self.assertRaises(DeviceSelectionError):parse_devices(dict(devices=[bad]))
        for rows in ([base,base],[base,dict(base,cuda_index=1)]):
            with self.assertRaises(DeviceSelectionError):parse_devices(dict(devices=rows))


class LaptopProfileTests(unittest.TestCase):
    def test_lower_profile_uses_observed_limit_without_writing_hardware(self):
        a=resolve_profile("laptop",None,None,True,80)
        self.assertEqual((a.duty,a.max_watts,a.max_gpu_c,a.require_ac),(.1,50,70,True))
        self.assertEqual(resolve_profile("laptop",None,None,True,40).max_watts,32)
        self.assertEqual(resolve_profile("standard",None,None,True,80).max_watts,64)

    def test_laptop_cannot_disable_ac_or_raise_duty_and_reviewed_threshold(self):
        for values in (("laptop",.2,None,True),("laptop",.1,51,True),("laptop",.1,50,False)):
            with self.assertRaises(ValueError):validate_request(*values)
        for limit in (None,0,float("nan"),True):
            with self.assertRaises(ValueError):resolve_profile("laptop",None,None,True,limit)
        with self.assertRaises(ValueError):resolve_profile("standard",.2,125,True,80)

    def test_actual_guard_latches_ac_thermal_power_and_memory_faults(self):
        p=resolve_profile("laptop",None,None,True,80)
        for sample in [Telemetry(10,60,30,2<<30,on_battery=True),
            Telemetry(10,60,30,2<<30,on_battery=None),Telemetry(10,70,30,2<<30,on_battery=False),
            Telemetry(10,60,50,2<<30,on_battery=False),Telemetry(10,60,30,1,on_battery=False)]:
            guard=Guard(stop_at=20,max_watts=p.max_watts,min_free_bytes=1<<30,laptop=True,max_gpu_c=p.max_gpu_c)
            guard.start();self.assertIsNotNone(guard.reason(10,sample));self.assertTrue(guard.stopped)

class UuidBindingTests(unittest.TestCase):
    def test_local_preflight_passes_uuid_and_rejects_foreign_sensor_before_gpu_work(self):
        from unittest.mock import patch
        from boris_pearl.local_search import LocalSearch
        from boris_pearl.telemetry import SensorError
        from types import SimpleNamespace
        chosen=parse_devices(dict(devices=[row(1,U1,(7,5))]))[0]
        bad=SimpleNamespace(start=lambda:None,updated=SimpleNamespace(wait=lambda t:True),
            details=lambda:dict(device=dict(uuid=U2,name="fixture",index=0)),
            sample=lambda:Telemetry(10,30,10,2<<30,power_limit_watts=80,on_battery=False),close=lambda:None)
        import tempfile,json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            receipt=Path(directory)/"receipt.json";receipt.write_text(json.dumps({"binary_sha256":"a"*64}))
            with patch("boris_pearl.local_search.discover",return_value=(chosen,)),patch("boris_pearl.local_search.TelemetryMonitor",return_value=bad) as monitor,patch("boris_pearl.local_search.CudaWorker") as gpu:
                session=LocalSearch(seconds=5,device_index=U1,receipt=receipt)
                with self.assertRaises(SensorError):session.start()
                monitor.assert_called_once_with(device_uuid=U1)
                gpu.assert_not_called()
                session.close()
