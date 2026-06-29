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
check('BAC-PA -> preferred, keeps own identity (dash->dot), NOT collapsed to BAC',
      get_underlying_candidates('BAC-PA'), [('BAC.PA', 'preferred')])
check('SPG.PRJ -> preferred Series J (the 2026-06-16 miss; .PR + beyond-H series)',
      get_underlying_candidates('SPG.PRJ'), [('SPG.PRJ', 'preferred')])
check('DLR-PRJ -> dash normalized to dot, kept as preferred',
      get_underlying_candidates('DLR-PRJ'), [('DLR.PRJ', 'preferred')])
check('BAC.PRO -> preferred (series O, well beyond old A-H cap)',
      get_underlying_candidates('BAC.PRO'), [('BAC.PRO', 'preferred')])
check('BACPRA -> both (bare preferred, still ambiguous)',
      get_underlying_candidates('BACPRA'), [('BACPRA', 'common'), ('BAC', 'preferred')])
check('BRK.A still a class share, not mistaken for preferred',
      get_underlying_candidates('BRK.A'), [('BRK.A', 'class_share')])
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

# Silent-miss guard (NMPWP 2026-06-16): a 200 page with NO history rows must
# NOT be read as "no event" for a known payer — it's UNCHECKED.
SA_EMPTY_200 = '<html><body><div>Temporarily unavailable</div></body></html>'
def _route_empty(url, timeout=12):
    return ('ok', FakeResp(SA_EMPTY_200))
scrapers._get_perticker = _route_empty
# Default (hit-verification) trusts the 200 as a definitive no-event
t, r, checked = scrapers._fetch_sa_dividend('NMPWP', TARGET)
check('empty 200, require_history=False -> checked no-event', (r, checked), (None, True))
# Known-payer sweep must treat empty 200 as UNCHECKED, not silent clean
t, r, checked = scrapers._fetch_sa_dividend('NMPWP', TARGET, require_history=True)
check('empty 200, require_history=True -> UNCHECKED', (r, checked), (None, False))
# A real page with history but no target-date match is still a confident no-event
def _route_realmiss(url, timeout=12):
    return ('ok', FakeResp(SA_MISS))
scrapers._get_perticker = _route_realmiss
t, r, checked = scrapers._fetch_sa_dividend('NMPWP', TARGET, require_history=True)
check('real page, no target match, require_history=True -> confident no-event',
      (r, checked), (None, True))
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
def _fake_fetch(sym, d, require_history=False):
    if sym == 'PSBYP':
        return sym, {'amount': '$0.325', 'source': 'StockAnalysis'}, True
    if sym == 'SPG.PRJ':
        return sym, {'amount': '$1.04688', 'source': 'StockAnalysis'}, True
    if sym == 'NOCOVP':
        return sym, None, False     # no page anywhere -> must surface as unchecked
    return sym, None, True
scrapers._fetch_sa_dividend = _fake_fetch

merged, unchecked = scrapers.get_all_dividends(
    TARGET, tickers=['UNH', 'PSBYP', 'SPG.PRJ', 'NOCOVP', 'CLEANP', 'BABA'])
check('bulk hit kept', 'UNH' in merged, True)
check('OTC-pref sweep catches PSBYP despite bulk blindness',
      merged.get('PSBYP'), {'amount': '$0.325', 'sources': ['StockAnalysis']})
check('dotted preferred SPG.PRJ swept (the 2026-06-16 miss)',
      merged.get('SPG.PRJ'), {'amount': '$1.04688', 'sources': ['StockAnalysis']})
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

# Weekend-effective splits: target Monday 6/15, split dated Saturday 6/13 must
# be caught when the skipped weekend dates are passed as extra_dates
MONDAY = datetime.date(2026, 6, 15)
WEEKEND = [datetime.date(2026, 6, 14), datetime.date(2026, 6, 13)]
BZ_WKND = '''<table><thead><tr>
<th>Ex-Date</th><th>Company</th><th>ticker</th><th>exchange</th><th>Split Ratio</th>
</tr></thead><tbody>
<tr><td>06/13/2026</td><td>Weekend Corp</td><td>WKND</td><td>NASDAQ</td><td>1:10</td></tr>
<tr><td>06/15/2026</td><td>Monday Corp</td><td>MOND</td><td>NYSE</td><td>1:5</td></tr>
<tr><td>06/12/2026</td><td>Friday Corp</td><td>FRDY</td><td>NYSE</td><td>1:2</td></tr>
</tbody></table>'''
scrapers._get = lambda url, timeout=15: FakeResp(BZ_WKND)
check('Saturday-dated split caught for Monday target (Benzinga)',
      scrapers.scrape_benzinga_splits(MONDAY, WEEKEND), {'WKND': '1:10', 'MOND': '1:5'})
