from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import requests
import os
import time
import re
from datetime import datetime, timedelta

app = Flask(__name__, static_folder='.')
CORS(app)

ISA_AUTH    = os.environ.get('ISA_AUTH', '')
INVEST_AUTH = os.environ.get('INVEST_AUTH', '')
FINNHUB_KEY = os.environ.get('FINNHUB_KEY', '')

T212_BASE    = 'https://live.trading212.com/api/v0'
FINNHUB_BASE = 'https://finnhub.io/api/v1'

SECTOR_MAP = {
    'INTC':'Technology','AVGO':'Technology','QCOM':'Technology','ASML':'Technology',
    'APP':'Technology','NVDA':'Technology','AMD':'Technology','MSFT':'Technology',
    'AAPL':'Technology','SOUN':'AI','QUBT':'AI','DMYI':'AI','PLTR':'AI',
    'ARM':'Technology','SEMI':'Technology','SOXL':'Technology','TSM':'Technology',
    'SMCI':'Technology','RIOT':'Crypto','BITF':'Crypto','BULL':'Crypto',
    'IREN':'Energy/AI','APLD':'Energy/AI','XE':'Energy','RR':'Energy',
    'SNII':'Finance','IPOE':'Finance','KCAC':'Finance','HOOD':'Finance',
    'SOFI':'Finance','ASST':'Finance','ALCC1':'Finance',
    'MAG5':'ETF','EQQQ':'ETF','MU':'ETF','HOD':'ETF',
    'LAA':'ETF','LLL':'ETF','UBR':'ETF','GIG':'Tech/AI',
    'PONY':'Technology','XPOA':'Technology','LLY':'Biotech',
    'CRWD':'Technology','AMZN':'Technology','TSLA':'Technology',
}

def t212_get(endpoint, auth):
    try:
        r = requests.get(
            f'{T212_BASE}/{endpoint}',
            headers={'Authorization': f'Basic {auth}'},
            timeout=15
        )
        if r.status_code == 200:
            return r.json()
        print(f'T212 {endpoint} status: {r.status_code}')
    except Exception as e:
        print(f'T212 error {endpoint}: {e}')
    return None

def fh(endpoint, params={}):
    try:
        p = dict(params)
        p['token'] = FINNHUB_KEY
        r = requests.get(f'{FINNHUB_BASE}/{endpoint}', params=p, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f'FH error {endpoint}: {e}')
    return {}

def clean_symbol(ticker):
    s = ticker.split('_')[0]
    s = re.sub(r'^\d+', '', s)
    s = s.rstrip('l')
    return s.upper()

def basic_holding(h, account):
    """Return holding with just T212 data — no Finnhub calls — loads instantly"""
    ticker = h.get('ticker', '')
    symbol = clean_symbol(ticker)
    qty    = h.get('quantity', 0) or 0
    price  = h.get('currentPrice', 0) or 0
    return {
        **h,
        'symbol':         symbol,
        'sector':         SECTOR_MAP.get(symbol, 'Other'),
        'portfolioValue': round(qty * price, 2),
        'account':        account,
        'indicators':     {},
        'signal':         'Loading...',
        'news':           {},
    }

# ── ROUTES ───────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/manifest.json')
def manifest():
    return send_from_directory('.', 'manifest.json')

@app.route('/sw.js')
def sw():
    return send_from_directory('.', 'sw.js')

# ── SUMMARY ──────────────────────────────────────────────────────
@app.route('/api/summary')
def api_summary():
    isa    = t212_get('equity/account/summary', ISA_AUTH) or {}
    invest = t212_get('equity/account/summary', INVEST_AUTH) or {}
    def safe(d, *keys):
        v = d
        for k in keys:
            v = (v or {}).get(k) or 0
        return float(v or 0)
    return jsonify({
        'isa':    isa,
        'invest': invest,
        'combined': {
            'totalValue':    round(safe(isa,'totalValue') + safe(invest,'totalValue'), 2),
            'availableCash': round(safe(isa,'cash','availableToTrade') + safe(invest,'cash','availableToTrade'), 2),
            'unrealizedPnL': round(safe(isa,'investments','unrealizedProfitLoss') + safe(invest,'investments','unrealizedProfitLoss'), 2),
            'realizedPnL':   round(safe(isa,'investments','realizedProfitLoss') + safe(invest,'investments','realizedProfitLoss'), 2),
        }
    })

# ── PORTFOLIO — loads instantly, no Finnhub ───────────────────────
@app.route('/api/portfolio/isa')
def api_isa():
    holdings = t212_get('equity/portfolio', ISA_AUTH)
    if not isinstance(holdings, list):
        return jsonify({'error': 'Failed to load ISA portfolio', 'data': []})
    result = [basic_holding(h, 'ISA') for h in holdings]
    result.sort(key=lambda x: x.get('portfolioValue', 0), reverse=True)
    return jsonify({'data': result})

