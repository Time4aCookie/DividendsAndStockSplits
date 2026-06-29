"""
Read GTC (good-till-canceled) order books for the daily dividend/split check.

Three trader order books live in a OneDrive-synced folder (updated in place each
morning). They are old-format .xls with a quirky layout, so they need a
dedicated reader separate from the positions reader:

  - Orders are on the 'Ready_for_Sale' sheet.
  - The HEADER is on the third row (index 2); rows 0-1 are banner/group labels.
  - FOUR columns are named 'Price' (Last, Bid, Ask, and the order limit), so the
    limit-price column is found positionally (the 'Price' immediately after
    'Price Type'), not by name.
  - Side values are 'Buy' and 'Sell Auto'.
  - Orders are laddered (same ticker at several price levels = several rows).
  - Some rows have 0 shares (saved placeholders) — kept, but flagged.

A FOURTH source is the consolidated GTC blotter CSV dropped in the project folder
each day ~3:35pm, named `GTC's_<date>` (no extension). It is ADDITIONAL to the
three morning trader books, not a replacement. We keep only Time-In-Force == GTC
and Status == Live rows. The CSV is a raw REDI export with UNQUOTED
thousands-separator commas in numeric fields (`1,000`, `1,100.00`), so rows are
ragged — we re-join thousands groups before splitting (carefully, so a decimal
like Avg Px `0.000000` followed by an integer is NOT merged). See read_gtc_csv_orders.

Requires xlrd (for .xls). Read-only — never writes to the trader files.
"""

import os
import re
import csv
import glob
import datetime
import logging

import xlrd

from ticker_utils import get_underlying_candidates

logger = logging.getLogger(__name__)

# Project folder where the GTC's_<date> blotter CSV is dropped daily.
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
GTC_CSV_GLOB = "GTC's_*"

# A thousands-separated number that is a STANDALONE field, not the tail of a
# decimal. `(?<![\d.])` stops it attaching to a preceding decimal (Avg Px
# `0.000000,958` stays two fields); the optional `(?:\.\d+)?` handles grouped
# decimals like `1,100.00`; `(?![\d])` stops mid-number false matches.
_THOUSANDS_RE = re.compile(r'(?<![\d.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d])')

def _strip_thousands(line: str) -> str:
    return _THOUSANDS_RE.sub(lambda m: m.group(0).replace(',', ''), line)

# OneDrive-synced folder holding the trader order books. Override with env GTC_DIR.
GTC_DIR = os.getenv(
    'GTC_DIR',
    r'C:\Users\Rohan\Jag Trading LLC\Jag Share - JAG Drive\brad new york',
)

# Per-trader filename match (case-insensitive substrings, all must be present).
# The newest file matching a trader's signature is used, so in-place daily
# updates AND occasional "save as new dated file" both resolve to the current book.
TRADER_SIGNATURES = {
    'ALEX':  ['alex', 'hidden', 'orders'],
    'CRAIG': ['craig', 'hidden', 'orders'],
    'JOSH':  ['josh', 'hidden', 'orders'],
}

SHEET = 'Ready_for_Sale'
STALE_AFTER_DAYS = 4   # warn if the newest matching file is older than this


def find_gtc_files(gtc_dir: str = GTC_DIR) -> dict[str, str]:
    """
    Return {trader: newest_matching_path}. Warns (does not fail) on missing
    traders or stale files so the daily run surfaces the problem loudly.
    """
    found: dict[str, str] = {}
    if not os.path.isdir(gtc_dir):
        logger.error(f"GTC directory not found: {gtc_dir}")
        return found

    all_xls = glob.glob(os.path.join(gtc_dir, '*.xls'))
    now = datetime.datetime.now()
    for trader, sig in TRADER_SIGNATURES.items():
        matches = [
            p for p in all_xls
            if all(s in os.path.basename(p).lower() for s in sig)
        ]
        if not matches:
            logger.warning(f"GTC: no file found for trader {trader} (signature {sig})")
            continue
        newest = max(matches, key=os.path.getmtime)
        age_days = (now - datetime.datetime.fromtimestamp(os.path.getmtime(newest))).days
        if age_days > STALE_AFTER_DAYS:
            logger.warning(
                f"GTC: newest {trader} file is {age_days} days old "
                f"({os.path.basename(newest)}) — may be stale; verify it updated."
            )
        found[trader] = newest
        logger.info(f"GTC: {trader} -> {os.path.basename(newest)} ({age_days}d old)")
    return found


