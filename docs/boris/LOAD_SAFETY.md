# Local search, real sensors and load control

This phase runs only artificial marked headers and a fixed hard target in a
separate test mode. It never uses a real job/pool/wallet or sends shares. The
full mainnet mining path remains disabled in the GUI and was not executed.

## Implemented

- Read-only NVML via the already-installed driver, no extra Python dependency.
  Windows loads System32/nvml.dll; Linux has libnvidia-ml.so.1 support in source.
  No NVML settings functions, clock/power writes, global trust/network edits,
  services, scheduling or autostart. AC uses Windows GetSystemPowerStatus or
  Linux power_supply sysfs; an unknown value is never treated as confirmed AC.
- An owned telemetry child isolates driver calls. Parent sampling is nonblocking;
  hung/crashed/malformed/identity-changing readings fail closed with a timeout.
  Reading age is also bounded by the parent's monotonic clock. GPU UUID from
  CUDA must match the NVML-monitored device, including CUDA_VISIBLE_DEVICES remaps.
- Guard latches on lost/stale sensors, thermal/power/memory/AC failure. A fresh
  explicit session is required after these failures. Overall GPU utilization
  can pause dispatch when another workload makes the card busy. GB10 still
  requires a board-temperature source, absent here, and retains the85W ceiling.
- Independent monotonic deadline watchdog terminates the owned worker even if
  the caller stops calling tick. Manual stop cancels an in-flight batch; process
  exit and removal from fresh NVML compute-process readings are checked.
- Software duty pacing applies to the whole work cycle, including CPU preparation
  and proof checks, and waits in cancellable20ms slices. It is not a hardware
  power cap or a promise of a particular GPU utilization. CUDA event time alone
  enters the fee ledger; waits, CPU, transfers and telemetry are not charged.
- Worker defaults to two Rayon threads, bounded row caches (~3MiB per job, at most4 cached jobs),
  persistent CUDA buffers (~96KiB atK4096) and cached graphs. Every GPU tile is
  checked by an independent CPU dot/transcript calculation; regenerated/noised
  rows are cross-checked with the original host on each32nd tile/new nonce.
- Signed INT8 DP4A and explicit WMMA/MMA tile paths retain full official proof
  tests and CPU canaries; sustained full-size GEMM throughput remains future qualification. See TENSOR_BACKENDS.md. Four immutable contexts and acknowledged nonce/cursor checkpoints preserve work on user/developer returns and context eviction. Normal clean-job cancellation remains inside the existing explicit session; actual failure/safety stop does not restart. On the
  small fixed64x64x4096 fixture, warm32-tile wall time improved about3.93x and GPU
  event time about1.34x. These are local fixture results, not mining hashrate.

NVML power on newer GPUs can be a one-second average. Software readings cannot
replace instantaneous driver/OEM hardware protections. We leave them unchanged.
WDDM does not provide reliable per-process usedGpuMemory through this NVML API;
PID disappearance and process reaping do not prove exact global VRAM recovery
while other applications use the card.

## Explicit local-test controls

```text
python -m boris_pearl sensors --device 0
python -m boris_pearl local-search-test --seconds 300 --duty 0.2 --max-watts 125 --device 0 --output local-test.json
```

The local test accepts0.2..600 seconds, at most25% work duty,25..150W observed
stop threshold, fresh sensors, at least1GiB free VRAM and temperature below75C.
Threshold must be <=80% of the actual driver limit. Pick a lower reviewed
threshold for a low-power card. `--desktop` explicitly selects a desktop profile
without requiring a laptop AC reading; it does not bypass thermal/power/memory
checks. Default requires confirmed external power.

The Rust --local-search-test mode independently fixes m=n=64,K4096,target1,
synthetic marker header, initial nonce,600s/2million-tile bounds and a64-tile/
~50ms chunk cap. It rejects real headers, other targets and other shapes. Nonce
advancement is enabled only for this artificial test or separately gated live
source. Positive proofs are verified in separate fixed-fixture tests.

GUI has separate explicit local-test Start/Stop,5..300s and10/20/25% pacing.
Window close waits for owned test threads to stop. No pool credentials/settings
are used by that test; its fee is0%. Main mining Start stays disabled.

## Evidence and remaining work

See evidence/load for before/after timing, manual/mid-request/deadline/sensor-loss
stop reports and the monitored local search report. Safety tests use actual
Windows RTX5090 NVML readings; Linux AC behavior additionally has temporary-sysfs
unit fixtures. Other GPUs, Linux runtime, GB10 board/OEM protections, notebook
AC transitions, long full-load soak, full-size tensor throughput and real accepted
shares remain unverified. A few minutes at limited duty cannot establish
long-term thermal stability or production readiness.

