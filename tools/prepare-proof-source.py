#!/usr/bin/env python3
"""Extract exact official Rust sources, excluding unrelated CUDA submodules."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tomllib

ROOT = Path(__file__).resolve().parent.parent
REV = "3fe226761a139a9652b8f28a6464a4bbc25986c8"
SOURCE = ROOT / ".cargo-proof-offline/git/checkouts/pearl-c97146df684ad884/3fe2267"
DEST = ROOT / "third_party/pearl-proof"


def main():
    revision = subprocess.check_output(["git", "-C", str(SOURCE), "rev-parse", "HEAD"], text=True).strip()
    if revision != REV:
        raise RuntimeError("Official checkout is not the pinned revision")
    archive = subprocess.check_output(["git", "-C", str(SOURCE), "archive", "--format=tar", REV,
                                       "LICENSE", "zk-pow", "pearl-blake3", "plonky2"])
    DEST.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for member in bundle.getmembers():
            parts = PurePosixPath(member.name)
            target = (DEST / member.name).resolve()
            if parts.is_absolute() or ".." in parts.parts or not target.is_relative_to(DEST.resolve()):
                raise RuntimeError("Archive path escapes the exact source destination")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.extractfile(member).read())
            else:
                raise RuntimeError("Unexpected link or special file in official Rust source")
    files = {p.relative_to(DEST).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(DEST.rglob("*")) if p.is_file() and p.name != "PROVENANCE.json"}
    tree = subprocess.check_output(["git", "-C", str(SOURCE), "rev-parse", REV + "^{tree}"], text=True).strip()
    (DEST / "PROVENANCE.json").write_text(json.dumps({"source": "https://github.com/pearl-research-labs/pearl",
         "commit": REV, "tree": tree, "source_archive_sha256": hashlib.sha256(archive).hexdigest(),
         "scope": ["LICENSE", "zk-pow", "pearl-blake3", "plonky2"], "files": files}, indent=2) + "\n", encoding="utf-8")
    dependencies = tomllib.loads((ROOT / "proof_bridge/Cargo.toml").read_text())["dependencies"]
    local = ROOT / "proof_bridge/local-build"
    local.mkdir(parents=True, exist_ok=True)
    lines = ['# Generated local build: exact verified Git archive; excludes irrelevant CUDA submodules.',
             '[package]', 'name = "boris-official-proof-check"', 'version = "0.1.0"',
             'edition = "2021"', 'license = "Apache-2.0"', '[workspace]',
             '[[bin]]', 'name = "boris-official-proof-check"', 'path = "../src/main.rs"',
             '[package.metadata.official-source]', f'commit = "{REV}"', f'tree = "{tree}"',
             '[dependencies]', 'zk-pow = { path = "../../third_party/pearl-proof/zk-pow" }']
    for name in ('serde', 'serde_json', 'bincode', 'hex', 'sha2'):
        value = dependencies[name]
        if isinstance(value, dict):
            lines.append(name + ' = { version = ' + json.dumps(value['version']) + ', features = ' + json.dumps(value['features']) + ' }')
        else:
            lines.append(name + ' = ' + json.dumps(value))
    lines.extend(('[profile.release]', 'opt-level = 2'))
    (local / 'Cargo.toml').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f"Extracted {len(files)} exact source files at {REV}; no CUDA submodules")


if __name__ == "__main__":
    main()