def _detect_columns(sheet) -> dict[str, int]:
    """
    Locate the header row (first row in 0-5 containing a 'ticker' cell) and map
    the columns we need by label, deriving the limit-price column positionally.
    Raises ValueError if the expected layout isn't found (loud, never silent).
    """
    header_row = None
    for r in range(min(6, sheet.nrows)):
        low = [str(sheet.cell_value(r, c)).strip().lower() for c in range(sheet.ncols)]
        if 'ticker' in low:
            header_row = r
            hdr = low
            break
    if header_row is None:
        raise ValueError(f"{SHEET}: no header row with a 'Ticker' column found")

    def find(label):
        return next((c for c, v in enumerate(hdr) if v == label), None)

    cols = {
        'header_row': header_row,
        'ticker': find('ticker'),
        'side':   find('side'),
        'shares': find('shares'),
        'account': find('account'),
    }
    # Limit price = the 'Price' column immediately after 'Price Type'
    pt = find('price type')
    if pt is not None and pt + 1 < len(hdr) and hdr[pt + 1] == 'price':
        cols['price'] = pt + 1
    else:
        cols['price'] = None
    # Market-data prices live in the first three columns (Last / Bid / Ask)
    cols['last'], cols['bid'], cols['ask'] = 0, 1, 2

    if cols['ticker'] is None or cols['side'] is None or cols['shares'] is None:
        raise ValueError(f"{SHEET}: missing required column(s): {cols}")
    return cols


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_gtc_orders(path: str, trader: str) -> list[dict]:
    """
    Parse one trader's 'Ready_for_Sale' sheet into a list of order dicts:
      {trader, ticker, shares, side, price, last, bid, ask, account, row}
    Includes 0-share placeholder rows (flagged via shares == 0).
    """
    book = xlrd.open_workbook(path, on_demand=True)
    if SHEET not in book.sheet_names():
        logger.error(f"GTC {trader}: sheet {SHEET!r} not in {os.path.basename(path)}")
        return []
    sh = book.sheet_by_name(SHEET)
    cols = _detect_columns(sh)

    orders: list[dict] = []
    for r in range(cols['header_row'] + 1, sh.nrows):
        ticker = str(sh.cell_value(r, cols['ticker'])).strip().upper()
        if not ticker:
            continue
        shares = _num(sh.cell_value(r, cols['shares'])) or 0.0
        price  = _num(sh.cell_value(r, cols['price'])) if cols['price'] is not None else None
        orders.append({
            'trader': trader,
            'ticker': ticker,
            'shares': shares,
            'side':   str(sh.cell_value(r, cols['side'])).strip(),
            'price':  price,
            'last':   _num(sh.cell_value(r, cols['last'])),
            'bid':    _num(sh.cell_value(r, cols['bid'])),
            'ask':    _num(sh.cell_value(r, cols['ask'])),
            'account': str(sh.cell_value(r, cols['account'])).strip() if cols['account'] is not None else '',
            'row': r + 1,
        })
    logger.info(f"GTC {trader}: {len(orders)} orders ({sum(1 for o in orders if o['shares'] > 0)} with shares>0)")
    return orders


# ---------------------------------------------------------------------------
# Consolidated GTC blotter CSV (GTC's_<date>) — additional source
# ---------------------------------------------------------------------------

def find_gtc_csv(folder: str = PROJECT_DIR) -> str | None:
    """Return the newest GTC's_<date> blotter file in `folder`, or None."""
    matches = glob.glob(os.path.join(folder, GTC_CSV_GLOB))
    matches = [p for p in matches if os.path.isfile(p)]
    if not matches:
        logger.info("GTC blotter CSV: none found (looked for GTC's_<date>)")
        return None
    return max(matches, key=os.path.getmtime)


def purge_old_gtc_csv(keep_path: str, folder: str = PROJECT_DIR) -> None:
    """Delete prior-day GTC's_<date> files, keeping only `keep_path`."""
    for p in glob.glob(os.path.join(folder, GTC_CSV_GLOB)):
        if os.path.isfile(p) and os.path.abspath(p) != os.path.abspath(keep_path):
            try:
                os.remove(p)
                logger.info(f"GTC blotter: deleted old file {os.path.basename(p)}")
            except OSError as e:
                logger.warning(f"GTC blotter: could not delete {os.path.basename(p)}: {e}")


