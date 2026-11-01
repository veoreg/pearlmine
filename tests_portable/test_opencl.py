# SPDX-License-Identifier: Apache-2.0
"""Pure portable policy/arithmetic fixtures. No OpenCL runtime or GPU loaded."""
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from boris_pearl.opencl_lab import OpenClDevice,OpenClError,choose,exact_uuid,validate_command
from boris_pearl.opencl_fixtures import arithmetic_case,golden_rows,reference_tile
from boris_pearl.opencl_validation import OpenClValidation,checked_result
from boris_pearl.proof_lab import fixture
from boris_pearl.ipc import WorkerError

U="11111111-1111-1111-1111-111111111111"
D=OpenClDevice(0,0,"fixture","vendor",0x10DE,"driver","OpenCL3.0","OpenCL C1.2",U,True,True,True,256,32768,2<<30)
# Real API text contains a space between C and version.
D=replace(D,opencl_c="OpenCL C 1.2")


class OpenClPolicyTests(unittest.TestCase):
    def test_uuid_requires_complete_physical_identity(self):
        self.assertEqual(exact_uuid(U.upper()),U)
        for x in (None,0,"0","GPU-"+U,U[:8],"00000000-0000-0000-0000-000000000000"):
            with self.assertRaises(ValueError):exact_uuid(x)

    def test_ambiguous_uuid_and_missing_device_never_fall_back(self):
        self.assertEqual(choose((D,),U),D)
        for ds in ((),(replace(D,uuid=None),),(D,replace(D,platform_index=1))):
            with self.assertRaises(OpenClError):choose(ds,U)

    def test_missing_capabilities_refuse_instead_of_guessing(self):
        for change in (dict(uuid=None),dict(compiler_available=False),dict(available=False),dict(little_endian=False),dict(max_group_size=64),dict(local_bytes=256),dict(opencl_c="OpenCL C 1.1")):
            with self.assertRaises(OpenClError):replace(D,**change).require_lab_eligible()

    def test_amd_requires_its_own_reviewed_telemetry_before_compilation_session(self):
        amd=replace(D,vendor_id=0x1002,name="AMD fixture")
        amd.require_lab_eligible()  # ISA/API eligibility only, never physical qualification
        with patch("boris_pearl.opencl_validation.TelemetryMonitor") as monitor:
            session=OpenClValidation(seconds=5)
            try:
                with self.assertRaisesRegex(OpenClError,"telemetry"):session.compile_nvidia((amd,),U)
                monitor.assert_not_called();self.assertIsNone(session.worker.process)
            finally:session.close()

    def test_gb10_opencl_cannot_bypass_oem_safety_with_another_name(self):
        from types import SimpleNamespace
        from boris_pearl.telemetry import SensorError
        with patch("boris_pearl.opencl_validation.discover",return_value=()),patch("boris_pearl.opencl_validation.select_device",return_value=SimpleNamespace(requires_gb10_guards=True)),patch("boris_pearl.opencl_validation.TelemetryMonitor") as monitor:
            session=OpenClValidation(seconds=5)
            try:
                with self.assertRaisesRegex(SensorError,"GB10"):session.compile_nvidia((D,),U)
                monitor.assert_not_called();self.assertIsNone(session.worker.process)
            finally:session.close()

    def test_protocol_rejects_live_or_extra_input(self):
        for cmd,p in (("mine",{}),("prepare",dict(header76="00"*76)),("case",dict(case_id=0,wallet="x")),("case",dict(case_id=True)),("case",dict(case_id=15)),("golden",dict(seed=4,tile_index=0)),("golden",dict(seed=1,tile_index=-1)),("compile",dict(uuid=U,source_sha256="a"*63))):
            with self.assertRaises((OpenClError,ValueError)):validate_command(cmd,p)
        validate_command("inventory",{});validate_command("case",dict(case_id=0))

    def test_malformed_tampered_or_unbounded_results_rejected(self):
        expected=dict(transcript=[0]*16,digest="a"*64)
        actual=dict(**expected,active_ns=100,k=2048,launches=1)
        self.assertEqual(checked_result(actual,expected,2048),actual)
        for change in (dict(active_ns=0),dict(active_ns=True),dict(active_ns=1_000_000_001),dict(k=4096),dict(launches=33),dict(transcript=[False]*16),dict(digest="b"*64)):
            with self.assertRaises(WorkerError):checked_result(dict(actual,**change),expected,2048)


class OpenClReferenceTests(unittest.TestCase):
    def test_fixed_case_generation_bounds_and_integer_shapes(self):
        for x in (-1,15,True,None):
            with self.assertRaises(ValueError):arithmetic_case(x)
        for x in (0,5,10):
            a,b,k,key=arithmetic_case(x);self.assertEqual((len(a),len(b),len(key)),(8*k,16*k,32));self.assertFalse(any(a+b))

    def test_reference_matches_preserved_rust_cpu_cuda_cases(self):
        root=Path(__file__).resolve().parent.parent
        cases=json.loads((root/"docs/boris/evidence/tensor/signed-arithmetic-kat.json").read_text())["checks"]
        for case_id in range(15):
            a,b,k,key=arithmetic_case(case_id);result=reference_tile(a,b,k,key)
            expected=next(c for c in cases if c["backend"]=="dp4a" and c["k"]==k and c["pattern"]==case_id%5)
            self.assertEqual(result["digest"],expected["digest"])

    def test_noised_rows_transcript_hash_match_three_immutable_golden_fixtures(self):
        for seed in (1,2,3):
            result=reference_tile(*golden_rows(seed,0));tile=fixture(seed)["first_tiles"][0]
            self.assertEqual(result["transcript"],tile["transcript"]);self.assertEqual(result["digest"],tile["digest"])
        for bad in (-1,8,True):
            with self.assertRaises(ValueError):golden_rows(1,bad)
