# DividendsAndStockSplits — Daily Workflow

## What this project does
After market close each trading day, check every equity position (and any GTC orders)
for stock splits and dividend ex-dates occurring the **next trading day**.
The Python script scrapes multiple data sources automatically.
Claude independently verifies those findings, then both results are compared.
Any discrepancy is flagged for **manual verification** before acting — real money depends on this.

---

## Daily Workflow (run in this order)

### Step 1 — Run the Python script
```bash
python check_events.py positions.xlsx
# If you also have GTC orders exported:
python check_events.py positions.xlsx --gtc gtc_orders.xlsx
```
This produces:
- `output/python_results_YYYY-MM-DD.csv`  — structured results
- `output/python_results_YYYY-MM-DD.json` — used for Claude comparison

Add `--no-email` to skip emailing while testing.

### Step 2 — Claude's independent check (this session)
When the user starts a Claude Code session in this directory and says
"run the daily check" or "check today's events", follow these exact steps:

1. **Read the positions file** the user provides or that exists in the working directory.
   Parse the ticker column. If ambiguous, ask.

2. **Determine the target date**: next trading day from today (skip weekends).
   Today is available via `date` in the shell. Skip Sat/Sun. Note: this does NOT
   account for market holidays — verify manually on holiday eves.

3. **Extract underlying tickers** using the rules below before checking any source.

4. **Check ALL splits sources** for the target date:
   - https://www.nasdaq.com/market-activity/stock-splits (look for the target date)
   - https://api.nasdaq.com/api/calendar/splits?date=YYYY-MM-DD (JSON API)
   - https://www.nasdaqtrader.com/dynamic/splits/splits.txt (pipe-delimited file)
   - https://www.tipranks.com/api/calendar/stock-splits/ (JSON endpoint)
   - https://stockanalysis.com/actions/splits/ (look for upcoming section)

5. **Check ALL dividend sources** for ex-dividend dates matching the target date:
   - https://api.nasdaq.com/api/calendar/dividends?date=YYYY-MM-DD (JSON API)
   - https://stockanalysis.com/dividends/calendar/?date=YYYY-MM-DD
   - https://www.marketbeat.com/dividends/ex-dividend-date/YYYY-MM-DD/
   - https://www.earningswhispers.com/dividend/YYYY-MM-DD
   - https://finviz.com/calendar.ashx (dividends section, filter by date)

6. **Filter results** — only report tickers where the UNDERLYING matches a position.

7. **Write Claude's findings** to `output/claude_results_YYYY-MM-DD.json` in this format:
   ```json
   [
     {"underlying": "AAPL", "event_type": "dividend", "amount_or_ratio": "0.25", "sources": ["NASDAQ", "StockAnalysis"]},
     {"underlying": "TSLA", "event_type": "split",    "amount_or_ratio": "3:1",   "sources": ["NASDAQTrader"]}
   ]
   ```
   Write an empty array `[]` if nothing found — this signals the check ran.

8. **Re-run the Python script** with `--no-email` to trigger comparison:
   ```bash
   python check_events.py positions.xlsx --no-email
   ```

9. **Report findings to the user** clearly:
   - List splits and dividends found
   - List GTC orders affected (if GTC file provided)
   - Call out every discrepancy with Python in bold — these need manual verification
   - Confirm which items both sources agree on

10. **Send the email** (ask user to confirm first if there are discrepancies):
    ```bash
    python check_events.py positions.xlsx
    ```

---

## Ticker Parsing Rules (extract underlying before checking)

Apply in this order — first match wins:

| Instrument | Pattern examples | Action |
|---|---|---|
| OCC Option | `AAPL240119C00150000` | Extract leading letters as underlying |
| Warrant | `ACMR.WS`, `ACMRW`, `ACMR.WT` | Strip `.WS`/`.WT`/trailing `W` |
| Right | `ACMR.R`, `ACMRR` | Strip `.R`/trailing `R` |
| Unit | `ACMR.U`, `ACMRU` | Strip `.U`/trailing `U` |
| Preferred | `BAC.PA`, `BAC-PA`, `BACPA`, `BACPRA` | Strip preferred suffix; check underlying `BAC` |
| Share class | `GOOGL`, `BRK.A`, `BRK-B` | Keep full ticker; it IS the common stock for splits/divs |
| Common stock | `AAPL` | No change |

