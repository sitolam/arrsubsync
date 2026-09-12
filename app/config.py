"""Configuration: environment defaults, overridable from the dashboard."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "/data")).resolve()
DATA_DIR = Path(os.environ.get("ARRSUBSYNC_DATA", "/config"))
DB_PATH = DATA_DIR / "arrsubsync.db"
PORT = int(os.environ.get("PORT", "8765"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

ALASS_BIN = os.environ.get("ALASS_BIN", "alass")
FFMPEG_PATH = os.environ.get("ALASS_FFMPEG_PATH", "ffmpeg")
FFPROBE_PATH = os.environ.get("ALASS_FFPROBE_PATH", "ffprobe")

SUBTITLE_SUFFIXES = {".srt", ".ssa", ".ass", ".idx"}
VIDEO_SUFFIXES = {
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".webm", ".mpg", ".mpeg", ".wmv", ".m2ts",
}

# Settings a user can change at runtime. Environment variables seed the
# defaults; whatever the dashboard saves wins from then on.
DEFAULTS: dict[str, Any] = {
    "alass_timeout": float(os.environ.get("ALASS_TIMEOUT", "180")),
    "alass_split_penalty": os.environ.get("ALASS_SPLIT_PENALTY", ""),
    "alass_no_split": os.environ.get("ALASS_NO_SPLITS", "false").lower() in {"1", "true", "yes"},
    "alass_speed_optimization": os.environ.get("ALASS_SPEED_OPTIMIZATION", ""),
    "max_concurrency": int(os.environ.get("MAX_CONCURRENCY", "2")),
    # Verification: a correct sync is a fixed point, so re-aligning the result
    # must find nothing left to do.
    "verify_after": os.environ.get("ARRSUBSYNC_VERIFY_AFTER", "true").lower() in {"1", "true", "yes"},
    "verify_threshold": float(os.environ.get("ARRSUBSYNC_VERIFY_THRESHOLD", "2.0")),
    # Backups of every subtitle before it is first modified.
    "backup_enabled": True,
    "backup_dir": os.environ.get("ARRSUBSYNC_BACKUP_DIR", str(MEDIA_ROOT / ".arrsubsync-backups")),
    # Automatic replacement of subtitles alass cannot align.
    "auto_replace": os.environ.get("ARRSUBSYNC_AUTO_REPLACE", "false").lower() in {"1", "true", "yes"},
    "max_replace_attempts": int(os.environ.get("ARRSUBSYNC_MAX_ATTEMPTS", "3")),
    "replace_wait_seconds": 90.0,
    # Scheduled full sweeps of the library.
    "schedule_enabled": os.environ.get("ARRSUBSYNC_SCHEDULE", "false").lower() in {"1", "true", "yes"},
    "schedule_hours": float(os.environ.get("ARRSUBSYNC_SCHEDULE_HOURS", "24")),
    "skip_tags": os.environ.get("ARRSUBSYNC_SKIP_TAGS", "forced"),
    "languages": os.environ.get("ARRSUBSYNC_LANGUAGES", ""),
    "bazarr_url": os.environ.get("BAZARR_URL", ""),
    "bazarr_api_key": os.environ.get("BAZARR_API_KEY", ""),
}

SECRET_ENV = "ARRSUBSYNC_SECRET"
USERNAME = os.environ.get("ARRSUBSYNC_USERNAME", "admin")
PASSWORD_ENV = os.environ.get("ARRSUBSYNC_PASSWORD", "")
AUTH_DISABLED = os.environ.get("ARRSUBSYNC_NO_AUTH", "false").lower() in {"1", "true", "yes"}
