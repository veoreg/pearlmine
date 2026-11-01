#!/usr/bin/env python3
"""Audited offline build + bounded fixed GPU fixtures + loopback TLS. No live mining."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/boris/evidence/worker"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "verification-results.json").write_text(json.dumps({
        "status":"incomplete", "time_utc":datetime.now(timezone.utc).isoformat(),
        "reason":"verification started; see individual logs if a prerequisite fails"}, indent=2) + "\n")
    env = dict(os.environ, CARGO_HOME=str(ROOT / ".cargo-proof-offline"),
               CARGO_TARGET_DIR=str(ROOT / "production_worker/target"),BORIS_CUDA_PROFILE="portable")
    checks = {}
    def run(name, command, timeout=60):
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout)
        (OUT / (name + ".txt")).write_text(result.stdout + result.stderr, encoding="utf-8")
        checks[name] = {"command":command, "exit_code":result.returncode}
        if name.endswith("tests") and result.returncode == 0 and "(skipped=" in result.stderr:
            checks[name]["original_exit_code"] = 0
            checks[name]["exit_code"] = 3
            checks[name]["status"] = "blocked: test prerequisites absent; skipped is not passed"
        return result
    metadata = run("cargo-metadata", ["cargo","+stable","metadata","--locked","--offline",
                       "--format-version","1","--manifest-path","production_worker/Cargo.toml"])
    if metadata.returncode:
        return 3
    (OUT / "cargo-metadata.json").write_text(metadata.stdout, encoding="utf-8")
    audit = run("integrity-check", [sys.executable,"tools/audit-proof-deps.py",
                    "--lock","production_worker/Cargo.lock","--output","docs/boris/evidence/worker"])
    if audit.returncode:
        return 3
    built = run("build-log", ["cargo","+stable","build","--locked","--offline","--release",
                             "--manifest-path","production_worker/Cargo.toml"], 180)
    if built.returncode:
        return 3
    binary = ROOT / "production_worker/target/release" / ("boris-pearl-worker.exe" if sys.platform == "win32" else "boris-pearl-worker")
    integrity = json.loads((OUT / "dependency-integrity.json").read_text())
    receipt = {"binary_sha256":hashlib.sha256(binary.read_bytes()).hexdigest(),
               "manifest":"production_worker/Cargo.toml","lock_sha256":integrity["lock_sha256"],
               "official_source_commit":integrity["official_source_commit"],
               "official_source_files_verified":integrity["official_source_files_verified"],
               "registry_archives_verified":integrity["registry_archives_verified"],
               "rustc":subprocess.check_output(["rustc","+stable","--version"], text=True).strip(),
               "nvcc":subprocess.check_output(["nvcc","--version"], text=True).strip(),
               "build_profile":"portable","scope":"DP4A/WMMA/MMA and isolated artificial resident grid; UUID-bound selection; sm75/sm89 + PTX75/80/89; no production qualification"}
    (OUT / "worker-build.json").write_text(json.dumps(receipt, indent=2) + "\n")
    run("portable-tests", [sys.executable,"-m","unittest","discover","-s","tests_portable","-v"])
    run("fixed-gpu-tls-tests", [sys.executable,"-m","unittest","discover","-s","tests_worker","-v"])
    run("rustfmt", ["rustfmt","+stable","--edition","2021","--check","production_worker/src/main.rs",
                    "production_worker/src/ffi.rs","production_worker/src/kernel_selftest.rs","production_worker/src/local_grid.rs","production_worker/build.rs","production_worker/build_plan.rs"])
    report = {"time_utc":datetime.now(timezone.utc).isoformat(), "checks":checks,
              "status":"passed" if all(v["exit_code"] == 0 for v in checks.values()) else "failed or blocked",
              "scope":"offline fixed fixtures and literal loopback TLS only",
              "live_mode_executions":0,"external_pool_connections":0,"real_submissions":0}
    (OUT / "verification-results.json").write_text(json.dumps(report, indent=2) + "\n")
    for key, value in checks.items():
        print(f"{key}: exit {value['exit_code']}")
    return 0 if all(v["exit_code"] == 0 for v in checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