def read_gtc_csv_orders(path: str) -> list[dict]:
    """
    Parse the consolidated GTC blotter CSV. Keep only Time-In-Force == GTC and
    Status == Live. Returns order dicts shaped like read_gtc_orders (trader='GTC').

    Robustness: rows are re-joined for thousands separators then CSV-split. Any
    GTC+Live row that still doesn't yield the expected column count is NOT
    dropped — its Symbol/Side are recovered by fixed left-anchored position
    (cols 3/4, always before the comma-bearing numeric fields) so a format quirk
    can never silently hide a resting order; price/qty are left None and flagged.
    """
    lines = open(path, encoding='utf-8', errors='replace').read().splitlines()
    if not lines:
        return []
    header = next(csv.reader([lines[0]]))
    H = len(header)
    idx = {name.strip(): i for i, name in enumerate(header)}
    i_sym = idx.get('Symbol', 3)
    i_side = idx.get('Side', 4)

    def _is_gtc_live(line: str) -> bool:
        return bool(re.search(r'(^|,)GTC(,|$)', line) and re.search(r'(^|,)Live(,|$)', line))

    orders: list[dict] = []
    recovered = 0
    for ln in lines[1:]:
        fields = next(csv.reader([_strip_thousands(ln)]))
        if len(fields) == H:
            rec = dict(zip(header, fields))
            if rec.get('Time In Force', '').strip().upper() == 'GTC' and \
               rec.get('Status', '').strip().lower() == 'live':
                _num_px = _num(rec.get('Price', ''))
                orders.append({
                    'trader': 'GTC',
                    'ticker': rec.get('Symbol', '').strip().upper(),
                    'shares': _num(rec.get('Qty', '')) or 0.0,
                    'side':   rec.get('Side', '').strip(),
                    'price':  _num_px,
                    'last': None, 'bid': None, 'ask': None,
                    'account': rec.get('Portfolio', '').strip(),
                    'row': None,
                })
        elif _is_gtc_live(ln):
            # Parser fell short on a GTC+Live row — recover symbol/side, never drop.
            raw = next(csv.reader([ln]))
            sym = raw[i_sym].strip().upper() if len(raw) > i_sym else ''
            if sym:
                recovered += 1
                orders.append({
                    'trader': 'GTC', 'ticker': sym, 'shares': 0.0,
                    'side': raw[i_side].strip() if len(raw) > i_side else '',
                    'price': None, 'last': None, 'bid': None, 'ask': None,
                    'account': '', 'row': None, 'parse_incomplete': True,
                })

    logger.info(f"GTC blotter ({os.path.basename(path)}): {len(orders)} GTC+Live orders"
                + (f" ({recovered} recovered via fallback — verify)" if recovered else ""))
    return orders


def build_gtc_map(gtc_dir: str = GTC_DIR, project_dir: str = PROJECT_DIR) -> dict[str, list[dict]]:
    """
    Read all GTC sources and return {underlying: [order, ...]}:
      - the three morning trader books (.xls, OneDrive), AND
      - the consolidated GTC blotter CSV (GTC's_<date>, project folder).
    Each order is filed under EVERY candidate underlying of its ticker (so an
    option/preferred/warrant order matches the event's underlying), matching the
    positions parser's behavior.
    """
    gtc_map: dict[str, list[dict]] = {}

    def _file(order):
        for underlying, _itype in get_underlying_candidates(order['ticker']):
            gtc_map.setdefault(underlying, []).append(order)

    # Three morning trader books
    for trader, path in find_gtc_files(gtc_dir).items():
        try:
            for order in read_gtc_orders(path, trader):
                _file(order)
        except Exception as e:
            logger.error(f"GTC {trader}: failed to read ({type(e).__name__}: {e})")

    # Consolidated GTC blotter CSV (additional source)
    csv_path = find_gtc_csv(project_dir)
    if csv_path:
        try:
            for order in read_gtc_csv_orders(csv_path):
                _file(order)
            purge_old_gtc_csv(keep_path=csv_path, folder=project_dir)
        except Exception as e:
            logger.error(f"GTC blotter CSV: failed to read ({type(e).__name__}: {e})")

    return gtc_map
