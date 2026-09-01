import sqlite3
import yfinance as yf
import pandas as pd
import numpy as np

DB_NAME = "trade_lifecycle.db"

# Broad market universe covering large-caps, liquid mid-caps, and high-momentum stocks
UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "BHARTIARTL", "SBIN", "INFY", "LICI", "ITC", "HINDUNILVR",
    "LT", "BAJFINANCE", "HCLTECH", "MARUTI", "SUNPHARMA", "ADANIENT", "KOTAKBANK", "TITAN", "ONGC", "TATAMOTORS",
    "NTPC", "AXISBANK", "POWERGRID", "ADANIPORTS", "COALINDIA", "TRENT", "BEL", "HAL", "ZOMATO", "CHOLAFIN",
    "MCX", "KALYANKJIL", "AMBER", "AEGISCHEM", "DLF", "DIXON", "POLYCAB", "PERSISTENT", "VBL", "JIOFIN",
    "BSE", "CDSL", "MANAPPURAM", "FEDERALBNK", "IDFCFIRSTB", "ABCAPITAL", "MOTHERSON", "TATASTEEL", "JSWSTEEL", "VEDL"
]

def calculate_levels(df, timeframe="intraday"):
    if df.empty or len(df) < 5:
        return None
    
    close = float(df['Close'].iloc[-1])
    high = float(df['High'].iloc[-1])
    low = float(df['Low'].iloc[-1])
    
    # ATR Approximation for dynamic risk/reward
    tr = np.maximum(df['High'] - df['Low'], np.maximum(abs(df['High'] - df['Close'].shift(1)), abs(df['Low'] - df['Close'].shift(1))))
    atr = float(tr.tail(14).mean()) if len(tr) >= 14 else (high - low)
    if atr == 0 or np.isnan(atr):
        atr = close * 0.015

    if timeframe == "intraday":
        entry = round(close, 2)
        target = round(close + (1.5 * atr), 2)
        stop_loss = round(close - (1.0 * atr), 2)
    elif timeframe == "swing":
        entry = round(close, 2)
        target = round(close + (3.0 * atr), 2)
        stop_loss = round(close - (1.8 * atr), 2)
    else:  # longterm
        entry = round(close, 2)
        target = round(close * 1.25, 2)
        stop_loss = round(close * 0.90, 2)
        
    return {"entry": entry, "target": target, "stop_loss": stop_loss}

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

    intraday_candidates = []
    swing_candidates = []
    longterm_candidates = []

    # Batch download to stay well within memory limits
    symbols_ns = [f"{s}.NS" for s in UNIVERSE]
    
    try:
        data = yf.download(symbols_ns, period="3mo", interval="1d", group_by='ticker', threads=True, progress=False)
    except Exception:
        data = None

    for sym in UNIVERSE:
        try:
            df = data[f"{sym}.NS"] if data is not None and f"{sym}.NS" in data else yf.Ticker(f"{sym}.NS").history(period="3mo", interval="1d")
            df = df.dropna()
            if len(df) < 20:
                continue

            close = df['Close']
            cmp_val = float(close.iloc[-1])
            
            # 1. Intraday Momentum Scoring (Short-term ROC & Volatility)
            roc_1d = ((cmp_val - float(close.iloc[-2])) / float(close.iloc[-2])) * 100
            roc_5d = ((cmp_val - float(close.iloc[-5])) / float(close.iloc[-5])) * 100
            intraday_score = abs(roc_1d) * 1.5 + abs(roc_5d)
            
            lvl_intra = calculate_levels(df, "intraday")
            if lvl_intra:
                intraday_candidates.append((intraday_score, (sym, lvl_intra['entry'], lvl_intra['target'], lvl_intra['stop_loss'])))

            # 2. Swing Scoring (EMA 20 Trend Strength & Breakout Potential)
            ema20 = close.ewm(span=20, adjust=False).mean()
            ema50 = close.ewm(span=50, adjust=False).mean() if len(df) >= 50 else ema20
            swing_score = (cmp_val - float(ema20.iloc[-1])) / float(ema20.iloc[-1]) * 100
            
            lvl_swing = calculate_levels(df, "swing")
            if lvl_swing:
                swing_candidates.append((swing_score, (sym, lvl_swing['entry'], lvl_swing['target'], lvl_swing['stop_loss'])))

            # 3. Longterm Scoring (Base consistency & Higher Low structure)
            sma50 = close.rolling(50).mean().iloc[-1] if len(df) >= 50 else cmp_val
            longterm_score = (cmp_val - float(sma50)) / float(sma50) * 100
            
            lvl_long = calculate_levels(df, "longterm")
            if lvl_long:
                longterm_candidates.append((longterm_score, (sym, lvl_long['entry'], lvl_long['target'], lvl_long['stop_loss'])))

        except Exception:
            continue

    # Sort candidates by top rank
    intraday_candidates.sort(key=lambda x: x[0], reverse=True)
    swing_candidates.sort(key=lambda x: x[0], reverse=True)
    longterm_candidates.sort(key=lambda x: x[0], reverse=True)

    # Save Top 10 to Database
    for seg, ranked in [("intraday", intraday_candidates[:10]), ("swing", swing_candidates[:10]), ("longterm", longterm_candidates[:10])]:
        table = f"{seg}_trades"
        cursor.execute(f"DELETE FROM {table}")
        for item in ranked:
            sym, entry, target, sl = item[1]
            cursor.execute(
                f"INSERT OR REPLACE INTO {table} (symbol, entry, target, stop_loss) VALUES (?, ?, ?, ?)",
                (sym, entry, target, sl)
            )

    conn.commit()
    conn.close()

if __name__ == "__main__":
    run_screener()
