import base64
import hashlib
import hmac
import json
import math
import os
import struct
import sys
import time
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

import requests

# Dhan credentials are read only from GitHub Actions Secrets.
CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "").strip()
ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
# OPTIONAL: lets the scanner create a fresh 24-hour token on every run.
DHAN_PIN = os.getenv("DHAN_PIN", "").strip()
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET", "").replace(" ", "").strip()

API_BASE = "https://api.dhan.co/v2"
AUTH_BASE = "https://auth.dhan.co"
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


def totp_now(secret, digits=6, step=30):
    """RFC 6238 TOTP using only the standard library (no pyotp needed)."""
    padded = secret.upper() + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    counter = struct.pack(">Q", int(time.time() // step))
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def resolve_access_token():
    """Dhan access tokens last only 24 hours. If PIN + TOTP secret are stored as
    secrets, generate a fresh token each run; otherwise use DHAN_ACCESS_TOKEN."""
    if DHAN_PIN and DHAN_TOTP_SECRET:
        try:
            resp = requests.post(
                f"{AUTH_BASE}/app/generateAccessToken",
                params={"dhanClientId": CLIENT_ID, "pin": DHAN_PIN, "totp": totp_now(DHAN_TOTP_SECRET)},
                timeout=REQUEST_TIMEOUT,
            )
            data = resp.json()
            token = data.get("accessToken")
            if token:
                print("Generated a fresh Dhan access token using PIN + TOTP.")
                return token
            print(f"Token generation failed: {str(data)[:200]}")
        except Exception as exc:
            print(f"Token generation failed: {type(exc).__name__}")
    return ACCESS_TOKEN


def api_post(path, body):
    headers = {
        "access-token": ACCESS_TOKEN,   # replaced by resolve_access_token() in main()
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
    if response.status_code >= 400:
        # Dhan puts the real reason (e.g. DH-901 invalid token, 806 Data API not
        # subscribed) in the body. The body never contains our credentials.
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("Dhan returned an unexpected response format")
    if result.get("status") == "failure":
        # Do not print request headers or credentials.
        raise RuntimeError(str(result.get("remarks") or result.get("message") or "Dhan API reported failure"))
    return result


def to_ist_datetime(raw, now):
    """Dhan timestamps may be Unix epoch (s or ms) or seconds since 1980-01-01.
    Pick whichever interpretation lands near 'now'."""
    stamp = float(raw)
    if stamp > 10_000_000_000:
        stamp /= 1000.0
    for offset in (0, 315532800):
        candidate = datetime.fromtimestamp(stamp + offset, IST)
        if now - timedelta(days=400) <= candidate <= now + timedelta(days=2):
            return candidate
    raise ValueError("timestamp outside expected range")


def fetch_intraday_candles(symbol, meta, now):
    start = (now - timedelta(days=10)).replace(hour=9, minute=0, second=0, microsecond=0)
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
            candle_time = to_ist_datetime(timestamps[i], now)
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
    global ACCESS_TOKEN
    now = datetime.now(IST)

    if not CLIENT_ID or not (ACCESS_TOKEN or (DHAN_PIN and DHAN_TOTP_SECRET)):
        print("ERROR: DHAN_CLIENT_ID and a token (DHAN_ACCESS_TOKEN, or DHAN_PIN + DHAN_TOTP_SECRET) are missing from GitHub Secrets.")
        previous = load_previous()
        previous.update({"status": "error", "errors": [{"symbol": "ALL", "error": "Dhan secrets missing in GitHub Actions"}]})
        write_payload(previous)
        return 1

    ACCESS_TOKEN = resolve_access_token()
    market_open = (
        now.weekday() < 5
        and dt_time(9, 15) <= now.time().replace(tzinfo=None) <= dt_time(15, 30)
    )

    rows = []
    errors = []
    print(f"Starting Dhan scan at {now:%Y-%m-%d %H:%M:%S %Z} (market_open={market_open})")

    for symbol, meta in WATCHLIST.items():
        try:
            row = analyse_symbol(symbol, meta, now)
            rows.append(row)
            print(f"{symbol}: {row['signal']} | LTP {row['ltp']}")
        except Exception as exc:
            message = str(exc)
            for secret in (ACCESS_TOKEN, DHAN_PIN, DHAN_TOTP_SECRET):
                if secret:
                    message = message.replace(secret, "[REDACTED]")
            errors.append({"symbol": symbol, "error": message[:240]})
            print(f"{symbol}: scan failed - {message[:180]}")
        time.sleep(0.4)  # stay well inside Dhan's data-API rate limit

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
