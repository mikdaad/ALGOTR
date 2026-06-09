"""
==================================================================================
  MODULE 2 — Data Fetching (data_fetcher.py)
==================================================================================
  Fetches historical intraday OHLCV candle data from Zerodha Kite Connect.
  Handles rate limits, retries, and returns clean pandas DataFrames.
==================================================================================
"""

import time
from datetime import datetime, timedelta
from typing import Dict, Optional
import pandas as pd
import pytz
from kiteconnect import KiteConnect
from config import WATCHLIST, CANDLE_INTERVAL, LOOKBACK_DAYS

IST = pytz.timezone("Asia/Kolkata")
REQUEST_DELAY = 0.4
MAX_RETRIES = 3


def fetch_historical(kite, token, interval=None, from_date=None, to_date=None):
    """Fetch OHLCV candles for one instrument with retry logic."""
    if interval is None:
        interval = CANDLE_INTERVAL
    now = datetime.now(IST)
    if to_date is None:
        to_date = now
    if from_date is None:
        from_date = now - timedelta(days=LOOKBACK_DAYS)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = kite.historical_data(
                instrument_token=token,
                from_date=from_date.strftime("%Y-%m-%d %H:%M:%S"),
                to_date=to_date.strftime("%Y-%m-%d %H:%M:%S"),
                interval=interval, continuous=False, oi=False,
            )
            if not raw:
                return pd.DataFrame(columns=["open","high","low","close","volume"])
            df = pd.DataFrame(raw)
            df["datetime"] = pd.to_datetime(df["date"])
            if df["datetime"].dt.tz is None:
                df["datetime"] = df["datetime"].dt.tz_localize(IST)
            else:
                df["datetime"] = df["datetime"].dt.tz_convert(IST)
            df = df[["datetime","open","high","low","close","volume"]].copy()
            df.set_index("datetime", inplace=True)
            df.sort_index(inplace=True)
            return df
        except Exception as e:
            err = str(e).lower()
            if "token" in err and "invalid" in err:
                raise RuntimeError("Access token expired. Re-run 'python auth.py'.") from e
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"   ⚠️  Retry {attempt}/{MAX_RETRIES} in {wait}s: {e}")
                time.sleep(wait)
            else:
                raise


def fetch_watchlist(kite, watchlist=None, interval=None):
    """Fetch candles for all watchlist stocks. Returns dict of symbol→DataFrame."""
    wl = watchlist or WATCHLIST
    iv = interval or CANDLE_INTERVAL
    result = {}
    for i, s in enumerate(wl, 1):
        try:
            print(f"   📊 [{i}/{len(wl)}] {s['symbol']}...", end=" ")
            df = fetch_historical(kite, s["token"], interval=iv)
            if df.empty:
                print("⚠️  empty")
            else:
                result[s["symbol"]] = df
                print(f"✅ {len(df)} candles")
        except RuntimeError:
            raise
        except Exception as e:
            print(f"❌ {e}")
        if i < len(wl):
            time.sleep(REQUEST_DELAY)
    return result


def fetch_latest(kite, watchlist=None, interval=None, lookback=30):
    """Fetch only the most recent N candles (optimized for scan loop)."""
    wl = watchlist or WATCHLIST
    iv = interval or CANDLE_INTERVAL
    mins = {"minute":1,"3minute":3,"5minute":5,"10minute":10,
            "15minute":15,"30minute":30,"60minute":60}.get(iv, 5)
    cpd = 375 // mins  # candles per trading day
    days = max(2, (lookback // cpd) + 2)
    now = datetime.now(IST)
    fd = now - timedelta(days=days)
    result = {}
    for i, s in enumerate(wl, 1):
        try:
            df = fetch_historical(kite, s["token"], interval=iv, from_date=fd, to_date=now)
            if not df.empty:
                result[s["symbol"]] = df.tail(lookback).copy()
        except RuntimeError:
            raise
        except Exception as e:
            print(f"   ⚠️  {s['symbol']}: {e}")
        if i < len(wl):
            time.sleep(REQUEST_DELAY)
    return result
