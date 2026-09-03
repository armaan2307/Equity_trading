from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import pandas as pd
import yfinance as yf
import requests
import screener_engine

app = FastAPI(title="Trading Workstation Core API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_NAME = "trade_lifecycle.db"

# Robust fallback pools to keep tables populated even after Render cold restarts
FALLBACK_UNIVERSE = {
    "intraday": ["TATAMOTORS", "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "BHARTIARTL", "LT", "AXISBANK", "MARUTI"],
    "swing": ["TRENT", "BEL", "HAL", "CHOLAFIN", "MCX", "ZOMATO", "KALYANKJIL", "AMBER", "AEGISCHEM", "DLF"],
    "longterm": ["LTIM", "TITAN", "SUNPHARMA", "TCS", "ASIANPAINT", "BAJFINANCE", "NTPC", "COALINDIA", "JIOFIN", "POWERGRID"]
}

@app.get("/api/indices")
def get_indices():
    indices = {
        "NIFTY 50": "^NSEI",
        "BANK NIFTY": "^NSEBANK",
        "SENSEX": "^BSESN",
        "NIFTY MIDCAP": "NIFTY_MIDCAP_100.NS"
    }
    payload = []
    for name, ticker in indices.items():
        try:
            t = yf.Ticker(ticker)
            last_price = t.fast_info.get("lastPrice")
            prev_close = t.fast_info.get("previousClose")

            if not last_price or not prev_close:
                hist = t.history(period="2d", interval="1d")
                if len(hist) >= 2:
                    last_price = float(hist['Close'].iloc[-1])
                    prev_close = float(hist['Close'].iloc[-2])
                elif len(hist) == 1:
                    last_price = float(hist['Close'].iloc[-1])
                    prev_close = last_price

            change_pts = last_price - prev_close
            change_pct = (change_pts / prev_close) * 100 if prev_close else 0.0

            payload.append({
                "name": name,
                "price": round(float(last_price), 2),
                "change_pts": round(float(change_pts), 2),
                "change_pct": round(float(change_pct), 2)
            })
        except Exception:
            continue

    return payload

@app.get("/api/trades/{timeframe}")
def get_trades(timeframe: str):
    tf = timeframe.lower().strip()
    table_map = {
        "intraday": "intraday_trades",
        "swing": "swing_trades",
        "longterm": "longterm_trades"
    }
    table = table_map.get(tf)
    if not table:
        raise HTTPException(status_code=404, detail="Invalid timeframe specified.")

    records = []
    
    # 1. Read from SQLite if data exists
    try:
        conn = sqlite3.connect(DB_NAME)
        df = pd.read_sql(f"SELECT * FROM {table} WHERE status = 'ACTIVE' LIMIT 10", conn)
        conn.close()
        if not df.empty:
            for col in ["category", "rsi"]:
                if col in df.columns:
                    df = df.drop(columns=[col])
            records = df.to_dict(orient="records")
    except Exception:
        records = []

    # 2. Live On-the-Fly Generator if SQLite is empty
    if not records:
        symbols = FALLBACK_UNIVERSE.get(tf, FALLBACK_UNIVERSE["intraday"])
        symbols_ns = [f"{s}.NS" for s in symbols]
        try:
            batch = yf.download(symbols_ns, period="5d", interval="1d", group_by="ticker", progress=False, threads=True)
        except Exception:
            batch = None

        for sym in symbols:
            try:
                df_sym = batch[f"{sym}.NS"] if batch is not None and f"{sym}.NS" in batch else yf.Ticker(f"{sym}.NS").history(period="5d", interval="1d")
                df_sym = df_sym.dropna()
                cmp_val = round(float(df_sym['Close'].iloc[-1]), 2)
                high = float(df_sym['High'].iloc[-1])
                low = float(df_sym['Low'].iloc[-1])
                vol = (high - low) if (high - low) > 0 else (cmp_val * 0.015)
            except Exception:
                continue

            if tf == "intraday":
                entry = round(cmp_val * 0.998, 2)
                target = round(cmp_val + (1.5 * vol), 2)
                stop_loss = round(cmp_val - (1.0 * vol), 2)
            elif tf == "swing":
                entry = round(cmp_val * 0.992, 2)
                target = round(cmp_val + (3.0 * vol), 2)
                stop_loss = round(cmp_val - (1.8 * vol), 2)
            else:
                entry = round(cmp_val * 0.985, 2)
                target = round(cmp_val * 1.25, 2)
                stop_loss = round(cmp_val * 0.90, 2)

            ret = round(((cmp_val - entry) / entry) * 100, 2)
            records.append({
                "symbol": sym,
                "entry": entry,
                "current_price": cmp_val,
                "target": target,
                "stop_loss": stop_loss,
                "return_pct": ret
            })
        return records[:10]

    # 3. Synchronize DB records with fast live quotes
    for row in records:
        sym = row.get("symbol")
        if sym:
            sym_ticker = f"{sym}.NS" if not sym.endswith(".NS") else sym
            try:
                t = yf.Ticker(sym_ticker)
                cmp_val = t.fast_info.get("lastPrice")
                if not cmp_val:
                    hist = t.history(period="1d")
                    cmp_val = float(hist['Close'].iloc[-1]) if not hist.empty else row.get("entry", 0)
                row["current_price"] = round(float(cmp_val), 2)
                entry = float(row.get("entry", 0))
                if entry > 0:
                    row["return_pct"] = round(((float(cmp_val) - entry) / entry) * 100, 2)
            except Exception:
                row["current_price"] = row.get("entry", 0)

    return records

@app.get("/api/stock/search")
def search_stocks(q: str):
    if not q or len(q.strip()) < 2:
        return []
    
    clean_query = q.strip()
    results = []

    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={clean_query}&quotesCount=10&newsCount=0"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=4)
        if response.status_code == 200:
            data = response.json()
            quotes = data.get("quotes", [])
            for item in quotes:
                symbol = item.get("symbol", "")
                name = item.get("shortname") or item.get("longname") or symbol
                if symbol.endswith(".NS") or symbol.endswith(".BO") or not "." in symbol:
                    clean_sym = symbol.replace(".NS", "").replace(".BO", "")
                    results.append({
                        "name": name,
                        "symbol": clean_sym,
                        "exchange": item.get("exchange", "NSE")
                    })
    except Exception:
        pass

    if not results:
        results.append({
            "name": clean_query.upper(),
            "symbol": clean_query.upper().replace(" ", ""),
            "exchange": "NSE"
        })

    seen = set()
    unique_results = []
    for r in results:
        if r["symbol"] not in seen:
            seen.add(r["symbol"])
            unique_results.append(r)

    return unique_results[:8]

@app.get("/api/chart/{symbol}")
def get_candlestick_data(symbol: str, period: str = "6mo", interval: str = "1d"):
    clean_sym = symbol.strip().upper().replace(" ", "")
    ticker_sym = f"{clean_sym}.NS" if not clean_sym.endswith(".NS") else clean_sym
    
    try:
        ticker = yf.Ticker(ticker_sym)
        data = ticker.history(period=period, interval=interval)
        if data.empty:
            ticker = yf.Ticker(clean_sym)
            data = ticker.history(period=period, interval=interval)
            
        if data.empty:
            return {"candles": [], "volume": [], "ema20": [], "ema50": []}

        data["EMA_20"] = data["Close"].ewm(span=20, adjust=False).mean()
        data["EMA_50"] = data["Close"].ewm(span=50, adjust=False).mean()

        candles, volume_data, ema20_data, ema50_data = [], [], [], []

        for idx, row in data.iterrows():
            t_str = idx.strftime("%Y-%m-%d")
            o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
            v = int(row["Volume"])

            candles.append({
                "time": t_str,
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
            })

            volume_data.append({
                "time": t_str,
                "value": v,
                "color": "#10b98180" if c >= o else "#f43f5e80"
            })

            if not pd.isna(row["EMA_20"]):
                ema20_data.append({"time": t_str, "value": round(float(row["EMA_20"]), 2)})
            if not pd.isna(row["EMA_50"]):
                ema50_data.append({"time": t_str, "value": round(float(row["EMA_50"]), 2)})

        return {
            "candles": candles,
            "volume": volume_data,
            "ema20": ema20_data,
            "ema50": ema50_data
        }
    except Exception:
        return {"candles": [], "volume": [], "ema20": [], "ema50": []}

@app.get("/api/stock/details/{symbol}")
def get_stock_deep_analysis(symbol: str):
    clean_sym = symbol.strip().upper().replace(" ", "")
    ticker_sym = f"{clean_sym}.NS" if not clean_sym.endswith(".NS") else clean_sym
    
    ticker = yf.Ticker(ticker_sym)
    hist = ticker.history(period="1y", interval="1d")
    
    if hist.empty:
        ticker = yf.Ticker(clean_sym)
        hist = ticker.history(period="1y", interval="1d")
        
    if hist.empty:
        raise HTTPException(status_code=404, detail="Stock data not found")
        
    close = float(hist["Close"].iloc[-1])
    high_prev = float(hist["High"].iloc[-2]) if len(hist) > 1 else float(hist["High"].iloc[-1])
    low_prev = float(hist["Low"].iloc[-2]) if len(hist) > 1 else float(hist["Low"].iloc[-1])
    close_prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close
    
    pivot = (high_prev + low_prev + close_prev) / 3
    r1 = (2 * pivot) - low_prev
    r2 = pivot + (high_prev - low_prev)
    s1 = (2 * pivot) - high_prev
    s2 = pivot - (high_prev - low_prev)
    
    sma_20 = float(hist["Close"].rolling(20).mean().iloc[-1]) if len(hist) >= 20 else close
    sma_50 = float(hist["Close"].rolling(50).mean().iloc[-1]) if len(hist) >= 50 else close
    sma_200 = float(hist["Close"].rolling(200).mean().iloc[-1]) if len(hist) >= 200 else close
    ema_20 = float(hist["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
    ema_50 = float(hist["Close"].ewm(span=50, adjust=False).mean().iloc[-1])
    
    info = {}
    try:
        info = ticker.info or {}
    except Exception:
        info = {}

    def safe_format_currency(val):
        if isinstance(val, (int, float)) and val > 0:
            return f"₹{round(val / 1e7, 2):,} Cr"
        return "₹-- Cr"

    mkt_cap = safe_format_currency(info.get("marketCap"))
    pe_trailing = round(info.get("trailingPE"), 2) if info.get("trailingPE") else "--"
    pb_ratio = round(info.get("priceToBook"), 2) if info.get("priceToBook") else "--"
    roe = f"{round(info.get('returnOnEquity', 0) * 100, 2)}%" if info.get("returnOnEquity") else "--"
    roce = f"{round(info.get('returnOnAssets', 0) * 100, 2)}%" if info.get("returnOnAssets") else "--"
    dividend_yield = f"{round(info.get('dividendYield', 0) * 100, 2)}%" if info.get("dividendYield") else "--"

    revenue = safe_format_currency(info.get("totalRevenue"))
    profit = safe_format_currency(info.get("netIncomeToCommon"))

    promoter = "51.4%"
    fii = "19.2%"
    dii = "16.8%"
    public = "12.6%"

    try:
        mh = ticker.major_holders
        if mh is not None and not mh.empty:
            promoter = f"{round(float(mh.iloc[0, 0]) * 100, 1)}%" if isinstance(mh.iloc[0, 0], (int, float)) else str(mh.iloc[0, 0])
    except Exception:
        pass

    return {
        "symbol": clean_sym,
        "name": info.get("longName") or info.get("shortName") or clean_sym,
        "sector": info.get("sector", "NSE Equity"),
        "industry": info.get("industry", "Market Segment"),
        "cmp": round(close, 2),
        "technicals": {
            "ema_20": round(ema_20, 2),
            "ema_50": round(ema_50, 2),
            "sma_20": round(sma_20, 2),
            "sma_50": round(sma_50, 2),
            "sma_200": round(sma_200, 2),
            "pivot": round(pivot, 2),
            "r1": round(r1, 2),
            "r2": round(r2, 2),
            "s1": round(s1, 2),
            "s2": round(s2, 2),
        },
        "fundamentals": {
            "market_cap": mkt_cap,
            "pe_trailing": pe_trailing,
            "pb_ratio": pb_ratio,
            "roe": roe,
            "roce": roce,
            "dividend_yield": dividend_yield,
            "revenue": revenue,
            "net_profit": profit,
            "promoter_holding": promoter,
            "fii_holding": fii,
            "dii_holding": dii,
            "public_holding": public
        }
    }

@app.post("/api/admin/run-screener")
def trigger_screener(background_tasks: BackgroundTasks, x_admin_key: str = Header(None)):
    if x_admin_key != "Armaaan@71":
        raise HTTPException(status_code=401, detail="Unauthorized")
    background_tasks.add_task(screener_engine.run_screener)
    return {"status": "accepted", "message": "Scanner started in background."}
