#!/usr/bin/env python3
"""Records the exact fast-worker build the panel is allowed to start."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent
binary=ROOT/"tma_worker/target/release/pearlmine-tma-worker.exe"
nvcc=Path(sys.argv[1])/"bin/nvcc.exe"
receipt=dict(binary_sha256=sha256(binary.read_bytes()).hexdigest(),
    cudart_sha256=sha256((binary.parent/"cudart64_13.dll").read_bytes()).hexdigest(),
    manifest="tma_worker/Cargo.toml",arch="sm_120a",
    rustc=subprocess.check_output(["rustc","--version"],text=True).strip(),
    nvcc=subprocess.check_output([str(nvcc),"--version"],text=True).strip().splitlines()[-1],
    scope="persistent TMA kernel, RTX 5090 class (compute capability 12.0)")
path=ROOT/"docs/boris/evidence/worker/tma-worker-build.json"
path.write_text(json.dumps(receipt,indent=2)+"\n")
print(receipt["binary_sha256"])
