"""Fixed offline CUDA result -> full dense proof candidate -> official verifier gate."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from .official_bridge import VerificationBlocked, verify
from .proof_lab import ROOT, bincode_proof, build_candidate, dense_precheck, fixture


def check_gpu_result(result):
    expected = fixture(1)["first_tiles"][0]
    if (result.get("mode") != "fixed-cuda-fixture" or type(result.get("fixture")) is not int or
            result["fixture"] != 1 or type(result.get("tile")) is not int or result["tile"] != 0 or
            type(result.get("row")) is not int or type(result.get("col")) is not int or
            not isinstance(result.get("transcript"), list) or
            any(type(x) is not int or not 0 <= x <= 0xFFFFFFFF for x in result["transcript"]) or
            result.get("row") != expected["t_rows"] or result.get("col") != expected["t_cols"] or
            result.get("transcript") != expected["transcript"] or result.get("digest") != expected["digest"] or
            type(result.get("tile_kernel_ns")) is not int or not 0 <= result["tile_kernel_ns"] <= 1_000_000_000):
        raise ValueError("CUDA result differs from the fixed offline fixture")


def run(output_dir, verifier=None, verifier_sha256=None):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    binary = ROOT / "native" / "out" / ("boris-offline-kat.exe" if sys.platform == "win32" else "boris-offline-kat")
    if not binary.is_file():
        raise VerificationBlocked("Build the bounded native CUDA test/fixture worker first")
    with tempfile.TemporaryDirectory(prefix="fixed-cuda-", dir=output) as tmp:
        result_path = Path(tmp) / "device.json"
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        completed = subprocess.run([str(binary), "--export-fixed-fixture", str(result_path.resolve())],
                                   capture_output=True, text=True, timeout=15, creationflags=flags)
        if completed.returncode != 0:
            raise RuntimeError("Fixed CUDA worker failed: " + completed.stderr[-1000:])
        result = json.loads(result_path.read_text())
        check_gpu_result(result)
    candidate = build_candidate(1, 0)
    if candidate["expected_digest"] != result["digest"]:
        raise ValueError("GPU digest is not bound to the assembled proof")
    if not dense_precheck(candidate):
        raise ValueError("Independent full dense-proof precheck failed; candidate must be discarded")
    wire = bincode_proof(candidate["proof"])
    (output / "proof-candidate.json").write_text(json.dumps(candidate, separators=(",", ":")) + "\n", encoding="utf-8")
    (output / "proof-candidate.bin").write_bytes(wire)
    (output / "gpu-fixture-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (output / "cuda-worker.txt").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    report = {"mode": "fixed offline fixture, no pool or nonce search", "proof_bytes": len(wire),
              "proof_sha256": sha256(wire).hexdigest(), "cuda_binary_sha256": sha256(binary.read_bytes()).hexdigest(),
              "gpu_digest": result["digest"], "merkle_precheck": "passed; independent implementation",
              "independent_dense_precheck": "passed; not official consensus verification",
              "official_verification": "blocked", "submitted": False,
              "difficulty_note": "0x207fffff is a test-only saturated override; never a real pool target"}
    try:
        if verifier is None or verifier_sha256 is None:
            raise VerificationBlocked("Official verifier binary not built: pinned Rust dependencies unavailable offline")
        accepted = verify(candidate, verifier, verifier_sha256)
        report["official_verification"] = "passed"
        report["official_wire_sha256"] = sha256(accepted.wire).hexdigest()
    except VerificationBlocked as exc:
        report["blocker"] = str(exc)
    (output / "pipeline-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
