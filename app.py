from flask import Flask, jsonify, Response, request, session, redirect
from flask_cors import CORS
import requests, os, time, re, math, threading, json, hashlib, secrets
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# ── PATHS ─────────────────────────────────────────────────────────────
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'users.json')

# ── USER STORAGE (PostgreSQL with file fallback) ─────────────────────
import base64

DATABASE_URL = os.environ.get('DATABASE_URL', '')

def get_db():
    """Get PostgreSQL connection"""
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    return conn

def init_db():
    """Create users table if it doesn't exist"""
    if not DATABASE_URL:
        return
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                data JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("Database initialised successfully")
    except Exception as e:
        print(f"Database init error: {e}")

def load_users():
    """Load all users from PostgreSQL or file fallback"""
    if DATABASE_URL:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT email, data FROM users")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return {row[0]: row[1] for row in rows}
        except Exception as e:
            print(f"DB load error: {e}")
    # File fallback
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE) as f: return json.load(f)
        except: pass
    return {}

def save_users(users):
    """Save all users to PostgreSQL or file fallback"""
    if DATABASE_URL:
        try:
            conn = get_db()
            cur = conn.cursor()
            for email, data in users.items():
                import psycopg2.extras
                cur.execute("""
                    INSERT INTO users (email, data, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (email) DO UPDATE
                    SET data = EXCLUDED.data, updated_at = NOW()
                """, (email, json.dumps(data)))
            conn.commit()
            cur.close()
            conn.close()
            return
        except Exception as e:
            print(f"DB save error: {e}")
    # File fallback
    try:
        with open(USERS_FILE, 'w') as f:
            json.dump(users, f, indent=2)
    except Exception as e:
        print(f"File save error: {e}")

def get_user(username):
    """Get single user efficiently from DB"""
    if DATABASE_URL:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT data FROM users WHERE email = %s", (username,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            return row[0] if row else None
        except Exception as e:
            print(f"DB get_user error: {e}")
    # File fallback
    return load_users().get(username)

# Initialise database on startup
init_db()

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# get_user defined in storage section above

def current_user():
    return session.get('username')

def require_login(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            return jsonify({'error': 'Not authenticated'}), 401
        return f(*args, **kwargs)
    return decorated

# ── BROKER CONFIG ─────────────────────────────────────────────────────
BROKER_TYPES = {
    'trading212_isa':    {'name': 'Trading212 ISA',    'base': 'https://live.trading212.com/api/v0', 'help': 'trading212'},
    'trading212_invest': {'name': 'Trading212 Invest', 'base': 'https://live.trading212.com/api/v0', 'help': 'trading212'},
    'trading212_us':     {'name': 'Trading212 (US)',   'base': 'https://live.trading212.com/api/v0', 'help': 'trading212'},
    'custom':            {'name': 'Custom',             'base': '', 'help': 'custom'},
}

FINNHUB_BASE = 'https://finnhub.io/api/v1'

# ── MAPS & CONSTANTS ──────────────────────────────────────────────────
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
    'BITF':'Keel Infrastructure','RIOT':'Riot Platforms','SOUN':'SoundHound AI',
    'QUBT':'Quantum Computing Inc','INTC':'Intel Corp','QCOM':'Qualcomm Inc',
    'AVGO':'Broadcom Inc','TSM':'Taiwan Semiconductor','ARM3':'Arm Holdings',
    'SOXL':'Direxion Daily Semi Bull 3X','SEMI':'iShares Semiconductor ETF',
    'EQQQ':'Invesco EQQQ Nasdaq','HOD':'WisdomTree Crude Oil 2X',
    'MU':'Micron Technology','RR':'Rolls-Royce Holdings','XE':'Xcel Energy',
    'APLD':'Applied Digital Corp','ASST':'Asset Entities Inc',
    'APP':'AppLovin Corp','BULL':'Direxion Bull 3X','GIG':'GigCapital4',
    'DMYI':'dMY Technology','SNII':'Spinnaker Nations II',
    'IPOE':'Social Capital Hedosophia','ALCC1':'AleAnna Inc',
    'XPOA':'XPO Inc','KCAC':'Kensington Capital','PLT':'Palantir ETF',
    'BRK.B':'Berkshire Hathaway B',
}

LEVERAGE_MAP = {
    '3AMDl_EQ': {'leverage':'3x','underlying':'AMD', 'name':'3x AMD ETP'},
    '2MUl_EQ':  {'leverage':'2x','underlying':'MU',  'name':'2x Micron ETP'},
    '3PLTl_EQ': {'leverage':'3x','underlying':'PLTR','name':'3x Palantir ETP'},
    '3TSMl_EQ': {'leverage':'3x','underlying':'TSM', 'name':'3x Taiwan Semi ETP'},
    '3HODl_EQ': {'leverage':'2x','underlying':'OIL', 'name':'2x Crude Oil ETP'},
    'SOXLl_EQ': {'leverage':'3x','underlying':'SOXX','name':'3x Semiconductor ETF'},
    'ARM3l_EQ': {'leverage':'3x','underlying':'ARM', 'name':'3x Arm Holdings ETP'},
    'SEMIl_EQ': {'leverage':'1x','underlying':'SOXX','name':'Semiconductor ETF'},
    'EQQQl_EQ': {'leverage':'1x','underlying':'QQQ', 'name':'Invesco EQQQ Nasdaq'},
    'RRl_EQ':   {'leverage':'1x','underlying':'RR',  'name':'Rolls-Royce Holdings'},
}

MARKET_CAP = {
    'AAPL':3000,'MSFT':2900,'NVDA':2800,'GOOGL':2000,'AMZN':1900,'META':1400,
    'TSLA':800,'AVGO':700,'LLY':700,'V':550,'JPM':500,'UNH':480,
    'AMD':220,'INTC':180,'QCOM':170,'PLTR':170,'CRWD':90,'COIN':50,
    'HOOD':15,'SOFI':10,'RIVN':10,'HIMS':5,'SQ':40,'SHOP':100,'UBER':150,
}


# ── BROKER REGISTRY ───────────────────────────────────────────────────
BROKER_REGISTRY = {
    'trading212_isa': {
        'name':        'Trading212 ISA',
        'shortname':   'T212 ISA',
        'group':       'Trading212',
        'status':      'live',
        'region':      'UK',
        'description': 'Stocks & Shares ISA account',
        'auth_type':   'header',
        'base_url':    'https://live.trading212.com/api/v0',
        'endpoints':   {'portfolio': 'equity/portfolio', 'summary': 'equity/account/summary'},
        'key_label':   'API Key',
        'key_hint':    'e.g. 450e8e4e-a01a-4d43-b4b8-xxxxxxxxxx',
        'help_steps':  [
            'Log in to trading212.com',
            'Click your profile icon (top right)',
            'Go to Settings',
            'Click API (Beta) in left menu',
            'Click Generate API key',
            'Select your ISA account',
            'Copy the key and paste it here',
        ],
    },
    'trading212_invest': {
        'name':        'Trading212 Invest',
        'shortname':   'T212 Invest',
        'group':       'Trading212',
        'status':      'live',
        'region':      'UK',
        'description': 'General Investment account',
        'auth_type':   'header',
        'base_url':    'https://live.trading212.com/api/v0',
        'endpoints':   {'portfolio': 'equity/portfolio', 'summary': 'equity/account/summary'},
        'key_label':   'API Key',
        'key_hint':    'e.g. 450e8e4e-a01a-4d43-b4b8-xxxxxxxxxx',
        'help_steps':  [
            'Log in to trading212.com',
            'Click your profile icon (top right)',
            'Go to Settings',
            'Click API (Beta) in left menu',
            'Click Generate API key',
            'Select your Invest account',
            'Copy the key and paste it here',
        ],
    },
    'trading212_us': {
        'name':        'Trading212 (US)',
        'shortname':   'T212 US',
        'group':       'Trading212',
        'status':      'live',
        'region':      'US',
        'description': 'Single brokerage account (no ISA)',
        'auth_type':   'header',
        'base_url':    'https://live.trading212.com/api/v0',
        'endpoints':   {'portfolio': 'equity/portfolio', 'summary': 'equity/account/summary'},
        'key_label':   'API Key',
        'key_hint':    'e.g. 450e8e4e-a01a-4d43-b4b8-xxxxxxxxxx',
        'help_steps':  [
            'Log in to trading212.com',
            'Click your profile icon (top right)',
            'Go to Settings',
            'Click API (Beta)',
            'Generate and copy your API key',
        ],
    },
    'ibkr': {
        'name':        'Interactive Brokers',
        'shortname':   'IBKR',
        'group':       'Interactive Brokers',
        'status':      'coming_soon',
        'region':      'Global',
        'description': 'Full-service brokerage',
        'key_label':   'Client ID',
        'key_hint':    '',
        'help_steps':  [],
    },
    'alpaca': {
        'name':        'Alpaca',
        'shortname':   'Alpaca',
        'group':       'Alpaca',
        'status':      'coming_soon',
        'region':      'US',
        'description': 'Commission-free US stock trading',
        'key_label':   'API Key',
        'key_hint':    '',
        'help_steps':  [],
    },
    'robinhood': {
        'name':        'Robinhood',
        'shortname':   'Robinhood',
        'group':       'Robinhood',
        'status':      'coming_soon',
        'region':      'US',
        'description': 'Commission-free US trading app',
        'key_label':   'API Token',
        'key_hint':    '',
        'help_steps':  [],
    },
    'webull': {
        'name':        'Webull',
        'shortname':   'Webull',
        'group':       'Webull',
        'status':      'coming_soon',
        'region':      'US',
        'description': 'Commission-free US stock trading',
        'key_label':   'API Key',
        'key_hint':    '',
        'help_steps':  [],
    },
    'td_ameritrade': {
        'name':        'TD Ameritrade / Schwab',
        'shortname':   'Schwab',
        'group':       'TD Ameritrade',
        'status':      'coming_soon',
        'region':      'US',
        'description': 'Full service US brokerage',
        'key_label':   'API Key',
        'key_hint':    '',
        'help_steps':  [],
    },
    'fidelity': {
        'name':        'Fidelity',
        'shortname':   'Fidelity',
        'group':       'Fidelity',
        'status':      'coming_soon',
        'region':      'US',
        'description': 'Full service US brokerage',
        'key_label':   'API Key',
        'key_hint':    '',
        'help_steps':  [],
    },
    'coinbase': {
        'name':        'Coinbase',
        'shortname':   'Coinbase',
        'group':       'Coinbase',
        'status':      'coming_soon',
        'region':      'Global',
        'description': 'Crypto exchange with public API',
        'key_label':   'API Key',
        'key_hint':    '',
        'help_steps':  [],
    },
    'etoro': {
        'name':        'eToro',
        'shortname':   'eToro',
        'group':       'eToro',
        'status':      'no_api',
        'region':      'Global',
        'description': 'No public API available',
        'key_label':   '',
        'key_hint':    '',
        'help_steps':  [],
    },
    'freetrade': {
        'name':        'Freetrade',
        'shortname':   'Freetrade',
        'group':       'Freetrade',
        'status':      'no_api',
        'region':      'UK',
        'description': 'No public API available',
        'key_label':   '',
        'key_hint':    '',
        'help_steps':  [],
    },
    'hl': {
        'name':        'Hargreaves Lansdown',
        'shortname':   'HL',
        'group':       'Hargreaves Lansdown',
        'status':      'no_api',
        'region':      'UK',
        'description': 'No public API available',
        'key_label':   '',
        'key_hint':    '',
        'help_steps':  [],
    },
    'zerodha': {
        'name':        'Zerodha',
        'shortname':   'Zerodha',
        'group':       'Zerodha',
        'status':      'coming_soon',
        'region':      'India',
        'description': 'Kite Connect API',
        'key_label':   'API Key',
        'key_hint':    '',
        'help_steps':  [],
    },
    'groww': {
        'name':        'Groww',
        'shortname':   'Groww',
        'group':       'Groww',
        'status':      'coming_soon',
        'region':      'India',
        'description': 'Groww API integration',
        'key_label':   'API Key',
        'key_hint':    '',
        'help_steps':  [],
    },
}

def fetch_portfolio(broker_id, api_key):
    """Fetch portfolio using broker-specific method"""
    broker = BROKER_REGISTRY.get(broker_id)
    if not broker or broker['status'] != 'live':
        return None, None
    if 'trading212' in broker_id:
        portfolio = t212_get(broker['endpoints']['portfolio'], api_key)
        summary   = t212_get(broker['endpoints']['summary'],   api_key)
        return portfolio, summary
    return None, None

# ── SERVER-SIDE CACHE (per-user keyed) ───────────────────────────────
_candle_cache   = {}
_ind_cache      = {}
_profile_cache  = {}
_quote_cache    = {}
_earnings_cache = {}
_portfolio_cache = {}  # username -> {data, ts}

CANDLE_TTL   = 3600
IND_TTL      = 3600
PROFILE_TTL  = 86400
QUOTE_TTL    = 300
EARNINGS_TTL = 3600
PORTFOLIO_TTL = 120   # 2 minutes

def cache_valid(entry, ttl):
    return entry and (time.time() - entry.get('ts', 0)) < ttl

