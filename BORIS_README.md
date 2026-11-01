# PearlMine development prototype

> **Статус: майнить пока нельзя.** Текущий GPU-код примерно в 300 000 раз медленнее обычных майнеров. См. [docs/boris/SPEED_GATE.md](docs/boris/SPEED_GATE.md).

> **Название.** Продукт теперь называется PearlMine. Внутренний пакет `boris_pearl`, переменные `BORIS_*`
> и файл `boris-pearl-worker` пока не переименованы: они привязаны к проверенным сборкам и их хешам.

## Запуск (Windows x64)

| Вариант | Что нужно | Как |
|---|---|---|
| **Самостоятельная сборка** | Ничего дополнительно: Python внутри | Распаковать `PearlMine-0.3-win-x64.zip` в папку с правом записи, открыть `PearlMine.exe` |
| Запуск из исходников | Python ≥3.11 с Tkinter | `PearlMine.exe` рядом с `boris_pearl/` (маленький launcher) или `launch-prototype.cmd` |

Настройки лежат в `state/` рядом с программой. Эту папку нельзя передавать: там коды подключения и ключ агента.
Самостоятельная сборка: `pyinstaller packaging/pearlmine.spec` (нужны собранные worker и verifier).

## Свои компьютеры через частную сеть

1. На каждом компьютере должна работать ваша частная VPN-сеть, например Tailscale. PearlMine её не ставит и не настраивает.
2. На удалённом компьютере: `PearlMine.exe --agent` (или `launch-agent.cmd`). Выберите VPN-адрес, нажмите «Включить агент», скопируйте код.
3. В панели: «Компьютеры» → «Добавить компьютер», вставьте код.
4. Запуск тестов разрешается отдельной галочкой на самом агенте; по умолчанию только просмотр.

При потере связи тест останавливается примерно через 10 секунд. Закрытие окна агента останавливает его работу.
Общего доступа разработчика нет: без кода конкретного агента подключиться нельзя. Код и ключ агента хранятся
в `state/agent/` открытым файлом под защитой только прав пользователя; в панели токен на Windows защищён DPAPI.

Separate miner project with a shared, disclosed fee/controller and replaceable
GPU backends. **Not a production release or a claim of support for every card.**
Physical validation so far: RTX 5090, Windows x64 only; artificial jobs only.

## Implemented and checked

- Public PRL bech32m addresses, explicit pools, disclosed 0/1.5/2% developer
  active-time fee. Default 0%, developer address blank. Pool fees shown separately.
  No legacy fee scheduler, hidden autostart or wallet-key handling.
- Durable integer CUDA-event accounting before dispatch, conservative crash
  recovery, no idle/CPU/network charge, no user-outage charge, developer-outage
  debt waiver. A time percentage is not a revenue percentage.
- Rust/CUDA DP4A, signed INT8 WMMA and Ampere+ dense MMA tile backends. Default
  remains DP4A; tensor paths require explicit selection. The normal tile worker
  checks every tile on CPU; its hits get full official proofs. The separate local
  resident-grid experiment samples CPU canaries as documented below. Original host/engine and 351 official source files are unchanged.
- Bounded caches, persistent CUDA graphs/buffers, resumable owner/job cursors,
  strict immutable job/proof routing, gated TLS pool coordinator and loopback
  integration. No real endpoint, real job, accepted share or live mode executed.
- Read-only installed NVML, AC/UUID/freshness checks, latched safety stop,
  independent deadline, manual stop and 20% default whole-work duty pacing.
  No clock/power/network/system setting changes. GB10 board sensor still needed.
- 97 portable, 23 proof, 27 CUDA GPU/worker tests pass, plus 35 official Merkle tests,
  37 original GPU KAT cases, 45 cross-backend signed arithmetic cases and two
  hidden GUI smokes. New tensor proofs match DP4A byte-for-byte in 15 cases,
  including K2176, irregular Merkle dimensions and a single full-size tile.

The same-binary warm fixture comparison measured MMA about 3.14x faster in CUDA
event time and 1.34x faster for the checked wall cycle than DP4A. These are not
mining hashrates. A guarded 180s MMA artificial search completed 96,097 tiles and
3,003 nonce transitions; max observed 31C/78.545W, 18.40% software work duty.
The owned process was reaped and absent from fresh NVML PID data after deadline.
See [tensor evidence](docs/boris/TENSOR_BACKENDS.md) for scope and exact hashes.

## Safe local use

```text
python -m boris_pearl gui
python -m boris_pearl diagnose
python -m boris_pearl sensors --device 0
python -m boris_pearl check --config boris-config.example.json
python -m boris_pearl simulate --seconds 3600
python -m boris_pearl worker-selftest
python -m boris_pearl local-search-test --seconds 180 --duty 0.2 --max-watts 125 --device 0 --backend mma-int8 --output local-test.json
```

