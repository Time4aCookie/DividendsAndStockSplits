# DividendsAndStockSplits — Daily Workflow

## What this project does
After market close each trading day, check every equity position (and any GTC orders)
for stock splits and dividend ex-dates occurring the **next trading day**.
Claude runs everything — the user's only job is to drop the Excel file(s) in this
folder and say "run the daily check."

---

## How to trigger the daily check

The user will say something like:
- "run the daily check"
- "check today's events"
- "run the workflow"

When this happens, follow the steps below **without asking the user to type anything
in the terminal.** Claude runs all scripts directly.

---

## Daily Workflow — Claude executes all of this

### Step 1 — Find the Excel file(s)
Run this to locate input files:
```bash
ls *.xlsx 2>/dev/null || ls *.xls 2>/dev/null
```
- The **positions file** matches: `position*.xlsx`, `holding*.xlsx`, or is the only `.xlsx` present.
- The **GTC file** matches: `gtc*.xlsx`, `order*.xlsx`, `gtc*.xls`.
- If multiple ambiguous files are found, show the list and ask the user which is positions vs GTC.
- If no file is found, tell the user to drop the Excel into this folder and try again.

### Step 2 — Determine the target date
```bash
date +%Y-%m-%d
```
Target = next **trading day** from today (skip Saturday and Sunday).
Note: does NOT account for market holidays — on holiday eves, mention this to the user.

### Step 3 — Run the Python scraper
```bash
python check_events.py <positions_file> [--gtc <gtc_file>]
# Add --no-email here — Claude will handle the email decision after comparison
python check_events.py <positions_file> --no-email
```
This produces:
- `output/python_results_YYYY-MM-DD.csv`
- `output/python_results_YYYY-MM-DD.json`

### Step 4 — Claude's independent check
Independently fetch every source below. Do NOT skip any — cross-coverage is the whole
point of the dual-check system.

**Splits** (check for target date):
- `https://api.nasdaq.com/api/calendar/splits?date=YYYY-MM-DD` (JSON)
- `https://www.nasdaq.com/market-activity/stock-splits` (HTML, scan for target date)
- `https://www.nasdaqtrader.com/dynamic/splits/splits.txt` (pipe-delimited, scan for target date)
- `https://www.tipranks.com/api/calendar/stock-splits/` (JSON)
- `https://stockanalysis.com/actions/splits/` (upcoming section)

**Dividends** (ex-date = target date):
- `https://api.nasdaq.com/api/calendar/dividends?date=YYYY-MM-DD` (JSON)
- `https://stockanalysis.com/dividends/calendar/?date=YYYY-MM-DD`
- `https://www.marketbeat.com/dividends/ex-dividend-date/YYYY-MM-DD/`
- `https://www.earningswhispers.com/dividend/YYYY-MM-DD`
- `https://finviz.com/calendar.ashx` (dividends tab, filter by date)

Filter all results: only keep tickers whose **underlying** matches a position.

### Step 5 — Write Claude's findings
Write to `output/claude_results_YYYY-MM-DD.json`:
```json
[
  {"underlying": "AAPL", "event_type": "dividend", "amount_or_ratio": "0.25", "sources": ["NASDAQ", "StockAnalysis"]},
  {"underlying": "TSLA", "event_type": "split",    "amount_or_ratio": "3:1",   "sources": ["NASDAQTrader"]}
]
```
Write `[]` if nothing found — this signals the check completed with no hits.

### Step 6 — Run comparison
```bash
python check_events.py <positions_file> --no-email
```
The script detects the Claude JSON and runs `compare.py` automatically, printing agreements and discrepancies.

### Step 7 — Report to the user
Present a clear summary:
- Splits found (ticker, ratio, confirmed by which sources)
- Dividends found (ticker, amount, confirmed by which sources)
- GTC orders affected (if GTC file was provided)
- Discrepancies — call these out explicitly: **"NEEDS MANUAL VERIFICATION"**
- Items agreed on by both

