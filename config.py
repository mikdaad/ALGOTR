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
TELEGRAM_BOT_TOKEN = "your_telegram_bot_token_here"
TELEGRAM_CHAT_ID = "your_telegram_chat_id_here"

# ──────────────────────────────────────────────────────────────────────────────
# SUPABASE / POSTGRESQL LOGGING (Optional)
# ──────────────────────────────────────────────────────────────────────────────
ENABLE_DB_LOGGING = False
SUPABASE_DB_URL = "postgresql://postgres.wmflxdqnbqchpwdarslk:Mik74738W!93@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"

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
# MARKET HOURS (IST)
# ──────────────────────────────────────────────────────────────────────────────
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30
