#!/usr/bin/env python3
"""Verify exact official-source bytes and registry archive SHA256 before building."""
from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/boris/evidence/proof-lab"


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--lock',default='proof_bridge/local-build/Cargo.lock')
    parser.add_argument('--output',default='docs/boris/evidence/proof-lab')
    args=parser.parse_args()
    output=ROOT / args.output
    output.mkdir(parents=True,exist_ok=True)
    official = ROOT / "third_party/pearl-proof"
    provenance = json.loads((official / "PROVENANCE.json").read_text())
    if provenance["commit"] != "3fe226761a139a9652b8f28a6464a4bbc25986c8":
        raise RuntimeError("Wrong official source revision")
    for relative, expected in provenance["files"].items():
        if hashlib.sha256((official / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError("Official source changed: " + relative)
    lock_path = ROOT / args.lock
    lock = tomllib.loads(lock_path.read_text())
    archives = []
    for package in lock["package"]:
        if package.get("source", "").startswith("registry+"):
            name = f"{package['name']}-{package['version']}.crate"
            matches = list((ROOT / ".cargo-proof-offline/registry/cache").glob("*/" + name))
            if len(matches) != 1:
                raise RuntimeError("Missing or ambiguous registry archive: " + name)
            actual = hashlib.sha256(matches[0].read_bytes()).hexdigest()
            if actual != package["checksum"]:
                raise RuntimeError("Registry archive checksum failed: " + name)
            archives.append({"name": package["name"], "version": package["version"], "sha256": actual})
    metadata = json.loads((output / "cargo-metadata.json").read_text(encoding="utf-8-sig"))
    packages = [{"name": p["name"], "version": p["version"], "license": p.get("license"),
                 "license_file": p.get("license_file"), "source": p.get("source"),
                 "rust_version": p.get("rust_version"),
                 "build_script": any(t["kind"] == ["custom-build"] for t in p["targets"])} for p in metadata["packages"]]
    report = {"official_source_commit": provenance["commit"], "official_source_files_verified": len(provenance["files"]),
              "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
              "registry_archives_verified": len(archives), "archives": archives,
              "packages": packages, "license_counts": dict(Counter(p["license"] or "see repository license" for p in packages))}
    (output / "dependency-integrity.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(provenance['files'])} official source files and {len(archives)} locked registry archive SHA256 checksums")


if __name__ == "__main__":
    main()
