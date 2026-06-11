# DividendsAndStockSplits

Daily automation for equity traders. After market close, checks all positions (and GTC orders) for **stock splits** and **dividend ex-dates** occurring the next trading day. Uses a dual-check system — a Python scraper and an independent Claude verification — and flags any disagreement for manual review before acting.

---

## How It Works

1. **Python script** scrapes 3 split calendars and 2 independent market-wide dividend calendars (Benzinga + Investing.com — both cover the ADRs and CEFs that other calendars miss, and each covers the other's blind spots), filters to your positions, verifies each hit on StockAnalysis, and writes a CSV + JSON. Runs in ~1 minute. Any failed verification is reported as UNCHECKED rather than silently skipped. (`--deep` per-ticker sweeps exist but are not viable at full portfolio scale — StockAnalysis's rate budget is far below 1400 requests/day.)
2. **Claude** independently verifies: re-reads the split calendars, confirms every split against the company's own press release, cross-checks dividends against a second calendar, confirms every dividend hit on the ticker's own page, **and reads the issuer's own declaration (8-K/6-K/press release) for every dividend hit** — the only source that reliably shows exact unrounded amounts, gross vs. net for ADRs, and cash vs. stock dividends (METCB's 2026-06-12 "dividend" was paid in Class B shares, which no calendar indicated).
3. Both results are compared — discrepancies are highlighted in the email and console.
4. A formatted HTML email with the attached CSV is sent to 5 recipients — **always**, even when nothing was found (a "no events" email confirms the check ran).

Instruments handled: common stocks, warrants, rights, preferred stock, options (OCC format and human-readable formats like `AMPG Oct 16 2026 7.50 CALL` or `XRX JAN 21 '28 7 CALL`), and multiple share classes. All non-common instruments are mapped to their underlying ticker before checking. Ambiguous bare suffixes (`GLW` could be Corning or a GL warrant) are checked under **both** interpretations so neither can be missed.

---

## Setup

### Requirements
- Python 3.11+
- GitHub account with access to this repo
- Gmail account (sender) with an app password

### Install

