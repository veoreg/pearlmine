"""Offline dense PlainProof assembly. This is NOT the official consensus verifier.

The Python Merkle code is an independent development implementation matching
the preserved Pearl tree layout. Every candidate still needs official verification.
Requires the optional, already installed `blake3` package for independent roots.
"""
from hashlib import sha256
import json
from pathlib import Path
import struct

MASK = 0xFFFFFFFF
IV = (0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A, 0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19)
PERM = (2, 6, 3, 10, 7, 0, 4, 13, 1, 11, 12, 5, 9, 14, 15, 8)
ROOT = Path(__file__).resolve().parent.parent


def digest(data, key=None):
    from blake3 import blake3
    return blake3(data, **({"key": key} if key is not None else {})).digest()


def compress(cv, block, counter, flags):
    """Full-block BLAKE3 compression for 1024-byte chunk CVs and parent nodes."""
    m = list(struct.unpack("<16I", block))
    v = list(cv) + list(IV[:4]) + [counter & MASK, counter >> 32, 64, flags]
    def g(a, b, c, d, x, y):
        v[a] = (v[a] + v[b] + x) & MASK
        z = v[d] ^ v[a]; v[d] = ((z >> 16) | (z << 16)) & MASK
        v[c] = (v[c] + v[d]) & MASK
        z = v[b] ^ v[c]; v[b] = ((z >> 12) | (z << 20)) & MASK
        v[a] = (v[a] + v[b] + y) & MASK
        z = v[d] ^ v[a]; v[d] = ((z >> 8) | (z << 24)) & MASK
        v[c] = (v[c] + v[d]) & MASK
        z = v[b] ^ v[c]; v[b] = ((z >> 7) | (z << 25)) & MASK
    for _ in range(7):
        g(0, 4, 8, 12, m[0], m[1]); g(1, 5, 9, 13, m[2], m[3])
        g(2, 6, 10, 14, m[4], m[5]); g(3, 7, 11, 15, m[6], m[7])
        g(0, 5, 10, 15, m[8], m[9]); g(1, 6, 11, 12, m[10], m[11])
        g(2, 7, 8, 13, m[12], m[13]); g(3, 4, 9, 14, m[14], m[15])
        m = [m[i] for i in PERM]
    return tuple(v[i] ^ v[i + 8] for i in range(8))


def chunk_cv(key, data, index):
    if len(data) != 1024:
        raise ValueError("Offline Merkle leaves must be exactly 1024 bytes")
    cv = struct.unpack("<8I", key)
    for i in range(16):
        cv = compress(cv, data[i * 64:(i + 1) * 64], index, 16 | (1 if i == 0 else 0) | (2 if i == 15 else 0))
    return struct.pack("<8I", *cv)


def parent(key, left, right, root=False):
    return struct.pack("<8I", *compress(struct.unpack("<8I", key), left + right, 0, 16 | 4 | (8 if root else 0)))


