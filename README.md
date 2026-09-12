# alass-sync

[![CI](https://github.com/sitolam/alass-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/sitolam/alass-sync/actions/workflows/ci.yml)
[![Docker](https://github.com/sitolam/alass-sync/actions/workflows/docker.yml/badge.svg)](https://github.com/sitolam/alass-sync/actions/workflows/docker.yml)
[![GHCR](https://img.shields.io/badge/ghcr.io-alass--sync-blue?logo=docker&logoColor=white)](https://github.com/sitolam/alass-sync/pkgs/container/alass-sync)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A small HTTP sidecar that re-syncs subtitles with [alass](https://github.com/kaegi/alass),
built to be called by [Bazarr](https://www.bazarr.media/)'s Custom Post-Processing hook
in a Docker Compose `*arr` stack.

Bazarr POSTs the video path and the subtitle path; the sidecar runs alass on those files
and atomically replaces the subtitle with the aligned version. Both containers mount the
same media volume, so nothing is uploaded — files are handled by path.

**Requirements:** Docker, a Bazarr container, and a media volume both containers
can mount. Nothing else — no database, no queue, no auth, no changes to the
Bazarr image.

## Contents

- [Quick start](#quick-start)
- [Why not Bazarr's built-in sync?](#why-not-bazarrs-built-in-sync)
- [API](#api) — [`POST /sync`](#post-sync), [`GET /health`](#get-health)
- [Configuration](#configuration)
- [Install into an existing *arr stack](#install-into-an-existing-arr-stack)
- [Image tags](#image-tags)
- [Wire it into Bazarr](#wire-it-into-bazarr)
- [Verify it end to end](#verify-it-end-to-end)
- [Troubleshooting](#troubleshooting)
- [**Bulk re-sync your existing library**](#bulk-re-sync-your-existing-library)
  — [dry run](#step-1--dry-run), [backups](#backups-and-restoring),
  [resuming](#resuming-and-running-it-again-later),
  [speed](#speed-and-load), [filters](#filtering-what-gets-touched),
  [all options](#all-options), [failures](#when-a-subtitle-fails)
- [Development](#development)

## Quick start

Add one service to your `docker-compose.yml`, next to Bazarr. Nothing to clone,
nothing to build — the image is published for `linux/amd64` and `linux/arm64`:

```yaml
  alass-sync:
    image: ghcr.io/sitolam/alass-sync:latest
    container_name: alass-sync
    restart: unless-stopped
    volumes:
      - /mnt/media:/data     # <- same media mount as Bazarr
    networks:
      - media                # <- same network as Bazarr
```

```bash
docker compose up -d alass-sync
docker exec bazarr curl -s http://alass-sync:8765/health
```

Then paste this into Bazarr → **Settings → Subtitles → Post-Processing →
Post-processing command** (note: no quotes around the variables — Bazarr adds
them itself):

```
curl -sS --max-time 300 -X POST http://alass-sync:8765/sync -H Content-Type:application/json -d '{"video": {{episode}}, "subtitle": {{subtitles}}}'
```

That's it. Every subtitle Bazarr downloads from then on gets re-aligned with
alass. For the subtitles you already have, see
[Bulk re-sync your existing library](#bulk-re-sync-your-existing-library).
The rest of this README explains each piece.

## Why not Bazarr's built-in sync?

Bazarr's built-in subtitle sync uses **ffsubsync**, which fits a single global
offset between the subtitle and the audio track. That works when a subtitle is
uniformly early or late, and fails when it is not:

- **Non-constant drift** — a subtitle cut for a different release (extra recap,
  different ad breaks, PAL speed-up, an extended cut) drifts by a different amount
  in each act. One offset cannot fix all of them.
- **Wrong audio track** — if the picked track is a commentary or a dub, the fitted
  offset is meaningless.

**alass** solves a different problem: it uses dynamic programming to align the
subtitle to the audio while allowing the timeline to be *split* into segments, each
shifted independently, with a penalty that discourages gratuitous splits. So it can
absorb ad breaks and inserted scenes instead of averaging them into one bad offset.
The `--split-penalty` value (useful range 5–20, default 7) controls how eagerly it
introduces those breaks; `--no-splits` makes it behave like a fast pure-offset sync.

Trade-off: alass is slower than a linear fit and needs `ffmpeg`/`ffprobe` to extract
audio, both of which this image handles. On a normal episode it finishes in seconds
to tens of seconds.

## What's in here

| Path | What it is |
| --- | --- |
| `app/main.py` | The FastAPI service (`POST /sync`, `GET /health`) |
| `app/bulk.py` | CLI that re-syncs an existing library through the service |
| `Dockerfile` | Builds alass (prebuilt binary on amd64, from Rust source on arm64) + static ffmpeg + the service |
| `requirements.txt` | Python dependencies |
| `compose-snippet.yml` | The service block to paste into your existing `docker-compose.yml` |
| `examples/bazarr-alass-sync.sh` | Optional shell wrapper for Bazarr, if you prefer a script over a one-liner |
| `tests/` | pytest suite that exercises the API against a stub alass binary |
| `.github/workflows/` | CI (pytest) and the multi-arch GHCR publish workflow |

## API

### `POST /sync`

```json
{
  "video": "/data/Movies/Foo (2019)/Foo (2019).mkv",
  "subtitle": "/data/Movies/Foo (2019)/Foo (2019).en.srt"
}
```

Optional fields: `split_penalty` (float), `no_splits` (bool),
`speed_optimization` (float — `0` turns alass's speed shortcut off, slower but
more accurate), `disable_fps_guessing` (bool), `dry_run` (bool — runs alass but
leaves the original file alone).

Success (`200`):

```json
{
  "status": "ok",
  "request_id": "3f9c1a7b2d04",
  "video": "/data/Movies/Foo (2019)/Foo (2019).mkv",
  "subtitle": "/data/Movies/Foo (2019)/Foo (2019).en.srt",
  "replaced": true,
  "bytes_before": 48213,
  "bytes_after": 48219,
  "duration_seconds": 11.4,
  "alass_summary": "shifted block of 812 subtitles with length 0:41:07.000 by -0:00:11.997",
  "alass_stdout": "extracting audio from reference file ...",
  "alass_stderr": ""
}
```

`alass_summary` is the line where alass reports what it did (the shift it applied
and how many splits); progress bars are stripped from `alass_stdout`. Note that
alass writes its *errors* to stdout too, so check both fields when debugging.

Failure: `400` for a bad or non-existent path, `502` with alass's `stdout`/`stderr`
and exit code when alass itself fails, `504` when alass exceeds `ALASS_TIMEOUT` and is
killed, `500` for anything else. Every response body is JSON, so Bazarr's
post-processing log line tells you exactly what went wrong.

### `GET /health`

Returns `200` when the alass binary, `ffmpeg`, `ffprobe`, and the media mount are all
present; `503` otherwise. Used by the container `HEALTHCHECK`.

### Safety rules enforced on every request

- Paths must be **absolute** (as seen inside the container).
- Paths are `resolve()`d and must land **inside `MEDIA_ROOT`** (default `/data`) —
  `..` traversal and symlinks pointing outside are rejected.
- Both files must exist and be regular files; the subtitle and its directory must be
  writable.
- The subtitle extension must be one alass supports: `.srt`, `.ssa`, `.ass`, `.idx`.
- The synced file is written to a `.alass-sync-<id>.<ext>` sibling (dot-prefixed, keeping the original extension because alass infers the output format from it) and moved over the
  original with `os.replace` (atomic on the same filesystem). The original's mode and
  owner are copied onto the new file first, so PUID/PGID ownership survives.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `MEDIA_ROOT` | `/data` | Every path in a request must resolve inside this |
| `ALASS_BIN` | `alass` | Path to the alass binary |
| `ALASS_TIMEOUT` | `120` | Seconds before alass is killed and the request fails with `504` |
| `ALASS_SPLIT_PENALTY` | *(unset)* | Default `--split-penalty` (alass's own default is 7) |
| `ALASS_NO_SPLITS` | `false` | Default to `--no-split` (fast, offset-only) |
| `ALASS_VERIFY_AFTER` | `true` | Re-align after syncing and revert if it did not converge |
| `ALASS_VERIFY_THRESHOLD` | `2.0` | Residual shift, in seconds, that counts as converged |
| `ALASS_SPEED_OPTIMIZATION` | *(alass default 1)* | `0` disables the speed shortcut: slower, more accurate |
| `ALASS_DISABLE_FPS_GUESSING` | `false` | Stop alass correcting a framerate difference |
| `MAX_CONCURRENCY` | `2` | Simultaneous alass runs; alass is CPU-hungry |
| `LOG_LEVEL` | `INFO` | Log level for the JSON logs on stdout |

## Install into an existing *arr stack

1. Paste the block from [`compose-snippet.yml`](compose-snippet.yml) into the
   `services:` section of your `docker-compose.yml`:

   ```yaml
     alass-sync:
       image: ghcr.io/sitolam/alass-sync:latest
       container_name: alass-sync
       restart: unless-stopped
       environment:
         TZ: ${TZ:-Etc/UTC}
         MEDIA_ROOT: /data
         ALASS_TIMEOUT: 120
         MAX_CONCURRENCY: 2
         LOG_LEVEL: INFO
       volumes:
         - /etc/localtime:/etc/localtime:ro
         - /mnt/media:/data      # <- same as Bazarr's media mount
       networks:
         - media                 # <- same network as Bazarr
   ```

   Two things have to match your stack:

   - **The media mount must be identical to Bazarr's.** If Bazarr has
     `/mnt/media:/data`, use exactly that here. Then a path Bazarr reports is
     valid inside this container **unchanged**, which is the whole trick — no
     file is ever uploaded.
   - **The network must be one Bazarr is on**, so `http://alass-sync:8765`
     resolves by container name. If your compose file just uses the default
     network, drop the `networks:` key entirely and it works.

   If your stack pins static IPs on a custom subnet, use the mapping form and
   pick an address that is not already taken:

   ```yaml
       networks:
         media:
           ipv4_address: 10.0.0.42
   ```

2. Start it:

   ```bash
   docker compose up -d alass-sync
   ```

3. Check it's alive from Bazarr's own container, which also proves the network path:

   ```bash
   docker exec bazarr curl -s http://alass-sync:8765/health
   ```

   Expect `"status": "ok"`.

## Image tags

Published to [`ghcr.io/sitolam/alass-sync`](https://github.com/sitolam/alass-sync/pkgs/container/alass-sync),
multi-arch (`linux/amd64`, `linux/arm64`):

| Tag | What it tracks |
| --- | --- |
| `latest` | The newest release |
| `1`, `1.2`, `1.2.3` | Pin as loosely or tightly as you like |
| `edge` / `main` | The tip of `main`, may break |

Update with:

```bash
docker compose pull alass-sync && docker compose up -d alass-sync
```

### Building it yourself instead

```bash
git clone https://github.com/sitolam/alass-sync.git
cd alass-sync
docker build -t alass-sync .
```

Or point compose at the source with `build: ./alass-sync` in place of `image:`.
On amd64 the build downloads upstream's official `alass-linux64` binary and
takes under a minute. Upstream ships no arm64 binary, so on arm64 it compiles
alass from Rust source and takes several minutes.

The image is roughly 660 MB, most of it the static ffmpeg/ffprobe pair alass
needs to decode the audio track.

## Wire it into Bazarr

Bazarr: **Settings → Subtitles → Post-Processing → Use Custom Post-Processing**, then
put the command in the **Post-processing command** field and save.

### The exact string to paste

```
curl -sS --max-time 300 -X POST http://alass-sync:8765/sync -H Content-Type:application/json -d '{"video": {{episode}}, "subtitle": {{subtitles}}}'
```

**Do not add your own quotes around `{{episode}}` or `{{subtitles}}`.** This is the
part that trips people up: Bazarr's `pp_replace()` strips any quote character
immediately around a variable and then wraps the value in double quotes itself. So
`{{episode}}` expands to `"/data/Movies/Foo/Foo.mkv"`, quotes included, and the line
above becomes valid JSON:

```
-d '{"video": "/data/Movies/Foo/Foo.mkv", "subtitle": "/data/Movies/Foo/Foo.en.srt"}'
```

Two more details behind that command:

- On Linux, Bazarr runs the command with `shlex.split()` and **`shell=False`** — there
  is no shell. The single quotes are consumed by `shlex` as one argument (good), but
  `&&`, `|`, `>`, `$VAR` and globs will **not** work. One command only.
- `-H Content-Type:application/json` is written without quotes on purpose: with no
  shell, `shlex` would keep the quotes literal and curl would send a malformed header
  name.

### Variables Bazarr actually provides

Verified against `bazarr/utilities/post_processing.py` on `master`:

`{{directory}}`, `{{episode}}` (the **video** file path, for movies too),
`{{episode_name}}`, `{{subtitles}}` (the subtitle file path), `{{subtitles_language}}`,
`{{subtitles_language_code2}}`, `{{subtitles_language_code3}}`,
`{{subtitles_language_code2_dot}}`, `{{subtitles_language_code3_dot}}`,
`{{episode_language}}`, `{{episode_language_code2}}`, `{{episode_language_code3}}`,
`{{score}}`, `{{subtitle_id}}`, `{{provider}}`, `{{uploader}}`, `{{release_info}}`,
`{{series_id}}`, `{{episode_id}}`.

Each one is substituted already-double-quoted.

### Script alternative

If you would rather not depend on the quoting behaviour above (for example if your
filenames can contain `'`), copy `examples/bazarr-alass-sync.sh` to your Bazarr config
volume — `./bazarr/alass-sync.sh` on the host is `/config/alass-sync.sh` in the
container — `chmod +x` it, and use:

```
/config/alass-sync.sh {{episode}} {{subtitles}}
```

The script falls back to `wget` if `curl` is missing.

### Only run it on poor matches

Bazarr has a **post-processing threshold** setting (separate values for series and
movies). With it enabled, the command runs only when the match score is *below* the
threshold — a decent way to avoid re-aligning subtitles that are already fine.

## Verify it end to end

Force a subtitle download in Bazarr, then:

```bash
docker logs -f alass-sync
```

You should see a `sync started` line followed by `sync complete` with
`bytes_before`/`bytes_after` and a duration. Bazarr's own log
(**System → Logs**) shows the JSON body the service returned, under
`BAZARR Post-processing result for file ...`.

You can also test without Bazarr, from any container on the network:

```bash
docker exec alass-sync python - <<'PY'
import json, urllib.request
body = json.dumps({"video": "/data/Movies/Foo/Foo.mkv",
                   "subtitle": "/data/Movies/Foo/Foo.en.srt",
                   "dry_run": True}).encode()
req = urllib.request.Request("http://127.0.0.1:8765/sync", body,
                             {"Content-Type": "application/json"})
print(urllib.request.urlopen(req).read().decode())
PY
```

## Troubleshooting

**`curl: not found` in Bazarr's log.**
The `lscr.io/linuxserver/bazarr` image is not guaranteed to ship `curl`. Check first:

```bash
docker exec bazarr sh -c 'command -v curl; command -v wget'
```

If only `wget` is there, use this in the post-processing field instead:

```
wget -q -O - --timeout=300 --header=Content-Type:application/json --post-data '{"video": {{episode}}, "subtitle": {{subtitles}}}' http://alass-sync:8765/sync
```

If neither exists, install curl persistently with the LinuxServer mod
(`DOCKER_MODS=linuxserver/mods:universal-package-install` and
`INSTALL_PACKAGES=curl` on the bazarr service) rather than `apt-get`-ing inside a
container that gets recreated.

**Bazarr logs the whole thing as an error.**
Bazarr treats *any* stderr output from the command as an error line. `curl -sS` only
writes to stderr on a genuine failure. Avoid plain `-v`.

**`alass exited with code N` with stderr about ffmpeg / no audio.**
alass needs a decodable audio track. Check the file plays and that ffprobe sees audio:
`docker exec alass-sync ffprobe -hide_banner /data/path/to/file.mkv`.

**alass "finds no alignment" or the result is worse.**
Usually the reference audio is a different language or a commentary track, or the
subtitle is for a genuinely different cut. Try a different split penalty per request:
`{"video": "...", "subtitle": "...", "split_penalty": 15}`, or `"no_splits": true` for
a pure offset. Use `"dry_run": true` while experimenting so the original stays intact.

**`504` / "alass timed out".**
Long movies with `--split-penalty` low can be slow. Raise `ALASS_TIMEOUT`, and lower
`MAX_CONCURRENCY` if several syncs run at once on a small box.

**Permission denied writing the subtitle.**
The *arr containers write as `PUID`/`PGID`; this container runs as root, which can
read and write those files and copies the original file's owner and mode onto the
replacement, so ownership does not change. If you deliberately run this container as a
non-root user, that user needs write access to the subtitle *and* its directory —
otherwise the service returns `400` with "not writable" before touching anything.

**Path exists in Bazarr but `400 ... does not exist inside this container`.**
The two containers disagree about paths. Both must mount the media the same way
(for example both `/mnt/media:/data`). Compare:
`docker exec bazarr ls /data` versus `docker exec alass-sync ls /data`.

## Bulk re-sync your existing library

Bazarr's post-processing hook only fires for subtitles it downloads **from now
on**. To re-align the subtitles you already have, the image ships a walker:
`app.bulk` pairs each video with the subtitles sitting next to it and pushes
every pair through the same `/sync` endpoint, so the alignment is identical to
what the hook does.

It runs inside the container, where `/data` is your library:

```bash
docker exec alass-sync python -m app.bulk --help
```

### The short version

```bash
# 1. look - nothing is modified without --apply
docker exec alass-sync python -m app.bulk /data

# 2. try five files, keeping the originals
docker exec alass-sync python -m app.bulk /data --apply --limit 5 \
  --backup-dir /data/.alass-backups

# 3. check those five in a player, then let it run
docker exec alass-sync python -m app.bulk /data --apply \
  --backup-dir /data/.alass-backups
```

### Step 1 — dry run

The default mode lists every pair it would touch and changes nothing:

```bash
docker exec alass-sync python -m app.bulk /data
```

```
412 subtitle(s) found, 37 filtered out, 0 already done, 375 to process
would sync /data/Movies/Foo (2019)/Foo (2019).en.srt  (against Foo (2019).mkv)
would sync /data/TV/Bar/Season 01/Bar - S01E01.en.srt  (against Bar - S01E01.mkv)
...
Dry run. Nothing was changed. Re-run with --apply to do it.
{"dry_run": true, "would_sync": 375, "considered": 412, "skipped_done": 0, "skipped_filter": 37, "synced": 0, "failed": 0, "failures": []}
```

Read the header line before going further:

| Number | Meaning |
| --- | --- |
| `subtitle(s) found` | Subtitles that were successfully paired with a video |
| `filtered out` | Excluded by `--skip-tags` (`forced` by default), `--languages`, `--include`/`--exclude` |
| `already done` | Recorded in the state file by an earlier run |
| `to process` | What `--apply` would actually work on |

If `found` is far lower than the number of subtitles you know you have, the
pairing is not matching your layout — see
[How videos and subtitles are paired](#how-videos-and-subtitles-are-paired).

Narrow the dry run to check a subset before committing to it:

```bash
docker exec alass-sync python -m app.bulk "/data/Movies/Foo (2019)"
docker exec alass-sync python -m app.bulk /data --languages en --limit 20
```

### Step 2 — a small batch, with backups

```bash
docker exec alass-sync python -m app.bulk /data --apply --limit 5 \
  --backup-dir /data/.alass-backups
```

```
412 subtitle(s) found, 37 filtered out, 0 already done, 5 to process
[1/5] ok   /data/Movies/Foo (2019)/Foo (2019).en.srt  shifted block of 812 subtitles with length 1:38:12.000 by -0:00:11.997
[2/5] ok   /data/TV/Bar/Season 01/Bar - S01E01.en.srt  shifted block of 402 subtitles with length 0:41:07.000 by 0:00:02.140
[3/5] FAIL /data/Movies/Baz/Baz.en.srt  alass exited with code 1
...
done in 94.2s: 4 synced, 1 failed, 0 already done, 37 filtered
{"dry_run": false, "elapsed_seconds": 94.2, "considered": 412, ...}
```

Now open a couple of those in a player before doing the rest. **`--backup-dir`
is the difference between "undo" and "gone"** — see
[Backups and restoring](#backups-and-restoring).

### Step 3 — the whole library

```bash
docker exec alass-sync python -m app.bulk /data --apply \
  --backup-dir /data/.alass-backups
```

This is an overnight job on a real library: alass takes seconds to tens of
seconds per subtitle. To keep it alive when your shell goes away, run it
detached and log to a file:

```bash
docker exec -d alass-sync sh -c \
  'python -m app.bulk /data --apply --backup-dir /data/.alass-backups \
   > /data/.alass-bulk.log 2>&1'

# watch it
docker exec alass-sync tail -f /data/.alass-bulk.log
```

Interrupting it at any point is safe — each subtitle is replaced atomically,
and the state file means a re-run continues where it stopped.

### Output, exit codes and the summary

Progress goes to **stderr**, one line per subtitle, so you can watch it. The
final line on **stdout** is a JSON summary, so you can pipe it somewhere:

```bash
docker exec alass-sync python -m app.bulk /data --apply 2>/dev/null | jq .
```

```json
{
  "dry_run": false,
  "elapsed_seconds": 4471.8,
  "considered": 412,
  "skipped_done": 0,
  "skipped_filter": 37,
  "synced": 368,
  "failed": 7,
  "failures": [
    {"subtitle": "/data/Movies/Baz/Baz.en.srt", "error": "alass exited with code 1"}
  ]
}
```

Exit code is `0` when everything worked, `1` when at least one subtitle failed,
`2` when the directory you passed does not exist inside the container.

Per-subtitle detail — the alass command line, its stderr, timings — is in the
service's own log:

```bash
docker logs -f alass-sync
```

### Resuming, and running it again later

Every finished subtitle is recorded in `/data/.alass-sync-bulk-state.json` with
its size and mtime. So:

- An interrupted run continues where it stopped.
- Running it again does nothing:

  ```
  412 subtitle(s) found, 37 filtered out, 375 already done, 0 to process
  ```

- A subtitle that **changed** since (Bazarr replaced it with a better one) is
  picked up again automatically.

| Flag | Effect |
| --- | --- |
| `--redo` | Ignore the state file, sync everything again |
| `--state PATH` | Keep the state file somewhere else |
| `--no-state` | Do not read or write one at all |

The state file lives in `/data` because that is the only volume the container
is guaranteed to keep. It is dot-prefixed, so the walker skips it and media
scanners ignore it.

### Backups and restoring

`--backup-dir` copies each original before it is replaced, into a mirror of your
tree:

```
/data/.alass-backups/Movies/Foo (2019)/Foo (2019).en.srt
/data/.alass-backups/TV/Bar/Season 01/Bar - S01E01.en.srt
```

It never overwrites an existing backup, so re-running keeps the **pristine**
original rather than the last synced version.

Put one file back:

```bash
docker exec alass-sync cp "/data/.alass-backups/Movies/Foo (2019)/Foo (2019).en.srt" \
                          "/data/Movies/Foo (2019)/Foo (2019).en.srt"
```

Put everything back:

```bash
docker exec alass-sync sh -c 'cd /data/.alass-backups && cp -a . /data/'
```

When you are happy with the results, reclaim the space:

```bash
docker exec alass-sync rm -rf /data/.alass-backups
```

Without `--backup-dir` the old subtitles are simply gone. Bazarr can always
re-download, but that costs provider hits and loses your scores.

### Speed and load

alass is CPU-bound and this box probably also transcodes. Two limits stack:

- `--workers N` — how many requests the walker sends at once (default 2).
- `MAX_CONCURRENCY` — how many alass processes the **service** will run at once
  (default 2, set in the compose environment). This is the real cap; raising
  `--workers` alone only makes requests queue.

To go faster, raise both and recreate the container:

```yaml
    environment:
      MAX_CONCURRENCY: 4
```

```bash
docker compose up -d alass-sync
docker exec alass-sync python -m app.bulk /data --apply --workers 4 --backup-dir /data/.alass-backups
```

To go easier on the box, drop both to 1. `--no-splits` is dramatically faster
(pure offset, no dynamic-programming split search) if a whole batch only needs
shifting.

`ALASS_TIMEOUT` (default 120s) still applies per subtitle; a very long film with
a low split penalty can hit it and come back as `504`.

### Filtering what gets touched

| Flag | Effect |
| --- | --- |
| `--languages en,nl` | Only subtitles tagged with these languages |
| `--include-untagged` | With `--languages`, also take `Foo.srt`, which has no tag |
| `--skip-tags forced,hi` | Skip these tags. Default is `forced` |
| `--include GLOB` | Only paths matching the glob. Repeatable |
| `--exclude GLOB` | Skip paths matching the glob. Repeatable |
| `--limit N` | Stop after N subtitles |

Tags are the dotted tokens between the video name and the extension:
`Foo (2019).en.hi.srt` has tags `en` and `hi`.

Some recipes:

```bash
# English only, films only
docker exec alass-sync python -m app.bulk /data --apply --languages en --include '*/Movies/*'

# everything except one noisy show
docker exec alass-sync python -m app.bulk /data --apply --exclude '*/TV/Some Show/*'

# a fast offset-only pass over one season
docker exec alass-sync python -m app.bulk "/data/TV/Bar/Season 01" --apply --no-splits

# retry with a higher split penalty where the default over-split
docker exec alass-sync python -m app.bulk "/data/Movies/Foo (2019)" --apply --redo --split-penalty 15
```

### All options

| Flag | Default | Effect |
| --- | --- | --- |
| `root` | *(required)* | Directory to walk, e.g. `/data` or `/data/Movies` |
| `--apply` | off | Actually re-sync. Without it, dry run |
| `--verify` | off | Check everything against its audio, change nothing |
| `--verify-threshold S` | `0.5` | Shift below this counts as in sync |
| `--dry-run` | on | Explicitly ask for the default |
| `--backup-dir DIR` | *(none)* | Copy each original subtitle here first |
| `--languages` | *(all)* | Comma-separated language tags |
| `--include-untagged` | off | With `--languages`, also take untagged subtitles |
| `--skip-tags` | `forced` | Comma-separated tags to skip |
| `--include GLOB` | *(all)* | Only matching paths, repeatable |
| `--exclude GLOB` | *(none)* | Skip matching paths, repeatable |
| `--limit N` | *(none)* | Stop after N subtitles |
| `--workers N` | `2` | Parallel requests |
| `--timeout S` | `600` | Per-request HTTP timeout |
| `--split-penalty` | *(alass default 7)* | Passed through to alass |
| `--no-splits` | off | Passed through to alass: offset only, fast |
| `--speed-optimization N` | *(alass default 1)* | `0` = slower, more accurate |
| `--disable-fps-guessing` | off | Do not correct a framerate difference |
| `--state PATH` | `/data/.alass-sync-bulk-state.json` | Resume file |
| `--no-state` | off | Do not use a resume file |
| `--redo` | off | Ignore the resume file |
| `--endpoint URL` | `http://127.0.0.1:8765` | Where the service is |

### How videos and subtitles are paired

A subtitle belongs to a video in the same folder when its name is the video's
name plus a dot:

| Video | Claims | Does not claim |
| --- | --- | --- |
| `Foo (2019).mkv` | `Foo (2019).srt`, `Foo (2019).en.srt`, `Foo (2019).en.hi.srt` | `Foo (2019) Part 2.srt` |

Hidden folders are skipped, which keeps `.alass-backups` out of the walk.
Videos are matched by extension: `.mkv .mp4 .avi .m4v .mov .ts .webm .mpg .mpeg
.wmv`; subtitles by `.srt .ssa .ass .idx`.

That is Bazarr's default layout. **If you have Bazarr configured to store
subtitles in a separate folder, this walker will not find them** — sync those
through Bazarr, or move them next to the videos first.

### When a subtitle fails

A `FAIL` line names the subtitle and quotes the error. The usual causes:

| Error | Cause |
| --- | --- |
| `alass exited with code 1` | alass found no usable alignment: the audio is a different language or cut, or the subtitle belongs to another release |
| `alass timed out after 120s` | Long film, low split penalty. Raise `ALASS_TIMEOUT` or use `--no-splits` |
| `subtitle is not writable by this container` | PUID/PGID mismatch on the share |
| `unsupported subtitle format` | Not one of `.srt .ssa .ass .idx` — a `.vtt` or `.sub`, say |
| `HTTP 400 ... does not exist` | The path moved mid-run |

Failures are never partially written: the original is left exactly as it was.
To retry just those, take the paths from the summary's `failures` and re-run
with `--redo` on that folder, or with different alass settings:

```bash
docker exec alass-sync python -m app.bulk "/data/Movies/Baz" --apply --redo --no-splits
```

### Verifying a whole library

`--verify` checks every pair without touching a single file: it asks alass what
it *would* do and classifies the answer.

```bash
docker exec alass-sync python -m app.bulk /data --verify --workers 3
```

```
[1/527] ok      /data/Series/Show/S01E01.en.srt  shifted block of 402 subtitles ... by 0:00:00.000
[2/527] OFF     /data/Series/Show/S01E02.en.srt  shifted block of 388 subtitles ... by 0:00:20.335
[3/527] SUSPECT /data/Series/Show/S01E03.en.srt  3 blocks shifted by -0:00:19.474, -0:00:37.084, -0:04:26.168
verified 527: 512 in sync, 12 out of sync, 2 suspect, 1 failed
```

| Verdict | Meaning | What to do |
| --- | --- | --- |
| `ok` | Shift below `--verify-threshold` (default 0.5s) | Nothing |
| `OFF` | A real, coherent shift | `--apply` will fix it |
| `SUSPECT` | Blocks disagree by more than 30s — alass found no coherent alignment | Do **not** apply. The subtitle probably belongs to another release |
| `FAILED` | alass could not align at all | Replace the subtitle |

Exit code is 0 only when everything is `ok`, so it works as a cron check. The
JSON summary lists every non-`ok` file, so you can feed the paths back into
`--include`.

### The safety net: verify-after-apply

Every `/sync` that replaces a file re-aligns the result and checks that alass
has nothing left to do. A correct sync is a **fixed point**. If the second pass
instead wants to move the subtitle by more than `ALASS_VERIFY_THRESHOLD`
(default 2s), the sync is treated as failed, **the original is restored**, and
the response is `409`:

```json
{
  "status": "reverted",
  "error": "alass did not converge: after syncing, it wanted to move the subtitle by another 291.6s. The subtitle does not match this audio, so the original was restored.",
  "alass_summary": "shifted block of 1059 subtitles ... by -0:00:29.152",
  "verify_summary": "shifted block of 1059 subtitles ... by -0:04:51.628"
}
```

This is what stops a mismatched subtitle — one timed for a different cut, or
simply the wrong release — from being silently mangled. It costs a second alass
run per sync. Turn it off with `verify_after: false` per request or
`ALASS_VERIFY_AFTER=false`, but there is rarely a good reason to.

A clean-looking result is **not** sufficient evidence on its own: a single-block
shift of a few seconds can still be a file alass cannot really align. Only the
second pass tells you.

### Checking that a sync is actually good

alass is honest about what it did, and the summary line is worth reading:

```
shifted block of 482 subtitles with length 0:52:07.391 by 0:00:00.000
3 blocks shifted by -0:00:19.474, -0:00:37.084, -0:01:26.168
```

- **One block, near-zero shift** — already correct, nothing was wrong.
- **One block, a real shift** — a clean constant offset. Exactly what you want.
- **A few blocks with similar shifts** — drift from a framerate mismatch, correctly
  absorbed. Good.
- **Blocks with wildly different shifts, especially minutes apart** — treat with
  suspicion. That usually means alass could not find a real alignment and settled
  on noise.

The decisive test is that a correct sync is a **fixed point**: run it again and
alass should find nothing left to do.

```bash
docker exec alass-sync python -m app.bulk "/data/Series/Some Show/Season 01" --redo --apply
```

A second pass reporting `by 0:00:00.000` confirms it. A second pass reporting
*another* large shift means the subtitle does not match this audio at all — and
each further run makes it worse, because alass is realigning its own bad output.
Restore that file from your backup and see
[When alass cannot align a subtitle](#when-alass-cannot-align-a-subtitle).

### When alass cannot align a subtitle

Some pairs genuinely do not match: the subtitle is for another cut, another
release, or occasionally the wrong episode. Symptoms are a non-converging sync
(above) or a plain `alass exited with code 1`.

Things to try, in order:

```bash
# 1. restore the original first - never stack passes on a bad result
docker exec alass-sync cp "/data/.alass-backups/Series/.../Episode.nl.srt" \
                          "/data/Series/.../Episode.nl.srt"

# 2. accuracy over speed, and keep the timeline in one piece
docker exec alass-sync python -m app.bulk "/data/Series/.../Season 05" \
  --apply --redo --speed-optimization 0 --split-penalty 20

# 3. offset only - if the subtitle just needs shifting, this cannot go haywire
docker exec alass-sync python -m app.bulk "/data/Series/.../Season 05" \
  --apply --redo --no-splits
```

If none of that converges, the subtitle is the problem, not the alignment. Delete
it and let Bazarr fetch another one — preferably one whose release name matches
your file — then sync that.

### Note on other bulk tools

Tools such as [`bazarr-sync`](https://github.com/ajmandourah/bazarr-sync) and
[`bazarr-bulk`](https://github.com/mateoradman/bazarr-bulk) drive *Bazarr's own*
(ffsubsync) sync through its API. They work, but they do not use alass — which
is the point of this service. Use `app.bulk` for alass alignment.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r tests/requirements-dev.txt
pytest
```

The tests use a stub `alass` shell script, so they need neither alass nor ffmpeg
installed locally.

## License

MIT — see [LICENSE](LICENSE).
