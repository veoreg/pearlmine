# Support matrix - 2026-10-31

Only RTX5090/Windows x64 has physical artificial-job verification. Build or ISA
eligibility does not imply real-pool acceptance, profitability or safety on all
cards. No current production compatibility certification exists.

| GPU | OS / CPU | Available implementation | Actual status | Required next tests |
|---|---|---|---|---|
| MSI EdgeXpert / GB10 CC12.1 |Linux ARM64 |gb10 dense DP4A/WMMA/MMA native ARM64 build plan; upstream TMA sm121a reference separate |7 Rust policy tests and pure safety/diagnostic fixtures; CUDA121/ARM64 build blocked here; no device test |Read-only physical preflight; native CUDA13+ build, GB10 proof, board/OEM sensor, UMA memory/power review, stop/soak |
| RTX5090 CC12.0 |Windows x64 |DP4A, WMMA INT8, dense MMA INT8; official verifier; TLS; NVML |CUDA12.4/PTX JIT build;27 worker tests;45 arithmetic comparisons;15 exact cross-backend proofs; bounded MMA180s test |Live full-size integration, current pool protocol/accepted shares, full-load soak, sleep/resume/TDR |
| RTX5090 |Linux x64 |Same source plus Linux build/NVML/sysfs branches |No Linux build/runtime test |Authorized Linux environment and physical tests |
| RTX4090 CC8.9 |Windows x64 |ada89: DP4A/WMMA/MMA SASS89+PTX89 |Compile +45 arithmetic/9 official proofs via PTX on5090; no physical4090 test |Proof, throughput, sensors, stop, pool and soak on4090 |
| RTX4090 |Linux x64 |DP4A/WMMA/MMA source |Not built/tested on Linux |Separate build and physical tests |
| RTX2060 Laptop CC7.5 |Windows x64 |turing75: DP4A/WMMA SASS75+PTX75; MMA refused |Compile +30 arithmetic/6 official proofs via PTX on5090; no physical2060 test |Proof, VRAM/OEM/AC/WDDM/thermal/stop/pool/soak |
| RTX2060 Laptop |Linux x64 |DP4A/WMMA source; MMA refused |Not built/tested on Linux |Separate build, AC/OEM sensors and physical tests |
| Other NVIDIA |Windows/Linux |DP4A/WMMA gateCC>=7.5; MMA gateCC>=8.0 |Eligibility only; unqualified models/drivers |Each GPU+OS+driver tested; multi-GPU not implemented |
| AMD RDNA/CDNA |Windows x64 |Original scalar OpenCL1.2 fixed-tile lab; no live adapter |NVIDIA compilation/execution only; no AMD device/compiler/sensor validation; AMD dispatch refused |Physical AMD ICD/identity/telemetry, compile/proof/stop/thermal tests |
| AMD RDNA/CDNA |Linux x64 |OpenCL source; future HIP/AMD SMI route |No Linux/AMD build or execution |Authorized native Linux AMD host and per-model driver/sensor/tests |
| Intel/other |Separate review |No backend |Planned only |Architecture-specific implementation and tests |

Model CC values follow [NVIDIA's GPU table](https://developer.nvidia.com/cuda/gpus).
Physical VRAM/BIOS/OEM limits must be queried. CUDA capability alone does not
establish Pearl compatibility. See TENSOR_BACKENDS.md for instruction choices,
exact performance scope and test hashes. No untested automatic backend selection.

Current build: SASS75/89, PTX75/80/89. No native Blackwell image. Upstream GB10
TMA/setmaxnreg kernel is architecture-specific; changing an arch flag does not
port it to2060/4090. New process-pipe controller avoids legacy Unix-only daemon
IPC, but that alone does not validate Linux binaries.

Windows-vs-Linux profitability has not been measured. Compare accepted shares,
wall power, driver stability and operating cost on the same physical machine.
No evidence here supports a universal profitability recommendation.

Resident-grid extension: a physically tested small full-grid path compares every
tile/nonce toCPU and performs official proof audits. Full-size artificial scan
status is recorded separately in FULL_GRID_LOCAL.md; no live compatibility is
inferred. Its dense MMA ISA gate remainsCC8.0+; no other model/OS has been tested.

RTX5090/Windows resident artificial result: one full131072-square K4096 nonce
plus4096 next tiles completed in21m13s under a25% work-duty ceiling,65,538
sampled CPU checks and3 official audit proofs. See FULL_GRID_LOCAL.md.
This does not qualify actual pool jobs, other cards or operating systems.


Latest architecture phase:78 portable and27 GPU/integration checks pass. Explicit
CUDA ordinal/full UUID selection binds NVML by UUID; laptop profile lowers
software limits and requiresAC. See ARCHITECTURES_AND_DEVICES.md for official
references, exact compiled images, actual test device and unresolved physical
gates. No4090/2060 or Linux compatibility/performance is certified.

GB10 native build policy, exact compile-negative evidence, power/UMA caveats and
minimal read-only device step: [GB10_LINUX_ARM64.md](GB10_LINUX_ARM64.md).

AMD licensing research, original OpenCL prototype, exact NVIDIA-only evidence and
remaining AMD hardware/telemetry gates: [AMD_OPENCL.md](AMD_OPENCL.md).
