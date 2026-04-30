from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import requests
import os
from functools import lru_cache
import time

app = Flask(__name__, static_folder='.')
CORS(app)

# ── API CREDENTIALS (set these in Render Environment Variables) ──
ISA_AUTH = os.environ.get('ISA_AUTH', '')          # Base64 encoded ISA key:secret
INVEST_AUTH = os.environ.get('INVEST_AUTH', '')    # Base64 encoded Invest key:secret
FINNHUB_KEY = os.environ.get('FINNHUB_KEY', '')    # Finnhub API key

T212_BASE = 'https://live.trading212.com/api/v0'
FINNHUB_BASE = 'https://finnhub.io/api/v1'

# ── SECTOR MAP ───────────────────────────────────────────────────
SECTOR_MAP = {
    'INTC_US_EQ': 'Technology', 'AVGO_US_EQ': 'Technology',
    'QCOM_US_EQ': 'Technology', 'ASML_US_EQ': 'Technology',
    'APP_US_EQ': 'Technology', 'SOUN_US_EQ': 'AI',
    'QUBT_US_EQ': 'AI', 'DMYI_US_EQ': 'AI',
    'ARM3l_EQ': 'Technology', '3AMDl_EQ': 'Technology',
    'SEMIl_EQ': 'Technology', 'SOXLl_EQ': 'Technology',
    '3TSMl_EQ': 'Technology', 'SMCIl_EQ': 'Technology',
    'RIOT_US_EQ': 'Crypto', 'BITF_US_EQ': 'Crypto',
    'BULL_US_EQ': 'Crypto', '3PLTl_EQ': 'Crypto',
    'IREN_US_EQ': 'Energy/AI', 'APLD_US_EQ': 'Energy/AI',
    'XE_US_EQ': 'Energy', 'RRl_EQ': 'Energy',
    'SNII_US_EQ': 'Finance', 'IPOE_US_EQ': 'Finance',
    'KCAC_US_EQ': 'Finance', 'HOOD_US_EQ': 'Finance',
    'MAG5l_EQ': 'ETF', 'EQQQl_EQ': 'ETF',
    '2MUl_EQ': 'ETF', '3HODl_EQ': 'ETF',
    'LAA3l_EQ': 'ETF', '3LLLl_EQ': 'ETF',
    '3UBRl_EQ': 'ETF', 'GIG_US_EQ': 'Tech/AI',
    'PONY_US_EQ': 'Technology', 'XPOA_US_EQ': 'Technology',
    'ALCC1_US_EQ': 'Finance', 'ASST_US_EQ': 'Finance',
    'DMYI_US_EQ': 'Technology',
}

def t212_headers(auth):
    return {'Authorization': f'Basic {auth}'}

def finnhub_get(endpoint, params={}):
    params['token'] = FINNHUB_KEY
    try:
        r = requests.get(f'{FINNHUB_BASE}/{endpoint}', params=params, timeout=10)
        return r.json()
    except:
        return {}

def t212_get(endpoint, auth):
    try:
        r = requests.get(f'{T212_BASE}/{endpoint}', headers=t212_headers(auth), timeout=10)
        return r.json()
    except:
        return {}

def clean_ticker(t212_ticker):
    """Convert Trading212 ticker to Finnhub symbol e.g. INTC_US_EQ -> INTC"""
    return t212_ticker.split('_')[0].replace('l', '').replace('3', '')

def get_signal(rsi, macd, macd_signal):
    """Generate bullish/bearish signal from indicators"""
    score = 0
    if rsi:
        if rsi < 30: score += 2
        elif rsi < 45: score += 1
        elif rsi > 70: score -= 2
        elif rsi > 55: score -= 1
    if macd and macd_signal:
        if macd > macd_signal: score += 1
        else: score -= 1
    if score >= 2: return 'Strong Bullish'
    elif score == 1: return 'Bullish'
    elif score == -1: return 'Bearish'
    elif score <= -2: return 'Strong Bearish'
    return 'Neutral'

def get_indicators(symbol):
    """Get RSI, MACD, Bollinger Bands for a symbol"""
    try:
        # RSI
        rsi_data = finnhub_get('indicator', {'symbol': symbol, 'resolution': 'D', 'indicator': 'rsi', 'timeperiod': 14})
        rsi = rsi_data.get('rsi', [None])[-1] if rsi_data.get('rsi') else None

        # MACD
        macd_data = finnhub_get('indicator', {'symbol': symbol, 'resolution': 'D', 'indicator': 'macd', 'fastperiod': 12, 'slowperiod': 26, 'signalperiod': 9})
        macd = macd_data.get('macd', [None])[-1] if macd_data.get('macd') else None
        macd_signal = macd_data.get('macdSignal', [None])[-1] if macd_data.get('macdSignal') else None
        macd_hist = macd_data.get('macdHist', [None])[-1] if macd_data.get('macdHist') else None

        # Bollinger Bands
        bb_data = finnhub_get('indicator', {'symbol': symbol, 'resolution': 'D', 'indicator': 'bbands', 'timeperiod': 20})
        bb_upper = bb_data.get('upperBand', [None])[-1] if bb_data.get('upperBand') else None
        bb_lower = bb_data.get('lowerBand', [None])[-1] if bb_data.get('lowerBand') else None
        bb_middle = bb_data.get('middleBand', [None])[-1] if bb_data.get('middleBand') else None

        # Moving Averages
        ma50_data = finnhub_get('indicator', {'symbol': symbol, 'resolution': 'D', 'indicator': 'sma', 'timeperiod': 50})
        ma50 = ma50_data.get('sma', [None])[-1] if ma50_data.get('sma') else None

        ma200_data = finnhub_get('indicator', {'symbol': symbol, 'resolution': 'D', 'indicator': 'sma', 'timeperiod': 200})
        ma200 = ma200_data.get('sma', [None])[-1] if ma200_data.get('sma') else None

        signal = get_signal(rsi, macd, macd_signal)

        return {
            'rsi': round(rsi, 2) if rsi else None,
            'macd': round(macd, 4) if macd else None,
            'macd_signal': round(macd_signal, 4) if macd_signal else None,
            'macd_hist': round(macd_hist, 4) if macd_hist else None,
            'bb_upper': round(bb_upper, 2) if bb_upper else None,
            'bb_lower': round(bb_lower, 2) if bb_lower else None,
            'bb_middle': round(bb_middle, 2) if bb_middle else None,
            'ma50': round(ma50, 2) if ma50 else None,
            'ma200': round(ma200, 2) if ma200 else None,
            'signal': signal,
            'overbought': rsi > 70 if rsi else False,
            'oversold': rsi < 30 if rsi else False,
        }
    except:
        return {'signal': 'Neutral', 'rsi': None, 'macd': None}

def get_news(symbol):
    """Get latest news for a symbol"""
    try:
        from datetime import datetime, timedelta
        today = datetime.now().strftime('%Y-%m-%d')
        month_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        news = finnhub_get('company-news', {'symbol': symbol, 'from': month_ago, 'to': today})
        if news and len(news) > 0:
            latest = news[0]
            return {
                'headline': latest.get('headline', ''),
                'source': latest.get('source', ''),
                'url': latest.get('url', ''),
                'datetime': latest.get('datetime', 0),
                'summary': latest.get('summary', '')[:200] + '...' if latest.get('summary') else ''
            }
    except:
        pass
    return {}

def enrich_holding(holding, account_type):
    """Add sector, indicators, news to a holding"""
    ticker = holding.get('ticker', '')
    symbol = clean_ticker(ticker)
    sector = SECTOR_MAP.get(ticker, 'Other')

    # Calculate portfolio value
    value = holding.get('quantity', 0) * holding.get('currentPrice', 0)

    # Get indicators (rate limited — basic info)
    indicators = get_indicators(symbol)

    # Get news
    news = get_news(symbol)

    return {
        **holding,
        'symbol': symbol,
        'sector': sector,
        'portfolioValue': round(value, 2),
        'account': account_type,
        'indicators': indicators,
        'signal': indicators.get('signal', 'Neutral'),
        'news': news
    }

# ── ROUTES ───────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/summary')
def get_summary():
    """Get account summaries for both accounts"""
    isa = t212_get('equity/account/summary', ISA_AUTH)
    invest = t212_get('equity/account/summary', INVEST_AUTH)
    return jsonify({
        'isa': isa,
        'invest': invest,
        'combined': {
            'totalValue': round((isa.get('totalValue', 0) or 0) + (invest.get('totalValue', 0) or 0), 2),
            'availableCash': round(
                (isa.get('cash', {}).get('availableToTrade', 0) or 0) +
                (invest.get('cash', {}).get('availableToTrade', 0) or 0), 2),
            'unrealizedPnL': round(
                (isa.get('investments', {}).get('unrealizedProfitLoss', 0) or 0) +
                (invest.get('investments', {}).get('unrealizedProfitLoss', 0) or 0), 2),
            'realizedPnL': round(
                (isa.get('investments', {}).get('realizedProfitLoss', 0) or 0) +
                (invest.get('investments', {}).get('realizedProfitLoss', 0) or 0), 2),
        }
    })

@app.route('/api/portfolio/isa')
def get_isa_portfolio():
    """Get ISA portfolio with enriched data"""
    holdings = t212_get('equity/portfolio', ISA_AUTH)
    if not isinstance(holdings, list):
        return jsonify([])
    enriched = [enrich_holding(h, 'ISA') for h in holdings]
    enriched.sort(key=lambda x: x.get('portfolioValue', 0), reverse=True)
    return jsonify(enriched)

