# GB10 / Linux ARM64 preparation - 2026-10-31

Status: source and build policy prepared; **native ARM64 worker/verifier build
and physical GB10 execution have not been performed**. This is separate from
the existing Windows RTX5090 results. There is no real mining/pool submission.

## Compatibility and build route

[MSI EdgeXpert specifications](https://uk.msi.com/Edge-AI-PC/EdgeXpert-MS)
identify GB10, Arm CPU, DGX OS/Ubuntu and unified memory. The actual memory size,
OEM firmware, cooling and sensors must be read on the user's device.
[NVIDIA's capability table](https://developer.nvidia.com/cuda/gpus) assigns GB10
CC12.1. The source uses fixed-width integer C ABI fields, dense signed INT8
DP4A/WMMA/MMA and unchanged official proof code; no x86-only intrinsics were
found in the new worker/bridge or their direct Spark Rust crates. This is source
inspection, not ARM ABI/linker/dependency execution proof.

The new `BORIS_CUDA_PROFILE=gb10` plans generic sm121 SASS + compute121 PTX,
minimum CC12.1 and the three existing dense tile backends. It does not import
or relabel the upstream architecture-specific TMA sm121a backend. The
[CUDA13 NVCC documentation](https://docs.nvidia.com/cuda/archive/13.0.0/cuda-compiler-driver-nvcc/index.html)
lists compute121/sm121; actual toolkit support is checked by `nvcc --list-gpu-arch`.
This profile refuses any host/target except a native
`aarch64-unknown-linux-gnu` pair and refuses an older toolkit. No automatic
WSL, container, cross compiler, lower-target fallback or installer exists.
Linux linking checks `targets/aarch64-linux/lib`, then `lib64`, within the
existing CUDA root. Windows portable/turing75/ada89 image lists stay unchanged.

[DGX Spark release notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html)
currently list CUDA13.0.3 for the reference Founders Edition; they explicitly
separate partner update schedules. These versions are information for diagnosis,
not instructions to replace MSI firmware, driver or OS. Keep OEM protections and
the supplied power adapter. Do not run upstream clockcap/install/service scripts.

## Read-only next step on the actual device

Copy/unpack the source to a user-owned directory on the MSI/Spark and run from
the project root using the existing Python3.11+:

~~~sh
python3 -m boris_pearl.gb10_preflight > gb10-preflight.json
~~~

No root, network, wallet, GPU kernels/allocation, build, install or setting
changes. Optional `--device-uuid GPU-...` binds NVML to a complete physical UUID;
otherwise the report explicitly uses diagnostic NVML index0. Tool probes and
the owned sensor subprocess have timeouts. UUIDs and process IDs are omitted
from the report. **Exit2 is intentional**: diagnostics never enable mining or
certify the hardware, even when all readings exist. On this Windows host it
returns the exact Linux ARM64 requirement before invoking tools or NVML.

It records existing nvcc/Rust targets, driver/device capability, NVML readings,
`/proc/meminfo` and raw thermal-zone type/value/error observations. Share that
JSON for review; it needs no private key or wallet address. Confirming the
exact MSI/OEM board sensor and power measurement is the minimum next step.

## Power, memory and stopping

CC12.1 triggers GB10 safety rules even when the OEM name omits GB10. A missing
board/OEM temperature now refuses the artificial test before a compute worker
is created. The current NVML reader does not provide a qualified board sensor;
raw `acpitz`, GPU or other sysfs zones are diagnostics only. Their identity,
coverage and threshold need review on this exact machine before integrating
an OEM sensor. No zone is silently accepted as the board sensor.

The project retains its conservative <=85W *observed software stop ceiling*
and the requirement for a valid reported driver power limit (threshold also
<=80% of that limit). 85W is inherited project policy, not an MSI/NVIDIA
hardware rating, instantaneous hardware cap or guarantee against power loss.
No missing driver limit is guessed and no OEM protection, fan, clock, power or
firmware setting is changed. Missing readings keep the test blocked.

[NVIDIA's known-issues guidance](https://docs.nvidia.com/dgx/dgx-spark/known-issues.html)
explains that shared CPU/GPU DRAM can make ordinary framebuffer accounting
unsupported. The preflight reports system MemAvailable and SwapFree separately;
it never counts swap as GPU headroom or replaces unavailable NVML free memory.
The eventual allocator needs hardware-validated shared-memory pressure and CUDA
allocation checks; present admission remains fail closed. GPU temperature,
board temperature, power, memory, sensor freshness, explicit deadline and manual
stop remain required. Restart stays stopped; independent monotonic watchdog and
owned-child reaping are preserved. Pure fault fixtures pass, but actual GB10
stop latency, driver hangs, AC events and thermal behavior remain untested.

## Build-only recipe after diagnosing prerequisites

For a later authorized native Linux ARM64 host with the existing supported
CUDA13+ toolchain, GCC/G++, Rust stable>=1.94 and a reviewed dependency cache:

~~~sh
RUSTUP_AUTO_INSTALL=0 BORIS_CUDA_PROFILE=gb10 cargo +stable build --locked --offline --release --manifest-path production_worker/Cargo.toml
RUSTUP_AUTO_INSTALL=0 CARGO_TARGET_DIR=proof_bridge/target cargo +stable build --locked --offline --release --manifest-path proof_bridge/local-build/Cargo.toml
~~~

These are build commands, not run commands; neither was executed on Linux.
Use existing CUDA_HOME/CUDA_PATH/NVCC if the toolkit is outside /usr/local/cuda.
The source ZIP excludes the Cargo cache and binaries. If cache/compiler/target
is missing, stop and report the exact error; this recipe does not fetch/install
or rewrite the toolchain pin. Record platform, compiler/driver, lock/source and
binary hashes. The verifier local-build manifest uses the included exact pinned
Pearl source, avoiding unrelated upstream CUDA submodules.

Only after a native build and sensor/memory review can an explicitly bounded
artificial GPU fixture be considered. Then require CPU arithmetic equality,
full official positive/negative proofs, stop/deadline/sensor-loss reaping and
small-load thermal review on actual GB10 before increasing test duration.
No current Windows receipt or performance number qualifies this build. Real
pool/accepted-share tests require separate consent after those gates.

## Checks completed here and exact blockers

`tools/boris-gb10-verify.py` uses the existing Windows compiler only: seven
pure Rust build-policy tests, portable Python tests, wrong-host rejection,
wrong-host diagnostic exit, formatting and two compile-only negative probes.
It launches no GPU workload. Evidence: `evidence/gb10/static-verification.json`
and individual logs. Windows regression build/27 bounded artificial GPU and
loopback integration checks are separate in `evidence/worker`.

1. `nvcc --ptx -arch=compute_121 ...` is **blocked** by installed CUDA12.4.131:
   `nvcc fatal : Value 'compute_121' is not defined for option 'gpu-architecture'`.
2. `rustc +stable --target aarch64-unknown-linux-gnu --emit=metadata ...` is
   **blocked** with E0463: cannot find crate std; only x86_64-pc-windows-msvc is
   installed. No ARM sysroot or native ARM compiler/linker is available, and no authorized
   Linux runtime is accessible.
3. Earlier `wsl --list --quiet` was **denied**, exit1,
   `Wsl/EnumerateDistros/Service/E_ACCESSDENIED`. It was not retried or bypassed;
   neither Docker nor a hidden alternate Linux path was used.
4. Physical MSI/Spark is absent. Its OEM thermal/power and shared-memory
   admission cannot be confirmed here. This is a hardware blocker, not a test
   pass or a pending mining permission request.

Minimum safe continuation: run only the read-only preflight above on the actual
MSI/Spark and provide its JSON. A separate authorized native ARM64 Linux stand
can establish compilation; only the actual GB10 can establish GPU/proof/safety
behavior. No system access change or real mining is needed for the first step.
