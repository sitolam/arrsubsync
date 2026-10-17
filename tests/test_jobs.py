"""The background jobs, including the self-healing loop."""

from __future__ import annotations

import asyncio
import time


def run(coro):
    return asyncio.run(coro)


def test_scan_indexes_the_library(env):
    job = env.jobs.Job(kind="scan")
    run(env.jobs.scan_job(job))
    assert job.totals["indexed"] == 4          # forced is filtered out
    assert env.db.counts()["total"] == 4
    assert env.db.get_subtitle(str(env.subtitle))["status"] == "unknown"


def test_scan_forgets_deleted_subtitles(env):
    run(env.jobs.scan_job(env.jobs.Job(kind="scan")))
    (env.media / "TV" / "Bar" / "Season 01" / "Bar - S01E01.en.srt").unlink()
    job = env.jobs.Job(kind="scan")
    run(env.jobs.scan_job(job))
    assert job.totals["removed"] == 1
    assert env.db.counts()["total"] == 3


def test_check_records_status_without_touching_files(env):
    before = env.subtitle.read_text()
    job = env.jobs.Job(kind="check")
    run(env.jobs.check_job(job, apply=False))
    assert env.subtitle.read_text() == before
    assert job.totals["off"] >= 1
    assert env.db.get_subtitle(str(env.subtitle))["status"] == "off"


def test_fix_applies_and_records(env):
    job = env.jobs.Job(kind="fix")
    run(env.jobs.check_job(job, apply=True, only=[str(env.subtitle)]))
    assert job.totals["fixed"] >= 1
    row = env.db.get_subtitle(str(env.subtitle))
    assert row["status"] == "fixed"
    assert row["fixed_at"] is not None


def test_heal_requires_bazarr(env):
    job = env.jobs.Job(kind="heal")
    try:
        run(env.jobs.heal_job(job))
    except RuntimeError as exc:
        assert "Bazarr is not configured" in str(exc)
    else:
        raise AssertionError("healing without Bazarr must be refused")


class FakeClient:
    """Stands in for Bazarr: 'downloads' a fresh subtitle when asked."""

    def __init__(self, subtitle, good_after=1):
        self.subtitle = subtitle
        self.calls = 0
        self.good_after = good_after

    def replace(self, video, subtitle_path, language, hi=False, forced=False):
        self.calls += 1
        # Write a new file so the loop sees a replacement arrive.
        self.subtitle.write_text(f"1\n00:00:0{self.calls},000 --> 00:00:09,000\nnew\n")
        os_time = time.time() + 10
        import os
        os.utime(self.subtitle, (os_time, os_time))
        return "blacklisted and re-searched"


def test_heal_replaces_then_syncs(env, monkeypatch):
    env.db.save_settings({"bazarr_url": "http://bazarr:6767", "bazarr_api_key": "k",
                          "replace_wait_seconds": 6, "max_replace_attempts": 2})
    env.db.upsert_subtitle(str(env.subtitle), str(env.video), status="reverted", language="en")
    fake = FakeClient(env.subtitle)
    monkeypatch.setattr(env.jobs.bazarr.Bazarr, "from_settings", classmethod(lambda cls: fake))

    job = env.jobs.Job(kind="heal")
    run(env.jobs.heal_job(job))

    assert fake.calls == 1, "one replacement should have been enough"
    assert job.totals["healed"] == 1
    assert job.totals["gave_up"] == 0
    assert env.db.get_subtitle(str(env.subtitle))["status"] in ("fixed", "ok")


