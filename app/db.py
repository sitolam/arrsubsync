"""SQLite storage: what we know about every subtitle, plus events and settings."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from app import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS subtitles (
    path            TEXT PRIMARY KEY,
    video           TEXT NOT NULL,
    language        TEXT,
    status          TEXT NOT NULL DEFAULT 'unknown',
    summary         TEXT,
    verify_summary  TEXT,
    worst_shift     REAL,
    applied_shift   REAL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    replacements    INTEGER NOT NULL DEFAULT 0,
    size            INTEGER,
    mtime           REAL,
    checked_at      REAL,
    fixed_at        REAL,
    ignored         INTEGER NOT NULL DEFAULT 0,
    note            TEXT
);
CREATE INDEX IF NOT EXISTS subtitles_status ON subtitles(status);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL,
    kind    TEXT NOT NULL,
    path    TEXT,
    message TEXT NOT NULL,
    data    TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts DESC);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    kind      TEXT NOT NULL,
    started   REAL NOT NULL,
    finished  REAL,
    status    TEXT NOT NULL,
    totals    TEXT
);
"""

# Statuses a subtitle can be in.
OK = "ok"               # verified against the audio, nothing to do
FIXED = "fixed"         # we corrected it and the correction verified
OFF = "off"             # out of sync, not yet corrected
REVERTED = "reverted"   # correction did not converge; original restored
FAILED = "failed"       # alass could not align it at all
UNKNOWN = "unknown"     # not checked yet
BAD = (REVERTED, FAILED)


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        _local.conn = conn
    return conn


def init() -> None:
    conn = connect()
    with conn:
        conn.executescript(SCHEMA)


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #


def get_settings() -> dict[str, Any]:
    values = dict(config.DEFAULTS)
    for row in connect().execute("SELECT key, value FROM settings"):
        if row["key"] in values:
            try:
                values[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                values[row["key"]] = row["value"]
    return values


def get_setting(key: str) -> Any:
    return get_settings().get(key)


def save_settings(updates: dict[str, Any]) -> None:
    conn = connect()
    with conn:
        for key, value in updates.items():
            if key not in config.DEFAULTS:
                continue
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )


def get_secret(name: str) -> str | None:
    row = connect().execute("SELECT value FROM settings WHERE key=?", (name,)).fetchone()
    return json.loads(row["value"]) if row else None


def put_secret(name: str, value: str) -> None:
    conn = connect()
    with conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (name, json.dumps(value)),
        )


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #


def log_event(kind: str, message: str, *, level: str = "info", path: str | None = None,
              data: dict[str, Any] | None = None) -> None:
    conn = connect()
    with conn:
        conn.execute(
            "INSERT INTO events(ts, level, kind, path, message, data) VALUES(?,?,?,?,?,?)",
            (time.time(), level, kind, path, message, json.dumps(data) if data else None),
        )
        conn.execute(
            "DELETE FROM events WHERE id < (SELECT MAX(id) - 5000 FROM events)"
        )


def recent_events(limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM events"
    params: list[Any] = []
    if kind:
        sql += " WHERE kind = ?"
        params.append(kind)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in connect().execute(sql, params)]


# --------------------------------------------------------------------------- #
# subtitles
# --------------------------------------------------------------------------- #


def upsert_subtitle(path: str, video: str, **fields: Any) -> None:
    conn = connect()
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k}=excluded.{k}" for k in fields)
    with conn:
        conn.execute(
            f"INSERT INTO subtitles(path, video{', ' + columns if fields else ''}) "
            f"VALUES(?, ?{', ' + placeholders if fields else ''}) "
            f"ON CONFLICT(path) DO UPDATE SET video=excluded.video"
            + (f", {updates}" if fields else ""),
            (path, video, *fields.values()),
        )


def bump(path: str, column: str, by: int = 1) -> None:
    conn = connect()
    with conn:
        conn.execute(f"UPDATE subtitles SET {column} = COALESCE({column}, 0) + ? WHERE path = ?", (by, path))


def get_subtitle(path: str) -> dict[str, Any] | None:
    row = connect().execute("SELECT * FROM subtitles WHERE path = ?", (path,)).fetchone()
    return dict(row) if row else None


