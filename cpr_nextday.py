"""Evening CPR scan: picks for the NEXT trading day across all NSE F&O stocks.
Uses daily candles of the last completed session (free Yahoo data)."""
import csv
import io
import json
import sys
import time
from datetime import datetime, timedelta, time as dt_time
from urllib.parse import quote

import requests
import yahoo

IST = yahoo.IST
OUT = "cpr_nextday.json"
LOT_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
INDICES = {"NIFTY 50": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "NIFTY_FIN_SERVICE.NS", "SENSEX": "^BSESN"}
NARROW, ULTRA = 0.35, 0.20          # CPR width as % of pivot
TIME_BUDGET = 600                   # seconds

# Used only if NSE's live F&O list cannot be downloaded.
FALLBACK = """360ONE ABB ABCAPITAL ADANIENSOL ADANIENT ADANIGREEN ADANIPORTS ALKEM AMBER AMBUJACEM ANGELONE APLAPOLLO
APOLLOHOSP ASHOKLEY ASIANPAINT ASTRAL AUBANK AUROPHARMA AXISBANK BAJAJ-AUTO BAJAJFINSV BAJAJHLDNG BAJFINANCE BANDHANBNK
BANKBARODA BANKINDIA BDL BEL BHARATFORG BHARTIARTL BHEL BIOCON BLUESTARCO BOSCHLTD BPCL BRITANNIA BSE CAMS CANBK CDSL
CGPOWER CHOLAFIN CIPLA COALINDIA COFORGE COLPAL CONCOR CROMPTON CUMMINSIND DABUR DALBHARAT DELHIVERY DIVISLAB DIXON DLF
DMART DRREDDY EICHERMOT ETERNAL EXIDEIND FEDERALBNK FORTIS GAIL GLENMARK GMRAIRPORT GODREJCP GODREJPROP GRASIM HAL HAVELLS
HCLTECH HDFCAMC HDFCBANK HDFCLIFE HEROMOTOCO HFCL HINDALCO HINDPETRO HINDUNILVR HINDZINC HUDCO ICICIBANK ICICIGI ICICIPRULI
IDEA IDFCFIRSTB IEX IGL IIFL INDHOTEL INDIANB INDIGO INDUSINDBK INDUSTOWER INFY INOXWIND IOC IRCTC IREDA IRFC ITC JINDALSTEL
JIOFIN JSWENERGY JSWSTEEL JUBLFOOD KALYANKJIL KAYNES KEI KFINTECH KOTAKBANK KPITTECH LAURUSLABS LICHSGFIN LICI LODHA LT LTF
LTIM LUPIN M&M MANAPPURAM MANKIND MARICO MARUTI MAXHEALTH MAZDOCK MCX MFSL MOTHERSON MPHASIS MUTHOOTFIN NATIONALUM NAUKRI NBCC
NCC NESTLEIND NHPC NMDC NTPC NUVAMA NYKAA OBEROIRLTY OFSS OIL ONGC PAGEIND PATANJALI PAYTM PERSISTENT PETRONET PFC PGEL
PHOENIXLTD PIDILITIND PIIND PNB PNBHOUSING POLICYBZR POLYCAB POWERGRID POWERINDIA PPLPHARMA PRESTIGE RBLBANK RECLTD RELIANCE
RVNL SAIL SAMMAANCAP SBICARD SBILIFE SBIN SHREECEM SHRIRAMFIN SIEMENS SOLARINDS SONACOMS SRF SUNPHARMA SUPREMEIND SUZLON
SWIGGY SYNGENE TATACONSUM TATAELXSI TATAPOWER TATASTEEL TATATECH TCS TECHM TIINDIA TITAGARH TITAN TMPV TORNTPHARM TORNTPOWER
TRENT TVSMOTOR ULTRACEMCO UNIONBANK UNITDSPR UNOMINDA UPL VBL VEDL VOLTAS WAAREEENER WIPRO YESBANK ZYDUSLIFE""".split()


def load_universe():
    """Returns ({symbol: lot_size}, source_label). Prefers NSE's live F&O list."""
    try:
        r = requests.get(LOT_URL, headers=yahoo.HEADERS, timeout=25)
        r.raise_for_status()
        lots = {}
        for row in csv.reader(io.StringIO(r.text)):
            row = [c.strip() for c in row]
            if len(row) < 3 or not row[1] or row[1].upper() in ("SYMBOL",) or row[1] in INDEX_SYMBOLS:
                continue
            lot = next((int(c) for c in row[2:] if c.isdigit()), None)
            if lot:
                lots[row[1]] = lot
        if len(lots) > 100:
            return lots, "NSE live F&O list"
    except Exception as exc:
        print(f"NSE list unavailable ({type(exc).__name__}); using built-in list")
    return {s: None for s in FALLBACK}, "built-in F&O list (may be slightly out of date)"


