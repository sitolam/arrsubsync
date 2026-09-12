"""Command line for arrsubsync, running the same jobs the dashboard does.

    docker exec arrsubsync python -m app.cli status
    docker exec arrsubsync python -m app.cli check              # look, change nothing
    docker exec arrsubsync python -m app.cli fix                # correct what drifted
    docker exec arrsubsync python -m app.cli heal               # replace what cannot be fixed
    docker exec arrsubsync python -m app.cli full               # all three, in order
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app import alass, config, db, jobs

alass.setup_logging()


async def with_progress(kind: str, coro_factory) -> jobs.Job:
    job = jobs.Job(kind=kind)
    task = asyncio.create_task(coro_factory(job))

    last = -1
    while not task.done():
        await asyncio.sleep(1)
        if job.done != last:
            last = job.done
            totals = " ".join(f"{v} {k}" for k, v in (job.totals or {}).items() if v)
            eta = job.as_dict()["eta_seconds"]
            eta_text = f" eta {int(eta // 60)}m{int(eta % 60):02d}s" if eta else ""
            print(f"\r[{job.done}/{job.total}] {totals}{eta_text}  {job.current[:60]}",
                  end="", file=sys.stderr, flush=True)
    await task
    print(file=sys.stderr)
    return job


def print_status() -> None:
    counts = db.counts()
    stats = db.stats()
    print(f"library      : {counts['total']} subtitles tracked")
    print(f"in sync      : {counts['in_sync']}  (ok {counts['ok']}, corrected {counts['fixed']})")
    print(f"out of sync  : {counts['off']}")
    print(f"unfixable    : {counts['needs_replacement']}  (reverted {counts['reverted']}, failed {counts['failed']})")
    print(f"not checked  : {counts['unknown']}")
    print(f"corrections  : {stats['corrected']} subtitles, {stats['seconds_corrected']}s of drift removed")
    print(f"replacements : {stats['replacements']} fetched from Bazarr")
    for run in db.recent_runs(5):
        when = run["finished"] or run["started"]
        print(f"  run {run['kind']:<10} {run['status']:<9} {json.dumps(run['totals'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="What the database knows")
    sub.add_parser("scan", help="Index the library, without running alass")
    for name, help_text in [("check", "Check every subtitle, change nothing"),
                            ("fix", "Check and correct what has drifted"),
                            ("heal", "Replace subtitles that cannot be aligned"),
                            ("full", "scan, then fix, then heal")]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("paths", nargs="*", help="Limit to these subtitles (default: everything)")
        p.add_argument("--root", type=Path, help="Only walk this folder")
        p.add_argument("--workers", type=int, help="Concurrent alass processes for this run")
        p.add_argument("--json", action="store_true", help="Print the totals as JSON")

    args = parser.parse_args(argv)
    db.init()

    if args.command == "status":
        print_status()
        return 0

    if getattr(args, "workers", None):
        db.save_settings({"max_concurrency": args.workers})

    paths = [str(Path(p).resolve()) for p in getattr(args, "paths", [])] or None
    root = getattr(args, "root", None)

    factories = {
        "scan": lambda job: jobs.scan_job(job, root),
        "check": lambda job: jobs.check_job(job, apply=False, only=paths, root=root),
        "fix": lambda job: jobs.check_job(job, apply=True, only=paths, root=root),
        "heal": lambda job: jobs.heal_job(job, paths),
        "full": jobs.full_job,
    }
    job = asyncio.run(with_progress(args.command, factories[args.command]))

    if getattr(args, "json", False):
        print(json.dumps(job.totals))
    else:
        print(f"{args.command} finished: " + ", ".join(f"{v} {k}" for k, v in job.totals.items()))
    bad = job.totals.get("reverted", 0) + job.totals.get("failed", 0) + job.totals.get("gave_up", 0)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
