import sqlite3
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import io
import time
from concurrent.futures import ThreadPoolExecutor

DB_NAME = "trade_lifecycle.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    for table in ["intraday_trades", "swing_trades", "longterm_trades"]:
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
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

# ----------------- UNIVERSE: NIFTY 500 (NIFTY 50 + MIDCAP 150 + SMALLCAP 250) -----------------
def get_nifty_500_universe():
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            df = pd.read_csv(io.StringIO(response.text))
            if "Symbol" in df.columns:
                symbols = df["Symbol"].dropna().unique().tolist()
                return symbols
    except Exception:
        pass

    try:
        mirror_url = "https://raw.githubusercontent.com/kprohith/nse-stock-analysis/master/ind_nifty500list.csv"
        df_mirror = pd.read_csv(mirror_url)
        if "Symbol" in df_mirror.columns:
            return df_mirror["Symbol"].dropna().unique().tolist()
    except Exception:
        pass

    return [
        "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "BHARTIARTL",
        "LT", "ITC", "AXISBANK", "KOTAKBANK", "TATAMOTORS", "TATASTEEL", "SUNPHARMA",
        "MARUTI", "AMBER", "SUNDARMFIN", "ADANIPORTS", "ADANIENT", "ARE&M", "M&M",
        "BAJFINANCE", "ASIANPAINT", "TITAN", "WIPRO", "HCLTECH", "POWERGRID",
        "NTPC", "COALINDIA", "ONGC", "MCX", "EICHERMOT", "KALYANKJIL", "AEGISCHEM",
        "DLF", "CHOLAFIN", "HAL", "BEL", "TRENT", "ZOMATO", "JIOFIN", "VEDL",
        "HINDALCO", "JSWSTEEL", "SIEMENS", "ABB", "DIVISLAB", "CIPLA", "APOLLOHOSP",
        "DRREDDY", "HEROMOTOCO", "BAJAJ-AUTO", "TVSMOTOR", "POLYCAB", "PERSISTENT",
        "COFORGE", "LTIM", "MPHASIS", "FEDERALBNK", "IDFCFIRSTB", "CANBK", "PNB"
    ]

# ----------------- INDICATOR HELPERS -----------------
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))

def calculate_atr(df, period=14):
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

# ----------------- 1. REFINED INTRADAY SCANNER (Adaptive Momentum & Volatility) -----------------
def analyze_intraday_symbol(sym):
    try:
        ticker = yf.Ticker(f"{sym}.NS")
        df = ticker.history(period="5d", interval="15m")
        if len(df) < 20:
            return None

        df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()
        df['RSI'] = calculate_rsi(df['Close'], 14)
        df['ATR'] = calculate_atr(df, 14)
        df['Vol_MA'] = df['Volume'].rolling(20).mean()

        curr = df.iloc[-1]
        vol_ratio = curr['Volume'] / (curr['Vol_MA'] + 1)
        
        # Trend strength & momentum score
        ema_diff = (curr['EMA9'] - curr['EMA21']) / curr['EMA21']
        score = (ema_diff * 100) + (curr['RSI'] / 10) + (vol_ratio * 2)

        # Condition: Upward bias and healthy non-exhausted RSI
        if curr['Close'] >= curr['EMA9'] and 48 <= curr['RSI'] <= 80:
            entry = round(float(curr['Close']), 2)
            atr = float(curr['ATR']) if not pd.isna(curr['ATR']) and curr['ATR'] > 0 else (entry * 0.012)
            target = round(entry + (1.8 * atr), 2)
            stop_loss = round(entry - (0.9 * atr), 2)
            return (sym, entry, target, stop_loss, score)
    except Exception:
        pass
    return None

# ----------------- 2. REFINED SWING SCANNER (Daily Pullback & EMA Stack) -----------------
def analyze_swing_symbol(sym):
    try:
        ticker = yf.Ticker(f"{sym}.NS")
        df = ticker.history(period="1y", interval="1d")
        if len(df) < 50:
            return None

        df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
        df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
        df['SMA200'] = df['Close'].rolling(200).mean() if len(df) >= 200 else df['EMA50']
        df['RSI'] = calculate_rsi(df['Close'], 14)
        df['ATR'] = calculate_atr(df, 14)

        curr = df.iloc[-1]
        
        dist_to_ema20 = (curr['Close'] - curr['EMA20']) / curr['EMA20']
        score = (curr['EMA20'] / curr['EMA50']) * 10 + (1 / (abs(dist_to_ema20) + 0.01))

        if curr['EMA20'] > curr['EMA50'] and -0.05 <= dist_to_ema20 <= 0.06 and 42 <= curr['RSI'] <= 75:
            entry = round(float(curr['Close']), 2)
            atr = float(curr['ATR']) if not pd.isna(curr['ATR']) and curr['ATR'] > 0 else (entry * 0.025)
            target = round(entry + (2.5 * atr), 2)
            stop_loss = round(min(entry - (1.2 * atr), float(curr['EMA50']) * 0.99), 2)
            return (sym, entry, target, stop_loss, score)
    except Exception:
        pass
    return None

# ----------------- 3. REFINED LONGTERM SCANNER (Structural Stage 2 Base) -----------------
def analyze_longterm_symbol(sym):
    try:
        ticker = yf.Ticker(f"{sym}.NS")
        df = ticker.history(period="1y", interval="1d")
        if len(df) < 100:
            return None

        sma200 = df['Close'].rolling(min(len(df), 200)).mean().iloc[-1]
        close = float(df['Close'].iloc[-1])
        high_52 = df['High'].max()
        dist_52 = (high_52 - close) / high_52

        score = (close / sma200) * 10 + (1 / (dist_52 + 0.01))

        if close >= (sma200 * 0.95) and dist_52 <= 0.28:
            entry = round(close, 2)
            target = round(entry * 1.25, 2)     # +25% upside
            stop_loss = round(entry * 0.90, 2)  # -10% stop loss
            return (sym, entry, target, stop_loss, score)
    except Exception:
        pass
    return None

# ----------------- DATABASE SAVER (Strict Top 10) -----------------
def save_top_10(table_name, candidates):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM {table_name}")
    
    # Sort candidates strictly by best algorithmic score
    candidates.sort(key=lambda x: x[4], reverse=True)
    
    top_10 = candidates[:10]
    for item in top_10:
        cursor.execute(
            f"INSERT OR REPLACE INTO {table_name} (symbol, entry, target, stop_loss, status) VALUES (?, ?, ?, ?, 'ACTIVE')",
            (item[0], item[1], item[2], item[3])
        )
    conn.commit()
    conn.close()
    print(f"[{table_name}] Successfully stored {len(top_10)} trades.")

# ----------------- RUN SCREENER -----------------
def run_screener():
    init_db()
    print("Loading universe from Nifty 50, Midcap 150 & Smallcap 250...")
    symbols = get_nifty_500_universe()
    print(f"Scanning across {len(symbols)} stocks with 20 parallel threads...")

    start_time = time.time()
    with ThreadPoolExecutor(max_workers=20) as executor:
        intraday_results = list(filter(None, executor.map(analyze_intraday_symbol, symbols)))
        swing_results = list(filter(None, executor.map(analyze_swing_symbol, symbols)))
        longterm_results = list(filter(None, executor.map(analyze_longterm_symbol, symbols)))

    save_top_10("intraday_trades", intraday_results)
    save_top_10("swing_trades", swing_results)
    save_top_10("longterm_trades", longterm_results)

    elapsed = round(time.time() - start_time, 2)
    print(f"Complete in {elapsed}s! All three tabs updated with 10 trades.")

if __name__ == "__main__":
    run_screener()
