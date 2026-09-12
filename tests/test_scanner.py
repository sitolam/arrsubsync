"""Pairing videos with the subtitles beside them."""

from __future__ import annotations


def names(pairs):
    return {p.subtitle.name for p in pairs}


def test_pairs_every_subtitle_with_its_video(env):
    found = list(env.scanner.find_pairs(env.media))
    assert names(found) == {
        "Foo (2019).en.srt", "Foo (2019).nl.srt", "Foo (2019).en.forced.srt",
        "Foo (2019).srt", "Bar - S01E01.en.srt",
    }


def test_unrelated_names_are_not_paired(env):
    assert "Foo (2019) Part 2.srt" not in names(env.scanner.find_pairs(env.media))


def test_hidden_directories_are_skipped(env):
    hidden = env.media / ".arrsubsync-backups" / "Movies"
    hidden.mkdir(parents=True)
    (hidden / "Foo (2019).mkv").write_text("v")
    (hidden / "Foo (2019).en.srt").write_text("s")
    assert not any(".arrsubsync" in str(p.subtitle) for p in env.scanner.find_pairs(env.media))


def test_tags_and_language(env):
    by_name = {p.subtitle.name: p for p in env.scanner.find_pairs(env.media)}
    assert by_name["Foo (2019).en.hi.srt" if False else "Foo (2019).en.forced.srt"].tags == ["en", "forced"]
    assert by_name["Foo (2019).en.srt"].language == "en"
    assert by_name["Foo (2019).srt"].tags == []


def test_forced_is_skipped_by_default(env):
    settings = env.db.get_settings()
    kept = names(p for p in env.scanner.find_pairs(env.media) if env.scanner.wanted(p, settings))
    assert "Foo (2019).en.forced.srt" not in kept


def test_language_filter(env):
    settings = dict(env.db.get_settings(), languages="nl")
    kept = names(p for p in env.scanner.find_pairs(env.media) if env.scanner.wanted(p, settings))
    assert kept == {"Foo (2019).nl.srt"}


def test_video_for_finds_the_companion(env):
    assert env.scanner.video_for(env.subtitle) == env.video
    assert env.scanner.video_for(env.media / "Movies" / "Foo (2019)" / "nothing.srt") is None
