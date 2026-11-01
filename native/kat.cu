// Boris Pearl prototype, 2026-10-31. New offline CUDA candidate, Apache-2.0.
// Synthetic 8x16 tile only: no pool, network, wallet, nonce search or mining loop.
// Reuses spm::b3 with its existing Pearl/Decred ISC notice; see ../NOTICE.
// This scalar kernel is a correctness baseline, not a competitive miner backend.
#include <cuda_runtime.h>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
#include "../cuda/common/blake3.cuh"
#include "../cuda/common/splitmix.cuh"
#include "../cuda/prep/prep.cuh"
#include "golden_vectors.h"

static FILE* fixture_export = nullptr;
static bool fixture_export_written = false;

__global__ void hash_kat(const uint32_t* input, uint32_t* output) {
  spm::b3::keyed_hash_one_block(input, input + 8, output);
}

__global__ void tile_kat(const int8_t* a, const int8_t* bt, int k,
                         const uint32_t* key, uint32_t* transcript, uint32_t* digest,
                         uint32_t row0, uint32_t col0, bool pattern_rows) {
  __shared__ uint32_t cells[128];
  const int cell = threadIdx.x;
  const uint32_t row = row0 + (cell / 16) * (pattern_rows ? 8 : 1);
  const uint32_t col = col0 + (pattern_rows ? (cell % 16 / 2) * 8 + cell % 2 : cell % 16);
  uint32_t acc = 0;
  if (cell == 0) for (int i = 0; i < 16; ++i) transcript[i] = 0;
  __syncthreads();
  for (int s = 0; s < k / 128; ++s) {
    for (int l = s * 128; l < (s + 1) * 128; ++l)
      acc += static_cast<uint32_t>(int32_t(a[row * k + l]) * int32_t(bt[col * k + l]));
    cells[cell] = acc;
    __syncthreads();
    if (cell == 0) {
      uint32_t fold = 0;
      for (int i = 0; i < 128; ++i) fold ^= cells[i];
      uint32_t word = transcript[s % 16];
      transcript[s % 16] = ((word << 13) | (word >> 19)) ^ fold;
    }
    __syncthreads();
  }
  if (cell == 0) spm::b3::keyed_hash_one_block(key, transcript, digest);
}

static bool checked(cudaError_t result, const char* operation) {
  if (result == cudaSuccess) return true;
  std::fprintf(stderr, "%s: %s\n", operation, cudaGetErrorString(result));
  return false;
}

static bool run_case(int k, int pattern) {
  std::vector<int8_t> a(8 * k), bt(16 * k);
  for (size_t i = 0; i < a.size(); ++i)
    a[i] = pattern == 0 ? 0 : static_cast<int8_t>(int((i * 31 + (i >> 3) * 7 + pattern * 19) % 255) - 127);
  for (size_t i = 0; i < bt.size(); ++i)
    bt[i] = static_cast<int8_t>(int((i * 17 + (i >> 5) * 13 + pattern * 23) % 255) - 127);
  uint32_t cpu[16] = {}, acc[8][16] = {}, key[8], expected[8], actual[24];
  for (int i = 0; i < 8; ++i) key[i] = 0x12345678u + uint32_t(i * 37);
  // Independently ordered CPU reference, mirroring spm-cpuref::tile_from_rows.
  for (int slice = 0; slice < k / 128; ++slice) {
    uint32_t fold = 0;
    for (int row = 0; row < 8; ++row) for (int col = 0; col < 16; ++col) {
      int32_t dot = 0;
      for (int l = 128 * slice; l < 128 * (slice + 1); ++l)
        dot += int32_t(a[row * k + l]) * int32_t(bt[col * k + l]);
      acc[row][col] += static_cast<uint32_t>(dot);
      fold ^= acc[row][col];
    }
    uint32_t v = cpu[slice % 16];
    cpu[slice % 16] = ((v << 13) | (v >> 19)) ^ fold;
  }
  spm::b3::keyed_hash_one_block(key, cpu, expected);
  int8_t *da = nullptr, *db = nullptr;
  uint32_t* state = nullptr;
  bool ok = checked(cudaMalloc(&da, a.size()), "allocate A") &&
            checked(cudaMalloc(&db, bt.size()), "allocate B") &&
            checked(cudaMalloc(&state, 32 * sizeof(uint32_t)), "allocate state");
  if (ok) ok = checked(cudaMemcpy(da, a.data(), a.size(), cudaMemcpyHostToDevice), "upload A") &&
               checked(cudaMemcpy(db, bt.data(), bt.size(), cudaMemcpyHostToDevice), "upload B") &&
               checked(cudaMemcpy(state, key, sizeof(key), cudaMemcpyHostToDevice), "upload key");
  if (ok) {
    tile_kat<<<1, 128>>>(da, db, k, state, state + 8, state + 24, 0, 0, false);
    ok = checked(cudaGetLastError(), "launch") && checked(cudaDeviceSynchronize(), "synchronize") &&
         checked(cudaMemcpy(actual, state + 8, sizeof(actual), cudaMemcpyDeviceToHost), "read result");
  }
  if (ok) {
    for (int i = 0; i < 16; ++i) if (actual[i] != cpu[i]) ok = false;
    for (int i = 0; i < 8; ++i) if (actual[16 + i] != expected[i]) ok = false;
  }
  cudaFree(da); cudaFree(db); cudaFree(state);
  std::printf("K=%d pattern=%d transcript+digest=%s\n", k, pattern, ok ? "PASS" : "FAIL");
  return ok;
}

