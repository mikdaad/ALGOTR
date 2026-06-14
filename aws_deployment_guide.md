# AWS Deployment Guide — Zerodha Signal Engine v5 (VPA)

> **Architecture Decision Summary**
> - **Backend (Python):** EC2 `t3.small` in `ap-south-1` (Mumbai) with Docker + `systemd` (NOT Fargate — see rationale below)
> - **Frontend (Next.js):** AWS Amplify Hosting (fully managed, zero-config SSR)
> - **State/Database:** Supabase (remains external — VPA schema migration required for v5, see Part 6B)

---

## PART 1 — Why EC2 over Fargate for Your Trading Engine

| Criteria | EC2 t3.small | Fargate |
|---|---|---|
| WebSocket uptime | ✅ Persistent OS-level process | ⚠ Task may restart mid-session |
| Cold-start latency | ✅ None (always-on) | ⚠ 30–60s cold-start is unacceptable |
| Crontab scheduling | ✅ Native `cron` / `systemd` timers | ❌ Requires EventBridge + ECS task triggers |
| SSH + live debugging | ✅ Direct shell access | ❌ Exec-only, verbose |
| Cost for 6h/day usage | ✅ ~$7/month (stop instance overnight) | ~$8–10/month (always billed per-task-second) |
| Kite WebSocket keepalive | ✅ Full OS network stack | ⚠ Fargate NAT adds unpredictable jitter |

**Verdict: EC2 `t3.small` in `ap-south-1`** — it gives you direct shell access, native crontab scheduling, and deterministic network behavior that is critical for WebSocket-based HFT.

---

## PART 2 — AWS Region Selection

**Use `ap-south-1` (Mumbai). No exceptions.**

Zerodha's infrastructure is hosted in Mumbai data centers. All `kite.zerodha.com` API endpoints and WebSocket servers (`wss://ws.kite.trade`) resolve to IP addresses in Mumbai.

- `ap-south-1` → Zerodha WS: **~3–8ms RTT** (same city)
- `ap-southeast-1` (Singapore): ~50ms RTT
- `us-east-1` (N. Virginia): ~180ms RTT

Every millisecond matters for your 3-point scalp. **Always use Mumbai.**

---

## PART 3 — EC2 Instance Setup (Console + CLI)

### Step 1: Launch the EC2 Instance

**Via AWS Console:**
1. Go to EC2 → **Launch Instance**
2. **Name:** `zerodha-signal-engine`
3. **AMI:** Ubuntu Server 24.04 LTS (HVM), x86_64
4. **Instance type:** `t3.small` (2 vCPU, 2GB RAM — adequate for Python + Pandas + NumPy VPA arrays)
5. **Key pair:** Create new → Download the `.pem` file → store it safely
6. **Network Settings:** Create new Security Group (configured below)
7. **Storage:** 20 GB gp3 (increase from default 8GB for logs + Docker layers)
8. **Launch!**

**Via AWS CLI (equivalent):**
```bash
aws ec2 run-instances \
  --image-id ami-0f5ee92e2d63afc18 \
  --instance-type t3.small \
  --key-name zerodha-key \
  --security-group-ids sg-XXXXXXXXXX \
  --region ap-south-1 \
  --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":20,\"VolumeType\":\"gp3\"}}]" \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=zerodha-signal-engine}]'
```

### Step 2: Allocate & Associate an Elastic IP

This is **critical** — your Kite API token and Supabase whitelist will be tied to this static IP.

```bash
# Allocate an Elastic IP
aws ec2 allocate-address --domain vpc --region ap-south-1

# Associate it (replace with your actual IDs)
aws ec2 associate-address \
  --instance-id i-XXXXXXXXXXXXXXXXX \
  --allocation-id eipalloc-XXXXXXXXXX \
  --region ap-south-1
```

> **Note the Elastic IP address** — you'll use it to whitelist in Zerodha's Kite Developer Console under "Redirect URL" and "Postback URL" if needed.

---

## PART 4 — Security Group Configuration

### Rule Table

