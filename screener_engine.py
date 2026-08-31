import sqlite3
import yfinance as yf
import pandas as pd
import numpy as np

DB_NAME = "trade_lifecycle.db"

# Core Universe of Liquid Stocks
STOCK_UNIVERSE = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS",
    "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "LT.NS", "TATAMOTORS.NS",
    "SUNPHARMA.NS", "TITAN.NS", "BAJFINANCE.NS", "MARUTI.NS", "ASIANPAINT.NS",
    "KIRLOSENG.NS", "MINDACORP.NS", "GRASIM.NS", "COHANCE.NS", "TATASTEEL.NS",
    "JSWSTEEL.NS", "HINDALCO.NS", "NTPC.NS", "POWERGRID.NS", "ADANIENT.NS"
]

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))

def init_tables():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    for tbl in ["intraday_trades", "swing_trades", "longterm_trades"]:
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {tbl} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT UNIQUE,
                entry REAL,
                target REAL,
                stop_loss REAL,
                status TEXT DEFAULT 'ACTIVE',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.commit()
    conn.close()

def run_screener():
    init_tables()
    print("Running multi-condition screener engine...")

    intraday_picks = []
    swing_picks = []
    longterm_picks = []

    for sym in STOCK_UNIVERSE:
        try:
            df = yf.Ticker(sym).history(period="1y", interval="1d")
            if len(df) < 50:
                continue

            clean_sym = sym.replace(".NS", "")
            close = df["Close"].iloc[-1]
            prev_close = df["Close"].iloc[-2]
            volume = df["Volume"].iloc[-1]
            avg_vol_20 = df["Volume"].rolling(20).mean().iloc[-1]

            df["EMA_20"] = df["Close"].ewm(span=20, adjust=False).mean()
            df["EMA_50"] = df["Close"].ewm(span=50, adjust=False).mean()
            df["RSI"] = calculate_rsi(df["Close"])

            curr_rsi = df["RSI"].iloc[-1]
            ema_20 = df["EMA_20"].iloc[-1]
            ema_50 = df["EMA_50"].iloc[-1]

            vol_spike = volume > (1.5 * avg_vol_20)
            daily_change_pct = ((close - prev_close) / prev_close) * 100

            # 1. Intraday Momentum Filter: Vol Spike + Price Up > 1% + RSI between 50-70
            if vol_spike and daily_change_pct >= 1.0 and 50 <= curr_rsi <= 72:
                intraday_picks.append({
                    "symbol": clean_sym,
                    "entry": round(close, 2),
                    "target": round(close * 1.025, 2), # 2.5% Target
                    "stop_loss": round(close * 0.988, 2) # 1.2% SL
                })

            # 2. Swing Breakout Filter: Price > 20 EMA > 50 EMA + RSI Pullback (55-68)
            if close > ema_20 > ema_50 and 55 <= curr_rsi <= 68 and vol_spike:
                swing_picks.append({
                    "symbol": clean_sym,
                    "entry": round(close, 2),
                    "target": round(close * 1.08, 2), # 8% Target
                    "stop_loss": round(close * 0.95, 2) # 5% SL
                })

            # 3. Longterm Trend Filter: 50 EMA upward slope + Price well above 50 EMA
            if close > ema_50 and ema_20 > ema_50:
                longterm_picks.append({
                    "symbol": clean_sym,
                    "entry": round(close, 2),
                    "target": round(close * 1.20, 2), # 20% Target
                    "stop_loss": round(close * 0.90, 2) # 10% SL
                })

        except Exception as e:
            continue

    # Sync to SQLite Database
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    def sync_table(table_name, picks):
        cursor.execute(f"DELETE FROM {table_name}")
        for p in picks[:8]: # Keep top picks
            cursor.execute(f"""
                INSERT OR REPLACE INTO {table_name} (symbol, entry, target, stop_loss, status)
                VALUES (?, ?, ?, ?, 'ACTIVE')
            """, (p["symbol"], p["entry"], p["target"], p["stop_loss"]))

    sync_table("intraday_trades", intraday_picks)
    sync_table("swing_trades", swing_picks)
    sync_table("longterm_trades", longterm_picks)

    conn.commit()
    conn.close()
    print("Screener completed successfully.")

if __name__ == "__main__":
    run_screener()