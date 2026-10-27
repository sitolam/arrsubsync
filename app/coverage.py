"""Spotting subtitles that are timed right but are missing part of the dialogue.

alass aligns whatever lines a subtitle has, so a file with the first ten minutes
cut off still syncs perfectly and verifies as a fixed point. The only sign is in
the timing itself: a long stretch of the video with nothing to say.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app import config
from app.shifts import describe as describe_seconds

# How long a stretch may go without a line before it looks like missing
# dialogue rather than a cold open, a battle, or the end credits.
LEAD_MAX = 360.0
GAP_MAX = 600.0
TAIL_MIN = 600.0
TAIL_FRACTION = 0.15

SRT_RE = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d+)\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d+)")
ASS_RE = re.compile(r"^Dialogue:\s*[^,]*,(\d+):(\d\d):(\d\d)\.(\d+),(\d+):(\d\d):(\d\d)\.(\d+)", re.M)


def _seconds(h: str, m: str, s: str, frac: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(frac) / 10 ** len(frac)


def cues(subtitle: Path) -> list[tuple[float, float]]:
    try:
        text = subtitle.read_bytes().decode("utf-8", "replace")
    except OSError:
        return []
    pattern = ASS_RE if subtitle.suffix.lower() in (".ass", ".ssa") else SRT_RE
    found = []
    for g in pattern.findall(text):
        start, end = _seconds(*g[:4]), _seconds(*g[4:])
        if end >= start:
            found.append((start, end))
    return sorted(found)


def duration(video: Path) -> float | None:
    try:
        out = subprocess.run(
            [config.FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        return float(out) if out else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


@dataclass
class Report:
    gaps: list[tuple[str, float, float]] = field(default_factory=list)   # (where, start, length)

    @property
    def incomplete(self) -> bool:
        return bool(self.gaps)

    @property
    def missing(self) -> float:
        """Seconds of video in the suspicious stretches."""
        return sum(length for _, _, length in self.gaps)

    def describe(self) -> str:
        parts = []
        for where, start, length in self.gaps:
            if where == "lead":
                parts.append(f"no subtitles for the first {describe_seconds(length)}")
            elif where == "tail":
                parts.append(f"no subtitles for the last {describe_seconds(length)}")
            else:
                parts.append(f"no subtitles for {describe_seconds(length)} "
                             f"from {describe_seconds(start)}")
        return "; ".join(parts)


def measure(subtitle: Path, duration: float | None) -> Report:
    report = Report()
    spans = cues(subtitle)
    if not spans:
        return report
    if spans[0][0] > LEAD_MAX:
        report.gaps.append(("lead", 0.0, spans[0][0]))
    end = spans[0][1]
    for start, stop in spans[1:]:
        if start - end > GAP_MAX:
            report.gaps.append(("gap", end, start - end))
        end = max(end, stop)
    if duration:
        tail = duration - end
        if tail > max(TAIL_MIN, TAIL_FRACTION * duration):
            report.gaps.append(("tail", end, tail))
    return report


def check(video: Path, subtitle: Path) -> Report:
    """Measure a subtitle against its video. Forced subtitles are sparse by design."""
    tags = subtitle.name.lower().split(".")
    if "forced" in tags or subtitle.suffix.lower() not in (".srt", ".ass", ".ssa"):
        return Report()
    return measure(subtitle, duration(video))
