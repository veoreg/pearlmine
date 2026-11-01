# Доступ и зависимости: прежняя блокировка снята

Новый GPU-worker также собран offline: проверены 243 registry archives и257
package records, отдельный `production_worker/Cargo.lock`. Evidence находится
в `evidence/worker/dependency-integrity.json`. Shared host-crates используют тот
же неизменённый official source subset через path dependencies. На новой
разрешённой машине: `cargo +stable fetch --locked --manifest-path production_worker/Cargo.toml`.
Нужен существующий подходящий CUDA/MSVC toolchain; автоматической установки нет.

После разрешения пользователя доступ к GitHub и crates.io выполнен штатным
механизмом разрешённого исполнения среды. Proxy, `CARGO_NET_OFFLINE`, сетевые
политики и настройки безопасности вручную не изменялись. ПО/драйверы/toolchain
не устанавливались. Все зависимости сохранены в task-local Cargo cache.

Получен официальный commit Pearl `3fe226761a139a9652b8f28a6464a4bbc25986c8`.
Подтверждена целостность 351 файла точной Rust-source выборки и 202 архивов crates
по Cargo checksums. Прямые версии serde 1.0.229, serde_json 1.0.151, bincode 1.3.3,
hex 0.4.3 и sha2 0.10.9 действительно получены из реестра; разрешён полный lockfile.

Обычный Cargo Git checkout остановился на длинном пути документации CUTLASS.
Для verifier выбраны только неизменённые Rust-каталоги через `git archive`
точного commit; сохранены ISC и MIT/Apache licenses. Рабочий manifest:
`proof_bridge/local-build/Cargo.toml`; Cargo.lock находится рядом. Сборка
`--locked --offline --release` и официальная проверка proof успешно выполнены.

Для продолжения **в этой среде доступ больше не блокирует работу verifier**.
На новом компьютере сначала нужны разрешённый fetch стандартных registry crates
или offline vendor bundle. В source archive уже включены official Rust source
и lockfile; registry cache и исполняемые файлы не включены.

```powershell
cargo +stable fetch --locked --manifest-path proof_bridge/local-build/Cargo.toml
cargo +stable build --locked --offline --release --manifest-path proof_bridge/local-build/Cargo.toml
python tools/boris-proof-verify.py
```

История прежних отказов соединения `127.0.0.1:9` и WinError 10061 сохранена в
`evidence/dependency-access/`. Это историческая диагностика до разрешения доступа,
а не текущий статус. Актуальные хеши и результаты — в `evidence/proof-lab/`.
