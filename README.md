# DividendsAndStockSplits

Daily automation for equity traders. After market close, checks all positions (and GTC orders) for **stock splits** and **dividend ex-dates** occurring the next trading day. Uses a dual-check system — a Python scraper and an independent Claude verification — and flags any disagreement for manual review before acting.

---

## How It Works

1. **Python script** scrapes 3 split sources and 4 dividend sources, filters to your positions, writes a CSV + JSON.
2. **Claude** independently checks the same sources and writes its own findings.
3. Both results are compared — discrepancies are highlighted in the email and console.
4. A formatted HTML email with the attached CSV is sent to 5 recipients.

Instruments handled: common stocks, warrants, rights, preferred stock, options (OCC format and human-readable format like `AMPG Oct 16 2026 7.50 CALL`), and multiple share classes. All non-common instruments are mapped to their underlying ticker before checking.

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

The `output/` directory and all Excel files are gitignored.

---

## Data Sources

### Splits
| Source | Status | Notes |
|---|---|---|
| NASDAQ API (`api.nasdaq.com/api/calendar/splits`) | ✓ Working | Primary — filter by `executionDate` |
| NASDAQ HTML (`nasdaq.com/market-activity/stock-splits`) | ✓ Working | Fallback |
| NASDAQTrader (`nasdaqtrader.com/dynamic/splits/splits.txt`) | ✓ Working | Pipe-delimited daily file |
| StockAnalysis (`stockanalysis.com/actions/splits/`) | ✓ Working | Cross-reference |
| TipRanks splits page (`tipranks.com/calendars/stock-splits/upcoming`) | ⚠ API returns 403 | Use HTML page instead of API |

### Dividends
| Source | Status | Notes |
|---|---|---|
| NASDAQ API (`api.nasdaq.com/api/calendar/dividends`) | ✓ Working | Primary — all rows returned are for the queried date |
| MarketBeat (`marketbeat.com/dividends/ex-dividend-date/`) | ✓ Working | Cross-reference |
| EarningsWhispers (`earningswhispers.com/dividend/`) | ⚠ Inconsistent | Use if available |
| Finviz (`finviz.com/calendar.ashx`) | ⚠ JS-rendered | May not return data without a browser |
| StockAnalysis dividends | ✗ Unreliable | URL returns 404 — skipped automatically |

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

Claude is generally more reliable on edge cases but both can miss things. The dual-check exists precisely because neither source is infallible and real money is on the line.
