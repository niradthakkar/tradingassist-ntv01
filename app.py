from flask import Flask, jsonify, Response
from flask_cors import CORS
import requests
import os
import time
import re
import math
import threading
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


# Fallback name map for common stocks when Finnhub profile returns empty
NAME_MAP = {
    'NVDA':'NVIDIA Corp','AMD':'Advanced Micro Devices','TSLA':'Tesla Inc',
    'AAPL':'Apple Inc','MSFT':'Microsoft Corp','GOOGL':'Alphabet Inc',
    'AMZN':'Amazon.com Inc','META':'Meta Platforms','PLTR':'Palantir Technologies',
    'SOFI':'SoFi Technologies','CRWD':'CrowdStrike Holdings','COIN':'Coinbase Global',
    'HOOD':'Robinhood Markets','MSTR':'MicroStrategy Inc','SMCI':'Super Micro Computer',
    'SNOW':'Snowflake Inc','SHOP':'Shopify Inc','UBER':'Uber Technologies',
    'RBLX':'Roblox Corp','RIVN':'Rivian Automotive','HIMS':'Hims & Hers Health',
    'SQ':'Block Inc','V':'Visa Inc','JPM':'JPMorgan Chase','LLY':'Eli Lilly',
    'ABBV':'AbbVie Inc','COST':'Costco Wholesale','UNH':'UnitedHealth Group',
    'IONQ':'IonQ Inc','RXRX':'Recursion Pharma','ALAB':'Astera Labs',
    'RKLB':'Rocket Lab USA','DDOG':'Datadog Inc','NET':'Cloudflare Inc',
    'PATH':'UiPath Inc','LUNR':'Intuitive Machines','ACHR':'Archer Aviation',
    'BITF':'Bitfarms Ltd','RIOT':'Riot Platforms','SOUN':'SoundHound AI',
    'QUBT':'Quantum Computing Inc','INTC':'Intel Corp','QCOM':'Qualcomm Inc',
    'AVGO':'Broadcom Inc','TSM':'Taiwan Semiconductor','ARM3':'Arm Holdings',
    'SOXL':'Direxion Semi Bull 3X','SEMI':'iShares Semiconductor ETF',
    'EQQQ':'Invesco EQQQ Nasdaq ETF','HOD':'WisdomTree Crude Oil 2X',
    'MU':'Micron Technology','RR':'Rolls-Royce Holdings','XE':'Xcel Energy',
    'APLD':'Applied Digital Corp','ASST':'Asset Entities Inc',
    'IPOE':'Social Capital Hedosophia','SNII':'Spinnaker Nations II',
    'ALCC1':'AleAnna Inc','XPOA':'XPO Inc','KCAC':'Kensington Capital',
    'DMYI':'dMY Technology Group','GIG':'GigCapital4','APP':'AppLovin Corp',
    'PLT':'Palantir Technologies ETF','BULL':'Direxion Daily Bull 3X',
    'ARKG':'ARK Genomic Revolution ETF','BRK.B':'Berkshire Hathaway',
}

# ── SERVER-SIDE CACHE ─────────────────────────────────────────────
_candle_cache  = {}   # symbol -> {closes, timestamps, volumes, ts}
_ind_cache     = {}   # symbol -> {rsi, macd, ...signal, ts}
_profile_cache = {}   # symbol -> {name, industry, ts}
_quote_cache   = {}   # symbol -> {c, d, dp, h, l, ts}

CANDLE_TTL  = 3600    # 1 hour
IND_TTL     = 3600
PROFILE_TTL = 86400   # 24 hours
QUOTE_TTL   = 300     # 5 minutes

def cache_valid(entry, ttl):
    return entry and (time.time() - entry.get('ts', 0)) < ttl

# ── API HELPERS ───────────────────────────────────────────────────
def t212_get(endpoint, auth):
    try:
        r = requests.get(f"{T212_BASE}/{endpoint}",
                         headers={"Authorization": f"Basic {auth}"}, timeout=15)
        if r.status_code == 200: return r.json()
    except Exception as e: print(f"T212 error: {e}")
    return None

def fh(endpoint, params={}):
    try:
        p = dict(params); p["token"] = FINNHUB_KEY
        r = requests.get(f"{FINNHUB_BASE}/{endpoint}", params=p, timeout=10)
        if r.status_code == 200: return r.json()
    except Exception as e: print(f"FH error {endpoint}: {e}")
    return {}

