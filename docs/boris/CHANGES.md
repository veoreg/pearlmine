# Changes from pinned Spark source

Base65365633e3bff61b0f29d72109622dd22ff5a379; separate Boris development product.
Original daemon/optimized TMA worker/installers are not launched. Original
host.rs/engine.rs, CPU/type crates and351 official vendor files remain unchanged.

- boris_pearl: public config/address validation, transparent0/1.5/2% fee,
  integer active-time ledger, durable reservations/conservative crash recovery,
  immutable routing/canonical dedup, guards, CLI/Tk and explicit local-test UI.
- production_worker, native/worker_kernel.cu: isolated Rust/CUDA DP4A/WMMA/MMA INT8 backends,
  CUDA-event graph timing outside lazy/JIT loading, persistent device buffers,
  bounded row/nonce caches, CPU dot/transcript canary every tile plus original
  regenerated-row canary each32nd/new nonce, official proof on hits.
- Four immutable host job contexts plus acknowledged nonce/cursor checkpoints
  preserve search on owner changes/eviction/process replacement. Ordinary
  clean-job retirement continues only within an existing explicit safe session;
  crashes/unsafe sensors/manual stop do not trigger a hidden restart.
- Explicit --local-search-test accepts only artificial header/shape/target,
 600s/2million process tile bounds,64-tile/~50ms chunks; never real pools/live mode.
  --large-fixed-fixture permits only one full-size synthetic tile/proof.
- telemetry.py: standard-library ctypes read-only NVML in an owned timeout child,
  monotonic age and device UUID, Windows/Linux AC read paths, no settings APIs.
  load.py: whole-work duty pacing, bounded cancellable waits; default2 CPU threads.
- Independent monotonic deadline watchdog, latched critical sensor/thermal/power/
  memory/AC stop, concurrent safe process teardown and reply-time permission check.
- pool.py/runtime.py: explicit endpoint/submission gates, mandatory validated TLS,
  object/Kryptex/v2 encoding, bounded transport/epochs, official wire-only sends,
  no stale/duplicate/uncertain resend. Future live nonce seeds use random63 bits.
- proof_bridge/official_bridge: pinned official verifier, request/header/target/
  digest/wire binding, bounded dense shapes, no synthetic consensus fallback.
- Original CUDA prep reused by independent KAT and golden fixed-fixture pipeline.
- 70 portable tests,23 proof tests,24 GPU/worker tests;35 official Merkle tests,
 37 GPU KAT,2 GUI smokes; bounded real-sensor search/stop/timing evidence.
- Source-only archive includes corresponding sources, official provenance,
  licenses/NOTICE, lockfiles, exact evidence and SHA256 manifest; binaries,
  package cache, local settings/state and ephemeral TLS keys excluded.

Existing root files modified: README.md (fork/reference notice), NOTICE,
.gitignore (local outputs), Cargo.toml (isolated nested workspaces and exact
vendored source paths for shared host crates). No official source implementation
changed. No commits/pushes, upstream edits, publication, services/autostart,
wallet operations or persistent GPU/network setting changes performed.

Tensor extension: original native/tensor_tile.cuh uses public CUDA WMMA and
documented dense MMA register mapping; backend is immutable job data and defaults
toDP4A. Build now carries PTX75/80/89. Added45 deterministic signed arithmetic
comparisons,15 official cross-backend proofs, tensor stop/sensor-loss coverage,
same-binary rotated benchmarks and bounded real-sensor180s MMA search. Normal
retirement races use typed interruption and preserve only the existing explicit
session. Original upstream/vendor source and dependency versions are unchanged.

Resident-grid extension: original native/local_grid.cuh plus local_grid.rs and
full_grid.py provide a separate fixed-artificial mode, existing unmodified CUDA
prep, bounded resident operands, paired INT8 tensor strips/shared A panels,
bulk transcript checksum, sampled independent CPU canaries and official audit
proofs. Dedicated mode has1800s ceiling, <=25% parent duty and unchanged sensor/
deadline stops; ordinary local mode retains600s. Added3 tests (23 worker total).
Explicit blake3=1.8.7 reuses the already-audited/cached transitive version; root
lock dependency entry updated offline, with no new registry archive download.
Fixed Kryptex submit.worker to match the exact wallet.worker auth identity.
Elapsed reporting now freezes at Stop and normal completion pays final pacing.

Additional full-size deadline test raises worker/integration total to24. It
allocates/prepares the full resident dataset, then confirms the independent
deadline reaps the worker without another tick. Reporting-only retention now
keeps4000 sensor rows for future long runs; the first full run's1300-row window
is explicitly labeled and its exact launched source snapshot/hash is retained.

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
