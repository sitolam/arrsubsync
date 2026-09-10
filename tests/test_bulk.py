"""Tests for the bulk re-sync CLI (app/bulk.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import bulk  # noqa: E402


@pytest.fixture()
def library(tmp_path: Path) -> Path:
    movies = tmp_path / "Movies" / "Foo (2019)"
    movies.mkdir(parents=True)
    (movies / "Foo (2019).mkv").write_text("v")
    (movies / "Foo (2019).en.srt").write_text("s")
    (movies / "Foo (2019).nl.srt").write_text("s")
    (movies / "Foo (2019).en.forced.srt").write_text("s")
    (movies / "Foo (2019).srt").write_text("s")
    # Must NOT be paired with the film above: no dot right after the stem.
    (movies / "Foo (2019) Part 2.srt").write_text("s")
    # Not a subtitle, and a non-video file with a matching stem.
    (movies / "Foo (2019).nfo").write_text("x")

    show = tmp_path / "TV" / "Bar" / "Season 01"
    show.mkdir(parents=True)
    (show / "Bar - S01E01.mkv").write_text("v")
    (show / "Bar - S01E01.en.srt").write_text("s")
    (show / "Bar - S01E02.mkv").write_text("v")  # no subtitle
    return tmp_path


def names(pairs) -> set[str]:
    return {p.subtitle.name for p in pairs}


def test_pairs_subtitles_with_their_video(library: Path):
    found = list(bulk.find_pairs(library))
    assert names(found) == {
        "Foo (2019).en.srt",
        "Foo (2019).nl.srt",
        "Foo (2019).en.forced.srt",
        "Foo (2019).srt",
        "Bar - S01E01.en.srt",
    }


def test_unrelated_names_are_not_paired(library: Path):
    assert "Foo (2019) Part 2.srt" not in names(bulk.find_pairs(library))


def test_hidden_directories_skipped(library: Path):
    hidden = library / ".alass-backups" / "Foo (2019)"
    hidden.mkdir(parents=True)
    (hidden / "Foo (2019).mkv").write_text("v")
    (hidden / "Foo (2019).en.srt").write_text("s")
    assert not any(".alass-backups" in str(p.subtitle) for p in bulk.find_pairs(library))


def test_tags_extraction(library: Path):
    by_name = {p.subtitle.name: p for p in bulk.find_pairs(library)}
    assert by_name["Foo (2019).en.srt"].tags == ["en"]
    assert by_name["Foo (2019).en.forced.srt"].tags == ["en", "forced"]
    assert by_name["Foo (2019).srt"].tags == []


def test_forced_skipped_by_default(library: Path):
    args = bulk.parse_args([str(library)])
    kept = [p for p in bulk.find_pairs(library) if bulk.wanted(p, args)]
    assert "Foo (2019).en.forced.srt" not in names(kept)


def test_language_filter(library: Path):
    args = bulk.parse_args([str(library), "--languages", "en"])
    kept = names(p for p in bulk.find_pairs(library) if bulk.wanted(p, args))
    assert kept == {"Foo (2019).en.srt", "Bar - S01E01.en.srt"}
    args = bulk.parse_args([str(library), "--languages", "en", "--include-untagged"])
    kept = names(p for p in bulk.find_pairs(library) if bulk.wanted(p, args))
    assert "Foo (2019).srt" in kept


def test_include_exclude_globs(library: Path):
    args = bulk.parse_args([str(library), "--exclude", "*/TV/*"])
    kept = names(p for p in bulk.find_pairs(library) if bulk.wanted(p, args))
    assert not any("Bar" in n for n in kept)


def test_dry_run_changes_nothing(library: Path, capsys, monkeypatch):
    called = []
    monkeypatch.setattr(bulk, "post_sync", lambda *a, **k: called.append(a) or {"status": "ok"})
    rc = bulk.main([str(library), "--no-state"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert called == []
    assert out["dry_run"] is True
    assert out["would_sync"] == 4  # forced is filtered out


def test_apply_posts_each_pair_and_backs_up(library: Path, tmp_path: Path, capsys, monkeypatch):
    seen = []

    def fake_post(endpoint, pair, args):
        seen.append(pair.subtitle.name)
        return {"status": "ok", "alass_summary": "shifted block"}

    monkeypatch.setattr(bulk, "post_sync", fake_post)
    backups = tmp_path / "backups"
    rc = bulk.main([str(library), "--apply", "--no-state", "--backup-dir", str(backups), "--workers", "1"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["synced"] == 4
    assert len(seen) == 4
    assert (backups / "Movies" / "Foo (2019)" / "Foo (2019).en.srt").exists()


def test_failures_are_reported_and_exit_nonzero(library: Path, capsys, monkeypatch):
    monkeypatch.setattr(
        bulk, "post_sync", lambda *a, **k: {"status": "error", "error": "alass exited with code 1"}
    )
    rc = bulk.main([str(library), "--apply", "--no-state", "--workers", "1"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["failed"] == 4
    assert out["failures"][0]["error"] == "alass exited with code 1"


def test_state_file_makes_reruns_skip(library: Path, tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setattr(bulk, "post_sync", lambda *a, **k: {"status": "ok"})
    state = tmp_path / "state.json"
    bulk.main([str(library), "--apply", "--state", str(state), "--workers", "1"])
    capsys.readouterr()
    bulk.main([str(library), "--apply", "--state", str(state), "--workers", "1"])
    out = json.loads(capsys.readouterr().out)
    assert out["synced"] == 0
    assert out["skipped_done"] == 4


def test_changed_subtitle_is_synced_again(library: Path, tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setattr(bulk, "post_sync", lambda *a, **k: {"status": "ok"})
    state = tmp_path / "state.json"
    bulk.main([str(library), "--apply", "--state", str(state), "--workers", "1"])
    capsys.readouterr()
    target = library / "Movies" / "Foo (2019)" / "Foo (2019).en.srt"
    target.write_text("a new subtitle was downloaded")
    bulk.main([str(library), "--apply", "--state", str(state), "--workers", "1"])
    out = json.loads(capsys.readouterr().out)
    assert out["synced"] == 1


def test_limit(library: Path, capsys, monkeypatch):
    monkeypatch.setattr(bulk, "post_sync", lambda *a, **k: {"status": "ok"})
    bulk.main([str(library), "--apply", "--no-state", "--limit", "2", "--workers", "1"])
    assert json.loads(capsys.readouterr().out)["synced"] == 2


def test_parse_shifts_handles_signs_and_multiple_blocks():
    assert bulk.parse_shifts("shifted block of 428 subtitles with length 0:50:44.994 by 0:00:20.335") == [20.335]
    assert bulk.parse_shifts("3 blocks shifted by -0:00:19.474, -0:00:37.084, -0:01:26.168") == [
        -19.474, -37.084, -86.168
    ]
    assert bulk.parse_shifts(None) == []


def test_verdict_classification():
    ok = {"status": "ok", "alass_summary": "shifted block of 4 subtitles with length 0:10:00.000 by 0:00:00.000"}
    off = {"status": "ok", "alass_summary": "shifted block of 4 subtitles with length 0:10:00.000 by 0:00:20.335"}
    suspect = {"status": "ok", "alass_summary": "3 blocks shifted by -0:00:19.474, -0:00:37.084, -0:04:26.168"}
    failed = {"status": "error", "error": "alass exited with code 1"}
    assert bulk.verdict(ok, 0.5)[0] == "ok"
    assert bulk.verdict(off, 0.5)[0] == "off"
    assert bulk.verdict(suspect, 0.5)[0] == "suspect"
    assert bulk.verdict(failed, 0.5)[0] == "failed"


def test_verify_reports_without_changing_anything(library: Path, capsys, monkeypatch):
    written = []
    monkeypatch.setattr(bulk, "post_sync", lambda e, p, a, dry_run=False: (
        written.append(dry_run),
        {"status": "ok", "alass_summary": "shifted block of 9 subtitles with length 0:10:00.000 by 0:00:20.000"},
    )[1])
    rc = bulk.main([str(library), "--verify", "--no-state", "--workers", "1"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1                      # something is out of sync
    assert out["verify"] is True
    assert out["off"] == 4 and out["ok"] == 0
    assert all(written), "verify must only ever send dry-run requests"


def test_verify_clean_library_exits_zero(library: Path, capsys, monkeypatch):
    monkeypatch.setattr(bulk, "post_sync", lambda e, p, a, dry_run=False: {
        "status": "ok", "alass_summary": "shifted block of 9 subtitles with length 0:10:00.000 by 0:00:00.010"})
    rc = bulk.main([str(library), "--verify", "--no-state", "--workers", "1"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"] == 4


def test_backup_mirrors_media_root_not_walk_root(library: Path, tmp_path: Path, monkeypatch, capsys):
    """A run scoped to one folder must back up into the same tree as a full run."""
    monkeypatch.setattr(bulk, "MEDIA_ROOT", library)
    monkeypatch.setattr(bulk, "post_sync", lambda *a, **k: {"status": "ok"})
    backups = tmp_path / "b"
    season = library / "TV" / "Bar" / "Season 01"
    bulk.main([str(season), "--apply", "--no-state", "--backup-dir", str(backups), "--workers", "1"])
    assert (backups / "TV" / "Bar" / "Season 01" / "Bar - S01E01.en.srt").exists()