| Direction | Type | Protocol | Port | Source/Dest | Reason |
|---|---|---|---|---|---|
| **Inbound** | SSH | TCP | 22 | Your IP only | Admin access |
| **Outbound** | HTTPS | TCP | 443 | 0.0.0.0/0 | Kite API, Supabase, Telegram |
| **Outbound** | WSS | TCP | 443 | 0.0.0.0/0 | `wss://ws.kite.trade` |
| **Outbound** | Custom TCP | TCP | 5432 | 0.0.0.0/0 | Supabase PostgreSQL (optional direct) |
| **Outbound** | DNS | UDP | 53 | 0.0.0.0/0 | Domain resolution |

> [!IMPORTANT]
> **Never open inbound 0.0.0.0/0 on port 22.** Restrict SSH strictly to your home/office IP (e.g., `203.x.x.x/32`). This is non-negotiable for a machine holding live trading credentials.

### CLI Commands to Create the Security Group:
```bash
# Create the group
SG_ID=$(aws ec2 create-security-group \
  --group-name zerodha-engine-sg \
  --description "Zerodha Signal Engine Security Group" \
  --region ap-south-1 \
  --query 'GroupId' --output text)

echo "Security Group: $SG_ID"

# Inbound: SSH from YOUR IP only
MY_IP=$(curl -s https://checkip.amazonaws.com)
aws ec2 authorize-security-group-ingress \
  --group-id $SG_ID \
  --protocol tcp \
  --port 22 \
  --cidr "${MY_IP}/32" \
  --region ap-south-1

# Outbound: HTTPS (covers Kite API, Supabase, Telegram) — already default
# Outbound: All traffic is the AWS default; restrict if you want:
aws ec2 authorize-security-group-egress \
  --group-id $SG_ID \
  --protocol tcp \
  --port 443 \
  --cidr 0.0.0.0/0 \
  --region ap-south-1

aws ec2 authorize-security-group-egress \
  --group-id $SG_ID \
  --protocol udp \
  --port 53 \
  --cidr 0.0.0.0/0 \
  --region ap-south-1
```

---

## PART 5 — Dockerfile (Optimized for Production)

Place this file at the **root** of your project (`d:\CLUSTERS\CODES\zeroda signals\Dockerfile`):

```dockerfile
# ──────────────────────────────────────────────────────────────────────────────
# Stage 1: Builder — install deps in a layer we can cache aggressively
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

# Install build tools needed for psycopg2, pandas, and numpy
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy only requirements first — maximizes Docker layer cache hits
COPY requirements.txt .

# Install to a target directory for clean copying to final stage
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2: Runtime — lean final image
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Runtime deps only (libpq for psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Set timezone to IST so cron/logging timestamps are correct
ENV TZ=Asia/Kolkata
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Create a non-root user for security
RUN useradd --create-home --shell /bin/bash trader
USER trader
WORKDIR /app

# Copy application code (order matters: least-frequently changed first)
COPY --chown=trader:trader requirements.txt .
COPY --chown=trader:trader *.py ./
COPY --chown=trader:trader screened_watchlist.json ./

# Expose no ports (this is a pure outbound-only WebSocket client)

# Environment variable placeholders — injected via docker run --env-file
ENV KITE_API_KEY="" \
    KITE_API_SECRET="" \
    SUPABASE_URL="" \
    SUPABASE_KEY="" \
    TELEGRAM_BOT_TOKEN="" \
    TELEGRAM_CHAT_ID=""

# Default: start the live engine (override with --screener flag for pre-market)
ENTRYPOINT ["python", "-u", "main.py"]
CMD []
```

---

## PART 6 — Secrets Management with AWS Secrets Manager

**Never** bake API keys into the Docker image or a `.env` file committed to git.

### Step 1: Store your secrets
```bash
aws secretsmanager create-secret \
  --name "zerodha/prod/engine-secrets" \
  --description "Zerodha Signal Engine Runtime Secrets" \
  --secret-string '{
    "KITE_API_KEY":      "your_kite_api_key",
    "KITE_API_SECRET":  "your_kite_api_secret",
    "SUPABASE_URL":     "https://xxxx.supabase.co",
    "SUPABASE_KEY":     "your_supabase_anon_key",
    "TELEGRAM_BOT_TOKEN": "your_bot_token",
    "TELEGRAM_CHAT_ID": "your_chat_id"
  }' \
  --region ap-south-1
```

### Step 2: Create a helper script to fetch secrets at runtime
Save as `/home/ubuntu/fetch_secrets.sh` on the EC2 instance:

