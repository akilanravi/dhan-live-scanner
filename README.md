# NSE Options Scanner (Yahoo Finance, no API key)

- **Header:** live GIFT Nifty / Nifty 50 / Bank Nifty / Sensex ticker (TradingView widget) + index tiles refreshed every 5 min.
- **Live signals tab:** CPR + EMA 9/21 on 5-minute candles for 4 indices and 20 liquid F&O stocks, every 5 min.
- **Tomorrow's CPR tab:** every day at 16:30 IST all NSE F&O stocks are scanned on daily candles; shows next-day CPR levels, narrow-CPR stocks and Call/Put watch with trigger, SL and targets.

## Setup
1. Put all files in the repo ROOT (keep `.github/workflows/market-scanner.yml`).
2. Settings -> Pages -> Source: **GitHub Actions**.
3. Actions -> **Options Scanner and Website Deploy** -> **Run workflow** -> mode **evening** (fills both tabs).

## Notes
- Yahoo NSE data may be delayed; GitHub schedules can run late. Not trading advice.
- F&O list comes from NSE's live lot-size file; a built-in list is used if NSE blocks the download.