The GUI has an explicit artificial GPU test, backend selector and Stop; real
mining Start remains disabled. Local test fee is 0%, with no pool/wallet use.
It allows only a marked 64x64x4096 header/target fixture, bounded nonce walk,
600s/2-million-tile process ceiling, <=64/~50ms chunks and <=25% work duty.
Fresh sensors, confirmed AC by default, <75C and >=1GiB free VRAM are required.
125W is a software stop threshold, not a hardware power cap; choose a lower
reviewed threshold for low-power hardware. Existing OEM protections remain on.

## Reproduce with an existing toolchain

```text
python tools/boris-proof-verify.py
python tools/boris-worker-verify.py
python tools/boris-tensor-profile.py
```

Builds use pinned source/lockfiles and an existing authorized dependency cache.
The installed Rust1.94/MSVC14.38/CUDA12.4 build contains SASS75/89 and PTX75/80/89;
5090 uses driver PTX JIT, not native sm120. Source ZIP excludes executables,
CUDA runtime and registry cache. Python3.11+/Tk is needed; optional test helpers
use already-installed blake3 1.0.8 and cryptography46.0.5. No software installed.

## Before live testing or release

Live full-size search qualification, production tensor throughput, real-pool negotiation,
accepted shares for both owners, public developer PRL address, long full-load/OEM
soak, notebook AC transitions, Linux, GB10/4090/2060 physical testing, multi-GPU
and AMD backend work remain. A bounded artificial run is not production safety
or compatibility qualification. Current WSL inventory was denied E_ACCESSDENIED;
it was not retried or bypassed. Linux needs an authorized separate environment.

Live endpoints/submissions/mining, installation and publication each remain
subject to the separate consent required by the task. The project is unofficial.
Original Apache/ISC/MIT/NOTICE and third-party provenance remain included.

[Support matrix](docs/boris/SUPPORT_MATRIX.md) | [Test results](docs/boris/TEST_RESULTS.md) |
[Safety](docs/boris/LOAD_SAFETY.md) | [Release gates](docs/boris/RELEASE_PLAN.md) |
[Licenses](docs/boris/LICENSE_AUDIT.md)

A separate artificial resident-grid experiment now permits a bounded complete
131072x131072x4096 scan, with full small-fixture CPU equivalence and separate
official audit proofs. It accepts no external header/target and cannot submit.
See [full-grid results](docs/boris/FULL_GRID_LOCAL.md) and
[official pool review](docs/boris/POOL_PROTOCOL_REVIEW.md). Prior timing/duration
reports bind their own historical binary hashes; latest evidence lives under
docs/boris/evidence/full-size. The resident path is not a live backend.

Full-size artificial result: one complete131072x131072x4096 nonce plus4096
next-nonce tiles (134,221,824 total),1273.188s at <=25% configured duty,
105,421.84 measured artificial tiles/wall-second.65,538 CPU canaries and3
independent102,193-byte easy-target audit proofs pass; owned worker reaped.
No real mining/share/credit. This resident path remains local-only.
See FULL_GRID_LOCAL.md and evidence/full-size/final-summary.json for exact
scope/hashes and the retained-window sensor limitation.


Latest device/architecture phase: separate turing75 and ada89 builds, explicit
CUDA index/full UUID selection in CLI/GUI, read-only NVML binding by UUID and
an optional conservative laptop profile.78 portable +27 GPU/integration tests
pass. Target-profile PTX checks on5090:30+45 arithmetic cases and6+9 official
proofs; no physical4090/2060 tests. See
[architecture/device results](docs/boris/ARCHITECTURES_AND_DEVICES.md) and
docs/boris/evidence/architectures/final-summary.json for current hashes.
Earlier tensor timing/full-grid duration reports retain their historical hashes.

GB10/Linux ARM64 preparation adds a native-only `gb10` build profile (sm121/PTX121),
seven Rust policy tests and nine pure GB10 safety/diagnostic tests. Compile-only
probes confirm the current CUDA12.4/Windows Rust toolchain cannot build that
target. `python3 -m boris_pearl.gb10_preflight` on the physical MSI/Spark is the
next read-only step; no root, kernels, mining or settings changes. Board/OEM
sensor, enforced power limit and unified-memory admission stay fail closed.
See [GB10 preparation](docs/boris/GB10_LINUX_ARM64.md) for exact blockers and a
build-only recipe. No GB10/Linux physical compatibility is claimed.

An original OpenCL1.2 scalar INT8 fixed-fixture laboratory now provides an
AMD-oriented portability route. It compiles and runs only in the measured
Windows RTX5090 validation:15 arithmetic matches,3 immutable golden/full
official proofs,6 negative verifier cases and3 stop scenarios PASS. These are
not AMD results. AMD dispatch stays blocked pending physical hardware/driver
and a reviewed AMD telemetry adapter; HIP compiler/runtime are absent.
Read-only inventory: `python -m boris_pearl.opencl_diagnose` (no context/kernel).
[AMD/OpenCL scope, licensing and blockers](docs/boris/AMD_OPENCL.md).
