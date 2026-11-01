"""Real pinned official verifier integration. No GPU search, network or pool."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest

from boris_pearl.core import Job, Router, Session
from boris_pearl.official_bridge import OfficialVerifierCallback, ProofContext, VerificationBlocked, verify
from boris_pearl.proof_lab import ROOT, bincode_proof, build_candidate

sys.path.insert(0, str(ROOT / "tests_portable"))
from test_core import config

RECEIPT = ROOT / "docs/boris/evidence/proof-lab/official-build.json"
BINARY = ROOT / "proof_bridge/target/release" / ("boris-official-proof-check.exe" if sys.platform == "win32" else "boris-official-proof-check")


@unittest.skipUnless(BINARY.is_file() and RECEIPT.is_file(), "Build and record the pinned official verifier first")
class RealOfficialVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        receipt = json.loads(RECEIPT.read_text())
        cls.expected_hash = receipt["binary_sha256"]
        if sha256(BINARY.read_bytes()).hexdigest() != cls.expected_hash:
            raise RuntimeError("Official verifier binary differs from recorded audited build")
        cls.candidate = build_candidate(1, 0)

    def accepted(self, candidate):
        return verify(candidate, BINARY, self.expected_hash)

    def test_all_three_full_golden_proofs_verify_and_bincode_matches(self):
        for seed in (1, 2, 3):
            candidate = self.candidate if seed == 1 else build_candidate(seed, 0)
            result = self.accepted(candidate)
            self.assertEqual(result.wire, bincode_proof(candidate["proof"]))
            self.assertEqual(result.digest, candidate["expected_digest"])

    def test_tampered_matrix_leaf_sibling_and_root_are_rejected(self):
        for side, field in (("a", "leaf_data"), ("bt", "leaf_data"), ("a", "siblings"), ("bt", "root")):
            bad = deepcopy(self.candidate)
            part = bad["proof"][side]["proof"][field]
            if field == "root":
                part[0] ^= 1
            else:
                part[0][0] ^= 1
            with self.assertRaises(VerificationBlocked):
                self.accepted(bad)

    def test_wrong_header_difficulty_digest_and_certificate_are_rejected(self):
        original = self.candidate["header76"]
        altered = original[:136] + ("00" if original[136:138] != "00" else "01") + original[138:]
        for key, value in (("header76", altered), ("nbits_override", 0x01010000),
                           ("expected_digest", "00" * 32), ("certificate_version", 4)):
            with self.assertRaises(VerificationBlocked):
                self.accepted(dict(self.candidate, **{key: value}))

    def test_dimension_and_row_claims_cannot_be_retargeted(self):
        for field, value in (("m", 192), ("noise_rank", 256)):
            bad = deepcopy(self.candidate)
            bad["proof"][field] = value
            with self.assertRaises(VerificationBlocked):
                self.accepted(bad)
        bad = deepcopy(self.candidate)
        bad["proof"]["a"]["row_indices"] = [x + 1 for x in bad["proof"]["a"]["row_indices"]]
        with self.assertRaises(VerificationBlocked):
            self.accepted(bad)

    def test_missing_or_extra_merkle_components_are_rejected(self):
        for field in ("leaf_data", "leaf_indices", "siblings"):
            bad = deepcopy(self.candidate)
            bad["proof"]["a"]["proof"][field] = []
            with self.assertRaises(VerificationBlocked):
                self.accepted(bad)

    def test_real_official_callback_routes_only_original_session_and_deduplicates_wire(self):
        c = config()
        callback = OfficialVerifierCallback(BINARY, self.expected_hash)
        router = Router(c, callback)
        session = Session("offline-user", 1, "user", c.user_pool, c.user_wallet)
        router.authorize(session)
        job = Job(session, "fixed-golden-1", sha256(bytes.fromhex(self.candidate["header76"])).hexdigest())
        router.register(job)
        callback.bind(job, ProofContext(self.candidate["header76"], self.candidate["nbits_override"]))
        self.assertEqual(router.route(job, json.dumps(self.candidate).encode()), session)
        self.assertEqual(callback.verified_wire[job], bincode_proof(self.candidate["proof"]))
        with self.assertRaises(ValueError):
            router.route(job, json.dumps(self.candidate, indent=2).encode())


if __name__ == "__main__":
    unittest.main()
