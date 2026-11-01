#!/usr/bin/env python3
"""Capture reproducible offline checks; GPU KAT is opt-in and never mines."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def run(name, command, evidence):
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=45)
        record = {"command": command, "exit_code": result.returncode}
        output = result.stdout + result.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        record = {"command": command, "exit_code": None, "error": str(exc)}
        output = str(exc)
    (evidence / (name + ".txt")).write_text(output, encoding="utf-8")
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu-kat", action="store_true", help="run the separately built, bounded synthetic GPU test")
    args = parser.parse_args()
    evidence = ROOT / "docs" / "boris" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    from boris_pearl.backends import inventory
    detected = inventory()
    for device in detected["devices"]:
        device.pop("uuid", None)  # machine identity is unnecessary for published evidence
    report = {"time_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
              "system": platform.platform(), "inventory": detected, "checks": {},
              "scope": "offline prototype only; no pool connection, proof search or mining"}
    checks = report["checks"]
    checks["cpu"] = run("cpu-tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests_portable", "-v"], evidence)
    checks["configuration"] = run("default-configuration", [sys.executable, "-m", "boris_pearl", "check", "--config", "boris-config.example.json"], evidence)
    checks["simulation"] = run("default-simulation", [sys.executable, "-m", "boris_pearl", "simulate", "--seconds", "3600"], evidence)
    checks["worker_ipc"] = run("worker-ipc", [sys.executable, "-m", "boris_pearl", "worker-selftest"], evidence)
    if args.gpu_kat:
        binary = ROOT / "native" / "out" / ("boris-offline-kat.exe" if sys.platform == "win32" else "boris-offline-kat")
        if not binary.is_file():
            parser.error("Build native/kat.cu first; this tool never installs CUDA or builds a live miner")
        report["kat_binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        checks["gpu_kat"] = run("gpu-kat", [str(binary)], evidence)
        checks["cuda_targets"] = run("cuda-targets", ["cuobjdump", "--list-elf", str(binary)], evidence)
        checks["cuda_ptx"] = run("cuda-ptx", ["cuobjdump", "--list-ptx", str(binary)], evidence)
    report_path = evidence / "results.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for name, check in checks.items():
        print(f"{name}: {'PASS' if check['exit_code'] == 0 else 'FAIL'}")
    print(f"Evidence: {report_path}")
    return 0 if all(c["exit_code"] == 0 for c in checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
