# syntax=docker/dockerfile:1

# alass-sync: FastAPI sidecar + alass, for amd64 and arm64.
#
# Upstream ships a prebuilt binary for linux/amd64 only (alass-linux64), so
# arm64 is compiled from the Rust source in a builder stage. BuildKit only
# builds the stage that the target architecture actually selects.

ARG ALASS_VERSION=v2.0.0
# Static ffmpeg/ffprobe builds (LGPL is enough - alass only decodes audio).
ARG FFMPEG_RELEASE=n8.1-latest
ARG TARGETARCH

# --------------------------------------------------------------------------- #
# amd64: grab the official prebuilt release binary
# --------------------------------------------------------------------------- #
FROM debian:bookworm-slim AS alass-amd64
ARG ALASS_VERSION
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL --retry 3 \
        -o /alass \
        "https://github.com/kaegi/alass/releases/download/${ALASS_VERSION}/alass-linux64" \
    && chmod +x /alass \
    && /alass --version

# --------------------------------------------------------------------------- #
# arm64: build alass from source
# --------------------------------------------------------------------------- #
FROM rust:1-bookworm AS alass-arm64
ARG ALASS_VERSION
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${ALASS_VERSION}" https://github.com/kaegi/alass /src
WORKDIR /src
RUN cargo build --release \
    && install -m 0755 "$(ls target/release/alass-cli target/release/alass 2>/dev/null | head -n1)" /alass \
    && /alass --version

# --------------------------------------------------------------------------- #
# pick the stage matching the build target
# --------------------------------------------------------------------------- #
FROM alass-${TARGETARCH} AS alass

# --------------------------------------------------------------------------- #
# static ffmpeg + ffprobe (Debian's ffmpeg package drags in ~600 MB of deps)
# --------------------------------------------------------------------------- #
FROM debian:bookworm-slim AS ffmpeg
ARG FFMPEG_RELEASE
ARG TARGETARCH
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*
RUN case "${TARGETARCH}" in \
        amd64) FF_ARCH=linux64 ;; \
        arm64) FF_ARCH=linuxarm64 ;; \
        *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && VERSION="${FFMPEG_RELEASE%%-*}" \
    && URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-${FFMPEG_RELEASE}-${FF_ARCH}-lgpl-${VERSION#n}.tar.xz" \
    && curl -fsSL --retry 3 -o /tmp/ffmpeg.tar.xz "$URL" \
    && mkdir -p /tmp/ff && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp/ff --strip-components=1 \
    && install -m 0755 /tmp/ff/bin/ffmpeg /tmp/ff/bin/ffprobe /usr/local/bin/ \
    && ffmpeg -version | head -n1 && ffprobe -version | head -n1

# --------------------------------------------------------------------------- #
# runtime
# --------------------------------------------------------------------------- #
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/sitolam/alass-sync" \
      org.opencontainers.image.description="Subtitle re-sync sidecar for Bazarr, powered by alass" \
      org.opencontainers.image.licenses="MIT"

# alass shells out to ffmpeg/ffprobe to extract the audio track from the video.
COPY --from=ffmpeg /usr/local/bin/ffmpeg /usr/local/bin/ffprobe /usr/local/bin/
COPY --from=alass /alass /usr/local/bin/alass

WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MEDIA_ROOT=/data \
    ALASS_BIN=alass \
    ALASS_TIMEOUT=120 \
    MAX_CONCURRENCY=2 \
    LOG_LEVEL=INFO \
    PORT=8765

EXPOSE 8765

# No curl in this image on purpose - the healthcheck uses the stdlib.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
