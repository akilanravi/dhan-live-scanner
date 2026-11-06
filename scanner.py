import json
import math
import sys
import time
from datetime import datetime, timedelta, time as dt_time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from zoneinfo import ZoneInfo

import yahoo

# Data source: Yahoo Finance (free, no API key). Yahoo's NSE data can be delayed
# by several minutes, so treat signals as indicative, not tick-by-tick.
IST = ZoneInfo("Asia/Kolkata")
OUTPUT_FILE = "live_data.json"

# Header tiles (GIFT Nifty is shown by the TradingView ticker on the page).
NARROW_PCT = 0.35               # narrow CPR = width under 0.35% of pivot
ST_PERIOD, ST_MULT = 10, 2.0     # Supertrend (10, 2)

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


def fetch_intraday_candles(symbol, meta, now):
    candles, _ = yahoo.chart(meta["yahoo"], "5m", "10d")
    if len(candles) < 40:
        raise RuntimeError(f"Not enough 5-minute candles returned ({len(candles)})")
    return candles


def index_tile(name, ticker, now):
    """Header tile: live price from Yahoo's quote meta, change vs previous close."""
    daily, meta = yahoo.chart(ticker, "1d", "5d")
    if len(daily) < 2:
        raise RuntimeError("not enough daily data")
    ltp = float(meta.get("regularMarketPrice") or daily[-1]["close"])
    today_bar = daily[-1]["date"] == now.date() or abs(daily[-1]["close"] - ltp) <= ltp * 0.0005
    prev_close = daily[-2]["close"] if today_bar else daily[-1]["close"]
    return {"name": name, "ltp": round(ltp, 2), "prev_close": round(prev_close, 2),
            "change": round(ltp - prev_close, 2), "change_pct": round((ltp / prev_close - 1) * 100, 2),
            "as_of": now.strftime("%Y-%m-%d %H:%M IST")}


def ema_series(values, period):
    out, k, e = [], 2.0 / (period + 1), None
    for i, v in enumerate(values):
        if i < period - 1:
            out.append(None)
        else:
            e = sum(values[:period]) / period if i == period - 1 else v * k + e * (1 - k)
            out.append(e)
    return out


def rsi_last(closes, period=14):
    if len(closes) <= period:
        return None
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(gains[:period]) / period, sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def supertrend_last(candles, period=10, mult=2.0):
    """Supertrend(10,2). Returns (direction, line): +1 bullish, -1 bearish."""
    if len(candles) <= period + 1:
        return None, None
    trs = []
    for i, c in enumerate(candles):
        pc = candles[i - 1]["close"] if i else c["close"]
        trs.append(max(c["high"] - c["low"], abs(c["high"] - pc), abs(c["low"] - pc)))
    atr = [None] * len(candles)
    atr[period - 1] = sum(trs[:period]) / period
    for i in range(period, len(candles)):
        atr[i] = (atr[i - 1] * (period - 1) + trs[i]) / period
    fu = fl = line = None
    direction = 1
    for i in range(period - 1, len(candles)):
        c, pc = candles[i], candles[i - 1]["close"] if i else candles[i]["close"]
        mid = (c["high"] + c["low"]) / 2
        bu, bl = mid + mult * atr[i], mid - mult * atr[i]
        fu = bu if fu is None or bu < fu or pc > fu else fu
        fl = bl if fl is None or bl > fl or pc < fl else fl
        if direction == 1 and c["close"] < fl:
            direction = -1
        elif direction == -1 and c["close"] > fu:
            direction = 1
        line = fl if direction == 1 else fu
    return direction, line


def bollinger_last(closes, period=20, mult=2.0):
    if len(closes) < period:
        return None, None
    w = closes[-period:]
    mean = sum(w) / period
    sd = math.sqrt(sum((x - mean) ** 2 for x in w) / period)
    return mean + mult * sd, mean - mult * sd


