"""Fault injection for local pipe tests only. No GPU, socket or mining access."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boris_pearl.ipc import read_frame, write_frame

mode = sys.argv[1]
if mode == "handshake":
    write_frame(sys.stdout.buffer, {"type": "ready", "protocol": 1, "mode": "live"})
    raise SystemExit(0)
if mode == "oversize":
    sys.stdout.buffer.write(b"\xff\xff\xff\xff")
    sys.stdout.buffer.flush()
    raise SystemExit(0)
write_frame(sys.stdout.buffer, {"type": "ready", "protocol": 1, "mode": "offline-synthetic"})
request = read_frame(sys.stdin.buffer)
if mode == "hang":
    time.sleep(10)
    raise SystemExit(0)
if mode == "crash":
    raise SystemExit(7)
reply = {"type": "result", "mode": "offline-synthetic", "id": request["id"],
         "job_key": request["job_key"], "active_ns": request["budget_ns"], "proof_hex": "00"}
if mode == "wrong-id":
    reply["id"] += 1
if mode == "wrong-job":
    reply["job_key"][-1] = "f" * 64
if mode == "overcharge":
    reply["active_ns"] += 1
write_frame(sys.stdout.buffer, reply)
