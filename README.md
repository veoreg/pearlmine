# PearlMine

Open miner for [Pearl (PRL)](https://github.com/pearl-research-labs/pearl) with a simple desktop panel: your cards, temperature, power, speed and the balance credited by the pool, in one window. Early release; updates will follow.

Открытый майнер Pearl (PRL) с простой панелью: карты, температура, мощность, скорость и баланс на пуле в одном окне. Ранний выпуск, дальше будут обновления.

## What works today

| Card | System | Status | Speed |
|---|---|---|---|
| RTX 5090 | Linux | Mined on a real pool (Kryptex), 30 minutes: 61 shares accepted, 0 rejected | **281 TH/s** at 575 W, 72 °C |
| RTX 5090 | Windows | Short session on a real pool, longer ones on a local test pool | 150-160 TH/s in a session, often limited by heat; 240 TH/s in short benchmarks |
| NVIDIA DGX Spark (GB10) | Linux | 10 minutes on a real pool: 4 shares accepted, 0 rejected | about 60 TH/s at 56 W |
| RTX 4090 and other cards | | Not supported yet | |

**This is not the fastest Pearl miner yet.** Other miners publish 392-418 TH/s for the RTX 5090. Catching up is the next goal, and every number above will be updated from real pool results only.

## Start (Windows x64)

1. Download `PearlMine-…-win-x64.zip` from [Releases](https://github.com/veoreg/pearlmine/releases/latest) and unpack it to a folder you can write to.
2. Open `PearlMine.exe`.
3. In **Кошелёк и пул**, enter your public PRL address and pool, then save. Only the public address is needed: **never enter a seed phrase or private key.**
4. On **Обзор**, tick a card, choose how long to mine, press **Начать майнинг** and confirm.

The panel is in Russian for now. It tells you when a new version is available; it downloads and installs nothing by itself.

## Developer fee

The default is **2% of active GPU time**, shown before every start and changeable in the settings (0%, 1.5% or 2%). It is paid in rare turns of about two minutes, because changing jobs costs preparation time. Fee time was verified on a local test pool at about 1.8%, not yet on a real pool or over a long run. Your pool's own fee is separate. Cards started through the remote service on Linux currently pay no fee.

## Safety

- Mining starts only when you press the button. No hidden services or autostart.
- It slows down from 75 °C, rests at 80 °C and stops at 83 °C.
- On DGX Spark it stops at 85 W, because the machine powers off under long loads of 88-92 W.
- The panel reads your balance from the pool's public page using your public address. Mining and that read are the only pool traffic. It also asks GitHub for the latest version number.
- Mining heats the card and uses electricity. Whether it pays depends on your power price and the coin's price. Nothing here is financial advice.

## Several computers

Machines you own can be added under **Компьютеры**, over your own SSH key or with a pairing code from `PearlMine.exe --agent` on a private network. There is no shared or developer access to anyone's computer.

## Build from source

Needs Rust, Python 3.11+ with Tkinter, MSVC on Windows and the CUDA 13 toolkit (`tools/pearlmine-build-tma.cmd`), then `pyinstaller packaging/pearlmine.spec`. Tests: `python -X utf8 -m unittest discover -s tests_portable`. Measurements and what is planned next: [docs/boris/SPEED_GATE.md](docs/boris/SPEED_GATE.md). The internal package name `boris_pearl` is historical.

## License and credits

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). Based on [spark-pearl-miner](https://github.com/xXGuilasXx/spark-pearl-miner) by xXGuilasXx and contributors (its original README is in [docs/upstream/README.md](docs/upstream/README.md); its programs and their fee are kept as reference and are not used by PearlMine) and on the official Pearl verifier. Windows releases include the NVIDIA CUDA runtime, under NVIDIA's terms. Not affiliated with or endorsed by Pearl Research Labs, Kryptex or NVIDIA.