class MatrixTree:
    """Bounded development tree, matching Pearl's odd-node promotion and root flag."""
    def __init__(self, data, key):
        if len(key) != 32 or not data or len(data) % 1024 or len(data) > 2 * 1024 * 1024:
            raise ValueError("Expected a key and at most 2 MiB of whole fixture chunks")
        self.data, self.key = data, key
        self.layers = [[chunk_cv(key, data[i:i + 1024], i // 1024) for i in range(0, len(data), 1024)]]
        if len(self.layers[0]) == 1:
            self.root = digest(data, key)
        else:
            while len(self.layers[-1]) > 2:
                previous = self.layers[-1]
                self.layers.append([parent(key, previous[i], previous[i + 1]) if i + 1 < len(previous) else previous[i]
                                    for i in range(0, len(previous), 2)])
            self.root = parent(key, *self.layers[-1], root=True)
        if self.root != digest(data, key):
            raise ValueError("Independent BLAKE3 root check failed")

    def opening(self, rows, width):
        if (not rows or rows != sorted(set(rows)) or width <= 0 or
                any(type(row) is not int or row < 0 or (row + 1) * width > len(self.data) for row in rows)):
            raise ValueError("Invalid row opening")
        indices = sorted({i for row in rows for i in range(row * width // 1024, ((row + 1) * width - 1) // 1024 + 1)})
        current = set(indices)
        siblings = []
        count = len(self.layers[0])
        for layer in self.layers:
            if count <= 1:
                break
            for index in sorted(current):
                sibling = index ^ 1
                if sibling < count and sibling not in current:
                    siblings.append(list(layer[sibling]))
            current = {i // 2 for i in current}
            count = (count + 1) // 2
        return {"row_indices": rows, "proof": {
            "leaf_data": [list(self.data[i * 1024:(i + 1) * 1024]) for i in indices],
            "leaf_indices": indices, "total_leaves": len(self.layers[0]),
            "root": list(self.root), "siblings": siblings}}


def check_opening(opening, key, rows, width, total_rows):
    """Structural/Merkle precheck only; never a substitute for verify_plain_proof."""
    try:
        p = opening["proof"]
        required = sorted({i for row in rows for i in range(row * width // 1024, ((row + 1) * width - 1) // 1024 + 1)})
        if (opening["row_indices"] != rows or p["leaf_indices"] != required or
                p["total_leaves"] != total_rows * width // 1024 or len(p["leaf_data"]) != len(required) or
                len(p["siblings"]) > 256 or len(p["root"]) != 32):
            return False
        current = {i: chunk_cv(key, bytes(data), i) for i, data in zip(required, p["leaf_data"])}
        count = p["total_leaves"]
        siblings = iter(bytes(x) for x in p["siblings"])
        if count == 1:
            return not p["siblings"] and digest(bytes(p["leaf_data"][0]), key) == bytes(p["root"])
        while count > 2:
            following = {}
            for i in sorted(current):
                if i % 2 and i - 1 in current:
                    continue
                if i % 2:
                    following[i // 2] = parent(key, next(siblings), current[i])
                elif i + 1 in current:
                    following[i // 2] = parent(key, current[i], current[i + 1])
                elif i + 1 < count:
                    following[i // 2] = parent(key, current[i], next(siblings))
                else:
                    following[i // 2] = current[i]
            current, count = following, (count + 1) // 2
        left = current[0] if 0 in current else next(siblings)
        right = current[1] if 1 in current else next(siblings)
        computed = parent(key, left, right, root=True)
        return computed == bytes(p["root"]) and next(siblings, None) is None
    except (ValueError, TypeError, KeyError, StopIteration, IndexError):
        return False


def fill(seed, domain, size):
    mask = (1 << 64) - 1
    result = bytearray()
    for w in range((size + 7) // 8):
        z = ((seed ^ domain) + (w + 1) * 0x9E3779B97F4A7C15) & mask
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & mask
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & mask
        z ^= z >> 31
        result.extend(((b & 127) - 64) & 255 for b in z.to_bytes(8, "little"))
    return bytes(result[:size])


def fixture(seed=1):
    if type(seed) is not int or seed not in (1, 2, 3):
        raise ValueError("Only fixed offline golden fixtures 1, 2, 3 are allowed")
    name = f"golden-cpuref-{seed}.json"
    folder = ROOT / "tests" / "fixtures"
    pins = {line.split()[1]: line.split()[0] for line in (folder / "SHA256SUMS").read_text().splitlines()}
    data = (folder / name).read_bytes().replace(b"\r\n", b"\n")
    if sha256(data).hexdigest() != pins[name]:
        raise ValueError("Golden fixture checksum mismatch")
    return json.loads(data)


def u64(value):
    return struct.pack("<Q", value)


def bincode_proof(proof):
    """Candidate bincode 1.x encoding; official byte-for-byte confirmation pending."""
    out = bytearray(b"".join(u64(proof[k]) for k in ("m", "n", "k", "noise_rank")))
    for side in (proof["a"], proof["bt"]):
        p = side["proof"]
        out.extend(u64(len(p["leaf_data"])))
        for data in p["leaf_data"]:
            out.extend(u64(len(data))); out.extend(bytes(data))
        out.extend(u64(len(p["leaf_indices"])))
        out.extend(b"".join(u64(i) for i in p["leaf_indices"]))
        out.extend(u64(p["total_leaves"])); out.extend(bytes(p["root"]))
        out.extend(u64(len(p["siblings"]))); out.extend(b"".join(bytes(x) for x in p["siblings"]))
        out.extend(u64(len(side["row_indices"]))); out.extend(b"".join(u64(i) for i in side["row_indices"]))
    if proof["moe"] is not None:
        raise ValueError("Offline lab supports dense proofs only")
    out.append(0)
    return bytes(out)


def build_candidate(seed=1, tile_index=0):
    f = fixture(seed)
    if type(tile_index) is not int or not 0 <= tile_index < 8:
        raise ValueError("Select one of eight precomputed fixture tiles; no nonce search")
    key = digest(bytes.fromhex(f["header76"] + f["config52"]))
    if key.hex() != f["job_key"]:
        raise ValueError("Job key mismatch")
    a = MatrixTree(fill(seed, 0x73706D2D612D3031, f["m"] * f["k"]), key)
    b = MatrixTree(fill(seed, 0x73706D2D622D3031, f["n"] * f["k"]), key)
    if a.root.hex() != f["root_a"] or b.root.hex() != f["root_b"]:
        raise ValueError("Matrix roots differ from pinned golden data")
    bound_a = digest(a.root + struct.pack("<I", f["m"]) + bytes(28), digest(b"pearl/cert-v3/noise-seed/A"))
    bound_b = digest(b.root + struct.pack("<I", f["n"]) + bytes(28), digest(b"pearl/cert-v3/noise-seed/B"))
    b_seed = digest(key + bound_b)
    a_seed = digest(b_seed + bound_a)
    for name, value in (("bound_a", bound_a), ("bound_b", bound_b), ("a_noise_seed", a_seed), ("b_noise_seed", b_seed)):
        if value.hex() != f[name]:
            raise ValueError("V3 commitment/seed chain mismatch: " + name)
    tile = f["first_tiles"][tile_index]
    rows = [tile["t_rows"] + 8 * i for i in range(8)]
    cols = [tile["t_cols"] + 8 * (i // 2) + i % 2 for i in range(16)]
    proof = {k: f[k] for k in ("m", "n", "k", "noise_rank")}
    proof.update(a=a.opening(rows, f["k"]), bt=b.opening(cols, f["k"]), moe=None)
    if not check_opening(proof["a"], key, rows, f["k"], f["m"]) or not check_opening(proof["bt"], key, cols, f["k"], f["n"]):
        raise ValueError("Merkle precheck failed")
    return {"mode": "fixed-offline-proof-candidate", "certificate_version": 3,
            "header76": f["header76"], "nbits_override": 0x207FFFFF,
            "expected_digest": tile["digest"], "proof": proof,
            "official_verification": "not performed; must not submit"}


def dense_precheck(candidate):
    """Independently replay the opened dense strips; NOT an official verifier.

    This covers the local test proof's Merkle/seed/noise/transcript/difficulty
    path. The official bridge remains mandatory and fail-closed for acceptance.
    """
    try:
        p = candidate["proof"]
        m, n, k = (p[name] for name in ("m", "n", "k"))
        if (candidate["certificate_version"] != 3 or p["noise_rank"] != 128 or p["moe"] is not None or
                any(type(x) is not int or not 64 <= x <= 512 or x % 64 for x in (m, n)) or
                type(k) is not int or not 2048 <= k <= 4096 or k % 128):
            return False
        rows, cols = p["a"]["row_indices"], p["bt"]["row_indices"]
        if (len(rows) != 8 or len(cols) != 16 or not rows or not cols or
                rows != [rows[0] + 8 * i for i in range(8)] or rows[0] % 64 >= 8 or
                cols != [cols[0] + 8 * (i // 2) + i % 2 for i in range(16)] or
                cols[0] % 64 not in (0, 2, 4, 6) or rows[-1] >= m or cols[-1] >= n):
            return False
        config = struct.pack("<IHH", k, 128, 0) + bytes((7, 7, 0, 0, 0, 0, 0, 1, 3, 7, 0, 0)) + bytes(32)
        header = bytes.fromhex(candidate["header76"])
        if len(header) != 76:
            return False
        key = digest(header + config)
        if not check_opening(p["a"], key, rows, k, m) or not check_opening(p["bt"], key, cols, k, n):
            return False
        bound_a = digest(bytes(p["a"]["proof"]["root"]) + struct.pack("<I", m) + bytes(28), digest(b"pearl/cert-v3/noise-seed/A"))
        bound_b = digest(bytes(p["bt"]["proof"]["root"]) + struct.pack("<I", n) + bytes(28), digest(b"pearl/cert-v3/noise-seed/B"))
        b_seed = digest(key + bound_b)
        a_seed = digest(b_seed + bound_a)
        def noised(side, indices, seed, label):
            leaves = dict(zip(side["proof"]["leaf_indices"], map(bytes, side["proof"]["leaf_data"])))
            def noise_hash(index, slot):
                message = bytearray(64)
                message[slot * 4:slot * 4 + 4] = struct.pack("<I", index + 1)
                message[32:32 + len(label)] = label
                return digest(bytes(message), seed)
            pairs = []
            for group in range((k + 7) // 8):
                for x in struct.unpack("<8I", noise_hash(group, 1)):
                    index = x & 127
                    pairs.append((index, index ^ (1 + ((127 * x) >> 32))))
            result = []
            for row in indices:
                raw = b"".join(leaves[i] for i in range(row * k // 1024, (row + 1) * k // 1024))
                values = [x if x < 128 else x - 256 for x in raw]
                if any(not -64 <= x <= 64 for x in values):
                    raise ValueError("Opened matrix value is outside the consensus strip range")
                factor = [(x & 63) - 32 for block in range(4) for x in noise_hash(4 * row + block, 0)]
                result.append([x + factor[a] - factor[b] for x, (a, b) in zip(values, pairs)])
            return result
        a = noised(p["a"], rows, a_seed, b"A_tensor")
        b = noised(p["bt"], cols, b_seed, b"B_tensor")
        acc = [0] * 128
        transcript = [0] * 16
        for s in range(k // 128):
            span = slice(s * 128, (s + 1) * 128)
            fold = 0
            for i, ar in enumerate(a):
                for j, br in enumerate(b):
                    cell = i * 16 + j
                    acc[cell] = (acc[cell] + sum(x * y for x, y in zip(ar[span], br[span]))) & MASK
                    fold ^= acc[cell]
            word = transcript[s % 16]
            transcript[s % 16] = (((word << 13) | (word >> 19)) ^ fold) & MASK
        actual = digest(struct.pack("<16I", *transcript), a_seed)
        nbits = candidate["nbits_override"]
        if type(nbits) is not int or not 0 < nbits <= MASK or nbits & 0x800000:
            return False
        exponent, mantissa = nbits >> 24, nbits & 0x7FFFFF
        if not 1 <= exponent <= 32 or not mantissa:
            return False
        target = mantissa << (8 * (exponent - 3)) if exponent >= 3 else mantissa >> (8 * (3 - exponent))
        bound = min((1 << 256) - 1, target * 128 * k)
        return actual.hex() == candidate["expected_digest"] and int.from_bytes(actual, "little") <= bound
    except (ValueError, TypeError, KeyError, IndexError, struct.error):
        return False
