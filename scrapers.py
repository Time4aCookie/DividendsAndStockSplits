"""
Web scrapers for stock splits and dividend ex-dates.

Sources:
  Splits  : StockAnalysis calendar, Benzinga calendar, Investing.com calendar
  Dividends: StockAnalysis per-ticker (primary), MarketBeat calendar (supplementary)

Removed sources (broken as of 2026-06-10):
  NASDAQ API          — consistent timeouts
  NASDAQ splits HTML  — JS-rendered; raw HTML contains no data rows
  TipRanks API/HTML   — 403 Forbidden
  NASDAQTrader txt    — 404
  StockAnalysis dividends calendar (/actions/dividends/) — 404
  EarningsWhispers    — error page
  Yahoo batch quote API — 401 (now requires auth)
"""

import datetime
import json as json_lib
import re
import time
import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Per-IP rate-limit backoff shared across threads.
# When any thread gets a 429, it sets this to (now + retry_after).
# All threads sleep until this time before making the next request.
import threading as _threading
_rate_lock        = _threading.Lock()
_rate_limit_until = 0.0   # monotonic seconds; 0 = no active backoff


def _wait_for_rate_limit() -> None:
    """Sleep until any active rate-limit backoff expires."""
    with _rate_lock:
        until = _rate_limit_until
    wait = until - time.monotonic()
    if wait > 0:
        time.sleep(wait + 0.05)   # +50ms buffer after the window expires


def _set_rate_limit(retry_after_seconds: int) -> None:
    global _rate_limit_until
    with _rate_lock:
        _rate_limit_until = max(_rate_limit_until, time.monotonic() + retry_after_seconds)


def _get(url: str, timeout: int = 15) -> requests.Response | None:
    try:
        resp = SESSION.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp
    except Exception as e:
        logger.warning(f"GET {url} failed: {e}")
        return None


def _get_perticker(url: str, timeout: int = 12) -> tuple[str, requests.Response | None]:
    """
    GET for per-ticker requests. Obeys shared rate-limit backoff and retries
    once after a 429. Does NOT log 404s (most tickers simply won't have pages).

    Returns (status, response):
      ('ok', resp)       — page fetched successfully
      ('notfound', None) — 404: the ticker has no page here (a real answer)
      ('error', None)    — timeout / 429 after retries / 5xx: we DON'T know.
                           Caller must treat the ticker as UNCHECKED, never as
                           "no event" — that distinction is what keeps rate
                           limiting from silently producing an empty report.
    """
    _wait_for_rate_limit()
    for attempt in range(2):
        try:
            resp = SESSION.get(url, timeout=timeout)
            if resp.status_code == 429:
                retry_after = max(int(resp.headers.get('Retry-After', 120)), 120)
                _set_rate_limit(retry_after)
                logger.warning(f"Rate limited — backing off {retry_after}s (attempt {attempt + 1})")
                time.sleep(retry_after + 0.5)
                continue   # retry
            if resp.status_code == 404:
                return 'notfound', None
            resp.raise_for_status()
            return 'ok', resp
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code == 429:
                retry_after = max(int(e.response.headers.get('Retry-After', 120)), 120)
                _set_rate_limit(retry_after)
                logger.warning(f"Rate limited — backing off {retry_after}s (attempt {attempt + 1})")
                time.sleep(retry_after + 0.5)
                continue
            if code == 404:
                return 'notfound', None
            logger.warning(f"GET {url} failed: {e}")
            return 'error', None
        except Exception as e:
            logger.warning(f"GET {url} failed: {e}")
            return 'error', None
    return 'error', None   # exhausted retries (persistent 429)


def _date_variants(d: datetime.date) -> set[str]:
    """All common string representations of a date for loose matching."""
    return {
        d.isoformat(),                                       # 2026-06-11
        d.strftime('%m/%d/%Y'),                              # 06/11/2026
        d.strftime('%m/%d/%y'),                              # 06/11/26
        d.strftime('%b %d, %Y'),                             # Jun 11, 2026 (zero-padded)
        d.strftime('%B %d, %Y'),                             # June 11, 2026 (zero-padded)
        f"{d.strftime('%b')} {d.day}, {d.year}",             # Jun 11, 2026 (no leading zero)
        f"{d.strftime('%B')} {d.day}, {d.year}",             # June 11, 2026 (no leading zero)
    }