```bash
#!/bin/bash
# Fetches secrets from AWS Secrets Manager and exports them as env vars
SECRETS=$(aws secretsmanager get-secret-value \
  --secret-id "zerodha/prod/engine-secrets" \
  --region ap-south-1 \
  --query SecretString \
  --output text)

export KITE_API_KEY=$(echo $SECRETS | python3 -c "import sys,json; print(json.load(sys.stdin)['KITE_API_KEY'])")
export KITE_API_SECRET=$(echo $SECRETS | python3 -c "import sys,json; print(json.load(sys.stdin)['KITE_API_SECRET'])")
export SUPABASE_URL=$(echo $SECRETS | python3 -c "import sys,json; print(json.load(sys.stdin)['SUPABASE_URL'])")
export SUPABASE_KEY=$(echo $SECRETS | python3 -c "import sys,json; print(json.load(sys.stdin)['SUPABASE_KEY'])")
export TELEGRAM_BOT_TOKEN=$(echo $SECRETS | python3 -c "import sys,json; print(json.load(sys.stdin)['TELEGRAM_BOT_TOKEN'])")
export TELEGRAM_CHAT_ID=$(echo $SECRETS | python3 -c "import sys,json; print(json.load(sys.stdin)['TELEGRAM_CHAT_ID'])")
```

Grant the EC2 instance an IAM Role with `secretsmanager:GetSecretValue` on the specific secret ARN.

---

## PART 6B — Supabase Schema Migration (v5 VPA — REQUIRED)

> [!IMPORTANT]
> The v5 VPA engine writes `current_poc`, `current_vah`, `current_val`, and `vpa_signal_type` to the `trading_signals` table on every signal. **The live engine will raise a Supabase insert error on the first signal if this migration has not been run.** Complete this step before starting the live engine for the first time after deploying v5.

Run the following in **Supabase → SQL Editor** (safe to re-run — all statements use `IF NOT EXISTS`):

```sql
-- Add VPA structural level columns
ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS current_poc      NUMERIC(12, 2) DEFAULT NULL;
ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS current_vah      NUMERIC(12, 2) DEFAULT NULL;
ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS current_val      NUMERIC(12, 2) DEFAULT NULL;
ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS vpa_signal_type  TEXT           DEFAULT NULL;

-- Optional: Index for dashboard filtering by setup type
CREATE INDEX IF NOT EXISTS idx_trading_signals_vpa_type
    ON trading_signals (vpa_signal_type)
    WHERE vpa_signal_type IS NOT NULL;

-- Verify the migration:
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'trading_signals'
  AND column_name IN ('current_poc', 'current_vah', 'current_val', 'vpa_signal_type');
```

> [!NOTE]
> The `target_price` column was already present in the original schema (velocity signals used it). It is now also populated by VPA breakout signals — no schema change needed for that column.

---

## PART 7 — Docker Deployment on EC2

### SSH into the instance:
```bash
ssh -i ~/Downloads/zerodha-key.pem ubuntu@<YOUR_ELASTIC_IP>
```

### Install Docker on the instance:
```bash
# Update and install Docker
sudo apt-get update
sudo apt-get install -y docker.io awscli

# Add ubuntu user to docker group (avoids sudo for every docker command)
sudo usermod -aG docker ubuntu
newgrp docker

# Verify
docker --version
```

### Build and run the container:
```bash
# Clone/copy your project to the instance
# Option A: From GitHub
git clone https://github.com/yourusername/zeroda-signals.git /home/ubuntu/app

# Option B: Via scp from local Windows machine
# scp -i ~/zerodha-key.pem -r "d:/CLUSTERS/CODES/zeroda signals/" ubuntu@<IP>:/home/ubuntu/app/

cd /home/ubuntu/app

# Build the Docker image
docker build -t zerodha-engine:latest .

# First run: Pre-market screener
source /home/ubuntu/fetch_secrets.sh
docker run --rm \
  --name screener \
  -e KITE_API_KEY=$KITE_API_KEY \
  -e KITE_API_SECRET=$KITE_API_SECRET \
  -e SUPABASE_URL=$SUPABASE_URL \
  -e SUPABASE_KEY=$SUPABASE_KEY \
  -e TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN \
  -e TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID \
  -v /home/ubuntu/app/access_token.json:/app/access_token.json \
  -v /home/ubuntu/app/screened_watchlist.json:/app/screened_watchlist.json \
  -v /home/ubuntu/app/triggered_signals.csv:/app/triggered_signals.csv \
  zerodha-engine:latest --screener

# Live engine run
docker run -d \
  --name live-engine \
  --restart unless-stopped \
  -e KITE_API_KEY=$KITE_API_KEY \
  -e KITE_API_SECRET=$KITE_API_SECRET \
  -e SUPABASE_URL=$SUPABASE_URL \
  -e SUPABASE_KEY=$SUPABASE_KEY \
  -e TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN \
  -e TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID \
  -v /home/ubuntu/app/access_token.json:/app/access_token.json \
  -v /home/ubuntu/app/screened_watchlist.json:/app/screened_watchlist.json \
  -v /home/ubuntu/app/triggered_signals.csv:/app/triggered_signals.csv \
  zerodha-engine:latest

# Monitor logs live
docker logs -f live-engine
```

