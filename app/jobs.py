"""Background work: scanning, fixing and healing the library.

Everything long-running goes through one job at a time so two sweeps can never
fight over the same files, and so the dashboard always has a single progress
number to show.
"""

from __future__ import annotations

import asyncio
import logging
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
                        totals["fixed" if (result.applied_shift or 0) > 0.05 else "ok"] += 1
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


async def heal_job(job: Job, paths: list[str] | None = None) -> None:
    """Replace subtitles that cannot be aligned, then sync whatever arrives.

    For each bad subtitle: ask Bazarr for a different one, wait for it to land,
    sync it, and verify. Repeat until it sticks or the attempt budget runs out.
    """
    settings = db.get_settings()
    client = bazarr.Bazarr.from_settings()
    if client is None:
        raise RuntimeError("Bazarr is not configured; set its URL and API key in Settings")

    if paths:
        targets = [db.get_subtitle(p) for p in paths]
        targets = [t for t in targets if t]
    else:
        targets = [row for row in db.list_subtitles(limit=100000)
                   if row["status"] in db.BAD and not row["ignored"]]

    job.total = len(targets)
    max_attempts = int(settings["max_replace_attempts"])
    wait = float(settings["replace_wait_seconds"])
    totals = {"healed": 0, "replaced": 0, "gave_up": 0, "errors": 0}

    for row in targets:
        if job.cancelled:
            break
        subtitle = Path(row["path"])
        video = row["video"]
        job.current = subtitle.name
        healed = False
        unavailable = False

        # Bazarr identifies a subtitle by language plus the HI and forced flags,
        # which live in the filename: Foo.en.hi.srt is a different subtitle to
        # Foo.en.srt, and asking for the wrong one gets "file not found".
        pair = scanner.Pair(Path(video), subtitle)
        tags = [t.lower() for t in pair.tags]
        language = pair.language or row["language"] or "en"
        hi = any(t in ("hi", "sdh", "cc") for t in tags)
        forced = "forced" in tags

        for attempt in range(1, max_attempts + 1):
            if job.cancelled:
                break
            try:
                before = subtitle.stat().st_mtime if subtitle.exists() else 0
                how = client.replace(video, str(subtitle), language, hi=hi, forced=forced)
                totals["replaced"] += 1
                db.bump(str(subtitle), "replacements")
                db.log_event("heal", f"attempt {attempt}: {how}", path=str(subtitle))
            except bazarr.BazarrError as exc:
                totals["errors"] += 1
                db.log_event("heal", f"Bazarr refused: {exc}", level="error", path=str(subtitle))
                break

            # Wait for a new file to appear. Bazarr's own post-processing hook
            # may already have synced it, which is fine - we check either way.
            arrived = False
            deadline = time.time() + wait
            while time.time() < deadline and not job.cancelled:
                await asyncio.sleep(3)
                if subtitle.exists() and subtitle.stat().st_mtime > before:
                    arrived = True
                    break
            if not arrived:
                # Bazarr found nothing to download. Trying again would only
                # blacklist another subtitle we never saw, so stop here.
                db.log_event(
                    "heal",
                    f"attempt {attempt}: Bazarr found no replacement within {wait:g}s - "
                    "no other subtitle seems to be available",
                    level="warning", path=str(subtitle),
                )
                unavailable = True
                break

            await asyncio.sleep(2)     # let the writer finish
            try:
                result = await syncer.sync(video, str(subtitle))
            except syncer.Rejected as exc:
                db.log_event("heal", exc.message, level="warning", path=str(subtitle))
                continue

            if result.status == "ok":
                healed = True
                totals["healed"] += 1
                db.log_event(
                    "heal",
                    f"fixed after {attempt} replacement(s): {describe(result.applied_shift or 0)}",
                    path=str(subtitle),
                )
                break

        if not healed and not job.cancelled:
            totals["gave_up"] += 1
            note = ("Bazarr has no other subtitle" if unavailable
                    else f"no working subtitle found in {max_attempts} tries")
            db.log_event("heal", note, level="warning", path=str(subtitle))
            db.upsert_subtitle(str(subtitle), video, note=note)

        job.done += 1
        job.totals = dict(totals)

    job.totals = dict(totals)


async def full_job(job: Job) -> None:
    """Everything, in order: index, correct, then heal what is left."""
    await scan_job(job)
    job.done, job.total = 0, 0
    await check_job(job, apply=True, statuses=[db.UNKNOWN, db.OFF, db.OK, db.FIXED])
    if db.get_setting("auto_replace") and bazarr.Bazarr.from_settings():
        checked = dict(job.totals)
        job.done, job.total = 0, 0
        await heal_job(job)
        job.totals = {**checked, **job.totals}