@app.route('/api/portfolio/invest')
def api_invest():
    holdings = t212_get('equity/portfolio', INVEST_AUTH)
    if not isinstance(holdings, list):
        return jsonify({'error': 'Failed to load Invest portfolio', 'data': []})
    result = [basic_holding(h, 'Invest') for h in holdings]
    result.sort(key=lambda x: x.get('portfolioValue', 0), reverse=True)
    return jsonify({'data': result})

# ── WATCHLIST — loads instantly ───────────────────────────────────
@app.route('/api/watchlist')
def api_watchlist():
    isa_h    = t212_get('equity/portfolio', ISA_AUTH) or []
    invest_h = t212_get('equity/portfolio', INVEST_AUTH) or []
    seen = {}
    for h in (isa_h if isinstance(isa_h, list) else []):
        t = h.get('ticker')
        if t and t not in seen:
            seen[t] = basic_holding(h, 'ISA')
    for h in (invest_h if isinstance(invest_h, list) else []):
        t = h.get('ticker')
        if t and t not in seen:
            seen[t] = basic_holding(h, 'Invest')
    items = sorted(seen.values(), key=lambda x: x.get('portfolioValue', 0), reverse=True)
    return jsonify({'data': items})

# ── INDICATORS — called per stock by frontend ─────────────────────
@app.route('/api/indicators/<symbol>')
def api_indicators(symbol):
    result = {
        'rsi': None, 'macd': None, 'macd_signal': None, 'macd_hist': None,
        'bb_upper': None, 'bb_lower': None, 'bb_middle': None,
        'ma50': None, 'ma200': None, 'signal': 'Neutral',
        'overbought': False, 'oversold': False
    }
    to_ts   = int(time.time())
    from_ts = to_ts - (300 * 86400)
    base    = {'symbol': symbol, 'resolution': 'D', 'from': from_ts, 'to': to_ts}
    try:
        # RSI
        r = fh('indicator', {**base, 'indicator': 'rsi', 'timeperiod': 14})
        lst = r.get('rsi', [])
        if lst: result['rsi'] = round(lst[-1], 2)

        # MACD
        r = fh('indicator', {**base, 'indicator': 'macd', 'fastperiod': 12, 'slowperiod': 26, 'signalperiod': 9})
        if r.get('macd'):       result['macd']        = round(r['macd'][-1], 4)
        if r.get('macdSignal'): result['macd_signal'] = round(r['macdSignal'][-1], 4)
        if r.get('macdHist'):   result['macd_hist']   = round(r['macdHist'][-1], 4)

        # Bollinger Bands
        r = fh('indicator', {**base, 'indicator': 'bbands', 'timeperiod': 20})
        if r.get('upperBand'):  result['bb_upper']  = round(r['upperBand'][-1], 2)
        if r.get('lowerBand'):  result['bb_lower']  = round(r['lowerBand'][-1], 2)
        if r.get('middleBand'): result['bb_middle'] = round(r['middleBand'][-1], 2)

        # MA50
        r = fh('indicator', {**base, 'indicator': 'sma', 'timeperiod': 50})
        if r.get('sma'): result['ma50'] = round(r['sma'][-1], 2)

        # MA200
        r = fh('indicator', {**base, 'indicator': 'sma', 'timeperiod': 200})
        if r.get('sma'): result['ma200'] = round(r['sma'][-1], 2)

        # Signal scoring
        rsi = result['rsi']
        if rsi:
            result['overbought'] = rsi > 70
            result['oversold']   = rsi < 30
        score = 0
        if rsi:
            if rsi < 30:   score += 2
            elif rsi < 45: score += 1
            elif rsi > 70: score -= 2
            elif rsi > 55: score -= 1
        if result['macd'] and result['macd_signal']:
            score += 1 if result['macd'] > result['macd_signal'] else -1
        if result['ma50'] and result['ma200']:
            score += 1 if result['ma50'] > result['ma200'] else -1
        if   score >= 3:  result['signal'] = 'Strong Bullish'
        elif score >= 1:  result['signal'] = 'Bullish'
        elif score <= -3: result['signal'] = 'Strong Bearish'
        elif score <= -1: result['signal'] = 'Bearish'
        else:             result['signal'] = 'Neutral'

    except Exception as e:
        print(f'Indicator error {symbol}: {e}')
    return jsonify(result)

