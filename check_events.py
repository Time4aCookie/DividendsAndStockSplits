"""
Daily dividend and stock split checker.

Usage:
    python check_events.py positions.xlsx [--date YYYY-MM-DD] [--no-email]

    --date      Override the target date (default: next trading day)
    --no-email  Generate the CSV/JSON but skip sending the email
    --gtc       Path to GTC orders Excel (optional, added Monday)
"""

import argparse
import csv
import datetime
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # must run before email_sender is imported (it reads env vars at module level)

import pandas as pd
from ticker_utils import build_ticker_map, get_underlying
from scrapers import get_all_splits, get_all_dividends
from email_sender import send_report, build_html_body
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path('output')
OUTPUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def next_trading_day(from_date: datetime.date | None = None) -> datetime.date:
    """Return the next weekday from today (skips Sat/Sun, not holidays)."""
    d = from_date or datetime.date.today()
    d += datetime.timedelta(days=1)
    while d.weekday() >= 5:   # 5=Sat, 6=Sun
        d += datetime.timedelta(days=1)
    return d


def read_positions(excel_path: str) -> list[str]:
    """Read ticker column from the positions Excel file."""
    df = pd.read_excel(excel_path, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]

    # Find the ticker column (flexible naming)
    candidates = ['ticker', 'symbol', 'sym', 'stock', 'security', 'instrument']
    col = next((c for c in candidates if c in df.columns), None)
    if col is None:
        # Fall back to first column
        col = df.columns[0]
        logger.warning(f"No recognized ticker column found; using first column: '{col}'")

    tickers = df[col].dropna().str.strip().str.upper().tolist()
    logger.info(f"Loaded {len(tickers)} positions from {excel_path}")
    return tickers


def filter_positions(
    position_map: dict[str, dict],
    universe: dict[str, dict],  # {underlying: info} from scraper
) -> dict[str, dict]:
    """Keep only tickers in our positions from the scraped universe."""
    hits: dict[str, dict] = {}
    for underlying, info in universe.items():
        clean = underlying.replace('-', '').replace('.', '').upper()
        # direct match
        if underlying in position_map:
            hits[underlying] = {**info, 'originals': position_map[underlying]['originals']}
        else:
            # fuzzy: strip hyphens/dots for preferred classes
            for pos_und in position_map:
                if pos_und.replace('-', '').replace('.', '').upper() == clean:
                    hits[underlying] = {**info, 'originals': position_map[pos_und]['originals']}
                    break
    return hits


def write_csv(
    splits:    dict[str, dict],
    dividends: dict[str, dict],
    target_date: datetime.date,
) -> Path:
    csv_path = OUTPUT_DIR / f"python_results_{target_date.isoformat()}.csv"
    rows: list[dict] = []
    for ticker, info in splits.items():
        rows.append({
            'underlying':     ticker,
            'event_type':     'split',
            'amount_or_ratio': info.get('ratio', ''),
            'sources':        '|'.join(info.get('sources', [])),
            'originals':      '|'.join(info.get('originals', [ticker])),
            'ex_date':        target_date.isoformat(),
        })
    for ticker, info in dividends.items():
        rows.append({
            'underlying':     ticker,
            'event_type':     'dividend',
            'amount_or_ratio': info.get('amount', ''),
            'sources':        '|'.join(info.get('sources', [])),
            'originals':      '|'.join(info.get('originals', [ticker])),
            'ex_date':        target_date.isoformat(),
        })
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(
            f, fieldnames=['underlying','event_type','amount_or_ratio',
                           'sources','originals','ex_date']
        )
        writer.writeheader()
        writer.writerows(rows)
    logger.info(f"CSV written: {csv_path}")
    return csv_path


