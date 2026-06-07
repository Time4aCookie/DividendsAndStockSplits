"""
Utilities for parsing equity tickers and extracting underlying symbols.

Handles: warrants, rights, preferred stock, options (OCC format and
human-readable format), multiple share classes, units, and other
derivative instruments.
"""

import re

# OCC option format: TICKER + YYMMDD + C/P + STRIKE (e.g. AAPL240119C00150000)
OCC_OPTION_RE = re.compile(r'^([A-Z]{1,6})\d{6}[CP]\d+$')

# Human-readable option format: "AVGO JUN 05 2026 310.00 PUT"
# Detection: contains a space, a month abbreviation, and ends with CALL or PUT
MONTH_ABBREVS = {'JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'}
HUMAN_OPTION_RE = re.compile(r'^([A-Z]{1,6})\s+\w+\s+\d{2}\s+\d{4}\s+[\d.]+\s+(CALL|PUT)$')

# Suffixes that indicate a non-common-stock instrument and what to strip
WARRANT_SUFFIXES = ['.WS', '.WT', '.W', 'W']          # e.g. ACMRW, ACMR.WS
RIGHT_SUFFIXES   = ['.R', 'R']                          # e.g. ACMRR
UNIT_SUFFIXES    = ['.U', 'U']                          # e.g. ACMRU
PREFERRED_SUFFIXES = [
    '.PA', '.PB', '.PC', '.PD', '.PE', '.PF', '.PG', '.PH',
    '-PA', '-PB', '-PC', '-PD', '-PE', '-PF', '-PG', '-PH',
    'PA', 'PB', 'PC', 'PD', 'PE', 'PF', 'PG', 'PH',
    'PRB', 'PRC', 'PRD', 'PRE', 'PRF',
]
# Share class suffixes — don't strip, just note it's still a common stock
SHARE_CLASS_SUFFIXES = ['.A', '.B', '.C', '-A', '-B', '-C']


def get_underlying(ticker: str) -> tuple[str, str]:
    """
    Return (underlying_ticker, instrument_type) for any equity instrument.

    instrument_type is one of:
        'common', 'warrant', 'right', 'unit', 'preferred',
        'option', 'class_share', 'unknown'
    """
    if not ticker or not isinstance(ticker, str):
        return ticker, 'unknown'

    t = ticker.strip().upper()

    # Human-readable option format: "AVGO JUN 05 2026 310.00 PUT"
    m = HUMAN_OPTION_RE.match(t)
    if m:
        return m.group(1), 'option'

    # OCC option format: AAPL240119C00150000
    m = OCC_OPTION_RE.match(t)
    if m:
        return m.group(1), 'option'

    # Check explicit dot/dash suffixes first (more specific)
    for suf in PREFERRED_SUFFIXES:
        if t.endswith(suf) and len(t) > len(suf):
            return t[: -len(suf)], 'preferred'

    for suf in WARRANT_SUFFIXES:
        if t.endswith(suf) and len(t) > len(suf):
            underlying = t[: -len(suf)]
            if len(underlying) >= 1:
                return underlying, 'warrant'

    for suf in RIGHT_SUFFIXES:
        if t.endswith(suf) and len(t) > len(suf):
            underlying = t[: -len(suf)]
            if len(underlying) >= 1:
                return underlying, 'right'

    for suf in UNIT_SUFFIXES:
        if t.endswith(suf) and len(t) > len(suf):
            underlying = t[: -len(suf)]
            if len(underlying) >= 1:
                return underlying, 'unit'

    for suf in SHARE_CLASS_SUFFIXES:
        if t.endswith(suf) and len(t) > len(suf):
            return t, 'class_share'   # keep full ticker, still a common stock

    return t, 'common'


def build_ticker_map(raw_tickers: list[str]) -> dict[str, dict]:
    """
    Given a list of raw tickers from the Excel, return a dict keyed by
    underlying ticker with metadata about the original position(s).

    Example output:
        {
            'ACMR': {
                'originals': ['ACMR', 'ACMR.WS'],
                'types': ['common', 'warrant'],
            },
            ...
        }
    """
    result: dict[str, dict] = {}
    for raw in raw_tickers:
        if not raw:
            continue
        underlying, itype = get_underlying(raw)
        if underlying not in result:
            result[underlying] = {'originals': [], 'types': []}
        result[underlying]['originals'].append(raw)
        result[underlying]['types'].append(itype)
    return result
