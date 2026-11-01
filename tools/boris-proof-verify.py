#!/usr/bin/env python3
"""Reproduce audited offline build + official proof checks. Never downloads or mines."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/boris/evidence/proof-lab"
MANIFEST = "proof_bridge/local-build/Cargo.toml"


def run(name, command, env=None, timeout=45):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout, env=env)
    (OUT / (name + ".txt")).write_text(result.stdout + result.stderr, encoding="utf-8")
    return {"command": command, "exit_code": result.returncode}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "verification-results.json").write_text(json.dumps({
        "status":"incomplete", "time_utc":datetime.now(timezone.utc).isoformat(),
        "reason":"verification started; see individual logs if a prerequisite fails"}, indent=2) + "\n")
    checks = {}
    env = dict(os.environ, CARGO_HOME=str(ROOT / ".cargo-proof-offline"), CARGO_TARGET_DIR=str(ROOT / "proof_bridge/target"))
    checks["dependency_integrity"] = run("dependency-integrity-check", [sys.executable, "tools/audit-proof-deps.py"])
    if checks["dependency_integrity"]["exit_code"]:
        print("Dependency integrity failed or cache absent; no verifier executed")
        return 3
    cargo = ["cargo", "+stable"]
    checks["official_bridge_offline_build"] = run("official-build", cargo + ["build", "--locked", "--offline", "--release", "--manifest-path", MANIFEST], env, 180)
    if checks["official_bridge_offline_build"]["exit_code"]:
        print("Audited offline verifier build failed; see official-build.txt")
        return 3
    binary = ROOT / "proof_bridge/target/release" / ("boris-official-proof-check.exe" if sys.platform == "win32" else "boris-official-proof-check")
    integrity = json.loads((OUT / "dependency-integrity.json").read_text())
    checksum = hashlib.sha256(binary.read_bytes()).hexdigest()
    receipt = {"status": "built successfully offline", "source_revision": integrity["official_source_commit"],
               "manifest": MANIFEST, "lock_sha256": integrity["lock_sha256"], "binary_sha256": checksum,
               "rustc": subprocess.check_output(["rustc", "+stable", "--version"], text=True).strip(),
               "registry_archive_checksums_verified": integrity["registry_archives_verified"],
               "official_source_files_verified": integrity["official_source_files_verified"]}
    (OUT / "official-build.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    checks["portable_cpu_tests"] = run("portable-cpu-tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests_portable", "-v"])
    checks["proof_cpu_and_official_tests"] = run("proof-cpu-tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests_proof_lab", "-v"])
    checks["official_merkle_library_tests"] = run("official-merkle-tests", cargo + ["test", "--locked", "--offline", "--release", "--manifest-path", MANIFEST, "-p", "pearl-blake3", "--lib"], env)
    checks["bridge_source_syntax"] = run("bridge-rustfmt", ["rustfmt", "+stable", "--edition", "2021", "--check", "proof_bridge/src/main.rs"])
    checks["fixed_cuda_proof_pipeline"] = run("pipeline-command", [sys.executable, "-m", "boris_pearl", "proof-lab", "--output", str(OUT), "--verifier", str(binary), "--verifier-sha256", checksum])
    pipeline = json.loads((OUT / "pipeline-report.json").read_text())
    report = {"time_utc": datetime.now(timezone.utc).isoformat(), "checks": checks,
              "status":"passed" if pipeline["official_verification"] == "passed" and all(c["exit_code"] == 0 for c in checks.values()) else "failed or blocked",
              "official_acceptance": pipeline["official_verification"], "pipeline": pipeline,
              "scope": "fixed offline fixtures only, no pool, no search, no mining"}
    (OUT / "verification-results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for name, check in checks.items():
        print(name + ": exit " + str(check["exit_code"]))
    print("official_acceptance: " + pipeline["official_verification"])
    return 0 if pipeline["official_verification"] == "passed" and all(c["exit_code"] == 0 for c in checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
