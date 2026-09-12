"""Parsing of alass's per-block shift reports, shared by the service and the CLI."""

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
    shifts = parse_shifts(summary)
    return max((abs(v) for v in shifts), default=0.0)