def _get_sveltekit_script(html: str) -> str | None:
    """Return the SvelteKit inline data script, or None if not found."""
    for sc in re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL):
        if '__sveltekit' in sc:
            return sc
    return None


# ---------------------------------------------------------------------------
# SPLITS
# ---------------------------------------------------------------------------

# StockAnalysis splits page SvelteKit format (as of 2026-06):
#   data:[{date:"Jun 11, 2026",symbol:"$SHPH",name:"...",splitType:"...",splitRatio:"1 for 10"}, ...]
# Keys are unquoted JS object literals, symbol has a "$" prefix.
_SA_SPLITS_ROW_RE = re.compile(
    r'\{date:"(?P<date>[^"]+)"'
    r',symbol:"(?P<symbol>[^"]+)"'
    r',name:"[^"]*"'
    r',splitType:"[^"]*"'
    r',splitRatio:"(?P<ratio>[^"]*)"\}'
)


def _combined_date_variants(
    target_date: datetime.date,
    extra_dates: list[datetime.date] | None,
) -> list[str]:
    """Date-string variants for the target date plus any extra dates (e.g. the
    weekend days a Monday target skipped — a split 'effective' Saturday takes
    effect at Monday's open and must not be missed by exact-date filtering)."""
    variants: list[str] = []
    for d in [target_date] + list(extra_dates or []):
        variants.extend(_date_variants(d))
    return variants


def scrape_stockanalysis_splits(
    target_date: datetime.date,
    extra_dates: list[datetime.date] | None = None,
) -> dict[str, str]:
    """StockAnalysis upcoming splits calendar. Returns {ticker: ratio_str}."""
    found: dict[str, str] = {}
    resp = _get('https://stockanalysis.com/actions/splits/')
    if not resp:
        return found

    date_variants = _combined_date_variants(target_date, extra_dates)

    # Primary: parse SvelteKit inline JS object literal
    svelte = _get_sveltekit_script(resp.text)
    if svelte:
        for m in _SA_SPLITS_ROW_RE.finditer(svelte):
            date_str = m.group('date')
            sym_raw  = m.group('symbol')
            ratio    = m.group('ratio')
            if any(v in date_str for v in date_variants):
                # Strip "$" prefix and any HTML tags (symbol field has html:true)
                sym = re.sub(r'<[^>]+>', '', sym_raw).lstrip('$').strip().upper()
                if sym:
                    found[sym] = ratio

    # Fallback: HTML table (in case page structure changes)
    # SA splits table columns: Date | Symbol | Company | Type | Ratio
    if not found:
        soup = BeautifulSoup(resp.text, 'html.parser')
        for row in soup.select('table tbody tr'):
            cells = row.find_all('td')
            if len(cells) < 2:
                continue
            date_text = cells[0].get_text(strip=True)
            if any(v in date_text for v in date_variants):
                sym   = cells[1].get_text(strip=True).lstrip('$').upper()
                ratio = cells[-1].get_text(strip=True)
                if sym and re.match(r'^[A-Z.]{1,10}$', sym):
                    found[sym] = ratio

    return found


