from flask import Flask, jsonify, Response
from flask_cors import CORS
import requests
import os
import time
import re
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
    except Exception as e: print(f'FH error: {e}')
    return {}

def clean_symbol(ticker):
    s = ticker.split('_')[0]
    s = re.sub(r'^\d+', '', s)
    s = s.rstrip('l')
    return s.upper()

def basic_holding(h, account):
    ticker = h.get('ticker', '')
    symbol = clean_symbol(ticker)
    qty = h.get('quantity', 0) or 0
    price = h.get('currentPrice', 0) or 0
    return {**h, 'symbol': symbol, 'sector': SECTOR_MAP.get(symbol, 'Other'),
            'portfolioValue': round(qty * price, 2), 'account': account,
            'indicators': {}, 'signal': 'Loading...', 'news': {}}

HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')).read()

@app.route('/')
def index():
    return Response(HTML, mimetype='text/html')

@app.route('/manifest.json')
def manifest():
    return Response('{"name":"TradingAssist-NTv0.1","short_name":"TradingAssist","start_url":"/","display":"standalone","background_color":"#0b0f1c","theme_color":"#0b0f1c"}', mimetype='application/json')

@app.route('/sw.js')
def sw():
    return Response("self.addEventListener('fetch', e => { e.respondWith(fetch(e.request)); });", mimetype='application/javascript')

@app.route('/api/summary')
def api_summary():
    isa = t212_get('equity/account/summary', ISA_AUTH) or {}
    invest = t212_get('equity/account/summary', INVEST_AUTH) or {}
    def safe(d, *keys):
        v = d
        for k in keys: v = (v or {}).get(k) or 0
        return float(v or 0)
    return jsonify({'isa': isa, 'invest': invest, 'combined': {
        'to
