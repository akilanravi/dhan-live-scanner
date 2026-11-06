import json
import math
import sys
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

import requests

# Data source: Yahoo Finance (free, no API key). Yahoo's NSE data can be delayed
# by several minutes, so treat signals as indicative, not tick-by-tick.
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
IST = ZoneInfo("Asia/Kolkata")
OUTPUT_FILE = "live_data.json"
REQUEST_TIMEOUT = 25

# "yahoo" = Yahoo ticker, "step" = strike gap used for the indicative ATM strike.
WATCHLIST = {
    "NIFTY 50": {"yahoo": "^NSEI", "step": 50},
    "BANKNIFTY": {"yahoo": "^NSEBANK", "step": 100},
    "FINNIFTY": {"yahoo": "NIFTY_FIN_SERVICE.NS", "step": 50},
    "RELIANCE": {"yahoo": "RELIANCE.NS", "step": 20},
    "HDFCBANK": {"yahoo": "HDFCBANK.NS", "step": 10},
    "ICICIBANK": {"yahoo": "ICICIBANK.NS", "step": 10},
    "INFY": {"yahoo": "INFY.NS", "step": 20},
    "TCS": {"yahoo": "TCS.NS", "step": 20},
    "SBIN": {"yahoo": "SBIN.NS", "step": 5},
    "TMPV (Tata Motors PV)": {"yahoo": "TMPV.NS", "step": 5},
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


def fetch_intraday_candles(symbol, meta, now):
    url = YAHOO_URL.format(ticker=meta["yahoo"])
    params = {"interval": "5m", "range": "10d", "includePrePost": "false"}
    last_error = None
    for attempt in range(3):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if response.status_code == 429:
                raise RuntimeError("Yahoo rate-limited this request (HTTP 429)")
            if response.status_code >= 400:
                raise RuntimeError(f"Yahoo HTTP {response.status_code}: {response.text[:120]}")
            result = (response.json().get("chart") or {}).get("result")
            if not result:
                raise RuntimeError("Yahoo returned no data for this ticker")
            break
        except Exception as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(str(last_error))

    result = result[0]
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    highs, lows, closes = quote.get("high") or [], quote.get("low") or [], quote.get("close") or []
    volumes = quote.get("volume") or [0] * len(stamps)

    candles = []
    for i in range(min(len(stamps), len(highs), len(lows), len(closes))):
        if highs[i] is None or lows[i] is None or closes[i] is None:
            continue  # Yahoo leaves gaps as null
        t = datetime.fromtimestamp(stamps[i], IST)
        candles.append({
            "time": t, "date": t.date(),
            "high": float(highs[i]), "low": float(lows[i]), "close": float(closes[i]),
            "volume": float((volumes[i] if i < len(volumes) else 0) or 0),
        })
    if len(candles) < 25:
        raise RuntimeError(f"Not enough 5-minute candles returned ({len(candles)})")
    return candles


def analyse_symbol(symbol, meta, now):
    candles = fetch_intraday_candles(symbol, meta, now)
    # Group candles by exchange-local date. The "current" session is the most
    # recent one that has candles (today during market hours, otherwise the last
    # trading day). CPR always uses the session BEFORE it.
    sessions = {}
    for candle in candles:
        sessions.setdefault(candle["date"], []).append(candle)

    dates = sorted(sessions)
    if len(dates) < 2:
        raise RuntimeError("Need at least two trading sessions of candles")
    current_date, previous_date = dates[-1], dates[-2]
    today_candles = sorted(sessions[current_date], key=lambda x: x["time"])
    previous_session = sorted(sessions[previous_date], key=lambda x: x["time"])
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


def load_previous():
    try:
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    now = datetime.now(IST)

    market_open = (
        now.weekday() < 5
        and dt_time(9, 15) <= now.time().replace(tzinfo=None) <= dt_time(15, 30)
    )

    rows = []
    errors = []
    print(f"Starting scan at {now:%Y-%m-%d %H:%M:%S %Z} (market_open={market_open})")

    for symbol, meta in WATCHLIST.items():
        try:
            row = analyse_symbol(symbol, meta, now)
            rows.append(row)
            print(f"{symbol}: {row['signal']} | LTP {row['ltp']}")
        except Exception as exc:
            message = str(exc)
            errors.append({"symbol": symbol, "error": message[:240]})
            print(f"{symbol}: scan failed - {message[:180]}")
        time.sleep(1.0)  # be polite to Yahoo

    if not rows:
        # Keep the last good rows visible instead of blanking the website.
        previous = load_previous()
        previous.update({"status": "error", "errors": errors, "market_open": market_open,
                         "checked_at": now.strftime("%Y-%m-%d %H:%M:%S IST")})
        write_payload(previous)
        print("Scan finished: 0 symbols returned.")
        return 1

    payload = {
        "status": "ok" if not errors else "partial",
        "market_open": market_open,
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
