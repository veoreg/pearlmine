# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Windows x64 folder build: everything sits next to PearlMine.exe, so ROOT and state/ stay beside the program.
# Requires an already built worker and verifier with their receipts; state/ and pairing codes are never collected.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root=Path(SPECPATH).parent
data=[(str(root/source),target) for source,target in (
    ("production_worker/target/release/boris-pearl-worker.exe","production_worker/target/release"),
    ("tma_worker/target/release/pearlmine-tma-worker.exe","tma_worker/target/release"),
    ("tma_worker/target/release/cudart64_13.dll","tma_worker/target/release"),
    ("docs/boris/evidence/worker/tma-worker-build.json","docs/boris/evidence/worker"),
    ("proof_bridge/target/release/boris-official-proof-check.exe","proof_bridge/target/release"),
    ("docs/boris/evidence/worker/worker-build.json","docs/boris/evidence/worker"),
    ("docs/boris/evidence/proof-lab/official-build.json","docs/boris/evidence/proof-lab"),
    ("native/opencl/fixed_tile.cl","native/opencl"),
    ("LICENSE","."),("NOTICE","."),
)]
a=Analysis([str(root/"packaging/pearlmine_entry.py")],pathex=[str(root)],datas=data,
    hiddenimports=collect_submodules("boris_pearl"),excludes=["PIL","numpy","pytest"])
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[("X utf8",None,"OPTION")],exclude_binaries=True,name="PearlMine",console=False,contents_directory=".",icon=str(root/"packaging/pearlmine.ico"))
COLLECT(exe,a.binaries,a.datas,name="PearlMine")
