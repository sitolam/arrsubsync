"""Syncing one subtitle, and proving the result is right before keeping it."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app import alass, config, db
from app.alass import logx
from app.shifts import describe, worst_shift

_semaphore: asyncio.Semaphore | None = None
_semaphore_size = 0


def semaphore(size: int) -> asyncio.Semaphore:
    """One alass process pool, resized when the setting changes."""
    global _semaphore, _semaphore_size
    if _semaphore is None or _semaphore_size != size:
        _semaphore = asyncio.Semaphore(size)
        _semaphore_size = size
    return _semaphore


class Rejected(Exception):
    """The request cannot be acted on; nothing was touched."""

    def __init__(self, message: str, field_name: str = "subtitle") -> None:
        super().__init__(message)
        self.message = message
        self.field = field_name


@dataclass
class Result:
    status: str                      # ok | reverted | failed | rejected
    subtitle: str
    video: str
    request_id: str
    summary: str | None = None
    verify_summary: str | None = None
    applied_shift: float | None = None
    residual: float | None = None
    error: str | None = None
    duration: float = 0.0
    replaced: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def http_status(self) -> int:
        return {"ok": 200, "rejected": 400, "reverted": 409, "failed": 502}.get(self.status, 500)

    def as_dict(self) -> dict[str, Any]:
        body = {
            "status": self.status,
            "request_id": self.request_id,
            "video": self.video,
            "subtitle": self.subtitle,
            "alass_summary": self.summary,
            "verify_summary": self.verify_summary,
            "applied_shift_seconds": self.applied_shift,
            "duration_seconds": round(self.duration, 2),
            "replaced": self.replaced,
        }
        if self.error:
            body["error"] = self.error
        body.update(self.details)
        return body


def validate(raw: str, field_name: str, *, writable: bool = False) -> Path:
    if not raw or not raw.strip():
        raise Rejected(f"{field_name} is empty", field_name)
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise Rejected(
            f"{field_name} must be an absolute path inside this container (got {raw!r})", field_name
        )
    resolved = candidate.resolve()
    if resolved != config.MEDIA_ROOT and config.MEDIA_ROOT not in resolved.parents:
        raise Rejected(
            f"{field_name} resolves outside the media root {config.MEDIA_ROOT} ({resolved})", field_name
        )
    if not resolved.exists():
        raise Rejected(f"{field_name} does not exist inside this container: {resolved}", field_name)
    if not resolved.is_file():
        raise Rejected(f"{field_name} is not a regular file: {resolved}", field_name)
    if writable:
        if not os.access(resolved, os.W_OK):
            raise Rejected(f"{field_name} is not writable by this container: {resolved}", field_name)
        if not os.access(resolved.parent, os.W_OK):
            raise Rejected(
                f"directory of {field_name} is not writable by this container: {resolved.parent}",
                field_name,
            )
    return resolved


def replace_file(original: Path, new: Path) -> None:
    """Move `new` over `original`, keeping the original's mode and owner."""
    st = original.stat()
    os.chmod(new, stat.S_IMODE(st.st_mode))
    try:
        os.chown(new, st.st_uid, st.st_gid)
    except PermissionError:
        pass
    os.replace(new, original)


def back_up(subtitle: Path, settings: dict[str, Any]) -> Path | None:
    if not settings.get("backup_enabled"):
        return None
    root = Path(settings.get("backup_dir") or (config.MEDIA_ROOT / ".arrsubsync-backups"))
    try:
        relative = subtitle.relative_to(config.MEDIA_ROOT)
    except ValueError:
        relative = Path(subtitle.name)
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():          # keep the pristine copy, never the latest
        shutil.copy2(subtitle, target)
    return target