# ── API HELPERS ───────────────────────────────────────────────────────
def t212_get(endpoint, api_key):
    try:
        # Trading212 API accepts the key directly in Authorization header
        r = requests.get(
            f"https://live.trading212.com/api/v0/{endpoint}",
            headers={"Authorization": api_key.strip()}, timeout=15)
        if r.status_code == 200: return r.json()
        print(f"T212 {endpoint} returned {r.status_code}: {r.text[:200]}")
    except Exception as e: print(f"T212 error: {e}")
    return None

def fh(endpoint, params={}):
    finnhub_key = os.environ.get('FINNHUB_KEY', '')
    try:
        p = dict(params); p["token"] = finnhub_key
        r = requests.get(f"{FINNHUB_BASE}/{endpoint}", params=p, timeout=15)
        if r.status_code == 200: return r.json()
        print(f"FH {endpoint} returned {r.status_code}")
    except Exception as e: print(f"FH error {endpoint}: {e}")
    return {}

def clean_symbol(ticker):
    s = ticker.split("_")[0]
    s = re.sub(r"^\d+", "", s)
    s = s.rstrip("l")
    return s.upper()

def is_us(ticker): return "_US_EQ" in ticker

def get_leverage_info(ticker): return LEVERAGE_MAP.get(ticker)

def get_indicator_symbol(ticker, symbol):
    info = LEVERAGE_MAP.get(ticker)
    if info and info.get('underlying'): return info['underlying']
    return symbol

# ── INDICATOR MATH ────────────────────────────────────────────────────
def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    ag = sum(gains[:period])/period; al = sum(losses[:period])/period
    for i in range(period, len(gains)):
        ag = (ag*(period-1)+gains[i])/period
        al = (al*(period-1)+losses[i])/period
    if al == 0: return 100.0
    return round(100-(100/(1+ag/al)),2)

def calc_ema(closes, period):
    if len(closes) < period: return []
    k = 2/(period+1)
    ema = [sum(closes[:period])/period]
    for p in closes[period:]: ema.append(p*k+ema[-1]*(1-k))
    return ema

def calc_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow+signal: return None,None,None
    ef=calc_ema(closes,fast); es=calc_ema(closes,slow)
    mn=min(len(ef),len(es))
    ml=[ef[-(mn-i)]-es[-(mn-i)] for i in range(mn)]
    if len(ml)<signal: return None,None,None
    sl=calc_ema(ml,signal)
    if not sl: return None,None,None
    return round(ml[-1],4),round(sl[-1],4),round(ml[-1]-sl[-1],4)

def calc_bbands(closes, period=20, mult=2):
    if len(closes)<period: return None,None,None
    w=closes[-period:]; mean=sum(w)/period
    std=math.sqrt(sum((x-mean)**2 for x in w)/period)
    return round(mean+mult*std,2),round(mean,2),round(mean-mult*std,2)

def calc_sma(closes, period):
    if len(closes)<period: return None
    return round(sum(closes[-period:])/period,2)

def score_signal(rsi, macd, macd_sig, ma50, ma200, closes):
    score = 0
    if rsi is not None:
        if rsi<25: score+=3
        elif rsi<35: score+=2
        elif rsi<45: score+=1
        elif rsi>75: score-=3
        elif rsi>65: score-=2
        elif rsi>55: score-=1
    if macd is not None and macd_sig is not None:
        diff=macd-macd_sig
        if diff>0: score+=2 if diff>abs(macd)*0.1 else 1
        else: score-=2 if abs(diff)>abs(macd)*0.1 else 1
    if ma50 and ma200: score+=2 if ma50>ma200 else -2
    if closes and len(closes)>=10:
        recent=sum(closes[-5:])/5; prev=sum(closes[-10:-5])/5
        if prev>0:
            mom=(recent-prev)/prev*100
            if mom>2: score+=2
            elif mom>0.5: score+=1
            elif mom<-2: score-=2
            elif mom<-0.5: score-=1
    if ma50 and closes: score+=1 if closes[-1]>ma50 else -1
    if score>=5: return "Strong Bullish"
    if score>=2: return "Bullish"
    if score<=-5: return "Strong Bearish"
    if score<=-2: return "Bearish"
    return "Neutral"

# ── DATA FETCHERS ─────────────────────────────────────────────────────
def get_candles(symbol):
    if cache_valid(_candle_cache.get(symbol), CANDLE_TTL):
        return _candle_cache[symbol]
    to_ts=int(time.time()); from_ts=to_ts-(300*86400)
    data=fh("stock/candle",{"symbol":symbol,"resolution":"D","from":from_ts,"to":to_ts})
    if not data or data.get("s")!="ok":
        entry={"closes":[],"timestamps":[],"volumes":[],"ts":time.time()}
    else:
        entry={"closes":data.get("c",[]),"timestamps":data.get("t",[]),"volumes":data.get("v",[]),"ts":time.time()}
    _candle_cache[symbol]=entry
    return entry

def get_indicators(symbol):
    if cache_valid(_ind_cache.get(symbol), IND_TTL):
        return _ind_cache[symbol]
    candles=get_candles(symbol); closes=candles["closes"]
    if len(closes)<30:
        entry={"rsi":None,"macd":None,"macd_signal":None,"macd_hist":None,
               "bb_upper":None,"bb_middle":None,"bb_lower":None,
               "ma50":None,"ma200":None,"signal":"Neutral","ts":time.time()}
        _ind_cache[symbol]=entry; return entry
    rsi=calc_rsi(closes); macd,ms,mh=calc_macd(closes)
    bbu,bbm,bbl=calc_bbands(closes); ma50=calc_sma(closes,50); ma200=calc_sma(closes,200)
    signal=score_signal(rsi,macd,ms,ma50,ma200,closes)
    entry={"rsi":rsi,"macd":macd,"macd_signal":ms,"macd_hist":mh,
           "bb_upper":bbu,"bb_middle":bbm,"bb_lower":bbl,
           "ma50":ma50,"ma200":ma200,"signal":signal,
           "overbought":rsi>70 if rsi else False,"oversold":rsi<30 if rsi else False,
           "closes":closes[-60:],"timestamps":candles["timestamps"][-60:],"ts":time.time()}
    _ind_cache[symbol]=entry; return entry

def get_profile(symbol):
    if cache_valid(_profile_cache.get(symbol), PROFILE_TTL):
        return _profile_cache[symbol]
    data=fh("stock/profile2",{"symbol":symbol})
    name=data.get("name","") or NAME_MAP.get(symbol,"")
    entry={"name":name,"industry":data.get("finnhubIndustry",""),"ts":time.time()}
    _profile_cache[symbol]=entry; return entry

def get_quote(symbol):
    if cache_valid(_quote_cache.get(symbol), QUOTE_TTL):
        return _quote_cache[symbol]
    data=fh("quote",{"symbol":symbol})
    entry={**data,"ts":time.time()}
    _quote_cache[symbol]=entry; return entry

