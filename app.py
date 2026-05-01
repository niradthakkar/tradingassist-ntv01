from flask import Flask, jsonify, Response
from flask_cors import CORS
import requests
import os
import time
import re
import math
from datetime import datetime, timedelta

app = Flask(__name__)
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
    'PLT':'Crypto','ARM3':'Technology',
}

def t212_get(endpoint, auth):
    try:
        r = requests.get(f'{T212_BASE}/{endpoint}', headers={'Authorization': f'Basic {auth}'}, timeout=15)
        if r.status_code == 200: return r.json()
    except Exception as e: print(f'T212 error: {e}')
    return None

def fh(endpoint, params={}):
    try:
        p = dict(params); p['token'] = FINNHUB_KEY
        r = requests.get(f'{FINNHUB_BASE}/{endpoint}', params=p, timeout=10)
        if r.status_code == 200: return r.json()
    except Exception as e: print(f'FH error {endpoint}: {e}')
    return {}

def clean_symbol(ticker):
    s = ticker.split('_')[0]
    s = re.sub(r'^\d+', '', s)
    s = s.rstrip('l')
    return s.upper()

def is_us_stock(ticker):
    return '_US_EQ' in ticker

def basic_holding(h, account):
    ticker = h.get('ticker', '')
    symbol = clean_symbol(ticker)
    qty    = h.get('quantity', 0) or 0
    avg    = h.get('averagePrice', 0) or 0
    ppl    = h.get('ppl') or 0
    us     = is_us_stock(ticker)
    portfolio_value = round((qty * avg) + ppl, 2)
    return {
        **h,
        'symbol':         symbol,
        'name':           '',
        'sector':         SECTOR_MAP.get(symbol, 'Other'),
        'portfolioValue': portfolio_value,
        'currency':       'USD' if us else 'GBP',
        'account':        account,
        'indicators':     {},
        'signal':         'Loading...',
        'news':           {},
    }

