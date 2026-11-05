import asyncio
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dhanhq import dhanhq, DhanContext
import pandas as pd

CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "1114099742")
ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJ1c2VyUmVnaW9uIjoiUjEiLCJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzkzOTgxMTI5LCJpYXQiOjE3OTM4OTQ3MjksInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTE0MDk5NzQyIn0.D-2qsSidi7Negv-jSV7-6Wu2w26AC14mESGv9AIPg708AgG9mA4NH_36S62SMGNXpZKncpyjDvoqa6VtOFdM7w")

dhan_context = DhanContext(CLIENT_ID, ACCESS_TOKEN)
dhan = dhanhq(dhan_context)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Start with top high-beta F&O symbols to stay well below rate limits
FNO_MAPPING = {
    "NIFTY 50": {"id": "13", "seg": "IDX_I", "step": 50},
    "BANKNIFTY": {"id": "25", "seg": "IDX_I", "step": 100},
    "FINNIFTY": {"id": "27", "seg": "IDX_I", "step": 50},
    "RELIANCE": {"id": "2885", "seg": "NSE_EQ", "step": 20},
    "HDFCBANK": {"id": "1333", "seg": "NSE_EQ", "step": 10},
    "ICICIBANK": {"id": "4963", "seg": "NSE_EQ", "step": 10},
    "INFY": {"id": "1594", "seg": "NSE_EQ", "step": 10},
    "TCS": {"id": "11536", "seg": "NSE_EQ", "step": 20},
    "SBIN": {"id": "3045", "seg": "NSE_EQ", "step": 5},
    "TATAMOTORS": {"id": "3456", "seg": "NSE_EQ", "step": 5},
}

market_data = {}

def calculate_cpr_and_ema(df, step):
    prev_h, prev_l, prev_c = df['high'].iloc[-2], df['low'].iloc[-2], df['close'].iloc[-2]
    curr_c = df['close'].iloc[-1]

    pivot = (prev_h + prev_l + prev_c) / 3.0
    bc = (prev_h + prev_l) / 2.0
    tc = (2 * pivot) - bc

    ema9 = df['close'].ewm(span=9, adjust=False).mean().iloc[-1]
    ema21 = df['close'].ewm(span=21, adjust=False).mean().iloc[-1]

    signal = "NEUTRAL"
    strike_type = "CE"
    if curr_c > max(tc, bc) and ema9 > ema21:
        signal = "BUY CALL"
        strike_type = "CE"
    elif curr_c < min(tc, bc) and ema9 < ema21:
        signal = "BUY PUT"
        strike_type = "PE"

    atm = int(round(curr_c / step) * step)
    return {
        "ltp": round(float(curr_c), 2),
        "signal": signal,
        "strike": f"{atm} {strike_type}",
        "cpr_tc": round(max(tc, bc), 1),
        "cpr_p": round(pivot, 1),
        "cpr_bc": round(min(tc, bc), 1),
        "sl": round(curr_c * (0.992 if strike_type == "CE" else 1.008), 1),
        "tgt": round(curr_c * (1.016 if strike_type == "CE" else 0.984), 1)
    }

@app.get("/api/quotes")
async def get_quotes():
    return list(market_data.values())

async def fetch_stock_candles_with_retry(meta, max_retries=3):
    """Executes call with backoff in case Dhan returns 429"""
    for attempt in range(max_retries):
        try:
            res = dhan.intraday_minute_data(
                security_id=meta["id"],
                exchange_segment=meta["seg"],
                instrument_type="INDEX" if "IDX" in meta["seg"] else "EQUITY"
            )
            # If Dhan returns an error payload indicating rate limit, back off
            if isinstance(res, dict) and res.get("status") == "failure":
                if "rate" in str(res).lower() or "limit" in str(res).lower():
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
            return res
        except Exception as e:
            if "429" in str(e):
                await asyncio.sleep(2 * (attempt + 1))
            else:
                break
    return None

async def scanner_task():
    while True:
        for sym, meta in FNO_MAPPING.items():
            res = await fetch_stock_candles_with_retry(meta)
            
            if res and isinstance(res, dict) and "data" in res and "close" in res["data"]:
                raw = res["data"]
                if len(raw.get("close", [])) >= 5:
                    df = pd.DataFrame({"high": raw["high"], "low": raw["low"], "close": raw["close"]})
                    analysis = calculate_cpr_and_ema(df, meta["step"])
                    market_data[sym] = {"symbol": sym, **analysis}

            # 0.8s gap ensures requests never exceed 1-2 calls/sec
            await asyncio.sleep(0.8)

        # Rest 5 seconds after completing one full scan across all assets
        await asyncio.sleep(5)

@app.on_event("startup")
async def startup():
    asyncio.create_task(scanner_task())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