```bash
git clone https://github.com/Time4aCookie/DividendsAndStockSplits.git
cd DividendsAndStockSplits
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:
```
EMAIL_SENDER=you@gmail.com
EMAIL_PASSWORD=your_gmail_app_password
EMAIL_RECIPIENTS=addr1@co.com,addr2@co.com,addr3@co.com,addr4@co.com,addr5@co.com
```

> **Gmail app password**: myaccount.google.com → Security → 2-Step Verification → App passwords. Name it "DividendsAndStockSplits" and use the 16-character code (no spaces) as `EMAIL_PASSWORD`. The SMTP host is auto-detected from the sender domain — Gmail and Outlook both work.

### Claude Code permission setup (skip all approval prompts)

After installing Claude Code, run this once to configure it globally so Claude never pauses to ask for permission:

```bash
mkdir -p ~/.claude && cat > ~/.claude/settings.json << 'EOF'
{
  "permissions": {
    "defaultMode": "bypassPermissions"
  }
}
EOF
```

If you already have a `~/.claude/settings.json` (e.g. with a theme set), just add `"permissions": { "defaultMode": "bypassPermissions" }` to it instead of overwriting the file.

---

## Daily Usage

**Your only job:** drop the Excel file(s) into this folder, open Claude Code in this directory, and say:

> "run the daily check"

Claude handles everything from there — finds the files, runs the scraper, does its own independent check, compares the results, and sends the email. If there are discrepancies, Claude will ask you to verify before sending.

To also check GTC orders, drop a GTC export (e.g. `gtc_orders.xlsx`) in the folder alongside the positions file before triggering the check.

### If you need to run the script manually
```bash
python check_events.py positions.xlsx [--gtc gtc_orders.xlsx] [--no-email] [--date YYYY-MM-DD]
```

### Review discrepancies
Any finding that only one side caught is flagged as **MANUAL VERIFICATION REQUIRED** in both the console output and the email. Do not act on a discrepancy without verifying it yourself.

---

## Input File Format

### Positions Excel
Must contain a ticker column. Recognized column names: `ticker`, `symbol`, `sym`, `stock`, `security`, `instrument`. Falls back to the first column if none match.

| ticker | position_size |
|--------|--------------|
| AAPL   | 500          |
| ACMR.WS| 1000         |
| BAC-PA | 200          |
| AMPG Oct 16 2026 7.50 CALL | 10 |

Position size is loaded but not used for event checking — only the ticker matters.

### GTC Orders Excel
Format TBD — will be added once the broker export format is confirmed.

---

## Output

| File | Description |
|---|---|
| `output/python_results_YYYY-MM-DD.csv` | Python findings (attached to email) |
| `output/python_results_YYYY-MM-DD.json` | Python findings in JSON (for Claude comparison) |
| `output/claude_results_YYYY-MM-DD.json` | Claude's findings (written after its independent check) |
| `output/unchecked_tickers_YYYY-MM-DD.txt` | Tickers that could not be verified (rate limit/errors) — only written when non-empty; its presence means the report is incomplete |

The `output/` directory and all Excel files are gitignored.

---

## Data Sources (as of 2026-06-10)

### Splits — 3 independent calendars
| Source | Notes |
|---|---|
| StockAnalysis (`stockanalysis.com/actions/splits/`) | Data in SvelteKit inline script. Missed VRNO on 2026-06-11 — never used alone |
| Benzinga (`benzinga.com/calendars/stock-splits`) | Server-rendered table, explicit Ex-Date per row; covers OTC and BATS ETFs |
| Investing.com (`investing.com/stock-split-calendar/`) | Date-grouped table |

Claude additionally verifies every split hit against the company's own press release (`stocktitan.net/news/TICKER/`) — the only check independent of all calendars.

### Dividends — bulk primary + per-ticker verification
| Source | Notes |
|---|---|
| Benzinga (`benzinga.com/calendars/dividends`) | Primary bulk — one request, whole market incl. ADRs (BABA) and CEFs (RA), declared gross amounts. Found 60 tickers for 2026-06-11 vs NASDAQ API's 6 |
| Investing.com dividends AJAX (`/dividends-calendar/Service/getCalendarFilteredData`) | Second comprehensive bulk (POST with date filter, country=US). Covers foreign Y-suffix ADRs Benzinga misses; ADR amounts net of fees so Benzinga's gross wins the merge |
| StockAnalysis per-ticker (`stockanalysis.com/stocks/TICKER/dividend/`) | Hit verification only — daily rate budget is far below full-portfolio scale (a 1400-ticker `--deep` sweep stalled in 429 backoff for 3 hours on 2026-06-11). ADR amounts shown net of depositary fee — gross comes from Benzinga/company 6-K |
| MarketBeat (`marketbeat.com/dividends/ex-dividend-date/`) | Secondary calendar — US equities only; page ignores the URL date, so rows are filtered by their Ex-Dividend Date column |

Claude additionally reads the issuer's own declaration (8-K/6-K/press release, via StockTitan or search — SEC.gov blocks direct fetches) for every dividend hit: exact amounts, gross vs. net, cash vs. stock.

### Dead sources — removed, do not re-add without re-testing
NASDAQ API (timeout), NASDAQ splits HTML (JS-rendered, no data in raw HTML), NASDAQTrader splits.txt (404), TipRanks (403), StockAnalysis dividends calendar (404), EarningsWhispers (error page), Yahoo Finance batch API (401), Finviz / WSJ / Barchart / Seeking Alpha / dividend.com (blocked or no coverage).

---

## Project Structure

```
DividendsAndStockSplits/
├── CLAUDE.md            # Full daily workflow instructions (read by Claude Code)
├── check_events.py      # Main script
├── scrapers.py          # Web scraping for all sources
├── ticker_utils.py      # Underlying ticker extraction
├── email_sender.py      # SMTP email with HTML report (Gmail + Outlook, auto-detected)
├── compare.py           # Python vs Claude result comparison
├── test_all.py          # Offline regression suite — run after any change (no network needed)
├── requirements.txt
├── .env.example         # Credential template
└── .env                 # Gitignored — your credentials
```

---

## Discrepancy Policy

| Scenario | Meaning | Action |
|---|---|---|
| Both agree | High confidence | Proceed, still confirm if large position |
| Claude only | Python scraper likely missed it | **Verify manually** |
| Python only | Claude may have missed a source | **Verify manually** |
| INCOMPLETE CHECK warning | Some tickers were never verified (rate limit/errors) | **Re-run or verify the unchecked list** — the report cannot be trusted as complete |

Claude is generally more reliable on edge cases but both can miss things. The dual-check exists precisely because neither source is infallible and real money is on the line.
