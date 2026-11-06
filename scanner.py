import os
import json
import time
import requests
import pandas as pd
from dhanhq import dhanhq, DhanContext

CLIENT_ID = "1114099742"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJ1c2VyUmVnaW9uIjoiUjEiLCJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzkzOTgxMTI5LCJpYXQiOjE3OTM4OTQ3MjksInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTE0MDk5NzQyIn0.D-2qsSidi7Negv-jSV7-6Wu2w26AC14mESGv9AIPg708AgG9mA4NH_36S62SMGNXpZKncpyjDvoqa6VtOFdM7w"

dhan = dhanhq(DhanContext(CLIENT_ID, ACCESS_TOKEN))

# Key F&O Watchlist with Dhan Security IDs and strike step sizes
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

def calculate_levels(symbol, ltp, high, low, close, step):
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
    print("Beginning Dhan scan...")

    for sym, meta in WATCHLIST.items():
        try:
            # Request latest OHLC data via Dhan API
            quote = dhan.get_ohlc_data(securities={meta["seg"]: [int(meta["id"])]})
            
            ltp = None
            if quote and isinstance(quote, dict) and "data" in quote:
                data_dict = quote["data"]
                first_key = list(data_dict.keys())[0] if data_dict else None
                if first_key:
                    item_data = data_dict[first_key]
                    ltp = item_data.get("last_price")
                    ohlc = item_data.get("ohlc", {})
                    high = ohlc.get("high", ltp * 1.01 if ltp else 100)
                    low = ohlc.get("low", ltp * 0.99 if ltp else 100)
                    close = ohlc.get("close", ltp if ltp else 100)

            # Fallback to intraday minute candle if quote did not yield an LTP
            if not ltp:
                res = dhan.intraday_minute_data(
                    security_id=meta["id"],
                    exchange_segment=meta["seg"],
                    instrument_type="INDEX" if "IDX" in meta["seg"] else "EQUITY"
                )
                if res and isinstance(res, dict) and "data" in res and res["data"].get("close"):
                    c_data = res["data"]
                    ltp = c_data["close"][-1]
                    high = max(c_data["high"][-5:])
                    low = min(c_data["low"][-5:])
                    close = c_data["close"][-2]

            if ltp and ltp > 0:
                row = calculate_levels(sym, ltp, high, low, close, meta["step"])
                results.append(row)
                print(f"Scanned {sym}: LTP={ltp}, Signal={row['signal']}")
        except Exception as err:
            print(f"Error scanning {sym}: {err}")
        time.sleep(0.3)

    output = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "total_scanned": len(results),
        "data": results
    }

    with open("live_data.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"Completed scan. Saved {len(results)} records to live_data.json.")

if __name__ == "__main__":
    main()
