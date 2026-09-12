"""arrsubsync: keeps an *arr subtitle library in sync.

Three ways in:
  * POST /sync      - Bazarr's post-processing hook calls this after a download
  * the dashboard   - status, history, settings, and buttons for the jobs
  * python -m app.bulk - the command line, for scripted sweeps
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app import alass, auth, bazarr, config, db, jobs, syncer, web
from app.alass import logx
from app.shifts import describe

alass.setup_logging()

@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    db.log_event("service", "arrsubsync started")
    task = asyncio.create_task(scheduler())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    lifespan=lifespan,
    title="arrsubsync",
    version="2.0.0",
    description="Subtitle synchronisation for *arr stacks, powered by alass.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

STARTED = time.time()


async def scheduler() -> None:
    """Run a full sweep every `schedule_hours`, when enabled."""
    while True:
        try:
            settings = db.get_settings()
            if settings.get("schedule_enabled"):
                hours = max(0.25, float(settings.get("schedule_hours", 24)))
                last = db.recent_runs(1)
                due = True
                if last and last[0]["kind"] == "scheduled" and last[0]["finished"]:
                    due = (time.time() - last[0]["finished"]) >= hours * 3600
                if due and not jobs.runner.busy:
                    jobs.runner.start("scheduled", jobs.full_job)
        except Exception as exc:  # noqa: BLE001 - the loop must survive anything
            logx(logging.ERROR, "scheduler error", error=repr(exc))
        await asyncio.sleep(300)


# --------------------------------------------------------------------------- #
# authentication
# --------------------------------------------------------------------------- #


def current_user(request: Request) -> str:
    user = auth.read_token(request.cookies.get(auth.COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


@app.post("/login")
async def login(response: Response, username: str = Form(...), password: str = Form(...)) -> Any:
    if not auth.check_login(username, password):
        db.log_event("auth", f"failed login for {username!r}", level="warning")
        return JSONResponse(status_code=401, content={"error": "wrong username or password"})
    token = auth.make_token(username)
    body = JSONResponse({"status": "ok"})
    body.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax",
                    max_age=auth.SESSION_DAYS * 86400)
    db.log_event("auth", f"{username} signed in")
    return body


@app.post("/api/setup")
async def setup(password: str = Form(...)) -> Any:
    """First-run: choose the password when none was supplied by environment."""
    if auth.configured():
        raise HTTPException(status_code=409, detail="already configured")
    if len(password) < 8:
        return JSONResponse(status_code=400, content={"error": "use at least 8 characters"})
    auth.set_password(password)
    return {"status": "ok"}


@app.post("/logout")
async def logout() -> Any:
    body = RedirectResponse("/", status_code=303)
    body.delete_cookie(auth.COOKIE)
    return body


# --------------------------------------------------------------------------- #
# the hook Bazarr calls
# --------------------------------------------------------------------------- #


class SyncRequest(BaseModel):
    video: str = Field(..., description="Absolute path to the video, as seen in this container.")
    subtitle: str = Field(..., description="Absolute path to the subtitle.")
    split_penalty: float | None = None
    no_splits: bool | None = Field(default=None, description="alass --no-split: offset only.")
    speed_optimization: float | None = None
    verify_after: bool | None = None
    verify_threshold: float | None = None
    dry_run: bool = False

    def overrides(self) -> dict[str, Any]:
        return {
            "alass_split_penalty": self.split_penalty,
            "alass_no_split": self.no_splits,
            "alass_speed_optimization": self.speed_optimization,
            "verify_after": self.verify_after,
            "verify_threshold": self.verify_threshold,
        }


@app.post("/sync")
async def sync_endpoint(req: SyncRequest) -> JSONResponse:
    try:
        result = await syncer.sync(
            req.video, req.subtitle, dry_run=req.dry_run, overrides=req.overrides()
        )
    except syncer.Rejected as exc:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": exc.message, "field": exc.field},
        )

    # A subtitle that cannot be aligned is a job for Bazarr, not for alass.
    if result.status in ("reverted", "failed") and db.get_setting("auto_replace"):
        if not jobs.runner.busy:
            jobs.runner.start("heal", lambda job: jobs.heal_job(job, [result.subtitle]))
            result.details["auto_replace"] = "queued a replacement through Bazarr"

    return JSONResponse(status_code=result.http_status, content=result.as_dict())


@app.get("/health")
async def health() -> JSONResponse:
    tools = alass.available()
    healthy = bool(tools["alass"]) and tools["ffmpeg"] and tools["ffprobe"] and config.MEDIA_ROOT.is_dir()
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "version": app.version,
            "media_root": str(config.MEDIA_ROOT),
            "media_root_mounted": config.MEDIA_ROOT.is_dir(),
            "uptime_seconds": round(time.time() - STARTED),
            **tools,
        },
    )


# --------------------------------------------------------------------------- #
# dashboard API
# --------------------------------------------------------------------------- #


@app.get("/api/status")
async def status(user: str = Depends(current_user)) -> dict[str, Any]:
    counts = db.counts()
    return {
        "counts": counts,
        "stats": db.stats(),
        "job": jobs.runner.job.as_dict() if jobs.runner.job else None,
        "runs": db.recent_runs(5),
        "events": db.recent_events(25),
        "history": db.daily_history(14),
        "bazarr": bool(bazarr.Bazarr.from_settings()),
        "scheduled": db.get_setting("schedule_enabled"),
        "auto_replace": db.get_setting("auto_replace"),
        "health": alass.available(),
    }


@app.get("/api/subtitles")
async def subtitles(status: str = "problem", search: str = "", limit: int = 100,
                    offset: int = 0, user: str = Depends(current_user)) -> dict[str, Any]:
    rows = db.list_subtitles(status=status, limit=limit, offset=offset, search=search or None)
    for row in rows:
        row["name"] = Path(row["path"]).name
        row["shift_text"] = describe(row["worst_shift"]) if row["worst_shift"] else None
    return {"items": rows, "counts": db.counts()}


class JobRequest(BaseModel):
    paths: list[str] | None = None


@app.post("/api/jobs/{kind}")
async def start_job(kind: str, req: JobRequest | None = None,
                    user: str = Depends(current_user)) -> dict[str, Any]:
    paths = (req.paths if req else None) or None
    factories = {
        "scan": lambda job: jobs.scan_job(job),
        "check": lambda job: jobs.check_job(job, apply=False, only=paths),
        "fix": lambda job: jobs.check_job(job, apply=True, only=paths),
        "heal": lambda job: jobs.heal_job(job, paths),
        "full": jobs.full_job,
    }
    if kind not in factories:
        raise HTTPException(status_code=404, detail=f"unknown job {kind!r}")
    try:
        job = jobs.runner.start(kind, factories[kind])
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.as_dict()


@app.post("/api/jobs/cancel")
async def cancel_job(user: str = Depends(current_user)) -> dict[str, Any]:
    return {"cancelled": jobs.runner.cancel()}


@app.post("/api/subtitles/ignore")
async def ignore(req: JobRequest, ignored: bool = True, user: str = Depends(current_user)) -> dict[str, Any]:
    for path in req.paths or []:
        db.set_ignored(path, ignored)
    return {"status": "ok"}


@app.get("/api/settings")
async def read_settings(user: str = Depends(current_user)) -> dict[str, Any]:
    settings = db.get_settings()
    settings["bazarr_api_key"] = "********" if settings.get("bazarr_api_key") else ""
    return settings


@app.post("/api/settings")
async def write_settings(payload: dict[str, Any], user: str = Depends(current_user)) -> dict[str, Any]:
    if payload.get("bazarr_api_key") == "********":
        payload.pop("bazarr_api_key")
    db.save_settings(payload)
    db.log_event("settings", f"updated: {', '.join(sorted(payload))}")
    return await read_settings(user)


@app.post("/api/bazarr/test")
async def test_bazarr(user: str = Depends(current_user)) -> dict[str, Any]:
    client = bazarr.Bazarr.from_settings()
    if client is None:
        return {"ok": False, "error": "no URL or API key configured"}
    try:
        info = client.ping()
        return {"ok": True, "bazarr_version": info.get("bazarr_version"), "info": info}
    except bazarr.BazarrError as exc:
        return {"ok": False, "error": str(exc)}


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    if not auth.configured():
        return HTMLResponse(web.SETUP_PAGE)
    if not auth.read_token(request.cookies.get(auth.COOKIE)):
        return HTMLResponse(web.LOGIN_PAGE)
    return HTMLResponse(web.DASHBOARD)
