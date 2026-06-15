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
- `output/unchecked_tickers_YYYY-MM-DD.txt` — only if some tickers could not be verified

**Modes & runtime:**
- **Fast mode (default)** — ~2 minutes. Benzinga bulk dividend calendar + Investing.com
  AJAX bulk (each 1 request, both cover ADRs/CEFs; Benzinga gross amounts win the
  merge) + MarketBeat (1 request) + StockAnalysis per-ticker verification of position
  hits + **OTC-preferred sweep**: every position matching `^[A-Z]{4,5}P$` (OTC/illiquid
  preferreds like PSBYP/PSBZP) is per-ticker checked because NO bulk calendar covers
  them — both were missed on 2026-06-12 until the user caught it. Tickers with no
  per-ticker page anywhere (e.g. SLMNP) surface as UNCHECKED for manual verification.
  Use this for the daily check — always.
- **Deep mode (`--deep`)** — **NOT VIABLE at this portfolio's scale (~1400 tickers).**
  Tested 2026-06-11: StockAnalysis's rate limiter has a budget far below 1400
  requests — the sweep ran 3 hours, spent ~100% of its time in 429 backoff loops,
  and was killed unfinished. Even an 8-hour quiet gap did not reset the budget.
  Only use `--deep` for small ticker sets. The effective audit of fast mode is the
  **union of the two independent comprehensive calendars** (Benzinga + Investing.com,
  which have different blind spots) plus Claude's independent Step 5 checks.

**After midnight:** the default target date is the next trading day from *today* —
if running after midnight for that same morning's market open, pass
`--date YYYY-MM-DD` explicitly or the script will target the following day.

**If the output reports UNCHECKED tickers**, the report is INCOMPLETE — some tickers
could not be verified (rate limit/errors). This is automatically appended to the
discrepancy list, which blocks the auto-send path. Re-run later or verify those
tickers manually (list in `output/unchecked_tickers_YYYY-MM-DD.txt`). Never present
a run with unchecked tickers as a clean "no events" day.

### Step 5 — Claude's independent check
The Python script sweeps bulk calendars and verifies hits; Claude independently
re-reads the calendars it can reach and verifies every hit against primary sources:

1. **Splits calendars (bulk)** — fetch all three, filter to target date, match against positions:
   - `https://stockanalysis.com/actions/splits/` — missed VRNO on 2026-06-11; never rely on it alone
   - `https://www.benzinga.com/calendars/stock-splits` — explicit Ex-Date column; covers OTC and BATS ETFs
   - `https://www.investing.com/stock-split-calendar/` — date-grouped table; ticker in parens
2. **Verify every split hit via press release** — splits are corporate actions the company
   itself announces, which makes this the only check truly independent of all calendars.
   For each split found (by the script or the calendars), fetch
   `https://www.stocktitan.net/news/TICKER/` and confirm the company announced the split
   with matching ratio and effective date. If StockTitan has nothing, WebSearch
   `"<company> reverse stock split <date>"` for the press release. A split hit with no
   findable announcement is a discrepancy — flag it.
3. **Dividend calendars (bulk)** — match against positions:
   - `https://www.benzinga.com/calendars/dividends` — PRIMARY: covers ADRs (BABA),
     CEFs (RA), preferred series; shows declared GROSS amounts. Filter Ex-Date column
     to target date.
   - `https://www.marketbeat.com/dividends/ex-dividend-date/YYYY-MM-DD/` — secondary;
     US equities only, and the page IGNORES the URL date (shows recent announcements
     with mixed ex-dates — always filter by the Ex-Dividend Date column).
4. **Verify every Python dividend hit per-ticker** — for each ticker the Python script
   reported, fetch `https://stockanalysis.com/stocks/TICKER/dividend/` and confirm the
   ex-date and amount match. (If `stocks/` 404s, try `etf/`.)
