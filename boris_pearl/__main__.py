"""Development interface. Check, detect and simulate without contacting any pool."""
import argparse
import json

from .backends import MATRIX, inventory
from .config import Config, load_config
from .core import NS, Planner


def simulate(config, seconds):
    planner = Planner(config)
    # Synthetic device-event durations only. No GPU, sockets, payout or mining.
    remaining = seconds * NS
    while remaining:
        grant = planner.reserve(running=True, user_ready=True, dev_ready=True)
        active = min(grant.budget_ns, remaining)
        planner.complete(grant, active)
        remaining -= active
    return {"mode": "synthetic simulation; no mining", "user_seconds": planner.user_ns / NS,
            "developer_seconds": planner.dev_ns / NS,
            "measured_time_percent": planner.dev_ns * 100 / (seconds * NS) if seconds else 0,
            "configured_bps": config.effective_bps}


def gui():
    from .dashboard import run
    run()


def main():
    parser = argparse.ArgumentParser(description="PearlMine offline development prototype")
    parser.add_argument("command", choices=("diagnose", "matrix", "check", "simulate", "gui", "worker-selftest", "proof-lab", "sensors", "local-search-test"))
    parser.add_argument("--config")
    parser.add_argument("--seconds", type=int)
    parser.add_argument("--output")
    parser.add_argument("--verifier")
    parser.add_argument("--verifier-sha256")
    parser.add_argument("--device",default="0",help="CUDA logical index or complete GPU UUID")
    parser.add_argument("--profile",choices=("standard","laptop"),default="standard")
    parser.add_argument("--duty",type=float)
    parser.add_argument("--max-watts",type=float)
    parser.add_argument("--backend",choices=("dp4a","wmma-int8","mma-int8"),default="dp4a")
    parser.add_argument("--desktop",action="store_true",help="Explicit desktop profile; do not require a laptop AC sensor for the local test")
    args = parser.parse_args()
    if args.seconds is None:
        args.seconds=300 if args.command=="local-search-test" else 3600
    if args.command == "gui":
        gui()
        return
    if args.command == "local-search-test":
        from .local_search import run_local
        try:
            code=run_local(args.seconds,output=args.output or "docs/boris/evidence/load/local-search-report.json",
                duty=args.duty,max_watts=args.max_watts,device_index=args.device,require_ac=not args.desktop,backend=args.backend,profile=args.profile)
        except (OSError,ValueError,RuntimeError) as exc:
            parser.error(str(exc))
        raise SystemExit(code)
    if args.command == "sensors":
        from .telemetry import TelemetryMonitor
        from .devices import discover,select_device
        selected=select_device(discover(),args.device)
        m=TelemetryMonitor(device_uuid=selected.uuid)
        try:
            m.start();m.updated.wait(3)
            details=m.details()
            if details:
                details["device"].pop("uuid",None)
                details.pop("compute_pids",None)
            print(json.dumps({"read_only":True,"details":details,"error":m.last_error},indent=2))
            if not m.sample():
                raise SystemExit(3)
        finally:
            m.close()
        return
    if args.command == "diagnose":
        print(json.dumps(inventory(), indent=2))
        return
    if args.command == "matrix":
        print(json.dumps(MATRIX, indent=2))
        return
    if args.command == "worker-selftest":
        from .ipc import transport_selftest
        print(json.dumps(transport_selftest(), indent=2))
        return
    if args.command == "proof-lab":
        from .proof_pipeline import run
        report = run(args.output or "docs/boris/evidence/proof-lab", args.verifier, args.verifier_sha256)
        print(json.dumps(report, indent=2))
        if report["official_verification"] != "passed":
            raise SystemExit(3)  # candidate produced, official acceptance blocked
        return
    try:
        config = load_config(args.config) if args.config else Config()
        if not 0 <= args.seconds <= 86400 * 7:
            raise ValueError("Simulation duration must be between 0 and 604800 seconds")
        print(config.disclosure())
        if args.command == "simulate":
            print(json.dumps(simulate(config, args.seconds), indent=2))
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
