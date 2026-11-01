#!/usr/bin/env python3
"""Turn pinned upstream fixture data into a bounded, dependency-free C++ KAT input."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def words(hexadecimal):
    value = bytes.fromhex(hexadecimal)
    if len(value) != 32:
        raise ValueError("Expected 32 fixture bytes")
    return "{" + ",".join(f"0x{int.from_bytes(value[i:i+4], 'little'):08x}u" for i in range(0, 32, 4)) + "}"


def main():
    checksums = {line.split(maxsplit=1)[1].lstrip("*"): line.split()[0]
                 for line in (FIXTURES / "SHA256SUMS").read_text().splitlines() if line.strip()}
    output = ["// Generated from pinned upstream golden data; do not hand edit.",
              "// Apache-2.0, upstream xXGuilasXx and contributors; see ../NOTICE.",
              "#pragma once", "#include <cstdint>",
              "struct GoldenTile { uint32_t row, col, transcript[16], digest[8]; };",
              "struct GoldenVector { uint64_t seed; uint32_t m,n,k; uint32_t a_seed[8], b_seed[8]; GoldenTile tiles[8]; };",
              "static const GoldenVector GOLDEN_VECTORS[] = {"]
    for seed in (1, 2, 3):
        name = f"golden-cpuref-{seed}.json"
        # Git on Windows can convert checked-out text to CRLF; upstream pins LF bytes.
        payload = (FIXTURES / name).read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(payload).hexdigest() != checksums.get(name):
            raise ValueError("Fixture checksum mismatch: " + name)
        f = json.loads(payload)
        if (f["format"] != "spm-cpuref-golden/1" or f["seed"] != seed or
                f["noise_rank"] != 128 or len(f["first_tiles"]) != 8 or
                not all(type(f[key]) is int and 0 < f[key] <= 4096 for key in ("m", "n", "k"))):
            raise ValueError("Unexpected fixture schema")
        output.append(f"// {name} sha256={checksums[name]}")
        output.append("{" + f"{seed}ull,{f['m']}u,{f['n']}u,{f['k']}u," + words(f["a_noise_seed"]) + "," + words(f["b_noise_seed"]) + ",{")
        for tile in f["first_tiles"]:
            transcript = tile["transcript"]
            if (len(transcript) != 16 or any(type(v) is not int or not 0 <= v < 2**32 for v in transcript) or
                    not 0 <= tile["t_rows"] + 56 < f["m"] or not 0 <= tile["t_cols"] + 57 < f["n"]):
                raise ValueError("Invalid golden tile bounds")
            values = "{" + ",".join(f"{v}u" for v in transcript) + "}"
            output.append("{" + f"{tile['t_rows']}u,{tile['t_cols']}u," + values + "," + words(tile["digest"]) + "},")
        output.append("}},")
    output.append("};")
    path = ROOT / "native" / "golden_vectors.h"
    path.write_text("\n".join(output) + "\n", encoding="ascii")
    print("Generated 24 pinned golden tile vectors:", path)


if __name__ == "__main__":
    main()
