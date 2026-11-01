// Boris Pearl deterministic arithmetic KAT, Apache-2.0. Artificial data only.
use crate::ffi;
use anyhow::{ensure, Result};
use serde_json::{json, Value};
use spm_cpuref::tile_from_rows;

fn random_byte(seed: &mut u64) -> i8 {
    *seed = seed.wrapping_add(0x9e3779b97f4a7c15);
    let mut x = *seed;
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d049bb133111eb);
    (x ^ (x >> 31)) as u8 as i8
}
pub fn run(v: &Value) -> Result<Value> {
    let device = v["device_index"].as_u64().unwrap_or(0);
    ensure!(device <= 31, "device index limit");
    let selected = v["backends"]
        .as_array()
        .ok_or_else(|| anyhow::anyhow!("explicit selftest backends required"))?;
    ensure!(
        !selected.is_empty() && selected.len() <= 3,
        "selftest backend count"
    );
    let mut checks = Vec::new();
    for backend in selected {
        let variant = match backend.as_str() {
            Some("dp4a") => 4,
            Some("wmma-int8") => 16,
            Some("mma-int8") => 32,
            _ => anyhow::bail!("unknown selftest backend"),
        };
        for k in [2048usize, 2176, 4096] {
            for pattern in 0..5u64 {
                let mut sa = 0x122334455667788u64 ^ pattern;
                let mut sb = 0x887766554433221u64 ^ pattern;
                let mut a: Vec<i8> = (0..8 * k).map(|_| random_byte(&mut sa)).collect();
                let mut b: Vec<i8> = (0..16 * k).map(|_| random_byte(&mut sb)).collect();
                if pattern == 0 {
                    a.fill(0);
                    b.fill(0);
                }
                if pattern == 1 {
                    for i in (0..a.len()).step_by(3) {
                        a[i] = -128;
                    }
                    for i in (1..b.len()).step_by(5) {
                        b[i] = 127;
                    }
                }
                if pattern == 2 {
                    for i in (0..a.len()).step_by(7) {
                        a[i] = 127;
                    }
                    for i in (0..b.len()).step_by(3) {
                        b[i] = -128;
                    }
                }
                if pattern == 3 {
                    a.fill(0);
                    b.fill(0);
                    for r in 0..8 {
                        a[r * k + (r * 31 + 15) % k] = -128;
                        a[r * k + (r * 31 + 16) % k] = 127;
                    }
                    for c in 0..16 {
                        b[c * k + (c * 17 + 31) % k] = -127;
                        b[c * k + (c * 17 + 32) % k] = 126;
                    }
                }
                let key =
                    std::array::from_fn(|i| (i as u8).wrapping_mul(13).wrapping_add(pattern as u8));
                let expected = tile_from_rows(0, 0, &a, &b, k, 128, &key)?;
                let gpu = ffi::tile(device as u32, &a, &b, k as u32, variant, &key)?;
                ensure!(
                    gpu.transcript == expected.transcript && gpu.digest == expected.digest,
                    "tensor selftest mismatch backend={backend} k={k} pattern={pattern}"
                );
                checks.push(
                    json!({"backend":backend,"k":k,"pattern":pattern,"active_ns":gpu.active_ns,
                    "digest":hex::encode(gpu.digest),"matched":true}),
                );
            }
        }
    }
    ffi::release();
    Ok(
        json!({"status":"passed","scope":"fixed signed arithmetic test; no job/search/network","checks":checks}),
    )
}
