"""The command line."""

from __future__ import annotations

import json


def test_status_reports_an_empty_library(env, capsys):
    from app import cli

    assert cli.main(["status"]) == 0
    assert "library      : 0 subtitles tracked" in capsys.readouterr().out


def test_scan_then_status(env, capsys):
    from app import cli

    cli.main(["scan"])
    capsys.readouterr()
    cli.main(["status"])
    assert "4 subtitles tracked" in capsys.readouterr().out


def test_check_changes_nothing_and_reports(env, capsys):
    from app import cli

    before = env.subtitle.read_text()
    assert cli.main(["check", "--json"]) == 0
    assert env.subtitle.read_text() == before
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["off"] >= 1


def test_fix_applies(env, capsys):
    from app import cli

    cli.main(["fix"])
    capsys.readouterr()
    assert "00:00:02,500" in env.subtitle.read_text()


def test_exit_code_flags_problems(env, capsys, monkeypatch):
    from app import cli

    monkeypatch.setenv("STUB_MODE", "drift")
    assert cli.main(["fix"]) == 1, "a reverted subtitle must not look like success"


def test_bulk_shim_forwards(env, capsys):
    from app import bulk

    assert bulk.main([str(env.media), "--apply"]) == 0
    assert "deprecated" in capsys.readouterr().err