def ema(vals, n):
    if len(vals) < n:
        return None
    k, e = 2 / (n + 1), sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def cpr(h, l, c):
    p = (h + l + c) / 3
    bc, tc0 = (h + l) / 2, 2 * p - (h + l) / 2
    return p, max(tc0, bc), min(tc0, bc)


def analyse(name, ticker, lot, now):
    candles, _ = yahoo.chart(ticker, "1d", "3mo")
    # drop today's candle if the session is still running
    if candles and candles[-1]["date"] == now.date() and now.time() < dt_time(15, 35):
        candles = candles[:-1]
    if len(candles) < 22:
        raise RuntimeError("not enough daily history")
    d, y = candles[-1], candles[-2]
    closes = [c["close"] for c in candles]
    p, tc, bc = cpr(d["high"], d["low"], d["close"])      # tomorrow's CPR
    p0, tc0, bc0 = cpr(y["high"], y["low"], y["close"])   # today's CPR
    width = (tc - bc) / p * 100
    cls = "Ultra narrow" if width < ULTRA else "Narrow" if width < NARROW else "Normal" if width < 0.7 else "Wide"
    if bc > tc0:
        rel = "Higher (bullish)"
    elif tc < bc0:
        rel = "Lower (bearish)"
    else:
        rel = "Overlap-up" if p > p0 else "Overlap-down"
    close, e20 = d["close"], ema(closes, 20)
    turnover = sum(x["close"] * x["volume"] for x in candles[-20:]) / 20 / 1e7   # avg daily value, Rs crore
    pos = "Above CPR" if close > tc else "Below CPR" if close < bc else "Inside CPR"
    bull = (rel.startswith("Higher") or pos == "Above CPR") and close > e20
    bear = (rel.startswith("Lower") or pos == "Below CPR") and close < e20
    rng = d["high"] - d["low"]
    r1, s1, r2, s2 = 2 * p - d["low"], 2 * p - d["high"], p + rng, p - rng
    plan = "WAIT"
    if width < NARROW and bull:
        plan = "BUY CALL WATCH"
    elif width < NARROW and bear:
        plan = "BUY PUT WATCH"
    row = {"symbol": name, "lot": lot, "close": round(close, 2),
           "chg_pct": round((close / y["close"] - 1) * 100, 2),
           "tc": round(tc, 2), "p": round(p, 2), "bc": round(bc, 2),
           "width_pct": round(width, 3), "cpr_class": cls, "cpr_vs_today": rel, "position": pos,
           "ema20": round(e20, 2), "avg_turnover_cr": round(turnover, 1), "plan": plan,
           "trigger": None, "sl": None, "t1": None, "t2": None}
    if plan == "BUY CALL WATCH":
        row.update(trigger=f"Above {row['tc']}", sl=round(bc, 2), t1=round(r1, 2), t2=round(r2, 2))
    elif plan == "BUY PUT WATCH":
        row.update(trigger=f"Below {row['bc']}", sl=round(tc, 2), t1=round(s1, 2), t2=round(s2, 2))
    return row, d["date"]


def next_weekday(day):
    n = day + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n


def main():
    now = datetime.now(IST)
    lots, source = load_universe()
    start = time.time()
    indices, stocks, errors, base = [], [], [], None
    jobs = [(n, t, None, True) for n, t in INDICES.items()] + \
           [(s, f"{quote(s)}.NS", lot, False) for s, lot in sorted(lots.items())]
    for name, ticker, lot, is_index in jobs:
        if time.time() - start > TIME_BUDGET:
            errors.append({"symbol": name, "error": "skipped: time budget reached"})
            continue
        try:
            row, base_day = analyse(name, ticker, lot, now)
            base = max(base, base_day) if base else base_day
            (indices if is_index else stocks).append(row)
        except Exception as exc:
            errors.append({"symbol": name, "error": str(exc)[:120]})
        time.sleep(0.5)
    order = {"BUY CALL WATCH": 0, "BUY PUT WATCH": 0, "WAIT": 1}
    stocks.sort(key=lambda r: (order[r["plan"]], r["width_pct"]))
    if not stocks:
        print("No stocks analysed; keeping previous file")
        return 1
    liquid = [r["symbol"] for r in sorted(stocks, key=lambda r: -r["avg_turnover_cr"])[:100]]
    payload = {"generated_at": now.strftime("%Y-%m-%d %H:%M:%S IST"),
               "based_on_session": str(base), "for_session": str(next_weekday(base)),
               "universe": source, "count": len(stocks),
               "call_watch": sum(r["plan"] == "BUY CALL WATCH" for r in stocks),
               "put_watch": sum(r["plan"] == "BUY PUT WATCH" for r in stocks),
               "narrow": sum(r["width_pct"] < NARROW for r in stocks),
               "liquid_top": liquid, "indices": indices, "stocks": stocks, "errors": errors,
               "note": "Next session assumes no exchange holiday. Levels are underlying price levels, not option premiums."}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"Done: {len(stocks)} stocks, {payload['call_watch']} call / {payload['put_watch']} put, {len(errors)} errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