---

## PART 8 — Market Hours Automation (Crontab)

Save as `/home/ubuntu/market_scheduler.sh`:

```bash
#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
#   ZERODHA ENGINE MARKET SCHEDULER
#   Runs daily to orchestrate pre-market screening + live engine lifecycle
#   Assumes TZ=Asia/Kolkata on the EC2 host
# ──────────────────────────────────────────────────────────────────────────────

set -e
LOG="/home/ubuntu/scheduler.log"
APP_DIR="/home/ubuntu/app"
IMAGE="zerodha-engine:latest"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S IST')] $*" | tee -a "$LOG"; }

# --- Action: start-screener ---
if [ "$1" = "start-screener" ]; then
  log "📡 Starting pre-market screener..."
  source /home/ubuntu/fetch_secrets.sh

  docker rm -f screener 2>/dev/null || true
  docker run --rm \
    --name screener \
    -e KITE_API_KEY=$KITE_API_KEY \
    -e KITE_API_SECRET=$KITE_API_SECRET \
    -e SUPABASE_URL=$SUPABASE_URL \
    -e SUPABASE_KEY=$SUPABASE_KEY \
    -e TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN \
    -e TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID \
    -v $APP_DIR/access_token.json:/app/access_token.json \
    -v $APP_DIR/screened_watchlist.json:/app/screened_watchlist.json \
    $IMAGE --screener && log "✅ Screener complete."

# --- Action: start-engine ---
elif [ "$1" = "start-engine" ]; then
  log "🚀 Starting live trading engine..."
  source /home/ubuntu/fetch_secrets.sh

  # Ensure no stale container
  docker rm -f live-engine 2>/dev/null || true

  docker run -d \
    --name live-engine \
    -e KITE_API_KEY=$KITE_API_KEY \
    -e KITE_API_SECRET=$KITE_API_SECRET \
    -e SUPABASE_URL=$SUPABASE_URL \
    -e SUPABASE_KEY=$SUPABASE_KEY \
    -e TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN \
    -e TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID \
    -v $APP_DIR/access_token.json:/app/access_token.json \
    -v $APP_DIR/screened_watchlist.json:/app/screened_watchlist.json \
    -v $APP_DIR/triggered_signals.csv:/app/triggered_signals.csv \
    $IMAGE && log "✅ Live engine running. Container ID: $(docker inspect --format='{{.Id}}' live-engine | cut -c1-12)"

# --- Action: stop-engine ---
elif [ "$1" = "stop-engine" ]; then
  log "🛑 Stopping live trading engine..."
  docker stop live-engine && docker rm live-engine
  log "✅ Engine stopped."

else
  echo "Usage: $0 {start-screener|start-engine|stop-engine}"
  exit 1
fi
```

```bash
chmod +x /home/ubuntu/market_scheduler.sh
```

### Crontab Configuration

