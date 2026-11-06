# NSE Options Scanner (Yahoo Finance, no API key)

- **Header:** TradingView ticker (GIFT Nifty, Nifty 50, Bank Nifty, Sensex) + index tiles from the 5-minute scan.
- **Call options tab / Put options tab:** top-100 F&O stocks by traded value + indices, 5-minute candles, every 5 min.
  CALL = EMA9>EMA21, Supertrend(10,2) bullish, RSI>60, price>R1, close>upper Bollinger(20,2), CPR narrow (<0.35%).
  PUT = the mirror (RSI<40, price<S1, close<lower band). Score n/6 shown per symbol; 6/6 = full signal.
- **Tomorrow's CPR tab:** every weekday 16:30 IST, all F&O stocks scanned for next-day CPR picks.

## Setup
1. Put all files in the repo ROOT (keep `.github/workflows/market-scanner.yml`).
2. Settings -> Pages -> Source: **GitHub Actions**.
3. Actions -> **Options Scanner and Website Deploy** -> **Run workflow** -> mode **evening**.

Tune `NARROW_PCT`, `ST_PERIOD`, `ST_MULT` at the top of `scanner.py`. Not trading advice.