Primary API references: [NVML device queries](https://docs.nvidia.com/deploy/nvml-api/latest/api/group__nvmlDeviceQueries.html),
[WDDM process memory](https://docs.nvidia.com/deploy/nvml-api/latest/api/structnvmlProcessInfo__t.html),
[signed DP4A](https://docs.nvidia.com/cuda/cuda-math-api/cuda_math_api/group__CUDA__MATH__INTRINSIC__INT.html).

Historical DP4A-only binary dd908e47... local300s PASS:114689 tiles,3584 nonce transitions,587 real sensor samples,32C/80.009W max observed,18.4% software work duty under20% limit, worker reaped and absent from fresh NVML PID data. No unexpected safety pause, no external pool or real submit.

Linux environment probe: read-only `wsl --list --quiet` returned exit1, `Wsl/EnumerateDistros/Service/E_ACCESSDENIED`. Distribution inventory could not be obtained; no settings changed, no retry/bypass. Linux validation needs a separate authorized Linux system or WSL access. Windows development tests continued independently; Linux qualification remains blocked.

Latest tensor binary89900562... passed a separate180s MMA artificial search:
96,097 tiles/3,003 nonce transitions,31C/78.545W max observed,18.3968% software
work duty,351 real sensor samples. Deadline reaped worker and fresh NVML PID
was absent. Both WMMA and MMA also pass manual-stop and actual sensor-child-loss
cases. No real mining or settings changes. Evidence/tensor binds exact hashes;
older DP4A duration evidence is not reused as a tensor-duration claim.

The separate artificial resident-grid mode uses allocation+1GiB admission,
~1.108GB full-size buffers,1800s native/parent ceiling and <=25% software duty.
It shares the existing NVML/AC/UUID/deadline/manual-stop gates. Every small
fixture tile is CPU-compared; full-size mode samples two per4096-tile batch.
It is not enabled for live data. Detailed scope/results: FULL_GRID_LOCAL.md.
Short manual/sensor-stop tests intentionally interrupt pacing; their busy
fraction is not a completed-run duty qualification.

Full-size artificial result: one complete131072x131072x4096 nonce plus4096
next-nonce tiles (134,221,824 total),1273.188s at <=25% configured duty,
105,421.84 measured artificial tiles/wall-second.65,538 CPU canaries and3
independent102,193-byte easy-target audit proofs pass; owned worker reaped.
No real mining/share/credit. This resident path remains local-only.
See FULL_GRID_LOCAL.md and evidence/full-size/final-summary.json for exact
scope/hashes and the retained-window sensor limitation.

Architecture/device extension: named portable/turing75/ada89 build gates,
read-only CUDA descriptors and exact UUID-based NVML selection, GUI/CLI device
choice and explicit laptop guard.78 portable +27 GPU/integration tests pass;
Turing profile30 arithmetic/6 official proofs and Ada45/9 pass via forcedPTX
on the actual5090. No physical target-card or Linux execution, no real pool,
no system setting changes. See ARCHITECTURES_AND_DEVICES.md and
evidence/architectures/final-summary.json. Old full-grid receipts are archived
under completed-run-* and remain specific to their historical binary.

GB10/Linux ARM64 preparation: native-only gb10 sm121/PTX121 build plan, seven
pure Rust tests, nine added Python GB10 fixtures (87 portable total), read-only
physical-device preflight, and CC12.1/OEM-name-independent safety selection.
Windows portable rebuild and27 GPU/integration regressions pass. Native GB10
build is blocked by CUDA12.4 rejecting compute121 and missing ARM64 Rust std;
WSL denial was not retried/bypassed. No GB10 GPU, board sensor or UMA qualification.
Exact evidence and next step: GB10_LINUX_ARM64.md and evidence/gb10.

AMD-oriented OpenCL phase: original scalar OpenCL C1.2 fixed-tile source,
owned child/UUID/source-hash guards, read-only inventory and10 new pure tests
(97 portable total). Actual compilation and GPU execution are Windows RTX5090
only:15 arithmetic +3 golden matches,3 official proofs accepted,6 tamper cases
rejected, manual/deadline/sensor-loss stops reaped and freshly absent from NVML.
No AMD/ HIP/Linux execution or real mining; AMD dispatch refuses until its own
telemetry is reviewed. See AMD_OPENCL.md and evidence/amd for source/driver,
exact scope and physical/compiler/sensor blockers.