async def sync(video_raw: str, subtitle_raw: str, *, dry_run: bool = False,
               overrides: dict[str, Any] | None = None) -> Result:
    """Align one subtitle to its video, verify the result, and keep it only if it holds."""
    request_id = uuid.uuid4().hex[:12]
    started = time.monotonic()
    settings = db.get_settings()
    overrides = overrides or {}

    video = validate(video_raw, "video")
    subtitle = validate(subtitle_raw, "subtitle", writable=not dry_run)

    if subtitle.suffix.lower() not in config.SUBTITLE_SUFFIXES:
        raise Rejected(
            f"unsupported subtitle format {subtitle.suffix!r}; alass reads "
            f"{sorted(config.SUBTITLE_SUFFIXES)}"
        )
    if video == subtitle:
        raise Rejected("video and subtitle are the same file")

    timeout = float(overrides.get("timeout") or settings["alass_timeout"])
    # alass decides the output format from the extension, so it must be kept.
    output = subtitle.parent / f".arrsubsync-{request_id}{subtitle.suffix}"
    argv = alass.build_argv(video, subtitle, output, settings, overrides)

    logx(logging.INFO, "sync started", request_id=request_id, video=str(video),
         subtitle=str(subtitle), argv=argv)

    result = Result(status="ok", subtitle=str(subtitle), video=str(video), request_id=request_id)

    try:
        async with semaphore(int(settings["max_concurrency"])):
            code, stdout, stderr = await alass.run(argv, timeout)
    except asyncio.TimeoutError:
        output.unlink(missing_ok=True)
        result.status, result.error = "failed", f"alass timed out after {timeout:g}s and was killed"
        return _finish(result, started, subtitle, video)
    except FileNotFoundError:
        result.status, result.error = "failed", f"alass binary not found at {config.ALASS_BIN!r}"
        return _finish(result, started, subtitle, video)

    result.summary = alass.summarise(stdout) or alass.summarise(stderr)

    if code != 0 or not output.exists() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        result.status = "failed"
        result.error = f"alass exited with code {code}"
        result.details = {"returncode": code, "stdout": stdout, "stderr": stderr}
        return _finish(result, started, subtitle, video)

    result.applied_shift = worst_shift(result.summary)

    if dry_run:
        output.unlink(missing_ok=True)
        return _finish(result, started, subtitle, video, record=False)

    back_up(subtitle, settings)
    keep = subtitle.parent / f".arrsubsync-original-{request_id}{subtitle.suffix}"
    shutil.copy2(subtitle, keep)

    try:
        replace_file(subtitle, output)
    except OSError as exc:
        output.unlink(missing_ok=True)
        keep.unlink(missing_ok=True)
        result.status = "failed"
        result.error = f"could not replace the original subtitle: {exc}"
        return _finish(result, started, subtitle, video)

    result.replaced = True

    verify = settings["verify_after"] if overrides.get("verify_after") is None else overrides["verify_after"]
    threshold = float(overrides.get("verify_threshold") or settings["verify_threshold"])

    if verify:
        check = subtitle.parent / f".arrsubsync-verify-{request_id}{subtitle.suffix}"
        try:
            async with semaphore(int(settings["max_concurrency"])):
                vcode, vout, verr = await alass.run(
                    alass.build_argv(video, subtitle, check, settings, overrides), timeout
                )
            check.unlink(missing_ok=True)
            if vcode == 0:
                result.verify_summary = alass.summarise(vout) or alass.summarise(verr)
                result.residual = worst_shift(result.verify_summary)
                if result.residual > threshold:
                    shutil.copy2(keep, subtitle)
                    result.status = "reverted"
                    result.replaced = False
                    result.error = (
                        "alass did not converge: after syncing it wanted to move the subtitle by "
                        f"another {describe(result.residual)}. The subtitle does not match this "
                        "audio, so the original was restored."
                    )
        except Exception as exc:  # noqa: BLE001 - a failed check must never lose the file
            check.unlink(missing_ok=True)
            logx(logging.WARNING, "verification could not run", request_id=request_id, error=repr(exc))
        finally:
            keep.unlink(missing_ok=True)
    else:
        keep.unlink(missing_ok=True)

    return _finish(result, started, subtitle, video)


def _finish(result: Result, started: float, subtitle: Path, video: Path,
            record: bool = True) -> Result:
    result.duration = time.monotonic() - started

    level = logging.INFO if result.status == "ok" else logging.WARNING
    logx(level, f"sync {result.status}", request_id=result.request_id, subtitle=str(subtitle),
         summary=result.summary, verify=result.verify_summary, error=result.error,
         duration_seconds=round(result.duration, 2))

    if record:
        record_result(result, subtitle, video)
    return result


def record_result(result: Result, subtitle: Path, video: Path) -> None:
    """Write what happened into the database, for the dashboard and the scheduler."""
    try:
        st = subtitle.stat()
    except OSError:
        return

    if result.status == "ok":
        status = db.FIXED if (result.applied_shift or 0) > 0.05 else db.OK
    elif result.status == "reverted":
        status = db.REVERTED
    else:
        status = db.FAILED

    fields: dict[str, Any] = {
        "status": status,
        "summary": result.summary,
        "verify_summary": result.verify_summary,
        "worst_shift": result.residual if result.residual is not None else result.applied_shift,
        "size": st.st_size,
        "mtime": st.st_mtime,
        "checked_at": time.time(),
    }
    if status == db.FIXED:
        fields["applied_shift"] = result.applied_shift
        fields["fixed_at"] = time.time()

    db.upsert_subtitle(str(subtitle), str(video), **fields)
    db.bump(str(subtitle), "attempts")

    message = {
        db.FIXED: f"corrected by {describe(result.applied_shift or 0)}",
        db.OK: "already in sync",
        db.REVERTED: "could not be aligned; original restored",
        db.FAILED: result.error or "alass failed",
    }[status]
    db.log_event(
        "sync", message,
        level="info" if status in (db.OK, db.FIXED) else "warning",
        path=str(subtitle),
        data={"summary": result.summary, "verify": result.verify_summary},
    )
