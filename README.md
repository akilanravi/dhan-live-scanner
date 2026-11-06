# NSE Options Scanner (Yahoo Finance version)

Scans 10 NSE symbols every 5 minutes on weekdays using free Yahoo Finance 5-minute candles (CPR from the previous session + EMA 9/21) and publishes the result to GitHub Pages. **No API key or secrets needed.** No orders are placed.

## Setup
1. Put all files in the ROOT of your GitHub repo (keep `.github/workflows/market-scanner.yml`).
2. Settings -> Pages -> Source: **GitHub Actions**.
3. Actions tab -> **Options Scanner and Website Deploy** -> **Run workflow**.
4. Open the site URL shown in Settings -> Pages.

## Notes
- Yahoo NSE data can be delayed and unofficial; Yahoo may sometimes rate-limit GitHub servers (the scanner retries, and failed symbols are listed on the page).
- GitHub scheduled runs can be late or skipped. Use *Run workflow* to refresh manually.
- SL/target are on the underlying price, not option premium. ATM strike is indicative only.
- Signals are rule-based alerts, not trading advice.
