"""
==================================================================================
  PHASE 1 — Pre-Market VCP & Trend-Aligned Screener (screener.py) [v3]
==================================================================================
  Runs after hours to build tomorrow's high-probability watchlist.

  v3 UPGRADES:
    - VCP Detection: Compression ratio = 1 - (ATR_3d / ATR_10d).
      Flags stocks where 3-day ATR is ≥20% below 10-day ATR (coiling spring).
    - Multi-Timeframe Trend Filter: BULL_ZONE setups must be above the
      20-day EMA. Breakouts against the daily trend are penalized/dropped.
    - Watchlist Handoff: saves {symbol, token} JSON consumed by live engine.

  Pipeline:
    1. Resolve instrument tokens from curated universe
    2. Verify liquidity via previous day's historical daily candle
    3. Fetch 5-day hourly candles → compute VCP compression + near S/R
    4. Fetch 30-day daily candles → compute 20-EMA trend filter
    5. Rank Top 15, save watchlist JSON for Phase 2

  USAGE:
    python screener.py                      # standalone
    python main.py --screener               # via orchestrator
==================================================================================
"""

import json
import time
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

import pandas as pd
import pandas_ta as ta
import numpy as np
import pytz

from config import (
    SCREENER_MODE,
    SCREENER_UNIVERSE,
    MIN_CLOSE_PRICE,
    MIN_DAILY_VOLUME,
    SCREENER_INTERVAL,
    SCREENER_LOOKBACK_DAYS,
    VCP_ATR_SHORT,
    VCP_ATR_LONG,
    VCP_COMPRESSION_THRESHOLD,
    TREND_EMA_PERIOD,
    TREND_LOOKBACK_DAYS,
    SR_PROXIMITY_PCT,
    TOP_SCREENER_RESULTS,
    RATE_LIMIT_DELAY,
    SCREENED_WATCHLIST_FILE,
)
from auth import get_authenticated_kite

IST = pytz.timezone("Asia/Kolkata")
MAX_RETRIES = 3


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: Build the Entry Universe (Token Resolution)
# ══════════════════════════════════════════════════════════════════════════════

def resolve_instrument_tokens(kite) -> List[Dict]:
    """
    Fetch the full NSE instrument dump and resolve tradingsymbols to tokens.

    In CURATED mode:
      - Match SCREENER_UNIVERSE symbols against the dump
      - Only keep matches with instrument_type == "EQ" and exchange == "NSE"
      - Warn about any symbols that couldn't be resolved

    In ALL_EQ mode:
      - Return all instruments where instrument_type == "EQ" and exchange == "NSE"

    Returns:
      List of {"symbol": str, "token": int} dicts
    """
    print("📡 Fetching NSE instrument master list...")
    raw_instruments = kite.instruments("NSE")
    all_instruments = pd.DataFrame(raw_instruments)
    print(f"   Raw instrument count: {len(all_instruments)}")

    # Filter strictly to equities on NSE
    equities = all_instruments[
        (all_instruments["instrument_type"] == "EQ") &
        (all_instruments["exchange"] == "NSE")
    ].copy()
    equities = equities.drop_duplicates(subset=["tradingsymbol"])
    print(f"   NSE equities (instrument_type=EQ): {len(equities)}")

    # Build a quick lookup: tradingsymbol → instrument_token
    sym_to_token = dict(zip(equities["tradingsymbol"], equities["instrument_token"]))

    if SCREENER_MODE == "CURATED":
        # Resolve curated symbols against the instrument dump
        resolved = []
        missing = []

        for sym in SCREENER_UNIVERSE:
            token = sym_to_token.get(sym)
            if token is not None:
                resolved.append({"symbol": sym, "token": int(token)})
            else:
                missing.append(sym)

        if missing:
            print(f"   ⚠️  {len(missing)} symbols not found in NSE dump (delisted/renamed?):")
            # Show up to 20 missing symbols
            for chunk_start in range(0, min(len(missing), 20), 10):
                chunk = missing[chunk_start:chunk_start + 10]
                print(f"      {', '.join(chunk)}")
            if len(missing) > 20:
                print(f"      ... and {len(missing) - 20} more")

        print(f"   ✅ Resolved {len(resolved)} / {len(SCREENER_UNIVERSE)} curated symbols")
        return resolved

    else:  # ALL_EQ mode
        resolved = [
            {"symbol": row["tradingsymbol"], "token": int(row["instrument_token"])}
            for _, row in equities.iterrows()
        ]
        print(f"   ✅ ALL_EQ mode: {len(resolved)} equities to scan")
        return resolved


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: Fetch Previous Day's Candle (Liquidity Verification)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_previous_day_candle(kite, token: int) -> Optional[Dict]:
    """
    Fetch the PREVIOUS trading day's daily OHLCV candle for one instrument.

    We look back up to 5 calendar days to handle weekends/holidays, and
    take the LAST available daily candle.

    Returns:
      {"close": float, "volume": int} or None on failure.
    """
    now = datetime.now(IST)
    # Go back 5 calendar days to cover weekends + potential holidays
    from_date = (now - timedelta(days=5)).strftime("%Y-%m-%d")
    to_date = now.strftime("%Y-%m-%d")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = kite.historical_data(
                instrument_token=token,
                from_date=from_date,
                to_date=to_date,
                interval="day",
                continuous=False,
                oi=False,
            )
            if not raw:
                return None

            # Take the last available daily candle (= most recent trading day)
            last_candle = raw[-1]
            return {
                "close": float(last_candle["close"]),
                "volume": int(last_candle["volume"]),
                "high": float(last_candle["high"]),
                "low": float(last_candle["low"]),
            }

        except Exception as e:
            err = str(e).lower()
            # Token invalid — skip silently
            if "token" in err and "invalid" in err:
                return None
            # Rate limit or network — retry with backoff
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
            else:
                return None

    return None