def clean_symbol(ticker):
    s = ticker.split("_")[0]
    s = re.sub(r"^\d+", "", s)
    s = s.rstrip("l")
    return s.upper()

def is_us(ticker): return "_US_EQ" in ticker

# ── INDICATOR MATH ────────────────────────────────────────────────
def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period-1) + gains[i]) / period
        al = (al * (period-1) + losses[i]) / period
    if al == 0: return 100.0
    return round(100 - (100 / (1 + ag/al)), 2)

def calc_ema(closes, period):
    if len(closes) < period: return []
    k = 2 / (period + 1)
    ema = [sum(closes[:period]) / period]
    for p in closes[period:]: ema.append(p * k + ema[-1] * (1 - k))
    return ema

def calc_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal: return None, None, None
    ef = calc_ema(closes, fast); es = calc_ema(closes, slow)
    mn = min(len(ef), len(es))
    ml = [ef[-(mn-i)] - es[-(mn-i)] for i in range(mn)]
    if len(ml) < signal: return None, None, None
    sl = calc_ema(ml, signal)
    if not sl: return None, None, None
    return round(ml[-1], 4), round(sl[-1], 4), round(ml[-1]-sl[-1], 4)

def calc_bbands(closes, period=20, mult=2):
    if len(closes) < period: return None, None, None
    w = closes[-period:]; mean = sum(w)/period
    std = math.sqrt(sum((x-mean)**2 for x in w)/period)
    return round(mean+mult*std, 2), round(mean, 2), round(mean-mult*std, 2)

def calc_sma(closes, period):
    if len(closes) < period: return None
    return round(sum(closes[-period:])/period, 2)

def score_signal(rsi, macd, macd_sig, ma50, ma200, closes):
    score = 0
    # RSI scoring
    if rsi is not None:
        if rsi < 25:   score += 3
        elif rsi < 35: score += 2
        elif rsi < 45: score += 1
        elif rsi > 75: score -= 3
        elif rsi > 65: score -= 2
        elif rsi > 55: score -= 1
    # MACD scoring
    if macd is not None and macd_sig is not None:
        diff = macd - macd_sig
        if diff > 0:   score += 2 if diff > abs(macd)*0.1 else 1
        else:          score -= 2 if abs(diff) > abs(macd)*0.1 else 1
    # MA cross scoring
    if ma50 and ma200:
        score += 2 if ma50 > ma200 else -2
    # Recent momentum - last 5 days vs previous 5 days
    if closes and len(closes) >= 10:
        recent  = sum(closes[-5:]) / 5
        prev    = sum(closes[-10:-5]) / 5
        if prev > 0:
            mom = (recent - prev) / prev * 100
            if mom > 2:   score += 2
            elif mom > 0.5: score += 1
            elif mom < -2:  score -= 2
            elif mom < -0.5:score -= 1
    # Price vs MA50
    if ma50 and closes:
        score += 1 if closes[-1] > ma50 else -1

    if   score >= 5:  return "Strong Bullish"
    elif score >= 2:  return "Bullish"
    elif score <= -5: return "Strong Bearish"
    elif score <= -2: return "Bearish"
    else:             return "Neutral"

# ── DATA FETCHERS WITH CACHE ──────────────────────────────────────
def get_candles(symbol):
    if cache_valid(_candle_cache.get(symbol), CANDLE_TTL):
        return _candle_cache[symbol]
    to_ts   = int(time.time())
    from_ts = to_ts - (300 * 86400)
    data = fh("stock/candle", {"symbol": symbol, "resolution": "D",
                                "from": from_ts, "to": to_ts})
    if not data or data.get("s") != "ok":
        return {"closes": [], "timestamps": [], "volumes": [], "ts": time.time()}
    entry = {
        "closes":     data.get("c", []),
        "timestamps": data.get("t", []),
        "volumes":    data.get("v", []),
        "ts":         time.time()
    }
    _candle_cache[symbol] = entry
    return entry

