"""
==================================================================================
  CONFIGURATION — Zerodha Signal Engine v3 (VCP + Weighted OBI)
==================================================================================
  Two operating modes:
    Mode 1: Pre-Market VCP & Trend-Aligned Screener (after hours)
    Mode 2: Live Breakout + Tier-Weighted OBI Engine (9:15 AM – 3:30 PM)
==================================================================================
"""

# ──────────────────────────────────────────────────────────────────────────────
# KITE CONNECT CREDENTIALS
# ──────────────────────────────────────────────────────────────────────────────
KITE_API_KEY = "fpgs36ybkczszzti"
KITE_API_SECRET = "gn51hjxm3gi9jn81t7c0o6hvcd5t8ofk"
KITE_REDIRECT_URL = "http://127.0.0.1"

# ──────────────────────────────────────────────────────────────────────────────
# ACCESS TOKEN PERSISTENCE
# ──────────────────────────────────────────────────────────────────────────────
ACCESS_TOKEN_FILE = "access_token.json"

# ──────────────────────────────────────────────────────────────────────────────
# TELEGRAM ALERT CREDENTIALS
# ──────────────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = "7206254977:AAGvXrpqfgrHtsVPxp1lNlGzv94f52_xqKA"
TELEGRAM_CHAT_ID = "1832175468"

# ──────────────────────────────────────────────────────────────────────────────
# SUPABASE / POSTGRESQL LOGGING (Optional)
# ──────────────────────────────────────────────────────────────────────────────
ENABLE_DB_LOGGING = False
SUPABASE_DB_URL = "postgresql://postgres.wmflxdqnbqchpwdarslk:Mik74738W!93@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"

# Supabase REST API credentials (for supabase-py client + dashboard bridge)
# Find these in: Supabase Dashboard → Settings → API
SUPABASE_URL = "https://wmflxdqnbqchpwdarslk.supabase.co"
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndtZmx4ZHFuYnFjaHB3ZGFyc2xrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI1MjkzODMsImV4cCI6MjA4ODEwNTM4M30.nTv1JYsJKS7dt7HLm5pOmgr0F32Sfc9S1k3U4-k91nc"  # ← Replace with your service_role key

# ──────────────────────────────────────────────────────────────────────────────
# PHASE 1: PRE-MARKET SCREENER PARAMETERS
# ──────────────────────────────────────────────────────────────────────────────

# SCREENER_MODE controls how the entry universe is built:
#   "CURATED"  — Use the SCREENER_UNIVERSE list below (~150 stocks, fast, ~1 min)
#   "ALL_EQ"   — Scan every EQ instrument on NSE (~2000 stocks, slow, ~15 min)
SCREENER_MODE = "CURATED"

# Liquidity filters — applied using FETCHED historical data, not the instrument dump
MIN_CLOSE_PRICE = 50              # Previous day close must be ≥ ₹50
MIN_DAILY_VOLUME = 500_000        # Previous day volume must be ≥ 500K shares

# Screener interval for ATR/consolidation analysis
SCREENER_INTERVAL = "60minute"    # 1-hour candles for the screener
SCREENER_LOOKBACK_DAYS = 5        # Fetch 5 days of hourly candles

# ── VCP (Volatility Contraction Pattern) Parameters ──
# Instead of checking sequential daily ATR drops (which produces 0.0% on
# short lookbacks), we compare short-term ATR vs long-term ATR.
# Compression = 1 - (ATR_short / ATR_long). A value ≥ 0.20 means the
# 3-day range is at least 20% tighter than the 10-day range → coiling.
VCP_ATR_SHORT = 3                 # Short-term ATR period (recent volatility)
VCP_ATR_LONG = 10                 # Long-term ATR period (baseline volatility)
VCP_COMPRESSION_THRESHOLD = 0.20  # Minimum compression ratio to flag as VCP

# ── Higher Timeframe Trend Filter ──
# BULL_ZONE setups are only valid if price is ABOVE the daily EMA.
# This ensures breakouts align with the structural daily uptrend.
TREND_EMA_PERIOD = 20             # 20-day EMA for trend alignment
TREND_LOOKBACK_DAYS = 30          # Fetch 30 days of daily data for EMA calc

# Near-S/R proximity: close within this % of the 5-day high/low
SR_PROXIMITY_PCT = 1.0            # 1% proximity threshold

# How many top screened stocks to output
TOP_SCREENER_RESULTS = 15

# Rate-limit delay between historical API calls (seconds)
# Kite allows ~3 HTTP requests/sec for historical data
RATE_LIMIT_DELAY = 0.35

