"""
Offline regression tests — no network required. Run after any change:

    python test_all.py

Covers ticker parsing (including ambiguous bare suffixes), every scraper's
HTML/JSON parsing against synthetic fixtures matching the real structures
observed 2026-06-10, the unchecked-ticker failure tracking, and comparison
logic. Exits 1 on any failure.
"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, '.')

import datetime

failures: list[str] = []

def check(label, actual, expected):
    ok = actual == expected
    if not ok:
        failures.append(label)
    print(f'  {"OK  " if ok else "FAIL"} {label}' + ('' if ok else f'\n        got:      {actual!r}\n        expected: {expected!r}'))


TARGET = datetime.date(2026, 6, 11)

class FakeResp:
    def __init__(self, text='', payload=None):
        self.text = text
        self._payload = payload
    def json(self):
        return self._payload
    def raise_for_status(self):
        pass


# ===========================================================================
print('=== ticker_utils ===')
from ticker_utils import get_underlying_candidates, get_underlying, build_ticker_map

check('GLW -> both interpretations (Corning, not just GL warrant)',
      get_underlying_candidates('GLW'), [('GLW', 'common'), ('GL', 'warrant')])
check('AMPG -> both (real ticker ending in PG)',
      get_underlying_candidates('AMPG'), [('AMPG', 'common'), ('AM', 'preferred')])
check('CTOR -> both (Citius Oncology vs CTO right)',
      get_underlying_candidates('CTOR'), [('CTOR', 'common'), ('CTO', 'right')])
check('ACMR.WS -> unambiguous warrant',
      get_underlying_candidates('ACMR.WS'), [('ACMR', 'warrant')])
check('BAC-PA -> unambiguous preferred',
      get_underlying_candidates('BAC-PA'), [('BAC', 'preferred')])
check('BACPRA -> both (bare preferred)',
      get_underlying_candidates('BACPRA'), [('BACPRA', 'common'), ('BAC', 'preferred')])
check('BRK.A kept as class share',
      get_underlying_candidates('BRK.A'), [('BRK.A', 'class_share')])
check('BRK-B normalized to dot',
      get_underlying_candidates('BRK-B'), [('BRK.B', 'class_share')])
check('WSO B space class -> dot form',
      get_underlying_candidates('WSO B'), [('WSO.B', 'class_share')])
check("XRX JAN 21 '28 7 CALL apostrophe-year option",
      get_underlying_candidates("XRX JAN 21 '28 7 CALL"), [('XRX', 'option')])
check("ASST2 JAN 15 '27 3 CALL digit underlying",
      get_underlying_candidates("ASST2 JAN 15 '27 3 CALL"), [('ASST2', 'option')])
check('AVGO JUN 05 2026 310.00 PUT 4-digit year',
      get_underlying_candidates('AVGO JUN 05 2026 310.00 PUT'), [('AVGO', 'option')])
check('AAPL240119C00150000 OCC option',
      get_underlying_candidates('AAPL240119C00150000'), [('AAPL', 'option')])
check('AU too short for bare strip',
      get_underlying_candidates('AU'), [('AU', 'common')])
check('BABA plain common', get_underlying_candidates('BABA'), [('BABA', 'common')])
check('RA plain common', get_underlying_candidates('RA'), [('RA', 'common')])
check('get_underlying returns primary', get_underlying('GLW'), ('GLW', 'common'))

m = build_ticker_map(['GLW', 'BABA'])
check('build_ticker_map dual keys', sorted(m.keys()), ['BABA', 'GL', 'GLW'])
check('GL traces to GLW original', m['GL']['originals'], ['GLW'])

# ===========================================================================
print('\n=== junk ticker filter ===')
import scrapers
from scrapers import _is_checkable_ticker

for junk in ['02616r102', "asst2 jan 15 '27 3 call", 'verylongticker123', '']:
    check(f'skip {junk!r}', _is_checkable_ticker(junk), False)
for good in ['BABA', 'RA', 'BRK.A', 'SPY']:
    check(f'check {good!r}', _is_checkable_ticker(good), True)

# ===========================================================================
print('\n=== StockAnalysis per-ticker dividend parsing ===')
SA_HIT = '<script>__sveltekit_x = {history:[{dt:"2026-06-11",amt:"$1.030",dec:"n/a",record:"2026-06-11",pay:"2026-07-13"}]}</script>'
SA_MISS = '<script>__sveltekit_x = {history:[{dt:"2025-06-12",amt:"$1.980",dec:"n/a",record:"2025-06-12",pay:"2025-07-10"}]}</script>'
# Pay date == target but ex-date is NOT — must not match
SA_PAYDATE_TRAP = '<table><tbody><tr><td>Jun 1, 2026</td><td>$0.50</td><td>Jun 2, 2026</td><td>Jun 11, 2026</td></tr></tbody></table>'

check('SvelteKit hit parsed',
      scrapers._parse_sa_dividend_page(SA_HIT, TARGET),
      {'amount': '$1.030', 'source': 'StockAnalysis'})
check('no entry for target -> None', scrapers._parse_sa_dividend_page(SA_MISS, TARGET), None)
check('pay-date trap -> None', scrapers._parse_sa_dividend_page(SA_PAYDATE_TRAP, TARGET), None)

# ===========================================================================
print('\n=== unchecked tracking ===')
_orig_get_perticker = scrapers._get_perticker
_orig_sleep = scrapers.time.sleep
scrapers.time.sleep = lambda s: None   # no pacing in tests

scrapers._get_perticker = lambda url, timeout=12: ('notfound', None)
t, r, checked = scrapers._fetch_sa_dividend('FAKE', TARGET)
check('404 both paths -> checked, no result', (r, checked), (None, True))

scrapers._get_perticker = lambda url, timeout=12: ('error', None)
t, r, checked = scrapers._fetch_sa_dividend('FAKE', TARGET)
check('error -> UNCHECKED', (r, checked), (None, False))

scrapers._get_perticker = lambda url, timeout=12: ('ok', FakeResp(SA_HIT))
t, r, checked = scrapers._fetch_sa_dividend('BABA', TARGET)
check('hit parsed via fetch', (r, checked), ({'amount': '$1.030', 'source': 'StockAnalysis'}, True))

def _route(url, timeout=12):
    if 'baba' in url: return ('ok', FakeResp(SA_HIT))
    if 'aapl' in url: return ('ok', FakeResp(SA_MISS))
    return ('error', None)
scrapers._get_perticker = _route
results, unchecked = scrapers.scrape_stockanalysis_dividends_perticker(TARGET, ['BABA', 'AAPL', 'BLOCKED'])
check('sweep results', results, {'BABA': {'amount': '$1.030', 'source': 'StockAnalysis'}})
check('sweep unchecked', unchecked, ['BLOCKED'])

# OTC preferreds (PSBYP/PSBZP, missed 2026-06-12): /stocks/ and /etf/ 404 but
# /quote/otc/ has the page — the fetcher must fall through to it
SA_OTC_HIT = '<script>__sveltekit_x = {history:[{dt:"2026-06-11",amt:"$0.325",dec:"n/a",record:"2026-06-11",pay:"2026-06-30"}]}</script>'
def _route_otc(url, timeout=12):
    if 'quote/otc' in url: return ('ok', FakeResp(SA_OTC_HIT))
    return ('notfound', None)
scrapers._get_perticker = _route_otc
t, r, checked = scrapers._fetch_sa_dividend('PSBYP', TARGET)
check('OTC preferred found via quote/otc fallback path',
      (r, checked), ({'amount': '$0.325', 'source': 'StockAnalysis'}, True))
scrapers._get_perticker = _orig_get_perticker

# ===========================================================================
print('\n=== fast-mode OTC-preferred sweep (bulk-invisible tickers) ===')
_orig_bz  = scrapers.scrape_benzinga_dividends
_orig_inv = scrapers.scrape_investing_dividends
_orig_mb  = scrapers.scrape_marketbeat_dividends
_orig_fetch = scrapers._fetch_sa_dividend

scrapers.scrape_benzinga_dividends   = lambda d: {'UNH': {'amount': '$2.32', 'source': 'Benzinga'}}
scrapers.scrape_investing_dividends  = lambda d: {}
scrapers.scrape_marketbeat_dividends = lambda d: {}
def _fake_fetch(sym, d):
    if sym == 'PSBYP':
        return sym, {'amount': '$0.325', 'source': 'StockAnalysis'}, True
    if sym == 'NOCOVP':
        return sym, None, False     # no page anywhere -> must surface as unchecked
    return sym, None, True
scrapers._fetch_sa_dividend = _fake_fetch

merged, unchecked = scrapers.get_all_dividends(TARGET, tickers=['UNH', 'PSBYP', 'NOCOVP', 'CLEANP', 'BABA'])
check('bulk hit kept', 'UNH' in merged, True)
check('OTC-pref sweep catches PSBYP despite bulk blindness',
      merged.get('PSBYP'), {'amount': '$0.325', 'sources': ['StockAnalysis']})
check('non-pattern ticker BABA not swept', 'BABA' in merged, False)
check('no-coverage preferred surfaces as UNCHECKED', unchecked, ['NOCOVP'])
check('clean preferred not flagged', 'CLEANP' in merged, False)

scrapers.scrape_benzinga_dividends   = _orig_bz
scrapers.scrape_investing_dividends  = _orig_inv
scrapers.scrape_marketbeat_dividends = _orig_mb
scrapers._fetch_sa_dividend          = _orig_fetch

# ===========================================================================
print('\n=== Benzinga dividends parsing ===')
BZ_DIV = '''<table><thead><tr>
<th>Ex-Date▲▼</th><th>ticker▲▼</th><th>Company▲▼</th><th>Payments per year▲▼</th>
<th>Dividend▲▼</th><th>Yield▲▼</th><th>Announced▲▼</th><th>Record▲▼</th><th>Payable▲▼</th><th>Get Alert</th>
</tr></thead><tbody>
<tr><td>06/11/2026</td><td>BABA</td><td>Alibaba Gr Hldgs</td><td>1</td><td>$1.05</td><td>0.78%</td><td>05/13/2026</td><td>06/11/2026</td><td>07/13/2026</td><td>Get Alert</td></tr>
<tr><td>06/12/2026</td><td>NDAQ</td><td>Nasdaq</td><td>4</td><td>$0.31</td><td>1.43%</td><td>04/01/2026</td><td>06/12/2026</td><td>06/26/2026</td><td>Get Alert</td></tr>
</tbody></table>'''
_orig_get = scrapers._get
scrapers._get = lambda url, timeout=15: FakeResp(BZ_DIV)
check('Benzinga: only target-date rows',
      scrapers.scrape_benzinga_dividends(TARGET),
      {'BABA': {'amount': '$1.05', 'source': 'Benzinga'}})
scrapers._get = _orig_get

# ===========================================================================
print('\n=== MarketBeat dividends parsing (page ignores URL date) ===')
MB_DIV = '''<table><thead><tr>
<th>Company</th><th>Period</th><th>Amount</th><th>Yield</th><th>Ex-Dividend Date</th><th>Record Date</th><th>Payable Date</th><th>Indicator(s)</th>
</tr></thead><tbody>
<tr><td>CASY Caseys General Stores</td><td>quarterly</td><td>$0.65</td><td>1.2%</td><td>7/31/2026</td><td>7/31/2026</td><td>8/15/2026</td><td>News</td></tr>
<tr><td>NDAQ Nasdaq</td><td>quarterly</td><td>$0.31</td><td>1.43%</td><td>6/11/2026</td><td>6/11/2026</td><td>6/26/2026</td><td>News</td></tr>
</tbody></table>'''
scrapers._get = lambda url, timeout=15: FakeResp(MB_DIV)
check('MarketBeat: filters by Ex-Dividend Date column, extracts ticker token',
      scrapers.scrape_marketbeat_dividends(TARGET),
      {'NDAQ': {'amount': '$0.31', 'source': 'MarketBeat'}})
scrapers._get = _orig_get

# ===========================================================================
print('\n=== Investing.com dividends parsing (AJAX payload) ===')
INV_HTML = '''<tr><td class="flag"></td>
<td class="left noWrap" title="Alibaba Group Holdings Ltd ADR"><span>Alibaba ADR</span>&nbsp;(<a class="bold" href="/equities/alibaba-dividends">BABA</a>)</td>
<td>Jun 11, 2026</td><td>1.03</td><td data-value="4"></td></tr>
<tr><td class="flag"></td>
<td class="left noWrap" title="Other Co"><span>Other Co</span>&nbsp;(<a class="bold" href="/equities/other">OTHR</a>)</td>
<td>Jun 12, 2026</td><td>0.50</td><td data-value="4"></td></tr>'''

class FakeSession:
    def post(self, url, data=None, headers=None, timeout=15):
        return FakeResp(payload={'data': INV_HTML})

_orig_session = scrapers.SESSION
scrapers.SESSION = FakeSession()
check('Investing.com: target-date rows only, ticker from <a>',
      scrapers.scrape_investing_dividends(TARGET),
      {'BABA': {'amount': '$1.03', 'source': 'Investing.com'}})
scrapers.SESSION = _orig_session

# ===========================================================================
print('\n=== Splits parsing ===')
SA_SPLITS = '''<script>__sveltekit_x = {data:[
{date:"Jun 11, 2026",symbol:"$SHPH",name:"Shuttle Pharmaceuticals Holdings Inc",splitType:"Reverse",splitRatio:"1 for 10"},
{date:"Jun 9, 2026",symbol:"$ZCMD",name:"Zhongchao Inc",splitType:"Reverse",splitRatio:"1 for 31"}]}</script>'''
scrapers._get = lambda url, timeout=15: FakeResp(SA_SPLITS)
check('StockAnalysis splits: SvelteKit rows filtered by date',
      scrapers.scrape_stockanalysis_splits(TARGET), {'SHPH': '1 for 10'})

BZ_SPLITS = '''<table><thead><tr>
<th>Ex-Date▲▼</th><th>Company▲▼</th><th>ticker▲▼</th><th>exchange▲▼</th><th>Split Ratio▲▼</th><th>Date Announced▲▼</th><th>Date Recorded▲▼</th><th>Distribution Date▲▼</th><th>Get Alert</th>
</tr></thead><tbody>
<tr><td>06/11/2026</td><td>Verano Holdings</td><td>VRNO</td><td>OTC</td><td>1:5</td><td>06/01/2026</td><td>06/11/2026</td><td>06/11/2026</td><td>Get Alert</td></tr>
<tr><td>06/03/2026</td><td>Power REIT</td><td>PW</td><td>AMEX</td><td>1:10</td><td>05/19/2026</td><td>06/03/2026</td><td>06/03/2026</td><td>Get Alert</td></tr>
</tbody></table>'''
scrapers._get = lambda url, timeout=15: FakeResp(BZ_SPLITS)
check('Benzinga splits: header-mapped, date-filtered',
      scrapers.scrape_benzinga_splits(TARGET), {'VRNO': '1:5'})

INV_SPLITS = '''<table><thead><tr><th>Split date</th><th>Company</th><th>Split ratio</th></tr></thead><tbody>
<tr><td>Jun 12, 2026</td><td>KLA Corp ( KLAC )</td><td>10:1</td></tr>
<tr><td>Jun 11, 2026</td><td>Global Mofy Metaverse ( GMM )</td><td>1:50</td></tr>
<tr><td></td><td>Shuttle Pharmaceuticals ( SHPH )</td><td>1:10</td></tr>
<tr><td>Jun 10, 2026</td><td>Abound Energy ( ZAIRD )</td><td>1:3</td></tr>
</tbody></table>'''
scrapers._get = lambda url, timeout=15: FakeResp(INV_SPLITS)
check('Investing splits: date-group carry-forward',
      scrapers.scrape_investing_splits(TARGET), {'GMM': '1:50', 'SHPH': '1:10'})
scrapers._get = _orig_get
scrapers.time.sleep = _orig_sleep

# ===========================================================================
print('\n=== compare.py ===')
from compare import compare

py  = {'BABA|dividend': {'underlying': 'BABA', 'event_type': 'dividend', 'amount_or_ratio': '$1.05'},
       'CTO|dividend':  {'underlying': 'CTO',  'event_type': 'dividend', 'amount_or_ratio': '$0.38'}}
cl  = {'BABA|dividend': {'underlying': 'BABA', 'event_type': 'dividend', 'amount_or_ratio': '$1.05'},
       'RA|dividend':   {'underlying': 'RA',   'event_type': 'dividend', 'amount_or_ratio': '$0.118'}}
disc, agree = compare(py, cl)
check('one agreement', len(agree), 1)
check('two discrepancies (CTO python-only, RA claude-only)', len(disc), 2)
check('python-only flagged', any('PYTHON ONLY: CTO' in d for d in disc), True)
check('claude-only flagged', any('CLAUDE ONLY: RA' in d for d in disc), True)

# ===========================================================================
print('\n=== next_trading_day ===')
from check_events import next_trading_day
check('Wed -> Thu', next_trading_day(datetime.date(2026, 6, 10)), datetime.date(2026, 6, 11))
check('Fri -> Mon', next_trading_day(datetime.date(2026, 6, 12)), datetime.date(2026, 6, 15))
check('Sat -> Mon', next_trading_day(datetime.date(2026, 6, 13)), datetime.date(2026, 6, 15))

# ===========================================================================
print()
if failures:
    print(f'{len(failures)} FAILURE(S):')
    for f in failures:
        print(f'  - {f}')
    sys.exit(1)
print('ALL TESTS PASSED')
