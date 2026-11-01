# AMD-oriented OpenCL prototype - 2026-10-31

Status: original portable scalar INT8 OpenCL laboratory prepared. It compiled
and executed **only on the physical NVIDIA RTX5090, Windows x64**, through the
already installed NVIDIA OpenCL ICD. **No AMD compiler, driver, GPU execution,
performance, safety or production compatibility is qualified.** No mining,
external pool or real share submission occurred. App mining Start stays disabled.

## Open-source choices and license boundary

| Candidate | Primary license/source checked | Decision |
|---|---|---|
| HIP API and AMD CLR (HIP/OpenCL source) |[HIP MIT](https://raw.githubusercontent.com/ROCm/hip/develop/LICENSE.md), [CLR MIT](https://raw.githubusercontent.com/ROCm/clr/develop/LICENSE.md) |Legitimate future HIP path; no source/SDK/runtime imported and no HIP compiler installed here |
| OpenCL + installed vendor ICD |[Khronos loader Apache-2.0](https://raw.githubusercontent.com/KhronosGroup/OpenCL-ICD-Loader/main/LICENSE), [OpenCL API](https://registry.khronos.org/OpenCL/specs/unified/html/OpenCL_API.html) |Selected original OpenCL C1.2 test kernel; existing OS loader/vendor driver called dynamically; neither bundled |
| AMD SMI telemetry |[AMD SMI MIT terms](https://raw.githubusercontent.com/ROCm/amdsmi/amd-staging/LICENSE) |Possible future Linux read-only adapter; absent, not bound or tested |
| Windows ADLX telemetry |[ADLX repository and separate SDK agreement](https://github.com/GPUOpen-LibrariesAndSDKs/ADLX) |Not adopted; do not assume MIT or permission to redistribute its runtime |

Open-source repository licenses do not automatically license all installed
Windows driver/runtime redistribution. The release-specific
[Windows HIP SDK component table](https://rocm.docs.amd.com/projects/install-on-windows/en/latest/conceptual/component-support.html)
distinguishes Windows/Linux components and licensing; it is legacy7.2
documentation, not a current universal support promise. Any eventual selected
release must be checked against the actual
[ROCm GPU/OS compatibility matrix](https://rocm.docs.amd.com/en/latest/compatibility/compatibility-matrix.html).
No third-party miner, restricted developer-fee implementation, ROCm header,
SDK, binary, patched driver or new dependency was copied or installed.
Research links/observations are in evidence/amd/backend-research.json.

The new wrapper, fixture generation and tile controller are original Apache-2.0.
The BLAKE3 one-block adaptation preserves existing Pearl Research Labs/Decred
ISC copyright/permission notice from cuda/common/blake3.cuh. New kernel SPDX is
Apache-2.0 AND ISC. Existing exact351 official source files/locked dependencies
are unchanged. A binary release still needs its actual dependency/runtime audit.

## Implementation

`native/opencl/fixed_tile.cl` computes one8x16 signed INT8 tile with cumulative
uint32 accumulation, XOR folding every128 K elements, rotate13 transcript and
keyed BLAKE3 of the64-byte transcript. It uses one128-item group and512 bytes of
shared cells; no fixed warp/wave width, tensor layout, subgroup instruction,
floating-point rounding or vendor extension. UInt wrapping and signed char
semantics follow [OpenCL C](https://registry.khronos.org/OpenCL/specs/unified/html/OpenCL_C.html).
This is a conservative scalar portability path, not an optimized AMD GEMM miner.

`boris_pearl.opencl_lab` loads only the existing system OpenCL loader in an
owned child. Inventory creates no context/buffers and launches no kernels.
Compilation explicitly binds a complete cl_khr_device_uuid UUID and exact source
SHA256, requires a compiler/little-endian/128-item group/512 local bytes, and uses
only -cl-std=CL1.2. Missing/duplicate UUID or unsupported capabilities refuse;
OpenCL ordinals are never treated as CUDA/NVML ordinals.

The protocol accepts inventory, compile,15 internal arithmetic case IDs and
three immutable golden seeds/eight saved tile IDs only. Arbitrary headers,
targets, nonces, wallet/pool/job input and extra parameters are rejected. There
is no production worker registration, nonce search, fee ledger credit, network
or submit path. The child limits32 launches,40 commands and180s between commands;
the parent separately enforces request timeout and a monotonic watchdog while
a driver call is blocked. Buffers/events/queue/program/context are released on
normal exit; timeout/stop reaps only the owned child. Driver-level hangs/TDR
behavior and long in-flight GPU preemption remain unverified.

The validation adapter enables actual dispatch only for NVIDIA vendor0x10DE,
with exact OpenCL/CUDA/NVML UUID agreement and existing reviewed CUDA identity.
CC12.1/GB10 is refused before the sensor/compute stage because OEM board/power/
unified-memory coverage remains unqualified. AMD/other-vendor dispatch is
explicitly refused in the child and adapter until its reviewed telemetry exists;
NVML is never silently used for an AMD card. This deliberate gate also prevents
a source-eligibility claim from enabling an unmonitored test.

NVIDIA checks retain fresh temperature/power/free-memory/AC/utilization,
<=75C, >=1GiB free, <=min(125W,80% reported driver limit), a fixed deadline,
manual stop, latched sensor loss and10% whole-request pacing. Thresholds are
observed software stops, not hardware power caps. No tuning API, OEM setting,
clock/voltage/fan/power modification, service, autostart or installation exists.
The separate test fee is0%; shared disclosed2% production fee logic is unchanged
and cannot turn this laboratory into a live backend.

## Actual results on this machine

| Check | Observed result |
|---|---|
| Portable CPU/process/policy suite |97 PASS (10 new OpenCL policy/arithmetic/GB10-refusal tests); no GPU loaded by these tests |
| Installed runtime |Windows x64 NVIDIA OpenCL3.0, OpenCL C1.2, driver617.42; one RTX5090, no AMD ICD/device |
| OpenCL compilation |PASS with -cl-std=CL1.2; NVIDIA emitted one noinline-attribute warning, retained in build log |
| Signed arithmetic |15 CPU/OpenCL matches: K2048/2176/4096 x zero/extremes/impulses/random; also matches preserved Rust CPU/CUDA expectations |
| Immutable Pearl golden fixtures |3 noised strip/transcript/digest matches at K2048/4096; full-width signed INT8/uint32 semantics |
| Official proof verifier |3 full host-assembled proofs accepted using OpenCL-computed digest;6 altered digest/Merkle leaf cases rejected |
| Safety lifecycle |Manual, deadline without another request, and sensor loss without another request: owned child reaped and absent from independently fresh NVML PID data |
| AMD/HIP/Linux runtime/real pool |Not performed; no AMD hardware/compiler/runtime; WSL denial not retried or bypassed |

Main validation executes18 fixed single-tile launches; three stop scenarios add
one each (21 total). Main device events sum to2.257984ms; this is fixed-test GPU
time, not an AMD benchmark, mining hashrate, revenue estimate or sustained-load
qualification. The17 recorded main sensor samples observed at most32C/76.401W;
these sparse short-test observations do not establish thermal stability.
Manual stop reaped in~16ms and sensor loss in~32ms in that run. The deadline
measurement includes waiting for the already configured deadline, not just
handling latency. All were between tiny completed kernels; they do not prove
preemption of a long/hung GPU kernel or AMD stop behavior. Fresh PID absence
does not establish exact VRAM recovery under WDDM.

Evidence: evidence/amd/opencl-validation.json, portable-tests.txt and the exact
executed harness snapshot named by that JSON. Subsequent harness edits only
generalize platform/device labels; historical execution remains bound to its
saved hash. Current default/read-only behavior is checked separately in
readonly-validation.json. No CUDA binary or earlier long-run result is relabeled
as an OpenCL/AMD measurement.

## Safe read-only inventory and reproducibility

With existing Python3.11+ and an existing OpenCL ICD, from project root:

~~~sh
python -m boris_pearl.opencl_diagnose > opencl-inventory.json
~~~

This uses only device queries in a timeout-bounded child: no root, network,
NVML, compiler, context/buffer allocation, kernels or setting changes. UUIDs are
omitted from the saved public report. It can identify missing AMD ICD/compiler
capabilities before considering any workload. No automatic install/fallback.

CPU checks and read-only inventory (already-installed optional blake3 needed for
CPU golden checks):

~~~sh
python tools/boris-opencl-verify.py --report docs/boris/evidence/amd/readonly-validation.json
~~~

The opt-in experiment `--nvidia-validation --device 0` additionally runs the
bounded fixtures above on the explicitly selected NVIDIA device. It is never
called by app startup, GUI mining Start or the default tool invocation. AMD
execution remains refused even with that flag. Do not treat this as instructions
for an AMD load test or installation.

## Exact remaining blockers / minimum next step

- **Hardware/runtime absent:** idle inventory sees only NVIDIA RTX5090. There
  is no actual AMD model, AMD ICD/compiler result or AMD kernel execution to
  validate. Windows and Linux need separate physical AMD tests.
- **HIP compilation blocked:** hipcc, amdclang++, clang++ and the AMD ROCm SDK
  are absent. No HIP target binary was built; an NVIDIA OpenCL compilation is
  not HIP compilation, AMD ISA compilation or an AMD emulator.
- **Safety integration unimplemented:** AMD needs a read-only, exact-device
  telemetry adapter with validated identity, temperature/hotspot/power scope,
  VRAM/shared-memory pressure and notebook AC behavior. Its driver limit/power
  semantics and SDK license must be reviewed; unavailable data must stop work.
- **Linux validation inaccessible:** earlier WSL enumeration returned
  Wsl/EnumerateDistros/Service/E_ACCESSDENIED. No retry, Docker bypass, system
  change or alternate Linux environment was attempted.

Minimum safe next step is an existing authorized physical AMD Windows/Linux
host: run only the read-only inventory above and identify the exact model/OS/
driver plus available read-only telemetry. That does not require mining or
system modification. Once hardware and sensors are reviewed, add the AMD
adapter and repeat fixed arithmetic, full official proof positive/negative,
stop/fault and short guarded thermal tests per model/OS. Optimized RDNA/CDNA
kernels, full-size search, soak and real accepted-share tests are later separate
gates; no claim of support for all AMD or all cards is made.
