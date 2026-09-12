"""The Bazarr client: resolving files and asking for a different subtitle."""

from __future__ import annotations

import pytest

SERIES = {"data": [{"sonarrSeriesId": 11, "title": "Game of Thrones", "path": "/data/Series/GoT"}]}
EPISODES = {"data": [{"sonarrEpisodeId": 1177, "season": 5, "episode": 1,
                      "path": "/data/Series/GoT/Season 05/ep1.mkv"}]}
MOVIES = {"data": [{"radarrId": 7, "title": "Aliens", "path": "/data/Movies/Aliens/Aliens.mkv"}]}
HISTORY = {"data": [{"subtitles_path": "/data/Series/GoT/Season 05/ep1.nl.srt",
                     "provider": "opensubtitles", "subs_id": "abc123", "language": "nl"}]}


class FakeBazarr:
    """Records calls instead of making them."""

    def __init__(self, env, **routes):
        from app.bazarr import Bazarr

        self.calls = []
        self.routes = {"series": SERIES, "episodes": EPISODES, "movies": MOVIES,
                       "episodes/history": HISTORY, "movies/history": {"data": []}, **routes}
        client = Bazarr("http://bazarr:6767", "key")
        client._request = self._request
        self.client = client

    def _request(self, method, path, params=None, form=None):
        self.calls.append((method, path, form))
        return self.routes.get(path)


def test_locates_an_episode(env):
    fake = FakeBazarr(env)
    target = fake.client.locate("/data/Series/GoT/Season 05/ep1.mkv")
    assert target == {"kind": "episode", "seriesid": 11, "episodeid": 1177,
                      "title": "Game of Thrones 5x1"}


def test_locates_a_movie(env):
    fake = FakeBazarr(env)
    assert fake.client.locate("/data/Movies/Aliens/Aliens.mkv")["kind"] == "movie"


def test_unknown_file_is_reported(env):
    from app.bazarr import BazarrError

    fake = FakeBazarr(env)
    with pytest.raises(BazarrError, match="does not know"):
        fake.client.replace("/data/Movies/Nope/Nope.mkv", "/data/Movies/Nope/Nope.srt", "nl")


def test_replace_blacklists_when_history_is_available(env):
    fake = FakeBazarr(env)
    how = fake.client.replace("/data/Series/GoT/Season 05/ep1.mkv",
                              "/data/Series/GoT/Season 05/ep1.nl.srt", "nl")
    posted = [c for c in fake.calls if c[1] == "episodes/blacklist"]
    assert posted, "a blacklist call is what stops the same subtitle coming back"
    assert posted[0][2]["subs_id"] == "abc123"
    assert posted[0][2]["episodeid"] == 1177
    assert "blacklisted" in how


def test_replace_falls_back_to_delete_and_search(env):
    """Subtitles that shipped with the release have no history to blacklist."""
    fake = FakeBazarr(env, **{"episodes/history": {"data": []}})
    how = fake.client.replace("/data/Series/GoT/Season 05/ep1.mkv",
                              "/data/Series/GoT/Season 05/ep1.nl.srt", "nl")
    methods = [(c[0], c[1]) for c in fake.calls]
    assert ("DELETE", "episodes/subtitles") in methods
    assert ("PATCH", "episodes/subtitles") in methods
    assert how == "deleted and re-searched"


def test_hi_subtitles_never_go_through_the_blacklist(env):
    """Bazarr's blacklist deletes with hi=False hardcoded, so it cannot find them."""
    fake = FakeBazarr(env)
    how = fake.client.replace("/data/Series/GoT/Season 05/ep1.mkv",
                              "/data/Series/GoT/Season 05/ep1.en.hi.srt", "en", hi=True)
    assert not [c for c in fake.calls if c[1] == "episodes/blacklist"]
    deletes = [c for c in fake.calls if c[1] == "episodes/subtitles" and c[0] == "DELETE"]
    assert deletes and deletes[0][2]["hi"] == "True"
    assert how == "deleted and re-searched"


def test_forced_subtitles_carry_the_flag(env):
    fake = FakeBazarr(env)
    fake.client.replace("/data/Movies/Aliens/Aliens.mkv", "/data/Movies/Aliens/Aliens.en.forced.srt",
                        "en", forced=True)
    deletes = [c for c in fake.calls if c[0] == "DELETE"]
    assert deletes[0][2]["forced"] == "True"


def test_a_refused_blacklist_falls_back_to_delete(env):
    from app.bazarr import BazarrError

    fake = FakeBazarr(env)
    original = fake.client._request

    def refuse(method, path, params=None, form=None):
        if path.endswith("blacklist"):
            raise BazarrError("HTTP 500: Subtitles file not found")
        return original(method, path, params, form)

    fake.client._request = refuse
    how = fake.client.replace("/data/Series/GoT/Season 05/ep1.mkv",
                              "/data/Series/GoT/Season 05/ep1.nl.srt", "nl")
    assert how == "deleted and re-searched", "a refusal must not abandon the subtitle"
