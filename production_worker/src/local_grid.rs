// Bounded full-grid artificial experiment only, Apache-2.0. No network/live job API.
use crate::{ffi, host, number};
use anyhow::{ensure, Result};
use serde_json::{json, Value};
use spm_pow::U256;
use spm_work::{Shape, WorkUnit};
use std::time::Instant;
use zk_pow::api::{proof::SeedDerivation, verify::verify_plain_proof};

pub struct LocalGrid {
    host: host::JobHost,
    wu: WorkUnit,
    nonce: u64,
    cursor: u64,
    executed: u64,
    checks: u64,
    audit_count: u64,
    prepared: bool,
    check_all: bool,
    started: Instant,
    checksum: blake3::Hasher,
}
fn coordinates(cursor: u64, n: u32) -> (u32, u32) {
    let columns = u64::from(n / 64 * 4);
    let ri = ((cursor / (columns * 2)) * 2 + cursor % 2) as u32;
    let ci = ((cursor / 2) % columns) as u32;
    ((ri / 8) * 64 + ri % 8, (ci / 4) * 64 + (ci % 4) * 2)
}
impl LocalGrid {
    pub fn prepare(v: &Value) -> Result<(Self, Value)> {
        let started = Instant::now();
        let fields = v
            .as_object()
            .ok_or_else(|| anyhow::anyhow!("grid prepare must be an object"))?;
        ensure!(
            fields
                .keys()
                .all(|k| ["size", "device_index", "check_all"].contains(&k.as_str())),
            "no external header, target, seed, nonce or cursor is accepted"
        );

        let size = u32::try_from(number(v, "size")?)?;
        ensure!(
            [64, 512, 131072].contains(&size),
            "fixed artificial grid sizes only"
        );
        let device = u32::try_from(number(v, "device_index")?)?;
        ensure!(device <= 31, "device index limit");
        let check_all = v
            .get("check_all")
            .map(|x| {
                x.as_bool()
                    .ok_or_else(|| anyhow::anyhow!("check_all must be bool"))
            })
            .transpose()?
            .unwrap_or(false);
        ensure!(
            !check_all || size <= 512,
            "all-CPU comparison only on small fixtures"
        );
        let mut header = [0u8; 76];
        header[..4].copy_from_slice(&0x20000000u32.to_le_bytes());
        header[4..36].fill(0x4b);
        header[36..68].fill(0x41);
        header[68..72].copy_from_slice(&0x66666666u32.to_le_bytes());
        header[72..76].copy_from_slice(&0x1c7fffffu32.to_le_bytes());
        let job = spm_proto::Job {
            job_id: "fixed-full-grid-local-test".into(),
            header,
            target: U256::from(1),
            height: None,
            diff: None,
            cert_version: Some(3),
        };
        let wu = WorkUnit::build(
            &job,
            Shape {
                m: size,
                n: size,
                k: 4096,
                r: 128,
            },
            1,
            1,
        )?;
        let host = host::JobHost::new(&wu)?;
        let uuid = ffi::identity(device)?;
        ffi::grid_allocate(device, size)?;
        let response = json!({"mode":"full-grid-local-test","device_uuid":uuid,"tiles_per_nonce":host.tiles(),
            "shape":[size,size,4096],"prepare_wall_seconds":started.elapsed().as_secs_f64(),"header76":hex::encode(header)});
        Ok((
            Self {
                host,
                wu,
                nonce: crate::LOCAL_NONCE,
                cursor: 0,
                executed: 0,
                checks: 0,
                audit_count: 0,
                prepared: false,
                check_all,
                started,
                checksum: blake3::Hasher::new(),
            },
            response,
        ))
    }
    pub fn chunk(&mut self, v: &Value) -> Result<Value> {
        let tiles = self.host.tiles();
        let limit = tiles + tiles.min(4096);
        ensure!(
            self.started.elapsed().as_secs() < 1800 && self.executed < limit,
            "full-grid local test exhausted"
        );
        let budget = number(v, "budget_ns")?;
        ensure!(
            (100_000_000..=1_000_000_000).contains(&budget),
            "grid budget100ms..1s"
        );
        let requested = number(v, "max_tiles")?;
        ensure!(
            (16..=4096).contains(&requested) && requested % 16 == 0,
            "local grid batch16..4096 in steps16"
        );
        let new_nonce = self.cursor == tiles;
        if new_nonce {
            self.nonce += 1;
            self.cursor = 0;
        }
        let count = requested
            .min(tiles - self.cursor)
            .min(limit - self.executed) as usize;
        let att = self.host.attempt(self.nonce);
        let prefix = att.prefix();
        let (results, timing) = ffi::grid_chunk(
            self.cursor,
            count as u32,
            self.host.gen_seed(),
            &att.a_noise_seed,
            &self.host.b_noise_seed(),
            &prefix,
            !self.prepared || new_nonce,
            !self.prepared,
        )?;
        ensure!(
            timing.active_ns <= budget,
            "grid exceeded reserved device time"
        );
        let mut audits = Vec::new();
        for i in 0..count {
            let words = &results[i * 24..(i + 1) * 24];
            let mut digest = [0u8; 32];
            for (dst, word) in digest.chunks_exact_mut(4).zip(&words[16..]) {
                dst.copy_from_slice(&word.to_le_bytes());
            }
            ensure!(
                U256::from_little_endian(&digest) > self.wu.share_bound(),
                "unexpected artificial hard-target hit; stop, never submit"
            );
            let position = self.cursor + i as u64;
            if self.check_all || i == 0 || i + 1 == count {
                let (tr, tc) = coordinates(position, self.wu.shape.n);
                let cpu = self.host.tile(&att, tr, tc)?;
                ensure!(
                    cpu.transcript == words[..16] && cpu.digest == digest,
                    "resident grid CPU canary mismatch at {position}"
                );
                self.checks += 1;
                if position == 0 || position + 1 == tiles {
                    let proof = self.host.proof(&att, tr, tc)?;
                    let easy_bits = spm_pow::compact_from_target(U256::MAX >> 19);
                    verify_plain_proof(
                        self.host.header(),
                        &proof,
                        Some(easy_bits),
                        SeedDerivation::Salted,
                    )?;
                    audits.push(json!({"mode":"worker-proof","certificate_version":3,"header76":hex::encode(self.wu.header),
                        "nbits_override":easy_bits,"expected_digest":hex::encode(digest),"proof":proof}));
                    self.audit_count += 1;
                }
            }
        }
        // One bulk hash preserves identical little-endian bytes without millions of tiny update calls.
        let wire: Vec<u8> = results.iter().flat_map(|word| word.to_le_bytes()).collect();
        self.checksum.update(&wire);
        let start = self.cursor;
        self.cursor += count as u64;
        self.executed += count as u64;
        self.prepared = true;
        Ok(
            json!({"active_ns":timing.active_ns,"device_bytes":timing.device_bytes,"tiles_this_chunk":count,
            "executed_tiles":self.executed,"nonce":self.nonce,"next_cursor":self.cursor,"chunk_start":start,
            "tiles_per_nonce":tiles,"nonces_fully_scanned":self.executed/tiles,"cpu_checks":self.checks,
            "all_tiles_cpu_checked":self.check_all,"checksum":self.checksum.finalize().to_hex().to_string(),
            "audits":audits,"audit_count":self.audit_count,"complete":self.executed==limit,
            "mode":"full-grid-local-test"}),
        )
    }
}
impl Drop for LocalGrid {
    fn drop(&mut self) {
        ffi::grid_release();
    }
}
