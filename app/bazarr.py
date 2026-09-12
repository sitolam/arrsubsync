"""A small Bazarr API client: enough to replace a subtitle that cannot be fixed.

Bazarr's blacklist endpoint does three useful things in one call: it records the
subtitle so the provider will not serve it again, deletes the file, and starts a
fresh search. That is exactly the "give me a different one" operation.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app import db
from app.alass import logx


class BazarrError(Exception):
    pass


class Bazarr:
    def __init__(self, url: str, api_key: str, timeout: float = 30.0) -> None:
        self.url = (url or "").rstrip("/")
        self.api_key = api_key or ""
        self.timeout = timeout

    @classmethod
    def from_settings(cls) -> "Bazarr | None":
        settings = db.get_settings()
        if not settings.get("bazarr_url") or not settings.get("bazarr_api_key"):
            return None
        return cls(settings["bazarr_url"], settings["bazarr_api_key"])

    # ---------------------------------------------------------------- plumbing
    def _request(self, method: str, path: str, params: dict[str, Any] | None = None,
                 form: dict[str, Any] | None = None) -> Any:
        url = f"{self.url}/api/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = urllib.parse.urlencode(form).encode() if form else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("X-API-KEY", self.api_key)
        if data:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                if not body:
                    return None
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    return body.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise BazarrError(f"{method} {path} -> HTTP {exc.code}: {exc.read()[:200]!r}") from exc
        except Exception as exc:  # noqa: BLE001
            raise BazarrError(f"{method} {path} -> {exc!r}") from exc

    def ping(self) -> dict[str, Any]:
        status = self._request("GET", "system/status")
        return (status or {}).get("data", status or {})

    # ------------------------------------------------------------- resolution
    def series(self) -> list[dict[str, Any]]:
        return (self._request("GET", "series") or {}).get("data", [])

    def movies(self) -> list[dict[str, Any]]:
        return (self._request("GET", "movies") or {}).get("data", [])

    def episodes(self, series_id: int) -> list[dict[str, Any]]:
        return (self._request("GET", "episodes", {"seriesid[]": series_id}) or {}).get("data", [])

    def locate(self, video: str) -> dict[str, Any] | None:
        """Map a video path to the Bazarr ids needed to act on its subtitles."""
        video_path = str(video)
        for movie in self.movies():
            if movie.get("path") and movie["path"] == video_path:
                return {"kind": "movie", "radarrid": movie["radarrId"], "title": movie.get("title")}
        for show in self.series():
            show_path = show.get("path") or ""
            if show_path and video_path.startswith(show_path.rstrip("/") + "/"):
                for episode in self.episodes(show["sonarrSeriesId"]):
                    if episode.get("path") == video_path:
                        return {
                            "kind": "episode",
                            "seriesid": show["sonarrSeriesId"],
                            "episodeid": episode["sonarrEpisodeId"],
                            "title": f"{show.get('title')} {episode.get('season')}x{episode.get('episode')}",
                        }
        return None

    # ---------------------------------------------------------------- history
    def history_for(self, target: dict[str, Any], subtitle_path: str) -> dict[str, Any] | None:
        """The download record for a subtitle, which carries provider and subs_id."""
        if target["kind"] == "episode":
            rows = (self._request("GET", "episodes/history",
                                  {"episodeid": target["episodeid"], "length": 50}) or {}).get("data", [])
        else:
            rows = (self._request("GET", "movies/history",
                                  {"radarrid": target["radarrid"], "length": 50}) or {}).get("data", [])
        name = Path(subtitle_path).name
        for row in rows:
            if row.get("subtitles_path") and Path(row["subtitles_path"]).name == name:
                if row.get("provider") and row.get("subs_id"):
                    return row
        for row in rows:                      # fall back to the newest usable entry
            if row.get("provider") and row.get("subs_id"):
                return row
        return None

    # ----------------------------------------------------------------- actions
    def blacklist_and_replace(self, target: dict[str, Any], subtitle_path: str,
                              language: str, history: dict[str, Any]) -> None:
        """Blacklist this subtitle, delete it, and let Bazarr fetch another."""
        form = {
            "provider": history["provider"],
            "subs_id": history["subs_id"],
            "language": history.get("language") or language,
            "subtitles_path": subtitle_path,
        }
        if target["kind"] == "episode":
            form |= {"seriesid": target["seriesid"], "episodeid": target["episodeid"]}
            self._request("POST", "episodes/blacklist", form=form)
        else:
            form |= {"radarrid": target["radarrid"]}
            self._request("POST", "movies/blacklist", form=form)

    def delete_and_search(self, target: dict[str, Any], subtitle_path: str, language: str) -> None:
        """Used when there is no history entry to blacklist: delete, then search."""
        common = {"language": language, "forced": "False", "hi": "False"}
        if target["kind"] == "episode":
            ids = {"seriesid": target["seriesid"], "episodeid": target["episodeid"]}
            self._request("DELETE", "episodes/subtitles", form=common | ids | {"path": subtitle_path})
            self._request("PATCH", "episodes/subtitles", form=common | ids)
        else:
            ids = {"radarrid": target["radarrid"]}
            self._request("DELETE", "movies/subtitles", form=common | ids | {"path": subtitle_path})
            self._request("PATCH", "movies/subtitles", form=common | ids)

    def replace(self, video: str, subtitle_path: str, language: str) -> str:
        """Ask Bazarr for a different subtitle. Returns the method used."""
        target = self.locate(video)
        if not target:
            raise BazarrError(f"Bazarr does not know this file: {video}")
        history = self.history_for(target, subtitle_path)
        if history:
            self.blacklist_and_replace(target, subtitle_path, language, history)
            logx(logging.INFO, "blacklisted subtitle in Bazarr", subtitle=subtitle_path,
                 provider=history.get("provider"), title=target.get("title"))
            return f"blacklisted ({history.get('provider')}) and re-searched"
        self.delete_and_search(target, subtitle_path, language)
        logx(logging.INFO, "deleted subtitle and asked Bazarr to search", subtitle=subtitle_path,
             title=target.get("title"))
        return "deleted and re-searched"