def get_indicators(symbol):
    if cache_valid(_ind_cache.get(symbol), IND_TTL):
        return _ind_cache[symbol]
    candles = get_candles(symbol)
    closes  = candles["closes"]
    if len(closes) < 30:
        entry = {"rsi": None, "macd": None, "macd_signal": None, "macd_hist": None,
                 "bb_upper": None, "bb_middle": None, "bb_lower": None,
                 "ma50": None, "ma200": None, "signal": "Neutral",
                 "overbought": False, "oversold": False, "ts": time.time()}
        _ind_cache[symbol] = entry
        return entry
    rsi          = calc_rsi(closes)
    macd, ms, mh = calc_macd(closes)
    bbu, bbm, bbl= calc_bbands(closes)
    ma50         = calc_sma(closes, 50)
    ma200        = calc_sma(closes, 200)
    signal       = score_signal(rsi, macd, ms, ma50, ma200, closes)
    entry = {
        "rsi": rsi, "macd": macd, "macd_signal": ms, "macd_hist": mh,
        "bb_upper": bbu, "bb_middle": bbm, "bb_lower": bbl,
        "ma50": ma50, "ma200": ma200, "signal": signal,
        "overbought": rsi > 70 if rsi else False,
        "oversold":   rsi < 30 if rsi else False,
        "closes":     closes[-60:],
        "timestamps": candles["timestamps"][-60:],
        "ts":         time.time()
    }
    _ind_cache[symbol] = entry
    return entry

def get_profile(symbol):
    if cache_valid(_profile_cache.get(symbol), PROFILE_TTL):
        cached = _profile_cache[symbol]
        # If cached name is empty, try NAME_MAP
        if not cached.get("name"):
            cached["name"] = NAME_MAP.get(symbol, "")
        return cached
    data = fh("stock/profile2", {"symbol": symbol})
    name = data.get("name","") or NAME_MAP.get(symbol, "")
    entry = {"name": name, "industry": data.get("finnhubIndustry",""), "ts": time.time()}
    _profile_cache[symbol] = entry
    return entry

def get_quote(symbol):
    if cache_valid(_quote_cache.get(symbol), QUOTE_TTL):
        return _quote_cache[symbol]
    data = fh("quote", {"symbol": symbol})
    entry = {**data, "ts": time.time()}
    _quote_cache[symbol] = entry
    return entry

# ── BACKGROUND PRE-FETCH ──────────────────────────────────────────
_bg_symbols = set()
_bg_lock    = threading.Lock()

def background_prefetch():
    """Runs in a daemon thread - pre-fetches indicators for all holdings."""
    while True:
        with _bg_lock:
            syms = list(_bg_symbols)
        for sym in syms:
            try:
                if not cache_valid(_ind_cache.get(sym), IND_TTL):
                    get_indicators(sym)
                    time.sleep(1.2)   # Respect Finnhub rate limit
                if not cache_valid(_profile_cache.get(sym), PROFILE_TTL):
                    get_profile(sym)
                    time.sleep(0.5)
            except Exception as e:
                print(f"BG prefetch error {sym}: {e}")
        time.sleep(30)  # Check again every 30s

# Start background thread
_bg_thread = threading.Thread(target=background_prefetch, daemon=True)
_bg_thread.start()

def register_symbols(holdings):
    """Register symbols for background pre-fetching."""
    with _bg_lock:
        for h in holdings:
            sym = h.get("symbol")
            if sym: _bg_symbols.add(sym)

# ── HOLDING BUILDER ───────────────────────────────────────────────
def basic_holding(h, account):
    ticker = h.get("ticker", "")
    symbol = clean_symbol(ticker)
    qty    = h.get("quantity", 0) or 0
    avg    = h.get("averagePrice", 0) or 0
    ppl    = h.get("ppl") or 0
    us     = is_us(ticker)
    return {
        **h,
        "symbol":         symbol,
        "name":           _profile_cache.get(symbol, {}).get("name","") or NAME_MAP.get(symbol,""),
        "sector":         SECTOR_MAP.get(symbol, "Other"),
        "portfolioValue": round((qty * avg) + ppl, 2),
        "currency":       "USD" if us else "GBP",
        "account":        account,
        "indicators":     {},
        "signal":         "Loading...",
        "news":           {},
    }

# ── FLASK ROUTES ──────────────────────────────────────────────────
HTML_CONTENT = open("index.html", "r", encoding="utf-8").read()

@app.route("/")
def index():
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
        html = open(path, 'r', encoding='utf-8').read()
        return Response(html, mimetype='text/html; charset=utf-8')
    except Exception as e:
        return Response('<h1>Loading...</h1><script>setTimeout(()=>location.reload(),3000)</script>', mimetype='text/html')

