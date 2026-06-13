#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
#   ZERODHA ENGINE MARKET SCHEDULER  (market_scheduler.sh)
#   Manages the Docker container lifecycle aligned to NSE trading hours.
#
#   Usage:
#     ./market_scheduler.sh start-screener   # 8:30 AM IST — pre-market VCP scan
#     ./market_scheduler.sh start-engine     # 9:10 AM IST — live trading engine
#     ./market_scheduler.sh stop-engine      # 3:35 PM IST — post-market shutdown
#
#   Crontab (on EC2 with TZ=Asia/Kolkata, weekdays only):
#     30 8 * * 1-5  /home/ubuntu/market_scheduler.sh start-screener >> /home/ubuntu/cron.log 2>&1
#     10 9 * * 1-5  /home/ubuntu/market_scheduler.sh start-engine   >> /home/ubuntu/cron.log 2>&1
#     35 15 * * 1-5  /home/ubuntu/market_scheduler.sh stop-engine   >> /home/ubuntu/cron.log 2>&1
#
#   Prerequisites:
#     - Docker installed and running
#     - AWS CLI installed and configured (for Secrets Manager)
#     - EC2 IAM Role with secretsmanager:GetSecretValue permission
#     - Image built: docker build -t zerodha-engine:latest /home/ubuntu/app/
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
readonly APP_DIR="/home/ubuntu/app"
readonly IMAGE="zerodha-engine:latest"
readonly SECRET_NAME="zerodha/prod/engine-secrets"
readonly AWS_REGION="ap-south-1"
readonly LOG_FILE="/home/ubuntu/scheduler.log"

# ── Logging helper ─────────────────────────────────────────────────────────────
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S IST')] $*" | tee -a "$LOG_FILE"
}

# ── Secrets fetcher (AWS Secrets Manager → env vars) ──────────────────────────
load_secrets() {
    log "🔐 Fetching secrets from AWS Secrets Manager..."
    local secrets
    secrets=$(aws secretsmanager get-secret-value \
        --secret-id "$SECRET_NAME" \
        --region "$AWS_REGION" \
        --query SecretString \
        --output text)

    # Parse JSON and export each key as an environment variable
    export KITE_API_KEY=$(echo "$secrets" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['KITE_API_KEY'])")
    export KITE_API_SECRET=$(echo "$secrets" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['KITE_API_SECRET'])")
    export SUPABASE_URL=$(echo "$secrets" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['SUPABASE_URL'])")
    export SUPABASE_KEY=$(echo "$secrets" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['SUPABASE_KEY'])")
    export TELEGRAM_BOT_TOKEN=$(echo "$secrets" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['TELEGRAM_BOT_TOKEN'])")
    export TELEGRAM_CHAT_ID=$(echo "$secrets" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['TELEGRAM_CHAT_ID'])")

    log "✅ Secrets loaded."
}

# ── Common docker run args ─────────────────────────────────────────────────────
docker_env_args() {
    echo "\
        -e KITE_API_KEY=$KITE_API_KEY \
        -e KITE_API_SECRET=$KITE_API_SECRET \
        -e SUPABASE_URL=$SUPABASE_URL \
        -e SUPABASE_KEY=$SUPABASE_KEY \
        -e TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN \
        -e TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID"
}

# ── ACTION: start-screener ─────────────────────────────────────────────────────
start_screener() {
    log "📡 === PRE-MARKET SCREENER STARTING ==="
    load_secrets

    # Remove stale screener container if it exists
    docker rm -f screener 2>/dev/null || true

    log "Running VCP + trend-aligned screener..."
    docker run --rm \
        --name screener \
        -e KITE_API_KEY="$KITE_API_KEY" \
        -e KITE_API_SECRET="$KITE_API_SECRET" \
        -e SUPABASE_URL="$SUPABASE_URL" \
        -e SUPABASE_KEY="$SUPABASE_KEY" \
        -e TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
        -e TELEGRAM_CHAT_ID="$TELEGRAM_CHAT_ID" \
        -v "$APP_DIR/access_token.json:/app/access_token.json:ro" \
        -v "$APP_DIR/screened_watchlist.json:/app/screened_watchlist.json" \
        "$IMAGE" --screener

    log "✅ Screener complete. Watchlist updated: $APP_DIR/screened_watchlist.json"
}

# ── ACTION: start-engine ───────────────────────────────────────────────────────
start_engine() {
    log "🚀 === LIVE ENGINE STARTING ==="
    load_secrets

    # Kill any stale engine container
    if docker inspect live-engine &>/dev/null; then
        log "⚠  Stale live-engine container found. Removing..."
        docker stop live-engine && docker rm live-engine
    fi

    docker run -d \
        --name live-engine \
        --restart unless-stopped \
        -e KITE_API_KEY="$KITE_API_KEY" \
        -e KITE_API_SECRET="$KITE_API_SECRET" \
        -e SUPABASE_URL="$SUPABASE_URL" \
        -e SUPABASE_KEY="$SUPABASE_KEY" \
        -e TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
        -e TELEGRAM_CHAT_ID="$TELEGRAM_CHAT_ID" \
        -v "$APP_DIR/access_token.json:/app/access_token.json:ro" \
        -v "$APP_DIR/screened_watchlist.json:/app/screened_watchlist.json:ro" \
        -v "$APP_DIR/triggered_signals.csv:/app/triggered_signals.csv" \
        "$IMAGE"

    local container_id
    container_id=$(docker inspect --format='{{.Id}}' live-engine | cut -c1-12)
    log "✅ Live engine running. Container: $container_id"
    log "   Monitor with: docker logs -f live-engine"
}

# ── ACTION: stop-engine ────────────────────────────────────────────────────────
stop_engine() {
    log "🛑 === ENGINE SHUTDOWN INITIATED ==="

    if ! docker inspect live-engine &>/dev/null; then
        log "⚠  No live-engine container found. Already stopped?"
        exit 0
    fi

    # Graceful stop (SIGTERM → 30s → SIGKILL)
    docker stop --time 30 live-engine && docker rm live-engine
    log "✅ Engine stopped cleanly."

    # Archive today's signals CSV with timestamp
    local today
    today=$(date '+%Y-%m-%d')
    if [ -f "$APP_DIR/triggered_signals.csv" ]; then
        cp "$APP_DIR/triggered_signals.csv" "$APP_DIR/signals_archive_${today}.csv"
        log "📁 Signals archived to: signals_archive_${today}.csv"
    fi
}

# ── MAIN dispatch ──────────────────────────────────────────────────────────────
ACTION="${1:-}"

case "$ACTION" in
    start-screener)  start_screener ;;
    start-engine)    start_engine ;;
    stop-engine)     stop_engine ;;
    *)
        echo "Usage: $0 {start-screener|start-engine|stop-engine}"
        echo ""
        echo "  start-screener  Run Phase 1 VCP screener (pre-market, ~8:30 AM IST)"
        echo "  start-engine    Start live breakout + velocity engine (~9:10 AM IST)"
        echo "  stop-engine     Gracefully stop engine and archive today's signals (3:35 PM IST)"
        exit 1
        ;;
esac
