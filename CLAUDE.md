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
```bash
ls *.xlsx 2>/dev/null; ls *.xls 2>/dev/null
```
- The **positions file** matches: `position*.xlsx`, `holding*.xlsx`, `trades*.xlsx`,
  or is the only `.xlsx` present.
- The **GTC file** matches: `gtc*.xlsx`, `order*.xlsx`.
- If multiple ambiguous files exist, show the list and ask the user which is which.
- If no file is found, tell the user to drop the Excel into this folder and try again.

### Step 2 — Inspect the positions file and confirm the ticker column
```bash
python -c "
import pandas as pd
df = pd.read_excel('<filename>', dtype=str, nrows=5)
print(df.columns.tolist())
print(df.head())
"
```
Show the user the detected columns and first few rows. Confirm which column contains
tickers before proceeding. The script auto-detects columns named: `ticker`, `symbol`,
`sym`, `stock`, `security`, `instrument` — or falls back to the first column.
If the real column name is different, note it and adjust.

### Step 3 — Determine the target date
```bash
date +%Y-%m-%d
```
Target = next **trading day** from today (skip Saturday and Sunday).
This does NOT account for market holidays — mention this to the user on holiday eves.

### Step 4 — Run the Python scraper
```bash
python check_events.py <positions_file> --no-email
```
With GTC orders (once format is confirmed):
```bash
python check_events.py <positions_file> --gtc <gtc_file> --no-email
```
This produces:
- `output/python_results_YYYY-MM-DD.csv`
- `output/python_results_YYYY-MM-DD.json`

### Step 5 — Claude's independent check
Independently fetch every source below. Do NOT skip any — cross-coverage is the whole
point of the dual-check system.

**Critical API behavior notes:**
- **NASDAQ dividends API**: All rows returned ARE for the queried date even if the
  `exOrEffDate` field is empty. Do NOT filter by the ex-date field — trust all rows.
- **NASDAQ splits API**: Returns upcoming splits across multiple dates. You MUST filter
  by `executionDate` matching the target date — do not include splits with other dates.

**Splits** (filter to executionDate = target date):
- `https://api.nasdaq.com/api/calendar/splits?date=YYYY-MM-DD` (JSON — filter by executionDate)
- `https://www.nasdaq.com/market-activity/stock-splits` (HTML — scan for target date)
- `https://www.nasdaqtrader.com/dynamic/splits/splits.txt` (pipe-delimited — scan for target date)
- `https://stockanalysis.com/actions/splits/` (upcoming splits section — scan for target date)
- TipRanks API returns 403. Try HTML page instead: `https://www.tipranks.com/calendars/stock-splits/upcoming`

**Dividends** (all rows from NASDAQ API are already for target date):
- `https://api.nasdaq.com/api/calendar/dividends?date=YYYY-MM-DD` (JSON — trust all rows returned)
- `https://www.marketbeat.com/dividends/ex-dividend-date/YYYY-MM-DD/` (HTML)
- `https://www.earningswhispers.com/dividend/YYYY-MM-DD` (HTML)
- `https://finviz.com/calendar.ashx` (HTML — dividends tab, filter by date)
- StockAnalysis dividend calendar URL has been unreliable — skip if it errors.

Filter all results: only keep tickers whose **underlying** matches a position.

### Step 6 — Write Claude's findings
Write to `output/claude_results_YYYY-MM-DD.json`:
```json
[
  {"underlying": "AAPL", "event_type": "dividend", "amount_or_ratio": "0.25", "sources": ["NASDAQ", "MarketBeat"]},
  {"underlying": "TSLA", "event_type": "split",    "amount_or_ratio": "3:1",   "sources": ["NASDAQTrader"]}
]
```
Write `[]` if nothing found — this signals the check completed with no hits.

### Step 7 — Run comparison
```bash
python check_events.py <positions_file> --no-email
```
The script detects the Claude JSON and runs `compare.py` automatically,
printing agreements and discrepancies.