def scrape_benzinga_splits(
    target_date: datetime.date,
    extra_dates: list[datetime.date] | None = None,
) -> dict[str, str]:
    """
    Benzinga stock-splits calendar. Server-rendered table with explicit
    Ex-Date on every row. Covers NASDAQ, NYSE, AMEX, OTC, and BATS ETFs.
    Returns {ticker: ratio_str}.
    """
    found: dict[str, str] = {}
    resp = _get('https://www.benzinga.com/calendars/stock-splits')
    if not resp:
        return found

    # Benzinga format: 06/11/2026. Accept target + skipped weekend dates.
    accepted = {d.strftime('%m/%d/%Y') for d in [target_date] + list(extra_dates or [])}
    soup = BeautifulSoup(resp.text, 'html.parser')

    for table in soup.find_all('table'):
        # Map column names to indexes from the header (strip sort arrows)
        headers = [th.get_text(strip=True).replace('▲', '').replace('▼', '').strip().lower()
                   for th in table.find_all('th')]
        try:
            i_date   = next(i for i, h in enumerate(headers) if 'ex-date' in h or 'ex date' in h)
            i_ticker = next(i for i, h in enumerate(headers) if 'ticker' in h or 'symbol' in h)
            i_ratio  = next(i for i, h in enumerate(headers) if 'ratio' in h)
        except StopIteration:
            continue   # not the splits table

        for row in table.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) <= max(i_date, i_ticker, i_ratio):
                continue
            if cells[i_date].get_text(strip=True) in accepted:
                sym   = cells[i_ticker].get_text(strip=True).upper()
                ratio = cells[i_ratio].get_text(strip=True)
                if sym and re.match(r'^[A-Z0-9.]{1,10}$', sym):
                    found[sym] = ratio
        break   # first matching table only

    return found


# Investing.com company cell looks like: "Global Mofy Metaverse ( GMM )"
_INVESTING_TICKER_RE = re.compile(r'\(\s*([A-Z0-9.]{1,10})\s*\)')


def scrape_investing_splits(
    target_date: datetime.date,
    extra_dates: list[datetime.date] | None = None,
) -> dict[str, str]:
    """
    Investing.com stock-split calendar. Date-grouped table: the date cell is
    filled only on the FIRST row of each date group, so we carry it forward.
    Returns {ticker: ratio_str}.
    """
    found: dict[str, str] = {}
    resp = _get('https://www.investing.com/stock-split-calendar/')
    if not resp:
        return found

    date_variants = _combined_date_variants(target_date, extra_dates)
    soup = BeautifulSoup(resp.text, 'html.parser')

    for table in soup.find_all('table'):
        headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]
        if not any('split' in h for h in headers):
            continue

        current_date = ''
        for row in table.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) < 3:
                continue
            date_text = cells[0].get_text(strip=True)
            if date_text:
                current_date = date_text   # new date group starts
            if not any(v in current_date for v in date_variants):
                continue
            m = _INVESTING_TICKER_RE.search(cells[1].get_text(' ', strip=True))
            if m:
                found[m.group(1)] = cells[2].get_text(strip=True)
        break

    return found


def get_all_splits(
    target_date: datetime.date,
    extra_dates: list[datetime.date] | None = None,
) -> dict[str, dict]:
    """
    Aggregate splits from all sources.
    extra_dates: non-trading dates the target skipped (weekend before a Monday
    target) — splits 'effective' on those dates hit at the target's open.
    Returns {ticker: {'ratio': str, 'sources': [str, ...]}}.
    """
    results: dict[str, dict] = {}
    sources = [
        ('StockAnalysis', scrape_stockanalysis_splits),
        ('Benzinga',      scrape_benzinga_splits),
        ('Investing.com', scrape_investing_splits),
    ]
    for name, fn in sources:
        try:
            found = fn(target_date, extra_dates)
            logger.info(f"{name} splits: {len(found)} tickers")
            for sym, ratio in found.items():
                if sym not in results:
                    results[sym] = {'ratio': ratio, 'sources': []}
                elif ratio and not results[sym]['ratio']:
                    results[sym]['ratio'] = ratio
                results[sym]['sources'].append(name)
        except Exception as e:
            logger.error(f"{name} splits failed: {e}")
        time.sleep(0.5)
    return results


# ---------------------------------------------------------------------------
# DIVIDENDS — per-ticker StockAnalysis parsing
# ---------------------------------------------------------------------------