static bool official_hash_vector() {
  // Public test data: BLAKE3-team/BLAKE3/test_vectors/test_vectors.json, input_len=64.
  // Check the first 32 bytes of keyed_hash; no third-party test implementation copied.
  const char* key = "whats the Elvish word for friend";
  constexpr char expected[] = "ba8ced36f327700d213f120b1a207a3b8c04330528586f414d09f2f7d9ccb7e6";
  static_assert(sizeof(expected) == 65, "Known digest must contain exactly 32 bytes");
  uint32_t input[24] = {}, host[8] = {}, device[8] = {};
  for (int i = 0; i < 32; ++i) input[i / 4] |= uint32_t(uint8_t(key[i])) << (8 * (i % 4));
  for (int i = 0; i < 64; ++i) input[8 + i / 4] |= uint32_t(i) << (8 * (i % 4));
  spm::b3::keyed_hash_one_block(input, input + 8, host);
  uint32_t *in = nullptr, *out = nullptr;
  bool ok = checked(cudaMalloc(&in, sizeof(input)), "hash allocation") &&
            checked(cudaMalloc(&out, sizeof(device)), "hash output allocation");
  if (ok) ok = checked(cudaMemcpy(in, input, sizeof(input), cudaMemcpyHostToDevice), "hash upload");
  if (ok) {
    hash_kat<<<1, 1>>>(in, out);
    ok = checked(cudaGetLastError(), "hash launch") && checked(cudaDeviceSynchronize(), "hash sync") &&
         checked(cudaMemcpy(device, out, sizeof(device), cudaMemcpyDeviceToHost), "hash read");
  }
  char host_hex[65] = {}, device_hex[65] = {};
  for (int i = 0; i < 32; ++i) {
    std::sprintf(host_hex + 2 * i, "%02x", (host[i / 4] >> (8 * (i % 4))) & 255);
    std::sprintf(device_hex + 2 * i, "%02x", (device[i / 4] >> (8 * (i % 4))) & 255);
  }
  ok = ok && std::strcmp(host_hex, expected) == 0 && std::strcmp(device_hex, expected) == 0;
  cudaFree(in); cudaFree(out);
  std::printf("BLAKE3 official keyed vector64 host+device=%s\n", ok ? "PASS" : "FAIL");
  return ok;
}

