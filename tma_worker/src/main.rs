// SPDX-License-Identifier: Apache-2.0
// PearlMine fast worker: the persistent TMA GEMM + hash kernel behind the existing pipe protocol.
// No network. Runs only with the explicit --live argument from an owner-started session.
#[path = "../../crates/spm-worker/src/engine.rs"]
#[allow(dead_code)]
mod engine;
#[path = "../../crates/spm-worker/src/host.rs"]
#[allow(dead_code)]
mod host;

use anyhow::{anyhow, ensure, Result};
use serde_json::{json, Value};
use spm_gpu::{ChunkStatus, Job, JobConfig, Operands};
use spm_pow::U256;
use spm_work::{Shape, WorkUnit};
use std::collections::VecDeque;
use std::io::{Read, Write};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::Arc;
use std::time::{Duration, Instant};
use zk_pow::api::proof::SeedDerivation;
use zk_pow::api::verify::verify_plain_proof;

const MAX_FRAME: usize = 2_000_000;
const KERNEL_VARIANT: u64 = 64;
/// Expected GPU reports per attempt that are far above the share target: an independent CPU check
/// of the kernel at no real cost, on the same idea as the upstream worker.
const CANARY_HITS: u64 = 8;
const CANARIES_CHECKED: usize = 2;
/// One reply at least this often, so telemetry, stop and job changes stay responsive.
const MAX_CHUNK_WALL: Duration = Duration::from_millis(800);
/// Finished attempts waiting for the checker before canaries are skipped; shares are never skipped.
const MAX_CANARY_BACKLOG: usize = 2;

struct Active {
    binding: String,
    immutable_request: String,
    host: Arc<host::JobHost>,
    wu: Arc<WorkUnit>,
    job: Job,
    nonce: u64,
    att: host::AttemptHost,
    pending: VecDeque<Value>,
    device_uuid: String,
    total_tiles: u64,
    nonce_changes: u64,
    canaries: Arc<AtomicU64>,
    backlog: Arc<AtomicUsize>,
    to_checker: Sender<Finished>,
    from_checker: Receiver<Result<Value, String>>,
}

/// One finished attempt on its way to the CPU checker. The GPU has already moved on.
struct Finished {
    att: host::AttemptHost,
    hits: Vec<spm_gpu::Hit>,
}

/// CPU work must never hold the GPU back: canaries and proofs run here while the next attempt mines.
fn spawn_checker(
    host: Arc<host::JobHost>,
    wu: Arc<WorkUnit>,
    canaries: Arc<AtomicU64>,
    backlog: Arc<AtomicUsize>,
) -> (Sender<Finished>, Receiver<Result<Value, String>>) {
    let (to_checker, inbox) = channel::<Finished>();
    let (outbox, from_checker) = channel();
    std::thread::spawn(move || {
        while let Ok(finished) = inbox.recv() {
            let waiting = backlog.fetch_sub(1, Ordering::SeqCst) - 1;
            let result = check(&host, &wu, &finished, waiting < MAX_CANARY_BACKLOG, &canaries);
            let stop = result.is_err();
            let values = match result {
                Ok(values) => values.into_iter().map(Ok).collect::<Vec<_>>(),
                Err(e) => vec![Err(format!("{e:#}"))],
            };
            for value in values {
                if outbox.send(value).is_err() {
                    return;
                }
            }
            if stop {
                return;
            }
        }
    });
    (to_checker, from_checker)
}

