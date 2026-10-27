"""Spotting subtitles that are timed right but are missing part of the dialogue."""

from __future__ import annotations

import asyncio


def srt(*spans):
    out = []
    for i, (a, b) in enumerate(spans, 1):
        def ts(s):
            return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}".replace(".", ",")
        out.append(f"{i}\n{ts(a)} --> {ts(b)}\nline {i}\n")
    return "\n".join(out)


def every(start, end, step=20):
    return [(t, t + 3) for t in range(start, end, step)]


def test_a_full_subtitle_is_complete(env, tmp_path):
    from app import coverage

    sub = tmp_path / "full.srt"
    sub.write_text(srt(*every(60, 3400)))
    report = coverage.measure(sub, duration=3600)
    assert not report.incomplete


def test_a_missing_opening_is_found(env, tmp_path):
    """Game of Thrones S05E08: perfectly timed, but nothing for the first 10m32s."""
    from app import coverage

    sub = tmp_path / "late.srt"
    sub.write_text(srt(*every(632, 3400)))
    report = coverage.measure(sub, duration=3603)
    assert report.incomplete
    assert "first 10m32s" in report.describe()
    assert report.missing > 300


def test_a_hole_in_the_middle_is_found(env, tmp_path):
    from app import coverage

    sub = tmp_path / "hole.srt"
    sub.write_text(srt(*every(60, 1200), *every(2000, 3400)))
    report = coverage.measure(sub, duration=3600)
    assert report.incomplete
    assert "13m" in report.describe()


def test_long_end_credits_are_not_a_gap(env, tmp_path):
    """Films can roll ten minutes of credits with nothing to say."""
    from app import coverage

    sub = tmp_path / "film.srt"
    sub.write_text(srt(*every(90, 6600)))
    assert not coverage.measure(sub, duration=7200).incomplete


def test_a_missing_ending_is_found(env, tmp_path):
    from app import coverage

    sub = tmp_path / "short.srt"
    sub.write_text(srt(*every(60, 1800)))
    assert coverage.measure(sub, duration=3600).incomplete


def test_ass_dialogue_lines_are_read(env, tmp_path):
    from app import coverage

    sub = tmp_path / "late.ass"
    sub.write_text("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                   + "".join(f"Dialogue: 0,0:{m:02d}:00.50,0:{m:02d}:03.00,Default,,0,0,0,,hi\n"
                             for m in range(15, 59)))
    assert coverage.measure(sub, duration=3600).incomplete


def test_no_cues_or_no_duration_is_not_judged(env, tmp_path):
    from app import coverage

    sub = tmp_path / "odd.srt"
    sub.write_text("not a subtitle")
    assert not coverage.measure(sub, duration=3600).incomplete
    sub.write_text(srt(*every(60, 1800)))
    assert not coverage.measure(sub, duration=None).incomplete, "the tail needs the duration"


def test_sync_reports_an_incomplete_subtitle(env, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "zero")
    monkeypatch.setattr(env.syncer.coverage, "duration", lambda video: 3603.0)
    env.subtitle.write_text(srt(*every(632, 3400)))
    result = asyncio.run(env.syncer.sync(str(env.video), str(env.subtitle)))
    assert result.status == "incomplete"
    assert "first 10m32s" in result.error
    row = env.db.get_subtitle(str(env.subtitle))
    assert row["status"] == "incomplete"
    assert "first 10m32s" in row["note"]


def test_forced_subtitles_are_sparse_by_design(env, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "zero")
    monkeypatch.setattr(env.syncer.coverage, "duration", lambda video: 3603.0)
    forced = env.media / "Movies" / "Foo (2019)" / "Foo (2019).en.forced.srt"
    forced.write_text(srt((2504, 2506)))
    result = asyncio.run(env.syncer.sync(str(env.video), str(forced)))
    assert result.status == "ok"


def test_the_scan_flags_incomplete_subtitles_already_verified(env, monkeypatch):
    """The library was checked before this existed; a scan must catch up without alass."""
    monkeypatch.setattr(env.jobs.coverage, "duration",
                        lambda video: 3603.0 if video == env.video else None)
    for sibling in env.video.parent.glob("Foo (2019).*.srt"):
        sibling.write_text(srt(*every(60, 3400)))
    (env.video.parent / "Foo (2019).srt").write_text(srt(*every(60, 3400)))
    env.subtitle.write_text(srt(*every(632, 3400)))
    asyncio.run(env.jobs.scan_job(env.jobs.Job(kind="scan")))
    for row in env.db.list_subtitles(limit=100):
        env.db.upsert_subtitle(row["path"], row["video"], status="ok")
    asyncio.run(env.jobs.scan_job(env.jobs.Job(kind="scan")))
    assert env.db.get_subtitle(str(env.subtitle))["status"] == "incomplete"
    assert env.db.counts()["incomplete"] == 1
