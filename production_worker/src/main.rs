// Boris Pearl worker: production-shaped host/nonce lifecycle and bounded CUDA baseline.
// No network. Default mode only accepts an explicit small local-test header.
#[path = "../../crates/spm-worker/src/engine.rs"]
mod engine;
mod ffi;
#[path = "../../crates/spm-worker/src/host.rs"]
mod host;
mod kernel_selftest;
mod local_grid;

use anyhow::{anyhow, ensure, Result};
use serde_json::{json, Value};
use spm_cpuref::{tile_from_rows, NoiseSide, SEED_LABEL_A, SEED_LABEL_B};
use spm_pow::U256;
use spm_work::{Shape, WorkUnit};
use std::collections::VecDeque;
use std::io::{Read, Write};
use std::sync::Arc;
use std::time::Instant;
use zk_pow::api::proof::SeedDerivation;
use zk_pow::api::verify::verify_plain_proof;

const MAX_FRAME: usize = 2_000_000;
const LOCAL_NONCE: u64 = 0x4b41545f6e6f6e63;

struct Active {
    binding: String,
    immutable_request: String,
    host: host::JobHost,
    wu: WorkUnit,
    nonce: u64,
    cursor: u64,
    device: u32,
    device_uuid: String,
    kernel_variant: u32,
    attempt: host::AttemptHost,
    a_side: NoiseSide,
    b_side: NoiseSide,
    a_cache: VecDeque<(u32, Arc<Vec<i8>>)>,
    b_cache: VecDeque<(u32, Arc<Vec<i8>>)>,
    total_tiles: u64,
    nonce_changes: u64,
    cache_hits: u64,
    started: Instant,
}
fn number(v: &Value, k: &str) -> Result<u64> {
    v[k].as_u64().ok_or_else(|| anyhow!("missing integer {k}"))
}
fn text<'a>(v: &'a Value, k: &str) -> Result<&'a str> {
    v[k].as_str().ok_or_else(|| anyhow!("missing text {k}"))
}
fn immutable_request(v: &Value) -> Result<String> {
    let mut value = v.clone();
    let object = value
        .as_object_mut()
        .ok_or_else(|| anyhow!("prepare must be an object"))?;
    object.remove("resume_nonce");
    object.remove("resume_cursor");
    Ok(serde_json::to_string(&value)?)
}

fn prepare(v: &Value, live: bool, large_fixture: bool, local_search: bool) -> Result<Active> {
    let bytes = hex::decode(text(v, "header76")?)?;
    let header: [u8; 76] = bytes
        .try_into()
        .map_err(|_| anyhow!("header must be 76 bytes"))?;
    let m = u32::try_from(number(v, "m")?)?;
    let n = u32::try_from(number(v, "n")?)?;
    let k = u32::try_from(number(v, "k")?)?;
    ensure!(
        m <= 131072 && n <= 131072 && k <= 4096 && k % 128 == 0,
        "baseline shape limit"
    );
    if !live {
        ensure!(
            (if large_fixture {
                m == 131072 && n == 131072 && k == 4096
            } else if local_search {
                m == 64 && n == 64 && k == 4096
            } else {
                m <= 512 && n <= 512
            }) && header[4..36] == [0x4b; 32]
                && header[36..68] == [0x41; 32]
                && header[68..72] == 0x66666666u32.to_le_bytes(),
            "live jobs require an explicit separately authorized run"
        );
    }
    ensure!(
        number(v, "certificate_version")? == 3,
        "update required: certificate version"
    );
    let initial_nonce = number(v, "nonce")?;
    if !live {
        ensure!(
            initial_nonce == LOCAL_NONCE,
            "local tests permit only the fixed nonce"
        );
    }
    let target =
        U256::from_str_radix(text(v, "target")?, 16).map_err(|e| anyhow!("target: {e}"))?;
    ensure!(
        !local_search || target == U256::from(1),
        "local search test requires the fixed hard synthetic target"
    );
    let job = spm_proto::Job {
        job_id: text(v, "job_id")?.into(),
        header,
        target,
        height: None,
        diff: None,
        cert_version: Some(3),
    };
    let wu = WorkUnit::build(
        &job,
        Shape { m, n, k, r: 128 },
        number(v, "session_id")?,
        number(v, "generation")?,
    )
    .map_err(|e| anyhow!("{e}"))?;
    let backend = match v.get("backend") {
        Some(x) => x.as_str().ok_or_else(|| anyhow!("backend must be text"))?,
        None => "dp4a",
    };
    let kernel_variant = match backend {
        "dp4a" => 4,
        "wmma-int8" => 16,
        "mma-int8" => 32,
        _ => return Err(anyhow!("unknown CUDA backend")),
    };
    let device = u32::try_from(number(v, "device_index")?)?;
    ensure!(device <= 31, "device index limit");
    ensure!(
        ffi::device_info(device)?.supports(kernel_variant),
        "backend unavailable for physical capability or compiled profile"
    );
    let nonce = v
        .get("resume_nonce")
        .map(|_| number(v, "resume_nonce"))
        .transpose()?
        .unwrap_or(initial_nonce);
    let cursor = v
        .get("resume_cursor")
        .map(|_| number(v, "resume_cursor"))
        .transpose()?
        .unwrap_or(0);
    let tiles = u64::from(m) * u64::from(n) / 128;
    ensure!(
        nonce >= initial_nonce && cursor <= tiles,
        "invalid resume position"
    );
    ensure!(
        live || local_search || nonce == LOCAL_NONCE,
        "fixed fixture cannot advance nonce"
    );
    ensure!(
        !large_fixture || cursor == 0,
        "large fixture permits exactly one tile"
    );
    let total_tiles = (nonce - initial_nonce)
        .checked_mul(tiles)
        .and_then(|x| x.checked_add(cursor))
        .ok_or_else(|| anyhow!("resume position overflow"))?;
    ensure!(
        !local_search || total_tiles < 2_000_000,
        "local resume exceeds test limit"
    );
    let host = host::JobHost::new(&wu)?;
    let attempt = host.attempt(nonce);
    let a_side = NoiseSide::new(&SEED_LABEL_A, &attempt.a_noise_seed, k as usize, 128);
    let b_side = NoiseSide::new(&SEED_LABEL_B, &host.b_noise_seed(), k as usize, 128);
    let device_uuid = ffi::identity(device)?;
    Ok(Active {
        binding: text(v, "binding")?.into(),
        immutable_request: immutable_request(v)?,
        host,
        wu,
        nonce,
        cursor,
        device,
        device_uuid,
        kernel_variant,
        attempt,
        a_side,
        b_side,
        a_cache: VecDeque::new(),
        b_cache: VecDeque::new(),
        total_tiles,
        nonce_changes: nonce - initial_nonce,
        cache_hits: 0,
        started: Instant::now(),
    })
}

