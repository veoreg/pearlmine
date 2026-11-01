# Boris Pearl INT8 CUDA worker

Development prototype, not a qualified production miner. No network in the
binary. Default mode accepts marked synthetic headers/fixed nonce. --live was
never executed; the main app has no enabled real-mining Start.

Uses unchanged original Spark host/engine and CPU/proof/type crates, plus exact
vendored official Pearl. Original daemon/fee scheduler is not compiled/launched.
Existing Rust1.94/MSVC14.38/CUDA12.4 build sm75/sm89 + PTX75/80/89; physical5090 PTX JIT.
Linux build flags exist but have not been executed. No toolchain installation.

```text
cargo +stable fetch --locked --manifest-path production_worker/Cargo.toml
cargo +stable build --locked --offline --release --manifest-path production_worker/Cargo.toml
python tools/boris-proof-verify.py
python tools/boris-worker-verify.py
```

Fetch needs authorized registry access or an offline cache. NVCC,
CUDA_PATH/CUDA_HOME and BORIS_CUDA_COMPILER_BIN select an existing toolchain.
Binaries/runtime/cache are not in the source archive.

Protocol1 is big-endian length + JSON<=2MB, exact cuda-baseline handshake.
prepare/chunk/release bind request IDs and immutable job headers/targets. Up to4
host contexts retain owner positions; acknowledged nonce/cursor checkpoints
restore an evicted context or ordinary job-cancel process replacement. Actual
crashes/safety stops do not silently restart. A chunk is<=64 tiles/~50ms CPU
loop; each GPU launch is one8x16 DP4A, WMMA or MMA tile. Explicit backend is immutable job data; default DP4A. WMMA gateCC7.5+, MMA gateCC8.0+; no silent fallback. Persistent buffers/graphs, bounded
row caches, every-tile CPU dot canary and sampled full regenerated-row canary
are retained. Full PlainProof is officially verified before returning a hit.

--large-fixed-fixture allows only131072x131072x4096 and exactly one synthetic
tile/nonce;102193-byte proof passed. --local-search-test allows only64x64x4096,
fixed hard target1/marked header/initial nonce and600s/2million process tile
bounds. It tests nonce progression without any real chain/pool/wallet. Read-only
NVML, UUID matching, duty/CPU limits and stop watchdog are in the parent layer.

See docs/boris/LOAD_SAFETY.md, TEST_RESULTS.md and SUPPORT_MATRIX.md. Full-size
full search, production tensor throughput, real accepted shares, long full-load/OEM soak,
Linux and physical other models remain unqualified.

Idle-only kernel-selftest RPC uses fixed internal artificial data and selected
backends, never a real header/search/network.45 arithmetic comparisons and15
full-proof cross-backend cases pass on Windows5090. See TENSOR_BACKENDS.md for
public ISA references, measured scope, safety cases and current binary hash.

--full-grid-local-test is a separate artificial-only experiment, with internal
header/target/nonce, sizes64/512/131072, at most one nonce plus next prefix and
1800s process ceiling. It rejects normal worker commands and external mining
data. Uses existing prep plus a resident paired-MMA kernel; default and live
worker paths remain separate. See docs/boris/FULL_GRID_LOCAL.md for CPU/proof
sampling, load bounds and results. Not a production/live backend.

Architecture/device extension: named portable/turing75/ada89 build gates,
read-only CUDA descriptors and exact UUID-based NVML selection, GUI/CLI device
choice and explicit laptop guard.78 portable +27 GPU/integration tests pass;
Turing profile30 arithmetic/6 official proofs and Ada45/9 pass via forcedPTX
on the actual5090. No physical target-card or Linux execution, no real pool,
no system setting changes. See ARCHITECTURES_AND_DEVICES.md and
evidence/architectures/final-summary.json. Old full-grid receipts are archived
under completed-run-* and remain specific to their historical binary.

Native Linux ARM64 GB10 build-only plan: `BORIS_CUDA_PROFILE=gb10`, generic
sm121/PTX121 and CC12.1 floor. Requires existing native ARM64 CUDA13+ toolkit
listing compute121; wrong host, cross build and older toolkit fail explicitly.
Not built or executed here. Read docs/boris/GB10_LINUX_ARM64.md and complete
physical read-only preflight/OEM sensor/UMA review before considering GPU tests.
