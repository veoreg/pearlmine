// Boris Pearl offline bridge, Apache-2.0. Calls official unmodified V3 verifier.
// Built with exact unmodified official sources; audited results in proof_bridge/README.md.
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::io::{self, Read};
use zk_pow::api::proof::{IncompleteBlockHeader, SeedDerivation};
use zk_pow::api::proof_utils::{compute_jackpot_hash, CompiledPublicParams};
use zk_pow::api::verify::verify_plain_proof;
use zk_pow::circuit::chip::compute_jackpot;
use zk_pow::circuit::pearl_noise::compute_noise;
use zk_pow::ffi::plain_proof::{check_cert_version_eligible, PlainProof};

const REV: &str = "3fe226761a139a9652b8f28a6464a4bbc25986c8";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    certificate_version: u32,
    header76: String,
    nbits_override: u32,
    expected_digest: String,
    proof: PlainProof,
}

fn check(r: Request) -> Result<serde_json::Value, String> {
    if r.certificate_version != 3
        || r.proof.moe.is_some()
        || r.proof.m < 64
        || r.proof.m > 131072
        || r.proof.m % 64 != 0
        || r.proof.n < 64
        || r.proof.n > 131072
        || r.proof.n % 64 != 0
        || r.proof.k < 2048
        || r.proof.k > 4096
        || r.proof.k % 128 != 0
        || r.proof.noise_rank != 128
        || r.proof.a.row_indices.len() != 8
        || r.proof.bt.row_indices.len() != 16
        || r.proof.a.proof.leaf_data.len() > 128
        || r.proof.bt.proof.leaf_data.len() > 128
        || r.proof.a.proof.siblings.len() > 256
        || r.proof.bt.proof.siblings.len() > 256
    {
        return Err("outside bounded dense-proof scope".into());
    }
    let header_bytes = hex::decode(r.header76).map_err(|e| e.to_string())?;
    let header = IncompleteBlockHeader::from_bytes(&header_bytes).map_err(|e| e.to_string())?;
    check_cert_version_eligible(3, &r.proof).map_err(|e| e.to_string())?;
    verify_plain_proof(
        &header,
        &r.proof,
        Some(r.nbits_override),
        SeedDerivation::Salted,
    )
    .map_err(|e| e.to_string())?;
    let (private, public) = r
        .proof
        .parse_proof(header, SeedDerivation::Salted)
        .map_err(|e| e.to_string())?;
    let compiled = CompiledPublicParams::from(&public);
    let noise = compute_noise(&compiled);
    let jackpot = compute_jackpot(&compiled, &private.s_a, &private.s_b, &noise);
    let actual = hex::encode(compute_jackpot_hash(&jackpot, compiled.a_noise_seed()));
    if actual != r.expected_digest {
        return Err("GPU digest differs from official verifier computation".into());
    }
    let wire = bincode::serialize(&r.proof).map_err(|e| e.to_string())?;
    Ok(json!({"accepted": true, "digest": actual, "proof_bincode_hex": hex::encode(wire)}))
}

fn main() {
    let mut bytes = Vec::new();
    if io::stdin().take(2_000_001).read_to_end(&mut bytes).is_err() || bytes.len() > 2_000_000 {
        eprintln!("bounded offline verifier input failed");
        std::process::exit(2);
    }
    let request_hash = hex::encode(Sha256::digest(&bytes));
    let checked = serde_json::from_slice::<Request>(&bytes)
        .map_err(|e| e.to_string())
        .and_then(check);
    let mut result = match checked {
        Ok(value) => value,
        Err(error) => json!({"accepted": false, "error": error}),
    };
    result["protocol"] = json!("boris-official-verifier/1");
    result["source_revision"] = json!(REV);
    result["request_sha256"] = json!(request_hash);
    println!("{result}");
}
