# GPU worker and pool integration РІР‚вЂќ 2026-10-31

Locally verified development baseline; not a production qualification. No live job, real-network nonce search, external pool connection or real share
submission executed. Artificial-job sustained search is separately gated; see LOAD_SAFETY.md.

## Execution path

`PoolCoordinator -> Controller -> durable Grant -> CudaWorker -> inherited pipes
-> Rust host/Merkle -> CUDA tile -> CPU canary -> full proof -> official V3
verifier -> Router -> endpoint-scoped PoolClient.submit`.

Jobs retain immutable owner, pool/wallet, session epoch, generation, header,
certificate and target. Reused wire job IDs receive a new internal generation,
including target-only changes. Validity is checked after verification and at
socket dispatch. Canonical duplicate and uncertain submissions are never
automatically resent. Registries/queues/line sizes have bounds; a reached limit
stops acceptance rather than silently allowing duplicates.

The worker imports unchanged Spark host.rs/engine.rs and CPU/type crates, not
the original daemon or fee scheduler. Root workspace dependency paths select the
exact audited official subset. All 351 official files remain byte-identical.

## Bounds and authorization

- Dense V3/r128, m/n multiples of64 up to131072, K2048..4096 in steps of128.
  One8x16 signed INT8 GPU tile per call (DP4A/WMMA/MMA), checked allocation sizes, CUDA event duration
  and CPU transcript/digest canary. Tensor paths are explicit; see TENSOR_BACKENDS.md.
- 100ms..1s admission budget. Lazy/JIT loading is forced before event recording
  via cudaFuncGetAttributes, following [NVIDIA's lazy loading guidance](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/lazy-loading.html).
  A CUDA graph queues begin/kernel/end together, excluding intervening host
  scheduling gaps; no unaccounted warm-up kernel is executed.
  An observed over-budget result stops the session; this does not prove GPU
  preemption before that deadline. Owned-process manual/deadline/sensor-loss stops are measured on this Windows5090; hardware preemption is not guaranteed.
- Default mode allows only a small synthetic header and fixed nonce. Offline
  exhaustion refuses nonce advancement. `--live` source exists but was never
  executed. No CLI/GUI Start creates live/device/deadline consent.
- An explicit --large-fixed-fixture mode allows only marked synthetic header,
  fixed nonce, shape131072x131072x4096 and exactly one tile. Another tile is
  refused. That bounded full-size preparation and official proof now pass.
- Worker/verifier binaries must match recorded SHA256. Live adapter consent
  binds config/device/deadline and is rechecked during waits. The controller
  additionally enforces a monotonic deadline and fresh fail-closed telemetry.
- Default PoolAccess allows only an exact literal loopback endpoint. External
  access requires explicit consent and validated TLS; submissions have another
  endpoint-scoped permission. System proxy/trust/security settings are unchanged.

## Protocol and fees

Object and Kryptex auth/notify shapes were replay-parsed from preserved redacted
captures without contacting their endpoints. Gzip requires an explicit v2 ack.
Single I/O owner, request IDs, 2MB caps, bounded queues, auth gates, timeout,
disconnect retirement, epoch reconnect and explicit backoff are implemented.
Current mainnet/pool requirements still need separate confirmation.

Only canonical bincode accepted by the pinned official verifier and Router for
that job may be submitted. A dropped acknowledgment is uncertain and not retried.
An explicit failover helper exists; a disclosed multi-alternate-pool policy is
still future work. Coordinator currently uses one user and one developer endpoint.

Only device-event active time enters the fee ledger. Minimum tile admission
lets fee credit accumulate before a developer dispatch; unused reservation is
returned. Developer outage waives credit; user outage pauses all work. CPU
preparation, proof checks, network waits and idle wall time do not count as GPU
activity. Time percentage does not guarantee revenue percentage.

## Evidence

Run `python tools/boris-proof-verify.py`, then `python tools/boris-worker-verify.py`
with existing audited dependency cache/toolchain. Both build offline. Worker
verification executes fixed small GPU fixtures and literal127.0.0.1 TLS mocks.
Missing prerequisites/skipped worker tests must report blocked, not success.

- evidence/worker/worker-build.json: executable/lock hashes and Rust/CUDA versions.
- dependency-integrity.json: 351 official files, 243 registry archives, 257 package
  license metadata records. Verifier has a separate202-archive audit.
- portable-tests.txt:78 tests, including TLS faults/dispatch races and fee logic.
- fixed-gpu-tls-tests.txt:27 worker/safety tests, including artificial search, real sensors, stops and cursor continuation.
- full-size-fixed-report.json: fixed131072x131072x4096 preparation/one tile,
  official102193-byte proof, second tile refused, owned process reaped.
- integration-report.json: distinct committed headers and51,697-byte official
  wires for user/developer, different SHA256, correct mock-pool destinations,
  worker reaped, zero external pool connections/real submissions/live executions.

The fee test explicitly credits five seconds of synthetic prior ledger history
to admit a developer tile without a long GPU run. It is not a physical2% test.
The fee-routing test uses synthetic safety telemetry; separate physical stop/search tests use real NVML. Ephemeral local TLS certificates use installed
cryptography46.0.5; their private test-server keys are deleted, not wallet keys,
and are not packaged. Optional independent proof lab uses existing blake3 1.0.8.

Full-size search/nonce transitions, production throughput, other-device sensors/stop latency,
sleep/resume, OEM safety, accepted shares/blocks, soak, Linux, other GPU models,
AMD and multi-GPU remain release gates; see SUPPORT_MATRIX/RELEASE_PLAN.

Latest extension: persistent DP4A workspace, four immutable host contexts, acknowledged cursor resume, real timeout-isolated NVML, duty pacing and monotonic stop watchdog. See LOAD_SAFETY.md and evidence/load. Small artificial nonce walk passes; full-size full search/live pools remain unqualified.

Latest separate experiment: native --full-grid-local-test accepts only fixed
internal artificial data, resident operand batches and audit proofs. It is not
a PoolCoordinator backend.24 worker tests now pass. See FULL_GRID_LOCAL.md.
The pool adapter identity fix and current official-source limits are documented
in POOL_PROTOCOL_REVIEW.md. No real protocol acceptance/credit was established.

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
