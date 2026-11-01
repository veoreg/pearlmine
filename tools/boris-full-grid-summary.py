#!/usr/bin/env python3
"""Validate saved physical-run evidence without running GPU or contacting a pool."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/"docs/boris/evidence/full-size"


def main():
    load=lambda p:json.loads(p.read_text())
    run=load(OUT/"grid-131072-complete.json")
    receipt=load(OUT/"completed-run-build.json")
    small=load(OUT/"completed-run-grid-512.json")
    status=load(OUT/"completed-run-verification.json")
    tiles=131072*131072//128
    stats=run["stats"]
    assert run["status"]=="passed" and stats["complete"]
    assert run["shape"]==[131072,131072,4096]
    assert stats["executed_tiles"]==tiles+4096
    assert stats["nonces_fully_scanned"]==stats["nonce_changes"]==1
    assert stats["next_cursor"]==4096 and stats["cpu_checks"]==2*(tiles//4096+1)
    assert len(run["official_audits"])==stats["official_audits"]==3
    assert all(x["wire_bytes"]==102193 for x in run["official_audits"])
    assert run["official_audits"][0]["wire_sha256"]!=run["official_audits"][2]["wire_sha256"]
    assert run["worker_binary_sha256"]==small["worker_binary_sha256"]==receipt["binary_sha256"]
    assert run["stop"]["worker_reaped"] and run["stop"]["nvml_after_stop_fresh"]
    assert run["stop"]["worker_still_listed_by_nvml"] is False
    assert run["software_busy_fraction"]<=.251 and not run["pause_counts"]
    assert run["real_submissions"]==run["external_pool_connections"]==0 and not run["live_mode"]
    assert small["stats"]["all_tiles_cpu_checked"] and small["stats"]["cpu_checks"]==4096
    assert small["stats"]["executed_tiles"]==4096 and small["stats"]["nonces_fully_scanned"]==2
    assert len(small["official_audits"])==4 and small["test_result"]=="passed"
    assert status["status"]=="passed" and all(x["exit_code"]==0 for x in status["checks"].values())
    tensor_receipts=load(OUT/"completed-run-tensor-receipts.json")
    for row in tensor_receipts.values():
        assert row["binary_sha256"]==receipt["binary_sha256"]
    # This first long run started before the logging-only4000-row fix.
    samples=run["sensors"]
    run["sensor_retention_limit"]=1300
    run["sensor_window_start"]=samples[0]["observed_at"]
    run["sensor_window_end"]=samples[-1]["observed_at"]
    run["sensor_extrema_scope"]="Retained last1300 readings only; not whole-run extrema. Safety guard sampled throughout."
    run["delivered_logging_fix"]="Full-grid source now retains4000 readings and window timestamps; exact launched source is run-local-search-source.txt"
    (OUT/"grid-131072-complete.json").write_text(json.dumps(run,indent=2)+"\n")
    slim={k:v for k,v in run.items() if k!="sensors"}
    paths=["grid-131072-complete.json","grid-512-all-cpu.json","grid-manual-stop.json","grid-sensor-loss-stop.json","grid-full-size-deadline-stop.json","build-review.json","sensor-retention-note.json","pool-protocol-review.json"]
    source=["native/local_grid.cuh","production_worker/src/local_grid.rs","production_worker/src/main.rs","production_worker/src/ffi.rs","boris_pearl/full_grid.py","boris_pearl/local_search.py","boris_pearl/pool.py","tools/boris-full-grid-test.py","tests_worker/test_full_grid.py","tests_portable/test_pool.py"]
    report=dict(time_utc=datetime.now(timezone.utc).isoformat(),status="passed",scope="historical RTX5090 Windows x64 full-grid phase; exact archived build, not newer executable",
        worker_binary_sha256=receipt["binary_sha256"],full_grid=slim,unit_tests=dict(portable=70,gpu_worker=24,cross_backend_arithmetic_cases=45,cross_backend_proof_cases=15),
        inherited_prior_checks=dict(proof_official=23,official_merkle=35,original_gpu_kat=37,gui_smokes=2),
        evidence_sha256={n:hashlib.sha256((OUT/n).read_bytes()).hexdigest() for n in paths},
        delivered_source_sha256={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in source},
        performance_scope="Measured artificial tiles/wall-second including CPU verification/pacing; no accepted-share hashrate, credited work or income prediction",
        production_readiness="Not ready: resident mode cannot accept live data; actual job/target/owner/fee integration, current pool wire confirmation and credited real shares remain unqualified",
        remaining_blockers=[
            dict(action="Choose exact pool and confirm JSON/gzip credit",status="pending user choice/operator specification or separately authorized handshake",reason="Example pool empty; Kryptex official pages expose settings, not complete wire contract"),
            dict(action="Live mining and submissions",status="not performed; separate consent pending",reason="Only artificial jobs authorized; public PRL destinations and bounded device/endpoint/submission consent absent"),
            dict(action="Linux build/runtime",target="wsl --list --quiet",status="prior access denied; not retried",reason="Wsl/EnumerateDistros/Service/E_ACCESSDENIED; separate authorized Linux system required"),
            dict(action="Other GPU/OS/OEM qualification",status="not performed",reason="No GB10/4090/2060 hardware here; AMD backend absent")],
        real_submissions=0,external_pool_connections=0,settings_changed=False)
    (OUT/"final-summary.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(dict(status="passed",elapsed=run["elapsed_seconds"],tiles=stats["executed_tiles"],cpu_checks=stats["cpu_checks"],official_audits=len(run["official_audits"]),tiles_per_wall_second=run["measured_tiles_per_wall_second"]),indent=2))


if __name__=="__main__":main()