fn tile(
    a: &mut Active,
    v: &Value,
    live: bool,
    large_fixture: bool,
    local_search: bool,
) -> Result<Value> {
    ensure!(text(v, "binding")? == a.binding, "stale worker generation");
    ensure!(
        !large_fixture || a.cursor == 0,
        "large fixed fixture permits exactly one tile; no search"
    );
    ensure!(
        !local_search || (a.total_tiles < 2_000_000 && a.started.elapsed().as_secs() < 600),
        "bounded local search test exhausted"
    );
    let budget = number(v, "budget_ns")?;
    ensure!(
        (100_000_000..=1_000_000_000).contains(&budget),
        "tile admission needs 100ms to 1s budget"
    );
    let columns = (a.wu.shape.n / 64) * 4;
    let rows = (a.wu.shape.m / 64) * 8;
    if a.cursor >= u64::from(rows) * u64::from(columns) {
        ensure!(
            live || local_search,
            "fixed local attempt exhausted; no nonce search"
        );
        a.nonce = a
            .nonce
            .checked_add(1)
            .ok_or_else(|| anyhow!("nonce exhausted"))?;
        a.cursor = 0;
        a.nonce_changes += 1;
        a.attempt = a.host.attempt(a.nonce);
        a.a_side = NoiseSide::new(
            &SEED_LABEL_A,
            &a.attempt.a_noise_seed,
            a.wu.shape.k as usize,
            128,
        );
        a.a_cache.clear();
    }
    let ri = (a.cursor / u64::from(columns)) as u32;
    let ci = (a.cursor % u64::from(columns)) as u32;
    let tr = (ri / 8) * 64 + ri % 8;
    let tc = (ci / 4) * 64 + (ci % 4) * 2;
    let att = &a.attempt;
    let ar: Vec<usize> = (0..8).map(|i| (tr + i * 8) as usize).collect();
    let br: Vec<usize> = (0..16)
        .map(|i| (tc + (i / 2) * 8 + i % 2) as usize)
        .collect();
    let k = a.wu.shape.k as usize;
    let an = if let Some((_, rows)) = a.a_cache.iter().find(|(key, _)| *key == tr) {
        a.cache_hits += 1;
        rows.clone()
    } else {
        let rows = Arc::new(a.a_side.noised_rows(&a.host.a_rows(att, &ar), &ar)?);
        if a.a_cache.len() == 32 {
            a.a_cache.pop_front();
        }
        a.a_cache.push_back((tr, rows.clone()));
        rows
    };
    let bn = if let Some((_, rows)) = a.b_cache.iter().find(|(key, _)| *key == tc) {
        a.cache_hits += 1;
        rows.clone()
    } else {
        let rows = Arc::new(a.b_side.noised_rows(&a.host.bt_rows(&br), &br)?);
        if a.b_cache.len() == 32 {
            a.b_cache.pop_front();
        }
        a.b_cache.push_back((tc, rows.clone()));
        rows
    };
    let gpu = ffi::tile(
        a.device,
        &an,
        &bn,
        a.wu.shape.k,
        a.kernel_variant,
        &att.a_noise_seed,
    )?;
    // Baseline canary on every tile. Faster backends may sample only after qualification.
    let cpu = tile_from_rows(tr, tc, &an, &bn, k, 128, &att.a_noise_seed)?;
    if a.cursor % 32 == 0 {
        let independent = a.host.tile(att, tr, tc)?;
        ensure!(
            cpu == independent,
            "cached row generation differs from independent host canary"
        );
    }
    ensure!(
        gpu.digest == cpu.digest && gpu.transcript == cpu.transcript,
        "GPU canary mismatch"
    );
    ensure!(
        gpu.active_ns <= budget,
        "GPU exceeded reserved active time; stop and requalify device"
    );
    a.cursor += 1;
    a.total_tiles += 1;
    let mut candidate = Value::Null;
    if cpu.meets(a.wu.share_bound()) {
        let proof = a.host.proof(att, tr, tc)?;
        verify_plain_proof(
            a.host.header(),
            &proof,
            Some(a.wu.nbits_share),
            SeedDerivation::Salted,
        )?;
        candidate = json!({"mode":"worker-proof","certificate_version":3,"header76":hex::encode(a.wu.header),
            "nbits_override":a.wu.nbits_share,"expected_digest":hex::encode(gpu.digest),"proof":proof});
    }
    Ok(
        json!({"binding":a.binding,"active_ns":gpu.active_ns,"nonce":a.nonce,"tile":a.cursor-1,
        "candidate":candidate,"canary_ok":true,"nbits_share":a.wu.nbits_share,
        "total_tiles":a.total_tiles,"nonce_changes":a.nonce_changes,"cache_hits":a.cache_hits,
        "device_bytes":gpu.device_bytes,"workspace_reused":gpu.reused==1,"kernel_variant":gpu.variant}),
    )
}

