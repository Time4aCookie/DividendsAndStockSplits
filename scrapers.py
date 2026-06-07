"""
Web scrapers for stock splits and dividend ex-dates.

Sources:
  Splits  : NASDAQ splits calendar, TipRanks splits, NASDAQTrader
  Dividends: NASDAQ dividend calendar, StockAnalysis, MarketBeat, Finviz
"""

import datetime
import re
import time
import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def _get(url: str, timeout: int = 15) -> requests.Response | None:
    try:
        resp = SESSION.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp
    except Exception as e:
        logger.warning(f"GET {url} failed: {e}")
        return None


# ---------------------------------------------------------------------------
# SPLITS
# ---------------------------------------------------------------------------

def scrape_nasdaq_splits(target_date: datetime.date) -> dict[str, str]:
    """NASDAQ market-activity splits calendar. Returns {ticker: ratio_str}."""
    found: dict[str, str] = {}
    date_str = target_date.strftime('%Y-%m-%d')
    url = 'https://api.nasdaq.com/api/calendar/splits'
    params = {'date': date_str}
    try:
        resp = SESSION.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        rows = (
            data.get('data', {}).get('rows') or
            data.get('data', {}).get('upcomingSplits', {}).get('rows') or
            []
        )
        date_fmt_slash = target_date.strftime('%m/%d/%Y')
        for row in rows:
            sym    = (row.get('symbol') or row.get('ticker') or '').strip().upper()
            exdate = row.get('executionDate') or row.get('exDate') or ''
            ratio  = str(row.get('ratio') or '').strip()
            # Only add if executionDate matches target — API returns upcoming splits, not just target date
            if sym and (date_fmt_slash in exdate or date_str in exdate):
                found[sym] = ratio
    except Exception as e:
        logger.warning(f"NASDAQ splits API error: {e}")

    # Fallback: scrape the HTML page
    if not found:
        resp = _get('https://www.nasdaq.com/market-activity/stock-splits')
        if resp:
            soup = BeautifulSoup(resp.text, 'html.parser')
            for row in soup.select('table tbody tr'):
                cells = row.find_all('td')
                if len(cells) >= 3:
                    date_cell  = cells[2].get_text(strip=True)
                    sym_cell   = cells[0].get_text(strip=True).upper()
                    ratio_cell = cells[1].get_text(strip=True) if len(cells) > 1 else ''
                    if target_date.strftime('%m/%d/%Y') in date_cell or date_str in date_cell:
                        found[sym_cell] = ratio_cell

    return found


def scrape_tipranks_splits(target_date: datetime.date) -> dict[str, str]:
    """TipRanks upcoming splits calendar. Returns {ticker: ratio_str}."""
    found: dict[str, str] = {}
    url = 'https://www.tipranks.com/api/calendar/stock-splits/'
    resp = _get(url)
    if not resp:
        return found
    try:
        data = resp.json()
        events = data if isinstance(data, list) else data.get('data', [])
        for ev in events:
            ex = ev.get('exDate') or ev.get('date') or ''
            sym = (ev.get('ticker') or ev.get('symbol') or '').strip().upper()
            ratio = str(ev.get('ratio') or ev.get('splitRatio') or '').strip()
            try:
                ev_date = datetime.date.fromisoformat(ex[:10])
            except Exception:
                continue
            if ev_date == target_date and sym:
                found[sym] = ratio
    except Exception as e:
        logger.warning(f"TipRanks splits parse error: {e}")
    return found


def scrape_nasdaqtrader_splits(target_date: datetime.date) -> dict[str, str]:
    """NASDAQTrader splits file (updated daily). Returns {ticker: ratio_str}."""
    found: dict[str, str] = {}
    url = 'https://www.nasdaqtrader.com/dynamic/splits/splits.txt'
    resp = _get(url)
    if not resp:
        return found
    date_fmt_slash = target_date.strftime('%m/%d/%Y')
    date_fmt_dash  = target_date.strftime('%Y-%m-%d')
    for line in resp.text.splitlines():
        parts = [p.strip() for p in line.split('|')]
        if len(parts) < 3:
            continue
        sym     = parts[0].upper()
        ratio   = parts[1] if len(parts) > 1 else ''
        ex_date = parts[2] if len(parts) > 2 else ''
        if date_fmt_slash in ex_date or date_fmt_dash in ex_date:
            found[sym] = ratio
    return found


def get_all_splits(target_date: datetime.date) -> dict[str, dict]:
    """
    Aggregate splits from all sources.
    Returns {ticker: {'ratio': str, 'sources': [str, ...]}}.
    """
    results: dict[str, dict] = {}

    sources = [
        ('NASDAQ',       scrape_nasdaq_splits),
        ('TipRanks',     scrape_tipranks_splits),
        ('NASDAQTrader', scrape_nasdaqtrader_splits),
    ]

    for name, fn in sources:
        try:
            found = fn(target_date)
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
# DIVIDENDS
# ---------------------------------------------------------------------------