# StockAnalysis per-ticker dividend page SvelteKit format (as of 2026-06):
#   history:[{dt:"2026-06-11",amt:"$1.030",dec:"n/a",record:"2026-06-11",pay:"2026-07-13"}, ...]
_SA_DIV_HIST_RE = re.compile(r'\{dt:"(?P<dt>[^"]+)",amt:"(?P<amt>[^"]+)"')


def _parse_sa_dividend_page(html: str, target_date: datetime.date) -> dict | None:
    """
    Parse a StockAnalysis /dividend/ page for target_date.
    Returns {'amount': '$X.XX', 'source': 'StockAnalysis'} on match, else None.

    Strategies (in order):
      1. SvelteKit history array (dt/amt keys) — current format as of 2026-06
      2. __NEXT_DATA__ JSON — legacy format
      3. HTML table scan — universal fallback
    """
    date_variants = _date_variants(target_date)

    # Strategy 1: SvelteKit history array
    svelte = _get_sveltekit_script(html)
    if svelte:
        for m in _SA_DIV_HIST_RE.finditer(svelte):
            dt  = m.group('dt')
            amt = m.group('amt')
            if any(v in dt for v in date_variants):
                return {
                    'amount': amt if amt.startswith('$') else f'${amt}',
                    'source': 'StockAnalysis',
                }

    # Strategy 2: __NEXT_DATA__ JSON (legacy)
    nd = re.search(r'id="__NEXT_DATA__"[^>]*>(.+?)</script>', html, re.DOTALL)
    if nd:
        try:
            data   = json_lib.loads(nd.group(1))
            amount = _search_dividend_in_json(data, date_variants)
            if amount is not None:
                amt_str = f'${amount}' if amount and not str(amount).startswith('$') else str(amount)
                return {'amount': amt_str, 'source': 'StockAnalysis'}
        except Exception:
            pass

    # Strategy 3: HTML table scan
    # SA dividend history columns: Ex-Dividend Date | Cash Amount | Record Date | Pay Date.
    # Match the ex-date column ONLY — matching the whole row would false-hit
    # rows whose record/pay date equals the target.
    soup = BeautifulSoup(html, 'html.parser')
    for row in soup.select('table tbody tr'):
        cells = row.find_all('td')
        if not cells:
            continue
        ex_text = cells[0].get_text(strip=True)
        if any(v in ex_text for v in date_variants):
            for cell in cells[1:]:
                text = cell.get_text(strip=True)
                # Match amounts like $1.030, 1.03, $0.118
                if re.match(r'^\$?[\d]+\.[\d]+$', text):
                    return {'amount': f'${text.lstrip("$")}', 'source': 'StockAnalysis'}
            # Date matched but couldn't parse amount — still a hit
            return {'amount': '', 'source': 'StockAnalysis'}

    return None


def _search_dividend_in_json(obj, date_variants: set, depth: int = 0) -> str | None:
    """
    Recursively search a JSON structure for a dividend record whose ex-date
    matches any of date_variants.
    Returns the amount string if found, '' if date matched but no amount, None if not found.
    """
    if depth > 12:
        return None
    if isinstance(obj, dict):
        DATE_KEYS = ('exDate', 'ex_date', 'date', 'exDividendDate', 'ex')
        AMT_KEYS  = ('amount', 'cash', 'dividend', 'dividendAmount', 'cashAmount', 'adjDividend')
        for dk in DATE_KEYS:
            if dk in obj:
                if any(v in str(obj[dk]) for v in date_variants):
                    for ak in AMT_KEYS:
                        if obj.get(ak) not in (None, '', 0):
                            return str(obj[ak])
                    return ''
        for v in obj.values():
            result = _search_dividend_in_json(v, date_variants, depth + 1)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _search_dividend_in_json(item, date_variants, depth + 1)
            if result is not None:
                return result
    return None


def _is_checkable_ticker(t: str) -> bool:
    """
    Return True if ticker looks like a real equity worth checking on StockAnalysis.
    Filters out CUSIPs (start with digit), unextracted options (contain spaces),
    and strings too long to be a valid ticker.
    """
    if not t or ' ' in t:
        return False
    if t[0].isdigit():       # CUSIPs and numeric IDs
        return False
    if len(t) > 8:           # no real equity ticker is this long
        return False
    return True