```bash
# Edit crontab
crontab -e

# ──────────────────────────────────────────────────────────────────────────────
# ZERODHA ENGINE SCHEDULE
# All times are IST (EC2 instance TZ must be Asia/Kolkata)
# ──────────────────────────────────────────────────────────────────────────────

# Verify TZ on your instance: timedatectl | grep "Time zone"
# If not IST: sudo timedatectl set-timezone Asia/Kolkata

# 8:30 AM IST — Run pre-market screener (VCP + trend scan)
30 8 * * 1-5  /home/ubuntu/market_scheduler.sh start-screener >> /home/ubuntu/cron.log 2>&1

# 9:10 AM IST — Start live engine (5 min before market open)
10 9 * * 1-5  /home/ubuntu/market_scheduler.sh start-engine >> /home/ubuntu/cron.log 2>&1

# 3:35 PM IST — Gracefully stop engine (5 min after market close)
35 15 * * 1-5  /home/ubuntu/market_scheduler.sh stop-engine >> /home/ubuntu/cron.log 2>&1

# Weekend: Optional EC2 Stop/Start via AWS CLI (only if you want to save cost)
# Note: Stopping the instance will drop the Elastic IP association if not Elastic IP
# 4:00 PM IST Friday — Stop EC2 instance to avoid idle charges
# 0 16 * * 5  aws ec2 stop-instances --instance-ids i-XXXXXXXXXXXXXXXXX --region ap-south-1
```

> [!NOTE]
> The `1-5` in the day-of-week column means Monday–Friday only. NSE public holidays are not handled automatically — you can integrate an NSE holiday API or manually check. The engine's internal `is_market_hours()` check will safely idle if called outside hours.

### Setting IST Timezone on EC2:
```bash
sudo timedatectl set-timezone Asia/Kolkata
timedatectl  # Verify: "Time zone: Asia/Kolkata (IST, +0530)"
```

---

## PART 9 — Daily Token Refresh Automation

Kite access tokens expire daily. You need a semi-automated flow:

**Option A (Manual via SSH, recommended for safety):**
```bash
# Evening before trading: Run auth.py locally
python auth.py

# SCP the token to EC2
scp -i ~/zerodha-key.pem access_token.json ubuntu@<IP>:/home/ubuntu/app/

# The mounted volume (-v flag) means the container picks it up on next run
```

**Option B (Automated via Selenium + Lambda — advanced):**
Automate the Kite login flow using a Lambda function triggered at 8:00 AM IST that writes the token to S3, then the EC2 startup script downloads it. Contact Zerodha support for their TOTP-based automation policy before implementing this.

---

## PART 10 — Next.js Frontend on AWS Amplify

### Why Amplify over S3+CloudFront?
Your Next.js app uses the **App Router with Server Components and Supabase Realtime**. Amplify Hosting provides native SSR support for Next.js 14+ with zero infrastructure management. S3+CloudFront would require Lambda@Edge for SSR, which is significantly more complex.

### Step 1: Push Your Dashboard to GitHub
```bash
cd "d:\CLUSTERS\CODES\zeroda signals\dashboard"

# Initialize git if not already done
git init
git add .
git commit -m "Initial dashboard commit"

# Create a GitHub repo and push
git remote add origin https://github.com/yourusername/zerodha-dashboard.git
git push -u origin main
```

### Step 2: Connect to AWS Amplify
1. Go to **AWS Console → Amplify → New App → Host Web App**
2. Select **GitHub** as the repository source
3. Authorize AWS Amplify to access your GitHub account
4. Select the `zerodha-dashboard` repository and `main` branch
5. Click **Next**

### Step 3: Build Configuration (`amplify.yml`)

Amplify will auto-detect Next.js, but provide this explicit config for precision. The file goes in your **dashboard root** (`d:\CLUSTERS\CODES\zeroda signals\dashboard\amplify.yml`):

```yaml
version: 1
frontend:
  phases:
    preBuild:
      commands:
        # Use Node 20 (LTS — required for Next.js 16.x)
        - nvm use 20
        # Use npm ci for deterministic, reproducible builds
        - npm ci
    build:
      commands:
        - npm run build
  artifacts:
    # For Next.js App Router with SSR, use the .next output directory
    baseDirectory: .next
    files:
      - "**/*"
  cache:
    paths:
      # Cache node_modules and Next.js build cache between deploys
      - node_modules/**/*
      - .next/cache/**/*
```

### Step 4: Configure Environment Variables in Amplify

1. In Amplify Console → Your App → **Environment Variables**
2. Click **Manage Variables** and add:

| Variable Name | Value | Secret? |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | `https://xxxx.supabase.co` | No |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | `your_anon_key` | **Yes (check "Secret")** |

> [!IMPORTANT]
> `NEXT_PUBLIC_` prefixed variables are embedded in the client bundle at build time. Mark the anon key as a secret in Amplify so it's encrypted at rest in their vault but still gets injected during build. **Do not commit `.env.local` to git** — add it to `.gitignore`.

