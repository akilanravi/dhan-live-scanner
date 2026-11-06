# NSE Options Trading Scanner — repaired GitHub project

## What this project does

- Runs a Dhan market-data scan from GitHub Actions on weekdays.
- Uses previous completed session OHLC for CPR.
- Uses 5-minute candles and EMA 9/21 for directional confirmation.
- Publishes `index.html` and the generated `live_data.json` to GitHub Pages.
- Does not place orders or access trading/order APIs.

## 1. Get your Dhan credentials

1. Log in at **web.dhan.co** -> **My Profile -> Access DhanHQ APIs**.
2. **Client ID** = your Dhan client ID (shown on the profile page).
3. **Access Token** = click *Generate Access Token*. It is valid for **24 hours only**.
4. This scanner reads candle data, so you also need an active **Data APIs** subscription (paid add-on). Without it Dhan returns errors such as `806 Data APIs not Subscribed`.

### Add GitHub secrets (Settings -> Secrets and variables -> **Actions** -> *Repository secrets*)

| Secret | Value |
|---|---|
| `DHAN_CLIENT_ID` | your client ID |
| `DHAN_ACCESS_TOKEN` | the 24-hour token (must be re-pasted daily) |
| `DHAN_PIN` *(optional)* | your 6-digit Dhan PIN |
| `DHAN_TOTP_SECRET` *(optional)* | the TOTP secret text shown when you enable TOTP |

With the two optional secrets the scanner creates a fresh token on every run, so you never paste a token again. To get the TOTP secret: Dhan Web -> Access DhanHQ APIs -> enable **TOTP** and copy the secret text shown next to the QR code (the same code you scan in an authenticator app). Storing PIN + TOTP secret in GitHub gives that repository access to generate tokens, so keep the repo **private** or at least never print them. Use Actions secrets, not Dependabot or Codespaces secrets.

Never put these values in any file in the repo. If a token was ever committed, regenerate it.

## 2. Repository layout

Copy the contents of this ZIP into the ROOT of your GitHub repository, preserving this layout:

```text
.
├── .github/
│   └── workflows/
│       └── market-scanner.yml
├── index.html
├── live_data.json
├── requirements.txt
└── scanner.py
```

Delete/replace the old duplicate files named `index (1).html`, `scanner (3).yml`, and `requirements (1).txt`. The old `main.py` FastAPI service is not used: GitHub Pages hosts static websites and does not run a persistent FastAPI server.

## 3. Enable GitHub Pages

1. Open **Settings → Pages** in your repository.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
3. Commit and push the files above.
4. Open the **Actions** tab and run **Dhan Options Scanner and Website Deploy** using **Run workflow**.
5. When it succeeds, open **Settings → Pages** to find the website URL.

The workflow scans on a five-minute schedule during weekdays, then deploys the updated JSON and website in the same workflow run. GitHub scheduled jobs can be delayed; this is not a tick-by-tick live feed.

## 4. Important trading/data notes

- The existing security IDs and strike steps were retained from the submitted project. Verify them against the latest Dhan instrument list before using signals.
- The displayed ATM strike is indicative only. It does not select a live option contract, expiry, lot size or option premium.
- SL and target are calculated from the underlying index/stock price, not from the option premium. They are illustrative reference levels, not an execution-ready options risk plan.
- CPR is calculated from the previous completed session. EMA 9/21 is calculated from available 5-minute candle closes.
- Dhan API access, permissions, rate limits, token validity, market holidays and data availability can affect scans.
- This project is signal display only. It does not place, modify or cancel orders.

## 5. Troubleshooting

- **Missing secrets:** check the two secret names exactly; do not add quotes around the secret values.
- **401/403:** revoke/regenerate the token if needed and check Dhan API access/permissions.
- **No rows:** open the failed Actions run and inspect the `Run Dhan market scanner` step. The JSON records per-symbol errors.
- **Website not deploying:** confirm Pages source is set to **GitHub Actions** and that the workflow has `pages: write` and `id-token: write`.
- **Stale data:** GitHub Actions scheduled runs may be delayed or skipped under GitHub scheduling constraints. Use `workflow_dispatch` to run manually.

## Sources

- Dhan historical/intraday API documentation: https://dhanhq.co/docs/v1/historical-data/
- Dhan Get OHLC documentation: https://docs.dhanhq.co/
- GitHub Pages custom workflows: https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages
