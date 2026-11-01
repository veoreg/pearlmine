# Exact INT8 tensor backends - 2026-10-31

Development prototype tested physically only on Windows x64 / RTX 5090 CC12.0,
driver617.42, CUDA12.4.131, Rust1.94/MSVC14.38. No live mining or external pool
submission. Default backend remains `dp4a`; explicit options are `wmma-int8`
and `mma-int8` in WorkerJob, coordinator, local CLI and GUI local-test selector.
Unknown names fail; no untested automatic backend fallback.

## Arithmetic and portability

Each backend computes the same signed INT8 8x16 dot tile, retaining cumulative
INT32 accumulators and the existing XOR/rotate transcript after every128 K
values, then keyed BLAKE3. At K<=4096, even 4096*128*128=67,108,864 fits INT32.
No floating-point or quantized approximation, TF32, or sparsity is used.

| Backend | Implementation | Runtime ISA gate | Physical qualification |
|---|---|---|---|
| dp4a | 128 threads, signed four-byte dot intrinsic | CC>=7.5 project gate | RTX5090/Windows artificial fixtures |
| wmma-int8 | One warp, public signed-char WMMA16x16x16; lower8 A rows zero, aligned shared memory; public store and top8 rows folded | CC>=7.5 project gate | RTX5090/Windows artificial fixtures |
| mma-int8 | One warp, two dense m16n8k32 INT8 instructions per K32; documented register mapping, zero lower8 A rows; warp XOR | CC>=8.0 | RTX5090/Windows artificial fixtures |

CUDA WMMA types, alignment and public load/store operations follow the
[NVIDIA CUDA12.4 programming guide](https://docs.nvidia.com/cuda/archive/12.4.1/cuda-c-programming-guide/index.html).
The dense MMA operand mapping and SM80+ requirement follow the
[NVIDIA PTX ISA](https://docs.nvidia.com/cuda/parallel-thread-execution/index.html).
The WMMA path does not assume an undocumented fragment element layout. The MMA
path explicitly uses the documented lane/register mapping for that instruction.
The pre-SM80 MMA image is never admitted by runtime; a device below8.0 errors.

The build contains sm75/sm89 SASS and compute75/80/89 PTX, statically inspected
with cuobjdump. sm75 includes IMMA.8816.S8.S8; sm89 includes IMMA.16816.S8.S8 and
IMMA.16832.S8.S8. RTX5090 runs driver-JIT PTX; no native sm120/sm121 code or
performance claim. Compiled targets/ISA gates do not prove support for2060,
4090,GB10, Linux or every driver. Those need separate builds and physical tests.
AMD has no implemented backend. No toolchain/system setting was changed.

## Correctness evidence

The original tensor timing/duration phase bound binary SHA256
`8990056241d6205d6604548586a6942b6e885e5b37adc1a5158a1b2a57c0e2fc`.

- 45 deterministic CPU/GPU transcript+digest comparisons: all3 backends,
  K2048/2176/4096, zeros, signed extremes, sparse boundary impulses and full
  signed random bytes. `evidence/tensor/signed-arithmetic-kat.json`.
- 15 full official proof cases: all3 backends at each of3 K sizes, irregular
  m192/n320/K2176 and one131072x131072x4096 tile per backend. Canonical proof
  bytes match DP4A exactly in each group; full-size proof102193 bytes. These
  are full proofs for bounded fixed tiles, not a full-size exhaustive search.
- Manual stop and loss of the real telemetry child are tested for each tensor
  path. Sensor loss latches and no further device time is credited; owned
  workers are reaped. `evidence/tensor/tensor-stops.json`.
- Every normal tile still runs the CPU dot/transcript canary, plus periodic
  regenerated-row canary; proof acceptance still uses unmodified official code.
  No safety/accounting shortcut was introduced for speed.

## Same-binary warm timing

Seven rotated rounds per backend, two warm-up calls then32 timed tile calls;
fixed artificial256x256x4096 target1, no nonce progression or pool. Medians:

| Backend |32-tile checked wall cycle |Sum of device-event intervals |Wall speed vs DP4A |Device speed vs DP4A |
|---|---|---|---|---|
| DP4A |11.7373ms |4.118893ms |1.00x |1.00x |
| WMMA |11.2445ms |3.688401ms |1.04x |1.12x |
| MMA |8.7826ms |1.313806ms |1.34x |3.14x |

Wall includes CPU canary, driver and RPC; excludes job preparation, proof and
network. Device intervals can include GPU scheduling. These local measurements
are not production hashrates or predictions for other GPUs. CPU/copy/RPC and
single-tile launch overhead now dominate. Batched/tiled resident-data full GEMM
requires a separate implementation, profiling and exact-proof/safety validation.
Evidence and reproduction: `evidence/tensor/warm-comparison.json` and
`python tools/boris-tensor-profile.py`.

## Guarded artificial duration test

`local-search-test --seconds180 --duty0.2 --max-watts125 --device0 --backend mma-int8`
completed180.078s,96,097 tiles,3,003 nonce transitions,351 real sensor samples.
Max observed31C/78.545W, minimum free VRAM30,714,429,440 bytes. Whole-work busy
fraction18.3968%, below20% limit. Deadline stop reaped the owned worker; a fresh
NVML sample no longer listed its PID. Developer credited time0. All external
pool connections, real submissions and persistent settings changes remain0.
See `evidence/tensor/mma-local-search-180s.json`. Power is an observed software
stop threshold; NVML may average readings. This is not a full-load/OEM soak.

Earlier DP4A300s reports under evidence/load bind dd908e47... and are historical;
they are not long-duration evidence for the current tensor binary. Early WMMA
reports likewise retain their own binary hashes. Latest receipts/summary select
only matching hashes. A real live test still requires the gates in RELEASE_PLAN.

Later resident-grid development rebuilds the same executable. Tensor proof/KAT
reports are rerun with their explicit newer hashes; the89900562 warm timing
and180s reports remain historical measurements for that exact binary.
The latest phase's complete status is evidence/full-size/final-summary.json.
A resident paired-strip/full-grid kernel is an artificial-only experiment;
see FULL_GRID_LOCAL.md. It is not the existing per-tile live-shaped backend.