def verify_liquidity(kite, universe: List[Dict]) -> List[Dict]:
    """
    Stage 2 filter: fetch previous day's daily candle for each stock,
    apply close ≥ MIN_CLOSE_PRICE and volume ≥ MIN_DAILY_VOLUME.

    This is the corrected approach — we use ACTUAL historical data
    instead of the broken last_price field from the instrument dump.

    Returns the filtered list of {"symbol", "token"} dicts.
    """
    print(f"\n🔍 Verifying liquidity via historical daily candles...")
    print(f"   Filters: close ≥ ₹{MIN_CLOSE_PRICE}, volume ≥ {MIN_DAILY_VOLUME:,}")
    print(f"   Checking {len(universe)} stocks (rate-limited)...\n")

    passed = []
    rejected_price = 0
    rejected_volume = 0
    fetch_failed = 0

    for i, stock in enumerate(universe, 1):
        symbol = stock["symbol"]
        token = stock["token"]

        # Progress every 25 stocks
        if i % 25 == 1 or i == len(universe):
            print(f"   ⏳ Liquidity check: {i}/{len(universe)} — {len(passed)} passed so far")

        candle = fetch_previous_day_candle(kite, token)

        if candle is None:
            fetch_failed += 1
        elif candle["close"] < MIN_CLOSE_PRICE:
            rejected_price += 1
        elif candle["volume"] < MIN_DAILY_VOLUME:
            rejected_volume += 1
        else:
            # Passes both filters
            passed.append({
                "symbol": symbol,
                "token": token,
                "prev_close": candle["close"],
                "prev_volume": candle["volume"],
            })

        # Rate limit: ~3 req/sec
        time.sleep(RATE_LIMIT_DELAY)

    print(f"\n   📊 Liquidity verification results:")
    print(f"      ✅ Passed:           {len(passed)}")
    print(f"      ❌ Close < ₹{MIN_CLOSE_PRICE}:      {rejected_price}")
    print(f"      ❌ Volume < {MIN_DAILY_VOLUME:,}: {rejected_volume}")
    print(f"      ⚠️  Fetch failed:     {fetch_failed}")

    return passed


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: Fetch 5-Day Hourly Candles (for ATR + S/R Scoring)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_hourly_candles(kite, token: int, days: int = 5) -> Optional[pd.DataFrame]:
    """
    Fetch `days` of hourly candle data for a single instrument.
    Returns None on failure (after retries).
    """
    now = datetime.now(IST)
    from_date = now - timedelta(days=days)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = kite.historical_data(
                instrument_token=token,
                from_date=from_date.strftime("%Y-%m-%d"),
                to_date=now.strftime("%Y-%m-%d"),
                interval=SCREENER_INTERVAL,
                continuous=False, oi=False,
            )
            if not raw:
                return None

            df = pd.DataFrame(raw)
            df["datetime"] = pd.to_datetime(df["date"])
            df = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
            df.set_index("datetime", inplace=True)
            df.sort_index(inplace=True)
            return df

        except Exception as e:
            err = str(e).lower()
            if "token" in err and "invalid" in err:
                return None
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
            else:
                return None

    return None


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4: Scoring Functions (v3 — VCP + Trend Filter)
# ══════════════════════════════════════════════════════════════════════════════