def set_ignored(path: str, ignored: bool) -> None:
    conn = connect()
    with conn:
        conn.execute("UPDATE subtitles SET ignored = ? WHERE path = ?", (int(ignored), path))


def forget_missing(seen: Iterable[str]) -> int:
    """Drop rows for subtitles that no longer exist on disk."""
    conn = connect()
    keep = set(seen)
    gone = [r["path"] for r in conn.execute("SELECT path FROM subtitles") if r["path"] not in keep]
    with conn:
        conn.executemany("DELETE FROM subtitles WHERE path = ?", [(p,) for p in gone])
    return len(gone)


def counts() -> dict[str, int]:
    rows = connect().execute(
        "SELECT status, COUNT(*) AS n FROM subtitles WHERE ignored = 0 GROUP BY status"
    )
    out = {OK: 0, FIXED: 0, OFF: 0, REVERTED: 0, FAILED: 0, UNKNOWN: 0}
    for row in rows:
        out[row["status"]] = row["n"]
    out["total"] = sum(out.values())
    out["needs_replacement"] = out[REVERTED] + out[FAILED]
    out["in_sync"] = out[OK] + out[FIXED]
    return out


def list_subtitles(status: str | None = None, limit: int = 200, offset: int = 0,
                   search: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM subtitles WHERE 1=1"
    params: list[Any] = []
    if status and status != "all":
        if status == "problem":
            sql += " AND status IN (?, ?, ?)"
            params += [REVERTED, FAILED, OFF]
        else:
            sql += " AND status = ?"
            params.append(status)
    if search:
        sql += " AND path LIKE ?"
        params.append(f"%{search}%")
    sql += " ORDER BY (status IN ('reverted','failed')) DESC, checked_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [dict(r) for r in connect().execute(sql, params)]


def stats() -> dict[str, Any]:
    conn = connect()
    fixed = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(ABS(applied_shift)), 0) AS total "
        "FROM subtitles WHERE fixed_at IS NOT NULL"
    ).fetchone()
    biggest = conn.execute(
        "SELECT path, applied_shift FROM subtitles WHERE applied_shift IS NOT NULL "
        "ORDER BY ABS(applied_shift) DESC LIMIT 1"
    ).fetchone()
    replaced = conn.execute("SELECT COALESCE(SUM(replacements), 0) AS n FROM subtitles").fetchone()
    return {
        "corrected": fixed["n"],
        "seconds_corrected": round(fixed["total"], 1),
        "biggest_correction": dict(biggest) if biggest else None,
        "replacements": replaced["n"],
    }


def daily_history(days: int = 14) -> list[dict[str, Any]]:
    """Corrections per day, for the dashboard chart."""
    since = time.time() - days * 86400
    rows = connect().execute(
        "SELECT CAST((fixed_at - ?) / 86400 AS INTEGER) AS bucket, COUNT(*) AS n "
        "FROM subtitles WHERE fixed_at IS NOT NULL AND fixed_at >= ? GROUP BY bucket",
        (since, since),
    )
    buckets = {int(r["bucket"]): r["n"] for r in rows}
    return [{"day": d - days + 1, "count": buckets.get(d, 0)} for d in range(days)]


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #


def start_run(kind: str) -> int:
    conn = connect()
    with conn:
        cur = conn.execute(
            "INSERT INTO runs(kind, started, status) VALUES(?, ?, 'running')", (kind, time.time())
        )
    return int(cur.lastrowid)


def finish_run(run_id: int, status: str, totals: dict[str, Any]) -> None:
    conn = connect()
    with conn:
        conn.execute(
            "UPDATE runs SET finished = ?, status = ?, totals = ? WHERE id = ?",
            (time.time(), status, json.dumps(totals), run_id),
        )


def recent_runs(limit: int = 10) -> list[dict[str, Any]]:
    rows = connect().execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
    out = []
    for row in rows:
        item = dict(row)
        item["totals"] = json.loads(item["totals"]) if item["totals"] else {}
        out.append(item)
    return out
