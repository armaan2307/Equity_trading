from fastapi import FastAPI, BackgroundTasks, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import yfinance as yf
import pandas as pd
import screener_engine

app = FastAPI(title="Trading Workstation Pro API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_NAME = "trade_lifecycle.db"

# Fallback default setups
DEFAULT_TRADES = {
    "intraday": [
        ("TATAMOTORS", 985.50, 1015.00, 970.00),
        ("RELIANCE", 2980.00, 3040.00, 2945.00),
        ("HDFCBANK", 1640.20, 1675.00, 1620.00),
        ("ICICIBANK", 1215.00, 1240.00, 1200.00),
        ("INFY", 1880.00, 1920.00, 1855.00),
        ("SBIN", 815.00, 835.00, 802.00),
        ("BHARTIARTL", 1540.00, 1575.00, 1515.00),
        ("LT", 3620.00, 3700.00, 3570.00),
        ("AXISBANK", 1180.00, 1210.00, 1160.00),
        ("MARUTI", 12450.00, 12750.00, 12250.00),
    ],
    "swing": [
        ("TRENT", 6950.00, 7450.00, 6700.00),
        ("BEL", 295.00, 325.00, 280.00),
        ("HAL", 4680.00, 5100.00, 4450.00),
        ("CHOLAFIN", 1420.00, 1560.00, 1350.00),
        ("MCX", 5850.00, 6400.00, 5550.00),
        ("ZOMATO", 265.00, 298.00, 248.00),
        ("KALYANKJIL", 690.00, 765.00, 650.00),
        ("AMBER", 4250.00, 4700.00, 4020.00),
        ("AEGISCHEM", 830.00, 920.00, 785.00),
        ("DLF", 845.00, 930.00, 805.00),
    ],
    "longterm": [
        ("LTIM", 6100.00, 7800.00, 5400.00),
        ("TITAN", 3450.00, 4300.00, 3050.00),
        ("SUNPHARMA", 1820.00, 2300.00, 1600.00),
        ("TCS", 4420.00, 5400.00, 3950.00),
        ("ASIANPAINT", 3120.00, 3900.00, 2750.00),
        ("BAJFINANCE", 7250.00, 9100.00, 6450.00),
        ("NTPC", 390.00, 500.00, 340.00),
        ("COALINDIA", 495.00, 620.00, 430.00),
        ("JIOFIN", 340.00, 460.00, 290.00),
        ("POWERGRID", 325.00, 410.00, 285.00),
    ]
}

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    for seg in ["intraday", "swing", "longterm"]:
        table = f"{seg}_trades"
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
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        if cursor.fetchone()[0] == 0:
            cursor.executemany(
                f"INSERT OR REPLACE INTO {table} (symbol, entry, target, stop_loss) VALUES (?, ?, ?, ?)",
                DEFAULT_TRADES[seg]
            )
    conn.commit()
    conn.close()

init_db()

@app.get("/")
def home():
    return {"status": "online", "service": "Trading Workstation Pro API"}

@app.get("/api/indices")
def get_indices():
    symbols = {
        "^NSEI": "NIFTY 50",
        "^NSEBANK": "BANK NIFTY",
        "^BSESN": "SENSEX",
        "NIFTY_MIDCAP_100.NS": "NIFTY MIDCAP"
    }
    results = []
    for sym, name in symbols.items():
        try:
            t = yf.Ticker(sym)
            df = t.history(period="2d")
            if len(df) >= 2:
                curr = float(df['Close'].iloc[-1])
                prev = float(df['Close'].iloc[-2])
                chg = curr - prev
                pct = (chg / prev) * 100
                results.append({"name": name, "price": round(curr, 2), "change_pts": round(chg, 2), "change_pct": round(pct, 2)})
            elif len(df) == 1:
                curr = float(df['Close'].iloc[-1])
                results.append({"name": name, "price": round(curr, 2), "change_pts": 0.0, "change_pct": 0.0})
        except Exception:
            pass

    if not results:
        results = [
            {"name": "NIFTY 50", "price": 24055.80, "change_pts": -24.60, "change_pct": -0.10},
            {"name": "BANK NIFTY", "price": 57409.60, "change_pts": -615.35, "change_pct": -1.06},
            {"name": "SENSEX", "price": 76944.28, "change_pts": -12.99, "change_pct": -0.02},
            {"name": "NIFTY MIDCAP", "price": 18248.80, "change_pts": 55.40, "change_pct": 0.30},
        ]
    return results

@app.get("/api/trades/{segment}")
def get_trades(segment: str):
    if segment not in ["intraday", "swing", "longterm"]:
        raise HTTPException(status_code=400, detail="Invalid segment")

    table = f"{segment}_trades"
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(f"SELECT symbol, entry, target, stop_loss FROM {table} WHERE status='ACTIVE' LIMIT 10")
    rows = cursor.fetchall()
    conn.close()

    # Fallback to defaults if empty
    if not rows:
        rows = DEFAULT_TRADES.get(segment, [])

    trades = []
    for r in rows:
        sym, entry, target, sl = r
        try:
            t = yf.Ticker(f"{sym}.NS")
            hist = t.history(period="1d")
            cmp_val = round(float(hist['Close'].iloc[-1]), 2) if len(hist) > 0 else entry
        except Exception:
            cmp_val = entry
        ret = round(((cmp_val - entry) / entry) * 100, 2)
        trades.append({"symbol": sym, "entry": entry, "cmp": cmp_val, "target": target, "stop_loss": sl, "return_pct": ret})
    return trades

@app.post("/api/admin/run-screener")
def trigger_screener(background_tasks: BackgroundTasks, x_admin_key: str = Header(None)):
    if x_admin_key != "Armaaan@71":
        raise HTTPException(status_code=401, detail="Invalid Admin Key")
    background_tasks.add_task(screener_engine.run_screener)
    return {"status": "accepted", "message": "Scanner started in background."}

@app.get("/api/chart/{symbol}")
def get_chart(symbol: str, interval: str = "1d"):
    period_map = {"5m": "5d", "15m": "5d", "1h": "1mo", "1d": "1y", "1mo": "5y"}
    period = period_map.get(interval, "1y")
    t = yf.Ticker(f"{symbol}.NS")
    df = t.history(period=period, interval=interval)
    if df.empty:
        t = yf.Ticker(symbol)
        df = t.history(period=period, interval=interval)
        if df.empty:
            return {"candles": [], "ema20": [], "ema50": []}

    candles = []
    for idx, row in df.iterrows():
        t_val = int(idx.timestamp()) if hasattr(idx, 'timestamp') else int(pd.to_datetime(idx).timestamp())
        candles.append({"time": t_val, "open": round(float(row['Open']), 2), "high": round(float(row['High']), 2), "low": round(float(row['Low']), 2), "close": round(float(row['Close']), 2)})

    df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
    ema20, ema50 = [], []
    for idx, row in df.iterrows():
        t_val = int(idx.timestamp()) if hasattr(idx, 'timestamp') else int(pd.to_datetime(idx).timestamp())
        if not pd.isna(row['EMA20']): ema20.append({"time": t_val, "value": round(float(row['EMA20']), 2)})
        if not pd.isna(row['EMA50']): ema50.append({"time": t_val, "value": round(float(row['EMA50']), 2)})

    return {"candles": candles, "ema20": ema20, "ema50": ema50}

@app.get("/api/stock/search")
def search_stock(q: str = ""):
    if not q or len(q.strip()) < 2:
        return []
    q_lower = q.strip().upper()
    predefined = [
        {"name": "Reliance Industries Ltd", "symbol": "RELIANCE"},
        {"name": "Tata Consultancy Services", "symbol": "TCS"},
        {"name": "HDFC Bank Ltd", "symbol": "HDFCBANK"},
        {"name": "ICICI Bank Ltd", "symbol": "ICICIBANK"},
        {"name": "Infosys Ltd", "symbol": "INFY"},
        {"name": "State Bank of India", "symbol": "SBIN"},
        {"name": "Bharti Airtel Ltd", "symbol": "BHARTIARTL"},
        {"name": "Larsen & Toubro Ltd", "symbol": "LT"},
        {"name": "ITC Ltd", "symbol": "ITC"},
        {"name": "Trent Ltd", "symbol": "TRENT"},
        {"name": "Zomato Ltd", "symbol": "ZOMATO"},
        {"name": "Kalyan Jewellers India Ltd", "symbol": "KALYANKJIL"},
        {"name": "Amber Enterprises India Ltd", "symbol": "AMBER"},
        {"name": "Aegis Logistics Ltd", "symbol": "AEGISCHEM"},
        {"name": "Bharat Electronics Ltd", "symbol": "BEL"},
        {"name": "Hindustan Aeronautics Ltd", "symbol": "HAL"},
    ]
    return [s for s in predefined if q_lower in s["symbol"] or q_lower in s["name"].upper()]

@app.get("/api/stock/details/{symbol}")
def stock_details(symbol: str):
    try:
        t = yf.Ticker(f"{symbol}.NS")
        info = t.info or {}
        df = t.history(period="1y", interval="1d")
        cmp_val = round(float(info.get("currentPrice") or info.get("regularMarketPrice") or (df['Close'].iloc[-1] if not df.empty else 0)), 2)
        
        # Technicals
        ema20 = round(float(df['Close'].ewm(span=20).mean().iloc[-1]), 2) if len(df) >= 20 else cmp_val
        ema50 = round(float(df['Close'].ewm(span=50).mean().iloc[-1]), 2) if len(df) >= 50 else cmp_val
        sma20 = round(float(df['Close'].rolling(20).mean().iloc[-1]), 2) if len(df) >= 20 else cmp_val
        sma50 = round(float(df['Close'].rolling(50).mean().iloc[-1]), 2) if len(df) >= 50 else cmp_val
        sma200 = round(float(df['Close'].rolling(200).mean().iloc[-1]), 2) if len(df) >= 200 else cmp_val
        
        high = float(df['High'].iloc[-1]) if not df.empty else cmp_val
        low = float(df['Low'].iloc[-1]) if not df.empty else cmp_val
        close = float(df['Close'].iloc[-1]) if not df.empty else cmp_val
        pivot = round((high + low + close) / 3, 2)
        r1 = round((2 * pivot) - low, 2)
        r2 = round(pivot + (high - low), 2)
        s1 = round((2 * pivot) - high, 2)
        s2 = round(pivot - (high - low), 2)

        def format_inr(val):
            if not val or pd.isna(val): return "N/A"
            v = float(val)
            if v >= 1e12: return f"₹{round(v / 1e12, 2)}T"
            if v >= 1e7: return f"₹{round(v / 1e7, 2)} Cr"
            return f"₹{round(v, 2)}"

        return {
            "symbol": symbol,
            "name": info.get("longName", symbol),
            "sector": info.get("sector", "Diversified"),
            "industry": info.get("industry", "Equity"),
            "cmp": cmp_val,
            "technicals": {
                "ema_20": ema20, "ema_50": ema50, "sma_20": sma20, "sma_50": sma50, "sma_200": sma200,
                "pivot": pivot, "r1": r1, "r2": r2, "s1": s1, "s2": s2
            },
            "fundamentals": {
                "market_cap": format_inr(info.get("marketCap")),
                "pe_trailing": str(round(float(info.get("trailingPE", 0)), 2)) if info.get("trailingPE") else "24.5",
                "pb_ratio": str(round(float(info.get("priceToBook", 0)), 2)) if info.get("priceToBook") else "3.2",
                "roe": f"{round(float(info.get('returnOnEquity', 0.15)) * 100, 2)}%",
                "roce": f"{round(float(info.get('returnOnAssets', 0.12)) * 100, 2)}%",
                "dividend_yield": f"{round(float(info.get('dividendYield', 0.01)) * 100, 2)}%",
                "revenue": format_inr(info.get("totalRevenue", 15000000000)),
                "net_profit": format_inr(info.get("netIncomeToCommon", 2500000000)),
                "promoter_holding": f"{round(float(info.get('heldPercentInsiders', 0.51)) * 100, 2)}%",
                "fii_holding": f"{round(float(info.get('heldPercentInstitutions', 0.22)) * 100, 2)}%",
                "dii_holding": "14.50%",
                "public_holding": "12.50%"
            }
        }
    except Exception:
        return None
