import json
import math
import os
import sys
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

import requests

# Dhan credentials are read only from GitHub Actions Secrets.
CLIENT_ID = os.getenv("1114099742", "").strip()
ACCESS_TOKEN = os.getenv("2ad43154", "").strip()

API_BASE = "https://api.dhan.co/v2"
IST = ZoneInfo("Asia/Kolkata")
OUTPUT_FILE = "live_data.json"
REQUEST_TIMEOUT = 25

# Verify security IDs and strike steps against the latest Dhan instrument list
# before relying on the watchlist. These are carried over from the supplied project.
WATCHLIST = {
    "NIFTY 50": {"id": "13", "segment": "IDX_I", "instrument": "INDEX", "step": 50},
    "BANKNIFTY": {"id": "25", "segment": "IDX_I", "instrument": "INDEX", "step": 100},
    "FINNIFTY": {"id": "27", "segment": "IDX_I", "instrument": "INDEX", "step": 50},
    "RELIANCE": {"id": "2885", "segment": "NSE_EQ", "instrument": "EQUITY", "step": 20},
    "HDFCBANK": {"id": "1333", "segment": "NSE_EQ", "instrument": "EQUITY", "step": 10},
    "ICICIBANK": {"id": "4963", "segment": "NSE_EQ", "instrument": "EQUITY", "step": 10},
    "INFY": {"id": "1594", "segment": "NSE_EQ", "instrument": "EQUITY", "step": 20},
    "TCS": {"id": "11536", "segment": "NSE_EQ", "instrument": "EQUITY", "step": 20},
    "SBIN": {"id": "3045", "segment": "NSE_EQ", "instrument": "EQUITY", "step": 5},
    "TATAMOTORS": {"id": "3456", "segment": "NSE_EQ", "instrument": "EQUITY", "step": 5},
}


def write_payload(payload):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def ema(values, period):
    """Return the final EMA value using the standard recursive calculation."""
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1.0)
    value = sum(values[:period]) / period
    for price in values[period:]:
        value = (price * alpha) + (value * (1.0 - alpha))
    return value


