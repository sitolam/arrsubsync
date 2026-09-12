"""Parsing of alass's per-block shift reports."""

from __future__ import annotations

import re

SHIFT_RE = re.compile(r"(-?)(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def parse_shifts(summary: str | None) -> list[float]:
    """Return the per-block shifts, in seconds, from an alass summary line."""
    if not summary:
        return []
    tail = summary.split(" by ", 1)[-1] if " by " in summary else summary
    shifts = []
    for sign, hours, minutes, seconds in SHIFT_RE.findall(tail):
        value = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        shifts.append(-value if sign == "-" else value)
    return shifts


def worst_shift(summary: str | None) -> float:
    """The largest absolute shift alass reported, or 0.0 if it reported none."""
    return max((abs(v) for v in parse_shifts(summary)), default=0.0)


def describe(seconds: float) -> str:
    """Human-readable shift, e.g. 20.3s or 4m51s."""
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    if seconds < 60:
        return f"{sign}{seconds:.1f}s"
    return f"{sign}{int(seconds // 60)}m{int(seconds % 60):02d}s"