### Step 8 — Send the email
If there are **no discrepancies**: send automatically.
```bash
python check_events.py <positions_file>
```

If there are **discrepancies**: tell the user what they are, then ask:
> "There are X discrepancies that need manual verification (listed above).
> Do you want me to send the email now with the discrepancies flagged in red,
> or verify first and send after?"

Only send after the user confirms.

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
| Share class | `BRK.A`, `BRK-B` | Keep full ticker; it IS the common stock |
| Common stock | `AAPL` | No change |

When in doubt, err on the side of checking — a false positive is cheaper than a miss.

---

## GTC Order Adjustment Logic

> **Note:** GTC export format is being confirmed. This section will be updated Monday.

When a position has a split or dividend ex-date tomorrow:

**Dividends:**
- Flag any open BUY limit orders — the stock opens ~`dividend_amount` lower on ex-date.
  Suggest adjusting the limit down by the dividend amount.
- Flag open SELL limit orders for the same reason.

**Stock Splits:**
- Flag ALL open orders on the underlying.
  Price ÷ split ratio, quantity × split ratio.
  Some brokers auto-adjust for splits — note this and tell the user to confirm with their broker.

---

## Email Configuration

Sender: `rohant@jagtradingllc.com`
Recipients: 5 addresses stored in `.env` as `EMAIL_RECIPIENTS` (comma-separated).

### First-time setup on a new machine
```bash
git clone https://github.com/Time4aCookie/DividendsAndStockSplits.git
cd DividendsAndStockSplits
pip install -r requirements.txt
cp .env.example .env
# Fill in EMAIL_PASSWORD and EMAIL_RECIPIENTS in .env
```
App password: Office 365 → Security → App passwords.
If your firm disables app passwords, ask to switch to Microsoft Graph API auth.

---

## Output Files

| File | Description |
|---|---|
| `output/python_results_YYYY-MM-DD.csv` | Python findings — attached to email |
| `output/python_results_YYYY-MM-DD.json` | Python findings in JSON — used for comparison |
| `output/claude_results_YYYY-MM-DD.json` | Claude's findings — written in Step 5 |

The `output/` directory and all `.xlsx` files are gitignored.

---

## Discrepancy Policy

| Scenario | Likely cause | Action |
|---|---|---|
| Both agree | High confidence | Proceed |
| Claude only | Python scraper missed it | **Verify manually before acting** |
| Python only | Claude missed a source | **Verify manually before acting** |

Never act on a discrepancy without human confirmation. The email flags these in red.

---

## Data Sources Reference

### Splits
| Source | URL |
|---|---|
| NASDAQ API | `api.nasdaq.com/api/calendar/splits?date=` |
| NASDAQ HTML | `nasdaq.com/market-activity/stock-splits` |
| NASDAQTrader | `nasdaqtrader.com/dynamic/splits/splits.txt` |
| TipRanks | `tipranks.com/api/calendar/stock-splits/` |
| StockAnalysis | `stockanalysis.com/actions/splits/` |

### Dividends
| Source | URL |
|---|---|
| NASDAQ API | `api.nasdaq.com/api/calendar/dividends?date=` |
| StockAnalysis | `stockanalysis.com/dividends/calendar/?date=` |
| MarketBeat | `marketbeat.com/dividends/ex-dividend-date/` |
| EarningsWhispers | `earningswhispers.com/dividend/` |
| Finviz | `finviz.com/calendar.ashx` |

---

## Project Structure

```
DividendsAndStockSplits/
├── CLAUDE.md            ← You are here. Full workflow for any machine.
├── check_events.py      ← Main script (Claude runs this, not the user)
├── scrapers.py          ← All web scraping logic
├── ticker_utils.py      ← Ticker parsing & underlying extraction
├── email_sender.py      ← Outlook SMTP email
├── compare.py           ← Python vs Claude comparison
├── requirements.txt
├── .env.example         ← Copy to .env and fill in credentials
└── .env                 ← GITIGNORED — credentials only
```