@app.route("/manifest.json")
def manifest():
    return Response('{"name":"TradingAssist-NTv0.1","short_name":"TradingAssist","start_url":"/","display":"standalone","background_color":"#0b0f1c","theme_color":"#0b0f1c"}', mimetype="application/json")

@app.route("/sw.js")
def sw():
    return Response("self.addEventListener('fetch',function(e){e.respondWith(fetch(e.request));});", mimetype="application/javascript")

@app.route("/api/summary")
def api_summary():
    isa    = t212_get("equity/account/summary", ISA_AUTH) or {}
    invest = t212_get("equity/account/summary", INVEST_AUTH) or {}
    def safe(d, *keys):
        v = d
        for k in keys: v = (v or {}).get(k) or 0
        return float(v or 0)
    return jsonify({"isa": isa, "invest": invest, "combined": {
        "totalValue":    round(safe(isa,"totalValue") + safe(invest,"totalValue"), 2),
        "availableCash": round(safe(isa,"cash","availableToTrade") + safe(invest,"cash","availableToTrade"), 2),
        "unrealizedPnL": round(safe(isa,"investments","unrealizedProfitLoss") + safe(invest,"investments","unrealizedProfitLoss"), 2),
        "realizedPnL":   round(safe(isa,"investments","realizedProfitLoss") + safe(invest,"investments","realizedProfitLoss"), 2),
    }})

@app.route("/api/portfolio/isa")
def api_isa():
    holdings = t212_get("equity/portfolio", ISA_AUTH)
    if not isinstance(holdings, list): return jsonify({"error": "Failed", "data": []})
    result = [basic_holding(h, "ISA") for h in holdings]
    result.sort(key=lambda x: x.get("portfolioValue", 0), reverse=True)
    register_symbols(result)
    return jsonify({"data": result})

@app.route("/api/portfolio/invest")
def api_invest():
    holdings = t212_get("equity/portfolio", INVEST_AUTH)
    if not isinstance(holdings, list): return jsonify({"error": "Failed", "data": []})
    result = [basic_holding(h, "Invest") for h in holdings]
    result.sort(key=lambda x: x.get("portfolioValue", 0), reverse=True)
    register_symbols(result)
    return jsonify({"data": result})

@app.route("/api/watchlist")
def api_watchlist():
    isa_h    = t212_get("equity/portfolio", ISA_AUTH) or []
    invest_h = t212_get("equity/portfolio", INVEST_AUTH) or []
    seen = {}
    for h in (isa_h if isinstance(isa_h, list) else []):
        t = h.get("ticker")
        if t and t not in seen: seen[t] = basic_holding(h, "ISA")
    for h in (invest_h if isinstance(invest_h, list) else []):
        t = h.get("ticker")
        if t and t not in seen: seen[t] = basic_holding(h, "Invest")
    items = sorted(seen.values(), key=lambda x: x.get("portfolioValue", 0), reverse=True)
    register_symbols(items)
    return jsonify({"data": items})

@app.route("/api/indicators/<symbol>")
def api_indicators(symbol):
    return jsonify(get_indicators(symbol))

@app.route("/api/stock/<symbol>")
def api_stock_detail(symbol):
    ind     = get_indicators(symbol)
    profile = get_profile(symbol)
    quote   = get_quote(symbol)
    today     = datetime.now().strftime("%Y-%m-%d")
    month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    news = fh("company-news", {"symbol": symbol, "from": month_ago, "to": today})
    return jsonify({
        "symbol":     symbol,
        "indicators": ind,
        "profile":    profile,
        "quote":      quote,
        "news":       news[:20] if isinstance(news, list) else [],
    })

@app.route("/api/profile/<symbol>")
def api_profile(symbol):
    p = get_profile(symbol)
    return jsonify({"name": p.get("name",""), "industry": p.get("industry","")})

@app.route("/api/news/<symbol>")
def api_news(symbol):
    today     = datetime.now().strftime("%Y-%m-%d")
    month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    news = fh("company-news", {"symbol": symbol, "from": month_ago, "to": today})
    return jsonify(news[:20] if isinstance(news, list) else [])

@app.route("/api/news/market/<category>")
def api_market_news(category):
    valid = ["general", "forex", "crypto", "merger"]
    cat = category if category in valid else "general"
    news = fh("news", {"category": cat})
    return jsonify(news[:20] if isinstance(news, list) else [])

@app.route("/api/quote/<symbol>")
def api_quote(symbol):
    return jsonify(get_quote(symbol))

