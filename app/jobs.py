"""Background work: scanning, fixing and healing the library.

Everything long-running goes through one job at a time so two sweeps can never
fight over the same files, and so the dashboard always has a single progress
number to show.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from app import bazarr, config, db, scanner, syncer
from app.alass import logx
from app.shifts import describe


@dataclass
class Job:
    kind: str
    total: int = 0
    done: int = 0
    current: str = ""
    started: float = field(default_factory=time.time)
    finished: float | None = None
    cancelled: bool = False
    totals: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        elapsed = (self.finished or time.time()) - self.started
        rate = self.done / elapsed if elapsed > 0 and self.done else 0
        return {
            "kind": self.kind,
            "total": self.total,
            "done": self.done,
            "current": self.current,
            "running": self.finished is None,
            "cancelled": self.cancelled,
            "elapsed_seconds": round(elapsed, 1),
            "eta_seconds": round((self.total - self.done) / rate, 0) if rate else None,
            "totals": self.totals,
            "error": self.error,
        }


class Runner:
    """Holds the one job that may be running, and the last one that finished."""

    def __init__(self) -> None:
        self.job: Job | None = None
        self._task: asyncio.Task | None = None

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, kind: str, coro_factory: Callable[[Job], Awaitable[None]]) -> Job:
        if self.busy:
            raise RuntimeError(f"a {self.job.kind if self.job else 'job'} is already running")
        job = Job(kind=kind)
        self.job = job
        run_id = db.start_run(kind)

        async def wrapper() -> None:
            try:
                await coro_factory(job)
                status = "cancelled" if job.cancelled else "finished"
            except Exception as exc:  # noqa: BLE001 - surfaced on the dashboard
                job.error = repr(exc)
                status = "failed"
                logx(logging.ERROR, "job failed", kind=kind, error=repr(exc))
            finally:
                job.finished = time.time()
                db.finish_run(run_id, status, job.totals)
                db.log_event("job", f"{kind} {status}: {job.totals}", kind_level(status))

        self._task = asyncio.create_task(wrapper())
        return job

    def cancel(self) -> bool:
        if self.busy and self.job:
            self.job.cancelled = True
            return True
        return False


def kind_level(status: str) -> str:
    return {"finished": "info", "cancelled": "warning", "failed": "error"}.get(status, "info")


runner = Runner()


# --------------------------------------------------------------------------- #
# the jobs themselves
# --------------------------------------------------------------------------- #


def collect(root: Path | None = None) -> list[scanner.Pair]:
    settings = db.get_settings()
    base = root or config.MEDIA_ROOT
    return [p for p in scanner.find_pairs(base) if scanner.wanted(p, settings)]


async def scan_job(job: Job, root: Path | None = None) -> None:
    """Index the library: record every pair, without running alass."""
    pairs = collect(root)
    job.total = len(pairs)
    seen = []
    for pair in pairs:
        if job.cancelled:
            break
        job.done += 1
        job.current = pair.subtitle.name
        seen.append(str(pair.subtitle))
        existing = db.get_subtitle(str(pair.subtitle))
        st = pair.subtitle.stat()
        changed = not existing or existing["size"] != st.st_size or existing["mtime"] != st.st_mtime
        db.upsert_subtitle(
            str(pair.subtitle), str(pair.video),
            language=pair.language, size=st.st_size, mtime=st.st_mtime,
            **({"status": db.UNKNOWN} if changed else {}),
        )
        if job.done % 200 == 0:
            await asyncio.sleep(0)
    removed = db.forget_missing(seen)
    job.totals = {"indexed": len(seen), "removed": removed}


async def check_job(job: Job, *, apply: bool, only: list[str] | None = None,
                    statuses: list[str] | None = None, root: Path | None = None) -> None:
    """Check subtitles against their audio, optionally correcting them."""
    settings = db.get_settings()
    if only:
        pairs = []
        for path in only:
            subtitle = Path(path)
            video = scanner.video_for(subtitle)
            if video:
                pairs.append(scanner.Pair(video, subtitle))
    else:
        pairs = collect(root)
        if statuses:
            wanted_paths = {
                row["path"] for row in db.list_subtitles(limit=100000)
                if row["status"] in statuses and not row["ignored"]
            }
            pairs = [p for p in pairs if str(p.subtitle) in wanted_paths]

    job.total = len(pairs)
    totals = {"ok": 0, "fixed": 0, "off": 0, "reverted": 0, "failed": 0}
    workers = max(1, int(settings["max_concurrency"]))
    queue: asyncio.Queue[scanner.Pair] = asyncio.Queue()
    for pair in pairs:
        queue.put_nowait(pair)

    async def worker() -> None:
        while not queue.empty() and not job.cancelled:
            pair = await queue.get()
            job.current = pair.subtitle.name
            try:
                result = await syncer.sync(str(pair.video), str(pair.subtitle), dry_run=not apply)
                if result.status == "ok":
                    if apply:
                        totals["fixed" if result.replaced else "ok"] += 1
                    else:
                        shift = result.applied_shift or 0
                        threshold = float(settings["verify_threshold"])
                        key = "off" if shift > threshold else "ok"
                        totals[key] += 1
                        db.upsert_subtitle(
                            str(pair.subtitle), str(pair.video),
                            status=db.OFF if key == "off" else db.OK,
                            summary=result.summary, worst_shift=shift, checked_at=time.time(),
                        )
                else:
                    totals[result.status] += 1
            except syncer.Rejected as exc:
                totals["failed"] += 1
                db.log_event("sync", exc.message, level="warning", path=str(pair.subtitle))
            except Exception as exc:  # noqa: BLE001
                totals["failed"] += 1
                logx(logging.ERROR, "check failed", subtitle=str(pair.subtitle), error=repr(exc))
            finally:
                job.done += 1
                job.totals = dict(totals)
                queue.task_done()

    await asyncio.gather(*[worker() for _ in range(workers)])
    job.totals = dict(totals)


def keep_copy(subtitle: Path) -> Path:
    """Copy a subtitle aside before Bazarr is asked to delete it."""
    try:
        relative = subtitle.relative_to(config.MEDIA_ROOT)
    except ValueError:
        relative = Path(subtitle.name)
    target = config.DATA_DIR / "replaced" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(subtitle, target)
    return target


def restore(saved: Path, subtitle: Path, owner: os.stat_result) -> None:
    shutil.copy2(saved, subtitle)
    try:
        os.chown(subtitle, owner.st_uid, owner.st_gid)
    except PermissionError:
        pass


async def wait_for_new(video: Path, before: dict[Path, float], wait: float, job: Job) -> Path | None:
    """The subtitle Bazarr wrote for `video` since `before`, whatever it named it."""
    deadline = time.time() + wait
    while time.time() < deadline and not job.cancelled:
        await asyncio.sleep(3)
        now = scanner.subtitles_beside(video)
        fresh = [p for p, mtime in now.items() if p not in before or mtime > before[p]]
        if fresh:
            return max(fresh, key=lambda p: now[p])
    return None


def heal_targets(paths: list[str] | None, automatic: bool) -> list[dict[str, Any]]:
    if paths:
        rows = [r for r in (db.get_subtitle(p) for p in paths) if r]
    else:
        # Only a verified mismatch is evidence the subtitle is wrong. alass
        # failing - a timeout, an audio track it cannot read - says nothing
        # about the subtitle, and is no reason to delete it.
        rows = [r for r in db.list_subtitles(status=db.REVERTED, limit=100000) if not r["ignored"]]
    if not automatic:
        return rows

    def given_up(row: dict[str, Any]) -> bool:
        try:
            return row["given_up_mtime"] == Path(row["path"]).stat().st_mtime
        except OSError:
            return False

    return [r for r in rows if not given_up(r)]


async def heal_job(job: Job, paths: list[str] | None = None, *, automatic: bool | None = None) -> None:
    """Replace subtitles that cannot be aligned, then sync whatever arrives.

    For each bad subtitle: ask Bazarr for a different one, wait for it to land,
    sync it, and verify. Repeat until it sticks or the attempt budget runs out.
    Whatever happens, the video is never left with less than it had: if no
    replacement arrives, the original is put back.

    Automatic runs skip a subtitle already given up on, until its file changes;
    asking for specific paths from the dashboard always tries.
    """
    settings = db.get_settings()
    client = bazarr.Bazarr.from_settings()
    if client is None:
        raise RuntimeError("Bazarr is not configured; set its URL and API key in Settings")

    targets = heal_targets(paths, paths is None if automatic is None else automatic)
    job.total = len(targets)
    totals = {"healed": 0, "replaced": 0, "restored": 0, "gave_up": 0, "errors": 0}

    for row in targets:
        if job.cancelled:
            break
        job.current = Path(row["path"]).name
        await heal_one(job, client, row, settings, totals)
        job.done += 1
        job.totals = dict(totals)

    job.totals = dict(totals)


async def heal_one(job: Job, client: bazarr.Bazarr, row: dict[str, Any], settings: dict[str, Any],
                   totals: dict[str, int]) -> None:
    subtitle, video = Path(row["path"]), Path(row["video"])
    if not subtitle.exists():
        db.forget(str(subtitle))
        return

    # Bazarr identifies a subtitle by language plus the HI and forced flags.
    # The client prefers Bazarr's own record; the filename is the fallback.
    pair = scanner.Pair(video, subtitle)
    stored = row["language"] if row["language"] not in scanner.FLAG_TAGS else None
    language = pair.language or stored or "en"
    owner = subtitle.stat()
    saved = keep_copy(subtitle)

    current = subtitle
    healed = unavailable = False
    refused = None
    max_attempts = int(settings["max_replace_attempts"])
    wait = float(settings["replace_wait_seconds"])
    try:
        for attempt in range(1, max_attempts + 1):
            if job.cancelled:
                break
            before = scanner.subtitles_beside(video)
            try:
                how = client.replace(str(video), str(current), language, hi=pair.hi, forced=pair.forced)
            except bazarr.BazarrError as exc:
                totals["errors"] += 1
                refused = f"Bazarr refused: {exc}"
                db.log_event("heal", refused, level="error", path=str(current))
                break
            totals["replaced"] += 1
            db.bump(str(current), "replacements")
            db.log_event("heal", f"attempt {attempt}: {how}", path=str(current))

            # Bazarr's own post-processing hook may already have synced it,
            # which is fine - we check either way.
            arrived = await wait_for_new(video, before, wait, job)
            if arrived is None:
                if not job.cancelled:
                    # Trying again would only blacklist another subtitle we
                    # never saw, so stop here.
                    db.log_event(
                        "heal",
                        f"attempt {attempt}: Bazarr found no replacement within {wait:g}s - "
                        "no other subtitle seems to be available",
                        level="warning", path=str(current),
                    )
                    unavailable = True
                break

            await asyncio.sleep(2)     # let the writer finish
            if arrived != current and not current.exists():
                db.forget(str(current))
            current = arrived
            try:
                result = await syncer.sync(str(video), str(current))
            except syncer.Rejected as exc:
                db.log_event("heal", exc.message, level="warning", path=str(current))
                continue

            if result.status == "ok":
                healed = True
                totals["healed"] += 1
                db.log_event(
                    "heal",
                    f"fixed after {attempt} replacement(s): {describe(result.applied_shift or 0)}",
                    path=str(current),
                )
                break
    finally:
        if not current.exists():
            restore(saved, subtitle, owner)
            current = subtitle
            totals["restored"] += 1
            db.log_event("heal", "nothing replaced it, so the original was put back",
                         level="warning", path=str(subtitle))

    if not healed and not job.cancelled:
        totals["gave_up"] += 1
        note = refused or ("Bazarr has no other subtitle" if unavailable
                           else f"no working subtitle found in {max_attempts} tries")
        db.log_event("heal", note, level="warning", path=str(current))
        db.upsert_subtitle(str(current), str(video), note=note,
                           given_up_mtime=current.stat().st_mtime)


async def full_job(job: Job) -> None:
    """Everything, in order: index, correct, then heal what is left."""
    await scan_job(job)
    job.done, job.total = 0, 0
    # Only what is new or changed: a verified subtitle stays verified until its
    # file changes, and the scan resets a changed file to unknown.
    await check_job(job, apply=True, statuses=[db.UNKNOWN, db.OFF])
    if db.get_setting("auto_replace") and bazarr.Bazarr.from_settings():
        checked = dict(job.totals)
        job.done, job.total = 0, 0
        await heal_job(job)
        job.totals = {**checked, **job.totals}