# ── BACKGROUND PRE-FETCH ──────────────────────────────────────────────
_bg_symbols = set()
_bg_lock    = threading.Lock()

def background_prefetch():
    while True:
        with _bg_lock: syms=list(_bg_symbols)
        for sym in syms:
            try:
                if not cache_valid(_ind_cache.get(sym), IND_TTL):
                    get_indicators(sym); time.sleep(1.2)
                if not cache_valid(_profile_cache.get(sym), PROFILE_TTL):
                    get_profile(sym); time.sleep(0.5)
            except Exception as e: print(f"BG error {sym}: {e}")
        time.sleep(30)

threading.Thread(target=background_prefetch, daemon=True).start()

def register_symbols(holdings):
    with _bg_lock:
        for h in holdings:
            sym=h.get("indSymbol") or h.get("symbol")
            if sym: _bg_symbols.add(sym)

# ── HOLDING BUILDER ───────────────────────────────────────────────────
def basic_holding(h, account_label):
    ticker=h.get("ticker",""); symbol=clean_symbol(ticker)
    qty=h.get("quantity",0) or 0; avg=h.get("averagePrice",0) or 0
    ppl=h.get("ppl") or 0; us=is_us(ticker)
    lev_info=get_leverage_info(ticker)
    leverage=lev_info["leverage"] if lev_info else None
    ind_sym=get_indicator_symbol(ticker,symbol)
    sector=SECTOR_MAP.get(symbol,"Other")
    if lev_info:
        und=lev_info.get("underlying","")
        if und in ["AMD","PLTR","ARM","TSM","MU"]: sector="Leveraged Tech"
        elif und in ["OIL"]: sector="Leveraged Commodity"
        elif und in ["SOXX","QQQ"]: sector="Leveraged ETF"
    is_uk_etp=ticker.endswith("l_EQ") and not us
    if is_uk_etp:
        current_price_gbp=(h.get("currentPrice",0) or 0)/100
        avg_price_gbp=avg/100
        portfolio_value=round(qty*current_price_gbp,2)
    else:
        current_price_gbp=h.get("currentPrice",0) or 0
        avg_price_gbp=avg
        portfolio_value=round((qty*avg)+ppl,2)
    h_copy=dict(h)
    if is_uk_etp:
        h_copy["currentPrice"]=round(current_price_gbp,4)
        h_copy["averagePrice"]=round(avg_price_gbp,4)
        h_copy["penceAvg"]=round(avg,2)
        h_copy["penceCurrent"]=round(h.get("currentPrice",0) or 0,2)
        if lev_info and lev_info.get("underlying"):
            uq=_quote_cache.get(lev_info["underlying"],{})
            h_copy["underlyingSymbol"]=lev_info["underlying"]
            h_copy["underlyingPrice"]=uq.get("c")
    return {**h_copy,
        "symbol":symbol,"name":_profile_cache.get(symbol,{}).get("name","") or NAME_MAP.get(symbol,""),
        "sector":sector,"portfolioValue":portfolio_value,"currency":"GBP",
        "isUkEtp":is_uk_etp,"account":account_label,"leverage":leverage,
        "indSymbol":ind_sym,"indicators":{},"signal":"Loading...","news":{},
    }

# ── USER PORTFOLIO FETCHER ────────────────────────────────────────────
def get_user_portfolio(username):
    """Fetch all portfolio data for a user based on their connected accounts"""
    user=get_user(username)
    if not user: return {"accounts":[],"summary":{}}

    accounts=user.get("accounts",[])
    all_holdings=[]
    summaries=[]

    for acct in accounts:
        if not acct.get("enabled",True): continue
        api_key=acct.get("api_key","")
        label=acct.get("label","Account")
        broker=acct.get("broker","trading212_invest")

        portfolio, summary = fetch_portfolio(broker, api_key)
        if isinstance(portfolio, list):
            enriched=[basic_holding(h, label) for h in portfolio]
            enriched.sort(key=lambda x: x.get("portfolioValue",0), reverse=True)
            register_symbols(enriched)
            all_holdings.append({"label":label,"broker":broker,"holdings":enriched,"summary":summary or {}})
            summaries.append({"label":label,"broker":broker,"summary":summary or {}})

    return {"accounts":all_holdings,"summaries":summaries}

# ── FLASK ROUTES — AUTH ───────────────────────────────────────────────
@app.route('/')
def index():
    try:
        path=os.path.join(os.path.dirname(os.path.abspath(__file__)),'index.html')
        html=open(path,'r',encoding='utf-8').read()
        return Response(html, mimetype='text/html; charset=utf-8')
    except:
        return Response('<h1>Loading...</h1><script>setTimeout(()=>location.reload(),3000)</script>',mimetype='text/html')

@app.route('/manifest.json')
def manifest():
    return Response('{"name":"TradingAssist-NTv0.1","short_name":"TradingAssist","start_url":"/","display":"standalone","background_color":"#0b0f1c","theme_color":"#0b0f1c"}',mimetype='application/json')

@app.route('/sw.js')
def sw():
    return Response("self.addEventListener('fetch',function(e){e.respondWith(fetch(e.request));});",mimetype='application/javascript')

@app.route('/api/auth/register', methods=['POST'])
def register():
    data=request.json or {}
    email   = data.get('email','').strip().lower()
    password= data.get('password','')
    name    = data.get('name','').strip()
    phone   = data.get('phone','').strip()
    address = data.get('address','').strip()
    postcode= data.get('postcode','').strip()
    country = data.get('country','').strip()
    # Validate all required fields
    if not email:    return jsonify({'error':'Email address is required'}),400
    if not name:     return jsonify({'error':'Full name is required'}),400
    if not phone:    return jsonify({'error':'Phone number is required'}),400
    if not address:  return jsonify({'error':'Street address is required'}),400
    if not postcode: return jsonify({'error':'Postcode is required'}),400
    if not country:  return jsonify({'error':'Please select your country'}),400
    if not password: return jsonify({'error':'Password is required'}),400
    if '@' not in email or '.' not in email:
        return jsonify({'error':'Please enter a valid email address'}),400
    # Password strength validation
    if len(password)<8:
        return jsonify({'error':'Password must be at least 8 characters'}),400
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?' for c in password)
    strength = sum([has_upper, has_lower, has_digit, has_special])
    if strength < 3:
        return jsonify({'error':'Password must contain at least 3 of: uppercase, lowercase, number, special character (!@#$%^&*)'}),400
    users=load_users()
    if email in users:
        return jsonify({'error':'An account with this email already exists'}),400
    users[email]={
        'username':email,
        'name':name,
        'email':email,
        'phone':phone,
        'address':address,
        'postcode':postcode,
        'country':country,
        'password':hash_password(password),
        'created':datetime.now().isoformat(),
        'role':'admin' if not users else 'user',
        'accounts':[]
    }
    save_users(users)
    session['username']=email
    return jsonify({'success':True,'username':email,'name':name,'email':email,'role':users[email]['role']})

