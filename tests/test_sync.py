"""Tests for the alass-sync service, using a stub alass binary.

    pip install -r requirements.txt -r tests/requirements-dev.txt
    pytest
"""

from __future__ import annotations

import importlib
import os
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

STUB = """#!/bin/sh
case "$STUB_MODE" in
  fail) echo "error: could not guess framerate" >&2; exit 1 ;;
  hang) sleep 30 ;;
  empty) : > "$3"; exit 0 ;;
esac
echo "shifted block of 2 subtitles with length 0:00:05.000 by -0:00:11.997"
printf '1\\n00:00:02,500 --> 00:00:03,500\\nhi\\n' > "$3"
"""

SRT = "1\n00:00:01,000 --> 00:00:02,000\nhi\n"


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    media = tmp_path / "media"
    (media / "Movies" / "Foo").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    video = media / "Movies" / "Foo" / "Foo.mkv"
    video.write_text("not really a video")
    subtitle = media / "Movies" / "Foo" / "Foo.en.srt"
    subtitle.write_text(SRT)
    (outside / "evil.srt").write_text("secret")
    (media / "Movies" / "Foo" / "link.srt").symlink_to(outside / "evil.srt")

    stub = tmp_path / "alass"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("MEDIA_ROOT", str(media))
    monkeypatch.setenv("ALASS_BIN", str(stub))
    monkeypatch.setenv("ALASS_TIMEOUT", "2")
    monkeypatch.delenv("STUB_MODE", raising=False)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main = importlib.reload(importlib.import_module("app.main"))
    return main, TestClient(main.app), video, subtitle, outside


def test_health_reports_media_root(env):
    _, client, _, _, _ = env
    body = client.get("/health").json()
    assert body["media_root_mounted"] is True
    assert body["alass"] is not None


def test_relative_path_rejected(env):
    _, client, _, subtitle, _ = env
    r = client.post("/sync", json={"video": "Foo.mkv", "subtitle": str(subtitle)})
    assert r.status_code == 400
    assert "absolute" in r.json()["error"]


def test_missing_file_rejected(env):
    _, client, video, subtitle, _ = env
    r = client.post("/sync", json={"video": str(video), "subtitle": str(subtitle) + ".nope"})
    assert r.status_code == 400
    assert "does not exist" in r.json()["error"]


def test_traversal_rejected(env):
    _, client, video, _, outside = env
    r = client.post("/sync", json={"video": str(video), "subtitle": str(outside / "evil.srt")})
    assert r.status_code == 400
    assert "outside the media root" in r.json()["error"]


def test_symlink_escape_rejected(env):
    _, client, video, subtitle, _ = env
    r = client.post("/sync", json={"video": str(video), "subtitle": str(subtitle.with_name("link.srt"))})
    assert r.status_code == 400
    assert "outside the media root" in r.json()["error"]


def test_unsupported_extension_rejected(env):
    _, client, video, _, _ = env
    r = client.post("/sync", json={"video": str(video), "subtitle": str(video)})
    assert r.status_code == 400
    assert "unsupported subtitle format" in r.json()["error"]


def test_successful_sync_replaces_file(env):
    _, client, video, subtitle, _ = env
    r = client.post("/sync", json={"video": str(video), "subtitle": str(subtitle)})
    assert r.status_code == 200
    body = r.json()
    assert body["replaced"] is True
    assert "shifted block" in body["alass_summary"]
    assert "00:00:02,500" in subtitle.read_text()
    assert not list(subtitle.parent.glob(".alass-sync-*"))


def test_temp_file_keeps_subtitle_extension(env):
    """alass refuses to write an output whose extension implies another format."""
    main, client, video, subtitle, _ = env
    seen: list[list[str]] = []
    original = main.run_alass

    async def spy(argv):
        seen.append(argv)
        return await original(argv)

    main.run_alass = spy
    client.post("/sync", json={"video": str(video), "subtitle": str(subtitle)})
    assert seen and seen[0][3].endswith(".srt")


