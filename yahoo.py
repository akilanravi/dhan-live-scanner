"""Shared Yahoo Finance helper (free, no API key)."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

IST = ZoneInfo("Asia/Kolkata")
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def chart(ticker, interval, rng):
    """Return (candles, meta). Candles are dicts with IST time/date and OHLCV."""
    last = None
    for attempt in range(3):
        try:
            r = requests.get(URL.format(ticker=ticker), params={"interval": interval, "range": rng},
                             headers=HEADERS, timeout=25)
            if r.status_code == 429:
                raise RuntimeError("Yahoo rate-limited (HTTP 429)")
            if r.status_code >= 400:
                raise RuntimeError(f"Yahoo HTTP {r.status_code}")
            res = (r.json().get("chart") or {}).get("result")
            if not res:
                raise RuntimeError("no data for ticker")
            res = res[0]
            break
        except Exception as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(str(last))

    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    stamps = res.get("timestamp") or []
    o, h, l, c, v = (q.get(k) or [] for k in ("open", "high", "low", "close", "volume"))
    out = []
    for i in range(min(len(stamps), len(h), len(l), len(c))):
        if h[i] is None or l[i] is None or c[i] is None:
            continue
        t = datetime.fromtimestamp(stamps[i], IST)
        out.append({"time": t, "date": t.date(), "high": float(h[i]), "low": float(l[i]), "close": float(c[i]),
                    "volume": float((v[i] if i < len(v) else 0) or 0)})
    return out, res.get("meta") or {}
