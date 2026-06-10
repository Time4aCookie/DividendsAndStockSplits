"""
Utilities for parsing equity tickers and extracting underlying symbols.

Handles: warrants, rights, preferred stock, options (OCC format and
human-readable format), multiple share classes, units, and other
derivative instruments.

Ambiguity policy: a bare suffix with no separator is ambiguous — ACMRW could
be an ACMR warrant or a ticker that simply ends in W (GLW is Corning, not a
GL warrant). Per project policy ("a false positive is cheaper than a miss"),
ambiguous tickers produce BOTH candidates: the full ticker as a common stock
AND the stripped underlying. Dot/dash separators (ACMR.WS, BAC-PA) are
unambiguous and produce only the stripped form.
"""

import re

# OCC option format: TICKER + YYMMDD + C/P + STRIKE (e.g. AAPL240119C00150000)
# Underlying may include a trailing digit (e.g. ASST2)
OCC_OPTION_RE = re.compile(r'^([A-Z][A-Z0-9]{0,5})\d{6}[CP]\d+$')

# Human-readable option format: "AVGO JUN 05 2026 310.00 PUT" or "XRX JAN 21 '28 7 CALL"
# Supports 4-digit year (2026) and 2-digit year with apostrophe ('26 / '28).
# Underlying may include a trailing digit (e.g. ASST2).
MONTH_ABBREVS = {'JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'}
HUMAN_OPTION_RE = re.compile(
    r"^([A-Z][A-Z0-9]{0,5})"           # underlying ticker (first letter A-Z, rest A-Z or digit)
    r"\s+(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"  # month abbreviation
    r"\s+\d{1,2}"                       # day (1 or 2 digits)
    r"\s+'?\d{2,4}"                     # year: optional apostrophe + 2-4 digits ('26, '28, 2026)
    r"\s+[\d.]+"                        # strike price
    r"\s+(CALL|PUT)$"                   # option type
)

# Space-separated share class: "WSO B" -> WSO.B (StockAnalysis/MarketBeat use dot form)
SPACE_CLASS_RE = re.compile(r'^([A-Z][A-Z0-9]{0,5})\s+([A-C])$')

# Unambiguous suffixes (dot/dash separator present) — strip with confidence
DOTDASH_PREFERRED = [
    '.PA', '.PB', '.PC', '.PD', '.PE', '.PF', '.PG', '.PH',
    '-PA', '-PB', '-PC', '-PD', '-PE', '-PF', '-PG', '-PH',
]
DOTDASH_WARRANT = ['.WS', '.WT', '.W']
DOTDASH_RIGHT   = ['.R']
DOTDASH_UNIT    = ['.U']
# Share class suffixes — the full ticker IS the security; normalize dash to dot
DOTDASH_CLASS   = ['.A', '.B', '.C', '-A', '-B', '-C']

# Ambiguous bare suffixes (no separator) — produce BOTH candidates
BARE_PREFERRED = ['PRA', 'PRB', 'PRC', 'PRD', 'PRE', 'PRF', 'PRG', 'PRH',
                  'PA', 'PB', 'PC', 'PD', 'PE', 'PF', 'PG', 'PH']
BARE_WARRANT   = ['W']
BARE_RIGHT     = ['R']
BARE_UNIT      = ['U']

# Minimum length of a stripped bare-suffix underlying for the candidate to be
# plausible (prevents AU -> A, etc.)
MIN_BARE_UNDERLYING_LEN = 2


def get_underlying_candidates(ticker: str) -> list[tuple[str, str]]:
    """
    Return a list of (underlying_ticker, instrument_type) candidates.

    Unambiguous instruments (options, dot/dash suffixes) return one candidate.
    Ambiguous bare-suffix tickers return two: the full ticker as 'common'
    plus the stripped underlying — so neither interpretation is ever missed.

    instrument_type is one of:
        'common', 'warrant', 'right', 'unit', 'preferred',
        'option', 'class_share', 'unknown'
    """
    if not ticker or not isinstance(ticker, str):
        return [(ticker, 'unknown')]

    t = ticker.strip().upper()

    # Options — unambiguous, single candidate
    m = HUMAN_OPTION_RE.match(t)
    if m:
        return [(m.group(1), 'option')]
    m = OCC_OPTION_RE.match(t)
    if m:
        return [(m.group(1), 'option')]

    # Space-separated share class: "WSO B" -> "WSO.B"
    m = SPACE_CLASS_RE.match(t)
    if m:
        return [(f'{m.group(1)}.{m.group(2)}', 'class_share')]

    # Unambiguous dot/dash suffixes — strip and return single candidate
    for suf in DOTDASH_PREFERRED:
        if t.endswith(suf) and len(t) > len(suf):
            return [(t[: -len(suf)], 'preferred')]
    for suf in DOTDASH_WARRANT:
        if t.endswith(suf) and len(t) > len(suf):
            return [(t[: -len(suf)], 'warrant')]
    for suf in DOTDASH_RIGHT:
        if t.endswith(suf) and len(t) > len(suf):
            return [(t[: -len(suf)], 'right')]
    for suf in DOTDASH_UNIT:
        if t.endswith(suf) and len(t) > len(suf):
            return [(t[: -len(suf)], 'unit')]
    for suf in DOTDASH_CLASS:
        if t.endswith(suf) and len(t) > len(suf):
            base = t[: -len(suf)]
            cls  = suf[-1]
            return [(f'{base}.{cls}', 'class_share')]   # normalize dash to dot

    # Ambiguous bare suffixes — full ticker first, then stripped candidate
    candidates: list[tuple[str, str]] = [(t, 'common')]
    bare_groups = [
        (BARE_PREFERRED, 'preferred'),
        (BARE_WARRANT,   'warrant'),
        (BARE_RIGHT,     'right'),
        (BARE_UNIT,      'unit'),
    ]
    for suffixes, itype in bare_groups:
        for suf in suffixes:
            if t.endswith(suf) and len(t) - len(suf) >= MIN_BARE_UNDERLYING_LEN:
                stripped = t[: -len(suf)]
                if (stripped, itype) not in candidates:
                    candidates.append((stripped, itype))
                break   # only the first matching suffix per group
        else:
            continue
        # a match in this group is enough — don't also strip R after stripping W
        if len(candidates) > 1:
            break

    return candidates


def get_underlying(ticker: str) -> tuple[str, str]:
    """
    Return the primary (underlying_ticker, instrument_type) for an instrument.
    For ambiguous bare-suffix tickers this is the full ticker as 'common';
    use get_underlying_candidates() to get all interpretations.
    """
    return get_underlying_candidates(ticker)[0]


def build_ticker_map(raw_tickers: list[str]) -> dict[str, dict]:
    """
    Given a list of raw tickers from the Excel, return a dict keyed by
    underlying ticker with metadata about the original position(s).
    Ambiguous tickers appear under ALL candidate underlyings.

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
        for underlying, itype in get_underlying_candidates(raw):
            if underlying not in result:
                result[underlying] = {'originals': [], 'types': []}
            if raw not in result[underlying]['originals']:
                result[underlying]['originals'].append(raw)
                result[underlying]['types'].append(itype)
    return result