def test_dry_run_leaves_original(env):
    _, client, video, subtitle, _ = env
    r = client.post("/sync", json={"video": str(video), "subtitle": str(subtitle), "dry_run": True})
    assert r.status_code == 200
    assert r.json()["replaced"] is False
    assert subtitle.read_text() == SRT


def test_alass_failure_reports_stderr(env):
    _, client, video, subtitle, _ = env
    os.environ["STUB_MODE"] = "fail"
    r = client.post("/sync", json={"video": str(video), "subtitle": str(subtitle)})
    assert r.status_code == 502
    assert "could not guess framerate" in r.json()["stderr"]
    assert subtitle.read_text() == SRT
    assert not list(subtitle.parent.glob(".alass-sync-*"))


def test_empty_output_is_not_installed(env):
    _, client, video, subtitle, _ = env
    os.environ["STUB_MODE"] = "empty"
    r = client.post("/sync", json={"video": str(video), "subtitle": str(subtitle)})
    assert r.status_code == 502
    assert subtitle.read_text() == SRT


def test_timeout_kills_alass(env):
    _, client, video, subtitle, _ = env
    os.environ["STUB_MODE"] = "hang"
    r = client.post("/sync", json={"video": str(video), "subtitle": str(subtitle)})
    assert r.status_code == 504
    assert "timed out" in r.json()["error"]
    assert subtitle.read_text() == SRT
    assert not list(subtitle.parent.glob(".alass-sync-*"))


def test_progress_bars_stripped(env):
    main, _, _, _, _ = env
    noisy = "working...\n1 / 2 [====>----] 50.00 % 89015.49/s 0s \r2 / 2 [=====] 100.00 % \ndone"
    cleaned = main.clean_alass_output(noisy)
    assert "%" not in cleaned
    assert cleaned.splitlines() == ["working...", "done"]


def test_summary_reports_every_block(env):
    main, _, _, _, _ = env
    text = (
        "shifted block of 82 subtitles with length 0:10:16.932 by -0:00:19.474\n"
        "shifted block of 281 subtitles with length 0:25:45.697 by -0:00:37.084\n"
        "shifted block of 65 subtitles with length 0:10:49.901 by -0:01:26.168"
    )
    summary = main.alass_summary(text)
    assert summary == "3 blocks shifted by -0:00:19.474, -0:00:37.084, -0:01:26.168"


def test_summary_passes_a_single_block_through(env):
    main, _, _, _, _ = env
    line = "shifted block of 402 subtitles with length 0:41:07.000 by 0:00:00.000"
    assert main.alass_summary(line) == line


def test_speed_optimization_and_fps_flags_reach_alass(env):
    main, client, video, subtitle, _ = env
    seen: list[list[str]] = []
    original = main.run_alass

    async def spy(argv):
        seen.append(argv)
        return await original(argv)

    main.run_alass = spy
    client.post("/sync", json={
        "video": str(video), "subtitle": str(subtitle),
        "speed_optimization": 0, "disable_fps_guessing": True, "dry_run": True,
    })
    assert "--speed-optimization" in seen[0]
    assert seen[0][seen[0].index("--speed-optimization") + 1] == "0.0"
    assert "--disable-fps-guessing" in seen[0]


def test_no_splits_uses_the_flag_alass_actually_has(env):
    """alass calls it --no-split (singular); --no-splits makes it exit 1."""
    main, client, video, subtitle, _ = env
    seen: list[list[str]] = []
    original = main.run_alass

    async def spy(argv):
        seen.append(argv)
        return await original(argv)

    main.run_alass = spy
    client.post("/sync", json={"video": str(video), "subtitle": str(subtitle),
                               "no_splits": True, "dry_run": True})
    assert "--no-split" in seen[0]
    assert "--no-splits" not in seen[0]
