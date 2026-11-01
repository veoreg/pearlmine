# Actual verification Р В Р вЂ Р В РІР‚С™Р Р†Р вЂљРЎСљ 2026-10-31

Windows x64, physical RTX5090 CC12.0, driver617.42,32607MiB. Existing CUDA12.4.131,
MSVC14.38, Rust1.94, Python3.11/Tk used. No system/toolchain/driver installation.

| Check | Result/evidence |
|---|---|
| CPU/process/loopback | 97 PASS, evidence/amd/portable-tests.txt; fee/config,20k randomized steps, crash persistence, TLS/epochs, stale dispatch, sensor-child faults, Linux sysfs AC fixtures, pacing and normal clean-job retirement |
| Proof/official integration | 23 PASS, evidence/proof-lab/proof-cpu-tests.txt; all3 golden proofs accepted; malformed leaf/sibling/root/header/target/digest/cert/dimension cases rejected |
| Official Merkle library | 35 PASS, evidence/proof-lab/official-merkle-tests.txt; no upstream mine/search tests invoked |
| Native KAT | 37 PASS:12 scalar cases, independent keyed BLAKE3 vector,24 preserved golden GPU fill/noise/transcript/digest cases |
| Rust/CUDA build | PASS offline; sm75/sm89 + PTX75/80/89, actual5090 uses PTX JIT, no native sm120 claim |
| GPU/worker integration | 27 PASS, evidence/worker/fixed-gpu-tls-tests.txt: official proofs, full-size single tile, K2176/17-slice/non-power-of-two Merkle, strict test gates, user/developer/user return, cursor/checkpoint/eviction, clean-job transition, sensor/UUID/stop cases |
| Full-size fixed proof |131072x131072x4096 prepare + exactly one tile, official102193-byte proof accepted; next tile refused. evidence/worker/full-size-fixed-report.json |
| Canonical owner routing | User/developer/user via loopback TLS, all accepted by mocks, returned user produces a new canonical proof. No real pool contacted |
| Real sensors | Installed read-only NVML reports temperature/power/memory/utilization/AC; timeout-isolated child and GPU UUID match. No settings API used |
| Physical owned-process stops | Manual, in-flight batch, monotonic deadline without next tick, and actual sensor-child loss PASS. Worker reaped; fresh NVML compute PID absent where available. evidence/load/*stop.json |
| GPU pacing | DutyLimiter math/cancellation PASS; local test20% work duty,2 CPU threads, sensor thresholds, no hardware cap writes |
| Local timing | Warm fixed32-tile wall cycle ~3.93x faster, device events~1.34x. evidence/load/before-optimization.json and after-optimization.json; not mining throughput |
| Sustained artificial search | Monitored300s test, target1/marked header/m=n64/K4096, no network, bounded batch/nonce walk; see evidence/load/local-search-300s.json for final binary hash/metrics |
| GUI | Hidden full-window smoke + synthetic local Start/Stop/close lifecycle PASS; no real mining UI Start |
| Integrity |351 exact official source files; verifier202 registry archives/211 packages; worker243 archives/257 packages. Lock/source hashes and license metadata recorded |

Reproduce: tools/boris-proof-verify.py, tools/boris-worker-verify.py. The long
local run is explicit through `python -m boris_pearl local-search-test`; it is not
silently run by a quick test suite. All external pool connections and real
submissions remain zero. Official35 tests are Merkle-only, not mining tests.

The fee composition test credits5 seconds of synthetic prior ledger history to
admit developer work without a long fee experiment. The sustained local test is
0% with actual device-event accounting. No measured revenue split is claimed.
NVML power may be a1-second average;125W is a software stop threshold, not a
hardware power cap. WDDM process memory is unavailable; do not infer exact global
VRAM recovery from PID disappearance while other apps use the device.

Still not tested: real jobs/accepted shares/blocks/payouts, full-size full search,
long full-load thermal/OEM soak, full-size tensor throughput, Linux runtime/build,
physical GB10/4090/2060, notebook AC transition, sleep/resume/TDR and multi-GPU.
AMD has a separate scalar OpenCL fixed-fixture prototype, compiled/executed on NVIDIA only; no AMD live backend or physical qualification. Other models remain unqualified. No publishing/services,
autostart, wallet or persistent GPU/network settings operations were performed.

Historical DP4A-only binary dd908e47... local300s PASS:114689 tiles,3584 nonce transitions,587 real sensor samples,32C/80.009W max observed,18.4% software work duty under20% limit, worker reaped and absent from fresh NVML PID data. No unexpected safety pause, no external pool or real submit.

Linux environment probe: read-only `wsl --list --quiet` returned exit1, `Wsl/EnumerateDistros/Service/E_ACCESSDENIED`. Distribution inventory could not be obtained; no settings changed, no retry/bypass. Linux validation needs a separate authorized Linux system or WSL access. Windows development tests continued independently; Linux qualification remains blocked.

Latest tensor phase: binary89900562...;45 signed arithmetic comparisons and15
cross-backend full-proof cases pass.20 worker/integration tests include both
tensor paths' manual stop and actual telemetry-child loss. Same-binary7-round
warm timing: MMA3.14x device /1.34x checked wall vs DP4A; not mining hashrate.
MMA180s artificial run:96,097 tiles/3,003 nonce transitions/351 samples,
31C/78.545W max observed,18.3968% work duty; deadline reaped worker and fresh
NVML PID absent. See TENSOR_BACKENDS.md and evidence/tensor/final-summary.json.
The older300s DP4A report does not qualify the new tensor binary.

Resident-grid phase adds3 integration tests (23 total worker):4096 small-grid
tiles across two complete nonces all match original CPU generation/transcripts,
four independent official audits, strict rejection of external inputs/other
modes, and actual sensor-loss/manual stop. New full-size scan results, old
intentionally stopped prefixes and their distinct hashes are under
evidence/full-size. This experiment cannot receive a pool job or submit.

Full-size artificial result: one complete131072x131072x4096 nonce plus4096
next-nonce tiles (134,221,824 total),1273.188s at <=25% configured duty,
105,421.84 measured artificial tiles/wall-second.65,538 CPU canaries and3
independent102,193-byte easy-target audit proofs pass; owned worker reaped.
No real mining/share/credit. This resident path remains local-only.
See FULL_GRID_LOCAL.md and evidence/full-size/final-summary.json for exact
scope/hashes and the retained-window sensor limitation.

Architecture/device extension: named portable/turing75/ada89 build gates,
read-only CUDA descriptors and exact UUID-based NVML selection, GUI/CLI device
choice and explicit laptop guard.78 portable +27 GPU/integration tests pass;
Turing profile30 arithmetic/6 official proofs and Ada45/9 pass via forcedPTX
on the actual5090. No physical target-card or Linux execution, no real pool,
no system setting changes. See ARCHITECTURES_AND_DEVICES.md and
evidence/architectures/final-summary.json. Old full-grid receipts are archived
under completed-run-* and remain specific to their historical binary.

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

## Панель и удалённое управление, 01.11.2026

RTX 5090, Windows x64, искусственные задания, loopback. Worker `678f5f25…92c5` (по квитанции). Пул не подключался.

- 129 переносимых тестов прошли (`python -X utf8 -m unittest discover -s tests_portable`).
- Живой агент отдельным процессом и настоящий HTTPS, `tools/boris-agent-live-test.py --gpu`:

| | Из исходников | Самостоятельная сборка, Python убран из PATH |
|---|---|---|
| Тест продержался дольше аренды 10 с | да | да |
| Панель убита → работа остановлена | 8,6 с | 8,5 с |
| Агент убит → дочерние процессы исчезли | 0,2 с | 0,2 с |

- Окна открылись и закрылись штатно, лишних процессов нет: launcher, оба `.cmd`, самостоятельная сборка и её режим `--agent`.

**Не проверено:** два физических компьютера и настоящая VPN (шли только по 127.0.0.1); компьютер, где Python нет совсем
(сборка запускалась на машине, где он установлен, но без него в PATH); карты, кроме RTX 5090; Linux и Spark; настоящий пул.
