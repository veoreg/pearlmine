# SPDX-License-Identifier: Apache-2.0
"""Read-only OpenCL inventory. No compiler, context, buffers, kernels or NVML."""
import json
import platform
import time
from .opencl_validation import OpenClValidation


def main():
    session=OpenClValidation(seconds=10)
    try:
        devices=session.inventory()
        print(json.dumps(dict(time_utc_epoch=time.time(),system=platform.system(),machine=platform.machine(),
            devices=[dict(d.public(),uuid_available=d.uuid is not None) for d in devices],
            amd_detected=any(d.vendor_id==0x1002 for d in devices),gpu_kernels_launched=0,
            runtime_qualification="unverified; inventory never authorizes workloads",mining_enabled=False),indent=2))
    finally:session.close()
    return 0


if __name__=="__main__":raise SystemExit(main())