def api_post(path, body):
    headers = {
        "access-token": ACCESS_TOKEN,
        "client-id": CLIENT_ID,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    response = requests.post(
        f"{API_BASE}{path}",
        headers=headers,
        json=body,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("Dhan returned an unexpected response format")
    if result.get("status") == "failure":
        # Do not print request headers or credentials.
        raise RuntimeError(str(result.get("remarks") or result.get("message") or "Dhan API reported failure"))
    return result


def fetch_intraday_candles(symbol, meta, now):
    start = (now - timedelta(days=7)).replace(hour=9, minute=0, second=0, microsecond=0)
    body = {
        "securityId": str(meta["id"]),
        "exchangeSegment": meta["segment"],
        "instrument": meta["instrument"],
        "interval": 5,
        "oi": False,
        "fromDate": start.strftime("%Y-%m-%d %H:%M:%S"),
        "toDate": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    response = api_post("/charts/intraday", body)
    data = response.get("data") or {}

    required = ("high", "low", "close")
    if any(not isinstance(data.get(key), list) for key in required):
        raise RuntimeError("Dhan response is missing candle arrays")

    timestamps = data.get("start_Time") or data.get("timestamp") or data.get("startTime")
    if not isinstance(timestamps, list):
        raise RuntimeError("Dhan response is missing candle timestamps")

    highs, lows, closes = data["high"], data["low"], data["close"]
    volumes = data.get("volume") or [0] * len(closes)
    count = min(len(timestamps), len(highs), len(lows), len(closes), len(volumes))
    if count < 25:
        raise RuntimeError(f"Not enough 5-minute candles returned ({count})")

    candles = []
    for i in range(count):
        try:
            stamp = float(timestamps[i])
            if stamp > 10_000_000_000:  # tolerate millisecond timestamps
                stamp /= 1000.0
            candle_time = datetime.fromtimestamp(stamp, IST)
            candles.append({
                "time": candle_time,
                "date": candle_time.date(),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "close": float(closes[i]),
                "volume": float(volumes[i] or 0),
            })
        except (TypeError, ValueError, OverflowError):
            continue

    if not candles:
        raise RuntimeError("No valid candles in Dhan response")
    return candles


def analyse_symbol(symbol, meta, now):
    candles = fetch_intraday_candles(symbol, meta, now)
    today = now.date()

    # Group candles by exchange-local date. CPR uses the last completed session,
    # never the current day's developing high/low/close.
    sessions = {}
    for candle in candles:
        sessions.setdefault(candle["date"], []).append(candle)

    previous_dates = sorted(day for day in sessions if day < today)
    today_candles = sorted(sessions.get(today, []), key=lambda x: x["time"])
    if not previous_dates:
        raise RuntimeError("Previous completed session not present in candle history")
    if not today_candles:
        raise RuntimeError("No candles found for today's session")

    previous_session = sessions[previous_dates[-1]]
    prev_high = max(c["high"] for c in previous_session)
    prev_low = min(c["low"] for c in previous_session)
    prev_close = previous_session[-1]["close"]

    pivot = (prev_high + prev_low + prev_close) / 3.0
    bc_raw = (prev_high + prev_low) / 2.0
    tc_raw = (2.0 * pivot) - bc_raw
    cpr_tc = max(tc_raw, bc_raw)
    cpr_bc = min(tc_raw, bc_raw)

    # Use completed 5-minute candles from the current session plus recent
    # history for a stable EMA at the start of the session.
    all_closes = [c["close"] for c in sorted(candles, key=lambda x: x["time"]) if c["time"] <= today_candles[-1]["time"]]
    ema9 = ema(all_closes, 9)
    ema21 = ema(all_closes, 21)
    if ema9 is None or ema21 is None:
        raise RuntimeError("Not enough candles to calculate EMA 9/21")

    ltp = today_candles[-1]["close"]
    signal = "NEUTRAL"
    option_side = "-"
    if ltp > cpr_tc and ema9 > ema21:
        signal, option_side = "BUY CALL", "CE"
    elif ltp < cpr_bc and ema9 < ema21:
        signal, option_side = "BUY PUT", "PE"

    step = float(meta["step"])
    atm = int(math.floor((ltp / step) + 0.5) * step)
    # These SL/target figures are underlying-price reference levels, NOT option premiums.
    if signal == "BUY CALL":
        stop_loss = round(ltp * 0.992, 2)
        target = round(ltp * 1.016, 2)
        strike = f"{atm} CE"
    elif signal == "BUY PUT":
        stop_loss = round(ltp * 1.008, 2)
        target = round(ltp * 0.984, 2)
        strike = f"{atm} PE"
    else:
        stop_loss = None
        target = None
        strike = "-"

    return {
        "symbol": symbol,
        "ltp": round(ltp, 2),
        "style": "INTRADAY",
        "signal": signal,
        "strike": strike,
        "option_side": option_side,
        "sl": stop_loss,
        "target": target,
        "cpr_tc": round(cpr_tc, 2),
        "cpr_p": round(pivot, 2),
        "cpr_bc": round(cpr_bc, 2),
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "candle_time": today_candles[-1]["time"].strftime("%Y-%m-%d %H:%M:%S IST"),
    }


def main():
    now = datetime.now(IST)

    if not CLIENT_ID or not ACCESS_TOKEN:
        print("ERROR: DHAN_CLIENT_ID and/or DHAN_ACCESS_TOKEN GitHub Secrets are missing.")
        return 1

    # GitHub cron is UTC and can start outside the NSE session. Do not replace
    # the last valid market snapshot with an empty pre-market/after-market scan.
    market_open = dt_time(9, 15)
    market_close = dt_time(15, 30)
    if now.weekday() >= 5 or not (market_open <= now.time().replace(tzinfo=None) <= market_close):
        print(f"Outside NSE session; skipping scan at {now:%Y-%m-%d %H:%M:%S %Z}.")
        return 0

    rows = []
    errors = []
    print(f"Starting Dhan scan at {now:%Y-%m-%d %H:%M:%S %Z}")

    for symbol, meta in WATCHLIST.items():
        try:
            row = analyse_symbol(symbol, meta, now)
            rows.append(row)
            print(f"{symbol}: {row['signal']} | LTP {row['ltp']}")
        except Exception as exc:
            # Only safe error text is written; credentials are never logged.
            message = str(exc).replace(ACCESS_TOKEN, "[REDACTED]") if ACCESS_TOKEN else str(exc)
            errors.append({"symbol": symbol, "error": message[:240]})
            print(f"{symbol}: scan failed - {message[:180]}")

    payload = {
        "status": "ok" if rows and not errors else ("partial" if rows else "error"),
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "timezone": "Asia/Kolkata",
        "total_scanned": len(rows),
        "signals_count": sum(1 for row in rows if row["signal"] != "NEUTRAL"),
        "data": rows,
        "errors": errors,
        "note": "SL and target are underlying spot-price reference levels, not option premium levels.",
    }
    write_payload(payload)

    print(f"Scan finished: {len(rows)} symbols returned, {len(errors)} errors.")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
