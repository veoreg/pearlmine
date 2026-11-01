# SPDX-License-Identifier: Apache-2.0
"""Real read-only CUDA/NVML join and visibility masks; physical RTX5090 only."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from boris_pearl.devices import discover,select_device,DeviceSelectionError
from boris_pearl.local_search import LocalSearch
from boris_pearl.telemetry import TelemetryMonitor

ROOT=Path(__file__).resolve().parent.parent
RECEIPT=ROOT/"docs/boris/evidence/worker/worker-build.json"
BINARY=ROOT/"production_worker/target/release"/("boris-pearl-worker.exe" if sys.platform=="win32" else "boris-pearl-worker")


@unittest.skipUnless(BINARY.is_file() and RECEIPT.is_file(),"Reviewed worker build required")
class PhysicalSelectionTests(unittest.TestCase):
    def test_cuda_selection_and_nvml_uuid_join(self):
        devices=discover()
        selected=select_device(devices,0)
        self.assertEqual(select_device(devices,selected.uuid),selected)
        monitor=TelemetryMonitor(device_uuid=selected.uuid)
        self.addCleanup(monitor.close);monitor.start();monitor.updated.wait(3)
        details=monitor.details();self.assertIsNotNone(details,monitor.last_error)
        self.assertEqual(details["device"]["uuid"].lower(),selected.uuid.lower())
        out=ROOT/"docs/boris/evidence/architectures/device-selection.json"
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(dict(status="passed",scope="actual single5090 CUDA/NVML UUID join; no kernels/pool",
            devices=[d.public() for d in devices],selected=selected.public(),nvml_index=details["device"]["index"],
            index_reordering_test="separate synthetic fixtures, not physical multi-GPU"),indent=2)+"\n")

    def test_scoped_cuda_visibility_mask_hides_devices_without_fallback(self):
        selected=select_device(discover(),0)
        digest=json.loads(RECEIPT.read_text())["binary_sha256"]
        scoped=dict(os.environ,CUDA_VISIBLE_DEVICES=selected.uuid)
        rows=discover(BINARY,digest,env=scoped)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0].cuda_index,0)
        self.assertEqual(select_device(rows,selected.uuid).uuid,selected.uuid)
        empty=discover(BINARY,digest,env=dict(os.environ,CUDA_VISIBLE_DEVICES="-1"))
        self.assertEqual(empty,())
        with self.assertRaises(DeviceSelectionError):select_device(empty,0)

    def test_invisible_explicit_device_never_creates_gpu_worker_or_sensor(self):
        devices=discover();missing=next((i for i in range(32) if i not in {d.cuda_index for d in devices}),None)
        if missing is None:self.fail("No unavailable CUDA index to exercise; record prerequisite rather than skip")
        session=LocalSearch(seconds=5,device_index=missing)
        self.addCleanup(session.close)
        with patch("boris_pearl.local_search.CudaWorker") as gpu,patch("boris_pearl.local_search.TelemetryMonitor") as sensor:
            with self.assertRaises(DeviceSelectionError):session.start()
            gpu.assert_not_called();sensor.assert_not_called()
        self.assertIsNone(session.worker)
