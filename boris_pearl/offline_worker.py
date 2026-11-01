"""Synthetic worker for exercising process lifecycle; NEVER executes mining/GPU work."""
import sys

from .core import NS, natural
from .ipc import WorkerError, read_frame, write_frame


def main():
    incoming, outgoing = sys.stdin.buffer, sys.stdout.buffer
    write_frame(outgoing, {"type": "ready", "protocol": 1, "mode": "offline-synthetic"})
    try:
        while True:
            request = read_frame(incoming)
            job = request.get("job_key")
            budget = request.get("budget_ns")
            if (request.get("type") != "execute" or not natural(request.get("id")) or
                    not natural(budget) or not 1 <= budget <= NS or not isinstance(job, list) or
                    len(job) != 4 or not isinstance(job[0], str) or not natural(job[1]) or
                    not isinstance(job[2], str) or not isinstance(job[3], str) or
                    len(job[3]) != 64 or any(c not in "0123456789abcdef" for c in job[3])):
                raise WorkerError("Invalid synthetic work request")
            # Report simulated time only. This is not device timing or a Pearl proof.
            write_frame(outgoing, {"type": "result", "mode": "offline-synthetic", "id": request["id"],
                        "job_key": job, "active_ns": budget,
                        "proof_hex": (job[3].encode() + request["id"].to_bytes(8, "big")).hex()})
    except EOFError:
        return 0
    except (OSError, ValueError, WorkerError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