check('without extra_dates only Monday matches',
      scrapers.scrape_benzinga_splits(MONDAY), {'MOND': '1:5'})
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
print('\n=== GTC matching & adjustment (offline, synthetic orders) ===')
from check_events import match_gtc_to_events, _parse_cash_amount, _split_factor

check('parse $1.05', _parse_cash_amount('$1.05'), 1.05)
check('parse net-noted amount', _parse_cash_amount('$0.0537 (Clough)'), 0.0537)
check('reverse split factor 1 for 10', _split_factor('1 for 10'), 10.0)
check('forward split factor 4:1', _split_factor('4:1'), 0.25)
check('unparseable ratio -> None', _split_factor('see filing'), None)

GTC = {
    'INPAP': [{'trader': 'ALEX', 'ticker': 'INPAP', 'shares': 50.0, 'side': 'Buy', 'price': 64.01},
              {'trader': 'ALEX', 'ticker': 'INPAP', 'shares': 0.0,  'side': 'Buy', 'price': 0.01}],
    'PUCKU': [{'trader': 'CRAIG', 'ticker': 'PUCKU', 'shares': 100.0, 'side': 'Sell Auto', 'price': 12.99}],
}
recs = match_gtc_to_events({'PUCKU': {'ratio': '1 for 10'}}, {'INPAP': {'amount': '$0.50'}}, GTC)
by = {r['underlying']: r for r in recs}
check('dividend match present', 'INPAP' in by, True)
check('dividend limit dropped by amount', by['INPAP']['orders'][0]['suggested_price'], 63.51)
check('0-share order carried, no suggestion', by['INPAP']['orders'][1]['suggested_price'], None)
check('split price x10', by['PUCKU']['orders'][0]['suggested_price'], 129.9)
check('split shares /10', by['PUCKU']['orders'][0]['suggested_shares'], 10.0)
check('event with no GTC order omitted',
      [r for r in match_gtc_to_events({}, {'ZZZZ': {'amount': '$1'}}, GTC)], [])

print('\n=== GTC blotter CSV parser (thousands-comma corruption) ===')
import tempfile, os as _os
import gtc_reader

_HDR = ",Cancel,Time,Symbol,Side,Qty,-,Price,+,Status,Time In Force,Traded,Avg Px,Qty Left,Change,Route,Portfolio,Order Id"
_CSV = "\n".join([
    _HDR,
    # clean GTC+Live, no commas
    "1,,9:00:00,ACHR,BUY,1, -  0.01,3.50, +  0.01,Live,GTC,0,0.000000,1,,NASDAQ,91JBJG09-STK,AAA-1",
    # Qty thousands (1,000) + Avg Px decimal followed by Qty Left (must NOT merge)
    "2,,9:01:00,AACBR,BUY,1,000, -  0.01,0.15, +  0.01,Live,GTC,204,0.000000,796,,NASDAQ,91JBJG09-STK,BBB-2",
    # multiple thousands groups in Qty/Traded/Qty Left
    "938,,9:02:00,PGACU,BUY,5,000, -  0.01,10.21, +  0.01,Live,GTC,1,255,0.000000,3,745,,NASDAQ,91JBJG09-STK,CCC-3",
    # thousands inside a DECIMAL price (1,100.00) AND row-number with comma (1,118)
    "1,118,,9:03:00,SNDK,BUY,1, -  0.01,1,100.00, +  0.01,Live,GTC,0,0.000000,1,,NASDAQ,91JBJG09-STK,DDD-4",
    # excluded: DAY order
    "3,,9:04:00,XOM,BUY,100, -  0.01,50.00, +  0.01,Live,DAY,0,0.000000,100,,NASDAQ,91JBJG09-STK,EEE-5",
    # excluded: GTC but Filled (not Live)
    "4,,9:05:00,IBM,BUY,100, -  0.01,200.00, +  0.01,Filled,GTC,100,200.00,0,,NASDAQ,91JBJG09-STK,FFF-6",
])
_tmp = _os.path.join(tempfile.gettempdir(), "GTC's_2099-01-01")
open(_tmp, 'w', encoding='utf-8').write(_CSV)
co = gtc_reader.read_gtc_csv_orders(_tmp)
_os.remove(_tmp)
by = {o['ticker']: o for o in co}
check('only GTC+Live kept (4 of 6)', sorted(by), ['AACBR', 'ACHR', 'PGACU', 'SNDK'])
check('DAY excluded', 'XOM' not in by, True)
check('GTC-but-Filled excluded', 'IBM' not in by, True)
check('Qty thousands joined (1,000->1000)', by['AACBR']['shares'], 1000.0)
check('AACBR price intact (Avg Px not merged into price)', by['AACBR']['price'], 0.15)
check('multi-group qty (5,000)', by['PGACU']['shares'], 5000.0)
check('decimal-thousands price (1,100.00->1100.0)', by['SNDK']['price'], 1100.0)
check('source labeled GTC', by['ACHR']['trader'], 'GTC')

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