def analyse_symbol(symbol, meta, now):
    candles = sorted(fetch_intraday_candles(symbol, meta, now), key=lambda x: x["time"])
    dates = sorted({c["date"] for c in candles})
    if len(dates) < 2:
        raise RuntimeError("Need at least two trading sessions of candles")
    prev = [c for c in candles if c["date"] == dates[-2]]
    ph, pl, pc = max(c["high"] for c in prev), min(c["low"] for c in prev), prev[-1]["close"]

    pivot = (ph + pl + pc) / 3.0                      # today's CPR/pivots come from the last full session
    bc_raw = (ph + pl) / 2.0
    tc_raw = 2.0 * pivot - bc_raw
    tc, bc = max(tc_raw, bc_raw), min(tc_raw, bc_raw)
    cpr_width = (tc - bc) / pivot * 100
    r1, s1 = 2 * pivot - pl, 2 * pivot - ph

    # Only fully closed 5-minute candles count.
    closed = [c for c in candles if c["time"] + timedelta(minutes=5) <= now] or candles
    closes = [c["close"] for c in closed]
    last = closed[-1]
    ltp = last["close"]

    e9, e21 = ema_series(closes, 9)[-1], ema_series(closes, 21)[-1]
    rsi = rsi_last(closes, 14)
    st_dir, st_line = supertrend_last(closed, ST_PERIOD, ST_MULT)
    bb_up, bb_lo = bollinger_last(closes, 20, 2.0)
    if None in (e9, e21, rsi, st_dir, bb_up):
        raise RuntimeError("Not enough candles for indicators")

    narrow = cpr_width < NARROW_PCT
    call_checks = {"ema": e9 > e21, "st": st_dir == 1, "rsi": rsi > 60, "pivot": ltp > r1, "bb": ltp > bb_up, "cpr": narrow}
    put_checks = {"ema": e9 < e21, "st": st_dir == -1, "rsi": rsi < 40, "pivot": ltp < s1, "bb": ltp < bb_lo, "cpr": narrow}
    call_score, put_score = sum(call_checks.values()), sum(put_checks.values())
    signal = "BUY CALL" if call_score == 6 else "BUY PUT" if put_score == 6 else "NEUTRAL"

    step = float(meta["step"] or step_for(ltp))
    atm = int(math.floor(ltp / step + 0.5) * step)
    call_side = call_score >= put_score
    risk = abs(ltp - st_line)
    return {
        "symbol": symbol, "ltp": round(ltp, 2), "chg_pct": round((ltp / pc - 1) * 100, 2),
        "signal": signal, "call_score": call_score, "put_score": put_score,
        "call_checks": call_checks, "put_checks": put_checks,
        "strike_ce": f"{atm} CE", "strike_pe": f"{atm} PE",
        "sl": round(st_line, 2), "target_ce": round(ltp + 2 * risk, 2), "target_pe": round(ltp - 2 * risk, 2),
        "rsi": round(rsi, 1), "supertrend": round(st_line, 2), "ema9": round(e9, 2), "ema21": round(e21, 2),
        "r1": round(r1, 2), "s1": round(s1, 2), "bb_upper": round(bb_up, 2), "bb_lower": round(bb_lo, 2),
        "cpr_tc": round(tc, 2), "cpr_p": round(pivot, 2), "cpr_bc": round(bc, 2), "cpr_width": round(cpr_width, 3),
        "candle_time": last["time"].strftime("%Y-%m-%d %H:%M IST"),
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
            indices.append(index_tile(name, ticker, now))
        except Exception as exc:
            errors.append({"symbol": f"{name} (index tile)", "error": str(exc)[:200]})
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
    rows.sort(key=lambda r: -max(r["call_score"], r["put_score"]))

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
        "call_signals": sum(r["signal"] == "BUY CALL" for r in rows),
        "put_signals": sum(r["signal"] == "BUY PUT" for r in rows),
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
