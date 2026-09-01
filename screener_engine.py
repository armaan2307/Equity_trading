import sqlite3
import yfinance as yf
import pandas as pd
import numpy as np
import time

DB_NAME = "trade_lifecycle.db"

# Expanded broad universe across Nifty 50, Nifty Next 50, and Top Midcaps
SCAN_UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "BHARTIARTL", "SBIN", "INFY", "ITC", "HINDUNILVR", "LT",
    "BAJFINANCE", "HCLTECH", "MARUTI", "SUNPHARMA", "ADANIENT", "KOTAKBANK", "TITAN", "ONGC", "TATAMOTORS",
    "NTPC", "AXISBANK", "POWERGRID", "ADANIPORTS", "COALINDIA", "TRENT", "BEL", "HAL", "ZOMATO", "CHOLAFIN",
    "MCX", "KALYANKJIL", "AMBER", "AEGISCHEM", "DLF", "DIXON", "POLYCAB", "PERSISTENT", "VBL", "JIOFIN",
    "BSE", "CDSL", "MANAPPURAM", "FEDERALBNK", "IDFCFIRSTB", "ABCAPITAL", "MOTHERSON", "TATASTEEL", "JSWSTEEL",
    "VEDL", "VOLTAS", "TVSMOTOR", "HEROMOTOCO", "BAJAJ-AUTO", "EICHERMOT", "APOLLOHOSP", "MAXHEALTH", "LUPIN",
    "CIPLA", "DRREDDY", "DIVISLAB", "AUROPHARMA", "TORNTPHARM", "ALKEM", "GLENMARK", "BIOCON", "INDIGO",
    "IRCTC", "RVNL", "IRFC", "MAZDOCK", "COCHINSHIP", "GRSE", "BDL", "BHEL", "SAIL", "NATIONALUM", "HINDALCO",
    "JINDALSTEL", "NMDC", "CANBK", "PNB", "BANKBARODA", "UNIONBANK", "IOB", "INDIANB", "MAHABANK", "PRESTIGE",
    "OBEROIRLTY", "GODREJPROP", "BRIGADE", "PHOENIXLTD", "SOBHA", "DEEPAKNTR", "TATACHEM", "SRF", "AARTIIND"
]

def calculate_levels(df, timeframe="intraday"):
    if df.empty or len(df) < 5:
        return None
    close = float(df['Close'].iloc[-1])
    high = float(df['High'].iloc[-1])
    low = float(df['Low'].iloc[-1])
    
    tr = np.maximum(df['High'] - df['Low'], np.maximum(abs(df['High'] - df['Close'].shift(1)), abs(df['Low'] - df['Close'].shift(1))))
    atr = float(tr.tail(14).mean()) if len(tr) >= 14 else (high - low)
    if atr == 0 or np.isnan(atr):
        atr = close * 0.015

    if timeframe == "intraday":
        entry = round(close, 2)
        target = round(close + (1.5 * atr), 2)
        sl = round(close - (1.0 * atr), 2)
    elif timeframe == "swing":
        entry = round(close, 2)
        target = round(close + (3.0 * atr), 2)
        sl = round(close - (1.8 * atr), 2)
    else:
        entry = round(close, 2)
        target = round(close * 1.25, 2)
        sl = round(close * 0.90, 2)
        
    return {"entry": entry, "target": target, "stop_loss": sl}

def run_screener():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    for seg in ["intraday", "swing", "longterm"]:
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {seg}_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT UNIQUE,
                entry REAL,
                target REAL,
                stop_loss REAL,
                status TEXT DEFAULT 'ACTIVE',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    intraday_list = []
    swing_list = []
    longterm_list = []

    # Batch process in chunks of 20 to prevent memory spikes
    chunk_size = 20
    chunks = [SCAN_UNIVERSE[i:i + chunk_size] for i in range(0, len(SCAN_UNIVERSE), chunk_size)]

    for chunk in chunks:
        symbols_ns = [f"{s}.NS" for s in chunk]
        try:
            data = yf.download(symbols_ns, period="1mo", interval="1d", group_by='ticker', threads=True, progress=False)
        except Exception:
            continue

        for sym in chunk:
            try:
                df = data[f"{sym}.NS"] if f"{sym}.NS" in data else None
                if df is None or df.empty or len(df.dropna()) < 10:
                    continue
                df = df.dropna()
                
                cmp_val = float(df['Close'].iloc[-1])
                prev_close = float(df['Close'].iloc[-2])
                roc_1d = ((cmp_val - prev_close) / prev_close) * 100

                # 1. Intraday Filter: High 1-Day ROC & Volume Expansion
                intra_score = abs(roc_1d)
                lvl_i = calculate_levels(df, "intraday")
                if lvl_i:
                    intraday_list.append((intra_score, sym, lvl_i['entry'], lvl_i['target'], lvl_i['stop_loss']))

                # 2. Swing Filter: Trend continuation over EMA 20
                ema20 = df['Close'].ewm(span=20, adjust=False).mean().iloc[-1]
                swing_score = (cmp_val - ema20) / ema20 * 100
                lvl_s = calculate_levels(df, "swing")
                if lvl_s:
                    swing_list.append((swing_score, sym, lvl_s['entry'], lvl_s['target'], lvl_s['stop_loss']))

                # 3. Longterm Filter: Steady consolidation / value scoring
                sma20 = df['Close'].rolling(20).mean().iloc[-1]
                long_score = (cmp_val - sma20) / sma20 * 100
                lvl_l = calculate_levels(df, "longterm")
                if lvl_l:
                    longterm_list.append((long_score, sym, lvl_l['entry'], lvl_l['target'], lvl_l['stop_loss']))
            except Exception:
                continue

    # Sort and pick top 10 ranked setups
    intraday_list.sort(key=lambda x: x[0], reverse=True)
    swing_list.sort(key=lambda x: x[0], reverse=True)
    longterm_list.sort(key=lambda x: x[0], reverse=True)

    for seg, data_rows in [("intraday", intraday_list[:10]), ("swing", swing_list[:10]), ("longterm", longterm_list[:10])]:
        table = f"{seg}_trades"
        cursor.execute(f"DELETE FROM {table}")
        for item in data_rows:
            _, sym, entry, target, sl = item
            cursor.execute(
                f"INSERT OR REPLACE INTO {table} (symbol, entry, target, stop_loss) VALUES (?, ?, ?, ?)",
                (sym, entry, target, sl)
            )

    conn.commit()
    conn.close()

if __name__ == "__main__":
    run_screener()
