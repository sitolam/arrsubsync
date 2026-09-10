"""Bulk re-sync an existing subtitle library through the alass-sync service.

Bazarr's post-processing hook only fires for subtitles it downloads from now
on. This walks a directory tree, pairs each video with the subtitles sitting
next to it, and POSTs each pair to `/sync`.

Run it inside the container:

    docker exec alass-sync python -m app.bulk /data --dry-run
    docker exec alass-sync python -m app.bulk /data --apply --backup-dir /data/.alass-backups

Nothing is modified without --apply. Progress is written to stderr, so the
final JSON summary on stdout can be piped somewhere.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".webm", ".mpg", ".mpeg", ".wmv"}
SUBTITLE_SUFFIXES = {".srt", ".ssa", ".ass", ".idx"}
DEFAULT_STATE = "/data/.alass-sync-bulk-state.json"


@dataclass
class Pair:
    video: Path
    subtitle: Path

    @property
    def tags(self) -> list[str]:
        """The dotted tokens between the video stem and the extension.

        `Foo (2019).en.hi.srt` next to `Foo (2019).mkv` -> ["en", "hi"].
        """
        rest = self.subtitle.name[len(self.video.stem):]
        rest = rest[: -len(self.subtitle.suffix)] if self.subtitle.suffix else rest
        return [t for t in rest.split(".") if t]


@dataclass
class Totals:
    considered: int = 0
    skipped_done: int = 0
    skipped_filter: int = 0
    synced: int = 0
    failed: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)


def find_pairs(root: Path) -> Iterator[Pair]:
    """Yield every (video, subtitle) pair found side by side under `root`."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in sorted(dirnames) if not d.startswith(".")]
        directory = Path(dirpath)
        names = sorted(filenames)
        videos = [n for n in names if Path(n).suffix.lower() in VIDEO_SUFFIXES]
        subtitles = [n for n in names if Path(n).suffix.lower() in SUBTITLE_SUFFIXES]
        if not videos or not subtitles:
            continue
        for video_name in videos:
            stem = Path(video_name).stem
            for sub_name in subtitles:
                # Either exactly `<stem>.srt` or `<stem>.<tags>.srt`; the dot
                # keeps `Foo.mkv` from claiming `Foo Part 2.srt`.
                if sub_name.startswith(stem + "."):
                    yield Pair(directory / video_name, directory / sub_name)


def load_state(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, path)
    except OSError as exc:
        print(f"warning: could not write state file {path}: {exc}", file=sys.stderr)