def test_heal_gives_up_after_the_attempt_budget(env, monkeypatch):
    """A title where every available subtitle is wrong must not loop forever."""
    monkeypatch.setenv("STUB_MODE", "drift")       # nothing will ever converge
    env.db.save_settings({"bazarr_url": "http://bazarr:6767", "bazarr_api_key": "k",
                          "replace_wait_seconds": 6, "max_replace_attempts": 3})
    env.db.upsert_subtitle(str(env.subtitle), str(env.video), status="reverted", language="en")
    fake = FakeClient(env.subtitle)
    monkeypatch.setattr(env.jobs.bazarr.Bazarr, "from_settings", classmethod(lambda cls: fake))

    job = env.jobs.Job(kind="heal")
    run(env.jobs.heal_job(job))

    assert fake.calls == 3, "it should try exactly the configured number of times"
    assert job.totals["gave_up"] == 1
    assert job.totals["healed"] == 0
    assert "no working subtitle found" in env.db.get_subtitle(str(env.subtitle))["note"]


def test_heal_stops_when_cancelled(env, monkeypatch):
    env.db.save_settings({"bazarr_url": "u", "bazarr_api_key": "k"})
    env.db.upsert_subtitle(str(env.subtitle), str(env.video), status="reverted", language="en")
    fake = FakeClient(env.subtitle)
    monkeypatch.setattr(env.jobs.bazarr.Bazarr, "from_settings", classmethod(lambda cls: fake))
    job = env.jobs.Job(kind="heal")
    job.cancelled = True
    run(env.jobs.heal_job(job))
    assert fake.calls == 0


class SilentClient:
    """Bazarr that accepts the request but never produces a new file."""

    def __init__(self):
        self.calls = 0

    def replace(self, video, subtitle_path, language, hi=False, forced=False):
        self.calls += 1
        return "blacklisted and re-searched"


def test_heal_stops_when_bazarr_has_nothing_to_offer(env, monkeypatch):
    """No replacement arriving means no other subtitle exists - do not burn attempts."""
    env.db.save_settings({"bazarr_url": "u", "bazarr_api_key": "k",
                          "replace_wait_seconds": 4, "max_replace_attempts": 3})
    env.db.upsert_subtitle(str(env.subtitle), str(env.video), status="reverted", language="en")
    fake = SilentClient()
    monkeypatch.setattr(env.jobs.bazarr.Bazarr, "from_settings", classmethod(lambda cls: fake))

    job = env.jobs.Job(kind="heal")
    run(env.jobs.heal_job(job))

    assert fake.calls == 1, "it should not keep asking when nothing comes back"
    assert env.db.get_subtitle(str(env.subtitle))["note"] == "Bazarr has no other subtitle"


def test_heal_passes_hi_and_forced_flags(env, monkeypatch):
    seen = {}

    class Recorder(SilentClient):
        def replace(self, video, subtitle_path, language, hi=False, forced=False):
            seen.update(language=language, hi=hi, forced=forced)
            return super().replace(video, subtitle_path, language, hi, forced)

    hi_sub = env.media / "Movies" / "Foo (2019)" / "Foo (2019).nl.hi.srt"
    hi_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    env.db.save_settings({"bazarr_url": "u", "bazarr_api_key": "k", "replace_wait_seconds": 3})
    env.db.upsert_subtitle(str(hi_sub), str(env.video), status="reverted")
    monkeypatch.setattr(env.jobs.bazarr.Bazarr, "from_settings", classmethod(lambda cls: Recorder()))

    run(env.jobs.heal_job(env.jobs.Job(kind="heal")))
    assert seen == {"language": "nl", "hi": True, "forced": False}


class DeletingClient(SilentClient):
    """What Bazarr really does: delete the subtitle, then search - and find nothing."""

    def replace(self, video, subtitle_path, language, hi=False, forced=False):
        import os
        os.unlink(subtitle_path)
        return super().replace(video, subtitle_path, language, hi, forced)


def heal_settings(env, **extra):
    env.db.save_settings({"bazarr_url": "u", "bazarr_api_key": "k",
                          "replace_wait_seconds": 4, "max_replace_attempts": 3, **extra})