def print_summary(
    splits:    dict[str, dict],
    dividends: dict[str, dict],
    target_date: datetime.date,
) -> None:
    print(f"\n{'='*60}")
    print(f"  PYTHON SCRIPT RESULTS  —  Target date: {target_date}")
    print(f"{'='*60}")
    if splits:
        print(f"\n[SPLITS] {len(splits)} position(s) affected:")
        for t, info in sorted(splits.items()):
            origs = ', '.join(info.get('originals', [t]))
            ratio = info.get('ratio', '?')
            srcs  = ', '.join(info.get('sources', []))
            print(f"  {t:10s}  ratio: {ratio:10s}  positions: {origs}  sources: [{srcs}]")
    else:
        print("\n[SPLITS] None found.")
    if dividends:
        print(f"\n[DIVIDENDS] {len(dividends)} position(s) affected:")
        for t, info in sorted(dividends.items()):
            origs = ', '.join(info.get('originals', [t]))
            amt   = info.get('amount', '?')
            srcs  = ', '.join(info.get('sources', []))
            print(f"  {t:10s}  amount: {amt:10s}  positions: {origs}  sources: [{srcs}]")
    else:
        print("\n[DIVIDENDS] None found.")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description='Daily dividend & split checker')
    parser.add_argument('positions', help='Path to positions Excel file')
    parser.add_argument('--date',     help='Target date YYYY-MM-DD (default: next trading day)')
    parser.add_argument('--gtc',      help='Path to GTC orders Excel file (optional)')
    parser.add_argument('--no-email', action='store_true', help='Skip sending email')
    parser.add_argument('--deep', action='store_true',
                        help='Full per-ticker StockAnalysis sweep (~30-45 min, rate-limit '
                             'sensitive). Default fast mode uses bulk calendars + per-ticker '
                             'verification of hits only (~1 min).')
    args = parser.parse_args()

    target_date = (
        datetime.date.fromisoformat(args.date)
        if args.date
        else next_trading_day()
    )
    logger.info(f"Checking events for: {target_date}")

    # 1. Load positions
    raw_tickers   = read_positions(args.positions)
    position_map  = build_ticker_map(raw_tickers)
    logger.info(f"{len(position_map)} unique underlying tickers to check")

    # GTC orders (placeholder — format TBD Monday)
    gtc_map: dict[str, dict] = {}
    if args.gtc:
        gtc_tickers = read_positions(args.gtc)
        gtc_map     = build_ticker_map(gtc_tickers)
        logger.info(f"GTC: {len(gtc_map)} unique underlying tickers")

    # 2. Scrape all sources
    # Splits 'effective' on a skipped weekend day take effect at the target
    # date's open — scan those dates too (e.g. Sat/Sun before a Monday target).
    skipped_dates: list[datetime.date] = []
    d = target_date - datetime.timedelta(days=1)
    while d.weekday() >= 5:   # walk back through Sat/Sun
        skipped_dates.append(d)
        d -= datetime.timedelta(days=1)
    if skipped_dates:
        logger.info(f"Weekend dates also scanned for splits: {[str(x) for x in skipped_dates]}")

    logger.info("Scraping splits sources...")
    all_splits    = get_all_splits(target_date, extra_dates=skipped_dates)

    logger.info("Scraping dividend sources...")
    all_dividends, unchecked_tickers = get_all_dividends(
        target_date, tickers=list(position_map.keys()), deep=args.deep,
    )

    # Tickers that could not be verified (rate limit / errors) — write them out
    # and surface loudly. An empty result with a long unchecked list means the
    # check FAILED, not "no events tomorrow".
    if unchecked_tickers:
        unchecked_path = OUTPUT_DIR / f"unchecked_tickers_{target_date.isoformat()}.txt"
        unchecked_path.write_text('\n'.join(sorted(unchecked_tickers)))
        logger.warning(
            f"{len(unchecked_tickers)} ticker(s) UNCHECKED — full list: {unchecked_path}"
        )

    # 3. Filter to positions we hold
    position_splits    = filter_positions(position_map, all_splits)
    position_dividends = filter_positions(position_map, all_dividends)

    # 4. GTC overlap (flag orders in affected tickers)
    gtc_splits_hits    = filter_positions(gtc_map, all_splits)    if gtc_map else {}
    gtc_dividend_hits  = filter_positions(gtc_map, all_dividends) if gtc_map else {}

    if gtc_splits_hits or gtc_dividend_hits:
        print("\n[GTC ORDERS REQUIRING ADJUSTMENT]")
        for t, info in gtc_splits_hits.items():
            print(f"  SPLIT  {t}  — GTC orders: {', '.join(info.get('originals', [t]))}")
        for t, info in gtc_dividend_hits.items():
            print(f"  DIV    {t}  amt={info.get('amount','?')}  — GTC orders: {', '.join(info.get('originals', [t]))}")

    # 5. Print and write CSV
    print_summary(position_splits, position_dividends, target_date)
    csv_path = write_csv(position_splits, position_dividends, target_date)

    # 6. Write JSON for Claude comparison
    json_path = OUTPUT_DIR / f"python_results_{target_date.isoformat()}.json"
    json_rows: list[dict] = []
    for ticker, info in position_splits.items():
        json_rows.append({'underlying': ticker, 'event_type': 'split',
                          'amount_or_ratio': info.get('ratio', ''),
                          'sources': info.get('sources', [])})
    for ticker, info in position_dividends.items():
        json_rows.append({'underlying': ticker, 'event_type': 'dividend',
                          'amount_or_ratio': info.get('amount', ''),
                          'sources': info.get('sources', [])})
    with open(json_path, 'w') as f:
        json.dump(json_rows, f, indent=2)
    logger.info(f"JSON written: {json_path} (Claude will compare against this)")

    # 7. Check for Claude results and run comparison
    claude_json = OUTPUT_DIR / f"claude_results_{target_date.isoformat()}.json"
    discrepancies: list[str] = []
    if claude_json.exists():
        from compare import load_csv_results, load_claude_results, compare, print_comparison
        py_res     = load_csv_results(csv_path)
        claude_res = load_claude_results(claude_json)
        discrepancies, agreements = compare(py_res, claude_res)
        print_comparison(discrepancies, agreements)
    else:
        print(
            f"\nNOTE: Claude results not found at {claude_json}.\n"
            "Run Claude's independent check first (see CLAUDE.md), "
            "then re-run this script to compare."
        )

    # Unchecked tickers are a discrepancy: the report may be incomplete.
    # This blocks the auto-send path so a rate-limited run can never go out
    # looking like a clean "no events" day.
    if unchecked_tickers:
        preview = ', '.join(sorted(unchecked_tickers)[:10])
        more    = f" (+{len(unchecked_tickers) - 10} more)" if len(unchecked_tickers) > 10 else ''
        warning = (
            f"INCOMPLETE CHECK: {len(unchecked_tickers)} ticker(s) could not be "
            f"verified on StockAnalysis (rate limit/errors): {preview}{more}. "
            f"Full list: output/unchecked_tickers_{target_date.isoformat()}.txt. "
            f"VERIFY OR RE-RUN BEFORE TRUSTING THIS REPORT."
        )
        discrepancies.append(warning)
        print(f"\n[!!] {warning}")

    # 8. Send email
    if not args.no_email:
        html_body = build_html_body(position_splits, position_dividends, target_date)
        send_report(
            report_date=target_date,
            csv_path=csv_path,
            html_body=html_body,
            discrepancies=discrepancies if discrepancies else None,
        )
    else:
        logger.info("--no-email flag set; skipping email.")


if __name__ == '__main__':
    main()
