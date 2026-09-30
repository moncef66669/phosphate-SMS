"""Stockpile operational coordinates.

The planning and YardMaster/ASP interfaces use physical ``X`` positions in
metres.  Operations identify longitudinal locations with pile-relative
``repères``.  Every pile has 1,000 equal repère intervals, bounded by R0 and
R1000 by default.

Repères are a coordinate layer, not 1,000 solver entities.  Keeping the
conversion here prevents a normalized value such as ``400`` from being
mistaken for 400 metres in travel, work-area or separation calculations.
"""
from __future__ import annotations

import math
from typing import Tuple


REPERE_SECTION_COUNT = 1000
DEFAULT_REPERE_AT_X_FROM = 0
DEFAULT_REPERE_AT_X_TO = 1000


def _frame(space) -> Tuple[float, float, float, float]:
    """Return ``(x_from, x_to, repere_at_x_from, repere_at_x_to)``.

    ``getattr`` keeps scenarios serialized before the repère fields were
    introduced backward-compatible.
    """
    x0 = float(space.x_from)
    x1 = float(space.x_to)
    r0 = float(getattr(space, "repere_at_x_from",
                       DEFAULT_REPERE_AT_X_FROM))
    r1 = float(getattr(space, "repere_at_x_to",
                       DEFAULT_REPERE_AT_X_TO))
    if not all(math.isfinite(v) for v in (x0, x1, r0, r1)):
        raise ValueError("stockpile coordinate frame must contain finite values")
    if x1 <= x0:
        raise ValueError("stockpile x_to must exceed x_from")
    if r1 == r0:
        raise ValueError("stockpile repère endpoints must differ")
    return x0, x1, r0, r1


def repere_limits(space) -> Tuple[float, float]:
    """Ascending valid repère limits for ``space``."""
    _, _, r0, r1 = _frame(space)
    return min(r0, r1), max(r0, r1)


def repere_to_x(space, repere: float, *, clamp: bool = False) -> float:
    """Convert one pile-relative repère to physical X metres.

    Reversed pile orientation is supported by configuring
    ``repere_at_x_from=1000`` and ``repere_at_x_to=0``.
    """
    x0, x1, r0, r1 = _frame(space)
    r = float(repere)
    if not math.isfinite(r):
        raise ValueError("repère must be finite")
    lo, hi = min(r0, r1), max(r0, r1)
    if clamp:
        r = min(max(r, lo), hi)
    elif not lo <= r <= hi:
        raise ValueError(f"repère {r:g} is outside R{lo:g}–R{hi:g}")
    return x0 + (r - r0) / (r1 - r0) * (x1 - x0)


def x_to_repere(space, x_m: float, *, clamp: bool = True,
                 rounded: bool = True):
    """Convert physical X metres to the pile's operational repère."""
    x0, x1, r0, r1 = _frame(space)
    x = float(x_m)
    if not math.isfinite(x):
        raise ValueError("X position must be finite")
    if clamp:
        x = min(max(x, x0), x1)
    elif not x0 <= x <= x1:
        raise ValueError(f"X {x:g} m is outside {x0:g}–{x1:g} m")
    r = r0 + (x - x0) / (x1 - x0) * (r1 - r0)
    return int(round(r)) if rounded else r


def repere_interval_to_x(space, start_repere: float,
                          stop_repere: float) -> Tuple[float, float]:
    """Convert an ascending operator interval to ascending physical X."""
    r0 = float(start_repere)
    r1 = float(stop_repere)
    if r1 <= r0:
        raise ValueError("stop repère must exceed start repère")
    xa = repere_to_x(space, r0)
    xb = repere_to_x(space, r1)
    return min(xa, xb), max(xa, xb)


def x_interval_to_reperes(space, start_x: float,
                           stop_x: float) -> Tuple[int, int]:
    """Convert a physical interval to an ascending repère interval."""
    ra = int(x_to_repere(space, start_x))
    rb = int(x_to_repere(space, stop_x))
    return min(ra, rb), max(ra, rb)


def format_repere(value: float) -> str:
    """Compact single-position label, e.g. ``R425``."""
    v = float(value)
    token = str(int(round(v))) if abs(v - round(v)) < 1e-9 else f"{v:g}"
    return f"R{token}"


def format_repere_range(start_repere: float, stop_repere: float) -> str:
    """Compact interval label, e.g. ``R400–R600``."""
    lo, hi = sorted((float(start_repere), float(stop_repere)))
    return f"{format_repere(lo)}–{format_repere(hi)}"


def job_repere_range(space, job) -> Tuple[int, int]:
    """Derived repère range for a job whose canonical range is in metres."""
    return x_interval_to_reperes(space, job.start_x, job.stop_x)


def job_repere_label(space, job) -> str:
    """Formatted derived range for a job."""
    return format_repere_range(*job_repere_range(space, job))
