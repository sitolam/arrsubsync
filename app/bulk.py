"""Deprecated: the command line moved to `app.cli`.

    python -m app.bulk /data --apply   ->   python -m app.cli fix
"""

from __future__ import annotations

import sys

from app import cli

MAPPING = {"--apply": "fix", "--verify": "check", "--dry-run": "check"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = "check"
    for flag, name in MAPPING.items():
        if flag in argv:
            command = name
            argv.remove(flag)
    root = next((a for a in argv if not a.startswith("-")), None)
    print(f"note: `app.bulk` is deprecated; this is now `python -m app.cli {command}`.",
          file=sys.stderr)
    forwarded = [command] + (["--root", root] if root else [])
    return cli.main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