5. **Verify every dividend hit against the ISSUER'S OWN announcement (mandatory)** —
   hits are rare (1–4/day), so this is cheap. For each hit, find the declaration:
   8-K/6-K via WebSearch (`"<company> dividend declared <month year>"`) or the
   StockTitan per-ticker news page (SEC.gov itself 403s direct fetches — use mirrors).
   Confirm from the declaration, not the calendar:
   - **Exact amount** (calendars round: Benzinga showed KIO $0.12, actual $0.1215)
   - **Gross vs net (ADR trap):** StockAnalysis/Investing.com list ADR dividends NET
     of the ~$0.02/ADS depositary fee (showed BABA $1.030 when the 6-K declared
     $1.05). The GROSS is what the price drops by on ex-date — gross goes in the
     report and GTC adjustments.
   - **Cash vs STOCK dividend:** calendars don't distinguish. METCB 2026-06-12 looked
     like a $0.14 cash dividend on Benzinga; the 8-K revealed $0.1369 paid IN CLASS B
     SHARES. Only the filing shows this.
   - **CEF caveat:** monthly payers (RA, KIO) declare several distributions in one
     batch press release months ahead — absence of a FRESH filing is normal and is
     NOT evidence against the event. The standard is any issuer communication, not a
     same-week filing.
6. **Re-check UNCHECKED tickers** — if the script reported unchecked tickers
   (`output/unchecked_tickers_YYYY-MM-DD.txt`), fetch those per-ticker pages
   individually if there are a handful; if there are many, re-run the script later
   instead. Do not skip this.