When in doubt, err on the side of checking — a false positive is cheaper than a miss.

---

## GTC Order Adjustment Logic

> **Note:** GTC export format is being confirmed. This section will be updated.

When a position has a split or dividend ex-date tomorrow:

**Dividends:**
- Flag any open BUY limit orders at or below the pre-dividend price.
  The stock will open roughly `dividend_amount` lower on ex-date.
  Suggest adjusting the limit down by the dividend amount.
- Flag any open SELL limit orders for the same reason.

**Stock Splits:**
- Flag ALL open orders on the underlying.
  Buy/sell price must be divided by the split ratio.
  Share quantity must be multiplied by the split ratio.
  Some brokers auto-adjust for splits — confirm with the broker before adjusting manually.

---

## Email Configuration

Sender: `rohant@jagtradingllc.com`
Recipients: stored in `.env` as `EMAIL_RECIPIENTS` (comma-separated, 5 addresses)

### First-time setup on a new machine
1. `cp .env.example .env`
2. Fill in `EMAIL_PASSWORD` with your Outlook app password.
   To generate: Office 365 → Security → App passwords (or ask IT if MFA is managed by the firm).
3. Fill in the 5 `EMAIL_RECIPIENTS`.
4. `pip install -r requirements.txt`
5. Test: `python check_events.py positions.xlsx --no-email`

---

## Output Files

| File | Description |
|---|---|
| `output/python_results_YYYY-MM-DD.csv` | Python script findings (attach to email) |
| `output/python_results_YYYY-MM-DD.json` | Python findings in JSON (for comparison) |
| `output/claude_results_YYYY-MM-DD.json` | Claude's findings (written by Claude in Step 7) |

The `output/` directory is gitignored. Excel input files are also gitignored.

---

## Discrepancy Policy

When Python and Claude disagree on a finding:
- **Claude-only finding**: Likely real — Python scraper may have missed it.
  VERIFY MANUALLY before assuming it's a false positive.
- **Python-only finding**: Could be a scraper artifact or Claude missed a source.
  VERIFY MANUALLY before discarding.
- Never act on a discrepancy without manual confirmation.
  The email will highlight these in red.

---

## Data Sources Reference

### Splits
| Source | URL | Notes |
|---|---|---|
| NASDAQ calendar | `nasdaq.com/market-activity/stock-splits` | Best primary source |
| NASDAQ API | `api.nasdaq.com/api/calendar/splits?date=` | JSON, most reliable |
| NASDAQTrader | `nasdaqtrader.com/dynamic/splits/splits.txt` | Daily pipe-delimited file |
| TipRanks | `tipranks.com/calendars/stock-splits/upcoming` | Good secondary source |
| StockAnalysis | `stockanalysis.com/actions/splits/` | Upcoming section |

### Dividends
| Source | URL | Notes |
|---|---|---|
| NASDAQ API | `api.nasdaq.com/api/calendar/dividends?date=` | JSON, most reliable |
| StockAnalysis | `stockanalysis.com/dividends/calendar/?date=` | Good coverage |
| MarketBeat | `marketbeat.com/dividends/ex-dividend-date/` | Cross-reference |
| EarningsWhispers | `earningswhispers.com/dividend/` | Additional cross-reference |
| Finviz | `finviz.com/calendar.ashx` | Dividends tab |

---

## Project Structure

```
DividendsAndStockSplits/
├── CLAUDE.md            ← You are here. Full instructions for any machine.
├── check_events.py      ← Main script (run daily)
├── scrapers.py          ← All web scraping logic
├── ticker_utils.py      ← Ticker parsing & underlying extraction
├── email_sender.py      ← Outlook SMTP email
├── compare.py           ← Python vs Claude comparison
├── requirements.txt
├── .env.example         ← Copy to .env and fill in credentials
├── .env                 ← GITIGNORED — credentials live here only
└── output/              ← GITIGNORED — daily CSV/JSON reports
```
