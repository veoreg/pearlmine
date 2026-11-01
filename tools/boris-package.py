#!/usr/bin/env python3
"""Source-only snapshot with SHA256 manifest. Does not publish or build binaries."""
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", ".cargo", ".cargo-proof-offline", ".claude", "out", "target", "dist", "build", "state"}
SKIP_NAMES = {"boris-config.json", "boris-state.json", "pairing.json", "agent-key.pem", "agent-cert.pem", "settings.json", "settings.json.bad"}
SKIP_EXTENSIONS = {".exe", ".dll", ".lib", ".exp", ".o", ".a", ".cubin", ".ptx", ".fatbin", ".pyc"}


def main():
    files = []
    official_root = ROOT / 'third_party' / 'pearl-proof'
    provenance = official_root / 'PROVENANCE.json'
    official_paths = set()
    if provenance.is_file():
        import json
        official_paths = set(json.loads(provenance.read_text())['files']) | {'PROVENANCE.json'}
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if relative.parts[:2] == ('third_party', 'pearl-proof'):
            official_relative = path.relative_to(official_root).as_posix()
            if path.is_file() and official_relative in official_paths:
                files.append((path, relative.as_posix()))
            continue
        if (not path.is_file() or path.is_symlink() or any(p in SKIP_DIRS for p in relative.parts) or
                path.name in SKIP_NAMES or path.suffix.lower() in SKIP_EXTENSIONS):
            continue
        files.append((path, relative.as_posix()))
    destination = ROOT.parent / "pearlmine-0.4-source.zip"
    manifest = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {name}\n" for p, name in files)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, relative in files:
            archive.write(path, "boris-pearl-prototype/" + relative)
        archive.writestr("boris-pearl-prototype/SOURCE_SHA256SUMS", manifest)
    with zipfile.ZipFile(destination) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError("ZIP CRC failed: " + bad)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".zip.sha256").write_text(f"{digest}  {destination.name}\n", encoding="ascii")
    print(f"{destination}\nfiles={len(files)} bytes={destination.stat().st_size}\nsha256={digest}")


if __name__ == "__main__":
    main()