fn chunk(a: &mut Active, v: &Value, live: bool, large: bool, local_search: bool) -> Result<Value> {
    let count = v
        .get("max_tiles")
        .map(|n| n.as_u64().ok_or_else(|| anyhow!("invalid tile batch")))
        .transpose()?
        .unwrap_or(1);
    ensure!((1..=64).contains(&count), "batch limit 1..64");
    ensure!(
        !large || count == 1,
        "large fixture permits exactly one tile"
    );
    let start = Instant::now();
    let mut active = 0u64;
    let mut completed = 0u64;
    let mut last = Value::Null;
    for _ in 0..count {
        if completed > 0
            && (start.elapsed().as_millis() >= 50 || active + 10_000_000 > number(v, "budget_ns")?)
        {
            break;
        }
        last = tile(a, v, live, large, local_search)?;
        active += last["active_ns"].as_u64().unwrap();
        completed += 1;
        ensure!(
            active <= number(v, "budget_ns")?,
            "batch exceeds reserved GPU time"
        );
        if !last["candidate"].is_null() {
            break;
        }
    }
    last["active_ns"] = json!(active);
    last["tiles_this_chunk"] = json!(completed);
    Ok(last)
}

fn read_frame() -> Result<Option<Value>> {
    let mut input = std::io::stdin().lock();
    let mut header = [0u8; 4];
    let n = input.read(&mut header)?;
    if n == 0 {
        return Ok(None);
    }
    if n < 4 {
        input.read_exact(&mut header[n..])?;
    }
    let size = u32::from_be_bytes(header) as usize;
    ensure!(size > 0 && size <= MAX_FRAME, "frame limit");
    let mut data = vec![0u8; size];
    input.read_exact(&mut data)?;
    Ok(Some(serde_json::from_slice(&data)?))
}
fn write_frame(value: &Value) -> Result<()> {
    let data = serde_json::to_vec(value)?;
    ensure!(data.len() <= MAX_FRAME, "response frame limit");
    let mut output = std::io::stdout().lock();
    output.write_all(&(data.len() as u32).to_be_bytes())?;
    output.write_all(&data)?;
    output.flush()?;
    Ok(())
}
fn run() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    ensure!(
        args.is_empty()
            || args == ["--live"]
            || args == ["--large-fixed-fixture"]
            || args == ["--local-search-test"]
            || args == ["--full-grid-local-test"],
        "unknown worker option"
    );
    let live = args == ["--live"];
    let large_fixture = args == ["--large-fixed-fixture"];
    let local_search = args == ["--local-search-test"];
    let full_grid = args == ["--full-grid-local-test"];
    let mut grid: Option<local_grid::LocalGrid> = None;
    let mut grid_initialized = false;
    write_frame(&json!({"type":"ready","protocol":1,"mode":"cuda-baseline"}))?;
    let mut contexts: VecDeque<Active> = VecDeque::new();
    let process_started = Instant::now();
    let mut local_test_tiles = 0u64;
    while let Some(request) = read_frame()? {
        let id = number(&request, "id")?;
        let result = (|| -> Result<Value> {
            if full_grid {
                ensure!(
                    process_started.elapsed().as_secs() < 1800,
                    "full-grid process1800s limit"
                );
                return match text(&request, "type")? {
                    "full-grid-prepare" => {
                        ensure!(
                            !grid_initialized,
                            "one immutable artificial grid per process"
                        );
                        grid_initialized = true;
                        let (state, reply) = local_grid::LocalGrid::prepare(&request["params"])?;
                        grid = Some(state);
                        Ok(reply)
                    }
                    "full-grid-chunk" => grid
                        .as_mut()
                        .ok_or_else(|| anyhow!("prepare fixed grid first"))?
                        .chunk(&request["params"]),
                    "release" => {
                        grid.take();
                        Ok(json!({"released":true}))
                    }
                    _ => Err(anyhow!(
                        "full-grid mode only accepts fixed internal artificial data"
                    )),
                };
            }
            match text(&request, "type")? {
                "prepare" => {
                    let v = &request["params"];
                    let binding = text(v, "binding")?;
                    let a = if let Some(index) = contexts.iter().position(|a| a.binding == binding)
                    {
                        ensure!(
                            contexts[index].immutable_request == immutable_request(v)?,
                            "cannot replace immutable cached job"
                        );
                        contexts.remove(index).unwrap()
                    } else {
                        if contexts.len() == 4 {
                            contexts.pop_front();
                        }
                        prepare(v, live, large_fixture, local_search)?
                    };
                    let value = json!({"binding":a.binding,"nbits_share":a.wu.nbits_share,
                "tiles":a.host.tiles(),"device_uuid":a.device_uuid,"total_tiles":a.total_tiles,
                "mode":if live{"live-authorized"}else if local_search{"local-search-test"}else{"local-fixed-attempt"}});
                    contexts.push_back(a);
                    Ok(value)
                }
                "chunk" => {
                    let mut params = request["params"].clone();
                    if local_search {
                        ensure!(
                            process_started.elapsed().as_secs() < 600
                                && local_test_tiles < 2_000_000,
                            "process local test limit reached"
                        );
                        let count = params
                            .get("max_tiles")
                            .map(|_| number(&params, "max_tiles"))
                            .transpose()?
                            .unwrap_or(1);
                        ensure!((1..=64).contains(&count), "batch limit 1..64");
                        params["max_tiles"] = json!(count.min(2_000_000 - local_test_tiles));
                    }
                    let result = chunk(
                        contexts
                            .back_mut()
                            .ok_or_else(|| anyhow!("no prepared job"))?,
                        &params,
                        live,
                        large_fixture,
                        local_search,
                    )?;
                    if local_search {
                        local_test_tiles += result["tiles_this_chunk"].as_u64().unwrap();
                    }
                    Ok(result)
                }
                "devices" => {
                    ensure!(contexts.is_empty(), "device inventory requires idle worker");
                    ffi::devices()
                }
                "kernel-selftest" => {
                    ensure!(
                        !live && contexts.is_empty(),
                        "kernel selftest requires idle offline worker"
                    );
                    kernel_selftest::run(&request["params"])
                }
                "release" => {
                    contexts.clear();
                    ffi::release();
                    Ok(json!({"released":true}))
                }
                _ => Err(anyhow!("unknown worker command")),
            }
        })();
        let response = match result {
            Ok(value) => json!({"type":"reply","mode":"cuda-baseline","id":id,"result":value}),
            Err(e) => {
                grid.take();
                contexts.clear();
                ffi::release();
                json!({"type":"reply","mode":"cuda-baseline","id":id,"error":format!("{e:#}")})
            }
        };
        write_frame(&response)?;
    }
    ffi::release();
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("worker stopped: {e:#}");
        ffi::release();
        std::process::exit(2)
    }
}
