"""Finding video/subtitle pairs on disk."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app import config


@dataclass(frozen=True)
class Pair:
    video: Path
    subtitle: Path

    @property
    def tags(self) -> list[str]:
        """Dotted tokens between the video stem and the extension.

        `Foo (2019).en.hi.srt` beside `Foo (2019).mkv` -> ["en", "hi"].
        """
        rest = self.subtitle.name[len(self.video.stem):]
        if self.subtitle.suffix:
            rest = rest[: -len(self.subtitle.suffix)]
        return [t for t in rest.split(".") if t]

    @property
    def language(self) -> str | None:
        for tag in self.tags:
            if len(tag) in (2, 3) and tag.isalpha():
                return tag.lower()
        return None


def find_pairs(root: Path) -> Iterator[Pair]:
    """Yield every (video, subtitle) pair sitting side by side under `root`."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in sorted(dirnames) if not d.startswith(".")]
        directory = Path(dirpath)
        names = sorted(filenames)
        videos = [n for n in names if Path(n).suffix.lower() in config.VIDEO_SUFFIXES]
        subtitles = [n for n in names if Path(n).suffix.lower() in config.SUBTITLE_SUFFIXES]
        if not videos or not subtitles:
            continue
        for video_name in videos:
            stem = Path(video_name).stem
            for sub_name in subtitles:
                # The dot stops `Foo.mkv` claiming `Foo Part 2.srt`.
                if sub_name.startswith(stem + "."):
                    yield Pair(directory / video_name, directory / sub_name)


def video_for(subtitle: Path) -> Path | None:
    """The video a given subtitle belongs to, if one is beside it."""
    directory = subtitle.parent
    if not directory.is_dir():
        return None
    for name in sorted(os.listdir(directory)):
        if Path(name).suffix.lower() not in config.VIDEO_SUFFIXES:
            continue
        if subtitle.name.startswith(Path(name).stem + "."):
            return directory / name
    return None


def wanted(pair: Pair, settings: dict) -> bool:
    skip = {t.strip().lower() for t in str(settings.get("skip_tags", "")).split(",") if t.strip()}
    languages = {t.strip().lower() for t in str(settings.get("languages", "")).split(",") if t.strip()}
    tags = [t.lower() for t in pair.tags]
    if any(t in skip for t in tags):
        return False
    if languages and not any(t in languages for t in tags):
        return False
    return True