@app.route('/api/auth/login', methods=['POST'])
def login():
    data=request.json or {}
    email   =data.get('email','').strip().lower()
    password=data.get('password','')
    if not email or not password:
        return jsonify({'error':'Email and password are required'}),400
    user=get_user(email)
    if not user or user['password']!=hash_password(password):
        return jsonify({'error':'Invalid email or password'}),401
    session['username']=email
    return jsonify({'success':True,'username':email,'name':user.get('name',email),'email':email,'role':user.get('role','user')})

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success':True})

@app.route('/api/auth/me')
def me():
    username=current_user()
    if not username: return jsonify({'authenticated':False}),401
    user=get_user(username)
    if not user: return jsonify({'authenticated':False}),401
    return jsonify({'authenticated':True,'username':username,'name':user.get('name',username),'email':user.get('email',''),'phone':user.get('phone',''),'address':user.get('address',''),'postcode':user.get('postcode',''),'country':user.get('country',''),'role':user.get('role','user'),'accounts':user.get('accounts',[])})

# ── FLASK ROUTES — ACCOUNTS ───────────────────────────────────────────
@app.route('/api/accounts', methods=['GET'])
@require_login
def get_accounts():
    user=get_user(current_user())
    return jsonify(user.get('accounts',[]))

@app.route('/api/accounts', methods=['POST'])
@require_login
def add_account():
    data=request.json or {}
    users=load_users()
    username=current_user()
    acct={
        'id':secrets.token_hex(8),
        'label':data.get('label','My Account'),
        'broker':data.get('broker','trading212_invest'),
        'api_key':data.get('api_key',''),
        'enabled':True,
        'added':datetime.now().isoformat()
    }
    users[username].setdefault('accounts',[]).append(acct)
    save_users(users)
    return jsonify({'success':True,'account':acct})

@app.route('/api/accounts/<acct_id>', methods=['PUT'])
@require_login
def update_account(acct_id):
    data=request.json or {}
    users=load_users(); username=current_user()
    for acct in users[username].get('accounts',[]):
        if acct['id']==acct_id:
            acct.update({k:v for k,v in data.items() if k in ['label','broker','api_key','enabled']})
            save_users(users)
            return jsonify({'success':True})
    return jsonify({'error':'Account not found'}),404

@app.route('/api/accounts/<acct_id>', methods=['DELETE'])
@require_login
def delete_account(acct_id):
    users=load_users(); username=current_user()
    users[username]['accounts']=[a for a in users[username].get('accounts',[]) if a['id']!=acct_id]
    save_users(users)
    return jsonify({'success':True})

@app.route('/api/brokers')
def api_brokers():
    """Return broker registry for frontend"""
    return jsonify(BROKER_REGISTRY)

@app.route('/api/accounts/test', methods=['POST'])
@require_login
def test_account():
    data    = request.json or {}
    api_key = data.get('api_key','')
    broker  = data.get('broker','trading212_invest')
    b       = BROKER_REGISTRY.get(broker)
    if not b:
        return jsonify({'success':False,'message':'Unknown broker'})
    if b['status'] != 'live':
        return jsonify({'success':False,'message':'This broker is not yet supported'})
    if 'trading212' in broker:
        result = t212_get('equity/account/summary', api_key)
        if result and isinstance(result, dict):
            val = result.get('totalValue',0)
            return jsonify({'success':True,'message':f'Connected! Account value: £{val:,.2f}'})
        # Try portfolio endpoint as fallback
        port = t212_get('equity/portfolio', api_key)
        if port and isinstance(port, list):
            return jsonify({'success':True,'message':f'Connected! {len(port)} holdings found.'})
        # Try to get our outbound IP to show user
        try:
            ip_resp = requests.get('https://api.ipify.org', timeout=5)
            our_ip = ip_resp.text.strip()
        except:
            our_ip = 'unknown'
        return jsonify({'success':False,'message':f'Connection failed. Please check: (1) API key is correct and complete, (2) Key matches account type (ISA key for ISA, Invest key for Invest), (3) In Trading212 API settings, whitelist this IP: {our_ip}'})
    return jsonify({'success':False,'message':'Broker integration coming soon'})

@app.route('/api/user/profile', methods=['POST'])
@require_login
def update_profile():
    data=request.json or {}
    users=load_users(); username=current_user()
    for field in ['name','phone','address','postcode','country']:
        if field in data:
            users[username][field]=data[field].strip()
    save_users(users)
    return jsonify({'success':True})

@app.route('/api/user/password', methods=['POST'])
@require_login
def change_password():
    data=request.json or {}
    old_pw=data.get('old_password',''); new_pw=data.get('new_password','')
    users=load_users(); username=current_user()
    if users[username]['password']!=hash_password(old_pw):
        return jsonify({'error':'Current password incorrect'}),400
    if len(new_pw)<8:
        return jsonify({'error':'Password must be at least 8 characters'}),400
    has_upper=any(c.isupper() for c in new_pw)
    has_lower=any(c.islower() for c in new_pw)
    has_digit=any(c.isdigit() for c in new_pw)
    has_special=any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?' for c in new_pw)
    if sum([has_upper,has_lower,has_digit,has_special])<3:
        return jsonify({'error':'Password must contain at least 3 of: uppercase, lowercase, number, special character'}),400
    users[username]['password']=hash_password(new_pw)
    save_users(users)
    return jsonify({'success':True})

# ── FLASK ROUTES — PORTFOLIO ──────────────────────────────────────────
@app.route('/api/portfolio')
@require_login
def api_portfolio():
    username=current_user()
    cache_key=f"portfolio_{username}"
    if cache_valid(_portfolio_cache.get(cache_key), PORTFOLIO_TTL):
        return jsonify(_portfolio_cache[cache_key]['data'])
    data=get_user_portfolio(username)
    _portfolio_cache[cache_key]={'data':data,'ts':time.time()}
    return jsonify(data)

