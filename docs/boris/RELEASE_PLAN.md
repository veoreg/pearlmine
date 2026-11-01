# Remaining release gates

**Gate 0, blocks everything below: throughput. See SPEED_GATE.md.**

Current baseline: separate Rust/CUDA DP4A/WMMA/MMA worker, official pinned V3 proof,
portable fee/controller/pipes, explicitly gated TLS coordinator, real Windows
NVML, duty limiting and independent stop deadline. Fixed/full-size single-proof
and bounded artificial nonce walk pass; normal job changes and user/developer
return preserve cursor/checkpoint. No production mining release exists.

1. Integrate and qualify the resident-data tensor experiment for actual live
   job/target/owner changes. Exact WMMA/MMA tile backends and local resident
   experiments pass their scoped checks; local speed is not credited pool work.
   GB10 TMA remains separate from Ada/Turing; do not just change -arch flags.
2. Build/test Windows x64, Linux x64, Linux ARM64 independently. Linux NVML/sysfs
   source and recipe exist but no Linux runtime was available. Obtain physical
   MSI/GB10,4090,2060 Laptop. AMD HIP/Vulkan and multi-GPU need implementation.
3. Qualify full-size131072x131072x4096 full search/nonce lifetime and resource use,
   target updates/consensus negotiation under realistic load. Only prepare/one
   full-size proof and small artificial sustained search are physically tested.
4. Validate real device/OEM sensor coverage, AC transitions, sleep/resume/WDDM/TDR,
   stop latency and VRAM recovery on each platform. GB10 board sensor is missing
   here;85W stop ceiling/protections are retained. A few minutes at20% duty do
   not establish a24h or full-load thermal qualification.
5. Keep a single disclosed fee policy0/1.5/2%; no legacy scheduler. Carry device
   event durations, reservations, conservative recovery, no idle/user-outage fee,
   developer debt waiver, exact owner/epoch/target proof binding and no uncertain
   resend into every backend. Long actual2% fee-rate experiments remain needed.
6. Add a disclosed alternate-pool list policy. Backoff helper exists, coordinator
   currently has one user and one developer endpoint. No hidden pool replacement.
7. With separate consent only: current real-pool negotiation, accepted shares
   for both public addresses and independent pool-record comparison; no wallet
   keys/seed required. Developer public PRL address still needed for live2%.
8. With separate consent only: monitored long full-load soak and normal daytime
   work, public release/installation, signing/installer and publication. Produce
   binary SBOM, exact sources/LICENSE/NOTICE, toolchain/targets/hashes and review
   CUDA redistribution terms before distributing executable artifacts.

Network source/dependency blocker is resolved. Dependencies already cached;
this machine used existing Rust/CUDA/MSVC. No driver/toolchain or system changes
were made. Original Library GB10 prototype could not be materialized; authorized
Spark65365633e3bff61b0f29d72109622dd22ff5a379 fallback and exact official Pearl
3fe226761a139a9652b8f28a6464a4bbc25986c8 sources remain documented and preserved.

Linux environment probe: read-only `wsl --list --quiet` returned exit1, `Wsl/EnumerateDistros/Service/E_ACCESSDENIED`. Distribution inventory could not be obtained; no settings changed, no retry/bypass. Linux validation needs a separate authorized Linux system or WSL access. Windows development tests continued independently; Linux qualification remains blocked.

Before even a bounded live pilot: confirm current official chain/pool protocol
against the chosen endpoint; audit the full-size nonce/resource path and stop
behavior on that exact GPU; supply public user/developer PRL addresses and
separate consent for endpoint, real search and submissions with a short deadline.
Keep device UUID/fresh sensors/low load/visible fee and an immediate Stop.
Compare each accepted proof and pool ownership records independently. No such
pilot has been started. Linux/GB10/4090/2060 pilots first need their own builds
and artificial correctness/safety qualification; AMD first needs a backend.

The new full resident grid is an artificial-only experiment, with its own
explicit1800s/25%-duty ceiling and fixed internal data. See FULL_GRID_LOCAL.md.
It does not close the live full-size integration gate: actual target/hit proof,
job retirement, owner switching/checkpoint/eviction and fee-accounting behavior
must be qualified when the resident path is connected to a future live worker.
The existing every-tile CPU-canary worker remains independent.

Official pool review is in POOL_PROTOCOL_REVIEW.md. Kryptex is only a research
assumption until a user destination is supplied. Public operator pages confirm
settings but do not fully specify its JSON/gzip-credit contract. No pool
handshake was attempted. A separately authorized protocol confirmation/pilot
must precede a release claim; actual credited records are still unverified.

RTX4090/2060 independent preparation is documented in ARCHITECTURES_AND_DEVICES.
Named profiles compile and their PTX passes local5090 CPU/proof checks; exact
device selection and laptop guards pass scoped tests. Missing physical cards
block native SASS/driver/OEM/AC/thermal/VRAM/stop/soak qualification. Linux still
requires an authorized environment/toolchain; WSL denial was not retried.
No target throughput or universal compatibility may be inferred from these
results. Live pool/fee/job integration gates remain separate.

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
