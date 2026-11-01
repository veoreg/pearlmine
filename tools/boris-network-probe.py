#!/usr/bin/env python3
"""Read-only dependency network diagnostics; never alters access/proxy settings."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tomllib
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "boris" / "evidence" / "dependency-access"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    probes = {}
    command = ["git", "ls-remote", "https://github.com/pearl-research-labs/pearl.git", "HEAD"]
    git = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=15)
    probes["official_git"] = {"command": command, "exit_code": git.returncode,
                              "output": git.stdout + git.stderr}
    for name, url in (("crates_index", "https://index.crates.io/config.json"),
                      ("crates_archive", "https://static.crates.io/crates/bincode/bincode-1.3.3.crate")):
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                data = response.read(2_000_000)
                probes[name] = {"url": url, "http_status": response.status,
                                "bytes": len(data), "download_sha256": hashlib.sha256(data).hexdigest()}
        except (OSError, urllib.error.URLError) as exc:
            reason = getattr(exc, "reason", exc)
            probes[name] = {"url": url, "status": "unreachable", "error": str(exc),
                            "error_type": type(reason).__name__, "errno": getattr(reason, "errno", None),
                            "winerror": getattr(reason, "winerror", None)}
    proxies = {}
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "GIT_HTTP_PROXY", "GIT_HTTPS_PROXY"):
        value = os.environ.get(name, "")
        try:
            parsed = urllib.parse.urlsplit(value)
            loopback = parsed.hostname in ("127.0.0.1", "localhost", "::1")
            proxies[name] = {"set": bool(value), "loopback": loopback,
                             "port": parsed.port if loopback else None}
        except ValueError:
            proxies[name] = {"set": bool(value), "parseable": False}
    lock_bytes = (ROOT / "Cargo.lock").read_bytes().replace(b"\r\n", b"\n")
    lock = tomllib.loads(lock_bytes.decode())
    names = {"zk-pow", "pearl-blake3", "serde", "serde_json", "bincode", "hex", "sha2"}
    dependencies = [{key: p[key] for key in ("name", "version", "source", "checksum") if key in p}
                    for p in lock["package"] if p["name"] in names]
    archive = probes["crates_archive"]
    if "download_sha256" in archive:
        expected = next(p["checksum"] for p in dependencies if p["name"] == "bincode" and p["version"] == "1.3.3")
        archive["matches_preserved_lock"] = archive["download_sha256"] == expected
    report = {"time_utc": datetime.now(timezone.utc).isoformat(), "probes": probes,
              "cargo_net_offline": os.environ.get("CARGO_NET_OFFLINE"), "proxy_summary": proxies,
              "source_lock_sha256_canonical_lf": hashlib.sha256(lock_bytes).hexdigest(),
              "expected_dependencies_from_preserved_lock": dependencies,
              "integrity_status": ("Only the bincode connectivity probe was read into memory; no dependency sources installed"
                                   if "download_sha256" in archive else
                                   "No dependency source/archive obtained; expected checksums are not download verification"),
              "access_changes": "none; no proxy/offline override and no escalation requested"}
    (OUT / "network-and-pins.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for name, probe in probes.items():
        print(name + ": " + (probe.get("status") or str(probe.get("exit_code", probe.get("http_status")))))
    print("CARGO_NET_OFFLINE=" + str(report["cargo_net_offline"]))
    print("No access settings changed. Report: " + str(OUT / "network-and-pins.json"))
    return 0 if (git.returncode == 0 and all("http_status" in probes[n] for n in ("crates_index", "crates_archive"))
                 and archive.get("matches_preserved_lock") is True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
