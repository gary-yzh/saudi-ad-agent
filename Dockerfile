# Single-stage image — image footprint isn't the bottleneck, build time is.
# Base: python:3.11-slim (~50 MB) + ffmpeg (~50 MB once we add Sprint 3 #19
# cut-down rendering) + our deps (~250 MB) → final image ~350 MB. Acceptable
# for a Fly.io free-tier deploy.
FROM python:3.11-slim

# ffmpeg is here ahead of need: Sprint 3 #19 (Cut-down auto-generation) will
# call it, and adding it now means later code changes are single-purpose
# commits rather than "feature + change Dockerfile".
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first — Docker caches this layer until requirements.txt
# changes, so iterating on code rebuilds in seconds rather than minutes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY . .

# Runtime directories. On Fly.io we mount a Volume at /app/data so the
# SQLite database + uploaded brand manuals persist across deploys.
# /app/outputs/runs holds generated images / videos / TTS — those can
# regenerate from Doubao so we don't bother persisting them.
RUN mkdir -p /app/data /app/outputs/runs /app/outputs/logos

# server.py listens on 8000. Fly.io expects an internal_port matching
# this (see fly.toml).
EXPOSE 8000

# Production note: uvicorn workers > 1 would help latency under load, but
# we only run a single Fly.io machine in the free tier so 1 worker is
# correct. When we scale out (multi-machine) we also need to move SQLite
# to PostgreSQL — tracked as Sprint 2 #9.
CMD ["python", "server.py"]