def scrape_nasdaq_dividends(target_date: datetime.date) -> dict[str, dict]:
    """NASDAQ dividend calendar API for ex-dividend dates."""
    results: dict[str, dict] = {}
    date_str = target_date.strftime('%Y-%m-%d')
    url = 'https://api.nasdaq.com/api/calendar/dividends'
    params = {'date': date_str}
    try:
        resp = SESSION.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get('data', {}).get('calendar', {}).get('rows') or []
        for row in rows:
            sym = (row.get('symbol') or '').strip().upper()
            # NASDAQ API uses 'dividend_Rate' (e.g. 0.22), not 'amount' or 'dividend'
            raw = row.get('dividend_Rate') or row.get('amount') or row.get('dividend') or ''
            amt = ('$' + f'{float(raw):.4f}'.rstrip('0').rstrip('.')) if raw != '' else ''
            # API is queried by date — all returned rows are for target_date, ex-field is often empty
            if sym:
                results[sym] = {'amount': amt, 'source': 'NASDAQ'}
    except Exception as e:
        logger.warning(f"NASDAQ dividends API error: {e}")
    return results


def scrape_stockanalysis_dividends(target_date: datetime.date) -> dict[str, dict]:
    """StockAnalysis dividend calendar."""
    results: dict[str, dict] = {}
    date_str = target_date.strftime('%Y-%m-%d')
    url = f'https://stockanalysis.com/dividends/?date={date_str}'
    resp = _get(url)
    if not resp:
        return results
    soup = BeautifulSoup(resp.text, 'html.parser')
    for row in soup.select('table tbody tr'):
        cells = row.find_all('td')
        if len(cells) < 3:
            continue
        sym = cells[0].get_text(strip=True).upper()
        ex  = cells[2].get_text(strip=True)   # ex-date column
        amt = cells[3].get_text(strip=True) if len(cells) > 3 else ''
        if date_str in ex or target_date.strftime('%m/%d/%Y') in ex:
            results[sym] = {'amount': amt, 'source': 'StockAnalysis'}
    return results


def scrape_marketbeat_dividends(target_date: datetime.date) -> dict[str, dict]:
    """MarketBeat dividend calendar."""
    results: dict[str, dict] = {}
    date_str = target_date.strftime('%Y-%m-%d')
    url = f'https://www.marketbeat.com/dividends/ex-dividend-date/{date_str}/'
    resp = _get(url)
    if not resp:
        return results
    soup = BeautifulSoup(resp.text, 'html.parser')
    for row in soup.select('table tbody tr, .dividend-table tr'):
        cells = row.find_all('td')
        if len(cells) < 2:
            continue
        sym = cells[0].get_text(strip=True).upper()
        amt = cells[2].get_text(strip=True) if len(cells) > 2 else ''
        if sym:
            results[sym] = {'amount': amt, 'source': 'MarketBeat'}
    return results


def scrape_earningswhispers_dividends(target_date: datetime.date) -> dict[str, dict]:
    """EarningsWhispers dividend calendar."""
    results: dict[str, dict] = {}
    date_str = target_date.strftime('%Y-%m-%d')
    url = f'https://www.earningswhispers.com/dividend/{date_str}'
    resp = _get(url)
    if not resp:
        return results
    soup = BeautifulSoup(resp.text, 'html.parser')
    for row in soup.select('table tbody tr'):
        cells = row.find_all('td')
        if not cells:
            continue
        sym = cells[0].get_text(strip=True).upper()
        amt = cells[1].get_text(strip=True) if len(cells) > 1 else ''
        if sym and re.match(r'^[A-Z]{1,6}$', sym):
            results[sym] = {'amount': amt, 'source': 'EarningsWhispers'}
    return results


def get_all_dividends(target_date: datetime.date) -> dict[str, dict]:
    """
    Aggregate dividends from all sources.
    Returns {ticker: {'amount': ..., 'sources': [...]}}.
    """
    merged: dict[str, dict] = {}

    sources = [
        ('NASDAQ',           scrape_nasdaq_dividends),
        ('StockAnalysis',    scrape_stockanalysis_dividends),
        ('MarketBeat',       scrape_marketbeat_dividends),
        ('EarningsWhispers', scrape_earningswhispers_dividends),
    ]

    for name, fn in sources:
        try:
            found = fn(target_date)
            logger.info(f"{name} dividends: {len(found)} tickers")
            for sym, info in found.items():
                if sym not in merged:
                    merged[sym] = {'amount': info.get('amount', ''), 'sources': []}
                merged[sym]['sources'].append(name)
                if not merged[sym]['amount'] and info.get('amount'):
                    merged[sym]['amount'] = info['amount']
        except Exception as e:
            logger.error(f"{name} dividends failed: {e}")
        time.sleep(0.5)

    return merged
