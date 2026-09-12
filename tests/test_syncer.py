"""The core: aligning a subtitle, and refusing to keep a result that does not hold."""

from __future__ import annotations

import asyncio
import os

import pytest


def run(coro):
    return asyncio.run(coro)


def test_rejects_relative_paths(env):
    with pytest.raises(env.syncer.Rejected, match="absolute"):
        run(env.syncer.sync("Foo.mkv", str(env.subtitle)))


def test_rejects_paths_outside_the_media_root(env):
    with pytest.raises(env.syncer.Rejected, match="outside the media root"):
        run(env.syncer.sync(str(env.video), str(env.outside / "evil.srt")))


def test_rejects_symlinks_that_escape_the_media_root(env):
    escape = env.media / "Movies" / "Foo (2019)" / "link.srt"
    with pytest.raises(env.syncer.Rejected, match="outside the media root"):
        run(env.syncer.sync(str(env.video), str(escape)))


def test_rejects_a_format_alass_cannot_read(env):
    with pytest.raises(env.syncer.Rejected, match="unsupported subtitle format"):
        run(env.syncer.sync(str(env.video), str(env.video)))


def test_successful_sync_replaces_the_file(env):
    result = run(env.syncer.sync(str(env.video), str(env.subtitle)))
    assert result.status == "ok"
    assert result.replaced is True
    assert "00:00:02,500" in env.subtitle.read_text()
    assert not list(env.subtitle.parent.glob(".arrsubsync-*")), "scratch files left behind"


def test_dry_run_changes_nothing(env):
    before = env.subtitle.read_text()
    result = run(env.syncer.sync(str(env.video), str(env.subtitle), dry_run=True))
    assert result.status == "ok"
    assert env.subtitle.read_text() == before


def test_non_converging_sync_is_reverted(env, monkeypatch):
    """A subtitle that does not belong to the audio must come back untouched."""
    monkeypatch.setenv("STUB_MODE", "drift")
    before = env.subtitle.read_text()
    result = run(env.syncer.sync(str(env.video), str(env.subtitle)))
    assert result.status == "reverted"
    assert "does not match this audio" in result.error
    assert env.subtitle.read_text() == before, "the original must be restored byte for byte"
    assert not list(env.subtitle.parent.glob(".arrsubsync-*"))


def test_verification_can_be_turned_off(env, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "drift")
    result = run(env.syncer.sync(str(env.video), str(env.subtitle),
                                 overrides={"verify_after": False}))
    assert result.status == "ok"
    assert "moved" in env.subtitle.read_text()


def test_alass_failure_is_reported_and_the_file_is_untouched(env, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "fail")
    before = env.subtitle.read_text()
    result = run(env.syncer.sync(str(env.video), str(env.subtitle)))
    assert result.status == "failed"
    assert "could not guess framerate" in result.details["stderr"]
    assert env.subtitle.read_text() == before


def test_empty_output_is_never_installed(env, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "empty")
    before = env.subtitle.read_text()
    assert run(env.syncer.sync(str(env.video), str(env.subtitle))).status == "failed"
    assert env.subtitle.read_text() == before


def test_timeout_kills_alass(env, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "hang")
    env.db.save_settings({"alass_timeout": 1})
    result = run(env.syncer.sync(str(env.video), str(env.subtitle)))
    assert result.status == "failed" and "timed out" in result.error


def test_backup_keeps_the_pristine_copy(env):
    original = env.subtitle.read_text()
    run(env.syncer.sync(str(env.video), str(env.subtitle)))
    run(env.syncer.sync(str(env.video), str(env.subtitle)))
    backup = env.media / ".arrsubsync-backups" / "Movies" / "Foo (2019)" / "Foo (2019).en.srt"
    assert backup.read_text() == original, "a re-run must not overwrite the pristine backup"


def test_temp_file_keeps_the_subtitle_extension(env, monkeypatch):
    """alass refuses an output whose extension implies a different format."""
    seen = []
    original = env.alass_run = env.syncer.alass.run

    async def spy(argv, timeout):
        seen.append(argv)
        return await original(argv, timeout)

    monkeypatch.setattr(env.syncer.alass, "run", spy)
    run(env.syncer.sync(str(env.video), str(env.subtitle)))
    assert seen[0][3].endswith(".srt")


def test_no_split_uses_the_flag_alass_actually_has(env, monkeypatch):
    """alass spells it --no-split; --no-splits makes it exit 1."""
    seen = []
    original = env.syncer.alass.run

    async def spy(argv, timeout):
        seen.append(argv)
        return await original(argv, timeout)

    monkeypatch.setattr(env.syncer.alass, "run", spy)
    run(env.syncer.sync(str(env.video), str(env.subtitle),
                        overrides={"alass_no_split": True}, dry_run=True))
    assert "--no-split" in seen[0] and "--no-splits" not in seen[0]


def test_ownership_is_preserved(env):
    st_before = os.stat(env.subtitle)
    run(env.syncer.sync(str(env.video), str(env.subtitle)))
    st_after = os.stat(env.subtitle)
    assert (st_after.st_uid, st_after.st_gid) == (st_before.st_uid, st_before.st_gid)