### Step 5: Deploy & Get Your Domain
1. Click **Save and Deploy**
2. Amplify provides a free `*.amplifyapp.com` subdomain
3. For a custom domain: Amplify Console → **Domain Management** → Add your own domain (Route 53 integration is automatic if your domain is on AWS)

### Step 6: Enable Continuous Deployment
Every push to `main` branch automatically triggers a redeploy. For a production trading dashboard, protect the `main` branch in GitHub and use PR-based workflow.

---

## PART 11 — Cost Estimate

| Service | Spec | Daily Usage | Monthly Cost |
|---|---|---|---|
| EC2 `t3.small` | 2 vCPU, 2GB | 7h on / 17h off (stop instance) | ~$4–6 |
| Elastic IP | Static IP | Attached to running instance (free) | $0 |
| ECR | Docker image storage | ~500MB image | ~$0.05 |
| Amplify Hosting | SSR Next.js | First 1000 build-mins free | $0–5 |
| Secrets Manager | 1 secret | — | $0.40 |
| Data Transfer | Outbound | WebSocket ticks are inbound | ~$0.50 |
| **Total** | | | **~$5–12/month** |

> [!TIP]
> **Stop the EC2 instance on weekends** using the crontab Friday schedule. This alone cuts compute cost by ~28%. You are NOT charged for EC2 while stopped — only for the EBS volume and Elastic IP.

---

## PART 12 — Production Monitoring

### CloudWatch Log Group for the Engine:

```bash
# Install CloudWatch agent on EC2
sudo apt-get install -y amazon-cloudwatch-agent

# Configure it to stream docker logs
cat > /opt/aws/amazon-cloudwatch-agent/etc/log-config.json << 'EOF'
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/home/ubuntu/cron.log",
            "log_group_name": "/zerodha/engine/cron",
            "log_stream_name": "{instance_id}"
          },
          {
            "file_path": "/home/ubuntu/scheduler.log",
            "log_group_name": "/zerodha/engine/scheduler",
            "log_stream_name": "{instance_id}"
          }
        ]
      }
    }
  }
}
EOF

sudo amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/log-config.json -s
```

### Set a CloudWatch Alarm to alert if engine crashes:
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "ZerodhaEngineDown" \
  --alarm-description "Engine container stopped unexpectedly during market hours" \
  --metric-name "MemoryUsage" \
  --namespace "CWAgent" \
  --statistic Average \
  --period 60 \
  --threshold 90 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 3 \
  --alarm-actions arn:aws:sns:ap-south-1:XXXX:zerodha-alerts \
  --region ap-south-1
```

---

## PART 13 — Complete Deployment Checklist

```
PRE-DEPLOYMENT
  [ ] AWS account active, billing alerts set ($20 threshold)
  [ ] ap-south-1 region selected in console
  [ ] EC2 key pair downloaded and stored safely
  [ ] Elastic IP allocated and associated

SECRETS
  [ ] All secrets stored in Secrets Manager (NOT in code)
  [ ] EC2 IAM Role has secretsmanager:GetSecretValue permission
  [ ] fetch_secrets.sh tested manually

DATABASE MIGRATION (v5 VPA — REQUIRED BEFORE FIRST LIVE RUN)
  [ ] Supabase SQL Editor: 4 ALTER TABLE statements run (Part 6B)
  [ ] Verification SELECT confirms all 4 VPA columns exist
  [ ] target_price column confirmed present (pre-existing)

DOCKER
  [ ] numpy>=1.26.0 confirmed in requirements.txt
  [ ] Dockerfile added to project root
  [ ] Image builds successfully: docker build -t zerodha-engine:latest .
  [ ] Container runs successfully with test env vars

SCHEDULER
  [ ] EC2 TZ set to Asia/Kolkata
  [ ] market_scheduler.sh added and chmod +x
  [ ] Crontab entries configured and verified with: crontab -l

FRONTEND
  [ ] dashboard/amplify.yml committed to git
  [ ] .env.local added to .gitignore
  [ ] Repository connected to AWS Amplify
  [ ] Environment variables set in Amplify Console
  [ ] First build successful

SECURITY
  [ ] Security Group: SSH locked to your specific IP only
  [ ] No credentials in any committed file
  [ ] access_token.json in .gitignore
```
