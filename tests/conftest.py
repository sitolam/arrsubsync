"""Shared fixtures: a fake library, a stub alass, and an isolated database."""

from __future__ import annotations

import importlib
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# alass reports a real shift the first time and nothing the second, which is how
# it behaves on a subtitle it can actually align.
STUB = """#!/bin/sh
case "$STUB_MODE" in
  fail)  echo "error: could not guess framerate" >&2; exit 1 ;;
  hang)  sleep 30 ;;
  empty) : > "$3"; exit 0 ;;
  drift) echo "shifted block of 9 subtitles with length 0:40:00.000 by -0:04:51.628"
         printf '1\\n00:00:09,000 --> 00:00:10,000\\nmoved\\n' > "$3"; exit 0 ;;
esac
n=$(cat "$STUB_COUNTER" 2>/dev/null || echo 0)
n=$((n + 1)); echo "$n" > "$STUB_COUNTER"
if [ "$n" = "1" ]; then
  echo "shifted block of 2 subtitles with length 0:00:05.000 by -0:00:11.997"
else
  echo "shifted block of 2 subtitles with length 0:00:05.000 by 0:00:00.000"
fi
printf '1\\n00:00:02,500 --> 00:00:03,500\\nhi\\n' > "$3"
"""

SRT = "1\n00:00:01,000 --> 00:00:02,000\nhi\n"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A library at tmp/media, a stub alass, and a fresh database."""
    media = tmp_path / "media"
    (media / "Movies" / "Foo (2019)").mkdir(parents=True)
    (media / "TV" / "Bar" / "Season 01").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    video = media / "Movies" / "Foo (2019)" / "Foo (2019).mkv"
    video.write_text("not really a video")
    subtitle = media / "Movies" / "Foo (2019)" / "Foo (2019).en.srt"
    subtitle.write_text(SRT)
    for extra in ("Foo (2019).nl.srt", "Foo (2019).en.forced.srt", "Foo (2019).srt"):
        (media / "Movies" / "Foo (2019)" / extra).write_text(SRT)
    (media / "Movies" / "Foo (2019)" / "Foo (2019) Part 2.srt").write_text(SRT)
    (media / "TV" / "Bar" / "Season 01" / "Bar - S01E01.mkv").write_text("v")
    (media / "TV" / "Bar" / "Season 01" / "Bar - S01E01.en.srt").write_text(SRT)
    (outside / "evil.srt").write_text("secret")
    (media / "Movies" / "Foo (2019)" / "link.srt").symlink_to(outside / "evil.srt")

    stub = tmp_path / "alass"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("MEDIA_ROOT", str(media))
    monkeypatch.setenv("ARRSUBSYNC_DATA", str(tmp_path / "config"))
    monkeypatch.setenv("ALASS_BIN", str(stub))
    monkeypatch.setenv("ALASS_TIMEOUT", "5")
    monkeypatch.setenv("STUB_COUNTER", str(tmp_path / "calls"))
    monkeypatch.setenv("ARRSUBSYNC_PASSWORD", "testpassword")
    monkeypatch.delenv("STUB_MODE", raising=False)

    from app import config as config_module
    config = importlib.reload(config_module)
    from app import shifts, db as db_module, alass as alass_module, scanner as scanner_module
    importlib.reload(shifts)
    db = importlib.reload(db_module)
    importlib.reload(alass_module)
    scanner = importlib.reload(scanner_module)
    from app import syncer as syncer_module, bazarr as bazarr_module, auth as auth_module
    syncer = importlib.reload(syncer_module)
    importlib.reload(bazarr_module)
    importlib.reload(auth_module)
    from app import jobs as jobs_module, web as web_module, main as main_module
    jobs = importlib.reload(jobs_module)
    importlib.reload(web_module)
    main = importlib.reload(main_module)
    db.init()

    class Env:
        pass

    e = Env()
    e.media, e.video, e.subtitle, e.outside = media, video, subtitle, outside
    e.stub, e.tmp = stub, tmp_path
    e.config, e.db, e.scanner, e.syncer, e.jobs, e.main = config, db, scanner, syncer, jobs, main
    return e


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    with TestClient(env.main.app) as c:
        c.post("/login", data={"username": "admin", "password": "testpassword"})
        yield c