# ──────────────────────────────────────────────────────────────────────────────
# CURATED SCREENER UNIVERSE
# ──────────────────────────────────────────────────────────────────────────────
# ~150 top liquid NSE equities (Nifty 100 + select Nifty Next 50 / Nifty 200).
# Instrument tokens are resolved automatically from kite.instruments("NSE")
# at runtime — you only need to keep the trading symbols updated.
#
# Add or remove symbols freely. Symbols must match the Kite tradingsymbol
# exactly (e.g., "M&M" not "M_AND_M", "BAJAJ-AUTO" not "BAJAJ AUTO").
# ──────────────────────────────────────────────────────────────────────────────
SCREENER_UNIVERSE = [
    # ── Nifty 50 ──
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BHARTIARTL", "BPCL",
    "BRITANNIA", "CIPLA", "COALINDIA", "DIVISLAB", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK",
    "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK",
    "LT", "M&M", "MARUTI", "NESTLEIND", "NTPC",
    "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
    "SHRIRAMFIN", "SUNPHARMA", "TATAMOTORS", "TATAPOWER", "TATASTEEL",
    "TCS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
    # ── Nifty Next 50 / Nifty 100 ──
    "ABB", "ACC", "AMBUJACEM", "ATGL", "AUROPHARMA",
    "BANKBARODA", "BEL", "BHEL", "BOSCHLTD", "CANBK",
    "CHOLAFIN", "COLPAL", "CONCOR", "DABUR", "DLF",
    "GAIL", "GODREJCP", "HAL", "HAVELLS", "HINDPETRO",
    "ICICIGI", "ICICIPRULI", "INDHOTEL", "INDUSTOWER", "IOC",
    "IRCTC", "IRFC", "JINDALSTEL", "JSWENERGY", "LICI",
    "LTIM", "LUPIN", "MARICO", "MOTHERSON", "MUTHOOTFIN",
    "NHPC", "NMDC", "PAGEIND", "PEL", "PERSISTENT",
    "PIDILITIND", "PNB", "POLYCAB", "PFC", "RECLTD",
    "SAIL", "SBICARD", "SHREECEM", "SIEMENS", "SRF",
    "TATACOMM", "TATAELXSI", "TORNTPHARM", "TVSMOTOR", "UNIONBANK",
    "UNITDSPR", "VEDL", "ZOMATO", "ZYDUSLIFE",
    # ── Additional Large/Midcap Liquid ──
    "BALKRISIND", "BANDHANBNK", "BERGEPAINT", "BIOCON", "CANFINHOME",
    "CGPOWER", "COFORGE", "CROMPTON", "CUMMINSIND", "DEEPAKNTR",
    "DELHIVERY", "DIXON", "ESCORTS", "EXIDEIND", "FEDERALBNK",
    "FORTIS", "GMRAIRPORT", "GODREJPROP", "IDFCFIRSTB", "IEX",
    "INDIAMART", "IREDA", "KAJARIACER", "KEI", "LAURUSLABS",
    "LICHSGFIN", "LTTS", "M&MFIN", "MFSL", "NAUKRI",
    "OBEROIRLTY", "PAYTM", "PETRONET", "PIIND", "POLICYBZR",
    "PRESTIGE", "PVRINOX", "RAMCOCEM", "RBLBANK", "SJVN",
    "SONACOMS", "SUNTV", "SUPREMEIND", "SYNGENE", "TATACHEM",
    "TIINDIA", "VOLTAS", "YESBANK",
]

# ──────────────────────────────────────────────────────────────────────────────
# PHASE 2: LIVE BREAKOUT + TIER-WEIGHTED OBI ENGINE PARAMETERS
# ──────────────────────────────────────────────────────────────────────────────

# Opening range: first N minutes of market to establish the breakout level
OPENING_RANGE_MINUTES = 15        # First 15 minutes (9:15–9:30)

# Live candle aggregation interval (for breakout detection)
LIVE_CANDLE_MINUTES = 5           # 5-minute candles built from ticks

# ── Tier-Weighted Order Book Imbalance (OBI) ──
# Kite streams 5 depth levels per side. Weighting inner tiers higher
# prevents whales from spoofing deep outer layers to trick the scanner.
#   Tier 1 (Best Bid/Ask):  40% weight
#   Tier 2-3:               40% weight (20% each)
#   Tier 4-5:               20% weight (10% each)
OBI_TIER_WEIGHTS = [0.40, 0.20, 0.20, 0.10, 0.10]
OBI_BULL_THRESHOLD = 0.60         # Trigger BUY only if weighted OBI ≥ 0.60
OBI_BEAR_THRESHOLD = -0.60        # Trigger SELL only if weighted OBI ≤ -0.60