def _fetch_sa_dividend(ticker: str, target_date: datetime.date) -> tuple[str, dict | None, bool]:
    """
    Fetch StockAnalysis per-ticker dividend page.
    Tries /stocks/TICKER/ first; if that 404s, tries /etf/TICKER/.

    Returns (ticker, result, checked):
      result  — {'amount', 'source'} if the ticker has an ex-date on target_date, else None
      checked — True if we got a definitive answer (page parsed, or 404 on both
                paths). False if any request errored — the ticker is UNCHECKED
                and must be reported as such, never treated as "no event".
    """
    time.sleep(1.2)   # 1.2s pacing — 0.8s proved too aggressive at full scale (sustained 429s)
    any_error = False
    # 'quote/otc' covers OTC-traded securities (e.g. delisted-to-OTC preferreds
    # like PSBYP/PSBZP) that /stocks/ 404s on — discovered 2026-06-12 when both
    # were missed because every path tried returned 404.
    for path_prefix in ('stocks', 'etf', 'quote/otc'):
        url = f'https://stockanalysis.com/{path_prefix}/{ticker.lower()}/dividend/'
        status, resp = _get_perticker(url)
        if status == 'ok':
            # Page loaded — definitive answer; don't try the other prefix
            return ticker, _parse_sa_dividend_page(resp.text, target_date), True
        if status == 'error':
            any_error = True
        # 'notfound' — try the other prefix
    return ticker, None, not any_error


def scrape_stockanalysis_dividends_perticker(
    target_date: datetime.date,
    tickers: list[str],
) -> tuple[dict[str, dict], list[str]]:
    """
    Check every supplied underlying ticker on StockAnalysis for a dividend
    ex-date matching target_date. Sequential with 0.8s pacing to stay under
    rate limits (~14 min for ~1000 tickers).

    Returns (results, unchecked):
      results   — {ticker: {'amount': str, 'source': 'StockAnalysis'}}
      unchecked — tickers we could NOT verify (rate limit / errors).
                  These must be surfaced to the user, never silently dropped.
    """
    results:   dict[str, dict] = {}
    unchecked: list[str] = []

    # Filter out junk before making any requests
    valid = [t for t in tickers if _is_checkable_ticker(t)]
    skipped = len(tickers) - len(valid)
    if skipped:
        logger.info(f"StockAnalysis per-ticker: skipping {skipped} non-equity tickers (CUSIPs, options, etc.)")
    logger.info(f"StockAnalysis per-ticker: checking {len(valid)} tickers (~{len(valid) * 1.5 / 60:.0f} min)...")

    for done, t in enumerate(sorted(valid), start=1):
        ticker, result, checked = _fetch_sa_dividend(t, target_date)
        if not checked:
            unchecked.append(ticker)
        elif result is not None:
            results[ticker] = result
            logger.info(f"  SA HIT: {ticker} — {result.get('amount', '?')}")
        if done % 100 == 0:
            logger.info(
                f"  StockAnalysis progress: {done}/{len(valid)} checked, "
                f"{len(results)} hits, {len(unchecked)} unchecked"
            )

    logger.info(
        f"StockAnalysis per-ticker: {len(results)} hits from {len(valid)} tickers; "
        f"{len(unchecked)} UNCHECKED"
    )
    if unchecked:
        logger.warning(
            f"{len(unchecked)} ticker(s) could not be verified (rate limit/errors) — "
            f"results are INCOMPLETE: {', '.join(unchecked[:15])}"
            + (' ...' if len(unchecked) > 15 else '')
        )
    return results, unchecked


# ---------------------------------------------------------------------------
# DIVIDENDS — Benzinga calendar (primary bulk source)
# ---------------------------------------------------------------------------