# ── NEWS — called per stock by frontend ───────────────────────────
@app.route('/api/news/<symbol>')
def api_news(symbol):
    today     = datetime.now().strftime('%Y-%m-%d')
    month_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    news = fh('company-news', {'symbol': symbol, 'from': month_ago, 'to': today})
    return jsonify(news[:10] if isinstance(news, list) else [])

# ── QUOTE ─────────────────────────────────────────────────────────
@app.route('/api/quote/<symbol>')
def api_quote(symbol):
    return jsonify(fh('quote', {'symbol': symbol}))

# ── EARNINGS ─────────────────────────────────────────────────────
@app.route('/api/earnings')
def api_earnings():
    today  = datetime.now().strftime('%Y-%m-%d')
    future = (datetime.now() + timedelta(days=90)).strftime('%Y-%m-%d')
    data   = fh('calendar/earnings', {'from': today, 'to': future})
    return jsonify((data.get('earningsCalendar') or [])[:30])

# ── SUGGESTIONS ──────────────────────────────────────────────────
@app.route('/api/suggestions')
def api_suggestions():
    return jsonify({
        '1day': [
            {'ticker':'NVDA','company':'NVIDIA Corp','sector':'AI','risk':'High',
             'reason':'Strong AI chip momentum. MACD bullish crossover. Institutional accumulation.',
             'targets':{'1d':3,'2d':5,'1w':8,'1m':18,'1y':55}},
            {'ticker':'AMD','company':'Advanced Micro Devices','sector':'Technology','risk':'Medium',
             'reason':'Data centre GPU demand rising. RSI recovered from oversold. Strong earnings beat.',
             'targets':{'1d':2,'2d':4,'1w':7,'1m':15,'1y':40}},
            {'ticker':'TSLA','company':'Tesla Inc','sector':'Technology','risk':'Very High',
             'reason':'Bouncing off key support. High volume. Robotaxi catalyst upcoming.',
             'targets':{'1d':4,'2d':7,'1w':12,'1m':25,'1y':70}},
        ],
        '1week': [
            {'ticker':'PLTR','company':'Palantir Technologies','sector':'AI','risk':'High',
             'reason':'Government AI contracts expanding. Bullish wedge breakout on weekly chart.',
             'targets':{'1d':1,'2d':2,'1w':8,'1m':20,'1y':60}},
            {'ticker':'SOFI','company':'SoFi Technologies','sector':'Finance','risk':'Medium',
             'reason':'Rate cut expectations boosting fintech. RSI oversold. Strong loan growth.',
             'targets':{'1d':1,'2d':2,'1w':6,'1m':16,'1y':45}},
            {'ticker':'CRWD','company':'CrowdStrike','sector':'Technology','risk':'Medium',
             'reason':'Cybersecurity spend accelerating. AI threat landscape growing.',
             'targets':{'1d':1,'2d':2,'1w':5,'1m':12,'1y':50}},
        ],
        '1month': [
            {'ticker':'AMZN','company':'Amazon','sector':'Technology','risk':'Low',
             'reason':'AWS AI growth accelerating. Strong free cash flow. Advertising outperforming.',
             'targets':{'1d':0.5,'2d':1,'1w':3,'1m':10,'1y':35}},
            {'ticker':'MSFT','company':'Microsoft','sector':'AI','risk':'Low',
             'reason':'Copilot enterprise adoption. Azure AI growing 50%+ YoY. Stable dividend.',
             'targets':{'1d':0.5,'2d':1,'1w':3,'1m':9,'1y':28}},
            {'ticker':'LLY','company':'Eli Lilly','sector':'Biotech','risk':'Low',
             'reason':'GLP-1 weight loss drugs dominating. Strong pipeline. Multiple catalysts.',
             'targets':{'1d':0.3,'2d':0.8,'1w':3,'1m':9,'1y':38}},
        ],
        '1year': [
            {'ticker':'IONQ','company':'IonQ Inc','sector':'AI','risk':'Very High',
             'reason':'Quantum computing leader. Government contracts. Long term quantum play.',
             'targets':{'1d':2,'2d':4,'1w':8,'1m':20,'1y':150}},
            {'ticker':'RXRX','company':'Recursion Pharma','sector':'Biotech','risk':'High',
             'reason':'AI drug discovery. NVIDIA partnership. Pipeline expanding rapidly.',
             'targets':{'1d':1,'2d':2,'1w':6,'1m':18,'1y':80}},
            {'ticker':'ALAB','company':'Astera Labs','sector':'Technology','risk':'High',
             'reason':'AI data centre connectivity. Explosive revenue growth. Undervalued vs peers.',
             'targets':{'1d':1,'2d':2,'1w':5,'1m':15,'1y':70}},
        ],
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