# ──────────────────────────────────────────────────────────────────────────────
# PHASE 4: VELOCITY SCANNER — 3-POINT SCALP DETECTOR
# ──────────────────────────────────────────────────────────────────────────────
# Ultra-low-latency scanner for rapid ±3 point intraday price expansions.
# Runs on 1-minute micro-candles inside the worker thread alongside Phase 2.

# ── Dynamic Universe Filter ──
# Only monitor stocks in the ₹150–₹500 band where a 3-pt move is
# a meaningful 0.6%–2.0% expansion — high probability, high velocity.
VELOCITY_PRICE_MIN = 150.0
VELOCITY_PRICE_MAX = 500.0

# ── ATR Feasibility Gate ──
# 1-minute ATR over the last 20 bars must be ≥ 0.50 points.
# This proves the stock has enough inherent velocity to reach
# the 3-point target without getting trapped in sideways chop.
VELOCITY_ATR_PERIOD = 20          # Number of 1-min bars for ATR calculation
VELOCITY_ATR_MIN = 0.50           # Minimum 1-min ATR to qualify (points)

# ── Micro-Consolidation Breakout ──
# Track the high/low envelope of the last N completed 1-min bars.
# Trigger fires when LTP pierces this micro-range.
VELOCITY_CONSOLIDATION_BARS = 3   # Trailing bar count for H/L envelope

# ── Volume Surge Confirmation ──
# Current 1-min bar volume must exceed N× the average of the last M bars.
VELOCITY_VOLUME_LOOKBACK = 10     # Bars to average for baseline volume
VELOCITY_VOLUME_MULTIPLIER = 2.0  # Required surge multiple (2× avg)

# ── WOBI Thresholds (stricter than Phase 2) ──
# Anti-spoofing weighted order book imbalance must confirm aggressive
# participation from the correct side before triggering.
VELOCITY_WOBI_BULL = 0.70         # Buy trigger: WOBI ≥ +0.70
VELOCITY_WOBI_BEAR = -0.70        # Sell trigger: WOBI ≤ -0.70

# ── Scalp Target & Risk ──
# Fixed 1:2 Risk-to-Reward ratio for crisp scalping.
VELOCITY_SCALP_TARGET = 3.00      # Target move in points (₹3.00)
VELOCITY_STOP_LOSS = 1.50         # Stop-loss distance in points (₹1.50)

# ──────────────────────────────────────────────────────────────────────────────
# FALLBACK WATCHLIST (used if screener hasn't run yet)
# ──────────────────────────────────────────────────────────────────────────────
FALLBACK_WATCHLIST = [
    {"symbol": "RELIANCE",   "token": 738561},
    {"symbol": "TCS",        "token": 2953217},
    {"symbol": "INFY",       "token": 408065},
    {"symbol": "HDFCBANK",   "token": 341249},
    {"symbol": "ICICIBANK",  "token": 1270529},
    {"symbol": "SBIN",       "token": 779521},
    {"symbol": "BAJFINANCE", "token": 81153},
    {"symbol": "ITC",        "token": 424961},
    {"symbol": "TATAMOTORS", "token": 884737},
    {"symbol": "HINDUNILVR", "token": 356865},
]

# File where Phase 1 saves the screened watchlist for Phase 2
SCREENED_WATCHLIST_FILE = "screened_watchlist.json"

# ──────────────────────────────────────────────────────────────────────────────
# PHASE 5: HUMAN-IN-THE-LOOP (HITL) EXECUTION GATEWAY
# ──────────────────────────────────────────────────────────────────────────────
# When enabled, velocity signals are sent as interactive Telegram messages
# with APPROVE/REJECT buttons. On approval, the user types QTY, SL, TARGET
# and the engine executes via kite.place_order().
# Breakout signals remain alert-only regardless of this setting.

HITL_ENABLED = False              # Master kill-switch (set True to enable live orders)
HITL_EXPIRY_SECONDS = 120         # Signal expires if not approved within 2 min
HITL_PRODUCT_TYPE = "MIS"         # "MIS" (intraday) or "CNC" (delivery)
HITL_ORDER_TYPE = "LIMIT"         # "LIMIT" or "MARKET"

# ──────────────────────────────────────────────────────────────────────────────
# MARKET HOURS (IST)
# ──────────────────────────────────────────────────────────────────────────────
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30