@app.route('/api/summary')
@require_login
def api_summary():
    username=current_user()
    cache_key=f"portfolio_{username}"
    if cache_valid(_portfolio_cache.get(cache_key), PORTFOLIO_TTL):
        portfolio=_portfolio_cache[cache_key]['data']
    else:
        portfolio=get_user_portfolio(username)
        _portfolio_cache[cache_key]={'data':portfolio,'ts':time.time()}
    # Build combined summary
    total_value=0; total_cash=0; total_unrealised=0; total_realised=0
    for acct in portfolio.get('summaries',[]):
        s=acct.get('summary',{})
        total_value+=s.get('totalValue',0) or 0
        total_cash+=(s.get('cash',{}) or {}).get('availableToTrade',0) or 0
        inv=(s.get('investments',{}) or {})
        total_unrealised+=inv.get('unrealizedProfitLoss',0) or 0
        total_realised+=inv.get('realizedProfitLoss',0) or 0
    return jsonify({
        'accounts':portfolio.get('summaries',[]),
        'combined':{'totalValue':round(total_value,2),'availableCash':round(total_cash,2),
                    'unrealizedPnL':round(total_unrealised,2),'realizedPnL':round(total_realised,2)}
    })

@app.route('/api/indicators/<symbol>')
@require_login
def api_indicators(symbol):
    for ticker,info in LEVERAGE_MAP.items():
        if clean_symbol(ticker)==symbol and info.get("underlying"):
            return jsonify(get_indicators(info["underlying"]))
    return jsonify(get_indicators(symbol))

@app.route('/api/stock/<symbol>')
@require_login
def api_stock_detail(symbol):
    ind=get_indicators(symbol); profile=get_profile(symbol); quote=get_quote(symbol)
    today=datetime.now().strftime("%Y-%m-%d")
    month_ago=(datetime.now()-timedelta(days=30)).strftime("%Y-%m-%d")
    news=fh("company-news",{"symbol":symbol,"from":month_ago,"to":today})
    return jsonify({"symbol":symbol,"indicators":ind,"profile":profile,"quote":quote,"news":news[:20] if isinstance(news,list) else []})

@app.route('/api/profile/<symbol>')
@require_login
def api_profile(symbol):
    p=get_profile(symbol)
    return jsonify({"name":p.get("name",""),"industry":p.get("industry","")})

@app.route('/api/news/<symbol>')
@require_login
def api_news(symbol):
    today=datetime.now().strftime("%Y-%m-%d")
    month_ago=(datetime.now()-timedelta(days=30)).strftime("%Y-%m-%d")
    news=fh("company-news",{"symbol":symbol,"from":month_ago,"to":today})
    return jsonify(news[:20] if isinstance(news,list) else [])

@app.route('/api/news/market/<category>')
@require_login
def api_market_news(category):
    valid=["general","forex","crypto","merger"]
    cat=category if category in valid else "general"
    news=fh("news",{"category":cat})
    return jsonify(news[:20] if isinstance(news,list) else [])

@app.route('/api/quote/<symbol>')
@require_login
def api_quote(symbol):
    return jsonify(get_quote(symbol))

@app.route('/api/earnings')
@require_login
def api_earnings():
    if cache_valid(_earnings_cache.get("entry"), EARNINGS_TTL):
        return jsonify(_earnings_cache["entry"]["data"])
    today=datetime.now().strftime("%Y-%m-%d")
    future=(datetime.now()+timedelta(days=90)).strftime("%Y-%m-%d")
    past=(datetime.now()-timedelta(days=90)).strftime("%Y-%m-%d")
    import concurrent.futures
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            f_up=ex.submit(lambda: fh("calendar/earnings",{"from":today,"to":future}) or {})
            f_past=ex.submit(lambda: fh("calendar/earnings",{"from":past,"to":today}) or {})
            upcoming_data=f_up.result(timeout=20); past_data=f_past.result(timeout=20)
    except Exception as e:
        print(f"Earnings fetch error: {e}"); upcoming_data={}; past_data={}
    owned=_bg_symbols
    def get_eps_verdict(a,e):
        if a is None or e is None: return None
        d=((a-e)/abs(e))*100 if e!=0 else 0
        return "Exceeded" if d>5 else "Missed" if d<-5 else "Met Expectations"
    def get_rev_verdict(a,e):
        if a is None or e is None: return None
        d=((a-e)/abs(e))*100 if e!=0 else 0
        return "Exceeded" if d>2 else "Missed" if d<-2 else "Met Expectations"
    def enrich(item):
        sym=item.get("symbol",""); p=_profile_cache.get(sym,{})
        name=p.get("name","") or NAME_MAP.get(sym,"")
        q=_quote_cache.get(sym,{}); mcap=MARKET_CAP.get(sym,0)
        in_p=sym in owned; price=q.get("c") or 0
        if price>0 and price<5 and not in_p: return None
        ea=item.get("epsActual"); ee=item.get("epsEstimate")
        ra=item.get("revenueActual"); re_=item.get("revenueEstimate")
        return {**item,"companyName":name,"currentPrice":price if price else None,
                "priceChange":q.get("d"),"priceChangePct":q.get("dp"),"inPortfolio":in_p,"marketCap":mcap,
                "epsVerdict":get_eps_verdict(ea,ee),"revVerdict":get_rev_verdict(ra,re_),
                "epsSurprisePct":round(((ea-ee)/abs(ee))*100,1) if ea is not None and ee else None,
                "revSurprisePct":round(((ra-re_)/abs(re_))*100,1) if ra is not None and re_ else None}
    def sort_upcoming(items):
        enriched=[e for e in [enrich(i) for i in items] if e is not None]
        portfolio=sorted([e for e in enriched if e["inPortfolio"]],key=lambda x:(x.get("date",""),-x["marketCap"]))
        others=sorted([e for e in enriched if not e["inPortfolio"]],key=lambda x:(x.get("date",""),-x["marketCap"]))
        return (portfolio+others)[:50]
    def sort_past(items):
        enriched=[e for e in [enrich(i) for i in items] if e is not None]
        portfolio=sorted([e for e in enriched if e["inPortfolio"]],key=lambda x: x.get("date",""),reverse=True)
        others=sorted([e for e in enriched if not e["inPortfolio"]],key=lambda x: x.get("date",""),reverse=True)
        return (portfolio+others)[:50]
    result={"upcoming":sort_upcoming(upcoming_data.get("earningsCalendar") or []),
            "past":sort_past(past_data.get("earningsCalendar") or [])}
    _earnings_cache["entry"]={"data":result,"ts":time.time()}
    return jsonify(result)

