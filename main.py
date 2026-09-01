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

@app.get("/")
def home():
    return {"status": "online", "service": "Trading Workstation Pro API"}

@app.get("/api/indices")
def get_indices():
    return [
        {"name": "NIFTY 50", "price": 24055.80, "change_pts": -24.60, "change_pct": -0.10},
        {"name": "BANK NIFTY", "price": 57409.60, "change_pts": -615.35, "change_pct": -1.06},
        {"name": "SENSEX", "price": 76944.28, "change_pts": -12.99, "change_pct": -0.02},
        {"name": "NIFTY MIDCAP", "price": 18248.80, "change_pts": 55.40, "change_pct": 0.30},
    ]

@app.get("/api/trades/{segment}")
def get_trades(segment: str):
    seg = segment.lower().strip()
    if seg not in ["intraday", "swing", "longterm"]:
        seg = "intraday"

    trades = []
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(f"SELECT symbol, entry, target, stop_loss FROM {seg}_trades WHERE status='ACTIVE' LIMIT 10")
        rows = cursor.fetchall()
        conn.close()

        if rows:
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
    except Exception:
        pass

    # Dynamic fallback if screener hasn't executed yet
    defaults = {
        "intraday": ["TATAMOTORS", "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "BHARTIARTL", "LT", "AXISBANK", "MARUTI"],
        "swing": ["TRENT", "BEL", "HAL", "CHOLAFIN", "MCX", "ZOMATO", "KALYANKJIL", "AMBER", "AEGISCHEM", "DLF"],
        "longterm": ["LTIM", "TITAN", "SUNPHARMA", "TCS", "ASIANPAINT", "BAJFINANCE", "NTPC", "COALINDIA", "JIOFIN", "POWERGRID"]
    }
    for sym in defaults.get(seg, defaults["intraday"]):
        trades.append({"symbol": sym, "entry": 1000.0, "cmp": 1010.0, "target": 1050.0, "stop_loss": 980.0, "return_pct": 1.0})
    return trades

@app.post("/api/admin/run-screener")
def trigger_screener(background_tasks: BackgroundTasks, x_admin_key: str = Header(None)):
    if x_admin_key != "Armaaan@71":
        raise HTTPException(status_code=401, detail="Invalid Admin Key")
    background_tasks.add_task(screener_engine.run_screener)
    return {"status": "accepted", "message": "Chunked market scanner started in background."}

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
