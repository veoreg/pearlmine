# License review and provenance РІР‚вЂќ 2026-10-31

This is a scoped source review, not a completed binary/SBOM clearance for release.
No code or binaries from fee-restricted third-party miners were copied.

| Component | Revision / source | License evidence | Use in this prototype |
|---|---|---|---|
| Spark Pearl Miner | `65365633e3bff61b0f29d72109622dd22ff5a379` | Root Apache-2.0 `LICENSE`, authors and third-party `NOTICE` | Source retained for porting; no legacy miner binary built or launched |
| `cuda/common/blake3.cuh` + `defs.cuh` | Same pinned Spark tree | Apache-2.0 root; blake3 header retains full Pearl/Decred ISC notice | Included directly by the offline KAT; original headers unchanged |
| `cuda/prep/prep.cu`, `prep.cuh`, `cuda/common/splitmix.cuh`, golden fixture data | Same pinned Spark tree | Apache-2.0 root, original authors and embedded notices retained | Unmodified prep code linked into expanded offline KAT; generated golden header records canonical LF fixture hashes |
| Official Pearl verifier source | Exact `3fe226761a139a9652b8f28a6464a4bbc25986c8`, 351 files checked | Preserved root ISC, Pearl Research Labs and Decred notices; Plonky2 MIT/Apache files included | Unmodified Rust source built and official positive/negative proof tests run |
| NVIDIA CUTLASS C++ headers | gitlink `098de2a652cf8f00fd70b2df54051c7eccbb855a` | [Pinned BSD-3-Clause license](https://github.com/NVIDIA/cutlass/blob/098de2a652cf8f00fd70b2df54051c7eccbb855a/LICENSE.txt) | Submodule contents absent from checkout; not used by new KAT. Fetch and package notices before upstream build |
| CUTLASS Python/CuTeDSL | Not used | Pinned license separately identifies NVIDIA EULA for `python/CuTeDSL` | Do not treat the whole CUTLASS tree as uniformly BSD-3 |
| Python/Tkinter | Existing Python 3.11 / Tk 8.6.12 | Python/Tcl/Tk licenses belong to installed runtime | No Python/Tk runtime bundled; new Python package uses standard library only |
| NVIDIA CUDA toolkit/runtime | Existing CUDA 12.4 | NVIDIA CUDA SDK/runtime terms; not Apache-2.0 | Used for local offline test; no CUDA runtime or proprietary toolkit redistributed |
| BLAKE3 keyed length-64 test vector | [Official BLAKE3 public test data](https://github.com/BLAKE3-team/BLAKE3/blob/master/test_vectors/test_vectors.json) | Constant expected output and test input, no implementation copied | Independent host/device known-answer check in `native/kat.cu` |
| New Boris portable source, test and KAT code | This working tree | Apache-2.0, root LICENSE; changes identified in NOTICE/README | Source only |
| Optional Python blake3 | Existing installed 1.0.8 | Installed metadata: `CC0-1.0 OR Apache-2.0`; distribution LICENSE present | Independent offline proof root/hash checks; no package bytes bundled, no install/download |
| New proof_bridge | Official API pinned to Pearl commit 3fe2267 | Apache-2.0 wrapper; official ISC and subproject terms preserved | Windows x64 build and full official verification pass; compiled binaries not included in source ZIP |
| New scalar CUDA worker and pool/coordinator | This working tree; original Spark host/engine unchanged | Apache-2.0; original ISC hash notices remain | Windows Rust/CUDA build and bounded fixed GPU/loopback TLS tests; no compiled binary redistribution |
| Optional cryptography test helper | Existing installed 46.0.5 | Apache-2.0 OR BSD-3-Clause, installed package metadata | Only ephemeral loopback TLS certificates; no package/key bytes included, no installation |

The included `NOTICE` lists MIT snippets in the legacy Spark tree; their file
headers remain intact. Original copyright and license text were not removed.
Modifications to existing files are called out in the top of README and the
NOTICE addendum. No claim of endorsement or official NVIDIA/Pearl status is made.

The root Pearl license describes separately licensed monorepo subprojects,
including Plonky2 (MIT OR Apache-2.0). Do not infer complete transitive license
coverage for zk-pow from the root ISC alone. The zk-pow and pearl-blake3 manifests
in the inspected current tree have no package license field; they have no local
LICENSE file. The repository root terms and subproject terms must both be carried
into source/vendor audits. The exact source subset now includes those licenses.

For the actual isolated verifier, metadata inventories 211 packages and verifies
202 downloaded registry archives against the resolved Cargo.lock checksums.
The license expressions offer permissive MIT/Apache/ISC/CC0/Zlib/BSD choices;
unicode-ident additionally requires its preserved Unicode-3.0 terms. One package
offers LGPL as an alternative, with MIT/Apache choices available. The three
official path crates without package license fields (zk-pow, pearl-blake3,
layout-macro) are covered by Pearl's preserved ISC root. This audit is scoped to
the helper build, not a cleared public miner binary release. Full registry
metadata/archive hashes are in `evidence/proof-lab/dependency-integrity.json`;
individual official file hashes are in `third_party/pearl-proof/PROVENANCE.json`.

The new worker's actual isolated build additionally inventories 257 packages and
verifies 243 registry archives against production_worker/Cargo.lock. Its TLS/type
dependencies include ring (Apache-2.0 AND ISC), webpki roots data under
CDLA-Permissive-2.0, Unicode-3.0 and packages with LGPL alternatives for which
MIT/Apache choices are available. Preserve the applicable per-file license/data
notices and source when preparing any binary distribution. Exact expressions
and hashes are in evidence/worker/dependency-integrity.json. This is not a
completed public-release SBOM/redistribution clearance.

`Cargo.lock` describes the **legacy** Rust workspace. Its full transitive registry
license inventory, sources, optional features and binaries have not been audited
or built here. Before distributing a compiled worker, produce an SBOM from the
actual build, verify every license/NOTICE and optional dependency, archive the
exact corresponding source and check CUDA redistribution conditions. The new
default portable control/UI has no third-party Python runtime dependencies; the
optional proof lab uses the existing `blake3==1.0.8` package. A build-system
declaration of setuptools is not a bundled runtime dependency.

The original hardcoded upstream fee is a behavior of the preserved reference,
not an additional fee charged by `boris_pearl`. The prototype never starts that
program. Combining both schedulers in a live release would violate this product's
requirements and is expressly excluded from the implementation plan.

Local instructions: workspace and ancestor AGENTS.md/.agents/skills were checked;
none were found. Upstream CLAUDE.md/HANDOFF.md were read. Their author-specific
hardware, publishing, model and immutable-wallet workflow does not describe this
computer or authorize release. The user's explicit separate-product and fee
requirements take precedence for these additions; the upstream files remain
preserved as upstream history. No subagents were launched.

Read-only NVML ctypes bindings call the installed NVIDIA driver; no NVML DLL,
header implementation or proprietary runtime is redistributed. DP4A is a CUDA
compiler intrinsic, not copied third-party miner code. New telemetry/load/local
GUI/search/context continuation source remains Apache-2.0 under root LICENSE.

The tensor extension is original Apache-2.0 source calling installed CUDA WMMA
interfaces and dense MMA PTX instructions described in NVIDIA documentation.
No dependency version, upstream verifier file, restricted miner implementation,
CUDA toolkit implementation or proprietary runtime was added to the source ZIP.
Binary release still requires the actual-build SBOM/notice/redistribution review.

Resident-grid experiment: new Apache-2.0 local_grid.cuh/local_grid.rs/control/
test code calls the unchanged Apache-root CUDA prep and ISC-noticed hash
helpers. The explicit blake3=1.8.7 dependency was already a locked/audited
transitive dependency; the existing CC0/Apache alternatives are preserved in
actual metadata. Registry count remains243, official exact files351. No network
fetch or toolchain install was needed. KRig docs/license were read for operator
settings; no KRig or fee-restricted miner code/binary was copied or run.

Architecture/device phase adds original Apache-2.0 source for named CUDA targets,
read-only CUDA/NVML descriptors and UUID selection, load profiles and tests.
API signatures/instruction requirements were checked in official NVIDIA docs;
no documentation implementation, proprietary binary, patched driver, restricted
miner or additional dependency was imported. Existing locked registry versions
and351 official source bytes remain unchanged; actual audit still243 archives.
No binary release/SBOM/redistribution clearance or vendor endorsement is claimed.

GB10 preparation adds original Apache-2.0 pure Rust build-policy and Python
diagnostic/fixture source, using installed compiler/NVML APIs and read-only
Linux proc/sysfs. No vendor documentation implementation, firmware, toolkit
binary, restricted miner code or new dependency was copied. All351 official
source files and locked dependencies remain unchanged. OEM thermal-zone names
are observations, not NVIDIA/MSI certification or permission to alter protections.

AMD/OpenCL phase: HIP/CLR source MIT and Khronos ICD-loader Apache-2.0 licenses
were read at primary sources; none of those implementations or binaries were
imported. ADLX has a separate SDK agreement and is not assumed MIT or adopted.
Existing Windows OpenCL vendor driver/loader are called through original ctypes
bindings and are not redistributed. Original wrapper/fixtures are Apache-2.0;
BLAKE3 adaptation from existing cuda/common/blake3.cuh retains complete Pearl/
Decred ISC notice in native/opencl/fixed_tile.cl (Apache-2.0 AND ISC).
No new package, restricted miner, altered fee code, driver, runtime binary or
SDK was copied/installed. All351 official files/locked dependencies remain exact.
A future selected AMD runtime/SDK needs its actual binary/redistribution and
per-file license review; public repository licensing is not that clearance.
Primary-source research: evidence/amd/backend-research.json and AMD_OPENCL.md.

## Самостоятельная сборка PearlMine

Внутрь входят CPython, Tcl/Tk, cryptography и OpenSSL, собранные PyInstaller 6.11.1. Перед публичным выпуском нужно
приложить их лицензии и перепроверить этот список. Сейчас сборка внутренняя.
