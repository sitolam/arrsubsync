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

Optional fields: `split_penalty` (float), `no_splits` (bool), `dry_run` (bool — runs
alass but leaves the original file alone).

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
| `ALASS_NO_SPLITS` | `false` | Default to `--no-splits` (fast, offset-only) |
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
on**. To re-align subtitles you already have, the image ships a walker that
pairs each video with the subtitles next to it and pushes every pair through
the same `/sync` endpoint.

Look first — nothing is touched without `--apply`:

```bash
docker exec alass-sync python -m app.bulk /data
```

```
412 subtitle(s) found, 37 filtered out, 0 already done, 375 to process
would sync /data/Movies/Foo (2019)/Foo (2019).en.srt  (against Foo (2019).mkv)
...
Dry run. Nothing was changed. Re-run with --apply to do it.
```

Then do it, keeping every original:

```bash
docker exec alass-sync python -m app.bulk /data \
  --apply --backup-dir /data/.alass-backups
```

```
[1/375] ok   /data/Movies/Foo (2019)/Foo (2019).en.srt  shifted block of 812 subtitles by -0:00:11.997
[2/375] FAIL /data/Movies/Bar/Bar.en.srt  alass exited with code 1
```

Progress goes to stderr, a JSON summary to stdout, and the exit code is
non-zero if anything failed.

### Do this first

- **Take a backup.** `--backup-dir` copies each original before it is replaced,
  into a mirror of your tree (`/data/.alass-backups/Movies/...`). It never
  overwrites an existing backup, so re-runs keep the pristine copy. Restoring is
  a `cp` back. Without this flag the old subtitles are gone.
- **Start narrow.** Run one folder first (`/data/Movies/Some Film`) and watch a
  couple of results in a player before turning it loose on the whole library.
- **It is slow.** alass takes seconds to tens of seconds per subtitle, so a
  large library is an overnight job. `--workers` runs several at once, but the
  service still caps concurrent alass processes at `MAX_CONCURRENCY`; raise both
  together, and keep an eye on CPU if this box also transcodes.

### Resuming

Finished subtitles are recorded in `/data/.alass-sync-bulk-state.json`, so an
interrupted run picks up where it stopped and a repeat run does nothing:

```
412 subtitle(s) found, 37 filtered out, 375 already done, 0 to process
```

A subtitle whose size or mtime changed since (Bazarr downloaded a new one) is
picked up again automatically. `--redo` ignores the state entirely, `--state
PATH` moves the file, `--no-state` disables it.

### Options

| Flag | Effect |
| --- | --- |
| `--apply` | Actually re-sync. Without it, dry run |
| `--backup-dir DIR` | Copy each original subtitle here first |
| `--languages en,nl` | Only these language tags |
| `--include-untagged` | With `--languages`, also take `Foo.srt` with no tag |
| `--skip-tags forced,hi` | Skip these tags (default: `forced`) |
| `--include` / `--exclude GLOB` | Filter by path, repeatable |
| `--limit N` | Stop after N subtitles — good for a first taste |
| `--workers N` | Parallel requests (default 2) |
| `--split-penalty` / `--no-splits` | Passed through to alass |
| `--redo`, `--state PATH`, `--no-state` | Control the resume file |
| `--endpoint URL` | Default `http://127.0.0.1:8765`, i.e. run inside the container |

### How videos and subtitles are paired

A subtitle belongs to a video in the same folder when its name is the video's
name plus a dot: `Foo (2019).mkv` claims `Foo (2019).en.srt`, `Foo (2019).srt`
and `Foo (2019).en.hi.srt`, but not `Foo (2019) Part 2.srt`. Hidden folders
(including the backup directory) are skipped.

That is Bazarr's own default layout. If you have Bazarr configured to store
subtitles in a **separate folder**, this walker will not find them — sync those
through Bazarr, or move them next to the videos.

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