def _compute_true_range(daily: pd.DataFrame) -> pd.Series:
    """Compute True Range series from daily OHLC (handles first row NaN)."""
    hl = daily["high"] - daily["low"]
    hc = (daily["high"] - daily["close"].shift(1)).abs()
    lc = (daily["low"] - daily["close"].shift(1)).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1)


def resample_to_daily(df_hourly: pd.DataFrame) -> pd.DataFrame:
    """Resample hourly candles to daily OHLCV bars."""
    daily = df_hourly.resample("1D").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return daily


def score_vcp(daily: pd.DataFrame) -> Dict:
    """
    Volatility Contraction Pattern (VCP) scoring.

    Instead of checking if ATR dropped on each consecutive day (which
    produces 0.0% on 5-day data with ATR(14)), we:

      1. Compute True Range on daily bars.
      2. Calculate short-term ATR (3-day rolling mean of TR).
      3. Calculate long-term ATR (10-day rolling mean of TR, or all available).
      4. Compression = 1 - (ATR_short / ATR_long).
         A value ≥ 0.20 means the recent range is ≥20% tighter than baseline.
      5. Also compute StdDev(close, 5) / ATR_long as a normalized
         volatility metric (lower = tighter consolidation).

    Returns:
        dict with compression_ratio, is_vcp, stddev_ratio, atr_short, atr_long
    """
    if len(daily) < max(VCP_ATR_SHORT, 3) + 1:
        return {"compression": 0.0, "is_vcp": False, "stddev_ratio": 0.0,
                "atr_short": 0.0, "atr_long": 0.0}

    tr = _compute_true_range(daily)

    # Short-term ATR: rolling mean of last N True Ranges
    atr_short_val = tr.tail(VCP_ATR_SHORT).mean()

    # Long-term ATR: rolling mean over longer window (or all available data)
    long_window = min(VCP_ATR_LONG, len(tr))
    atr_long_val = tr.tail(long_window).mean()

    # Compression ratio
    if atr_long_val > 0:
        compression = 1.0 - (atr_short_val / atr_long_val)
    else:
        compression = 0.0

    is_vcp = compression >= VCP_COMPRESSION_THRESHOLD

    # StdDev(close, 5) / ATR_long — lower = tighter consolidation
    close_std = daily["close"].tail(5).std()
    stddev_ratio = (close_std / atr_long_val) if atr_long_val > 0 else 0.0

    return {
        "compression": round(compression, 4),
        "is_vcp": is_vcp,
        "stddev_ratio": round(stddev_ratio, 3),
        "atr_short": round(atr_short_val, 2),
        "atr_long": round(atr_long_val, 2),
    }


def score_near_sr(daily: pd.DataFrame, proximity_pct: float = 1.0) -> Dict:
    """
    Check if latest close is within proximity_pct% of 5-day high/low.
    """
    if daily.empty:
        return {"near_resistance": False, "near_support": False,
                "dist_to_high_pct": 999, "dist_to_low_pct": 999,
                "five_day_high": 0, "five_day_low": 0}

    close = daily["close"].iloc[-1]
    high_5d = daily["high"].max()
    low_5d = daily["low"].min()

    dist_to_high = ((high_5d - close) / close) * 100 if close > 0 else 999
    dist_to_low = ((close - low_5d) / close) * 100 if close > 0 else 999

    return {
        "near_resistance": dist_to_high <= proximity_pct,
        "near_support": dist_to_low <= proximity_pct,
        "dist_to_high_pct": round(dist_to_high, 3),
        "dist_to_low_pct": round(dist_to_low, 3),
        "five_day_high": round(high_5d, 2),
        "five_day_low": round(low_5d, 2),
    }


