"""alass-sync: a tiny HTTP sidecar that re-syncs subtitles with alass.

Bazarr (or anything else on the same Docker network) POSTs a video path and a
subtitle path; this service runs `alass <video> <subtitle> <tmp>` and, on
success, atomically replaces the original subtitle file with the synced one.

Both containers mount the same media volume, so paths are passed by reference
and nothing is uploaded.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import stat
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Configuration (all via environment variables)
# --------------------------------------------------------------------------- #

MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "/data")).resolve()
ALASS_BIN = os.environ.get("ALASS_BIN", "alass")
ALASS_TIMEOUT = float(os.environ.get("ALASS_TIMEOUT", "120"))
ALASS_SPLIT_PENALTY = os.environ.get("ALASS_SPLIT_PENALTY", "").strip()
ALASS_NO_SPLITS = os.environ.get("ALASS_NO_SPLITS", "false").lower() in {"1", "true", "yes"}
ALASS_SPEED_OPTIMIZATION = os.environ.get("ALASS_SPEED_OPTIMIZATION", "").strip()
ALASS_DISABLE_FPS_GUESSING = os.environ.get("ALASS_DISABLE_FPS_GUESSING", "false").lower() in {"1", "true", "yes"}
MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY", "2"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# alass reads these itself, but we surface them so misconfiguration is visible.
FFMPEG_PATH = os.environ.get("ALASS_FFMPEG_PATH", "ffmpeg")
FFPROBE_PATH = os.environ.get("ALASS_FFPROBE_PATH", "ffprobe")

SUBTITLE_SUFFIXES = {".srt", ".ssa", ".ass", ".idx"}

_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)


# --------------------------------------------------------------------------- #
# Structured logging to stdout
# --------------------------------------------------------------------------- #


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(LOG_LEVEL)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uv = logging.getLogger(name)
        uv.handlers = [handler]
        uv.propagate = False
    return logging.getLogger("alass-sync")


log = _setup_logging()


def logx(level: int, msg: str, **fields: Any) -> None:
    log.log(level, msg, extra={"extra_fields": fields})


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class SyncRequest(BaseModel):
    video: str = Field(..., description="Absolute path to the video file, as seen in this container.")
    subtitle: str = Field(..., description="Absolute path to the subtitle file, as seen in this container.")
    split_penalty: float | None = Field(
        default=None, description="Override alass --split-penalty (useful range 5-20)."
    )
    no_splits: bool | None = Field(
        default=None, description="Override: run alass --no-splits (pure time shift, much faster)."
    )
    speed_optimization: float | None = Field(
        default=None,
        description="alass -O: 0 disables the speed optimisation and aligns more accurately.",
    )
    disable_fps_guessing: bool | None = Field(
        default=None, description="alass -g: do not guess/correct a framerate difference."
    )
    dry_run: bool = Field(
        default=False, description="Run alass but do not replace the original subtitle file."
    )


class ValidationError(Exception):
    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


# --------------------------------------------------------------------------- #
# Path validation
# --------------------------------------------------------------------------- #


def validate_path(raw: str, field: str, *, must_be_writable: bool = False) -> Path:
    """Resolve `raw` and confirm it is an existing regular file under MEDIA_ROOT."""
    if not raw or not raw.strip():
        raise ValidationError(f"{field} is empty", field)

    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValidationError(
            f"{field} must be an absolute path as seen inside the container (got {raw!r})", field
        )

    # resolve() collapses `..` and follows symlinks, so the containment check
    # below cannot be defeated by traversal or a symlink pointing outside.
    resolved = candidate.resolve()

    if resolved != MEDIA_ROOT and MEDIA_ROOT not in resolved.parents:
        raise ValidationError(f"{field} resolves outside the media root {MEDIA_ROOT} ({resolved})", field)

    if not resolved.exists():
        raise ValidationError(f"{field} does not exist inside this container: {resolved}", field)

    if not resolved.is_file():
        raise ValidationError(f"{field} is not a regular file: {resolved}", field)

    if must_be_writable:
        if not os.access(resolved, os.W_OK):
            raise ValidationError(f"{field} is not writable by this container: {resolved}", field)
        if not os.access(resolved.parent, os.W_OK):
            raise ValidationError(
                f"directory of {field} is not writable by this container: {resolved.parent}", field
            )

    return resolved


# --------------------------------------------------------------------------- #
# alass invocation
# --------------------------------------------------------------------------- #


def build_argv(video: Path, subtitle: Path, output: Path, req: SyncRequest) -> list[str]:
    argv = [ALASS_BIN, str(video), str(subtitle), str(output)]
    no_splits = ALASS_NO_SPLITS if req.no_splits is None else req.no_splits
    if no_splits:
        argv.append("--no-split")
    else:
        penalty = req.split_penalty if req.split_penalty is not None else (
            float(ALASS_SPLIT_PENALTY) if ALASS_SPLIT_PENALTY else None
        )
        if penalty is not None:
            argv += ["--split-penalty", str(penalty)]

    speed = req.speed_optimization if req.speed_optimization is not None else (
        float(ALASS_SPEED_OPTIMIZATION) if ALASS_SPEED_OPTIMIZATION else None
    )
    if speed is not None:
        argv += ["--speed-optimization", str(speed)]

    no_fps_guess = (
        ALASS_DISABLE_FPS_GUESSING if req.disable_fps_guessing is None else req.disable_fps_guessing
    )
    if no_fps_guess:
        argv.append("--disable-fps-guessing")
    return argv


def clean_alass_output(text: str) -> str:
    """Strip alass's carriage-return progress bars, keeping the informative lines."""
    lines = []
    for raw in text.splitlines():
        line = raw.split("\r")[-1].strip()
        if not line or ("%" in line and "[" in line and "]" in line):
            continue
        lines.append(line)
    return "\n".join(lines)


