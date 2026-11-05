import os
import json
import time
import requests
import pandas as pd
from dhanhq import dhanhq, DhanContext

CLIENT_ID = os.getenv("1114099742", "").strip()
ACCESS_TOKEN = os.getenv("eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJ1c2VyUmVnaW9uIjoiUjEiLCJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzkzOTgxMTI5LCJpYXQiOjE3OTM4OTQ3MjksInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTE0MDk5NzQyIn0.D-2qsSidi7Negv-jSV7-6Wu2w26AC14mESGv9AIPg708AgG9mA4NH_36S62SMGNXpZKncpyjDvoqa6VtOFdM7w", "").strip()

print(f"Debug: CLIENT_ID length = {len(CLIENT_ID)}, ACCESS_TOKEN length = {len(ACCESS_TOKEN)}")

if not CLIENT_ID or not ACCESS_TOKEN:
    print(f"Error: Missing Dhan API credentials! (CLIENT_ID present: {bool(CLIENT_ID)}, ACCESS_TOKEN present: {bool(ACCESS_TOKEN)})")
    exit(1)

dhan = dhanhq(DhanContext(CLIENT_ID, ACCESS_TOKEN))

def get_fno_symbols():
    url = "https://archives.nseindia.com/content/fo/fo_mktlots.csv"
    headers = {"User-Agent": "Mozilla/5.0"}
    symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY"]
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            for line in res.text.split("\n")[1:]:
                parts = line.split(",")
                if len(parts) > 1:
                    s = parts[1].strip()
                    if s and s != "SYMBOL" and s not in symbols:
                        symbols.append(s)
    except Exception as e:
        print(f"Failed to fetch NSE list: {e}")
    return symbols

def get_dhan_mappings(fno_symbols):
    print("Downloading Dhan scrip master...")
    url = "https://images.dhan.co/api-data/api-scrip-master.csv"
    try:
        df = pd.read_csv(url, low_memory=False)
        eq_df = df[(df['SEM_EXM_EXCH_ID'] == 'NSE') & (df['SEM_INSTRUMENT_NAME'].isin(['EQUITY', 'INDEX']))]
        
        mapping = {}
        for sym in fno_symbols:
            match = eq_df[eq_df['SEM_TRADING_SYMBOL'].str.startswith(sym, na=False)]
            if not match.empty:
                row = match.iloc[0]
                sec_id = str(row['SEM_SMST_SECURITY_ID'])
                seg = "IDX_I" if row['SEM_INSTRUMENT_NAME'] == 'INDEX' else "NSE_EQ"
                mapping[sym] = {"id": sec_id, "seg": seg}
        return mapping
    except Exception as e:
        print(f"Fallback to default list: {e}")
        return {
            "NIFTY": {"id": "13", "seg": "IDX_I"},
            "BANKNIFTY": {"id": "25", "seg": "IDX_I"},
            "FINNIFTY": {"id": "27", "seg": "IDX_I"},
            "RELIANCE": {"id": "2885", "seg": "NSE_EQ"},
            "HDFCBANK": {"id": "1333", "seg": "NSE_EQ"},
            "ICICIBANK": {"id": "4963", "seg": "NSE_EQ"},
            "INFY": {"id": "1594", "seg": "NSE_EQ"},
            "TCS": {"id": "11536", "seg": "NSE_EQ"},
            "SBIN": {"id": "3045", "seg": "NSE_EQ"},
            "TATAMOTORS": {"id": "3456", "seg": "NSE_EQ"}
        }

def calculate_cpr_and_strike(symbol, df):
    if len(df) < 5:
        return None

    prev_high = df['high'].iloc[-2]
    prev_low = df['low'].iloc[-2]
    prev_close = df['close'].iloc[-2]
    current_price = df['close'].iloc[-1]

    # Central Pivot Range (CPR)
    pivot = (prev_high + prev_low + prev_close) / 3.0
    bc = (prev_high + prev_low) / 2.0
    tc = (2 * pivot) - bc

    cpr_tc = max(tc, bc)
    cpr_bc = min(tc, bc)

    ema9 = df['close'].ewm(span=9, adjust=False).mean().iloc[-1]
    ema21 = df['close'].ewm(span=21, adjust=False).mean().iloc[-1]

    signal = "NEUTRAL"
    strike_type = "CE"
    style = "INTRADAY"

    if current_price > cpr_tc and ema9 > ema21:
        signal = "BUY CALL"
        strike_type = "CE"
        style = "SCALP"
    elif current_price < cpr_bc and ema9 < ema21:
        signal = "BUY PUT"
        strike_type = "PE"
        style = "SCALP"

    if "NIFTY" in symbol:
        step = 50 if "BANK" not in symbol else 100
    elif current_price > 2000:
        step = 20
    elif current_price > 1000:
        step = 10
    else:
        step = 5

    atm_strike = int(round(current_price / step) * step)

    return {
        "symbol": symbol,
        "ltp": round(float(current_price), 2),
        "style": style,
        "signal": signal,
        "strike": f"{atm_strike} {strike_type}",
        "sl": round(current_price * 0.992 if strike_type == "CE" else current_price * 1.008, 1),
        "target": round(current_price * 1.016 if strike_type == "CE" else current_price * 0.984, 1),
        "cpr_tc": round(cpr_tc, 1),
        "cpr_p": round(pivot, 1),
        "cpr_bc": round(cpr_bc, 1)
    }

def main():
    fno_list = get_fno_symbols()
    mapping = get_dhan_mappings(fno_list)
    results = []

    print(f"Scanning {len(mapping)} F&O instruments...")
    for sym, meta in mapping.items():
        try:
            data = dhan.intraday_minute_data(
                security_id=meta["id"],
                exchange_segment=meta["seg"],
                instrument_type="INDEX" if meta["seg"] == "IDX_I" else "EQUITY"
            )
            if data and isinstance(data, dict) and "data" in data and "close" in data["data"]:
                raw = data["data"]
                if len(raw.get("close", [])) >= 5:
                    df = pd.DataFrame({
                        "high": raw["high"],
                        "low": raw["low"],
                        "close": raw["close"]
                    })
                    analysis = calculate_cpr_and_strike(sym, df)
                    if analysis:
                        results.append(analysis)
        except Exception:
            pass
        time.sleep(0.7)  # Safe rate limit pacing

    # Output live quotes JSON
    output_data = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "total_scanned": len(results),
        "data": results
    }

    with open("live_data.json", "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"Done! Scanned and saved {len(results)} active instruments to live_data.json")

if __name__ == "__main__":
    main()