7. **Spot-check known payers** — positions known to pay monthly (e.g. RA, KIO) or with
   recently announced events, even if nothing else flagged them. Known tricky positions
   (as of 2026-06-12):
   - **SLMNP** (LyondellBasell 6% pfd, $1000 par, $15/qtr) — NO per-ticker coverage
     anywhere; ex-dates ~Jan/Apr/Jul/Oct 15. Verify manually near those dates.
   - **CETXP** (Cemtrex Series 1 pfd) — pays 10% IN PREFERRED SHARES ~quarter-end
     (Mar 31, Jun 30 records), not cash. Calendars miss it entirely.
   - **BACPRP and other BAC preferreds** — declared in batch press releases;
     record dates cluster in early-to-mid July/Oct/Jan/Apr.
   - **PSBYP/PSBZP** (Link Parks OTC pfds) — record 15th of Mar/Jun/Sep/Dec;
     only visible via StockAnalysis `quote/otc/` pages (now in the script's sweep).
   - **NMKBP/NMKCP/NMPWP** (Niagara Mohawk 3.60%/3.90%/3.40% pfds, $100 par) —
     $0.90/$0.975/$0.85 quarterly, record ~16th of Mar/Jun/Sep/Dec; OTC sweep
     covers them.
   - **ARESPB and other NYSE "PB"/"PRB"/"PRx" preferreds** — KNOWN GAP. The
     bare-suffix logic strips ARESPB→ARES and catches the COMMON's dividend, but
     the preferred's OWN dividend is not checked: bulk calendars list it under a
     different format (ARES-PB) and the OTC sweep pattern `^[A-Z]{4,5}P$` doesn't
     match a trailing PB/PRB. StockAnalysis `/stocks/arespb/` 404s (real path is
     `ares-pb`-style, needs translation). Until fixed, a hit on the stripped
     common for one of these positions is a FALSE POSITIVE for the preferred —
     and the preferred's real ex-date may be missed. Verify these manually near
     quarter-end. (ARES Series B: record ~15th of Mar/Jun/Sep/Dec.)

Filter all results: only keep tickers whose **underlying** matches a position.

**Why these dividend sources (as of 2026-06-11):** most bulk calendars tested
(NASDAQ API, StockAnalysis calendar, EarningsWhispers, Finviz, Yahoo, WSJ, Barchart,
Seeking Alpha) are broken, bot-blocked, or miss ADRs/CEFs. Only two are comprehensive:
Benzinga (60 tickers for 2026-06-11 incl. BABA and RA) and Investing.com's AJAX
endpoint (66 tickers, script-only since it requires POST). They have different blind
spots — Investing.com uniquely covers foreign Y-suffix ADRs, Benzinga uniquely covers
some preferreds/CEFs — so the script uses their union. StockAnalysis per-ticker pages
are accurate but rate-capped far below portfolio scale: hit verification only.

### Step 6 — Write Claude's findings
Write to `output/claude_results_YYYY-MM-DD.json`:
```json
[
  {"underlying": "BABA", "event_type": "dividend", "amount_or_ratio": "$1.05", "sources": ["Benzinga", "Alibaba 6-K (gross)", "StockAnalysis"]},
  {"underlying": "SHPH", "event_type": "split",    "amount_or_ratio": "1 for 10", "sources": ["StockAnalysis", "Benzinga", "StockTitan press release"]}
]
```
Use declared GROSS amounts for ADRs (see the ADR amount trap in Step 5).
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
**Always send the email**, whether or not any events were found. A "nothing found" email is expected and confirms the check ran.

If there are **no discrepancies** (including the case where nothing was found): send automatically.
```bash
python check_events.py <positions_file>
```

If there are **discrepancies**: tell the user what they are, then ask:
> "There are X discrepancies that need manual verification (listed above).
> Do you want me to send the email now with the discrepancies flagged in red,
> or verify first and send after?"

Only send after the user confirms in the discrepancy case.

---

## Ticker Parsing Rules (extract underlying before checking)

Apply in this order — first match wins:

| Instrument | Pattern examples | Action |
|---|---|---|
| Human-readable option | `AVGO JUN 05 2026 310.00 PUT`, `XRX JAN 21 '28 7 CALL`, `ASST2 JAN 15 '27 3 CALL` | Extract first word as underlying. Year may be 4-digit (`2026`) or apostrophe form (`'28`). Underlying may contain a digit (`ASST2`). |
| OCC option | `AAPL240119C00150000` | Extract leading symbol as underlying |
| Space share class | `WSO B` | Convert to dot form: `WSO.B` |
| Warrant (separator) | `ACMR.WS`, `ACMR.WT`, `ACMR.W` | Strip suffix — unambiguous |
| Right / Unit (separator) | `ACMR.R`, `ACMR.U` | Strip suffix — unambiguous |
| Preferred (separator) | `BAC.PA`, `BAC-PA` | Strip suffix; check underlying `BAC` |
| Share class | `BRK.A`, `BRK-B` | Keep full ticker (normalize dash to dot: `BRK.B`) |
| **Bare suffix — AMBIGUOUS** | `ACMRW`, `BACPA`, `BACPRA`, `GLW`, `AMPG` | Check **BOTH** the full ticker AND the stripped form |
| Common stock | `AAPL`, `BABA`, `RA` | No change |

**Bare-suffix ambiguity (critical):** a trailing `W`/`R`/`U` or `PA`–`PH`/`PRA`–`PRH`
with no dot/dash separator cannot be disambiguated: `GLW` is Corning (common stock),
not a GL warrant; `AMPG` is AmpliTech (common stock), not AM preferred. But `ZOOZW`
really IS a ZOOZ warrant. So ambiguous tickers are checked under BOTH interpretations —
the full ticker and the stripped underlying. This can produce occasional false-positive
hits (e.g. a GL dividend attributed to a GLW position) — flag them for the user to
dismiss; that is far cheaper than missing a real event. Bare-suffix stripping is
skipped when the stripped form would be a single character (`AU` stays `AU`).

**Human-readable option detection**: if the ticker string contains a space AND contains
a month name (Jan–Dec) AND ends with CALL or PUT, extract everything before the first
space as the underlying.

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
Recipients: stored in `.env` as `EMAIL_RECIPIENTS` (comma-separated; currently one
address — `rohant@jagtradingllc.com`, note the **t** after rohan. **Watch for typos**:
wrong addresses silently swallowed three reports on 2026-06-10/11 before being caught —
SMTP accepts invalid recipients without error).

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

## Known Source Issues (as of 2026-06-11)

| Source | Status | Notes |
|---|---|---|
| StockAnalysis splits calendar | ✓ Working | Splits source 1 of 3. Data is in a SvelteKit inline script (JS object literals, `$`-prefixed symbols). **Missed VRNO on 2026-06-11** — never use alone. |
| Benzinga splits calendar | ✓ Working | Splits source 2 of 3. Server-rendered table, explicit Ex-Date per row. Caught VRNO when StockAnalysis missed it. |
| Investing.com splits calendar | ✓ Working | Splits source 3 of 3. Date-grouped table (date only on first row of each group). Also caught VRNO. |
| StockTitan per-ticker news | ✓ Working | Split verification: `stocktitan.net/news/TICKER/` surfaces the company's own split press release (ratio + effective date). Used in Claude's Step 5, not by the script. |
| Benzinga dividends calendar | ✓ Working | PRIMARY dividends source — one request covers the whole market incl. ADRs (BABA $1.05 gross) and CEFs (RA). Found 60 tickers for 2026-06-11 when NASDAQ API found 6 and MarketBeat 0. |
| Investing.com dividends AJAX | ✓ Working | Second comprehensive bulk (script-only — POST endpoint, Claude's WebFetch cannot POST). Covers ADRs/CEFs incl. foreign Y-suffix ADRs Benzinga misses; Benzinga uniquely covers some preferreds/CEFs. ADR amounts NET of fees — Benzinga gross wins the merge. |
| StockAnalysis per-ticker dividends | ⚠ Hit verification + OTC-pref sweep ONLY | Fine for a handful of requests. **Cannot sustain full-portfolio sweeps** — daily request budget is far below 1400; a 2026-06-11 sweep attempt spent 3 hours in 429 backoff and was killed. ADR amounts shown NET of depositary fee — use Benzinga/6-K gross. **OTC tickers live under `/quote/otc/TICKER/dividend/`** — `/stocks/` 404s on them (how PSBYP/PSBZP were initially missed). **Silent-200 guard:** a flaky HTTP-200 with no history rows once dropped NMPWP (2026-06-16) with no error; the OTC sweep now treats a 200-without-history as UNCHECKED, not "no event". |
| NASDAQ HTML splits page | ✗ JS-rendered | Raw HTML has no data rows — always returned 0. Removed 2026-06-10. |
| MarketBeat dividends calendar | ✓ Working | Supplementary — US equities only; missed BABA and RA on 2026-06-11 |
| NASDAQ API (splits + dividends) | ✗ Timeout | Removed |
| NASDAQTrader splits file | ✗ 404 | Removed |
| TipRanks splits API | ✗ 403 Forbidden | Removed |
| StockAnalysis dividends calendar | ✗ 404 | Removed |
| EarningsWhispers | ✗ Error page | Removed |
| Yahoo Finance batch quote API | ✗ 401 | Tested 2026-06-10 — now requires auth |
| WSJ / Barchart / Seeking Alpha / dividend.com calendars | ✗ 404/blocked | Tested 2026-06-10 |

---

## Output Files

| File | Description |
|---|---|
| `output/python_results_YYYY-MM-DD.csv` | Python findings — attached to email |
| `output/python_results_YYYY-MM-DD.json` | Python findings in JSON — used for comparison |
| `output/claude_results_YYYY-MM-DD.json` | Claude's findings — written in Step 6 |
| `output/unchecked_tickers_YYYY-MM-DD.txt` | Tickers the script could NOT verify (rate limit/errors) — only written when non-empty. Presence means the report is incomplete. |

The `output/` directory and all `.xlsx` files are gitignored.

---

## Discrepancy Policy

| Scenario | Likely cause | Action |
|---|---|---|
| Both agree | High confidence | Proceed |
| Claude only | Python scraper missed it | **Verify manually before acting** |
| Python only | Claude missed a source | **Verify manually before acting** |
| INCOMPLETE CHECK warning | Rate limiting / fetch errors — some tickers never verified | **Treat as a discrepancy.** Re-run or verify the unchecked list before trusting the report |

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
├── test_all.py          ← Offline regression suite — run after ANY code change
├── requirements.txt
├── .env.example         ← Copy to .env and fill in credentials
└── .env                 ← GITIGNORED — credentials only
```
