"""Software duty pacing; no hardware clock/power changes or claimed wattage cap."""
import math
import time


class DutyLimiter:
    def __init__(self, fraction=0.2, *, clock=time.monotonic, wait=time.sleep):
        if type(fraction) not in (float, int) or not math.isfinite(fraction) or not 0 < fraction <= 1:
            raise ValueError("Duty fraction must be >0 and <=1")
        self.fraction, self.clock, self.wait = fraction, clock, wait
        self.next_at = self.clock()
        self.busy_seconds = self.waited_seconds = 0.0

    def record(self, start):
        end = self.clock()
        busy = max(0, end-start)
        self.busy_seconds += busy
        self.next_at = max(self.next_at, end + busy*(1/self.fraction-1))

    def ready(self, permit):
        while True:
            reason = permit()
            if reason:
                return reason
            remaining = self.next_at-self.clock()
            if remaining <= 0:
                return None
            duration = min(0.02, remaining)
            self.wait(duration)
            self.waited_seconds += duration