@app.route('/api/suggestions')
@require_login
def api_suggestions():
    return jsonify({
        "1day":[
            {"ticker":"NVDA","company":"NVIDIA Corp","sector":"AI","risk":"High","reason":"Strong AI chip momentum. MACD bullish crossover.","perf":{"1d":3.2,"2d":5.1,"1w":-1.2,"1m":12.4},"targets":{"1d":3,"2d":5,"1w":8,"1m":18,"1y":55}},
            {"ticker":"AMD","company":"Advanced Micro Devices","sector":"Technology","risk":"Medium","reason":"Data centre GPU demand rising. RSI recovered.","perf":{"1d":2.1,"2d":3.8,"1w":-2.1,"1m":8.3},"targets":{"1d":2,"2d":4,"1w":7,"1m":15,"1y":40}},
            {"ticker":"TSLA","company":"Tesla Inc","sector":"Technology","risk":"Very High","reason":"Bouncing off key support. Robotaxi catalyst.","perf":{"1d":4.5,"2d":6.2,"1w":2.1,"1m":-5.3},"targets":{"1d":4,"2d":7,"1w":12,"1m":25,"1y":70}},
            {"ticker":"META","company":"Meta Platforms","sector":"Technology","risk":"Medium","reason":"AI ad revenue acceleration. Strong FCF.","perf":{"1d":1.8,"2d":2.9,"1w":4.2,"1m":15.1},"targets":{"1d":2,"2d":3,"1w":6,"1m":14,"1y":45}},
            {"ticker":"GOOGL","company":"Alphabet Inc","sector":"Technology","risk":"Low","reason":"Search AI integration driving revenue.","perf":{"1d":1.2,"2d":2.1,"1w":3.5,"1m":9.8},"targets":{"1d":1.5,"2d":2.5,"1w":5,"1m":12,"1y":38}},
            {"ticker":"MSTR","company":"MicroStrategy Inc","sector":"Crypto","risk":"Very High","reason":"Bitcoin proxy play. Strong institutional interest.","perf":{"1d":5.2,"2d":8.1,"1w":3.2,"1m":-8.4},"targets":{"1d":5,"2d":8,"1w":15,"1m":30,"1y":120}},
            {"ticker":"COIN","company":"Coinbase Global","sector":"Crypto","risk":"Very High","reason":"Crypto market recovery. Regulatory clarity improving.","perf":{"1d":3.8,"2d":5.5,"1w":1.2,"1m":-12.3},"targets":{"1d":4,"2d":6,"1w":10,"1m":22,"1y":80}},
            {"ticker":"SMCI","company":"Super Micro Computer","sector":"Technology","risk":"High","reason":"AI server demand surge. NVIDIA partnership.","perf":{"1d":4.1,"2d":6.8,"1w":-3.2,"1m":-18.5},"targets":{"1d":4,"2d":7,"1w":12,"1m":25,"1y":90}},
            {"ticker":"PLTR","company":"Palantir Technologies","sector":"AI","risk":"High","reason":"Government AI contracts expanding rapidly.","perf":{"1d":2.5,"2d":4.1,"1w":6.8,"1m":22.3},"targets":{"1d":2,"2d":4,"1w":8,"1m":20,"1y":65}},
            {"ticker":"HOOD","company":"Robinhood Markets","sector":"Finance","risk":"High","reason":"Crypto trading volumes surging.","perf":{"1d":3.1,"2d":4.9,"1w":2.3,"1m":8.7},"targets":{"1d":3,"2d":5,"1w":9,"1m":18,"1y":55}},
        ],
        "1week":[
            {"ticker":"PLTR","company":"Palantir Technologies","sector":"AI","risk":"High","reason":"Bullish wedge breakout on weekly chart.","perf":{"1d":2.5,"2d":4.1,"1w":6.8,"1m":22.3},"targets":{"1d":1,"2d":2,"1w":8,"1m":20,"1y":60}},
            {"ticker":"SOFI","company":"SoFi Technologies","sector":"Finance","risk":"Medium","reason":"Rate cut expectations. RSI oversold.","perf":{"1d":0.8,"2d":1.5,"1w":-3.2,"1m":-8.1},"targets":{"1d":1,"2d":2,"1w":6,"1m":16,"1y":45}},
            {"ticker":"CRWD","company":"CrowdStrike Holdings","sector":"Technology","risk":"Medium","reason":"Cybersecurity spend accelerating.","perf":{"1d":1.2,"2d":2.3,"1w":4.5,"1m":11.2},"targets":{"1d":1,"2d":2,"1w":5,"1m":12,"1y":50}},
            {"ticker":"SNOW","company":"Snowflake Inc","sector":"Technology","risk":"High","reason":"AI workloads driving usage.","perf":{"1d":0.9,"2d":1.8,"1w":-1.5,"1m":5.3},"targets":{"1d":1,"2d":2,"1w":7,"1m":18,"1y":55}},
            {"ticker":"SHOP","company":"Shopify Inc","sector":"Technology","risk":"Medium","reason":"E-commerce AI tools gaining traction.","perf":{"1d":1.1,"2d":2.2,"1w":3.8,"1m":14.6},"targets":{"1d":1,"2d":2,"1w":6,"1m":15,"1y":48}},
            {"ticker":"SQ","company":"Block Inc","sector":"Finance","risk":"High","reason":"Cash App growing. Bitcoin integration.","perf":{"1d":1.5,"2d":2.8,"1w":-0.8,"1m":-4.2},"targets":{"1d":1,"2d":2,"1w":7,"1m":17,"1y":52}},
            {"ticker":"UBER","company":"Uber Technologies","sector":"Technology","risk":"Low","reason":"Autonomous vehicle partnerships.","perf":{"1d":0.7,"2d":1.4,"1w":2.9,"1m":8.4},"targets":{"1d":1,"2d":1.5,"1w":5,"1m":11,"1y":35}},
            {"ticker":"RBLX","company":"Roblox Corp","sector":"Technology","risk":"High","reason":"Metaverse monetisation improving.","perf":{"1d":1.8,"2d":3.1,"1w":-2.4,"1m":6.8},"targets":{"1d":2,"2d":3,"1w":8,"1m":18,"1y":60}},
            {"ticker":"HIMS","company":"Hims & Hers Health","sector":"Biotech","risk":"High","reason":"GLP-1 compounding opportunity.","perf":{"1d":2.2,"2d":3.8,"1w":5.1,"1m":18.9},"targets":{"1d":2,"2d":4,"1w":9,"1m":22,"1y":75}},
            {"ticker":"RIVN","company":"Rivian Automotive","sector":"Technology","risk":"Very High","reason":"VW partnership funding secured.","perf":{"1d":2.8,"2d":4.5,"1w":-1.8,"1m":-9.3},"targets":{"1d":3,"2d":5,"1w":10,"1m":20,"1y":80}},
        ],
        "1month":[
            {"ticker":"AMZN","company":"Amazon","sector":"Technology","risk":"Low","reason":"AWS AI growth accelerating 40%+ YoY.","perf":{"1d":0.8,"2d":1.5,"1w":3.2,"1m":9.8},"targets":{"1d":0.5,"2d":1,"1w":3,"1m":10,"1y":35}},
            {"ticker":"MSFT","company":"Microsoft Corp","sector":"AI","risk":"Low","reason":"Copilot adoption. Azure AI growing 50%+ YoY.","perf":{"1d":0.6,"2d":1.2,"1w":2.8,"1m":7.5},"targets":{"1d":0.5,"2d":1,"1w":3,"1m":9,"1y":28}},
            {"ticker":"LLY","company":"Eli Lilly","sector":"Biotech","risk":"Low","reason":"GLP-1 drugs dominating market.","perf":{"1d":0.4,"2d":0.9,"1w":2.1,"1m":6.3},"targets":{"1d":0.3,"2d":0.8,"1w":3,"1m":9,"1y":38}},
            {"ticker":"AAPL","company":"Apple Inc","sector":"Technology","risk":"Low","reason":"AI iPhone supercycle building.","perf":{"1d":0.5,"2d":1.0,"1w":2.5,"1m":5.8},"targets":{"1d":0.5,"2d":1,"1w":3,"1m":8,"1y":25}},
            {"ticker":"V","company":"Visa Inc","sector":"Finance","risk":"Low","reason":"Global payment volumes growing.","perf":{"1d":0.3,"2d":0.8,"1w":1.8,"1m":4.2},"targets":{"1d":0.3,"2d":0.7,"1w":2,"1m":7,"1y":22}},
            {"ticker":"JPM","company":"JPMorgan Chase","sector":"Finance","risk":"Low","reason":"Rate environment favourable. Strong capital.","perf":{"1d":0.4,"2d":0.9,"1w":2.2,"1m":5.1},"targets":{"1d":0.4,"2d":0.8,"1w":2.5,"1m":8,"1y":24}},
            {"ticker":"ABBV","company":"AbbVie Inc","sector":"Biotech","risk":"Low","reason":"Skyrizi and Rinvoq growing fast.","perf":{"1d":0.4,"2d":0.8,"1w":2.0,"1m":5.5},"targets":{"1d":0.3,"2d":0.7,"1w":2.5,"1m":8,"1y":22}},
            {"ticker":"COST","company":"Costco Wholesale","sector":"Consumer","risk":"Low","reason":"Membership renewal rates at record highs.","perf":{"1d":0.3,"2d":0.7,"1w":1.8,"1m":4.9},"targets":{"1d":0.3,"2d":0.6,"1w":2,"1m":6,"1y":20}},
            {"ticker":"UNH","company":"UnitedHealth Group","sector":"Biotech","risk":"Low","reason":"Healthcare demand inelastic.","perf":{"1d":0.3,"2d":0.7,"1w":1.9,"1m":4.5},"targets":{"1d":0.3,"2d":0.7,"1w":2,"1m":7,"1y":20}},
            {"ticker":"AVGO","company":"Broadcom Inc","sector":"Technology","risk":"Low","reason":"AI custom chip demand surging.","perf":{"1d":0.5,"2d":1.0,"1w":2.3,"1m":6.1},"targets":{"1d":0.5,"2d":1,"1w":3,"1m":9,"1y":30}},
        ],
        "1year":[
            {"ticker":"IONQ","company":"IonQ Inc","sector":"Quantum","risk":"Very High","reason":"Quantum computing leader. Government contracts.","perf":{"1d":2.1,"2d":3.5,"1w":8.2,"1m":25.4},"targets":{"1d":2,"2d":4,"1w":8,"1m":20,"1y":150}},
            {"ticker":"RXRX","company":"Recursion Pharma","sector":"Biotech","risk":"High","reason":"AI drug discovery pioneer. NVIDIA partnership.","perf":{"1d":1.5,"2d":2.8,"1w":5.1,"1m":12.3},"targets":{"1d":1,"2d":2,"1w":6,"1m":18,"1y":80}},
            {"ticker":"ALAB","company":"Astera Labs","sector":"Technology","risk":"High","reason":"AI data centre connectivity chips.","perf":{"1d":1.8,"2d":3.2,"1w":6.5,"1m":18.7},"targets":{"1d":1,"2d":2,"1w":5,"1m":15,"1y":70}},
            {"ticker":"RKLB","company":"Rocket Lab USA","sector":"Technology","risk":"Very High","reason":"Small satellite launch market leader.","perf":{"1d":2.5,"2d":4.2,"1w":9.1,"1m":28.5},"targets":{"1d":2,"2d":4,"1w":10,"1m":25,"1y":120}},
            {"ticker":"DDOG","company":"Datadog Inc","sector":"Technology","risk":"Medium","reason":"Observability platform essential for AI.","perf":{"1d":0.9,"2d":1.8,"1w":3.5,"1m":10.2},"targets":{"1d":1,"2d":2,"1w":5,"1m":14,"1y":55}},
            {"ticker":"NET","company":"Cloudflare Inc","sector":"Technology","risk":"Medium","reason":"Zero trust security leader.","perf":{"1d":1.1,"2d":2.1,"1w":4.2,"1m":12.8},"targets":{"1d":1,"2d":2,"1w":5,"1m":15,"1y":60}},
            {"ticker":"PATH","company":"UiPath Inc","sector":"AI","risk":"High","reason":"Enterprise automation with AI.","perf":{"1d":1.3,"2d":2.5,"1w":4.8,"1m":14.1},"targets":{"1d":1,"2d":2,"1w":6,"1m":16,"1y":65}},
            {"ticker":"LUNR","company":"Intuitive Machines","sector":"Technology","risk":"Very High","reason":"NASA lunar contracts pioneer.","perf":{"1d":3.5,"2d":5.8,"1w":12.4,"1m":38.2},"targets":{"1d":3,"2d":5,"1w":12,"1m":30,"1y":200}},
            {"ticker":"ACHR","company":"Archer Aviation","sector":"Technology","risk":"Very High","reason":"eVTOL air taxi leader. United Airlines partnership.","perf":{"1d":2.8,"2d":4.5,"1w":9.8,"1m":32.1},"targets":{"1d":3,"2d":5,"1w":12,"1m":28,"1y":180}},
            {"ticker":"ARKG","company":"ARK Genomic Revolution ETF","sector":"Biotech","risk":"High","reason":"Genomic revolution multi-year theme.","perf":{"1d":0.8,"2d":1.5,"1w":3.2,"1m":8.9},"targets":{"1d":1,"2d":2,"1w":5,"1m":15,"1y":65}},
        ],
    })

@app.route('/api/cache/status')
@require_login
def api_cache_status():
    user_count = len(load_users())
    return jsonify({
        "candles":len(_candle_cache),"indicators":len(_ind_cache),
        "profiles":len(_profile_cache),"quotes":len(_quote_cache),
        "bg_symbols":len(_bg_symbols),
        "users":user_count,
        "db_connected":bool(DATABASE_URL)
    })

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port,debug=False)
