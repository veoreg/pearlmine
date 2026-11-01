# RTX4090 / RTX2060 preparation - 2026-10-31

This phase builds exact Windows x64 CUDA profiles and checks their portable
code on the only installed GPU: RTX5090 CC12.0. **Neither physical RTX4090 nor
RTX2060 Laptop is present.** No performance, thermal, driver or pool compatibility
qualification for those models is claimed. No real pool/mining/submission.

## Official CUDA requirements

[NVIDIA's GPU table](https://developer.nvidia.com/cuda/gpus) lists RTX4090 at
CC8.9 and RTX2060 atCC7.5. Model names are not used to guess capabilities at
runtime; the worker queries the actual device.

The [CUDA12.4 Turing guide](https://docs.nvidia.com/cuda/archive/12.4.1/turing-tuning-guide/index.html)
documents signed INT8 tensor operations with INT32 accumulation and16x16x16
WMMA fragments. Our existing public-WMMA path uses that operation, alignment
and public stores; it does not transfer opaque fragment layouts across kernels.
The [PTX ISA](https://docs.nvidia.com/cuda/parallel-thread-execution/index.html)
requires SM80+ for dense INT8 m16n8k32; therefore that MMA and the resident-grid
path are refused onCC7.5. No floating-point approximation or sparse MMA is used.

The [Ada compatibility guide](https://docs.nvidia.com/cuda/archive/12.4.1/ada-compatibility-guide/index.html)
distinguishes architecture-specific cubin from forward-compatible PTX.
SASS for sm75 cannot be validated on an SM12 device. The checks below therefore
compile/inspect SASS, then explicitly force PTX JIT on the actual5090. That is
evidence for the portable code path, not an emulator or a physical Ada/Turing test.

## Named build profiles and gates

| Profile | Images | Available tile backends | Device floor |
|---|---|---|---|
| portable (default) |SASS75/89; PTX75/80/89 |DP4A/WMMA; MMA atCC>=8.0 |CC>=7.5 |
| turing75 |SASS75 + PTX75 |DP4A, WMMA INT8; MMA/resident refused even on a newer GPU |CC>=7.5 |
| ada89 |SASS89 + PTX89 |DP4A, WMMA INT8, dense MMA INT8 |CC>=8.9 |

A later GB10 preparation phase adds `gb10` (native Linux ARM64 only, generic
sm121 SASS/PTX121, CC12.1 floor); it has no ARM build or physical execution
result. See [GB10_LINUX_ARM64.md](GB10_LINUX_ARM64.md).
BORIS_CUDA_PROFILE accepts only these four names. The build's backend mask and
minimum CC are part of the native descriptor and checked alongside actual
hardware capability. Both Python preflight and native prepare reject invalid
choices before GPU work; native rejection also protects direct IPC callers.
No automatic backend fallback or use of the pre-SM80 MMA stub is permitted.

Existing CUDA12.4.131/MSVC14.38/Rust1.94 build offline from locked/audited
dependencies. No compiler/driver installation or upgrade. Reproduce the bounded
architecture experiment with an existing reviewed cache/toolchain:

~~~text
python tools/boris-architecture-verify.py
python tools/boris-worker-verify.py
~~~

The first tool builds/copies both profiles, inspects their images, runs only
fixed artificial arithmetic/proofs under real sensors, then restores portable.
The second audits/updates the shared portable receipt and checks the app.
Test binaries and per-process PTX cache stay under native/out and are excluded
from the source archive. Environment flags are scoped to children; system
environment, GPU firmware/clocks/power settings are unchanged.

## Actual results

| Check | Executed result |
|---|---|
| turing75 compile |PASS; sm75 SASS/PTX75, IMMA.8816.S8.S8 inspected |
| turing75 PTX on5090 |30 signed CPU/GPU arithmetic cases +6 official full proofs PASS |
| turing75 forbidden MMA |Native prepare rejected it on actual5090, despite newer physical capability |
| ada89 compile |PASS; sm89 SASS/PTX89, IMMA.16816/16832.S8.S8 inspected |
| ada89 PTX on5090 |45 signed arithmetic cases +9 official full proofs PASS |
| Canonical proof equivalence |All15 profile proof cases match the same canonical bytes per K2048/2176/4096 |
| Portable app suite |78 CPU/process/loopback tests +27 GPU/integration tests PASS, no skips |
| Device selection |Actual CUDA/NVML UUID join, child-scoped UUID visibility and empty mask PASS |
| GUI |Two hidden smoke checks PASS; simulated4090/2060 selection forwards exact UUID/backend/laptop profile |
| Reordered indices |Pure fixtures PASS; physical multi-GPU reorder was not available |
| Physical4090/2060, Linux |Not performed; no target cards, WSL access remains denied |

All target-specific timing/throughput is deliberately absent. PTX on5090 is
not a4090/2060 benchmark and gives no income estimate. Exact executable hashes,
proof hashes and logs are under evidence/architectures. Source/licensing audit
still verifies351 exact official files and243 registry archives.

## Exact single-device selection

The [CUDA runtime](https://docs.nvidia.com/cuda/archive/12.4.1/cuda-runtime-api/group__CUDART__DEVICE.html)
provides actual logical ordinal, name, UUID, PCI ID, CC and memory.
[NVML documentation](https://docs.nvidia.com/deploy/nvml-api/latest/api/group__nvmlDeviceQueries.html)
recommends stable PCI/UUID lookup because enumeration order need not persist.

The new read-only devices RPC launches no kernels. Select either an exact CUDA
logical index or complete physical GPU UUID. NVML opens that UUID directly and
reports its independent NVML index. The compute worker then confirms the same
UUID before dispatch. Thus CUDA_VISIBLE_DEVICES remapping cannot silently bind
sensors to an unrelated GPU. Unknown, missing, duplicate or partial identifiers
fail without choosing GPU0. Card names and nvidia-smi/NVML indices are not
silently interpreted as CUDA ordinals. MIG is not qualified.

GUI refresh is explicit, bounded and starts no work; a displayed card maps to
its full UUID internally. Changing device/profile while a session runs is
disabled. CLI examples (choose actual visible device, never assume index1 exists):

~~~text
python -m boris_pearl diagnose
python -m boris_pearl sensors --device 0
python -m boris_pearl local-search-test --seconds 30 --device 0 --backend wmma-int8 --profile laptop
~~~

A complete GPU UUID can replace0. A future coordinator caller must pass the
selected CUDA ordinal and expected UUID together. This is single-device
selection; simultaneous multi-GPU mining is not implemented or qualified.

## Laptop guard and remaining physical gates

Explicit laptop profile: default/max10% whole-work duty, maxGPU70C, confirmedAC,
>=1GiB free memory, and automatic observed-power stop threshold=min(50W,80% of
the reported enforced driver limit). An explicit higher value/duty or desktop
AC override is refused. Standard defaults remain20%/75C, automatic threshold
min(125W,80% driver limit), with explicit existing power bounds preserved.
All are software stop thresholds, not hardware power caps or OEM guarantees.

Missing/stale power/temperature/memory, unknown/battery AC, identity change and
deadline retain fail-closed/latched stops. No persistent GPU setting is changed.
An unsupported notebook NVML power/limit sensor remains a concrete blocker,
not a reason to disable protection. Actual AC unplug, laptop cooling/TGP/VRAM/
OEM behavior, native SASS execution, stop/TDR/sleep/resume and long stability
need that exact laptop.4090 similarly needs actual native-image proof/safety/
load/stop tests. GB10 board sensor/OEM requirements remain unchanged.

Linux x64 and ARM64 builds have not been run. The earlier read-only
wsl --list --quiet action returned Wsl/EnumerateDistros/Service/E_ACCESSDENIED;
there was no retry/bypass or system/network change. An authorized Linux system
and its toolchain are required. Real pool acceptance/credit and live permission
remain separate gates. Earlier21m full-grid evidence binds historical ac12a272
binary; it was not repeated or reassigned to the new selection build.