def scrape_benzinga_dividends(target_date: datetime.date) -> dict[str, dict]:
    """
    Benzinga dividends calendar — primary bulk source. One server-rendered
    table covers the whole market for several days around today, INCLUDING
    ADRs (BABA) and CEFs (RA) that other calendars miss. Amounts are the
    declared GROSS (e.g. BABA $1.05), which is what the price drops by on
    ex-date — unlike StockAnalysis, which lists ADRs net of depositary fees.
    Table columns: Ex-Date | ticker | Company | Payments per year | Dividend | Yield | ...
    """
    results: dict[str, dict] = {}
    resp = _get('https://www.benzinga.com/calendars/dividends', timeout=25)
    if not resp:
        return results

    date_mdY = target_date.strftime('%m/%d/%Y')   # Benzinga format: 06/11/2026
    soup = BeautifulSoup(resp.text, 'html.parser')

    for table in soup.find_all('table'):
        headers = [th.get_text(strip=True).replace('▲', '').replace('▼', '').strip().lower()
                   for th in table.find_all('th')]
        try:
            i_date   = next(i for i, h in enumerate(headers) if 'ex-date' in h or 'ex date' in h)
            i_ticker = next(i for i, h in enumerate(headers) if 'ticker' in h or 'symbol' in h)
            i_amount = next(i for i, h in enumerate(headers) if h == 'dividend' or 'amount' in h)
        except StopIteration:
            continue

        for row in table.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) <= max(i_date, i_ticker, i_amount):
                continue
            if cells[i_date].get_text(strip=True) == date_mdY:
                sym = cells[i_ticker].get_text(strip=True).upper()
                amt = cells[i_amount].get_text(strip=True)
                if sym and re.match(r'^[A-Z0-9.]{1,10}$', sym):
                    results[sym] = {'amount': amt, 'source': 'Benzinga'}
        break

    return results


# ---------------------------------------------------------------------------
# DIVIDENDS — Investing.com calendar (second comprehensive bulk source)
# ---------------------------------------------------------------------------

def scrape_investing_dividends(target_date: datetime.date) -> dict[str, dict]:
    """
    Investing.com dividends calendar via its AJAX endpoint (POST with a date
    filter, country=US). Second comprehensive bulk source — covers ADRs and
    CEFs like Benzinga. NOTE: lists ADR amounts NET of depositary fees (BABA
    shows 1.03, not the declared 1.05), so it ranks below Benzinga in the
    merge — its amounts only fill in when Benzinga lacks the ticker.
    """
    results: dict[str, dict] = {}
    try:
        resp = SESSION.post(
            'https://www.investing.com/dividends-calendar/Service/getCalendarFilteredData',
            data={
                'country[]': '5',                       # United States
                'dateFrom': target_date.isoformat(),
                'dateTo':   target_date.isoformat(),
                'currentTab': 'custom',
                'limit_from': '0',
            },
            headers={
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': 'https://www.investing.com/dividends-calendar/',
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        html = payload.get('data', '') if isinstance(payload, dict) else ''
    except Exception as e:
        logger.warning(f"Investing.com dividends request failed: {e}")
        return results

    date_variants = _date_variants(target_date)
    soup = BeautifulSoup(f'<table>{html}</table>', 'html.parser')
    for row in soup.find_all('tr'):
        cells = row.find_all('td')
        if len(cells) < 4:
            continue
        # Columns: flag | company (ticker in <a>) | ex-date | amount | ...
        a = cells[1].find('a')
        sym = a.get_text(strip=True).upper() if a else ''
        date_text = cells[2].get_text(strip=True)
        amt = cells[3].get_text(strip=True)
        if sym and re.match(r'^[A-Z0-9.]{1,10}$', sym) and any(v in date_text for v in date_variants):
            results[sym] = {'amount': f'${amt}' if amt and not amt.startswith('$') else amt,
                            'source': 'Investing.com'}
    return results


# ---------------------------------------------------------------------------
# DIVIDENDS — MarketBeat calendar (supplementary)
# ---------------------------------------------------------------------------

def scrape_marketbeat_dividends(target_date: datetime.date) -> dict[str, dict]:
    """
    MarketBeat dividend announcements — supplementary cross-check for US equities.

    NOTE: the /ex-dividend-date/YYYY-MM-DD/ URL is misleading — the page IGNORES
    the date and always shows a rolling table of recent dividend announcements
    with mixed ex-dates. We MUST filter rows by the Ex-Dividend Date column;
    trusting all rows would report dividends weeks early.
    Table columns: Company | Period | Amount | Yield | Ex-Dividend Date | ...
    The Company cell starts with the ticker ("CASY Casey's General Stores").
    """
    results: dict[str, dict] = {}
    url = f'https://www.marketbeat.com/dividends/ex-dividend-date/{target_date.isoformat()}/'
    resp = _get(url)
    if not resp:
        return results

    # MarketBeat date format has no leading zeros: 6/11/2026
    date_mdy = f'{target_date.month}/{target_date.day}/{target_date.year}'

    soup = BeautifulSoup(resp.text, 'html.parser')
    for table in soup.find_all('table'):
        headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]
        try:
            i_company = next(i for i, h in enumerate(headers) if 'company' in h)
            i_amount  = next(i for i, h in enumerate(headers) if 'amount' in h)
            i_exdate  = next(i for i, h in enumerate(headers) if 'ex-dividend' in h or 'ex dividend' in h)
        except StopIteration:
            continue

        for row in table.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) <= max(i_company, i_amount, i_exdate):
                continue
            if cells[i_exdate].get_text(strip=True) != date_mdy:
                continue
            # Ticker is the first whitespace token of the company cell
            company_text = cells[i_company].get_text(' ', strip=True)
            sym = company_text.split()[0].upper() if company_text else ''
            amt = cells[i_amount].get_text(strip=True)
            if sym and re.match(r'^[A-Z0-9.]{1,10}$', sym):
                results[sym] = {'amount': amt, 'source': 'MarketBeat'}
        break

    return results


