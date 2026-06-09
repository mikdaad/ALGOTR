"""
==================================================================================
  DATABASE LOGGER — Upgraded Schema (db_logger.py)
==================================================================================
  Logs both legacy signals and new BreakoutSignals to PostgreSQL (Supabase).
  
  Run init.sql in the Supabase SQL Editor to create the required tables.
==================================================================================
"""

from config import ENABLE_DB_LOGGING, SUPABASE_DB_URL


def _get_connection():
    """Create a PostgreSQL connection."""
    import psycopg2
    return psycopg2.connect(SUPABASE_DB_URL)


def log_breakout_signal(signal) -> bool:
    """
    Insert a BreakoutSignal into the breakout_signals table.
    Returns True on success, False on failure.
    No-ops silently if DB logging is disabled.
    """
    if not ENABLE_DB_LOGGING:
        return False

    try:
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO breakout_signals (
                symbol, direction, breakout_type, entry_price, stop_loss,
                obi, total_bid_qty, total_ask_qty,
                or_high, or_low,
                candle_open, candle_high, candle_low, candle_close,
                signal_time
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            signal.symbol, signal.direction, signal.breakout_type,
            signal.entry_price, signal.stop_loss,
            signal.obi, signal.total_bid_qty, signal.total_ask_qty,
            signal.or_high, signal.or_low,
            signal.candle_open, signal.candle_high, signal.candle_low,
            signal.candle_close, str(signal.timestamp),
        ))
        conn.commit()
        cur.close()
        conn.close()
        print(f"   💾 DB: ✅ Logged {signal.direction} {signal.symbol}")
        return True
    except Exception as e:
        print(f"   💾 DB: ❌ {e}")
        return False


def log_screener_result(result_df) -> bool:
    """
    Log the pre-market screener results to the screener_runs table.
    Accepts the DataFrame from run_screener().
    """
    if not ENABLE_DB_LOGGING:
        return False

    try:
        conn = _get_connection()
        cur = conn.cursor()
        from datetime import datetime
        import pytz
        IST = pytz.timezone("Asia/Kolkata")
        run_time = datetime.now(IST).isoformat()

        for _, row in result_df.iterrows():
            cur.execute("""
                INSERT INTO screener_runs (
                    run_time, symbol, close_price, atr,
                    atr_shrink_pct, setup, five_day_high, five_day_low,
                    avg_volume, score
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                run_time, row["symbol"], row["close"], row["atr"],
                row["atr_shrink_pct"], row["setup"],
                row["5d_high"], row["5d_low"],
                row["avg_vol"], row["score"],
            ))

        conn.commit()
        cur.close()
        conn.close()
        print(f"   💾 DB: ✅ Logged screener run ({len(result_df)} stocks)")
        return True
    except Exception as e:
        print(f"   💾 DB: ❌ Screener log failed — {e}")
        return False
