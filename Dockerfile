# ──────────────────────────────────────────────────────────────────────────────
# Stage 1: Builder — install deps in a layer we can cache aggressively
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Install build tools needed for psycopg2 and pandas native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy only requirements first — maximizes Docker layer cache hits on re-builds
COPY requirements.txt .

# Install all packages to /install prefix (clean separation from runtime image)
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2: Runtime — lean final image (~200MB vs ~800MB single-stage)
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL maintainer="trading-engine" \
    version="4.0" \
    description="Zerodha Signal Engine — Live Breakout + 3-Point Velocity Scanner"

# Runtime libs only (libpq5 for psycopg2, tzdata for IST timezone)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Set timezone to IST — critical for all cron, market-hours checks, logging
ENV TZ=Asia/Kolkata
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Copy installed Python packages from builder stage
COPY --from=builder /install /usr/local

# Create a non-root user (security best practice: never run trading code as root)
RUN useradd --create-home --shell /bin/bash --uid 1001 trader
USER trader
WORKDIR /app

# Copy application source (order: least to most frequently changed)
COPY --chown=trader:trader requirements.txt .
COPY --chown=trader:trader config.py auth.py alerts.py main.py ./
COPY --chown=trader:trader live_engine.py screener.py strategy.py ./
COPY --chown=trader:trader data_fetcher.py db_logger.py velocity_scanner.py ./
COPY --chown=trader:trader supabase_bridge.py supabase_listener.py ./
COPY --chown=trader:trader order_executor.py hitl_bot.py ./

# These files are mounted as Docker volumes at runtime (not baked in):
# - access_token.json  (rotates daily)
# - screened_watchlist.json  (updated by screener run)
# - triggered_signals.csv  (log output, needs persistence)

# Environment variable placeholders — ALL injected at runtime via --env-file
# or AWS Secrets Manager. Nothing is hardcoded here.
ENV KITE_API_KEY="" \
    KITE_API_SECRET="" \
    SUPABASE_URL="" \
    SUPABASE_KEY="" \
    TELEGRAM_BOT_TOKEN="" \
    TELEGRAM_CHAT_ID="" \
    HITL_ENABLED="False" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# PYTHONUNBUFFERED=1 ensures docker logs -f shows output immediately (no buffer)

# This is a pure outbound-only service — no ports needed
# All comms: HTTPS/WSS to Kite, Supabase, Telegram

# Default: start the live engine
# Override CMD at docker run time: docker run ... zerodha-engine --screener
ENTRYPOINT ["python", "-u", "main.py"]
CMD []