# ── INDICATOR CALCULATIONS ────────────────────────────────────────

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period-1) + gains[i]) / period
        avg_loss = (avg_loss * (period-1) + losses[i]) / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calc_ema(closes, period):
    if len(closes) < period: return []
    k = 2 / (period + 1)
    ema = [sum(closes[:period]) / period]
    for price in closes[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema

def calc_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal: return None, None, None
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    min_len = min(len(ema_fast), len(ema_slow))
    macd_line = [ema_fast[-(min_len-i)] - ema_slow[-(min_len-i)] for i in range(min_len)]
    if len(macd_line) < signal: return None, None, None
    signal_line = calc_ema(macd_line, signal)
    if not signal_line: return None, None, None
    macd_val = macd_line[-1]
    sig_val  = signal_line[-1]
    hist_val = macd_val - sig_val
    return round(macd_val, 4), round(sig_val, 4), round(hist_val, 4)

def calc_bbands(closes, period=20, std_mult=2):
    if len(closes) < period: return None, None, None
    window = closes[-period:]
    mean = sum(window) / period
    variance = sum((x - mean)**2 for x in window) / period
    std = math.sqrt(variance)
    return round(mean + std_mult*std, 2), round(mean, 2), round(mean - std_mult*std, 2)

def calc_sma(closes, period):
    if len(closes) < period: return None
    return round(sum(closes[-period:]) / period, 2)

def get_candles(symbol, resolution='D', days=300):
    to_ts   = int(time.time())
    from_ts = to_ts - (days * 86400)
    data = fh('stock/candle', {'symbol': symbol, 'resolution': resolution, 'from': from_ts, 'to': to_ts})
    if not data or data.get('s') != 'ok': return [], [], []
    return data.get('c', []), data.get('t', []), data.get('v', [])

def compute_indicators(symbol):
    closes, timestamps, volumes = get_candles(symbol)
    if not closes or len(closes) < 30:
        return {'rsi':None,'macd':None,'macd_signal':None,'macd_hist':None,
                'bb_upper':None,'bb_middle':None,'bb_lower':None,
                'ma50':None,'ma200':None,'signal':'Neutral',
                'overbought':False,'oversold':False,
                'closes':[],'timestamps':[],'volumes':[]}

    rsi   = calc_rsi(closes)
    macd, macd_sig, macd_hist = calc_macd(closes)
    bb_upper, bb_middle, bb_lower = calc_bbands(closes)
    ma50  = calc_sma(closes, 50)
    ma200 = calc_sma(closes, 200)

    # Signal scoring
    score = 0
    if rsi is not None:
        if rsi < 30:   score += 2
        elif rsi < 45: score += 1
        elif rsi > 70: score -= 2
        elif rsi > 55: score -= 1
    if macd is not None and macd_sig is not None:
        score += 1 if macd > macd_sig else -1
    if ma50 is not None and ma200 is not None:
        score += 1 if ma50 > ma200 else -1
    if ma50 is not None and closes:
        score += 1 if closes[-1] > ma50 else -1

    if   score >= 3:  signal = 'Strong Bullish'
    elif score >= 1:  signal = 'Bullish'
    elif score <= -3: signal = 'Strong Bearish'
    elif score <= -1: signal = 'Bearish'
    else:             signal = 'Neutral'

    return {
        'rsi':        rsi,
        'macd':       macd,
        'macd_signal':macd_sig,
        'macd_hist':  macd_hist,
        'bb_upper':   bb_upper,
        'bb_middle':  bb_middle,
        'bb_lower':   bb_lower,
        'ma50':       ma50,
        'ma200':      ma200,
        'signal':     signal,
        'overbought': rsi > 70 if rsi else False,
        'oversold':   rsi < 30 if rsi else False,
        'closes':     closes[-60:],
        'timestamps': timestamps[-60:],
        'volumes':    volumes[-60:],
    }

HTML_CONTENT = open('index.html', 'r', encoding='utf-8').read()

@app.route('/')
def index():
    return Response(HTML_CONTENT, mimetype='text/html; charset=utf-8')

@app.route('/manifest.json')
def manifest():
    return Response('{"name":"TradingAssist-NTv0.1","short_name":"TradingAssist","start_url":"/","display":"standalone","background_color":"#0b0f1c","theme_color":"#0b0f1c"}', mimetype='application/json')

@app.route('/sw.js')
def sw():
    return Response("self.addEventListener('fetch',function(e){e.respondWith(fetch(e.request));});", mimetype='application/javascript')

@app.route('/api/summary')
def api_summary():
    isa    = t212_get('equity/account/summary', ISA_AUTH) or {}
    invest = t212_get('equity/account/summary', INVEST_AUTH) or {}
    def safe(d, *keys):
        v = d
        for k in keys: v = (v or {}).get(k) or 0
        return float(v or 0)
    return jsonify({'isa': isa, 'invest': invest, 'combined': {
        'totalValue':    round(safe(isa,'totalValue') + safe(invest,'totalValue'), 2),
        'availableCash': round(safe(isa,'cash','availableToTrade') + safe(invest,'cash','availableToTrade'), 2),
        'unrealizedPnL': round(safe(isa,'investments','unrealizedProfitLoss') + safe(invest,'investments','unrealizedProfitLoss'), 2),
        'realizedPnL':   round(safe(isa,'investments','realizedProfitLoss') + safe(invest,'investments','realizedProfitLoss'), 2),
    }})

@app.route('/api/portfolio/isa')
def api_isa():
    holdings = t212_get('equity/portfolio', ISA_AUTH)
    if not isinstance(holdings, list): return jsonify({'error': 'Failed', 'data': []})
    result = sorted([basic_holding(h, 'ISA') for h in holdings], key=lambda x: x.get('portfolioValue', 0), reverse=True)
    return jsonify({'data': result})

@app.route('/api/portfolio/invest')
def api_invest():
    holdings = t212_get('equity/portfolio', INVEST_AUTH)
    if not isinstance(holdings, list): return jsonify({'error': 'Failed', 'data': []})
    result = sorted([basic_holding(h, 'Invest') for h in holdings], key=lambda x: x.get('portfolioValue', 0), reverse=True)
    return jsonify({'data': result})

@app.route('/api/watchlist')
def api_watchlist():
    isa_h    = t212_get('equity/portfolio', ISA_AUTH) or []
    invest_h = t212_get('equity/portfolio', INVEST_AUTH) or []
    seen = {}
    for h in (isa_h if isinstance(isa_h, list) else []):
        t = h.get('ticker')
        if t and t not in seen: seen[t] = basic_holding(h, 'ISA')
    for h in (invest_h if isinstance(invest_h, list) else []):
        t = h.get('ticker')
        if t and t not in seen: seen[t] = basic_holding(h, 'Invest')
    return jsonify({'data': sorted(seen.values(), key=lambda x: x.get('portfolioValue', 0), reverse=True)})


@app.route('/api/profile/<symbol>')
def api_profile(symbol):
    profile = fh('stock/profile2', {'symbol': symbol})
    return jsonify({'name': profile.get('name',''), 'industry': profile.get('finnhubIndustry','')})

@app.route('/api/indicators/<symbol>')
def api_indicators(symbol):
    return jsonify(compute_indicators(symbol))

@app.route('/api/stock/<symbol>')
def api_stock_detail(symbol):
    ind  = compute_indicators(symbol)
    today     = datetime.now().strftime('%Y-%m-%d')
    month_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    news = fh('company-news', {'symbol': symbol, 'from': month_ago, 'to': today})
    profile = fh('stock/profile2', {'symbol': symbol})
    quote   = fh('quote', {'symbol': symbol})
    return jsonify({
        'symbol':     symbol,
        'indicators': ind,
        'news':       news[:20] if isinstance(news, list) else [],
        'profile':    profile,
        'quote':      quote,
    })

@app.route('/api/news/<symbol>')
def api_news(symbol):
    today     = datetime.now().strftime('%Y-%m-%d')
    month_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    news = fh('company-news', {'symbol': symbol, 'from': month_ago, 'to': today})
    return jsonify(news[:20] if isinstance(news, list) else [])

@app.route('/api/news/market/<category>')
def api_market_news(category):
    valid = ['general', 'forex', 'crypto', 'merger']
    cat = category if category in valid else 'general'
    news = fh('news', {'category': cat})
    return jsonify(news[:20] if isinstance(news, list) else [])

@app.route('/api/quote/<symbol>')
def api_quote(symbol):
    return jsonify(fh('quote', {'symbol': symbol}))

@app.route('/api/earnings')
def api_earnings():
    today  = datetime.now().strftime('%Y-%m-%d')
    future = (datetime.now() + timedelta(days=90)).strftime('%Y-%m-%d')
    data   = fh('calendar/earnings', {'from': today, 'to': future})
    return jsonify((data.get('earningsCalendar') or [])[:30])

@app.route('/api/suggestions')
def api_suggestions():
    return jsonify({
        '1day': [
            {'ticker':'NVDA','company':'NVIDIA Corp','sector':'AI','risk':'High','reason':'Strong AI chip momentum. MACD bullish crossover.','targets':{'1d':3,'2d':5,'1w':8,'1m':18,'1y':55}},
            {'ticker':'AMD','company':'Advanced Micro Devices','sector':'Technology','risk':'Medium','reason':'Data centre GPU demand rising. RSI recovered.','targets':{'1d':2,'2d':4,'1w':7,'1m':15,'1y':40}},
            {'ticker':'TSLA','company':'Tesla Inc','sector':'Technology','risk':'Very High','reason':'Bouncing off key support. Robotaxi catalyst.','targets':{'1d':4,'2d':7,'1w':12,'1m':25,'1y':70}},
        ],
        '1week': [
            {'ticker':'PLTR','company':'Palantir Technologies','sector':'AI','risk':'High','reason':'Government AI contracts. Bullish wedge breakout.','targets':{'1d':1,'2d':2,'1w':8,'1m':20,'1y':60}},
            {'ticker':'SOFI','company':'SoFi Technologies','sector':'Finance','risk':'Medium','reason':'Rate cut expectations. RSI oversold.','targets':{'1d':1,'2d':2,'1w':6,'1m':16,'1y':45}},
            {'ticker':'CRWD','company':'CrowdStrike','sector':'Technology','risk':'Medium','reason':'Cybersecurity spend accelerating.','targets':{'1d':1,'2d':2,'1w':5,'1m':12,'1y':50}},
        ],
        '1month': [
            {'ticker':'AMZN','company':'Amazon','sector':'Technology','risk':'Low','reason':'AWS AI growth accelerating. Strong cash flow.','targets':{'1d':0.5,'2d':1,'1w':3,'1m':10,'1y':35}},
            {'ticker':'MSFT','company':'Microsoft','sector':'AI','risk':'Low','reason':'Copilot adoption. Azure AI growing 50%+ YoY.','targets':{'1d':0.5,'2d':1,'1w':3,'1m':9,'1y':28}},
            {'ticker':'LLY','company':'Eli Lilly','sector':'Biotech','risk':'Low','reason':'GLP-1 drugs dominating. Strong pipeline.','targets':{'1d':0.3,'2d':0.8,'1w':3,'1m':9,'1y':38}},
        ],
        '1year': [
            {'ticker':'IONQ','company':'IonQ Inc','sector':'AI','risk':'Very High','reason':'Quantum computing leader. Government contracts.','targets':{'1d':2,'2d':4,'1w':8,'1m':20,'1y':150}},
            {'ticker':'RXRX','company':'Recursion Pharma','sector':'Biotech','risk':'High','reason':'AI drug discovery. NVIDIA partnership.','targets':{'1d':1,'2d':2,'1w':6,'1m':18,'1y':80}},
            {'ticker':'ALAB','company':'Astera Labs','sector':'Technology','risk':'High','reason':'AI data centre connectivity. Explosive growth.','targets':{'1d':1,'2d':2,'1w':5,'1m':15,'1y':70}},
        ],
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
