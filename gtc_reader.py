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

Requires xlrd (for .xls). Read-only — never writes to the trader files.
"""

import os
import glob
import datetime
import logging

import xlrd

from ticker_utils import get_underlying_candidates

logger = logging.getLogger(__name__)

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


def build_gtc_map(gtc_dir: str = GTC_DIR) -> dict[str, list[dict]]:
    """
    Read all trader books and return {underlying: [order, ...]}.
    Each order is filed under EVERY candidate underlying of its ticker (so an
    option/preferred/warrant order matches the event's underlying), matching the
    positions parser's behavior.
    """
    gtc_map: dict[str, list[dict]] = {}
    for trader, path in find_gtc_files(gtc_dir).items():
        try:
            for order in read_gtc_orders(path, trader):
                for underlying, _itype in get_underlying_candidates(order['ticker']):
                    gtc_map.setdefault(underlying, []).append(order)
        except Exception as e:
            logger.error(f"GTC {trader}: failed to read ({type(e).__name__}: {e})")
    return gtc_map