def fingerprint(p: Path) -> str:
    st = p.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def post_sync(endpoint: str, pair: Pair, args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {"video": str(pair.video), "subtitle": str(pair.subtitle)}
    if args.split_penalty is not None:
        payload["split_penalty"] = args.split_penalty
    if args.no_splits:
        payload["no_splits"] = True
    if args.speed_optimization is not None:
        payload["speed_optimization"] = args.speed_optimization
    if args.disable_fps_guessing:
        payload["disable_fps_guessing"] = True
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/sync", body, {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except Exception:
            return {"status": "error", "error": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001 - network/timeout, reported per file
        return {"status": "error", "error": repr(exc)}


def back_up(pair: Pair, root: Path, backup_dir: Path) -> None:
    try:
        relative = pair.subtitle.relative_to(root)
    except ValueError:
        relative = Path(pair.subtitle.name)
    target = backup_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():  # never overwrite a pristine original on a re-run
        shutil.copy2(pair.subtitle, target)


def wanted(pair: Pair, args: argparse.Namespace) -> bool:
    if args.include and not any(fnmatch.fnmatch(str(pair.subtitle), p) for p in args.include):
        return False
    if any(fnmatch.fnmatch(str(pair.subtitle), p) for p in args.exclude):
        return False
    tags = [t.lower() for t in pair.tags]
    if any(t in args.skip_tags for t in tags):
        return False
    if args.languages:
        if not tags:
            return args.include_untagged
        if not any(t in args.languages for t in tags):
            return False
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.bulk",
        description="Re-sync an existing subtitle library through alass-sync.",
    )
    parser.add_argument("root", type=Path, help="Directory to walk, e.g. /data/Movies")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="List what would be synced (default)")
    mode.add_argument("--apply", action="store_true", help="Actually re-sync and replace files")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8765", help="alass-sync base URL")
    parser.add_argument("--backup-dir", type=Path, help="Copy each original subtitle here first")
    parser.add_argument("--state", type=Path, default=Path(DEFAULT_STATE),
                        help=f"Resume file recording finished subtitles (default {DEFAULT_STATE})")
    parser.add_argument("--no-state", action="store_true", help="Do not read or write a state file")
    parser.add_argument("--redo", action="store_true", help="Ignore the state file and sync everything again")
    parser.add_argument("--languages", default="", help="Only these language tags, e.g. en,nl")
    parser.add_argument("--include-untagged", action="store_true",
                        help="With --languages, also take subtitles carrying no language tag")
    parser.add_argument("--skip-tags", default="forced", help="Skip these tags (default: forced)")
    parser.add_argument("--include", action="append", default=[], help="Only paths matching this glob (repeatable)")
    parser.add_argument("--exclude", action="append", default=[], help="Skip paths matching this glob (repeatable)")
    parser.add_argument("--limit", type=int, help="Stop after this many subtitles")
    parser.add_argument("--workers", type=int, default=2, help="Parallel requests (default 2)")
    parser.add_argument("--timeout", type=float, default=600, help="Per-request timeout in seconds")
    parser.add_argument("--split-penalty", type=float, help="Pass through to alass")
    parser.add_argument("--no-splits", action="store_true", help="Pass --no-splits to alass")
    parser.add_argument("--speed-optimization", type=float,
                        help="alass -O: 0 disables the speed optimisation, slower but more accurate")
    parser.add_argument("--disable-fps-guessing", action="store_true",
                        help="alass -g: do not guess/correct a framerate difference")
    args = parser.parse_args(argv)

    args.apply = args.apply and not args.dry_run
    args.languages = {t.strip().lower() for t in args.languages.split(",") if t.strip()}
    args.skip_tags = {t.strip().lower() for t in args.skip_tags.split(",") if t.strip()}
    if args.no_state:
        args.state = None
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root: Path = args.root

    if not root.is_dir():
        print(f"error: {root} is not a directory inside this container", file=sys.stderr)
        return 2

    state = {} if (args.redo or args.no_state) else load_state(args.state)
    totals = Totals()
    started = time.monotonic()

    pairs: list[Pair] = []
    for pair in find_pairs(root):
        totals.considered += 1
        if not wanted(pair, args):
            totals.skipped_filter += 1
            continue
        key = str(pair.subtitle)
        if not args.redo and state.get(key, {}).get("fingerprint") == fingerprint(pair.subtitle):
            totals.skipped_done += 1
            continue
        pairs.append(pair)
        if args.limit and len(pairs) >= args.limit:
            break

    print(
        f"{totals.considered} subtitle(s) found, {totals.skipped_filter} filtered out, "
        f"{totals.skipped_done} already done, {len(pairs)} to process",
        file=sys.stderr,
    )

    if not args.apply:
        for pair in pairs:
            print(f"would sync {pair.subtitle}  (against {pair.video.name})", file=sys.stderr)
        print("\nDry run. Nothing was changed. Re-run with --apply to do it.", file=sys.stderr)
        json.dump({"dry_run": True, "would_sync": len(pairs), **totals.__dict__}, sys.stdout, default=str)
        print()
        return 0

    if args.backup_dir:
        args.backup_dir.mkdir(parents=True, exist_ok=True)

    def work(pair: Pair) -> tuple[Pair, dict[str, Any]]:
        if args.backup_dir:
            try:
                back_up(pair, root, args.backup_dir)
            except OSError as exc:
                return pair, {"status": "error", "error": f"backup failed: {exc}"}
        return pair, post_sync(args.endpoint, pair, args)

    total = len(pairs)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for pair, result in pool.map(work, pairs):
            done += 1
            if result.get("status") == "ok":
                totals.synced += 1
                summary = result.get("alass_summary") or ""
                print(f"[{done}/{total}] ok   {pair.subtitle}  {summary}", file=sys.stderr)
                try:
                    state[str(pair.subtitle)] = {
                        "fingerprint": fingerprint(pair.subtitle),
                        "at": int(time.time()),
                    }
                except OSError:
                    pass
            else:
                totals.failed += 1
                error = result.get("error", "unknown error")
                totals.failures.append({"subtitle": str(pair.subtitle), "error": error})
                print(f"[{done}/{total}] FAIL {pair.subtitle}  {error}", file=sys.stderr)
            if done % 25 == 0:
                save_state(args.state, state)

    save_state(args.state, state)

    elapsed = round(time.monotonic() - started, 1)
    print(
        f"\ndone in {elapsed}s: {totals.synced} synced, {totals.failed} failed, "
        f"{totals.skipped_done} already done, {totals.skipped_filter} filtered",
        file=sys.stderr,
    )
    json.dump({"dry_run": False, "elapsed_seconds": elapsed, **totals.__dict__}, sys.stdout, default=str)
    print()
    return 1 if totals.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
