# SPDX-License-Identifier: Apache-2.0
"""Conservative software stop thresholds; never writes hardware power/clock settings."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class LoadProfile:
    name:str
    duty:float
    max_watts:float
    max_gpu_c:float
    require_ac:bool


def validate_request(profile,duty,max_watts,require_ac):
    if profile not in ("standard","laptop"):raise ValueError("Unknown load profile")
    if type(require_ac) is not bool:raise ValueError("AC requirement must be explicit bool")
    fraction=(.1 if profile=="laptop" else .2) if duty is None else duty
    ceiling=.1 if profile=="laptop" else .25
    if type(fraction) not in (int,float) or not math.isfinite(fraction) or not 0<fraction<=ceiling:
        raise ValueError(f"{profile} profile needs duty<={ceiling*100:g}%")
    if profile=="laptop" and not require_ac:raise ValueError("Laptop profile requires confirmed AC; desktop override refused")
    if max_watts is not None and (type(max_watts) not in (int,float) or not math.isfinite(max_watts) or not 25<=max_watts<=150):
        raise ValueError("Explicit observed power stop threshold must be25..150W")
    if profile=="laptop" and max_watts is not None and max_watts>50:
        raise ValueError("Laptop profile observed stop threshold cannot exceed50W")
    return fraction


def resolve_profile(profile,duty,max_watts,require_ac,driver_limit,*,gb10=False):
    fraction=validate_request(profile,duty,max_watts,require_ac)
    if type(driver_limit) not in (int,float) or not math.isfinite(driver_limit) or driver_limit<=0:
        raise ValueError("Confirmed driver power limit required; no guessed wattage")
    ceiling=min(50 if profile=="laptop" else 125,85 if gb10 else 150)
    threshold=min(ceiling,driver_limit*.8) if max_watts is None else max_watts
    if threshold>driver_limit*.8 or gb10 and threshold>85:
        raise ValueError("Observed stop threshold must be <=80% driver limit and retain GB10 ceiling")
    return LoadProfile(profile,fraction,threshold,70 if profile=="laptop" else 75,require_ac)
