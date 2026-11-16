# syntax=docker/dockerfile:1
# reelline: the engine, Remotion with its browser, ffmpeg, fonts and Rhubarb in one image. A channel is not in
# it: mount a channel folder (config.yaml, prompts, sources, assets and its .env) and name it with
# REELLINE_CHANNEL (compose.yaml does). `docker run --rm -v ./my-channel:/channel -e REELLINE_CHANNEL=/channel
# reelline doctor`.
#
# Layers go from the slowest-changing to the fastest: system packages, Rhubarb, Python requirements, Remotion's
# npm packages and its browser, then the engine itself.
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

# ffmpeg; the fonts Chrome falls back to; the libraries Remotion's headless Chrome needs; curl and unzip for Rhubarb.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg ca-certificates curl unzip git \
        fonts-dejavu-core fonts-liberation fonts-noto-core fonts-noto-color-emoji \
        libnss3 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libgbm1 libasound2 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libxkbcommon0 libpango-1.0-0 libcairo2 libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# Node 24 (what CI builds Remotion with), copied whole from the official image.
COPY --from=node:24-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:24-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Rhubarb Lip Sync (engine/tools/README.md: the version and the checksum).
RUN curl -fsSL -o /tmp/rhubarb.zip \
        https://github.com/DanielSWolf/rhubarb-lip-sync/releases/download/v1.14.0/Rhubarb-Lip-Sync-1.14.0-Linux.zip \
    && echo "a9a9074862cff47b2d59b8bf399a678a3b0b74f9452ad6ad94cb292913dd8667  /tmp/rhubarb.zip" | sha256sum -c - \
    && unzip -q /tmp/rhubarb.zip -d /tmp/rhubarb \
    && mkdir -p /app/engine/tools/rhubarb \
    && cp -r /tmp/rhubarb/Rhubarb-Lip-Sync-1.14.0-Linux/. /app/engine/tools/rhubarb/ \
    && chmod +x /app/engine/tools/rhubarb/rhubarb \
    && rm -rf /tmp/rhubarb /tmp/rhubarb.zip

WORKDIR /app

COPY engine/requirements.txt engine/requirements.txt
RUN pip install -r engine/requirements.txt

# Remotion: the npm packages, then its headless Chrome (downloaded once, into node_modules).
COPY engine/remotion/package.json engine/remotion/package-lock.json engine/remotion/
RUN cd engine/remotion && npm ci --no-audit --no-fund && npx remotion browser ensure

COPY engine engine
RUN pip install --no-deps -e engine

ENV REELLINE_CHANNEL=/channel
VOLUME ["/channel", "/app/out"]
ENTRYPOINT ["reelline"]
CMD ["tick"]
