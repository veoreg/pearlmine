"""Optional offline proof-lab checks. Requires installed blake3; no GPU/network."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import struct
import tempfile
import unittest

from boris_pearl.official_bridge import REVISION, OfficialVerifierCallback, ProofContext, VerificationBlocked, accept_response, request_bytes, verify
from boris_pearl.core import Job, Session
from boris_pearl.proof_lab import MatrixTree, bincode_proof, build_candidate, check_opening, dense_precheck, digest, fixture
from boris_pearl.proof_pipeline import check_gpu_result


class MerkleTests(unittest.TestCase):
    def test_odd_and_even_trees_match_independent_blake3(self):
        key = bytes(range(32))
        for chunks in (1, 2, 3, 5, 7, 10):
            data = bytes(i % 251 for i in range(chunks * 1024))
            tree = MatrixTree(data, key)
            self.assertEqual(tree.root, digest(data, key))
            rows = sorted({0, chunks // 2, chunks - 1})
            opening = tree.opening(rows, 1024)
            self.assertTrue(check_opening(opening, key, rows, 1024, chunks))

    def test_malformed_matrix_and_rows_rejected(self):
        for data in (b"", b"short", bytes(3 * 1024 * 1024)):
            with self.assertRaises(ValueError):
                MatrixTree(data, bytes(32))
        tree = MatrixTree(bytes(4 * 1024), bytes(32))
        for rows in ([], [1, 1], [3, 2], [-1], [4]):
            with self.assertRaises(ValueError):
                tree.opening(rows, 1024)


class FullProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate = build_candidate(1, 0)
        cls.key = bytes.fromhex(fixture(1)["job_key"])

    def test_complete_proof_has_both_matrix_openings_and_expected_size(self):
        p = self.candidate["proof"]
        self.assertEqual(len(p["a"]["row_indices"]), 8)
        self.assertEqual(len(p["bt"]["row_indices"]), 16)
        self.assertEqual(len(p["a"]["proof"]["leaf_data"]), 16)
        self.assertEqual(len(p["bt"]["proof"]["leaf_data"]), 32)
        self.assertEqual(len(bincode_proof(p)), 51697)
        self.assertEqual(bincode_proof(p)[:32], struct.pack("<4Q", 256, 256, 2048, 128))
        self.assertIn("must not submit", self.candidate["official_verification"])

    def test_full_roots_and_seed_chain_match_all_three_golden_fixtures(self):
        for seed in (1, 2, 3):
            candidate = self.candidate if seed == 1 else build_candidate(seed, 0)
            f = fixture(seed)
            self.assertEqual(bytes(candidate["proof"]["a"]["proof"]["root"]).hex(), f["root_a"])
            self.assertEqual(bytes(candidate["proof"]["bt"]["proof"]["root"]).hex(), f["root_b"])

    def test_tampered_leaf_sibling_root_rows_or_extra_path_is_rejected(self):
        original = self.candidate["proof"]["a"]
        mutations = []
        p = deepcopy(original); p["proof"]["leaf_data"][0][10] ^= 1; mutations.append(p)
        p = deepcopy(original); p["proof"]["siblings"][0][0] ^= 1; mutations.append(p)
        p = deepcopy(original); p["proof"]["root"][0] ^= 1; mutations.append(p)
        p = deepcopy(original); p["row_indices"][0] += 1; mutations.append(p)
        p = deepcopy(original); p["proof"]["siblings"].append([0] * 32); mutations.append(p)
        p = deepcopy(original); p["proof"]["leaf_indices"][0] += 1; mutations.append(p)
        for p in mutations:
            self.assertFalse(check_opening(p, self.key, original["row_indices"], 2048, 256))

    def test_wrong_key_and_total_shape_fail_merkle_precheck(self):
        a = self.candidate["proof"]["a"]
        self.assertFalse(check_opening(a, bytes(32), a["row_indices"], 2048, 256))
        self.assertFalse(check_opening(a, self.key, a["row_indices"], 2048, 512))

    def test_only_fixed_seed_and_precomputed_tile_inputs_allowed(self):
        for seed, tile in ((0, 0), (1, -1), (1, 8), (True, 0)):
            with self.assertRaises(ValueError):
                build_candidate(seed, tile)

    def test_independent_full_dense_replay_matches_gpu_golden_digest(self):
        self.assertTrue(dense_precheck(self.candidate))

    def test_dense_replay_rejects_wrong_header_digest_and_difficulty(self):
        for key, value in (("header76", "00" * 76), ("expected_digest", "00" * 32), ("nbits_override", 0x01010000)):
            self.assertFalse(dense_precheck(dict(self.candidate, **{key: value})))
        bad = deepcopy(self.candidate)
        bad["proof"]["bt"]["proof"]["leaf_data"][0][0] ^= 1
        self.assertFalse(dense_precheck(bad))


class OfficialBridgeProtocolTests(unittest.TestCase):
    """Mock response tests exercise binding only, never claim official verification."""
    @classmethod
    def setUpClass(cls):
        cls.candidate = build_candidate(1, 0)
        cls.payload = request_bytes(cls.candidate)
        cls.response = {"accepted": True, "protocol": "boris-official-verifier/1", "source_revision": REVISION,
                        "request_sha256": sha256(cls.payload).hexdigest(), "digest": cls.candidate["expected_digest"],
                        "proof_bincode_hex": bincode_proof(cls.candidate["proof"]).hex()}

    def test_absent_official_binary_blocks_instead_of_accepting(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(VerificationBlocked):
                verify(self.candidate, Path(tmp) / "absent.exe", "0" * 64)

    def test_unreviewed_binary_is_never_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unreviewed.exe"
            path.write_bytes(b"not a verifier")
            with self.assertRaises(VerificationBlocked):
                verify(self.candidate, path, "0" * 64)

    def test_bound_mock_response_carries_exact_wire(self):
        result = accept_response(self.candidate, self.payload, 0, json.dumps(self.response))
        self.assertEqual(result.wire, bincode_proof(self.candidate["proof"]))

    def test_false_true_string_wrong_source_hash_digest_wire_are_rejected(self):
        changes = (("accepted", False), ("accepted", "true"), ("source_revision", "wrong"),
                   ("request_sha256", "0" * 64), ("digest", "0" * 64), ("proof_bincode_hex", "00"))
        for key, value in changes:
            reply = dict(self.response, **{key: value})
            with self.assertRaises(VerificationBlocked):
                accept_response(self.candidate, self.payload, 0, json.dumps(reply))

    def test_failed_process_and_malformed_response_are_rejected(self):
        for code, response in ((1, json.dumps(self.response)), (0, "{}"), (0, "bad json"), (0, "[]")):
            with self.assertRaises(VerificationBlocked):
                accept_response(self.candidate, self.payload, code, response)

    def test_router_callback_binds_job_and_fails_closed_without_official_binary(self):
        job = Job(Session("offline-context", 1, "user", "unused", "unused"), "fixed-fixture",
                  sha256(bytes.fromhex(self.candidate["header76"])).hexdigest())
        with tempfile.TemporaryDirectory() as tmp:
            callback = OfficialVerifierCallback(Path(tmp) / "absent.exe", "0" * 64)
            wire = json.dumps(self.candidate).encode()
            self.assertIs(callback(job, wire), False)
            callback.bind(job, ProofContext(self.candidate["header76"], self.candidate["nbits_override"]))
            self.assertIs(callback(job, wire), False)
            self.assertIn("absent", callback.last_error)
            self.assertFalse(callback.verified_wire)

    def test_router_callback_rejects_retarget_and_context_replacement(self):
        job = Job(Session("offline-context", 1, "user", "unused", "unused"), "fixed-fixture",
                  sha256(bytes.fromhex(self.candidate["header76"])).hexdigest())
        callback = OfficialVerifierCallback("absent", "0" * 64)
        callback.bind(job, ProofContext(self.candidate["header76"], self.candidate["nbits_override"]))
        changed = dict(self.candidate, nbits_override=0x1D7FFFFF)
        self.assertIs(callback(job, json.dumps(changed).encode()), False)
        self.assertIn("target", callback.last_error)
        with self.assertRaises(ValueError):
            callback.bind(job, ProofContext(self.candidate["header76"], 0x1D7FFFFF))
        callback.retire(job)
        self.assertIs(callback(job, json.dumps(self.candidate).encode()), False)


class GpuEnvelopeTests(unittest.TestCase):
    def test_fixed_fixture_binding_rejects_wrong_digest_or_tile(self):
        tile = fixture(1)["first_tiles"][0]
        result = {"mode": "fixed-cuda-fixture", "fixture": 1, "tile": 0, "row": tile["t_rows"],
                  "col": tile["t_cols"], "tile_kernel_ns": 1000, "transcript": tile["transcript"], "digest": tile["digest"]}
        check_gpu_result(result)
        for key, value in (("digest", "0" * 64), ("tile", 1), ("fixture", True), ("tile_kernel_ns", -1)):
            with self.assertRaises(ValueError):
                check_gpu_result(dict(result, **{key: value}))


if __name__ == "__main__":
    unittest.main()