@app.route("/api/earnings")
def api_earnings():
    today    = datetime.now().strftime("%Y-%m-%d")
    future   = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
    past     = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    upcoming_data = fh("calendar/earnings", {"from": today, "to": future})
    past_data     = fh("calendar/earnings", {"from": past, "to": today})

    def enrich(item):
        sym = item.get("symbol","")
        # Get quote for current price and change
        q = get_quote(sym) if sym else {}
        # Get profile for company name
        p = _profile_cache.get(sym,{})
        name = p.get("name","") or NAME_MAP.get(sym,"")
        return {
            **item,
            "companyName":      name,
            "currentPrice":     q.get("c"),
            "priceChange":      q.get("d"),
            "priceChangePct":   q.get("dp"),
            "inPortfolio":      sym in owned_symbols(),
        }

    def owned_symbols():
        isa    = t212_get("equity/portfolio", ISA_AUTH) or []
        invest = t212_get("equity/portfolio", INVEST_AUTH) or []
        syms = set()
        for h in (isa if isinstance(isa,list) else []):
            syms.add(clean_symbol(h.get("ticker","")))
        for h in (invest if isinstance(invest,list) else []):
            syms.add(clean_symbol(h.get("ticker","")))
        return syms

    upcoming = [enrich(e) for e in (upcoming_data.get("earningsCalendar") or [])[:40]]
    past_list = [enrich(e) for e in (past_data.get("earningsCalendar") or [])[:40]]

    return jsonify({"upcoming": upcoming, "past": past_list})

@app.route("/api/cache/status")
def api_cache_status():
    return jsonify({
        "candles":  len(_candle_cache),
        "indicators": len(_ind_cache),
        "profiles": len(_profile_cache),
        "quotes":   len(_quote_cache),
        "bg_symbols": len(_bg_symbols),
    })

