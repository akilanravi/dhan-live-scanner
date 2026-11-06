import os
import json
import time
import requests
import pandas as pd
from dhanhq import dhanhq, DhanContext

CLIENT_ID = "1114099742"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJ1c2VyUmVnaW9uIjoiUjEiLCJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzkzOTgxMTI5LCJpYXQiOjE3OTM4OTQ3MjksInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTE0MDk5NzQyIn0.D-2qsSidi7Negv-jSV7-6Wu2w26AC14mESGv9AIPg708AgG9mA4NH_36S62SMGNXpZKncpyjDvoqa6VtOFdM7w"

dhan = dhanhq(DhanContext(CLIENT_ID, ACCESS_TOKEN))

WATCHLIST = {
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

def calculate_cpr_and_strike(symbol, ltp, high, low, close, step):
    pivot = (high + low + close) / 3.0
    bc = (high + low) / 2.0
    tc = (2 * pivot) - bc
    cpr_tc, cpr_bc = max(tc, bc), min(tc, bc)

    signal = "BUY CALL" if ltp > cpr_tc else ("BUY PUT" if ltp < cpr_bc else "NEUTRAL")
    strike_type = "CE" if "CALL" in signal else ("PE" if "PUT" in signal else "CE")
    atm = int(round(ltp / step) * step)

    return {
        "symbol": symbol,
        "ltp": round(float(ltp), 2),
        "style": "INTRADAY",
        "signal": signal,
        "strike": f"{atm} {strike_type}",
        "sl": round(ltp * (0.992 if strike_type == "CE" else 1.008), 1),
        "target": round(ltp * (1.016 if strike_type == "CE" else 0.984), 1),
        "cpr_tc": round(cpr_tc, 1),
        "cpr_p": round(pivot, 1),
        "cpr_bc": round(cpr_bc, 1)
    }

def main():
    results = []
    print("Fetching live market data...")
    for sym, meta in WATCHLIST.items():
        try:
            # First attempt: Intraday historical minute candles
            res = dhan.intraday_minute_data(
                security_id=meta["id"],
                exchange_segment=meta["seg"],
                instrument_type="INDEX" if "IDX" in meta["seg"] else "EQUITY"
            )
            
            ltp, high, low, close = None, None, None, None
            if res and isinstance(res, dict) and "data" in res and res["data"].get("close"):
                raw = res["data"]
                ltp = raw["close"][-1]
                high = max(raw["high"][-5:])
                low = min(raw["low"][-5:])
                close = raw["close"][-2]
            else:
                # Fallback: Live LTP quote API when market is closed or no candles returned
                quote = dhan.get_ohlc_data(securities={meta["seg"]: [int(meta["id"])]})
                if quote and "data" in quote:
                    d = list(quote["data"].values())[0]
                    ltp = d.get("last_price", 0)
                    high = d.get("ohlc", {}).get("high", ltp * 1.01)
                    low = d.get("ohlc", {}).get("low", ltp * 0.99)
                    close = d.get("ohlc", {}).get("close", ltp)

            if ltp and ltp > 0:
                item = calculate_cpr_and_strike(sym, ltp, high, low, close, meta["step"])
                results.append(item)
        except Exception as e:
            print(f"Error on {sym}: {e}")
        time.sleep(0.5)

    output = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "total_scanned": len(results),
        "data": results
    }
    with open("live_data.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"Saved {len(results)} symbols.")

if __name__ == "__main__":
    main()
