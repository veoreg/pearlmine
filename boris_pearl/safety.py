"""Fail-closed telemetry and explicit stop deadline, without changing GPU settings."""
from dataclasses import dataclass
import math


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class Telemetry:
    observed_at: float
    gpu_c: float | None
    watts: float | None
    free_bytes: int | None
    board_c: float | None = None
    on_battery: bool | None = None
    gpu_util_percent: float | None = None
    total_bytes: int | None = None
    power_limit_watts: float | None = None


class Guard:
    def __init__(self, *, stop_at, max_watts, min_free_bytes, gb10=False, laptop=False,
                 max_gpu_c=None, max_gpu_util=None):
        if (not finite_number(stop_at) or not finite_number(max_watts) or
                max_watts <= 0 or type(min_free_bytes) is not int or min_free_bytes <= 0):
            raise ValueError("Explicit deadline, reviewed power threshold and memory budget are required")
        if gb10 and max_watts > 85:
            raise ValueError("GB10 threshold must not exceed the upstream 85 W stop ceiling")
        self.stop_at = stop_at
        self.max_watts = max_watts
        self.min_free_bytes = min_free_bytes
        self.gb10 = gb10
        self.laptop = laptop
        ceiling = 83 if gb10 else 80
        if max_gpu_c is not None and (not finite_number(max_gpu_c) or not 30 <= max_gpu_c <= ceiling):
            raise ValueError("Temperature threshold may only lower the safety ceiling")
        if max_gpu_util is not None and (not finite_number(max_gpu_util) or not 1 <= max_gpu_util <= 100):
            raise ValueError("GPU utilization threshold must be 1..100")
        self.max_gpu_c = ceiling if max_gpu_c is None else max_gpu_c
        self.max_gpu_util = max_gpu_util
        self.started = False
        self.stopped = False

    def start(self):
        if self.stopped:
            raise ValueError("Stopped session cannot restart automatically")
        self.started = True

    def stop(self):
        self.stopped = True

    def reason(self, now, telemetry):
        def unsafe(reason):
            self.stop()
            return reason
        if not finite_number(now) or now >= self.stop_at:
            self.stop()
            return "stop deadline"
        if self.stopped or not self.started:
            return "stopped"
        t = telemetry
        if t is None or not finite_number(t.observed_at) or not 0 <= now - t.observed_at <= 2:
            return unsafe("missing or stale telemetry")
        for value in (t.gpu_c, t.watts, t.free_bytes):
            if not finite_number(value) or value < 0:
                return unsafe("missing or invalid telemetry")
        if self.laptop and t.on_battery is not False:
            return unsafe("battery power or unknown AC state")
        if t.gpu_c >= self.max_gpu_c:
            return unsafe("GPU temperature")
        if self.gb10 and (not finite_number(t.board_c) or not 0 <= t.board_c < 95):
            return unsafe("board temperature unavailable or high")
        if t.watts >= self.max_watts:
            return unsafe("power threshold")
        if t.free_bytes < self.min_free_bytes:
            return unsafe("memory pressure")
        if self.max_gpu_util is not None and (not finite_number(t.gpu_util_percent) or
                not 0 <= t.gpu_util_percent < self.max_gpu_util):
            return "GPU busy or utilization unavailable"
        return None