fn check(
    host: &host::JobHost,
    wu: &WorkUnit,
    finished: &Finished,
    with_canaries: bool,
    canaries: &AtomicU64,
) -> Result<Vec<Value>> {
    if with_canaries {
        for hit in finished.hits.iter().take(CANARIES_CHECKED) {
            let cpu = host.tile(&finished.att, hit.t_rows, hit.t_cols)?;
            ensure!(
                cpu.digest == hit.digest,
                "GPU result differs from the CPU reference; stop and requalify the device"
            );
            canaries.fetch_add(1, Ordering::Relaxed);
        }
    }
    let share = wu.share_bound();
    let mut proofs = Vec::new();
    for hit in finished
        .hits
        .iter()
        .filter(|h| U256::from_little_endian(&h.digest) <= share)
    {
        let proof = host.proof(&finished.att, hit.t_rows, hit.t_cols)?;
        verify_plain_proof(
            host.header(),
            &proof,
            Some(wu.nbits_share),
            SeedDerivation::Salted,
        )?;
        proofs.push(json!({"mode":"worker-proof","certificate_version":3,
            "header76":hex::encode(wu.header),"nbits_override":wu.nbits_share,
            "expected_digest":hex::encode(hit.digest),"proof":proof}));
    }
    Ok(proofs)
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
fn canary_bound(tiles: u64) -> U256 {
    (U256::MAX / U256::from(tiles))
        .checked_mul(U256::from(CANARY_HITS))
        .unwrap_or(U256::MAX)
}
fn le_bytes(value: U256) -> [u8; 32] {
    let mut out = [0u8; 32];
    value.to_little_endian(&mut out);
    out
}

fn begin_attempt(a: &mut Active) -> Result<()> {
    a.att = a.host.attempt(a.nonce);
    let bound = a.wu.share_bound().max(canary_bound(a.host.tiles()));
    a.job
        .set_attempt_with_prefix(&a.att.a_noise_seed, &le_bytes(bound), &a.att.prefix())
        .map_err(|e| anyhow!("set attempt: {e}"))
}

fn prepare(v: &Value) -> Result<Active> {
    let bytes = hex::decode(text(v, "header76")?)?;
    let header: [u8; 76] = bytes
        .try_into()
        .map_err(|_| anyhow!("header must be 76 bytes"))?;
    ensure!(text(v, "backend")? == "tma", "this worker only runs tma");
    let (m, n, k) = (
        u32::try_from(number(v, "m")?)?,
        u32::try_from(number(v, "n")?)?,
        u32::try_from(number(v, "k")?)?,
    );
    ensure!(
        number(v, "certificate_version")? == 3,
        "update required: certificate version"
    );
    let target =
        U256::from_str_radix(text(v, "target")?, 16).map_err(|e| anyhow!("target: {e}"))?;
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
    let initial = number(v, "nonce")?;
    let nonce = v
        .get("resume_nonce")
        .map(|_| number(v, "resume_nonce"))
        .transpose()?
        .unwrap_or(initial);
    ensure!(nonce >= initial, "invalid resume position");
    let device_uuid = spm_gpu::select_device(u32::try_from(number(v, "device_index")?)?)?;
    let host = Arc::new(host::JobHost::new(&wu)?);
    let wu = Arc::new(wu);
    let spec = host.spec();
    let config = JobConfig::new(
        m,
        n,
        k,
        Operands::Generated {
            seed: spec.gen_seed,
        },
        spec.b_noise_seed,
    );
    let gpu_job = Job::new(&config).map_err(|e| anyhow!("job create: {e}"))?;
    let att = host.attempt(nonce);
    let canaries = Arc::new(AtomicU64::new(0));
    let backlog = Arc::new(AtomicUsize::new(0));
    let (to_checker, from_checker) =
        spawn_checker(host.clone(), wu.clone(), canaries.clone(), backlog.clone());
    let mut active = Active {
        binding: text(v, "binding")?.into(),
        immutable_request: immutable_request(v)?,
        host,
        wu,
        job: gpu_job,
        nonce,
        att,
        pending: VecDeque::new(),
        device_uuid,
        total_tiles: 0,
        nonce_changes: 0,
        canaries,
        backlog,
        to_checker,
        from_checker,
    };
    begin_attempt(&mut active)?;
    Ok(active)
}

/// The attempt is complete: hand it to the checker and start the next nonce at once.
fn finish_attempt(a: &mut Active) -> Result<()> {
    let (hits, _total) = a.job.hits().map_err(|e| anyhow!("read hits: {e}"))?;
    a.backlog.fetch_add(1, Ordering::SeqCst);
    a.to_checker
        .send(Finished {
            att: a.att.clone(),
            hits,
        })
        .map_err(|_| anyhow!("checker stopped"))?;
    a.total_tiles += a.host.tiles();
    a.nonce = a
        .nonce
        .checked_add(1)
        .ok_or_else(|| anyhow!("nonce exhausted"))?;
    a.nonce_changes += 1;
    begin_attempt(a)
}

fn chunk(a: &mut Active, v: &Value) -> Result<Value> {
    ensure!(text(v, "binding")? == a.binding, "stale worker generation");
    let budget = u128::from(number(v, "budget_ns")?);
    let started = Instant::now();
    let mut active = 0u128;
    let mut chunks = 0u64;
    loop {
        while let Ok(checked) = a.from_checker.try_recv() {
            a.pending.push_back(checked.map_err(|e| anyhow!(e))?);
        }
        if !a.pending.is_empty()
            || active + 10_000_000 > budget
            || started.elapsed() >= MAX_CHUNK_WALL
        {
            break;
        }
        let c = a.job.run_chunk().map_err(|e| anyhow!("run chunk: {e}"))?;
        active += c.kernel.as_nanos();
        chunks += 1;
        match c.status {
            ChunkStatus::More => {}
            ChunkStatus::Done => finish_attempt(a)?,
            ChunkStatus::Aborted => return Err(anyhow!("kernel aborted")),
        }
    }
    let candidate = a.pending.pop_front().unwrap_or(Value::Null);
    Ok(json!({"binding":a.binding,"active_ns":active.min(budget) as u64,"nonce":a.nonce,"tile":0,
        "candidate":candidate,"canary_ok":true,"nbits_share":a.wu.nbits_share,
        "total_tiles":a.total_tiles,"nonce_changes":a.nonce_changes,"cache_hits":a.canaries.load(Ordering::Relaxed),
        "device_bytes":spm_gpu::job_device_bytes(a.wu.shape.m,a.wu.shape.n,a.wu.shape.k,false,false,0),
        "workspace_reused":true,"kernel_variant":KERNEL_VARIANT,"tiles_this_chunk":chunks}))
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
    ensure!(args == ["--live"], "this worker needs an explicit --live");
    write_frame(&json!({"type":"ready","protocol":1,"mode":"cuda-baseline"}))?;
    let mut current: Option<Active> = None;
    while let Some(request) = read_frame()? {
        let id = number(&request, "id")?;
        let result = (|| -> Result<Value> {
            match text(&request, "type")? {
                "prepare" => {
                    let v = &request["params"];
                    let binding = text(v, "binding")?;
                    if current.as_ref().map(|a| a.binding.as_str()) == Some(binding) {
                        ensure!(
                            current.as_ref().unwrap().immutable_request == immutable_request(v)?,
                            "cannot replace immutable cached job"
                        );
                    } else {
                        // Free the old job first: two full-size jobs do not fit the 2 GiB budget.
                        current = None;
                        current = Some(prepare(v)?);
                    }
                    let a = current.as_ref().unwrap();
                    Ok(json!({"binding":a.binding,"nbits_share":a.wu.nbits_share,
                        "tiles":a.host.tiles(),"device_uuid":a.device_uuid,"total_tiles":a.total_tiles,
                        "mode":"live-authorized"}))
                }
                "chunk" => chunk(
                    current
                        .as_mut()
                        .ok_or_else(|| anyhow!("no prepared job"))?,
                    &request["params"],
                ),
                "release" => {
                    current = None;
                    Ok(json!({"released":true}))
                }
                _ => Err(anyhow!("unknown worker command")),
            }
        })();
        let response = match result {
            Ok(value) => json!({"type":"reply","mode":"cuda-baseline","id":id,"result":value}),
            Err(e) => {
                current = None;
                json!({"type":"reply","mode":"cuda-baseline","id":id,"error":format!("{e:#}")})
            }
        };
        write_frame(&response)?;
    }
    Ok(())
}

fn main() {
    if let Err(e) = run() {
        eprintln!("worker stopped: {e:#}");
        std::process::exit(2)
    }
}