@app.route("/api/suggestions")
def api_suggestions():
    return jsonify({
        "1day": [
            {"ticker":"NVDA","company":"NVIDIA Corp","sector":"AI","risk":"High","reason":"Strong AI chip momentum. MACD bullish crossover. Institutional accumulation continuing.","perf":{"1d":3.2,"2d":5.1,"1w":-1.2,"1m":12.4},"targets":{"1d":3,"2d":5,"1w":8,"1m":18,"1y":55}},
            {"ticker":"AMD","company":"Advanced Micro Devices","sector":"Technology","risk":"Medium","reason":"Data centre GPU demand rising. RSI recovered from oversold territory. Strong earnings beat expected.","perf":{"1d":2.1,"2d":3.8,"1w":-2.1,"1m":8.3},"targets":{"1d":2,"2d":4,"1w":7,"1m":15,"1y":40}},
            {"ticker":"TSLA","company":"Tesla Inc","sector":"Technology","risk":"Very High","reason":"Bouncing off key support level. High volume accumulation. Robotaxi catalyst upcoming.","perf":{"1d":4.5,"2d":6.2,"1w":2.1,"1m":-5.3},"targets":{"1d":4,"2d":7,"1w":12,"1m":25,"1y":70}},
            {"ticker":"META","company":"Meta Platforms","sector":"Technology","risk":"Medium","reason":"AI ad revenue acceleration. Llama models gaining enterprise adoption. Strong FCF.","perf":{"1d":1.8,"2d":2.9,"1w":4.2,"1m":15.1},"targets":{"1d":2,"2d":3,"1w":6,"1m":14,"1y":45}},
            {"ticker":"GOOGL","company":"Alphabet Inc","sector":"Technology","risk":"Low","reason":"Search AI integration driving revenue. YouTube momentum. Cloud growing 28% YoY.","perf":{"1d":1.2,"2d":2.1,"1w":3.5,"1m":9.8},"targets":{"1d":1.5,"2d":2.5,"1w":5,"1m":12,"1y":38}},
            {"ticker":"MSTR","company":"MicroStrategy","sector":"Crypto","risk":"Very High","reason":"Bitcoin proxy play. Leveraged BTC exposure. Strong institutional interest in crypto.","perf":{"1d":5.2,"2d":8.1,"1w":3.2,"1m":-8.4},"targets":{"1d":5,"2d":8,"1w":15,"1m":30,"1y":120}},
            {"ticker":"COIN","company":"Coinbase Global","sector":"Crypto","risk":"Very High","reason":"Crypto market recovery. Regulatory clarity improving. Strong trading volume uptick.","perf":{"1d":3.8,"2d":5.5,"1w":1.2,"1m":-12.3},"targets":{"1d":4,"2d":6,"1w":10,"1m":22,"1y":80}},
            {"ticker":"SMCI","company":"Super Micro Computer","sector":"Technology","risk":"High","reason":"AI server demand surge. NVIDIA partnership. Data centre build-out accelerating globally.","perf":{"1d":4.1,"2d":6.8,"1w":-3.2,"1m":-18.5},"targets":{"1d":4,"2d":7,"1w":12,"1m":25,"1y":90}},
            {"ticker":"PLTR","company":"Palantir Technologies","sector":"AI","risk":"High","reason":"Government AI contracts expanding rapidly. AIP platform gaining commercial traction.","perf":{"1d":2.5,"2d":4.1,"1w":6.8,"1m":22.3},"targets":{"1d":2,"2d":4,"1w":8,"1m":20,"1y":65}},
            {"ticker":"HOOD","company":"Robinhood Markets","sector":"Finance","risk":"High","reason":"Crypto trading volumes surging. New product launches. Younger investor base growing.","perf":{"1d":3.1,"2d":4.9,"1w":2.3,"1m":8.7},"targets":{"1d":3,"2d":5,"1w":9,"1m":18,"1y":55}},
        ],
        "1week": [
            {"ticker":"PLTR","company":"Palantir Technologies","sector":"AI","risk":"High","reason":"Government AI contracts expanding. Bullish wedge breakout on weekly chart. AIP commercial momentum.","perf":{"1d":2.5,"2d":4.1,"1w":6.8,"1m":22.3},"targets":{"1d":1,"2d":2,"1w":8,"1m":20,"1y":60}},
            {"ticker":"SOFI","company":"SoFi Technologies","sector":"Finance","risk":"Medium","reason":"Rate cut expectations boosting fintech. RSI oversold. Strong loan growth and banking licence benefits.","perf":{"1d":0.8,"2d":1.5,"1w":-3.2,"1m":-8.1},"targets":{"1d":1,"2d":2,"1w":6,"1m":16,"1y":45}},
            {"ticker":"CRWD","company":"CrowdStrike","sector":"Technology","risk":"Medium","reason":"Cybersecurity spend accelerating post-breach awareness. AI-powered threat detection leader.","perf":{"1d":1.2,"2d":2.3,"1w":4.5,"1m":11.2},"targets":{"1d":1,"2d":2,"1w":5,"1m":12,"1y":50}},
            {"ticker":"SNOW","company":"Snowflake Inc","sector":"Technology","risk":"High","reason":"Data cloud demand growing. AI workloads driving usage. New CEO executing well on strategy.","perf":{"1d":0.9,"2d":1.8,"1w":-1.5,"1m":5.3},"targets":{"1d":1,"2d":2,"1w":7,"1m":18,"1y":55}},
            {"ticker":"SHOP","company":"Shopify Inc","sector":"Technology","risk":"Medium","reason":"E-commerce AI tools gaining traction. International expansion accelerating. Strong merchant growth.","perf":{"1d":1.1,"2d":2.2,"1w":3.8,"1m":14.6},"targets":{"1d":1,"2d":2,"1w":6,"1m":15,"1y":48}},
            {"ticker":"SQ","company":"Block Inc","sector":"Finance","risk":"High","reason":"Bitcoin integration. Cash App growing. Square ecosystem expanding into new markets globally.","perf":{"1d":1.5,"2d":2.8,"1w":-0.8,"1m":-4.2},"targets":{"1d":1,"2d":2,"1w":7,"1m":17,"1y":52}},
            {"ticker":"UBER","company":"Uber Technologies","sector":"Technology","risk":"Low","reason":"Autonomous vehicle partnerships. Profitability inflection. Freight business recovering strongly.","perf":{"1d":0.7,"2d":1.4,"1w":2.9,"1m":8.4},"targets":{"1d":1,"2d":1.5,"1w":5,"1m":11,"1y":35}},
            {"ticker":"RBLX","company":"Roblox Corp","sector":"Technology","risk":"High","reason":"Metaverse monetisation improving. AI-generated content tools. Teen demographic engagement rising.","perf":{"1d":1.8,"2d":3.1,"1w":-2.4,"1m":6.8},"targets":{"1d":2,"2d":3,"1w":8,"1m":18,"1y":60}},
            {"ticker":"HIMS","company":"Hims & Hers Health","sector":"Biotech","risk":"High","reason":"GLP-1 compounding opportunity. Telehealth platform expanding. Strong subscriber growth momentum.","perf":{"1d":2.2,"2d":3.8,"1w":5.1,"1m":18.9},"targets":{"1d":2,"2d":4,"1w":9,"1m":22,"1y":75}},
            {"ticker":"RIVN","company":"Rivian Automotive","sector":"Technology","risk":"Very High","reason":"Amazon delivery van contract. VW partnership funding secured. Production ramp accelerating.","perf":{"1d":2.8,"2d":4.5,"1w":-1.8,"1m":-9.3},"targets":{"1d":3,"2d":5,"1w":10,"1m":20,"1y":80}},
        ],
        "1month": [
            {"ticker":"AMZN","company":"Amazon","sector":"Technology","risk":"Low","reason":"AWS AI growth accelerating 40%+ YoY. Strong advertising revenue. Healthcare expansion beginning.","perf":{"1d":0.8,"2d":1.5,"1w":3.2,"1m":9.8},"targets":{"1d":0.5,"2d":1,"1w":3,"1m":10,"1y":35}},
            {"ticker":"MSFT","company":"Microsoft","sector":"AI","risk":"Low","reason":"Copilot enterprise adoption accelerating. Azure AI growing 50%+ YoY. Stable dividend and buybacks.","perf":{"1d":0.6,"2d":1.2,"1w":2.8,"1m":7.5},"targets":{"1d":0.5,"2d":1,"1w":3,"1m":9,"1y":28}},
            {"ticker":"LLY","company":"Eli Lilly","sector":"Biotech","risk":"Low","reason":"GLP-1 weight loss drugs dominating market. Alzheimer pipeline progressing. Multiple catalysts ahead.","perf":{"1d":0.4,"2d":0.9,"1w":2.1,"1m":6.3},"targets":{"1d":0.3,"2d":0.8,"1w":3,"1m":9,"1y":38}},
            {"ticker":"AAPL","company":"Apple Inc","sector":"Technology","risk":"Low","reason":"AI iPhone supercycle building. Services revenue growing. India manufacturing diversification complete.","perf":{"1d":0.5,"2d":1.0,"1w":2.5,"1m":5.8},"targets":{"1d":0.5,"2d":1,"1w":3,"1m":8,"1y":25}},
            {"ticker":"V","company":"Visa Inc","sector":"Finance","risk":"Low","reason":"Global payment volumes growing. Tap-to-pay expansion. Cross-border travel recovery continuing.","perf":{"1d":0.3,"2d":0.8,"1w":1.8,"1m":4.2},"targets":{"1d":0.3,"2d":0.7,"1w":2,"1m":7,"1y":22}},
            {"ticker":"JPM","company":"JPMorgan Chase","sector":"Finance","risk":"Low","reason":"Rate environment favourable. AI adoption across banking ops. Strong capital position and dividends.","perf":{"1d":0.4,"2d":0.9,"1w":2.2,"1m":5.1},"targets":{"1d":0.4,"2d":0.8,"1w":2.5,"1m":8,"1y":24}},
            {"ticker":"BRK.B","company":"Berkshire Hathaway","sector":"Finance","risk":"Low","reason":"Record cash reserves ready to deploy. Buffett value approach. Insurance business performing well.","perf":{"1d":0.2,"2d":0.5,"1w":1.5,"1m":3.8},"targets":{"1d":0.3,"2d":0.6,"1w":2,"1m":6,"1y":18}},
            {"ticker":"UNH","company":"UnitedHealth Group","sector":"Biotech","risk":"Low","reason":"Healthcare demand inelastic. AI claims processing reducing costs. Strong managed care growth.","perf":{"1d":0.3,"2d":0.7,"1w":1.9,"1m":4.5},"targets":{"1d":0.3,"2d":0.7,"1w":2,"1m":7,"1y":20}},
            {"ticker":"ABBV","company":"AbbVie Inc","sector":"Biotech","risk":"Low","reason":"Humira biosimilar transition managed well. Skyrizi and Rinvoq growing fast. Strong dividend yield.","perf":{"1d":0.4,"2d":0.8,"1w":2.0,"1m":5.5},"targets":{"1d":0.3,"2d":0.7,"1w":2.5,"1m":8,"1y":22}},
            {"ticker":"COST","company":"Costco Wholesale","sector":"Consumer","risk":"Low","reason":"Membership renewal rates at record highs. Inflation-resistant model. International expansion strong.","perf":{"1d":0.3,"2d":0.7,"1w":1.8,"1m":4.9},"targets":{"1d":0.3,"2d":0.6,"1w":2,"1m":6,"1y":20}},
        ],
        "1year": [
            {"ticker":"IONQ","company":"IonQ Inc","sector":"Quantum","risk":"Very High","reason":"Quantum computing leader. Government contracts expanding. Long-term quantum advantage emerging.","perf":{"1d":2.1,"2d":3.5,"1w":8.2,"1m":25.4},"targets":{"1d":2,"2d":4,"1w":8,"1m":20,"1y":150}},
            {"ticker":"RXRX","company":"Recursion Pharma","sector":"Biotech","risk":"High","reason":"AI drug discovery pioneer. NVIDIA partnership deepening. Massive pipeline with multiple catalysts.","perf":{"1d":1.5,"2d":2.8,"1w":5.1,"1m":12.3},"targets":{"1d":1,"2d":2,"1w":6,"1m":18,"1y":80}},
            {"ticker":"ALAB","company":"Astera Labs","sector":"Technology","risk":"High","reason":"AI data centre connectivity chips. Explosive revenue growth. Undervalued vs semiconductor peers.","perf":{"1d":1.8,"2d":3.2,"1w":6.5,"1m":18.7},"targets":{"1d":1,"2d":2,"1w":5,"1m":15,"1y":70}},
            {"ticker":"ARKG","company":"ARK Genomics ETF","sector":"Biotech","risk":"High","reason":"Genomic revolution multi-year theme. CRISPR, gene editing, biotech convergence. Long-term megatrend.","perf":{"1d":0.8,"2d":1.5,"1w":3.2,"1m":8.9},"targets":{"1d":1,"2d":2,"1w":5,"1m":15,"1y":65}},
            {"ticker":"RKLB","company":"Rocket Lab USA","sector":"Technology","risk":"Very High","reason":"Small satellite launch market leader. Neutron rocket development on track. Space economy growing.","perf":{"1d":2.5,"2d":4.2,"1w":9.1,"1m":28.5},"targets":{"1d":2,"2d":4,"1w":10,"1m":25,"1y":120}},
            {"ticker":"DDOG","company":"Datadog Inc","sector":"Technology","risk":"Medium","reason":"Observability platform essential for AI workloads. Strong net revenue retention. Cloud monitoring leader.","perf":{"1d":0.9,"2d":1.8,"1w":3.5,"1m":10.2},"targets":{"1d":1,"2d":2,"1w":5,"1m":14,"1y":55}},
            {"ticker":"NET","company":"Cloudflare Inc","sector":"Technology","risk":"Medium","reason":"Zero trust security leader. AI inference at edge growing. Developer platform gaining enterprise traction.","perf":{"1d":1.1,"2d":2.1,"1w":4.2,"1m":12.8},"targets":{"1d":1,"2d":2,"1w":5,"1m":15,"1y":60}},
            {"ticker":"PATH","company":"UiPath Inc","sector":"AI","risk":"High","reason":"Enterprise automation with AI. Agentic AI workflows emerging. Large installed base to upsell.","perf":{"1d":1.3,"2d":2.5,"1w":4.8,"1m":14.1},"targets":{"1d":1,"2d":2,"1w":6,"1m":16,"1y":65}},
            {"ticker":"LUNR","company":"Intuitive Machines","sector":"Technology","risk":"Very High","reason":"NASA lunar contracts. Commercial space economy pioneer. First mover advantage on Moon logistics.","perf":{"1d":3.5,"2d":5.8,"1w":12.4,"1m":38.2},"targets":{"1d":3,"2d":5,"1w":12,"1m":30,"1y":200}},
            {"ticker":"ACHR","company":"Archer Aviation","sector":"Technology","risk":"Very High","reason":"eVTOL air taxi leader. United Airlines partnership. FAA certification pathway progressing on schedule.","perf":{"1d":2.8,"2d":4.5,"1w":9.8,"1m":32.1},"targets":{"1d":3,"2d":5,"1w":12,"1m":28,"1y":180}},
        ],
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
