"""
==================================================================================
  MODULE 3 — Strategy Engine (strategy.py)
==================================================================================
  Volume-Price Analysis & Price Action Reversal Detection.
  
  STRATEGY LOGIC:
  ───────────────
  Detects bullish/bearish engulfing candlestick patterns that occur
  simultaneously with a volume spike (≥ 1.5× the 20-period volume SMA).
  
  BULLISH ENGULFING:
    - Previous candle is bearish (close < open)
    - Current candle is bullish (close > open)  
    - Current body fully engulfs previous body
      (current open ≤ prev close AND current close ≥ prev open)
    - Volume spike present on current candle
    
  BEARISH ENGULFING:
    - Previous candle is bullish (close > open)
    - Current candle is bearish (close < open)
    - Current body fully engulfs previous body
      (current open ≥ prev close AND current close ≤ prev open)
    - Volume spike present on current candle
    
  STOP-LOSS CALCULATION:
    - For BUY signals:  SL = low of the engulfing candle
    - For SELL signals: SL = high of the engulfing candle
==================================================================================
"""

from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import pandas_ta as ta

from config import VOLUME_SPIKE_MULTIPLIER, VOLUME_MA_PERIOD


@dataclass
class Signal:
    """Represents a detected trading signal."""
    symbol: str
    direction: str          # "BUY" or "SELL"
    pattern: str            # "BULLISH_ENGULFING" or "BEARISH_ENGULFING"
    entry_price: float      # Close price of the signal candle
    stop_loss: float        # Low (for BUY) or High (for SELL) of signal candle
    candle_time: datetime   # Timestamp of the signal candle
    volume: int             # Volume of the signal candle
    volume_ratio: float     # How many × above the volume MA
    open_price: float
    high_price: float
    low_price: float
    close_price: float


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add technical indicators needed for the strategy.
    
    Adds columns:
      - vol_sma_20: 20-period SMA of volume
      - vol_ratio:  current volume / vol_sma_20
      - vol_spike:  True if vol_ratio >= VOLUME_SPIKE_MULTIPLIER
    """
    df = df.copy()

    # Volume moving average
    df["vol_sma_20"] = ta.sma(df["volume"], length=VOLUME_MA_PERIOD)
    
    # Volume ratio (how many × above the MA)
    df["vol_ratio"] = df["volume"] / df["vol_sma_20"]
    
    # Volume spike flag
    df["vol_spike"] = df["vol_ratio"] >= VOLUME_SPIKE_MULTIPLIER
    
    return df


def detect_engulfing(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detect bullish and bearish engulfing patterns on the last candle.
    
    Adds columns:
      - bullish_engulfing: True on candles that form a bullish engulfing
      - bearish_engulfing: True on candles that form a bearish engulfing
    """
    df = df.copy()
    
    df["prev_open"]  = df["open"].shift(1)
    df["prev_close"] = df["close"].shift(1)
    
    # Body directions
    prev_bearish = df["prev_close"] < df["prev_open"]
    prev_bullish = df["prev_close"] > df["prev_open"]
    curr_bullish = df["close"] > df["open"]
    curr_bearish = df["close"] < df["open"]
    
    # Engulfing conditions (body engulfs body)
    df["bullish_engulfing"] = (
        prev_bearish & curr_bullish &
        (df["open"] <= df["prev_close"]) &
        (df["close"] >= df["prev_open"])
    )
    
    df["bearish_engulfing"] = (
        prev_bullish & curr_bearish &
        (df["open"] >= df["prev_close"]) &
        (df["close"] <= df["prev_open"])
    )
    
    # Clean up temp columns
    df.drop(columns=["prev_open", "prev_close"], inplace=True)
    
    return df


def scan_stock(symbol: str, df: pd.DataFrame) -> Optional[Signal]:
    """
    Run the full strategy on a single stock's DataFrame.
    
    Checks ONLY the most recent (last) candle for a signal.
    This prevents duplicate alerts on old candles.
    
    Returns:
        A Signal object if conditions are met, else None.
    """
    if df is None or len(df) < VOLUME_MA_PERIOD + 2:
        return None
    
    # Compute indicators
    df = compute_indicators(df)
    df = detect_engulfing(df)
    
    # Drop rows where indicators haven't warmed up
    df = df.dropna(subset=["vol_sma_20"])
    
    if df.empty:
        return None
    
    # Check ONLY the last candle
    last = df.iloc[-1]
    
    # ── BULLISH ENGULFING + VOLUME SPIKE ──
    if last.get("bullish_engulfing", False) and last.get("vol_spike", False):
        return Signal(
            symbol=symbol,
            direction="BUY",
            pattern="BULLISH_ENGULFING",
            entry_price=round(float(last["close"]), 2),
            stop_loss=round(float(last["low"]), 2),
            candle_time=last.name,  # DatetimeIndex
            volume=int(last["volume"]),
            volume_ratio=round(float(last["vol_ratio"]), 2),
            open_price=round(float(last["open"]), 2),
            high_price=round(float(last["high"]), 2),
            low_price=round(float(last["low"]), 2),
            close_price=round(float(last["close"]), 2),
        )
    
    # ── BEARISH ENGULFING + VOLUME SPIKE ──
    if last.get("bearish_engulfing", False) and last.get("vol_spike", False):
        return Signal(
            symbol=symbol,
            direction="SELL",
            pattern="BEARISH_ENGULFING",
            entry_price=round(float(last["close"]), 2),
            stop_loss=round(float(last["high"]), 2),
            candle_time=last.name,
            volume=int(last["volume"]),
            volume_ratio=round(float(last["vol_ratio"]), 2),
            open_price=round(float(last["open"]), 2),
            high_price=round(float(last["high"]), 2),
            low_price=round(float(last["low"]), 2),
            close_price=round(float(last["close"]), 2),
        )
    
    return None


def scan_all(data: Dict[str, pd.DataFrame]) -> List[Signal]:
    """
    Scan all stocks in the data dict for signals.
    
    Args:
        data: Dict of symbol → OHLCV DataFrame (from data_fetcher).
        
    Returns:
        List of Signal objects (may be empty if no setups found).
    """
    signals = []
    
    for symbol, df in data.items():
        try:
            signal = scan_stock(symbol, df)
            if signal is not None:
                signals.append(signal)
        except Exception as e:
            print(f"   ⚠️  Strategy error on {symbol}: {e}")
    
    return signals