# ---------------------------------------------------------------------------
# DIVIDENDS — aggregator
# ---------------------------------------------------------------------------

def get_all_dividends(
    target_date: datetime.date,
    tickers: list[str] | None = None,
    deep: bool = False,
) -> tuple[dict[str, dict], list[str]]:
    """
    Aggregate dividends from all sources.
    tickers: underlying tickers from our positions.

    Fast mode (default): Benzinga bulk calendar (1 request, covers ADRs/CEFs,
    gross amounts) + MarketBeat bulk (1 request) + StockAnalysis per-ticker
    verification of position hits only (a handful of requests). ~1 minute.

    Deep mode (--deep): additionally sweeps EVERY position ticker on
    StockAnalysis per-ticker (~30-45 min, rate-limit sensitive). Use as an
    occasional audit of the bulk sources, not for the daily run.

    Returns (merged, unchecked):
      merged    — {ticker: {'amount': str, 'sources': [str, ...]}}
      unchecked — tickers that could not be verified. In fast mode, if the
                  primary bulk source fails entirely, ALL checkable tickers
                  are reported unchecked — a failed sweep must never look
                  like a clean "no events" day.
    """
    merged:    dict[str, dict] = {}
    unchecked: list[str] = []

    def _add(sym: str, amount: str, source: str) -> None:
        if sym not in merged:
            merged[sym] = {'amount': '', 'sources': []}
        if source not in merged[sym]['sources']:
            merged[sym]['sources'].append(source)
        if not merged[sym]['amount'] and amount:
            merged[sym]['amount'] = amount

    # Primary bulk: Benzinga (gross amounts first so they win the merge)
    benzinga_ok = False
    try:
        bz = scrape_benzinga_dividends(target_date)
        benzinga_ok = len(bz) > 0
        logger.info(f"Benzinga dividends: {len(bz)} tickers for {target_date}")
        for sym, info in bz.items():
            _add(sym, info.get('amount', ''), 'Benzinga')
    except Exception as e:
        logger.error(f"Benzinga dividends failed: {e}")

    time.sleep(0.5)

    # Second comprehensive bulk: Investing.com AJAX (also covers ADRs/CEFs;
    # amounts are net-of-fee for ADRs, so Benzinga's gross wins the merge)
    investing_ok = False
    try:
        inv = scrape_investing_dividends(target_date)
        investing_ok = len(inv) > 0
        logger.info(f"Investing.com dividends: {len(inv)} tickers for {target_date}")
        for sym, info in inv.items():
            _add(sym, info.get('amount', ''), 'Investing.com')
    except Exception as e:
        logger.error(f"Investing.com dividends failed: {e}")

    time.sleep(0.5)

    # Tertiary: MarketBeat (date-filtered announcements; US equities only)
    try:
        mb = scrape_marketbeat_dividends(target_date)
        logger.info(f"MarketBeat dividends: {len(mb)} tickers for {target_date}")
        for sym, info in mb.items():
            _add(sym, info.get('amount', ''), 'MarketBeat')
    except Exception as e:
        logger.error(f"MarketBeat dividends failed: {e}")

    if deep and tickers:
        # Full per-ticker sweep — slow, thorough audit
        try:
            sa_results, unchecked = scrape_stockanalysis_dividends_perticker(target_date, tickers)
            for sym, info in sa_results.items():
                _add(sym, info.get('amount', ''), 'StockAnalysis')
        except Exception as e:
            logger.error(f"StockAnalysis per-ticker sweep failed: {e}")
            unchecked = [t for t in tickers if _is_checkable_ticker(t)]
    elif tickers:
        if not benzinga_ok and not investing_ok:
            # BOTH comprehensive bulk sources failed — no real sweep happened.
            # MarketBeat alone misses ADRs/CEFs, so everything is unverified.
            logger.warning(
                "Both Benzinga and Investing.com bulk returned nothing — no comprehensive "
                "dividend sweep ran. All tickers reported UNCHECKED; re-run later or use --deep."
            )
            unchecked = [t for t in tickers if _is_checkable_ticker(t)]
        else:
            # Verify position hits on StockAnalysis per-ticker (cheap: only hits).
            # Keep the bulk (gross) amount — SA lists ADRs net of depositary fees.
            position_hits = [sym for sym in merged if sym in set(tickers)]
            for sym in position_hits:
                try:
                    _, result, checked = _fetch_sa_dividend(sym, target_date)
                    if result is not None:
                        _add(sym, '', 'StockAnalysis')   # confirm source, keep bulk amount
                    elif checked:
                        logger.warning(
                            f"  {sym}: bulk sources show a dividend but StockAnalysis does not — "
                            f"flagging for manual verification"
                        )
                except Exception as e:
                    logger.warning(f"  {sym}: SA verification errored ({e}) — hit stands on bulk sources")

            # OTC/illiquid preferreds (5-6 letter tickers ending in P) are INVISIBLE
            # to every bulk calendar — PSBYP/PSBZP were missed 2026-06-12 this way.
            # Always per-ticker check them; tickers with no page anywhere are
            # reported UNCHECKED so the gap is loud, never silent.
            otc_pref = sorted(
                t for t in set(tickers)
                if re.match(r'^[A-Z]{4,5}P$', t) and _is_checkable_ticker(t) and t not in merged
            )
            if otc_pref:
                logger.info(f"OTC-preferred sweep: checking {len(otc_pref)} bulk-invisible tickers...")
                for sym in otc_pref:
                    try:
                        _, result, checked = _fetch_sa_dividend(sym, target_date)
                        if result is not None:
                            _add(sym, result.get('amount', ''), 'StockAnalysis')
                            logger.info(f"  OTC-pref HIT: {sym} — {result.get('amount', '?')}")
                        elif not checked:
                            unchecked.append(sym)
                    except Exception:
                        unchecked.append(sym)
                if unchecked:
                    logger.warning(
                        f"OTC-preferred sweep: {len(unchecked)} ticker(s) have no per-ticker "
                        f"coverage anywhere — verify manually: {', '.join(unchecked)}"
                    )

    return merged, unchecked