def fetch_daily_for_trend(kite, token: int, days: int = 30) -> Optional[pd.DataFrame]:
    """
    Fetch daily candle data for the trend filter (20-day EMA check).
    Separate from the hourly fetch to avoid resampling artifacts.
    """
    now = datetime.now(IST)
    from_date = now - timedelta(days=days)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = kite.historical_data(
                instrument_token=token,
                from_date=from_date.strftime("%Y-%m-%d"),
                to_date=now.strftime("%Y-%m-%d"),
                interval="day",
                continuous=False, oi=False,
            )
            if not raw:
                return None
            df = pd.DataFrame(raw)
            df["datetime"] = pd.to_datetime(df["date"])
            df = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
            df.set_index("datetime", inplace=True)
            df.sort_index(inplace=True)
            return df
        except Exception:
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
            else:
                return None
    return None


def check_trend_alignment(daily_30d: pd.DataFrame) -> Dict:
    """
    Multi-timeframe trend filter using 20-day EMA.

    Returns:
        above_ema: bool — True if latest close > 20-EMA
        ema_value: float — the 20-EMA value
        ema_distance_pct: float — how far above/below (positive = above)
    """
    if daily_30d is None or len(daily_30d) < TREND_EMA_PERIOD:
        return {"above_ema": True, "ema_value": 0, "ema_distance_pct": 0}

    ema = ta.ema(daily_30d["close"], length=TREND_EMA_PERIOD)
    if ema is None or ema.dropna().empty:
        return {"above_ema": True, "ema_value": 0, "ema_distance_pct": 0}

    ema_val = float(ema.iloc[-1])
    close = float(daily_30d["close"].iloc[-1])
    dist = ((close - ema_val) / ema_val) * 100 if ema_val > 0 else 0

    return {
        "above_ema": close > ema_val,
        "ema_value": round(ema_val, 2),
        "ema_distance_pct": round(dist, 2),
    }


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5: Score a Single Stock (v3 — VCP + Trend-Aligned S/R)
# ══════════════════════════════════════════════════════════════════════════════