static bool golden_case(const GoldenVector& g) {
  // Upstream CPU-oracle fixtures, with its official-reference cross-check described
  // in crates/spm-cpuref/tests/golden.rs. No full proof/consensus verifier is run here.
  int8_t *a = nullptr, *bt = nullptr, *af = nullptr, *bf = nullptr;
  uint8_t *ap = nullptr, *bp = nullptr;
  uint32_t* state = nullptr;
  cudaEvent_t begin = nullptr, end = nullptr;
  bool ok = checked(cudaMalloc(&a, uint64_t(g.m) * g.k), "golden A") &&
            checked(cudaMalloc(&bt, uint64_t(g.n) * g.k), "golden B") &&
            checked(cudaMalloc(&af, uint64_t(g.m) * 128), "golden A factors") &&
            checked(cudaMalloc(&bf, uint64_t(g.n) * 128), "golden B factors") &&
            checked(cudaMalloc(&ap, g.k * 2), "golden A pairs") &&
            checked(cudaMalloc(&bp, g.k * 2), "golden B pairs") &&
            checked(cudaMalloc(&state, 32 * sizeof(uint32_t)), "golden state") &&
            checked(cudaEventCreate(&begin), "golden begin event") &&
            checked(cudaEventCreate(&end), "golden end event");
  spm::prep::Seed sa, sb;
  for (int i = 0; i < 8; ++i) { sa.w[i] = g.a_seed[i]; sb.w[i] = g.b_seed[i]; }
  if (ok) ok = checked(spm::prep::launch_uniform_factor(af, g.m, sa, spm::LABEL_A_W0, nullptr), "A factor") &&
               checked(spm::prep::launch_uniform_factor(bf, g.n, sb, spm::LABEL_B_W0, nullptr), "B factor") &&
               checked(spm::prep::launch_pairs(ap, g.k, sa, spm::LABEL_A_W0, nullptr), "A pairs") &&
               checked(spm::prep::launch_pairs(bp, g.k, sb, spm::LABEL_B_W0, nullptr), "B pairs");
  spm::prep::OperandSource as = {nullptr, g.seed ^ spm::DOMAIN_A, nullptr, 0};
  spm::prep::OperandSource bs = {nullptr, g.seed ^ spm::DOMAIN_BT, nullptr, 0};
  if (ok) ok = checked(spm::prep::launch_noised_operand(a, as, af, ap, g.m, g.k, false, nullptr), "noised A") &&
               checked(spm::prep::launch_noised_operand(bt, bs, bf, bp, g.n, g.k, false, nullptr), "noised B") &&
               checked(cudaMemcpy(state, g.a_seed, sizeof(g.a_seed), cudaMemcpyHostToDevice), "golden key");
  for (const auto& tile : g.tiles) {
    if (!ok) break;
    if (!checked(cudaEventRecord(begin), "record begin")) { ok = false; break; }
    tile_kat<<<1, 128>>>(a, bt, g.k, state, state + 8, state + 24, tile.row, tile.col, true);
    uint32_t result[24];
    float kernel_ms = 0;
    bool tile_ok = checked(cudaGetLastError(), "golden launch") &&
                   checked(cudaEventRecord(end), "record end") &&
                   checked(cudaDeviceSynchronize(), "golden sync") &&
                   checked(cudaEventElapsedTime(&kernel_ms, begin, end), "device time") &&
                   checked(cudaMemcpy(result, state + 8, sizeof(result), cudaMemcpyDeviceToHost), "golden result");
    if (tile_ok) {
      for (int i = 0; i < 16; ++i) if (result[i] != tile.transcript[i]) tile_ok = false;
      for (int i = 0; i < 8; ++i) if (result[16 + i] != tile.digest[i]) tile_ok = false;
    }
    std::printf("golden seed=%llu K=%u row=%u col=%u fill+noise+transcript+digest=%s\n",
                static_cast<unsigned long long>(g.seed), g.k, tile.row, tile.col, tile_ok ? "PASS" : "FAIL");
    if (tile_ok && fixture_export && !fixture_export_written && g.seed == 1) {
      std::fprintf(fixture_export, "{\"mode\":\"fixed-cuda-fixture\",\"fixture\":1,\"tile\":0,\"row\":%u,\"col\":%u,\"tile_kernel_ns\":%llu,\"transcript\":[",
                   tile.row, tile.col, static_cast<unsigned long long>(kernel_ms * 1000000.0));
      for (int i = 0; i < 16; ++i) std::fprintf(fixture_export, "%s%u", i ? "," : "", result[i]);
      std::fprintf(fixture_export, "],\"digest\":\"");
      for (int i = 0; i < 32; ++i) std::fprintf(fixture_export, "%02x", (result[16 + i / 4] >> (8 * (i % 4))) & 255);
      std::fprintf(fixture_export, "\"}\n");
      fixture_export_written = true;
    }
    ok = tile_ok && ok;
  }
  cudaFree(a); cudaFree(bt); cudaFree(af); cudaFree(bf); cudaFree(ap); cudaFree(bp); cudaFree(state);
  if (begin) cudaEventDestroy(begin);
  if (end) cudaEventDestroy(end);
  return ok;
}

int main(int argc, char** argv) {
  if (argc != 1 && (argc != 3 || std::strcmp(argv[1], "--export-fixed-fixture") != 0)) {
    std::fputs("Only no-argument KAT or --export-fixed-fixture OUTPUT are allowed.\n", stderr);
    return 2;
  }
  if (argc == 3) {
    fixture_export = std::fopen(argv[2], "wb");
    if (!fixture_export) return 2;
  }
  std::puts("OFFLINE KAT ONLY. No pool connection or mining.");
  int count = 0;
  if (!checked(cudaGetDeviceCount(&count), "enumerate") || count == 0) return 2;
  cudaDeviceProp properties;
  if (!checked(cudaGetDeviceProperties(&properties, 0), "properties")) return 2;
  std::printf("device=%s cc=%d.%d\n", properties.name, properties.major, properties.minor);
  if (properties.major < 7 || (properties.major == 7 && properties.minor < 5)) return 2;
  bool ok = true;
  if (fixture_export) {
    ok = golden_case(GOLDEN_VECTORS[0]);
    ok = ok && fixture_export_written && !std::ferror(fixture_export);
    if (std::fclose(fixture_export) != 0) ok = false;
  } else {
    ok = official_hash_vector();
    for (int k : {128, 256, 2176, 4096}) for (int pattern : {0, 1, 2})
      ok = run_case(k, pattern) && ok;
    for (const auto& g : GOLDEN_VECTORS) ok = golden_case(g) && ok;
  }
  cudaDeviceReset();
  return ok ? 0 : 1;
}
