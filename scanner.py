import json
import math
import sys
import time
from datetime import datetime, time as dt_time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from zoneinfo import ZoneInfo

import yahoo

# Data source: Yahoo Finance (free, no API key). Yahoo's NSE data can be delayed
# by several minutes, so treat signals as indicative, not tick-by-tick.
IST = ZoneInfo("Asia/Kolkata")
OUTPUT_FILE = "live_data.json"

# Header tiles (GIFT Nifty is shown by the TradingView ticker on the page).
INDEX_TILES = {"Nifty 50": "^NSEI", "Bank Nifty": "^NSEBANK", "Sensex": "^BSESN"}

INDEX_WATCH = {
    "NIFTY 50": {"yahoo": "^NSEI", "step": 50},
    "BANKNIFTY": {"yahoo": "^NSEBANK", "step": 100},
    "FINNIFTY": {"yahoo": "NIFTY_FIN_SERVICE.NS", "step": 50},
    "SENSEX": {"yahoo": "^BSESN", "step": 100},
}
# Used until the first evening scan has ranked F&O stocks by traded value.
FALLBACK_LIQUID = """RELIANCE HDFCBANK ICICIBANK SBIN INFY TCS BHARTIARTL AXISBANK KOTAKBANK LT ITC BAJFINANCE MARUTI SUNPHARMA
TATASTEEL M&M HCLTECH ADANIENT NTPC TMPV ETERNAL ONGC POWERGRID COALINDIA HINDALCO JSWSTEEL WIPRO TECHM TITAN ULTRACEMCO
ASIANPAINT BAJAJFINSV HINDUNILVR NESTLEIND DRREDDY CIPLA EICHERMOT HEROMOTOCO TVSMOTOR BPCL IOC VEDL ADANIPORTS ADANIGREEN
JIOFIN LICI SBILIFE HDFCLIFE PNB BANKBARODA CANBK IDFCFIRSTB YESBANK FEDERALBNK INDUSINDBK TRENT HAL BEL BHEL RVNL IRFC PFC
RECLTD DLF GODREJPROP SAIL NMDC TATAPOWER SUZLON IDEA HINDPETRO GAIL INDIGO DIXON POLYCAB PERSISTENT COFORGE LTIM MPHASIS
LUPIN AUROPHARMA ZYDUSLIFE BIOCON MANKIND APOLLOHOSP MAXHEALTH""".split()


def step_for(price):
    """Indicative strike gap by price level (real gaps vary by stock)."""
    for limit, step in ((100, 2.5), (250, 5), (1000, 10), (2000, 20), (4000, 50)):
        if price < limit:
            return step
    return 100


def build_watchlist():
    symbols, source = FALLBACK_LIQUID, "built-in list"
    try:
        with open("cpr_nextday.json", encoding="utf-8") as f:
            ranked = json.load(f).get("liquid_top") or []
        if len(ranked) >= 50:
            symbols, source = ranked, "top 100 F&O by 20-day traded value"
    except Exception:
        pass
    watch = dict(INDEX_WATCH)
    for sym in symbols[:100]:
        watch[sym] = {"yahoo": f"{quote(sym)}.NS", "step": None}
    return watch, source


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
    candles, _ = yahoo.chart(meta["yahoo"], "5m", "10d")
    if len(candles) < 25:
        raise RuntimeError(f"Not enough 5-minute candles returned ({len(candles)})")
    return candles


def index_tile(name, ticker):
    """Live tile for an index: last price + change vs previous session close."""
    candles, meta = yahoo.chart(ticker, "5m", "5d")
    days = sorted({c["date"] for c in candles})
    if len(days) < 2:
        raise RuntimeError("not enough sessions")
    prev_close = [c for c in candles if c["date"] == days[-2]][-1]["close"]
    last = candles[-1]
    ltp = float(meta.get("regularMarketPrice") or last["close"])
    return {"name": name, "ltp": round(ltp, 2), "prev_close": round(prev_close, 2),
            "change": round(ltp - prev_close, 2), "change_pct": round((ltp / prev_close - 1) * 100, 2),
            "as_of": last["time"].strftime("%Y-%m-%d %H:%M IST")}


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

    step = float(meta["step"] or step_for(ltp))
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
        "chg_pct": round((ltp / prev_close - 1) * 100, 2),
        "strength": round(abs(ema9 - ema21) / ltp * 100 + abs(ltp - pivot) / ltp * 100, 3),
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
    indices = []
    for name, ticker in INDEX_TILES.items():
        try:
            indices.append(index_tile(name, ticker))
        except Exception as exc:
            errors.append({"symbol": name, "error": str(exc)[:200]})
    print(f"Starting scan at {now:%Y-%m-%d %H:%M:%S %Z} (market_open={market_open})")

    watchlist, universe = build_watchlist()

    def work(item):
        symbol, meta = item
        try:
            return analyse_symbol(symbol, meta, now), None
        except Exception as exc:
            return None, {"symbol": symbol, "error": str(exc)[:160]}

    with ThreadPoolExecutor(max_workers=4) as pool:
        for row, err in pool.map(work, watchlist.items()):
            if row:
                rows.append(row)
            else:
                errors.append(err)
    print(f"Scanned {len(rows)} of {len(watchlist)} symbols ({universe}), {len(errors)} errors")
    rank = {"BUY CALL": 0, "BUY PUT": 0, "NEUTRAL": 1}
    rows.sort(key=lambda r: (rank[r["signal"]], -r["strength"]))

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
        "indices": indices,
        "universe": universe,
        "bulls": sum(r["signal"] == "BUY CALL" for r in rows),
        "bears": sum(r["signal"] == "BUY PUT" for r in rows),
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