def score_stock(kite, symbol: str, token: int, prev_close: float) -> Optional[Dict]:
    """
    v3 scoring pipeline for a single liquid stock:

      1. Fetch 5-day hourly candles → resample to daily → compute VCP
      2. Score proximity to 5-day S/R levels
      3. Fetch 30-day daily candles → compute 20-EMA trend alignment
      4. BULL_ZONE setups below 20-EMA are penalized or dropped
      5. Combine into a composite score

    Returns a result dict or None if the stock doesn't qualify.
    """
    # ── Hourly → Daily for VCP + S/R ──
    df = fetch_hourly_candles(kite, token, days=SCREENER_LOOKBACK_DAYS)
    if df is None or len(df) < 10:
        return None

    daily = resample_to_daily(df)
    if daily.empty or len(daily) < 2:
        return None

    # VCP compression scoring
    vcp = score_vcp(daily)

    # S/R proximity scoring
    sr = score_near_sr(daily, SR_PROXIMITY_PCT)

    # A stock must have VCP compression OR be near a key level
    qualifies = vcp["is_vcp"] or sr["near_resistance"] or sr["near_support"]
    if not qualifies:
        return None

    # ── Trend alignment (20-day EMA from daily candles) ──
    # Extra API call — only for stocks that passed the first gate
    time.sleep(RATE_LIMIT_DELAY)
    daily_30d = fetch_daily_for_trend(kite, token, days=TREND_LOOKBACK_DAYS)
    trend = check_trend_alignment(daily_30d)

    # ── Build setup label ──
    setup = []
    if vcp["is_vcp"]:
        setup.append("VCP")
    if sr["near_resistance"]:
        # BULL_ZONE below 20-EMA = invalid, drop it
        if not trend["above_ema"]:
            setup.append("BULL_ZONE(!EMA)")  # Flagged but penalized
        else:
            setup.append("BULL_ZONE")
    if sr["near_support"]:
        setup.append("BEAR_ZONE")

    # ── Composite score ──
    score = 0.0

    # VCP: higher compression = higher score (max ~1.0, practical ~0.2-0.5)
    if vcp["is_vcp"]:
        score += vcp["compression"] * 50  # 0.20 compression → 10 pts

    # S/R proximity
    if sr["near_resistance"]:
        proximity_bonus = (SR_PROXIMITY_PCT - sr["dist_to_high_pct"]) * 10
        if trend["above_ema"]:
            score += proximity_bonus      # Full credit if aligned with trend
        else:
            score -= abs(proximity_bonus)  # PENALIZE: near high but below EMA

    if sr["near_support"]:
        score += (SR_PROXIMITY_PCT - sr["dist_to_low_pct"]) * 10

    # Trend bonus: reward stocks well above their 20-EMA
    if trend["above_ema"] and trend["ema_distance_pct"] > 0:
        score += min(trend["ema_distance_pct"], 5)  # Cap at 5 pts

    last_close = round(float(daily["close"].iloc[-1]), 2)

    return {
        "symbol": symbol,
        "token": token,
        "close": last_close,
        "compress%": round(vcp["compression"] * 100, 1),
        "atr_s": vcp["atr_short"],
        "atr_l": vcp["atr_long"],
        "setup": "+".join(setup) if setup else "NONE",
        "5d_high": sr["five_day_high"],
        "5d_low": sr["five_day_low"],
        "ema20": trend["ema_value"],
        "trend": "ABOVE" if trend["above_ema"] else "BELOW",
        "avg_vol": 0,  # Filled by caller from liquidity step
        "score": round(score, 2),
    }


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6: Full Screening Pipeline
# ══════════════════════════════════════════════════════════════════════════════

def run_screener(kite=None) -> pd.DataFrame:
    """
    Execute the corrected pre-market screening pipeline:

      1. Resolve instrument tokens (CURATED or ALL_EQ mode)
      2. Verify liquidity via historical daily candle (close + volume)
      3. Score survivors for ATR shrinkage + near S/R
      4. Rank and output Top 15
      5. Save watchlist JSON for Phase 2

    Returns a DataFrame of the top screened stocks.
    """
    if kite is None:
        kite = get_authenticated_kite()

    print()
    print("=" * 70)
    print("  🔬 PHASE 1 — PRE-MARKET VCP & TREND-ALIGNED SCREENER (v3)")
    print("=" * 70)
    print(f"  Mode: {SCREENER_MODE} ({len(SCREENER_UNIVERSE) if SCREENER_MODE == 'CURATED' else 'all'} symbols)")
    print(f"  VCP: ATR({VCP_ATR_SHORT}) vs ATR({VCP_ATR_LONG}), threshold ≥ {VCP_COMPRESSION_THRESHOLD*100:.0f}%")
    print(f"  Trend: 20-EMA filter on {TREND_LOOKBACK_DAYS}d daily data")
    print()

    # ── Stage 1: Resolve tokens ──
    universe = resolve_instrument_tokens(kite)
    if not universe:
        print("   ❌ No instruments resolved. Check SCREENER_UNIVERSE in config.py.")
        return pd.DataFrame()

    # ── Stage 2: Verify liquidity from historical data ──
    liquid_stocks = verify_liquidity(kite, universe)
    if not liquid_stocks:
        print("   ❌ No stocks passed the liquidity filter.")
        return pd.DataFrame()

    # ── Stage 3: Score each liquid stock (VCP + S/R + Trend) ──
    total = len(liquid_stocks)
    # Each stock needs ~2 API calls (hourly + daily), so double the estimate
    est_minutes = (total * RATE_LIMIT_DELAY * 2) / 60
    print(f"\n🔄 Scoring {total} liquid stocks for VCP + trend setups (~{est_minutes:.1f} min)...\n")

    results = []
    errors = 0

    for i, stock in enumerate(liquid_stocks, 1):
        symbol = stock["symbol"]
        token = stock["token"]

        if i % 20 == 1 or i == total:
            pct = (i / total) * 100
            print(f"   ⏳ Scoring: {i}/{total} ({pct:.0f}%) — {len(results)} setups found")

        try:
            result = score_stock(kite, symbol, token, stock["prev_close"])
            if result is not None:
                result["avg_vol"] = stock["prev_volume"]
                results.append(result)
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"   ⚠️  {symbol}: {e}")

        time.sleep(RATE_LIMIT_DELAY)

    print(f"\n📊 Scoring complete: {len(results)} setups / {total - len(results) - errors} no-match / {errors} errors")

    if not results:
        print("   ❌ No stocks matched VCP compression or S/R proximity criteria.")
        return pd.DataFrame()

    # ── Stage 4: Rank and output ──
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values("score", ascending=False).head(TOP_SCREENER_RESULTS)
    results_df = results_df.reset_index(drop=True)
    results_df.index += 1  # 1-based rank

    _print_results_table(results_df)
    _save_watchlist(results_df)

    return results_df


# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _print_results_table(df: pd.DataFrame) -> None:
    """Print a clean ASCII table of v3 screener results."""
    print()
    print("=" * 120)
    print(f"  🏆 TOP {len(df)} SCREENED STOCKS — Tomorrow's Watchlist (v3 VCP + Trend)")
    print("=" * 120)

    header = (
        f"{'#':>3} │ {'Symbol':<14} │ {'Close':>8} │ {'VCP%':>6} │ {'ATR s/l':>10} │ "
        f"{'Setup':<22} │ {'5D-High':>8} │ {'EMA20':>8} │ {'Trend':>5} │ {'Volume':>12} │ {'Score':>6}"
    )
    print(header)
    print("─" * 120)

    for rank, row in df.iterrows():
        atr_sl = f"{row['atr_s']}/{row['atr_l']}"
        line = (
            f"{rank:>3} │ {row['symbol']:<14} │ ₹{row['close']:>6} │ {row['compress%']:>5}% │ "
            f"{atr_sl:>10} │ {row['setup']:<22} │ ₹{row['5d_high']:>6} │ "
            f"₹{row['ema20']:>6} │ {row['trend']:>5} │ {row['avg_vol']:>12,} │ {row['score']:>6}"
        )
        print(line)

    print("=" * 120)
    print()


def _save_watchlist(df: pd.DataFrame) -> None:
    """Save screened watchlist to JSON for the live engine to pick up.
    Includes trend metadata so the live engine can filter by EMA alignment."""
    watchlist = [
        {
            "symbol": row["symbol"],
            "token": int(row["token"]),
            "trend": row.get("trend", "N/A"),
        }
        for _, row in df.iterrows()
    ]
    with open(SCREENED_WATCHLIST_FILE, "w") as f:
        json.dump({
            "generated_at": datetime.now(IST).isoformat(),
            "count": len(watchlist),
            "stocks": watchlist,
        }, f, indent=2)
    print(f"💾 Watchlist saved to {SCREENED_WATCHLIST_FILE} ({len(watchlist)} stocks)")


def load_screened_watchlist() -> List[Dict]:
    """Load the screened watchlist from disk. Falls back to FALLBACK_WATCHLIST."""
    from config import FALLBACK_WATCHLIST
    try:
        with open(SCREENED_WATCHLIST_FILE, "r") as f:
            data = json.load(f)
        stocks = data.get("stocks", [])
        if stocks:
            gen = data.get("generated_at", "unknown")
            print(f"📋 Loaded screened watchlist: {len(stocks)} stocks (generated: {gen})")
            return stocks
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    print(f"📋 No screened watchlist found — using fallback ({len(FALLBACK_WATCHLIST)} stocks)")
    return FALLBACK_WATCHLIST


# ══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    try:
        run_screener()
    except KeyboardInterrupt:
        print("\n🛑 Screener interrupted.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