def alass_summary(text: str) -> str | None:
    """Summarise what alass did.

    alass prints one "shifted block of N subtitles ... by T" line per segment,
    so reporting only the last one hides the rest — and the spread between
    segments is exactly what tells you whether the alignment is trustworthy.
    """
    lines = [
        line
        for line in clean_alass_output(text).splitlines()
        if "shifted" in line.lower() or "split" in line.lower()
    ]
    if not lines:
        return None
    if len(lines) == 1:
        return lines[0]
    shifts = [line.rsplit(" by ", 1)[-1] for line in lines if " by " in line]
    if len(shifts) == len(lines):
        return f"{len(lines)} blocks shifted by " + ", ".join(shifts)
    return " | ".join(lines)


async def run_alass(argv: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=ALASS_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return (
        proc.returncode if proc.returncode is not None else -1,
        clean_alass_output(stdout.decode("utf-8", "replace")),
        clean_alass_output(stderr.decode("utf-8", "replace")),
    )


def replace_subtitle(original: Path, synced: Path) -> None:
    """Atomically move `synced` over `original`, preserving mode and ownership.

    The sidecar normally runs as root while Bazarr writes as PUID/PGID, so
    copying the original file's owner and mode keeps the library consistent.
    """
    st = original.stat()
    os.chmod(synced, stat.S_IMODE(st.st_mode))
    try:
        os.chown(synced, st.st_uid, st.st_gid)
    except PermissionError:
        logx(
            logging.WARNING,
            "could not preserve subtitle ownership (not running as root)",
            subtitle=str(original),
            uid=st.st_uid,
            gid=st.st_gid,
        )
    # Same directory, same filesystem -> os.replace is atomic.
    os.replace(synced, original)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

app = FastAPI(
    title="alass-sync",
    version="1.0.0",
    description="Subtitle re-sync sidecar for Bazarr, powered by alass.",
)


@app.get("/health")
async def health() -> dict[str, Any]:
    alass_path = shutil.which(ALASS_BIN)
    ffmpeg_ok = shutil.which(FFMPEG_PATH) is not None
    ffprobe_ok = shutil.which(FFPROBE_PATH) is not None
    healthy = bool(alass_path) and ffmpeg_ok and ffprobe_ok and MEDIA_ROOT.is_dir()
    body = {
        "status": "ok" if healthy else "degraded",
        "alass": alass_path,
        "ffmpeg": ffmpeg_ok,
        "ffprobe": ffprobe_ok,
        "media_root": str(MEDIA_ROOT),
        "media_root_mounted": MEDIA_ROOT.is_dir(),
        "timeout_seconds": ALASS_TIMEOUT,
        "max_concurrency": MAX_CONCURRENCY,
    }
    return JSONResponse(status_code=200 if healthy else 503, content=body)


@app.post("/sync")
async def sync(req: SyncRequest) -> JSONResponse:
    request_id = uuid.uuid4().hex[:12]
    started = time.monotonic()

    try:
        video = validate_path(req.video, "video")
        subtitle = validate_path(req.subtitle, "subtitle", must_be_writable=True)
    except ValidationError as exc:
        logx(logging.ERROR, "rejected request", request_id=request_id, error=exc.message, field=exc.field)
        return JSONResponse(
            status_code=400,
            content={"status": "error", "request_id": request_id, "error": exc.message, "field": exc.field},
        )

    if subtitle.suffix.lower() not in SUBTITLE_SUFFIXES:
        msg = f"unsupported subtitle format {subtitle.suffix!r}; alass supports {sorted(SUBTITLE_SUFFIXES)}"
        logx(logging.ERROR, "rejected request", request_id=request_id, error=msg, field="subtitle")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "request_id": request_id, "error": msg, "field": "subtitle"},
        )

    if video == subtitle:
        msg = "video and subtitle are the same file"
        return JSONResponse(
            status_code=400,
            content={"status": "error", "request_id": request_id, "error": msg, "field": "subtitle"},
        )

    size_before = subtitle.stat().st_size
    # alass infers the output format from the output file's extension and refuses
    # to write a format that differs from the input, so the temporary file must
    # keep the original suffix. The dot prefix keeps it out of library scanners,
    # and it sits in the same directory so os.replace() stays atomic.
    output = subtitle.parent / f".alass-sync-{request_id}{subtitle.suffix}"
    argv = build_argv(video, subtitle, output, req)

    logx(
        logging.INFO,
        "sync started",
        request_id=request_id,
        video=str(video),
        subtitle=str(subtitle),
        argv=argv,
    )

    try:
        async with _semaphore:
            returncode, stdout, stderr = await run_alass(argv)
    except asyncio.TimeoutError:
        output.unlink(missing_ok=True)
        msg = f"alass timed out after {ALASS_TIMEOUT:g}s and was killed"
        logx(logging.ERROR, "sync timeout", request_id=request_id, subtitle=str(subtitle), error=msg)
        return JSONResponse(
            status_code=504,
            content={"status": "error", "request_id": request_id, "error": msg, "subtitle": str(subtitle)},
        )
    except FileNotFoundError:
        msg = f"alass binary not found at {ALASS_BIN!r}"
        logx(logging.ERROR, "sync failed", request_id=request_id, error=msg)
        return JSONResponse(
            status_code=500, content={"status": "error", "request_id": request_id, "error": msg}
        )
    except Exception as exc:  # noqa: BLE001 - report anything unexpected as JSON
        output.unlink(missing_ok=True)
        logx(logging.ERROR, "sync crashed", request_id=request_id, error=repr(exc))
        return JSONResponse(
            status_code=500, content={"status": "error", "request_id": request_id, "error": repr(exc)}
        )

    duration = round(time.monotonic() - started, 2)

    if returncode != 0 or not output.exists() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        logx(
            logging.ERROR,
            "alass failed",
            request_id=request_id,
            subtitle=str(subtitle),
            returncode=returncode,
            stderr=stderr,
            duration_seconds=duration,
        )
        return JSONResponse(
            status_code=502,
            content={
                "status": "error",
                "request_id": request_id,
                "error": f"alass exited with code {returncode}",
                "video": str(video),
                "subtitle": str(subtitle),
                "returncode": returncode,
                "stdout": stdout,
                "stderr": stderr,
                "duration_seconds": duration,
            },
        )

    size_after = output.stat().st_size

    if req.dry_run:
        output.unlink(missing_ok=True)
        logx(logging.INFO, "dry run complete", request_id=request_id, subtitle=str(subtitle))
    else:
        try:
            replace_subtitle(subtitle, output)
        except OSError as exc:
            output.unlink(missing_ok=True)
            msg = f"could not replace original subtitle: {exc}"
            logx(logging.ERROR, "replace failed", request_id=request_id, subtitle=str(subtitle), error=msg)
            return JSONResponse(
                status_code=500,
                content={"status": "error", "request_id": request_id, "error": msg, "subtitle": str(subtitle)},
            )

    logx(
        logging.INFO,
        "sync complete",
        request_id=request_id,
        subtitle=str(subtitle),
        bytes_before=size_before,
        bytes_after=size_after,
        duration_seconds=duration,
        dry_run=req.dry_run,
    )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "request_id": request_id,
            "video": str(video),
            "subtitle": str(subtitle),
            "replaced": not req.dry_run,
            "bytes_before": size_before,
            "bytes_after": size_after,
            "duration_seconds": duration,
            # alass reports the offsets and split count it applied on stdout.
            "alass_summary": alass_summary(stdout) or alass_summary(stderr),
            "alass_stdout": stdout,
            "alass_stderr": stderr,
        },
    )
