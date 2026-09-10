#!/bin/sh
# Optional wrapper for Bazarr's Custom Post-Processing field.
#
# Use this instead of the one-line curl command if your paths can contain
# single quotes, or if you want the post-processing step to never fail the
# subtitle download.
#
# Install: copy to Bazarr's config volume, e.g. ./bazarr/alass-sync.sh on the
# host (that is /config/alass-sync.sh inside the container), then:
#   chmod +x ./bazarr/alass-sync.sh
#
# Bazarr Custom Post-Processing command:
#   /config/alass-sync.sh {{episode}} {{subtitles}}
#
# Bazarr already wraps each {{variable}} in double quotes, so do not add your
# own quotes around them.

set -eu

VIDEO="${1:?usage: alass-sync.sh <video> <subtitle>}"
SUBTITLE="${2:?usage: alass-sync.sh <video> <subtitle>}"

ENDPOINT="${ALASS_SYNC_URL:-http://alass-sync:8765/sync}"

# Bazarr logs stderr as an error, so keep normal output on stdout and only
# exit non-zero on a real failure.
if command -v curl >/dev/null 2>&1; then
    curl -sS --max-time 300 -X POST "$ENDPOINT" \
        -H 'Content-Type: application/json' \
        --data-binary "$(printf '{"video": "%s", "subtitle": "%s"}' "$VIDEO" "$SUBTITLE")"
elif command -v wget >/dev/null 2>&1; then
    wget -q -O - --timeout=300 \
        --header='Content-Type: application/json' \
        --post-data="$(printf '{"video": "%s", "subtitle": "%s"}' "$VIDEO" "$SUBTITLE")" \
        "$ENDPOINT"
else
    echo "alass-sync: neither curl nor wget is available in this container" >&2
    exit 1
fi

echo