### Step 8 — Report to the user
Present a clear summary:
- Splits found (ticker, ratio, confirmed by which sources)
- Dividends found (ticker, amount, confirmed by which sources)
- GTC orders affected (if GTC file was provided)
- Discrepancies — call these out explicitly: **"NEEDS MANUAL VERIFICATION"**
- Items both sources agreed on

### Step 9 — Send the email
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
| Human-readable option | `AVGO JUN 05 2026 310.00 PUT`, `AMPG Oct 16 2026 7.50 CALL` | Extract first word (before the space) as underlying |
| OCC option | `AAPL240119C00150000` | Extract leading letters as underlying |
| Warrant | `ACMR.WS`, `ACMRW`, `ACMR.WT` | Strip `.WS`/`.WT`/trailing `W` |
| Right | `ACMR.R`, `ACMRR` | Strip `.R`/trailing `R` |
| Unit | `ACMR.U`, `ACMRU` | Strip `.U`/trailing `U` |
| Preferred | `BAC.PA`, `BAC-PA`, `BACPA`, `BACPRA` | Strip preferred suffix; check underlying `BAC` |
| Share class | `BRK.A`, `BRK-B` | Keep full ticker; it IS the common stock |
| Common stock | `AAPL` | No change |

**Human-readable option detection**: if the ticker string contains a space AND contains
a month name (Jan, Feb, Mar, Apr, May, Jun, Jul, Aug, Sep, Oct, Nov, Dec) AND ends with
CALL or PUT, extract everything before the first space as the underlying.

When in doubt, err on the side of checking — a false positive is cheaper than a miss.

---

## GTC Order Adjustment Logic

> **Note:** GTC export format is being confirmed. This section will be updated once the
> broker export is shared.

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

Sender: `rohantatikonda@gmail.com` (Gmail app password required)
Recipients: 5 addresses stored in `.env` as `EMAIL_RECIPIENTS` (comma-separated).

### First-time setup on a new machine
```bash
git clone https://github.com/Time4aCookie/DividendsAndStockSplits.git
cd DividendsAndStockSplits
pip install -r requirements.txt
cp .env.example .env
# Fill in EMAIL_SENDER, EMAIL_PASSWORD (Gmail app password), and EMAIL_RECIPIENTS in .env
```

**Gmail app password**: myaccount.google.com → Security → 2-Step Verification → App passwords.
Name it "DividendsAndStockSplits". Use the 16-character code (no spaces) as EMAIL_PASSWORD.

---

## Known Source Issues (as of 2026-06-06)

| Source | Status | Notes |
|---|---|---|
| NASDAQ API (splits + dividends) | ✓ Working | Primary source — most reliable |
| NASDAQTrader splits file | ✓ Working | Good secondary for splits |
| MarketBeat dividends | ✓ Working | Good secondary for dividends |
| TipRanks splits API | ✗ 403 Forbidden | Try HTML page instead |
| StockAnalysis dividends | ✗ 404 | URL unreliable — skip if errors |
| EarningsWhispers | ⚠ Inconsistent | Use if available |

---

## Output Files

| File | Description |
|---|---|
| `output/python_results_YYYY-MM-DD.csv` | Python findings — attached to email |
| `output/python_results_YYYY-MM-DD.json` | Python findings in JSON — used for comparison |
| `output/claude_results_YYYY-MM-DD.json` | Claude's findings — written in Step 6 |

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

## Project Structure

```
DividendsAndStockSplits/
├── CLAUDE.md            ← You are here. Full workflow for any machine.
├── check_events.py      ← Main script (Claude runs this, not the user)
├── scrapers.py          ← All web scraping logic
├── ticker_utils.py      ← Ticker parsing & underlying extraction
├── email_sender.py      ← SMTP email (Gmail or Outlook, auto-detected)
├── compare.py           ← Python vs Claude comparison
├── requirements.txt
├── .env.example         ← Copy to .env and fill in credentials
└── .env                 ← GITIGNORED — credentials only
```
