"""The HTTP surface: the Bazarr hook, the dashboard API, and who may call them."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_health_reports_the_tools(env):
    with TestClient(env.main.app) as c:
        body = c.get("/health").json()
    assert body["media_root_mounted"] is True
    assert body["version"]


def test_dashboard_requires_a_login(env):
    with TestClient(env.main.app) as c:
        assert "Sign in" in c.get("/").text
        assert c.get("/api/status").status_code == 401
        assert c.post("/api/jobs/scan").status_code == 401


def test_login_and_dashboard(client):
    assert "arrsubsync" in client.get("/").text
    assert client.get("/api/status").status_code == 200


def test_wrong_password_is_refused(env):
    with TestClient(env.main.app) as c:
        assert c.post("/login", data={"username": "admin", "password": "nope"}).status_code == 401


def test_sync_endpoint_syncs(env, client):
    r = client.post("/sync", json={"video": str(env.video), "subtitle": str(env.subtitle)})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["alass_summary"]


def test_sync_endpoint_reverts_and_returns_409(env, client, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "drift")
    before = env.subtitle.read_text()
    r = client.post("/sync", json={"video": str(env.video), "subtitle": str(env.subtitle)})
    assert r.status_code == 409
    assert r.json()["status"] == "reverted"
    assert env.subtitle.read_text() == before


def test_sync_endpoint_rejects_bad_paths(env, client):
    r = client.post("/sync", json={"video": "relative.mkv", "subtitle": str(env.subtitle)})
    assert r.status_code == 400
    assert "absolute" in r.json()["error"]


def test_sync_hook_needs_no_login(env):
    """Bazarr calls this from inside the network and has no session."""
    with TestClient(env.main.app) as c:
        r = c.post("/sync", json={"video": str(env.video), "subtitle": str(env.subtitle)})
    assert r.status_code == 200


def test_settings_round_trip_and_hide_the_api_key(client):
    client.post("/api/settings", json={"bazarr_url": "http://bazarr:6767",
                                       "bazarr_api_key": "secret-key",
                                       "verify_threshold": 3.5})
    body = client.get("/api/settings").json()
    assert body["verify_threshold"] == 3.5
    assert body["bazarr_api_key"] == "********", "the key must not be echoed back"


def test_masked_api_key_is_not_saved_over_the_real_one(env, client):
    client.post("/api/settings", json={"bazarr_api_key": "real-key"})
    client.post("/api/settings", json={"bazarr_api_key": "********"})
    assert env.db.get_settings()["bazarr_api_key"] == "real-key"


def test_jobs_can_be_started_and_only_one_at_a_time(client):
    assert client.post("/api/jobs/scan").status_code == 200
    assert client.post("/api/jobs/nonsense").status_code == 404


def test_subtitles_listing(env, client):
    client.post("/api/jobs/scan")
    body = client.get("/api/subtitles?status=all").json()
    assert body["counts"]["total"] >= 1
    assert all("name" in item for item in body["items"])


def test_ignoring_a_subtitle_hides_it_from_the_counts(env, client):
    client.post("/api/jobs/scan")
    import time
    for _ in range(40):
        if env.db.counts()["total"]:
            break
        time.sleep(0.05)
    before = env.db.counts()["total"]
    client.post("/api/subtitles/ignore", json={"paths": [str(env.subtitle)]})
    assert env.db.counts()["total"] == before - 1


def test_cancel_is_not_swallowed_by_the_job_route(client):
    """/api/jobs/cancel must not be read as a job named "cancel"."""
    r = client.post("/api/jobs/cancel")
    assert r.status_code == 200
    assert "cancelled" in r.json()


def test_scheduler_looks_at_the_last_scheduled_run(env):
    """A manual job running more recently must not make a sweep look overdue."""
    import time

    from app import db

    run = db.start_run("scheduled")
    db.finish_run(run, "finished", {})
    manual = db.start_run("heal")           # more recent, different kind
    db.finish_run(manual, "finished", {})

    previous = next((r for r in db.recent_runs(50)
                     if r["kind"] == "scheduled" and r["finished"]), None)
    assert previous is not None
    assert time.time() - previous["finished"] < 3600, "the scheduled run is recent, so nothing is due"
