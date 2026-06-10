# ══════════════════════════════════════════════════════════════════════════════
#  POLYMARKET — by Genoshide | polymarket arbitrage script bot
#  Dockerfile — production-ready container image
#
#  Build:  docker build -t polymarket-bot .
#  Run:    docker compose up          (paper mode, detached)
#          docker compose run bot     (interactive with dashboard)
# ══════════════════════════════════════════════════════════════════════════════

# ── Stage 1: dependency builder ────────────────────────────────────────────────
# Uses the full slim image to compile C extensions (web3/cryptography/coincurve).
# Result is copied to the final stage so build tools don't land in production.
FROM python:3.11-slim AS builder

WORKDIR /build

# System packages needed to compile C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libssl-dev \
        libffi-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip once, then install all deps into a separate prefix so we can
# copy only the packages (not the entire python installation) to the final stage.
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel \
 && pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# tini: minimal init process — forwards signals (SIGTERM/SIGINT) correctly
# to the Python process so `docker stop` triggers a clean shutdown.
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled packages from builder stage
COPY --from=builder /install /usr/local

# ── Non-root security user ────────────────────────────────────────────────────
RUN groupadd --gid 1001 botgroup \
 && useradd  --uid 1001 --gid botgroup --shell /bin/bash --create-home botuser

WORKDIR /app

# ── Copy source code ──────────────────────────────────────────────────────────
# .dockerignore excludes: venv/, .env, __pycache__, *.log, .git, tests/
COPY --chown=botuser:botgroup . .

# Create log directory with correct ownership
RUN mkdir -p /app/logs && chown botuser:botgroup /app/logs

USER botuser

# ── Runtime configuration ─────────────────────────────────────────────────────
# Disable Python output buffering so logs appear immediately in `docker logs`
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Write logs to /app/logs/ (mounted as a volume in docker-compose)
    LOG_FILE=/app/logs/polymarket_bot.log \
    # Disable Rich's colour codes when running without a TTY (detached mode)
    # Set to "0" to force disable; unset to let Rich auto-detect.
    TERM=xterm-256color

# ── Entry point ───────────────────────────────────────────────────────────────
# tini -g: forward signals to the whole process group
# Default CMD is empty — PAPER_MODE is read from .env (injected at runtime)
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "python", "main.py"]
CMD []
