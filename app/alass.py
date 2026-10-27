"""Running alass and making sense of what it says."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from app import config

log = logging.getLogger("arrsubsync")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(record.created)),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(config.LOG_LEVEL)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False


def logx(level: int, message: str, **fields: Any) -> None:
    log.log(level, message, extra={"extra_fields": fields})


def clean_output(text: str) -> str:
    """Strip alass's carriage-return progress bars, keeping the useful lines."""
    lines = []
    for raw in text.splitlines():
        line = raw.split("\r")[-1].strip()
        if not line or ("%" in line and "[" in line and "]" in line):
            continue
        lines.append(line)
    return "\n".join(lines)


def summarise(text: str) -> str | None:
    """One line describing what alass did, covering every block it moved."""
    lines = [ln for ln in clean_output(text).splitlines()
             if "shifted" in ln.lower() or "split" in ln.lower()]
    if not lines:
        return None
    if len(lines) == 1:
        return lines[0]
    shifts = [ln.rsplit(" by ", 1)[-1] for ln in lines if " by " in ln]
    if len(shifts) == len(lines):
        return f"{len(lines)} blocks shifted by " + ", ".join(shifts)
    return " | ".join(lines)


# alass reads subtitles as UTF-8 unless told otherwise. Older Western European
# subtitles are often Windows-1252, which it rejects with this message.
CHARSET_ERROR = "wrong charset encoding"
FALLBACK_ENCODING = "windows-1252"


def charset_error(*texts: str) -> bool:
    return any(CHARSET_ERROR in text for text in texts)


def build_argv(video: Path, subtitle: Path, output: Path, settings: dict[str, Any],
               overrides: dict[str, Any] | None = None, *, encoding: str | None = None) -> list[str]:
    opts = dict(settings)
    opts.update({k: v for k, v in (overrides or {}).items() if v is not None})
    argv = [config.ALASS_BIN, str(video), str(subtitle), str(output)]
    if encoding:
        argv += ["--encoding-inc", encoding]

    if opts.get("alass_no_split"):
        argv.append("--no-split")          # alass spells it singular
    elif str(opts.get("alass_split_penalty", "")).strip():
        argv += ["--split-penalty", str(opts["alass_split_penalty"])]

    if str(opts.get("alass_speed_optimization", "")).strip() != "":
        argv += ["--speed-optimization", str(opts["alass_speed_optimization"])]
    return argv


async def run(argv: list[str], timeout: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return (
        proc.returncode if proc.returncode is not None else -1,
        clean_output(stdout.decode("utf-8", "replace")),
        clean_output(stderr.decode("utf-8", "replace")),
    )


async def run_sync(video: Path, subtitle: Path, output: Path, settings: dict[str, Any],
                   overrides: dict[str, Any] | None, timeout: float) -> tuple[int, str, str, list[str]]:
    """Run alass, retrying as Windows-1252 when the subtitle is not UTF-8."""
    argv = build_argv(video, subtitle, output, settings, overrides)
    code, stdout, stderr = await run(argv, timeout)
    if code != 0 and charset_error(stdout, stderr):
        output.unlink(missing_ok=True)
        argv = build_argv(video, subtitle, output, settings, overrides, encoding=FALLBACK_ENCODING)
        code, stdout, stderr = await run(argv, timeout)
    return code, stdout, stderr, argv


def available() -> dict[str, Any]:
    return {
        "alass": shutil.which(config.ALASS_BIN),
        "ffmpeg": shutil.which(config.FFMPEG_PATH) is not None,
        "ffprobe": shutil.which(config.FFPROBE_PATH) is not None,
    }
