# GPU -> full proof -> official verifier

Дата: 2026-10-31. Официальный verifier успешно собран и проверен. Никакой
реальной добычи, поиска настоящих сетевых заданий, внешнего пула или настоящего submit не выполнялось.

Первый путь: native fixed-fixture CUDA process -> независимый Python dense proof
-> официальный verify_plain_proof(Salted). У golden fixture1 digest совпал с CUDA,
51,697 bincode bytes совпали побайтно, SHA256
`786614f2d1a0e02130d9f4c64d636d7cf83ec1e7abfc9ea4ab44e8fb0bcd3125`.
Все три golden proofs приняты, негативные мутации отклонены.

Новый путь: bounded Rust/CUDA worker -> исходный Spark host/Merkle/proof ->
официальная проверка в worker -> OfficialVerifierCallback -> immutable Router
-> локальный TLS mockpool. Подробности и ограничения в GPU_POOL_WORKER.md.

Verifier-wrapper ограничивает dense V3/r128, m/n64..131072 кратно64,
K2048..4096 кратно128, размер входа2MB и Merkle-структур. Он проверяет proof через
неизменённый официальный код, пересчитывает digest и возвращает официальную bincode.
Проверка обёртки не означает физического теста всех допустимых shapes: executed
малые fixtures256/512 и один fixed full-size131072×131072×4096 proof.
В последнем ровно один тайл, proof102193 bytes принят, второй тайл запрещён.
MoE, неизвестные certificates и malformed input
отклоняются. Нет независимого synthetic fallback для acceptance.

Исходник Pearl закреплён: commit3fe226761a139a9652b8f28a6464a4bbc25986c8,
tree3586dc7431a9286a6775f5bd5723f39615513a60. Проверены351 точных файла и
202 registry archives. Git archive subset исключил ненужный Rust-сборке CUTLASS
submodule с длинными Windows paths; системные настройки не менялись.

```text
cargo +stable build --locked --offline --release --manifest-path proof_bridge/local-build/Cargo.toml
python tools/boris-proof-verify.py
python tools/boris-worker-verify.py
```

Нужны существующие Rust1.94+, CUDA для GPU-проверки и registry cache либо
разрешённое получение зависимостей. Официальный source subset и lockfiles входят
в архив; crate cache/toolchain/executables не входят. Контрольная сумма executable
проверяется перед запуском. См. evidence/proof-lab/official-build.json,
dependency-integrity.json, proof-cpu-tests.txt, pipeline-report.json и
verification-results.json. Для worker есть отдельные receipts/evidence/worker.

Следующие обязательные проверки: полный full-size search/nonce lifecycle, актуальные
требования выбранного настоящего пула, accepted shares с отдельного согласия,
датчики/stop latency/soak и отдельные GPU/ОС. Наличие малого принятого proof не
даёт права заявлять готовность майнера для всех карт.

Расширение: DP4A/persistent workspace, user-developer-user cursor continuation и K2176/17-slice proof проходят официальный verifier. Искусственный bounded nonce walk с реальными NVML-датчиками описан в LOAD_SAFETY.md; реальная добыча не выполнялась.

Tensor extension: DP4A/WMMA/MMA canonical full proofs match byte-for-byte in15
artificial cases across three K sizes, irregular Merkle dimensions and exactly
one full-size tile per backend.45 signed CPU/GPU transcript+digest cases also
pass. See TENSOR_BACKENDS.md; official verifier sources remain unchanged.

Full-size artificial result: one complete131072x131072x4096 nonce plus4096
next-nonce tiles (134,221,824 total),1273.188s at <=25% configured duty,
105,421.84 measured artificial tiles/wall-second.65,538 CPU canaries and3
independent102,193-byte easy-target audit proofs pass; owned worker reaped.
No real mining/share/credit. This resident path remains local-only.
See FULL_GRID_LOCAL.md and evidence/full-size/final-summary.json for exact
scope/hashes and the retained-window sensor limitation.
