# Pinned official verifier bridge — built and tested

The Windows x64 helper was built with the existing Rust 1.94.0 and accepted
three complete fixed dense proofs through the **official unmodified**
`zk-pow::api::verify::verify_plain_proof` with V3 salted seed derivation.
GPU digest and official bincode serialization match byte for byte. Negative
proof mutations and immutable Router context/canonical deduplication pass.

## Exact sources and build

Official Pearl commit: `3fe226761a139a9652b8f28a6464a4bbc25986c8`.
All 351 extracted source files are SHA256-pinned in
`third_party/pearl-proof/PROVENANCE.json`. All 202 registry archives are verified
against the generated `proof_bridge/local-build/Cargo.lock`. License inventory
and checksums are in `docs/boris/evidence/proof-lab/dependency-integrity.json`.

The original Git dependency also tries to extract an unrelated CUTLASS CUDA
submodule, whose documentation exceeded Windows path length. The working build
uses exact Rust files from `git archive` of the same commit: zk-pow,
pearl-blake3 and Plonky2, with original ISC/MIT/Apache licenses. No official
verifier source file or system path/security setting was changed.

```text
cargo +stable build --locked --offline --release --manifest-path proof_bridge/local-build/Cargo.toml
python tools/boris-proof-verify.py
```

The source archive includes the official Rust subset and the resolved lockfile;
registry archives are in the task-local cache, not bundled. A new environment
needs authorized `cargo fetch --locked --manifest-path proof_bridge/local-build/Cargo.toml`
or an offline vendor bundle before the offline build can work. `stable` selects
the already installed 1.94.0 here; no Rust/driver/toolkit was installed or upgraded.
No reproducible/attested release claim is made.

## Scope

The helper reads one JSON request (maximum 2 MB), verifies it, recomputes the
official jackpot digest, returns official bincode plus request SHA256/revision,
and exits. Admission supports bounded dense V3/r128 m/n up to131072 and
K2048..4096 (multiples of128); executed tests remain small fixtures. No MoE,
pool, wallet, service or nonce search. The host checks executable SHA256.

The fixed offline pipeline uses saturated test target `0x207fffff`; its artifacts
must not be submitted to a pool. A production GPU worker, full-size lifecycle,
real pool target negotiation and accepted shares remain unqualified. The new
scalar worker/official proof/loopback TLS integration passes; see
../docs/boris/GPU_POOL_WORKER.md for precise scope and remaining gates.