def test_heal_puts_the_original_back_when_nothing_replaces_it(env, monkeypatch):
    """Never leave a video with no subtitle because a better one was hoped for."""
    heal_settings(env)
    original = "1\n00:00:01,000 --> 00:00:02,000\nthe only one we have\n"
    env.subtitle.write_text(original)
    env.db.upsert_subtitle(str(env.subtitle), str(env.video), status="reverted", language="en")
    fake = DeletingClient()
    monkeypatch.setattr(env.jobs.bazarr.Bazarr, "from_settings", classmethod(lambda cls: fake))

    job = env.jobs.Job(kind="heal")
    run(env.jobs.heal_job(job))

    assert fake.calls == 1
    assert env.subtitle.read_text() == original
    assert job.totals["restored"] == 1
    kept = env.config.DATA_DIR / "replaced" / "Movies" / "Foo (2019)" / "Foo (2019).en.srt"
    assert kept.read_text() == original, "a copy of every subtitle handed to Bazarr is kept"


def test_heal_recognises_a_replacement_with_a_different_name(env, monkeypatch):
    """Bazarr names files by what it knows, so X.hi.srt may come back as X.nl.srt."""
    heal_settings(env)
    folder = env.media / "TV" / "Bar" / "Season 01"
    video, old, new = folder / "Bar - S01E01.mkv", folder / "Bar - S01E01.hi.srt", folder / "Bar - S01E01.nl.srt"
    old.write_text("1\n00:00:01,000 --> 00:00:02,000\nold\n")
    env.db.upsert_subtitle(str(old), str(video), status="reverted")

    class Renaming(SilentClient):
        def replace(self, video, subtitle_path, language, hi=False, forced=False):
            import os
            os.unlink(subtitle_path)
            new.write_text("1\n00:00:05,000 --> 00:00:06,000\nnew\n")
            return super().replace(video, subtitle_path, language, hi, forced)

    fake = Renaming()
    monkeypatch.setattr(env.jobs.bazarr.Bazarr, "from_settings", classmethod(lambda cls: fake))
    job = env.jobs.Job(kind="heal")
    run(env.jobs.heal_job(job))

    assert job.totals["healed"] == 1
    assert not old.exists(), "the original must not be put back beside its replacement"
    assert env.db.get_subtitle(str(old)) is None
    assert env.db.get_subtitle(str(new))["status"] in ("ok", "fixed")


def test_automatic_heal_leaves_alass_failures_alone(env, monkeypatch):
    """alass crashing or timing out says nothing about the subtitle; never delete over it."""
    heal_settings(env)
    env.db.upsert_subtitle(str(env.subtitle), str(env.video), status="failed")
    fake = DeletingClient()
    monkeypatch.setattr(env.jobs.bazarr.Bazarr, "from_settings", classmethod(lambda cls: fake))
    run(env.jobs.heal_job(env.jobs.Job(kind="heal")))
    assert fake.calls == 0
    assert env.subtitle.exists()


def test_automatic_heal_does_not_retry_what_it_gave_up_on(env, monkeypatch):
    """Otherwise every sweep blacklists whatever Bazarr fetched since, forever."""
    heal_settings(env)
    env.db.upsert_subtitle(str(env.subtitle), str(env.video), status="reverted")
    fake = DeletingClient()
    monkeypatch.setattr(env.jobs.bazarr.Bazarr, "from_settings", classmethod(lambda cls: fake))
    run(env.jobs.heal_job(env.jobs.Job(kind="heal")))
    run(env.jobs.heal_job(env.jobs.Job(kind="heal")))
    assert fake.calls == 1
    # asking explicitly, from the dashboard, still works
    run(env.jobs.heal_job(env.jobs.Job(kind="heal"), [str(env.subtitle)]))
    assert fake.calls == 2


def test_the_sweep_skips_subtitles_already_verified(env):
    """Re-aligning 400 good subtitles every night finds nothing and rewrites them all."""
    run(env.jobs.scan_job(env.jobs.Job(kind="scan")))
    for row in env.db.list_subtitles(limit=100):
        env.db.upsert_subtitle(row["path"], row["video"], status="ok")
    run(env.jobs.full_job(env.jobs.Job(kind="full")))
    assert not (env.tmp / "calls").exists(), "alass should not have run at all"