@app.route('/api/portfolio/invest')
def get_invest_portfolio():
    """Get Invest portfolio with enriched data"""
    holdings = t212_get('equity/portfolio', INVEST_AUTH)
    if not isinstance(holdings, list):
        return jsonify([])
    enriched = [enrich_holding(h, 'Invest') for h in holdings]
    enriched.sort(key=lambda x: x.get('portfolioValue', 0), reverse=True)
    return jsonify(enriched)

@app.route('/api/watchlist')
def get_watchlist():
    """Get combined watchlist from both accounts"""
    isa = t212_get('equity/portfolio', ISA_AUTH)
    invest = t212_get('equity/portfolio', INVEST_AUTH)
    all_tickers = {}
    if isinstance(isa, list):
        for h in isa:
            t = h.get('ticker')
            if t and t not in all_tickers:
                all_tickers[t] = h
    if isinstance(invest, list):
        for h in invest:
            t = h.get('ticker')
            if t and t not in all_tickers:
                all_tickers[t] = h
    enriched = [enrich_holding(h, 'Watchlist') for h in all_tickers.values()]
    enriched.sort(key=lambda x: x.get('portfolioValue', 0), reverse=True)
    return jsonify(enriched)

@app.route('/api/news/<symbol>')
def get_stock_news(symbol):
    """Get detailed news for a specific stock"""
    from datetime import datetime, timedelta
    today = datetime.now().strftime('%Y-%m-%d')
    month_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    news = finnhub_get('company-news', {'symbol': symbol, 'from': month_ago, 'to': today})
    return jsonify(news[:10] if news else [])

@app.route('/api/earnings')
def get_earnings():
    """Get upcoming earnings for portfolio stocks"""
    from datetime import datetime, timedelta
    today = datetime.now().strftime('%Y-%m-%d')
    future = (datetime.now() + timedelta(days=90)).strftime('%Y-%m-%d')
    earnings = finnhub_get('calendar/earnings', {'from': today, 'to': future})
    return jsonify(earnings.get('earningsCalendar', [])[:20])

@app.route('/api/suggestions')
def get_suggestions():
    """Get AI stock suggestions by timeframe"""
    suggestions = {
        '1day': [
            {'ticker': 'NVDA', 'company': 'NVIDIA', 'sector': 'AI', 'risk': 'High',
             'reason': 'Strong momentum on AI chip demand. RSI pulled back from overbought.',
             'targets': {'1d': 3, '1w': 8, '1m': 15, '1y': 45}},
            {'ticker': 'TSLA', 'company': 'Tesla', 'sector': 'Technology', 'risk': 'Very High',
             'reason': 'Bouncing off key support. High volume accumulation signal.',
             'targets': {'1d': 4, '1w': 10, '1m': 20, '1y': 60}},
            {'ticker': 'AMD', 'company': 'AMD', 'sector': 'Technology', 'risk': 'Medium',
             'reason': 'MACD crossover on daily. Strong data center growth.',
             'targets': {'1d': 2, '1w': 6, '1m': 12, '1y': 35}},
        ],
        '1week': [
            {'ticker': 'PLTR', 'company': 'Palantir', 'sector': 'AI', 'risk': 'High',
             'reason': 'Government AI contracts expanding. Bullish wedge breakout.',
             'targets': {'1d': 1, '1w': 7, '1m': 18, '1y': 55}},
            {'ticker': 'SOFI', 'company': 'SoFi Technologies', 'sector': 'Finance', 'risk': 'Medium',
             'reason': 'Rate cut expectations. Fintech recovery play. RSI oversold.',
             'targets': {'1d': 1, '1w': 5, '1m': 14, '1y': 40}},
        ],
        '1month': [
            {'ticker': 'AMZN', 'company': 'Amazon', 'sector': 'Technology', 'risk': 'Low',
             'reason': 'AWS AI growth accelerating. Strong free cash flow.',
             'targets': {'1d': 0.5, '1w': 3, '1m': 10, '1y': 30}},
            {'ticker': 'MSFT', 'company': 'Microsoft', 'sector': 'AI', 'risk': 'Low',
             'reason': 'Copilot adoption driving enterprise growth. Stable dividend.',
             'targets': {'1d': 0.5, '1w': 2, '1m': 8, '1y': 25}},
        ],
        '1year': [
            {'ticker': 'CRWD', 'company': 'CrowdStrike', 'sector': 'Technology', 'risk': 'Medium',
             'reason': 'Cybersecurity spending accelerating with AI threats. Market leader.',
             'targets': {'1d': 0.5, '1w': 3, '1m': 9, '1y': 50}},
            {'ticker': 'LLY', 'company': 'Eli Lilly', 'sector': 'Biotech', 'risk': 'Low',
             'reason': 'GLP-1 drug pipeline. Weight loss market massive. Strong earnings.',
             'targets': {'1d': 0.3, '1w': 2, '1m': 7, '1y': 35}},
        ]
    }
    return jsonify(suggestions)

@app.route('/api/quote/<symbol>')
def get_quote(symbol):
    """Get live quote for a symbol"""
    quote = finnhub_get('quote', {'symbol': symbol})
    return jsonify(quote)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
