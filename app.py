from flask import Flask, jsonify, Response, request, session, redirect
from flask_cors import CORS
import requests, os, time, re, math, threading, json, hashlib, secrets
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config.update(
    SESSION_COOKIE_SECURE=False,      # Set True if HTTPS only - Render handles HTTPS at proxy level
    SESSION_COOKIE_HTTPONLY=True,     # Prevent JS access to cookie
    SESSION_COOKIE_SAMESITE='Lax',   # Allow cross-origin redirects
    SESSION_COOKIE_NAME='ta_session', # Custom name to avoid conflicts
    PERMANENT_SESSION_LIFETIME=86400 * 30,  # 30 days
)

# ── PATHS ─────────────────────────────────────────────────────────────
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'users.json')

# ── USER STORAGE (Supabase REST API + file fallback) ─────────────────
import base64

# Supabase REST API config
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')      # e.g. https://xxxx.supabase.co
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')      # service_role or anon key

def supa_headers():
    return {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation'
    }

def init_db():
    """Verify Supabase connection"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Supabase not configured - using file storage")
        return
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/users?limit=1",
            headers=supa_headers(), timeout=10)
        if r.status_code in [200, 206]:
            print("Supabase connected successfully!")
        elif r.status_code == 404:
            print("Supabase connected but users table not found - please create it")
        else:
            print(f"Supabase connection check: {r.status_code} {r.text[:100]}")
    except Exception as e:
        print(f"Supabase init error: {e}")

def load_users():
    """Load all users from Supabase REST API or file fallback"""
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/users?select=email,data",
                headers=supa_headers(), timeout=10)
            if r.status_code == 200:
                rows = r.json()
                return {row['email']: json.loads(row['data']) for row in rows}
            print(f"Supabase load error: {r.status_code} {r.text[:100]}")
        except Exception as e:
            print(f"DB load error: {e}")
    # File fallback
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE) as f: return json.load(f)
        except: pass
    return {}

def save_users(users):
    """Save all users via Supabase REST API or file fallback"""
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            for email, data in users.items():
                r = requests.post(
                    f"{SUPABASE_URL}/rest/v1/users",
                    headers={**supa_headers(), 'Prefer': 'resolution=merge-duplicates'},
                    json={'email': email, 'data': json.dumps(data)},
                    timeout=10)
                if r.status_code not in [200, 201]:
                    print(f"Supabase save error: {r.status_code} {r.text[:100]}")
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
    """Get single user via Supabase REST API or file fallback"""
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/users?email=eq.{username}&select=data",
                headers=supa_headers(), timeout=10)
            if r.status_code == 200:
                rows = r.json()
                return json.loads(rows[0]['data']) if rows else None
            print(f"Supabase get_user error: {r.status_code}")
        except Exception as e:
            print(f"DB get_user error: {e}")
    # File fallback
    return load_users().get(username)

# Initialise on startup
init_db()

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ── EMAIL ─────────────────────────────────────────────────────────────
import smtplib, random, string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

GMAIL_USER = os.environ.get('GMAIL_USER', '')
GMAIL_PASS = os.environ.get('GMAIL_PASS', '')
_reset_tokens = {}

def send_email(to_email, subject, html_body):
    if not GMAIL_USER or not GMAIL_PASS:
        print(f"Email not configured - skipping send to {to_email}")
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = f"TradingAssist Support <{GMAIL_USER}>"
        msg['To']      = to_email
        msg.attach(MIMEText(html_body, 'html'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, to_email, msg.as_string())
        print(f"Email sent to {to_email}")
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

def generate_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=32))

def get_app_url():
    return os.environ.get('APP_URL', 'https://tradingassist-ntv01.onrender.com')

def reset_email_html(name, reset_url):
    return (
        '<div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px;background:#111827;color:#dce6f0;border-radius:12px">'
        '<h2 style="color:#3b82f6;margin-bottom:8px">TradingAssist</h2>'
        '<p>Hi ' + name + ',</p>'
        '<p>You requested a password reset. Click below to set a new password:</p>'
        '<a href="' + reset_url + '" style="display:inline-block;background:#3b82f6;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;margin:16px 0;font-size:14px">Reset My Password</a>'
        '<p style="color:#7a96b8;font-size:12px;margin-top:16px">This link expires in 1 hour. If you did not request this, you can safely ignore this email.</p>'
        '<hr style="border:none;border-top:1px solid #1e2d45;margin:20px 0">'
        '<p style="color:#3d5470;font-size:11px">TradingAssist NT v0.1 &middot; Need help? <a href="mailto:tradingassist.support@gmail.com" style="color:#3b82f6">tradingassist.support@gmail.com</a></p>'
        '</div>'
    )

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

FINNHUB_BASE   = 'https://finnhub.io/api/v1'
TWELVE_BASE    = 'https://api.twelvedata.com'
TWELVE_KEY     = os.environ.get('TWELVE_DATA_KEY', '')

# ── NEW API KEYS (Phase 2 - all sources) ──────────────────────────────
ALPHA_KEY      = os.environ.get('ALPHA_VANTAGE_KEY', '')
POLYGON_KEY    = os.environ.get('POLYGON_KEY', '')
OPENEX_KEY     = os.environ.get('OPEN_EXCHANGE_KEY', '')
MARKETAUX_KEY  = os.environ.get('MARKETAUX_KEY', '')
FMP_KEY        = os.environ.get('FMP_KEY', '')
NEWS_API_KEY   = os.environ.get('NEWS_API_KEY', '')
FRED_KEY       = os.environ.get('FRED_KEY', '')

# ── API BASE URLS ─────────────────────────────────────────────────────
YAHOO_BASE     = 'https://query1.finance.yahoo.com'
YAHOO_BASE2    = 'https://query2.finance.yahoo.com'
ALPHA_BASE     = 'https://www.alphavantage.co/query'
POLYGON_BASE   = 'https://api.polygon.io'
OPENEX_BASE    = 'https://openexchangerates.org/api'
MARKETAUX_BASE = 'https://api.marketaux.com/v1'
FMP_BASE       = 'https://financialmodelingprep.com/api/v3'
NEWS_BASE      = 'https://newsapi.org/v2'
FRED_BASE      = 'https://api.stlouisfed.org/fred'
NASDAQ_BASE    = 'https://api.nasdaq.com/api'
STOOQ_BASE     = 'https://stooq.com/q/d/l'
STOCKTWITS_BASE= 'https://api.stocktwits.com/api/2'
TIPRANKS_BASE  = 'https://www.tipranks.com/api/stocks'
SEC_BASE       = 'https://data.sec.gov'

# ── MAPS & CONSTANTS ──────────────────────────────────────────────────
SECTOR_MAP = {
    'INTC':'Technology','AVGO':'Technology','QCOM':'Technology','ASML':'Technology',
    'APP':'Technology','NVDA':'Technology','AMD':'Technology','MSFT':'Technology',
    'AAPL':'Technology','SOUN':'AI','QUBT':'AI','DMYI':'SPAC','PLTR':'AI',
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
    'DMYI':'dMY Technology Group',
    'OAC':'Oaktree Acquisition Corp',
    'LAA3':'LAA3 ETF',
    'LLL':'L3Harris Technologies',
    'UBR':'UBS MSCI Brazil ETF',
    'MAG5':'Magnificent 7 ETF',
    'EQQQ':'Invesco EQQQ Nasdaq-100',
    'PONY':'Pony AI Inc',
    'XPOA':'XPO Inc',
    'KCAC':'Kensington Capital Acquisition',
    'PLT':'Palantir Technologies ETF',
    'IPOE':'Social Capital Hedosophia',
    'ALCC1':'AleAnna Energy',
    'SNII':'Spinnaker Nations II',
    'GIG':'GigCapital4 Inc',
    'HOD':'WisdomTree Crude Oil 2x ETP',
    'BULL':'Direxion Daily S&P Bull 3x',
    'ASST':'Asset Entities Inc',
    'APLD':'Applied Digital Corp',
    'IONQ':'IonQ Inc',
    'RXRX':'Recursion Pharmaceuticals',
    'ALAB':'Astera Labs Inc',
    'RKLB':'Rocket Lab USA',
    'LUNR':'Intuitive Machines',
    'ACHR':'Archer Aviation',
}

LEVERAGE_MAP = {
    '3AMDl_EQ': {'leverage':'3x','underlying':'AMD', 'name':'3x AMD ETP'},
    '2MUl_EQ':  {'leverage':'2x','underlying':'MU',  'name':'2x Micron ETP'},
    '3PLTl_EQ': {'leverage':'3x','underlying':'PLTR','name':'3x Palantir ETP'},
    '3TSMl_EQ': {'leverage':'3x','underlying':'TSM', 'name':'3x Taiwan Semi ETP'},
    '3HODl_EQ': {'leverage':'2x','underlying':'OIL', 'name':'2x Crude Oil ETP'},
    'SOXLl_EQ': {'leverage':'3x','underlying':'SOXX','name':'3x Semiconductor ETF'},
    'ARM3l_EQ': {'leverage':'3x','underlying':'ARM', 'name':'3x Arm Holdings ETP'},
    'GOOl_EQ':  {'leverage':'3x','underlying':'GOOGL','name':'3x Alphabet ETP'},
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
            'Go to Settings → API (Beta) in the left menu',
            'Click Generate API key',
            'Select your ISA account',
            'Under IP Whitelist, add your app server IP (shown below)',
            'Copy the API Key ID and Secret Key',
            'Paste both into the fields in TradingAssist',
        ],
        'help_note': 'You must whitelist the server IP address shown in the Test & Connect error message. Each account (ISA, Invest) has a separate API key.',
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
            'Go to Settings → API (Beta) in the left menu',
            'Click Generate API key',
            'Select your Invest account',
            'Under IP Whitelist, add your app server IP (shown below)',
            'Copy the API Key ID and Secret Key',
            'Paste both into the fields in TradingAssist',
        ],
        'help_note': 'You must whitelist the server IP address shown in the Test & Connect error message. Each account (ISA, Invest) has a separate API key.',
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
            'Go to Settings → API (Beta)',
            'Click Generate API key',
            'Under IP Whitelist, add your app server IP (shown below)',
            'Copy the API Key ID and Secret Key',
            'Paste both into the fields in TradingAssist',
        ],
        'help_note': 'US accounts have a single account (no ISA/Invest split). You must whitelist the server IP shown in the Test & Connect error message.',
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

# Account currency mapping
ACCOUNT_CURRENCY = {
    'trading212_isa':    {'currency':'GBP','symbol':'£','region':'UK'},
    'trading212_invest': {'currency':'GBP','symbol':'£','region':'UK'},
    'trading212_us':     {'currency':'USD','symbol':'$','region':'US'},
}

def get_account_currency(broker_id, user_country='GB'):
    """Get account settlement currency based on broker and user country"""
    if broker_id in ACCOUNT_CURRENCY:
        return ACCOUNT_CURRENCY[broker_id]
    # Default based on user country
    if user_country == 'US':   return {'currency':'USD','symbol':'$','region':'US'}
    if user_country == 'IN':   return {'currency':'INR','symbol':'₹','region':'IN'}
    if user_country == 'EU' or user_country in ['DE','FR','ES','IT','NL','SE','NO','DK','CH']:
        return {'currency':'EUR','symbol':'€','region':'EU'}
    return {'currency':'GBP','symbol':'£','region':'UK'}

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
_market_cache    = {}  # market data cache
_news_cache      = {}  # symbol -> list of news items

CANDLE_TTL   = 3600
IND_TTL      = 3600
PROFILE_TTL  = 86400
QUOTE_TTL    = 300
EARNINGS_TTL = 1800   # 30 mins
MARKET_TTL   = 600    # 10 minutes - avoid Twelve Data rate limits
PORTFOLIO_TTL = 120   # 2 minutes

def cache_valid(entry, ttl):
    return entry and (time.time() - entry.get('ts', 0)) < ttl

# ── API HELPERS ───────────────────────────────────────────────────────
def t212_get(endpoint, api_key):
    try:
        import base64
        key = api_key.strip()
        # Trading212 uses Basic auth: base64(KeyID:SecretKey)
        # api_key stored as "KeyID:SecretKey" - encode the whole thing
        if ':' not in key:
            # Legacy: just key with empty secret
            credentials = f"{key}:"
        else:
            # Already in KeyID:SecretKey format
            credentials = key
        encoded = base64.b64encode(credentials.encode()).decode()
        auth_header = f"Basic {encoded}"
        r = requests.get(
            f"https://live.trading212.com/api/v0/{endpoint}",
            headers={"Authorization": auth_header}, timeout=15)
        if r.status_code == 200: return r.json()
        print(f"T212 {endpoint} returned {r.status_code}: {r.text[:200]}")
    except Exception as e: print(f"T212 error: {e}")
    return None

def fh(endpoint, params={}):
    finnhub_key = os.environ.get('FINNHUB_KEY', '')
    if not finnhub_key: return {}
    try:
        p = dict(params); p["token"] = finnhub_key
        r = requests.get(f"{FINNHUB_BASE}/{endpoint}", params=p, timeout=15)
        if r.status_code == 200: return r.json()
        if r.status_code == 429:
            time.sleep(1)  # rate limit - back off
        elif r.status_code != 403:  # don't log 403 for premium endpoints
            print(f"FH {endpoint} returned {r.status_code}")
    except Exception as e: print(f"FH error {endpoint}: {e}")
    return {}

def td(endpoint, params={}):
    """Twelve Data API helper - fallback for Finnhub"""
    if not TWELVE_KEY: return {}
    try:
        p = dict(params); p["apikey"] = TWELVE_KEY
        r = requests.get(f"{TWELVE_BASE}/{endpoint}", params=p, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "error":
                print(f"TD {endpoint} error: {data.get('message','')}")
                return {}
            return data
        print(f"TD {endpoint} returned {r.status_code}")
    except Exception as e: print(f"TD error {endpoint}: {e}")
    return {}

# ── YAHOO FINANCE v8 (Primary backbone - no key, unlimited) ──────────

def yf_quote(symbol):
    """Yahoo Finance quote - fast, no key, very reliable"""
    try:
        url = f"{YAHOO_BASE}/v8/finance/chart/{symbol}"
        r = requests.get(url, params={'interval':'1d','range':'1d'},
                        headers={'User-Agent':'Mozilla/5.0'}, timeout=10)
        if r.status_code != 200: return {}
        data = r.json()
        result = data.get('chart',{}).get('result',[])
        if not result: return {}
        meta = result[0].get('meta',{})
        price = meta.get('regularMarketPrice',0) or meta.get('previousClose',0)
        prev  = meta.get('previousClose', price)
        chg   = price - prev
        chgp  = (chg/prev*100) if prev else 0
        return {'c': round(price,4), 'd': round(chg,4), 'dp': round(chgp,2),
                'pc': round(prev,4), 'sym': symbol,
                'currency': meta.get('currency','USD'),
                'name': meta.get('shortName','')}
    except Exception as e:
        print(f"YF quote error {symbol}: {e}")
        return {}

def yf_candles(symbol, period='6mo', interval='1d'):
    """Yahoo Finance candles - replaces Finnhub for indicators"""
    try:
        url = f"{YAHOO_BASE}/v8/finance/chart/{symbol}"
        r = requests.get(url, params={'interval':interval,'range':period},
                        headers={'User-Agent':'Mozilla/5.0'}, timeout=8)
        if r.status_code != 200: return {}
        data = r.json()
        result = data.get('chart',{}).get('result',[])
        if not result: return {}
        quotes = result[0].get('indicators',{}).get('quote',[{}])[0]
        ts = result[0].get('timestamp',[])
        closes = [c for c in (quotes.get('close') or []) if c is not None]
        highs  = [h for h in (quotes.get('high') or [])  if h is not None]
        lows   = [l for l in (quotes.get('low') or [])   if l is not None]
        vols   = [v for v in (quotes.get('volume') or []) if v is not None]
        return {'closes':closes,'highs':highs,'lows':lows,'volumes':vols,
                'timestamps':ts,'count':len(closes)}
    except Exception as e:
        print(f"YF candles error {symbol}: {e}")
        return {}

def yf_news(symbol, count=10):
    """Yahoo Finance news per ticker"""
    try:
        url = f"{YAHOO_BASE}/v1/finance/search"
        r = requests.get(url, params={'q':symbol,'newsCount':count,'quotesCount':0},
                        headers={'User-Agent':'Mozilla/5.0'}, timeout=10)
        if r.status_code != 200: return []
        items = r.json().get('news',[])
        news = []
        for item in items[:count]:
            news.append({
                'headline': item.get('title',''),
                'summary':  item.get('summary',''),
                'url':      item.get('link',''),
                'source':   item.get('publisher','Yahoo Finance'),
                'datetime': item.get('providerPublishTime', int(time.time())),
                'sentiment':'Neutral',
            })
        return news
    except Exception as e:
        print(f"YF news error {symbol}: {e}")
        return []

def yf_earnings(symbol):
    """Yahoo Finance earnings data for a symbol"""
    try:
        url = f"{YAHOO_BASE2}/v10/finance/quoteSummary/{symbol}"
        r = requests.get(url, params={'modules':'earningsTrend,earnings'},
                        headers={'User-Agent':'Mozilla/5.0'}, timeout=10)
        if r.status_code != 200: return {}
        return r.json().get('quoteSummary',{}).get('result',[{}])[0]
    except Exception as e:
        print(f"YF earnings error {symbol}: {e}")
        return {}

def yf_index(symbol):
    """Yahoo Finance index quote - use ^GSPC ^FTSE etc."""
    return yf_quote(symbol)

# ── NASDAQ EARNINGS (No key, very reliable) ───────────────────────────

def nasdaq_earnings(date_from=None, date_to=None):
    """Nasdaq earnings calendar - much more reliable than Finnhub"""
    try:
        from datetime import date, timedelta
        if not date_from:
            date_from = date.today().strftime('%Y-%m-%d')
        if not date_to:
            date_to = (date.today() + timedelta(days=30)).strftime('%Y-%m-%d')
        url = f"{NASDAQ_BASE}/calendar/earnings"
        r = requests.get(url, params={'date': date_from},
                        headers={'User-Agent':'Mozilla/5.0',
                                 'Accept':'application/json, text/plain, */*'}, timeout=15)
        if r.status_code != 200:
            print(f"Nasdaq earnings returned {r.status_code}")
            return []
        rows = r.json().get('data',{}).get('rows',[]) or []
        result = []
        for row in rows:
            result.append({
                'symbol':          row.get('symbol',''),
                'name':            row.get('name',''),
                'date':            row.get('lastReportDate','') or date_from,
                'epsEstimate':     _safe_float(row.get('epsForecast')),
                'epsActual':       _safe_float(row.get('eps')),
                'revenueEstimate': None,
                'revenueActual':   None,
                'when':            'time not supplied',
            })
        return result
    except Exception as e:
        print(f"Nasdaq earnings error: {e}")
        return []

def _safe_float(val):
    try: return float(str(val).replace(',','').replace('$','')) if val and str(val) not in ['N/A','--',''] else None
    except: return None

# ── STOOQ (Global indices - no key) ───────────────────────────────────

def stooq_quote(symbol):
    """Stooq.com quote for global indices - ^SPX, ^UKX, ^NKX etc."""
    try:
        r = requests.get(STOOQ_BASE, params={'s':symbol,'i':'d'},
                        headers={'User-Agent':'Mozilla/5.0'}, timeout=10)
        if r.status_code != 200: return {}
        lines = r.text.strip().split('\n')
        if len(lines) < 2: return {}
        headers = lines[0].split(',')
        vals = lines[-1].split(',')
        data = dict(zip(headers, vals))
        price = float(data.get('Close', 0) or 0)
        open_ = float(data.get('Open', price) or price)
        chg   = price - open_
        chgp  = (chg/open_*100) if open_ else 0
        return {'c': round(price,2), 'd': round(chg,2), 'dp': round(chgp,2)}
    except Exception as e:
        print(f"Stooq error {symbol}: {e}")
        return {}

# ── OPEN EXCHANGE RATES (FX - replaces Finnhub OANDA) ────────────────

_fx_cache = {}
_fx_ts    = 0

def get_fx_rates():
    """Get all FX rates - cached 1 hour"""
    global _fx_cache, _fx_ts
    if time.time() - _fx_ts < 3600 and _fx_cache:
        return _fx_cache
    try:
        if OPENEX_KEY:
            r = requests.get(f"{OPENEX_BASE}/latest.json",
                            params={'app_id': OPENEX_KEY}, timeout=10)
            if r.status_code == 200:
                _fx_cache = r.json().get('rates', {})
                _fx_ts = time.time()
                return _fx_cache
        # Fallback: use Yahoo Finance for GBP/USD
        q = yf_quote('GBPUSD=X')
        if q.get('c'):
            _fx_cache = {'GBP': 1/q['c'] if q['c'] else 0.79}
            _fx_ts = time.time()
            return _fx_cache
    except Exception as e:
        print(f"FX rates error: {e}")
    return {}

def get_gbpusd():
    """Get GBP/USD rate"""
    rates = get_fx_rates()
    if rates:
        gbp = rates.get('GBP', 0)
        if gbp: return round(1/gbp, 4)
    # Last resort: Yahoo Finance direct
    q = yf_quote('GBPUSD=X')
    rate = q.get('c', 0)
    if rate:
        _quote_cache['OANDA:GBP_USD'] = {'c': rate, 'dp': q.get('dp',0)}
    return rate or 1.27

# ── ALPHA VANTAGE (Pre-calculated indicators) ─────────────────────────

def alpha_indicator(symbol, func='RSI', period=14):
    """Alpha Vantage technical indicator - pre-calculated"""
    if not ALPHA_KEY: return {}
    try:
        params = {
            'function': func, 'symbol': symbol,
            'time_period': period, 'series_type': 'close',
            'apikey': ALPHA_KEY
        }
        r = requests.get(ALPHA_BASE, params=params, timeout=15)
        if r.status_code != 200: return {}
        data = r.json()
        if 'Information' in data:
            print(f"Alpha Vantage limit hit: {data['Information'][:50]}")
            return {}
        return data
    except Exception as e:
        print(f"Alpha Vantage error {symbol}: {e}")
        return {}

# ── POLYGON.IO (News + quotes) ────────────────────────────────────────

def polygon_news(symbol, limit=10):
    """Polygon news for a ticker"""
    if not POLYGON_KEY: return []
    try:
        r = requests.get(f"{POLYGON_BASE}/v2/reference/news",
                        params={'ticker':symbol,'limit':limit,'apiKey':POLYGON_KEY},
                        timeout=10)
        if r.status_code != 200: return []
        items = r.json().get('results', [])
        news = []
        for item in items:
            news.append({
                'headline': item.get('title',''),
                'summary':  item.get('description','')[:200],
                'url':      item.get('article_url',''),
                'source':   item.get('publisher',{}).get('name','Polygon'),
                'datetime': int(time.mktime(time.strptime(item['published_utc'][:19], '%Y-%m-%dT%H:%M:%S'))) if item.get('published_utc') else int(time.time()),
                'sentiment':'Neutral',
            })
        return news
    except Exception as e:
        print(f"Polygon news error {symbol}: {e}")
        return []

# ── MARKETAUX (News + sentiment) ──────────────────────────────────────

def marketaux_news(symbol, limit=5):
    """Marketaux news with sentiment scores"""
    if not MARKETAUX_KEY: return []
    try:
        r = requests.get(f"{MARKETAUX_BASE}/news/all",
                        params={'symbols':symbol,'limit':limit,
                                'api_token':MARKETAUX_KEY,'language':'en'},
                        timeout=10)
        if r.status_code != 200: return []
        items = r.json().get('data',[])
        news = []
        for item in items:
            # Marketaux gives sentiment score -1 to 1
            score = item.get('sentiment_score', 0) or 0
            sentiment = 'Bullish' if score > 0.2 else 'Bearish' if score < -0.2 else 'Neutral'
            news.append({
                'headline': item.get('title',''),
                'summary':  item.get('description','')[:200],
                'url':      item.get('url',''),
                'source':   item.get('source','Marketaux'),
                'datetime': int(time.mktime(time.strptime(item['published_at'][:19], '%Y-%m-%dT%H:%M:%S'))) if item.get('published_at') else int(time.time()),
                'sentiment': sentiment,
                'sentiment_score': round(score, 2),
            })
        return news
    except Exception as e:
        print(f"Marketaux error {symbol}: {e}")
        return []

# ── FINANCIAL MODELING PREP (Analyst ratings + fundamentals) ─────────

_fmp_cache = {}

def fmp_analyst(symbol):
    """FMP analyst consensus - Buy/Hold/Sell + price target"""
    if not FMP_KEY: return {}
    cache_key = f"fmp_analyst_{symbol}"
    if cache_valid(_fmp_cache.get(cache_key), 3600):
        return _fmp_cache[cache_key]['data']
    try:
        # Analyst consensus
        r1 = requests.get(f"{FMP_BASE}/analyst-stock-recommendations/{symbol}",
                         params={'apikey':FMP_KEY}, timeout=10)
        # Price target
        r2 = requests.get(f"{FMP_BASE}/price-target-consensus/{symbol}",
                         params={'apikey':FMP_KEY}, timeout=10)
        result = {}
        if r1.status_code == 200:
            recs = r1.json()
            if recs:
                latest = recs[0]
                result['buy']       = latest.get('analystRatingsbuy', 0)
                result['hold']      = latest.get('analystRatingsHold', 0)
                result['sell']      = latest.get('analystRatingsSell', 0)
                result['consensus'] = latest.get('analystRatingsStrongBuy', 0)
        if r2.status_code == 200:
            targets = r2.json()
            if targets:
                t = targets[0]
                result['targetHigh']    = t.get('targetHigh')
                result['targetLow']     = t.get('targetLow')
                result['targetMean']    = t.get('targetConsensus')
                result['targetMedian']  = t.get('targetMedian')
        _fmp_cache[cache_key] = {'data': result, 'ts': time.time()}
        return result
    except Exception as e:
        print(f"FMP analyst error {symbol}: {e}")
        return {}

def fmp_fundamentals(symbol):
    """FMP key fundamentals - P/E, market cap, revenue"""
    if not FMP_KEY: return {}
    cache_key = f"fmp_fund_{symbol}"
    if cache_valid(_fmp_cache.get(cache_key), 86400):
        return _fmp_cache[cache_key]['data']
    try:
        r = requests.get(f"{FMP_BASE}/quote/{symbol}",
                        params={'apikey':FMP_KEY}, timeout=10)
        if r.status_code != 200: return {}
        data = r.json()
        if not data: return {}
        q = data[0]
        result = {
            'pe':         q.get('pe'),
            'eps':        q.get('eps'),
            'marketCap':  q.get('marketCap'),
            'beta':       q.get('beta'),
            'week52High': q.get('yearHigh'),
            'week52Low':  q.get('yearLow'),
            'avgVolume':  q.get('avgVolume'),
        }
        _fmp_cache[cache_key] = {'data': result, 'ts': time.time()}
        return result
    except Exception as e:
        print(f"FMP fundamentals error {symbol}: {e}")
        return {}

# ── STOCKTWITS (Social sentiment - no key) ────────────────────────────

def stocktwits_sentiment(symbol):
    """StockTwits bull/bear sentiment"""
    try:
        r = requests.get(f"{STOCKTWITS_BASE}/streams/symbol/{symbol}.json",
                        params={'limit':30}, timeout=10)
        if r.status_code != 200: return {}
        data = r.json()
        msgs = data.get('messages', [])
        bull = sum(1 for m in msgs if m.get('entities',{}).get('sentiment',{}).get('basic') == 'Bullish')
        bear = sum(1 for m in msgs if m.get('entities',{}).get('sentiment',{}).get('basic') == 'Bearish')
        total = bull + bear
        return {
            'bullish': bull, 'bearish': bear, 'total': total,
            'bullPct': round(bull/total*100) if total else 50,
            'bearPct': round(bear/total*100) if total else 50,
        }
    except Exception as e:
        print(f"StockTwits error {symbol}: {e}")
        return {}

# ── FEAR & GREED INDEX (CNN - no key) ────────────────────────────────

_fg_cache = {}

def fear_greed_index():
    """CNN Fear & Greed Index"""
    if cache_valid(_fg_cache.get('fg'), 3600):
        return _fg_cache['fg']['data']
    try:
        r = requests.get(
            'https://production.dataviz.cnn.io/index/fearandgreed/graphdata',
            headers={'User-Agent':'Mozilla/5.0'}, timeout=10)
        if r.status_code != 200: return {}
        data = r.json()
        score = data.get('fear_and_greed',{}).get('score', 0)
        rating = data.get('fear_and_greed',{}).get('rating','')
        result = {'score': round(score), 'rating': rating,
                  'label': rating.replace('_',' ').title()}
        _fg_cache['fg'] = {'data': result, 'ts': time.time()}
        return result
    except Exception as e:
        print(f"Fear & Greed error: {e}")
        return {}

# ── TIPRANKS (Unofficial - no key) ────────────────────────────────────

_tr_cache = {}

def tipranks_data(symbol):
    """TipRanks unofficial API - Smart Score + analyst consensus"""
    cache_key = f"tr_{symbol}"
    if cache_valid(_tr_cache.get(cache_key), 3600):
        return _tr_cache[cache_key]['data']
    try:
        r = requests.get(f"{TIPRANKS_BASE}/getData/",
                        params={'name': symbol},
                        headers={
                            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                            'Referer': f'https://www.tipranks.com/stocks/{symbol.lower()}/forecast',
                        }, timeout=15)
        if r.status_code != 200:
            print(f"TipRanks {symbol}: {r.status_code}")
            return {}
        data = r.json()
        consensus = data.get('expertRatings', {})
        smart_score = data.get('tipranksStockScore', {}).get('score')
        pt = data.get('priceTarget', {})
        result = {
            'smartScore':   smart_score,
            'buy':          consensus.get('buy', 0),
            'hold':         consensus.get('hold', 0),
            'sell':         consensus.get('sell', 0),
            'priceTarget':  pt.get('priceTarget'),
            'consensus':    data.get('expertRatingDescription',''),
        }
        _tr_cache[cache_key] = {'data': result, 'ts': time.time()}
        return result
    except Exception as e:
        print(f"TipRanks error {symbol}: {e}")
        return {}

# ── SEC EDGAR (Insider trades - no key) ──────────────────────────────

def sec_insider_trades(symbol, limit=5):
    """SEC EDGAR recent insider transactions"""
    try:
        # First get CIK for the company
        r = requests.get(f"{SEC_BASE}/submissions/CIK{symbol}.json",
                        headers={'User-Agent':'TradingAssist tradingassist.support@gmail.com'},
                        timeout=10)
        if r.status_code != 200:
            # Try search
            rs = requests.get('https://efts.sec.gov/LATEST/search-index?q=%22'+symbol+'%22&dateRange=custom&startdt=2024-01-01&forms=4',
                             headers={'User-Agent':'TradingAssist tradingassist.support@gmail.com'},
                             timeout=10)
            return []
        data = r.json()
        # Get recent Form 4 filings (insider trades)
        filings = data.get('filings',{}).get('recent',{})
        forms   = filings.get('form',[])
        dates   = filings.get('filingDate',[])
        trades  = []
        for i, form in enumerate(forms[:50]):
            if form == '4':  # Form 4 = insider trades
                trades.append({'date': dates[i], 'form': '4'})
                if len(trades) >= limit: break
        return trades
    except Exception as e:
        print(f"SEC EDGAR error {symbol}: {e}")
        return []

# ── FRED (Macro data) ─────────────────────────────────────────────────

_fred_cache = {}

def fred_series(series_id):
    """Fetch a FRED data series - e.g. FEDFUNDS, CPIAUCSL, GDP"""
    if not FRED_KEY: return {}
    cache_key = f"fred_{series_id}"
    if cache_valid(_fred_cache.get(cache_key), 86400):
        return _fred_cache[cache_key]['data']
    try:
        r = requests.get(f"{FRED_BASE}/series/observations",
                        params={
                            'series_id': series_id,
                            'api_key': FRED_KEY,
                            'file_type': 'json',
                            'limit': 5,
                            'sort_order': 'desc'
                        }, timeout=10)
        if r.status_code != 200: return {}
        obs = r.json().get('observations', [])
        if not obs: return {}
        latest = obs[0]
        result = {
            'value': float(latest.get('value', 0)) if latest.get('value') not in ['.',''] else None,
            'date':  latest.get('date',''),
            'series': series_id,
        }
        _fred_cache[cache_key] = {'data': result, 'ts': time.time()}
        return result
    except Exception as e:
        print(f"FRED error {series_id}: {e}")
        return {}

def get_macro_data():
    """Fetch key macro indicators from FRED"""
    import concurrent.futures
    series = {
        'fed_rate':   'FEDFUNDS',      # Fed Funds Rate
        'cpi':        'CPIAUCSL',      # CPI Inflation
        'unemployment':'UNRATE',       # Unemployment Rate
        'gdp_growth': 'A191RL1Q225SBEA', # Real GDP Growth
        'treasury_10y':'DGS10',        # 10-Year Treasury
        'treasury_2y': 'DGS2',         # 2-Year Treasury
    }
    result = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(fred_series, v): k for k,v in series.items()}
        for f in concurrent.futures.as_completed(futures, timeout=20):
            k = futures[f]
            try: result[k] = f.result()
            except: pass
    return result

# ── NEWS API (newsapi.org) ────────────────────────────────────────────

def newsapi_news(symbol, company_name='', limit=5):
    """NewsAPI.org financial news"""
    if not NEWS_API_KEY: return []
    try:
        query = company_name if company_name else symbol
        r = requests.get(f"{NEWS_BASE}/everything",
                        params={
                            'q': f'{query} stock',
                            'apiKey': NEWS_API_KEY,
                            'language': 'en',
                            'sortBy': 'publishedAt',
                            'pageSize': limit,
                        }, timeout=10)
        if r.status_code != 200: return []
        articles = r.json().get('articles', [])
        news = []
        for a in articles:
            import dateutil.parser
            try:
                dt = int(dateutil.parser.parse(a['publishedAt']).timestamp()) if a.get('publishedAt') else int(time.time())
            except:
                dt = int(time.time())
            news.append({
                'headline': a.get('title',''),
                'summary':  (a.get('description') or '')[:200],
                'url':      a.get('url',''),
                'source':   a.get('source',{}).get('name','NewsAPI'),
                'datetime': dt,
                'sentiment':'Neutral',
            })
        return news
    except Exception as e:
        print(f"NewsAPI error {symbol}: {e}")
        return []


def get_quote_td(symbol):
    """Get quote from Twelve Data"""
    data = td("price", {"symbol": symbol})
    if data.get("price"):
        price = float(data["price"])
        return {"c": price, "d": 0, "dp": 0}
    return {}

def get_candles_td(symbol):
    """Get candles from Twelve Data as fallback"""
    data = td("time_series", {
        "symbol": symbol, "interval": "1day",
        "outputsize": 300, "format": "JSON"
    })
    if not data.get("values"): return {"closes":[],"timestamps":[],"volumes":[]}
    values = data["values"]
    values.reverse()  # Twelve Data returns newest first
    closes = [float(v["close"]) for v in values]
    timestamps = [int(__import__('datetime').datetime.strptime(v["datetime"],"%Y-%m-%d").timestamp()) for v in values]
    volumes = [float(v.get("volume",0)) for v in values]
    return {"closes": closes, "timestamps": timestamps, "volumes": volumes, "ts": time.time()}

def get_indicators_td(symbol):
    """Get RSI and MACD from Twelve Data"""
    import concurrent.futures
    def get_rsi():
        return td("rsi", {"symbol": symbol, "interval": "1day", "time_period": 14})
    def get_macd():
        return td("macd", {"symbol": symbol, "interval": "1day"})
    def get_ma50():
        return td("ema", {"symbol": symbol, "interval": "1day", "time_period": 50})
    def get_ma200():
        return td("ema", {"symbol": symbol, "interval": "1day", "time_period": 200})
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            f_rsi  = ex.submit(get_rsi)
            f_macd = ex.submit(get_macd)
            f_ma50 = ex.submit(get_ma50)
            f_ma200= ex.submit(get_ma200)
            rsi_d  = f_rsi.result(timeout=10)
            macd_d = f_macd.result(timeout=10)
            ma50_d = f_ma50.result(timeout=10)
            ma200_d= f_ma200.result(timeout=10)
    except: return {}
    try:
        rsi  = float(rsi_d.get("values",[{}])[0].get("rsi",0)) if rsi_d.get("values") else None
        macd_val = float(macd_d.get("values",[{}])[0].get("macd",0)) if macd_d.get("values") else None
        macd_sig = float(macd_d.get("values",[{}])[0].get("macd_signal",0)) if macd_d.get("values") else None
        ma50  = float(ma50_d.get("values",[{}])[0].get("ema",0)) if ma50_d.get("values") else None
        ma200 = float(ma200_d.get("values",[{}])[0].get("ema",0)) if ma200_d.get("values") else None
        signal = score_signal(rsi, macd_val, macd_sig, ma50, ma200, [])
        return {"rsi":rsi,"macd":macd_val,"macd_signal":macd_sig,
                "ma50":ma50,"ma200":ma200,"signal":signal,
                "overbought": rsi>70 if rsi else False,
                "oversold": rsi<30 if rsi else False,
                "source":"twelvedata","ts":time.time()}
    except: return {}

def get_news_td(symbol):
    """Get news from Twelve Data"""
    data = td("news", {"symbol": symbol, "outputsize": 10})
    items = data if isinstance(data, list) else data.get("data", [])
    return [{
        "headline": n.get("title",""),
        "summary": n.get("description","")[:300],
        "url": n.get("url",""),
        "source": n.get("source",""),
        "datetime": int(__import__('datetime').datetime.strptime(
            n["published_at"][:19],"%Y-%m-%dT%H:%M:%S").timestamp())
            if n.get("published_at") else 0
    } for n in items[:10]]

def get_screener_suggestions():
    """Get dynamic stock suggestions using Twelve Data screener signals"""
    # Screen for momentum stocks using Twelve Data
    screener_params = {
        "exchange": "NASDAQ,NYSE",
        "country": "United States",
        "outputsize": 40,
        "type": "Common Stock"
    }
    # Get stocks with strong volume 
    gainers = td("stocks", screener_params)
    return []  # Will be populated below

def clean_symbol(ticker):
    """Convert T212 ticker to clean symbol for API lookups"""
    # First check LEVERAGE_MAP for known ETPs
    if ticker in LEVERAGE_MAP:
        return LEVERAGE_MAP[ticker].get("underlying", ticker)
    # UK leveraged ETPs end in l_EQ
    if ticker.endswith("l_EQ") or ticker.endswith("l_US"):
        s = ticker.split("_")[0]
        s = re.sub(r"^\d+", "", s)
        s = s.rstrip("l")
        return s.upper()
    # Regular tickers - just strip exchange suffix
    s = ticker.split("_")[0]
    s = re.sub(r"^\d+", "", s)
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

    # PRIMARY: Yahoo Finance (no key, unlimited, very reliable)
    yf_data = yf_candles(symbol, period='6mo', interval='1d')
    if yf_data and len(yf_data.get('closes',[])) >= 20:
        entry = {
            'closes':  yf_data['closes'],
            'highs':   yf_data.get('highs',[]),
            'lows':    yf_data.get('lows',[]),
            'volumes': yf_data.get('volumes',[]),
            'ts':      time.time()
        }
        _candle_cache[symbol] = entry
        return entry

    # FALLBACK: Finnhub
    to_ts=int(time.time()); from_ts=to_ts-(300*86400)
    data=fh("stock/candle",{"symbol":symbol,"resolution":"D","from":from_ts,"to":to_ts})
    if not data or data.get("s")!="ok":
        # Fallback to Twelve Data
        print(f"YF+Finnhub candles failed for {symbol} - trying Twelve Data")
        td_data = get_candles_td(symbol)
        if td_data.get("closes"):
            td_data["ts"] = time.time()
            _candle_cache[symbol] = td_data
            return td_data
        entry={"closes":[],"timestamps":[],"volumes":[],"ts":time.time()}
    else:
        entry={"closes":data.get("c",[]),"timestamps":data.get("t",[]),"volumes":data.get("v",[]),"ts":time.time()}
    _candle_cache[symbol]=entry
    return entry

# Symbols that work better with Twelve Data than Finnhub
TD_PREFERRED = {
    'GLD','SLV','GDX','SPY','QQQ','IWM','TLT','LQD','HYG',
    'XLF','XLE','XLK','XLV','XLU','XLI','XLP','XLB','XLRE',
    'SOXX','SMH','ARKK','ARKG','ARKW','ARKF',
    'VXX','UVXY','SQQQ','TQQQ','SOXS','SOXL',
    'OIL','USO','UNG','DBO','IAU','SGOL',
    'EEM','EFA','VWO','VEA','IEMG',
}

def get_indicators(symbol):
    if cache_valid(_ind_cache.get(symbol), IND_TTL):
        return _ind_cache[symbol]

    # Use Twelve Data for ETFs/commodities that Finnhub doesn't support
    if symbol in TD_PREFERRED and TWELVE_KEY:
        td_ind = get_indicators_td(symbol)
        if td_ind.get("rsi"):
            _ind_cache[symbol] = td_ind
            return td_ind

    # Try Finnhub candles
    candles = get_candles(symbol)
    closes  = candles.get("closes", [])

    if len(closes) < 30:
        # Fallback to Twelve Data
        if TWELVE_KEY:
            td_ind = get_indicators_td(symbol)
            if td_ind.get("rsi"):
                _ind_cache[symbol] = td_ind
                return td_ind
        entry = {"rsi":None,"macd":None,"macd_signal":None,"macd_hist":None,
                 "bb_upper":None,"bb_middle":None,"bb_lower":None,
                 "ma50":None,"ma200":None,"signal":"Neutral","ts":time.time()}
        _ind_cache[symbol] = entry
        return entry
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

    # Skip Yahoo for OANDA/forex symbols - use FX rates
    if 'OANDA:' in symbol or '/' in symbol:
        pass  # fall through to Finnhub
    else:
        # PRIMARY: Yahoo Finance
        yq = yf_quote(symbol)
        if yq and yq.get('c') and float(yq['c']) > 0:
            entry = {'c': yq['c'], 'd': yq.get('d',0), 'dp': yq.get('dp',0),
                     'pc': yq.get('pc',0), 'h': 0, 'l': 0, 'o': 0, 't': int(time.time())}
            _quote_cache[symbol] = entry
            return entry
    data=fh("quote",{"symbol":symbol})
    if not data.get("c"):
        # Fallback to Twelve Data
        td_quote = get_quote_td(symbol)
        if td_quote.get("c"):
            entry={**td_quote,"source":"twelvedata","ts":time.time()}
            _quote_cache[symbol]=entry; return entry
    entry={**data,"ts":time.time()}
    _quote_cache[symbol]=entry; return entry

# ── BACKGROUND PRE-FETCH ──────────────────────────────────────────────
_bg_symbols = set()
_bg_lock    = threading.Lock()

# get_gbpusd() is now defined in new API section above

def background_prefetch():
    # Pre-fetch GBP/USD exchange rate on startup
    try:
        get_gbpusd()
    except: pass
    while True:
        # Keep FX rates fresh
        try:
            get_gbpusd()
        except: pass
        with _bg_lock: syms=list(_bg_symbols)
        for sym in syms:
            try:
                if not cache_valid(_ind_cache.get(sym), IND_TTL):
                    get_indicators(sym)
                    time.sleep(0.5 if sym in TD_PREFERRED else 0.8)
                if not cache_valid(_profile_cache.get(sym), PROFILE_TTL):
                    get_profile(sym); time.sleep(0.4)
                if not cache_valid(_quote_cache.get(sym), QUOTE_TTL):
                    get_quote(sym); time.sleep(0.3)
            except Exception as e: print(f"BG error {sym}: {e}")
        time.sleep(20)

threading.Thread(target=background_prefetch, daemon=True).start()

def register_symbols(holdings):
    with _bg_lock:
        for h in holdings:
            sym=h.get("indSymbol") or h.get("symbol")
            if sym: _bg_symbols.add(sym)

# ── HOLDING BUILDER ───────────────────────────────────────────────────
def basic_holding(h, account_label, acct_cur=None):
    ticker  = h.get("ticker",""); symbol=clean_symbol(ticker)
    qty     = h.get("quantity",0) or 0
    avg     = h.get("averagePrice",0) or 0
    ppl     = h.get("ppl") or 0        # T212 P&L in account currency (GBP)
    us      = is_us(ticker)
    lev_info= get_leverage_info(ticker)
    leverage= lev_info["leverage"] if lev_info else None
    ind_sym = get_indicator_symbol(ticker,symbol)
    sector  = SECTOR_MAP.get(symbol,"Other")
    if lev_info:
        und=lev_info.get("underlying","")
        if und in ["AMD","PLTR","ARM","TSM","MU"]: sector="Leveraged Tech"
        elif und in ["OIL"]:                        sector="Leveraged Commodity"
        elif und in ["SOXX","QQQ"]:                 sector="Leveraged ETF"

    is_uk_etp = ticker.endswith("l_EQ") and not us
    gbp_usd   = _quote_cache.get("OANDA:GBP_USD",{}).get("c",0) or 1.27

    # ── PRICES ──────────────────────────────────────────────────────
    if is_uk_etp:
        # UK ETP: prices in pence → convert to GBP
        current_price_native = h.get("currentPrice",0) or 0  # pence
        avg_price_native      = avg                            # pence
        current_price_gbp     = round(current_price_native/100, 4)
        avg_price_gbp         = round(avg/100, 4)
        portfolio_value       = round(qty * current_price_gbp, 2)
        display_currency      = "GBP"
    elif us:
        # US stock: prices in USD
        current_price_native = h.get("currentPrice",0) or 0  # USD
        avg_price_native      = avg                            # USD
        current_price_gbp     = round(current_price_native/gbp_usd, 4) if gbp_usd else 0
        avg_price_gbp         = round(avg/gbp_usd, 4) if gbp_usd else avg
        # Total holding in GBP = current USD price * qty / GBP_USD
        portfolio_value       = round((current_price_native * qty)/gbp_usd, 2) if gbp_usd else round((qty*avg)+ppl,2)
        display_currency      = "USD"
    else:
        # UK stock: prices already in GBP
        current_price_native = h.get("currentPrice",0) or 0
        avg_price_native      = avg
        current_price_gbp     = current_price_native
        avg_price_gbp         = avg
        portfolio_value       = round((qty * current_price_native) if current_price_native else (qty*avg)+ppl, 2)
        display_currency      = "GBP"

    # ── ACCOUNT SETTLEMENT CURRENCY ─────────────────────────────────
    # Determines what currency P&L and Total Holding are shown in
    if acct_cur:
        settle_currency = acct_cur['currency']
        settle_symbol   = acct_cur['symbol']
    else:
        settle_currency = 'GBP'
        settle_symbol   = '£'

    # ── P&L ─────────────────────────────────────────────────────────
    # T212 ppl IS in account settlement currency ✅
    ppl_gbp = round(ppl, 2)

    # ── DAY CHANGE - use quote cache or fetch from Yahoo ────────────
    q = _quote_cache.get(ind_sym, {})
    if not q or not q.get("dp"):
        q = _quote_cache.get(symbol, {})
    day_change_pct = q.get("dp", 0) or 0
    # If still 0, try Yahoo Finance directly (fast, no rate limit)
    if day_change_pct == 0 and ind_sym:
        try:
            yq = yf_quote(ind_sym)
            if yq and yq.get("dp"):
                day_change_pct = yq["dp"]
                _quote_cache[ind_sym] = {"c": yq.get("c",0), "dp": yq["dp"], "d": yq.get("d",0)}
        except: pass

    h_copy = dict(h)
    if is_uk_etp:
        h_copy["currentPrice"] = current_price_gbp
        h_copy["averagePrice"] = avg_price_gbp
        h_copy["penceAvg"]     = round(avg, 2)
        h_copy["penceCurrent"] = round(current_price_native, 2)
        if lev_info and lev_info.get("underlying"):
            uq = _quote_cache.get(lev_info["underlying"],{})
            h_copy["underlyingSymbol"] = lev_info["underlying"]
            h_copy["underlyingPrice"]  = uq.get("c")
    elif us:
        # Keep native USD prices for display, add GBP converted
        h_copy["currentPriceGBP"] = current_price_gbp
        h_copy["avgPriceGBP"]     = avg_price_gbp

    return {**h_copy,
        "symbol":          symbol,
        "name":            _profile_cache.get(symbol,{}).get("name","") or NAME_MAP.get(symbol,""),
        "sector":          sector,
        "portfolioValue":  portfolio_value,
        "ppl":             ppl_gbp,
        "currency":        display_currency,    # for avg/current price display
        "settleCurrency":  settle_currency,     # account settlement currency
        "settleSymbol":    settle_symbol,       # £ $ ₹ €
        "dayChangePct":    round(day_change_pct,2),
        "isUkEtp":         is_uk_etp,
        "account":         account_label,
        "leverage":        leverage,
        "indSymbol":       ind_sym,
        "indicators":      {},
        "signal":          "Loading...",
        "news":            {},
    }

# ── USER PORTFOLIO FETCHER ────────────────────────────────────────────
def get_user_portfolio(username):
    """Fetch all portfolio data for a user based on their connected accounts"""
    user=get_user(username)
    if not user: return {"accounts":[],"summary":{}}

    accounts=user.get("accounts",[])
    all_holdings=[]
    summaries=[]

    import concurrent.futures as cf

    def fetch_one_account(acct):
        if not acct.get("enabled", True): return None
        api_key  = acct.get("api_key", "")
        label    = acct.get("label", "Account")
        broker   = acct.get("broker", "trading212_invest")
        acct_cur = get_account_currency(broker, user.get('country', 'GB'))
        try:
            portfolio, summary = fetch_portfolio(broker, api_key)
            if isinstance(portfolio, list):
                enriched = [basic_holding(h, label, acct_cur) for h in portfolio]
                enriched.sort(key=lambda x: x.get("portfolioValue", 0), reverse=True)
                register_symbols(enriched)
                return {"label":label,"broker":broker,"holdings":enriched,"summary":summary or {}}
        except Exception as e:
            print(f"Account fetch error {label}: {e}")
        return None

    # Fetch all accounts in parallel for speed
    active = [a for a in accounts if a.get("enabled", True)]
    workers = max(1, len(active))
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        acct_results = list(ex.map(fetch_one_account, active))

    for r in acct_results:
        if not r: continue
        all_holdings.append(r)
        summaries.append({"label":r["label"],"broker":r["broker"],"summary":r["summary"]})

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
    if not email:    return jsonify({'error':'Email address is required'}),200
    if not name:     return jsonify({'error':'Full name is required'}),200
    if not phone:    return jsonify({'error':'Phone number is required'}),200
    if not address:  return jsonify({'error':'Street address is required'}),200
    if not postcode: return jsonify({'error':'Postcode is required'}),200
    if not country:  return jsonify({'error':'Please select your country'}),200
    if not password: return jsonify({'error':'Password is required'}),200
    if '@' not in email or '.' not in email:
        return jsonify({'error':'Please enter a valid email address'}),200
    # Password strength validation
    if len(password)<8:
        return jsonify({'error':'Password must be at least 8 characters'}),200
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?' for c in password)
    strength = sum([has_upper, has_lower, has_digit, has_special])
    if strength < 3:
        return jsonify({'error':'Password must contain at least 3 of: uppercase, lowercase, number, special character (!@#$%^&*)'}),200
    users=load_users()
    if email in users:
        return jsonify({'error':'An account with this email already exists'}),200
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
    return jsonify({'success':True,'username':email,'name':name,'email':email,'phone':phone,'address':address,'postcode':postcode,'country':country,'role':users[email]['role'],'accounts':[]})

@app.route('/api/auth/login', methods=['POST'])
def login():
    data=request.json or {}
    email   =data.get('email','').strip().lower()
    password=data.get('password','')
    if not email or not password:
        return jsonify({'error':'Email and password are required'}),200
    user=get_user(email)
    if not user or user['password']!=hash_password(password):
        return jsonify({'error':'Invalid email or password'}),200  # 200 so frontend can show the error
    session['username']=email
    return jsonify({'success':True,'username':email,'name':user.get('name',email),'email':email,'phone':user.get('phone',''),'address':user.get('address',''),'postcode':user.get('postcode',''),'country':user.get('country',''),'role':user.get('role','user'),'accounts':user.get('accounts',[])})

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
    """Return broker registry for frontend, including server IP"""
    try:
        ip_resp = requests.get('https://api.ipify.org', timeout=5)
        server_ip = ip_resp.text.strip()
    except:
        server_ip = 'Unable to detect - check Test & Connect error message'
    return jsonify({'brokers': BROKER_REGISTRY, 'server_ip': server_ip})

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

@app.route('/api/auth/forgot', methods=['POST'])
@app.route('/api/auth/forgot-password', methods=['POST'])
def forgot_password():
    data  = request.json or {}
    email = data.get('email','').strip().lower()
    if not email:
        return jsonify({'error':'Email required'}),400
    user = get_user(email)
    if user:
        token   = generate_token()
        expires = time.time() + 3600
        _reset_tokens[token] = {'email':email,'expires':expires}
        reset_url = get_app_url() + '/reset-password?token=' + token
        html = reset_email_html(user.get('name',''), reset_url)
        send_email(email, 'Reset your TradingAssist password', html)
    return jsonify({'success':True,'message':'If an account exists with that email, a reset link has been sent.'})

@app.route('/api/auth/reset', methods=['POST'])
@app.route('/api/auth/reset-password', methods=['POST'])
def reset_password():
    data   = request.json or {}
    token  = data.get('token','')
    new_pw = data.get('password','')
    entry  = _reset_tokens.get(token)
    if not entry:
        return jsonify({'error':'Invalid or expired reset link'}),400
    if time.time() > entry['expires']:
        del _reset_tokens[token]
        return jsonify({'error':'Reset link has expired. Please request a new one.'}),400
    if len(new_pw) < 8:
        return jsonify({'error':'Password must be at least 8 characters'}),400
    has_upper=any(c.isupper() for c in new_pw)
    has_lower=any(c.islower() for c in new_pw)
    has_digit=any(c.isdigit() for c in new_pw)
    has_special=any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?' for c in new_pw)
    if sum([has_upper,has_lower,has_digit,has_special])<3:
        return jsonify({'error':'Password too weak. Use uppercase, lowercase, number and special character.'}),400
    email = entry['email']
    users = load_users()
    if email not in users:
        return jsonify({'error':'Account not found'}),404
    users[email]['password'] = hash_password(new_pw)
    save_users(users)
    del _reset_tokens[token]
    return jsonify({'success':True,'message':'Password updated! You can now log in.'})

@app.route('/api/user/change-email', methods=['POST'])
@require_login
def change_email():
    data      = request.json or {}
    new_email = data.get('new_email','').strip().lower()
    password  = data.get('password','')
    if not new_email or not password:
        return jsonify({'error':'New email and current password required'}),400
    if '@' not in new_email or '.' not in new_email:
        return jsonify({'error':'Please enter a valid email address'}),400
    users    = load_users()
    username = current_user()
    user     = users.get(username)
    if not user or user['password'] != hash_password(password):
        return jsonify({'error':'Incorrect password'}),401
    if new_email in users and new_email != username:
        return jsonify({'error':'An account with this email already exists'}),400
    users[new_email] = dict(user)
    users[new_email]['email']    = new_email
    users[new_email]['username'] = new_email
    if new_email != username:
        del users[username]
    save_users(users)
    session['username'] = new_email
    send_email(username, 'TradingAssist - Email changed', '<p>Your email was changed to <strong>' + new_email + '</strong>. If this was not you, contact support immediately.</p>')
    send_email(new_email, 'TradingAssist - Email confirmed', '<p>Your TradingAssist login email is now <strong>' + new_email + '</strong>.</p>')
    return jsonify({'success':True,'message':'Email updated successfully.'})

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
    # Get user's country for currency
    user = get_user(username) or {}
    user_country = user.get('country','GB')
    base_currency = 'USD' if user_country == 'US' else 'INR' if user_country == 'IN' else 'GBP'
    currency_symbol = '$' if base_currency == 'USD' else '₹' if base_currency == 'INR' else '£'

    # Build combined summary from T212 data
    total_value=0; total_cash=0; total_unrealised=0; total_realised=0
    total_day_change=0
    for acct in portfolio.get('summaries',[]):
        s=acct.get('summary',{})
        # T212 field names: totalValue, cash.availableToTrade
        # portfolio.total, portfolio.result (daily), portfolio.ppl (unrealised), portfolio.realizedPpl
        total_value += s.get('totalValue',0) or 0
        total_cash  += (s.get('cash',{}) or {}).get('availableToTrade',0) or 0
        port = s.get('portfolio',{}) or {}
        total_unrealised  += port.get('ppl',0) or 0
        total_realised    += port.get('realizedPpl',0) or 0
        # 'result' in T212 portfolio = today's daily P&L change
        total_day_change  += port.get('result',0) or 0
    # Calculate day change - fetch quotes if not cached
    all_holdings_flat = []
    for acct in portfolio.get('accounts',[]):
        all_holdings_flat.extend(acct.get('holdings',[]))

    # Get unique symbols and fetch quotes in parallel
    import concurrent.futures
    unique_syms = list({h.get('indSymbol') or h.get('symbol','') for h in all_holdings_flat if h.get('indSymbol') or h.get('symbol')})
    
    def fetch_q(sym):
        if not cache_valid(_quote_cache.get(sym), QUOTE_TTL):
            return sym, get_quote(sym)
        return sym, _quote_cache.get(sym, {})
    
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            futs = {ex.submit(fetch_q, sym): sym for sym in unique_syms[:20]}
            for f in concurrent.futures.as_completed(futs, timeout=8):
                try: f.result()
                except: pass
    except: pass

    # Use T212's own result field for day change (most accurate)
    # Also fetch quotes to supplement if T212 result is 0
    gbp_usd = _quote_cache.get("OANDA:GBP_USD", {}).get("c", 0) or 1.27
    if total_day_change == 0:
        # Fallback: calculate from quote day changes
        all_holdings_flat = []
        for acct in portfolio.get('accounts',[]):
            all_holdings_flat.extend(acct.get('holdings',[]))
        for holding in all_holdings_flat:
            sym = holding.get('indSymbol') or holding.get('symbol','')
            qty = holding.get('quantity', 0) or 0
            is_usd = holding.get('currency','') == 'USD'
            is_etp = holding.get('isUkEtp', False)
            q = _quote_cache.get(sym, {})
            d = q.get('d', 0) or 0
            if d and qty:
                if is_usd:
                    total_day_change += (d * qty) / gbp_usd
                elif is_etp:
                    total_day_change += (d / 100) * qty
                else:
                    total_day_change += d * qty
    total_day_change_pct = round((total_day_change / total_value) * 100, 2) if total_value else 0
    total_day_change = round(total_day_change, 2)

    return jsonify({
        'accounts':portfolio.get('summaries',[]),
        'combined':{
            'totalValue':round(total_value,2),
            'availableCash':round(total_cash,2),
            'dayChange':round(total_day_change,2),
            'dayChangePct':round(total_day_change_pct,2),
            'unrealizedPnL':round(total_unrealised,2),
            'realizedPnL':round(total_realised,2),
            'currency':base_currency,
            'currencySymbol':currency_symbol,
        }
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

def get_yahoo_news(symbol):
    """Yahoo Finance RSS news - kept for compatibility"""
    return yf_news(symbol)

def get_all_news(symbol, company_name='', limit=15):
    """Fetch news from ALL sources in parallel - deduplicated"""
    import concurrent.futures
    all_news = []
    seen_titles = set()

    def fetch_yf():     return yf_news(symbol, count=limit)
    def fetch_poly():   return polygon_news(symbol, limit=5) if POLYGON_KEY else []
    def fetch_mktaux(): return marketaux_news(symbol, limit=5) if MARKETAUX_KEY else []
    def fetch_fh():     return [{'headline':n.get('headline',''),'summary':n.get('summary',''),
                                  'url':n.get('url',''),'source':n.get('source','Finnhub'),
                                  'datetime':n.get('datetime',0),'sentiment':'Neutral'}
                                 for n in (fh('company-news',{'symbol':symbol,
                                    'from':(datetime.now()-timedelta(days=30)).strftime('%Y-%m-%d'),
                                    'to':datetime.now().strftime('%Y-%m-%d')}) or [])[:5]]

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(f) for f in [fetch_yf, fetch_poly, fetch_mktaux, fetch_fh]]
        for f in concurrent.futures.as_completed(futures, timeout=8):
            try:
                for item in f.result():
                    title = item.get('headline','')[:50]
                    if title and title not in seen_titles:
                        seen_titles.add(title)
                        all_news.append(item)
            except: pass

    all_news.sort(key=lambda x: x.get('datetime',0), reverse=True)
    return all_news[:limit]


def api_news(symbol):
    today=datetime.now().strftime("%Y-%m-%d")
    week_ago=(datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d")
    month_ago=(datetime.now()-timedelta(days=30)).strftime("%Y-%m-%d")
    import concurrent.futures
    def get_fh_news():
        news=fh("company-news",{"symbol":symbol,"from":week_ago,"to":today})
        if not news or len(news)<3:
            news=fh("company-news",{"symbol":symbol,"from":month_ago,"to":today})
        return news if isinstance(news,list) else []
    def get_td_news(): return get_news_td(symbol)
    def get_yf_news(): return get_yahoo_news(symbol)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            f1=ex.submit(get_fh_news); f2=ex.submit(get_td_news); f3=ex.submit(get_yf_news)
            fh_news=f1.result(timeout=10); td_news=f2.result(timeout=10); yf_news=f3.result(timeout=10)
    except: fh_news=[]; td_news=[]; yf_news=[]
    seen=set(); combined=[]
    for n in (fh_news+td_news+yf_news):
        headline=(n.get("headline") or n.get("title",""))[:60]
        if headline and headline not in seen:
            seen.add(headline); combined.append(n)
    combined.sort(key=lambda x: x.get("datetime",0), reverse=True)
    return jsonify(combined[:20])

@app.route('/api/earnings/news/<symbol>')
@require_login
def api_earnings_news(symbol):
    """Get fresh news for a specific earnings stock"""
    if symbol in _news_cache:
        return jsonify(_news_cache[symbol])
    today=datetime.now().strftime("%Y-%m-%d")
    week_ago=(datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d")
    month_ago=(datetime.now()-timedelta(days=30)).strftime("%Y-%m-%d")
    news=fh("company-news",{"symbol":symbol,"from":week_ago,"to":today})
    if not news or len(news)<2:
        news=fh("company-news",{"symbol":symbol,"from":month_ago,"to":today})
    result = news[:10] if isinstance(news,list) else []
    _news_cache[symbol] = result
    return jsonify(result)

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
    print(f"Earnings: fetching fresh data from Finnhub...")
    today=datetime.now().strftime("%Y-%m-%d")
    tomorrow=(datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d")
    future=(datetime.now()+timedelta(days=90)).strftime("%Y-%m-%d")
    past=(datetime.now()-timedelta(days=90)).strftime("%Y-%m-%d")
    import concurrent.futures
    def fetch_upcoming(from_dt, to_dt):
        # PRIMARY: Nasdaq earnings API (no key, very reliable)
        items = nasdaq_earnings(from_dt, to_dt)
        if items:
            print(f"Nasdaq earnings: got {len(items)} upcoming items")
            return {"earningsCalendar": items}
        # FALLBACK: Finnhub
        result = fh("calendar/earnings", {"from": from_dt, "to": to_dt})
        count = len((result or {}).get("earningsCalendar",[]))
        print(f"Finnhub earnings: got {count} upcoming items")
        return result or {}

    def fetch_past(from_dt, to_dt):
        # PRIMARY: Nasdaq (past)
        items = nasdaq_earnings(from_dt, to_dt)
        if items:
            print(f"Nasdaq past earnings: got {len(items)} items")
            return {"earningsCalendar": items}
        # FALLBACK: Finnhub
        result = fh("calendar/earnings", {"from": from_dt, "to": to_dt})
        count = len((result or {}).get("earningsCalendar",[]))
        print(f"Finnhub past earnings: got {count} items")
        return result or {}

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            f_up   = ex.submit(fetch_upcoming, today, future)
            f_past = ex.submit(fetch_past, past, today)
            upcoming_data = f_up.result(timeout=25)
            past_data     = f_past.result(timeout=25)
        print(f"Earnings: got {len(upcoming_data.get('earningsCalendar',[]))} upcoming, {len(past_data.get('earningsCalendar',[]))} past")
    except Exception as e:
        print(f"Earnings fetch error: {e}")
        upcoming_data={}; past_data={}
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
        if price>0 and price<2 and not in_p: return None  # only filter penny stocks under $2
        ea=item.get("epsActual"); ee=item.get("epsEstimate")
        ra=item.get("revenueActual"); re_=item.get("revenueEstimate")
        # Get top news headline from cache (no extra API call)
        news_item = None
        if sym in _news_cache:
            nl = _news_cache[sym]
            if nl: news_item = {"headline": nl[0].get("headline",""), "url": nl[0].get("url",""), "datetime": nl[0].get("datetime",0)}
        return {**item,"companyName":name,"currentPrice":price if price else None,
                "priceChange":q.get("d"),"priceChangePct":q.get("dp"),"inPortfolio":in_p,"marketCap":mcap,
                "epsVerdict":get_eps_verdict(ea,ee),"revVerdict":get_rev_verdict(ra,re_),
                "epsSurprisePct":round(((ea-ee)/abs(ee))*100,1) if ea is not None and ee else None,
                "revSurprisePct":round(((ra-re_)/abs(re_))*100,1) if ra is not None and re_ else None,
                "latestNews": news_item}
    def sort_upcoming(items):
        """Sort by date ascending - earliest earnings first, next 30 days only"""
        from datetime import date, timedelta
        cutoff = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        today_str = date.today().strftime("%Y-%m-%d")
        enriched = [e for e in [enrich(i) for i in items] if e is not None]
        # Only show next 30 days
        enriched = [e for e in enriched if today_str <= e.get("date","") <= cutoff]
        # Sort: earliest date first, then by market cap within same date
        enriched.sort(key=lambda x: (x.get("date","9999-99-99"), -x.get("marketCap",0)))
        # Portfolio stocks at top within each date group
        enriched.sort(key=lambda x: (x.get("date","9999-99-99"), 0 if x.get("inPortfolio") else 1, -x.get("marketCap",0)))
        return enriched[:50]
    def sort_past(items):
        enriched=[e for e in [enrich(i) for i in items] if e is not None]
        portfolio=sorted([e for e in enriched if e["inPortfolio"]],key=lambda x: x.get("date",""),reverse=True)
        others=sorted([e for e in enriched if not e["inPortfolio"]],key=lambda x: x.get("date",""),reverse=True)
        return (portfolio+others)[:50]
    upcoming_list = upcoming_data.get("earningsCalendar") or [] if isinstance(upcoming_data, dict) else []
    past_list     = past_data.get("earningsCalendar") or [] if isinstance(past_data, dict) else []
    print(f"Earnings processing: {len(upcoming_list)} upcoming, {len(past_list)} past items")
    result={"upcoming":sort_upcoming(upcoming_list),
            "past":sort_past(past_list)}
    _earnings_cache["entry"]={"data":result,"ts":time.time()}

    # Background: pre-fetch quotes AND news for all earnings stocks
    def prefetch_earnings_data():
        all_syms=list({e["symbol"] for e in result["upcoming"]+result["past"]})[:30]
        for sym in all_syms:
            try:
                if not cache_valid(_quote_cache.get(sym), QUOTE_TTL):
                    get_quote(sym); time.sleep(0.2)
                if sym not in _news_cache:
                    today=datetime.now().strftime("%Y-%m-%d")
                    week_ago=(datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d")
                    news=fh("company-news",{"symbol":sym,"from":week_ago,"to":today})
                    _news_cache[sym]=news[:5] if isinstance(news,list) else []
                    time.sleep(0.3)
            except: pass
    threading.Thread(target=prefetch_earnings_data, daemon=True).start()

    return jsonify(result)

@app.route('/api/suggestions')
@require_login
def api_suggestions():
    """Dynamic AI suggestions - rotates daily, enriched with live indicators"""
    import concurrent.futures, hashlib, random

    # Cache suggestions for 1 hour
    cache_key = "suggestions"
    if cache_valid(_market_cache.get(cache_key), 3600):
        return jsonify(_market_cache[cache_key]["data"])

    # ── STOCK UNIVERSE ─────────────────────────────────────────────
    # Large pool - we pick from these dynamically based on signals
    UNIVERSE = {
        "1day": [
            # High momentum, high volume day trade candidates
            {"ticker":"NVDA","company":"NVIDIA Corp","sector":"AI/Semiconductors","risk":"High","reason":"Leading AI chip maker with strong institutional momentum."},
            {"ticker":"AMD","company":"Advanced Micro Devices","sector":"Semiconductors","risk":"High","reason":"AI GPU competitor gaining data center share."},
            {"ticker":"TSLA","company":"Tesla Inc","sector":"EV/Energy","risk":"Very High","reason":"High beta stock with strong retail trader following."},
            {"ticker":"META","company":"Meta Platforms","sector":"Social Media","risk":"Medium","reason":"Strong ad revenue recovery and AI investment cycle."},
            {"ticker":"GOOGL","company":"Alphabet Inc","sector":"Tech/AI","risk":"Medium","reason":"AI search dominance and cloud growth."},
            {"ticker":"MSTR","company":"MicroStrategy","sector":"Bitcoin Proxy","risk":"Very High","reason":"Leveraged Bitcoin exposure via equity."},
            {"ticker":"COIN","company":"Coinbase","sector":"Crypto Exchange","risk":"Very High","reason":"Crypto market cycle play."},
            {"ticker":"PLTR","company":"Palantir Technologies","sector":"AI/Defense","risk":"High","reason":"Government AI contracts and commercial expansion."},
            {"ticker":"SMCI","company":"Super Micro Computer","sector":"AI Infrastructure","risk":"Very High","reason":"AI server demand beneficiary."},
            {"ticker":"HOOD","company":"Robinhood Markets","sector":"Fintech","risk":"High","reason":"Retail investor platform growth."},
            {"ticker":"CRWD","company":"CrowdStrike","sector":"Cybersecurity","risk":"High","reason":"Market leader in endpoint security."},
            {"ticker":"SOFI","company":"SoFi Technologies","sector":"Fintech","risk":"High","reason":"Digital bank with strong student loan exposure."},
        ],
        "1week": [
            {"ticker":"MSFT","company":"Microsoft Corp","sector":"Cloud/AI","risk":"Low","reason":"Azure cloud growth and OpenAI partnership."},
            {"ticker":"AAPL","company":"Apple Inc","sector":"Consumer Tech","risk":"Low","reason":"Services growth and India manufacturing expansion."},
            {"ticker":"AMZN","company":"Amazon.com","sector":"E-Commerce/Cloud","risk":"Medium","reason":"AWS margin expansion and advertising growth."},
            {"ticker":"SHOP","company":"Shopify Inc","sector":"E-Commerce","risk":"High","reason":"SMB commerce platform with strong merchant growth."},
            {"ticker":"SQ","company":"Block Inc","sector":"Fintech","risk":"High","reason":"Cash App ecosystem and Bitcoin integration."},
            {"ticker":"UBER","company":"Uber Technologies","sector":"Mobility","risk":"Medium","reason":"Autonomous vehicle partnerships and profitability."},
            {"ticker":"RBLX","company":"Roblox Corp","sector":"Gaming/Metaverse","risk":"High","reason":"User engagement and creator economy growth."},
            {"ticker":"HIMS","company":"Hims & Hers Health","sector":"Digital Health","risk":"High","reason":"GLP-1 weight loss drug opportunity."},
            {"ticker":"SNOW","company":"Snowflake Inc","sector":"Cloud Data","risk":"High","reason":"Data cloud platform with AI tailwinds."},
            {"ticker":"NET","company":"Cloudflare Inc","sector":"Cloud/Security","risk":"High","reason":"Zero trust security and AI edge computing."},
            {"ticker":"DDOG","company":"Datadog Inc","sector":"Cloud Monitoring","risk":"High","reason":"Observability platform with AI features."},
            {"ticker":"MDB","company":"MongoDB Inc","sector":"Cloud Database","risk":"High","reason":"Developer-first database with AI vector search."},
        ],
        "1month": [
            {"ticker":"ORCL","company":"Oracle Corp","sector":"Cloud/Database","risk":"Medium","reason":"Cloud infrastructure buildout for AI workloads."},
            {"ticker":"CRM","company":"Salesforce Inc","sector":"Enterprise SaaS","risk":"Medium","reason":"Agentforce AI driving upsell in CRM."},
            {"ticker":"NOW","company":"ServiceNow","sector":"Enterprise SaaS","risk":"Medium","reason":"AI workflow automation across enterprises."},
            {"ticker":"PANW","company":"Palo Alto Networks","sector":"Cybersecurity","risk":"Medium","reason":"Platform consolidation play in security."},
            {"ticker":"ASML","company":"ASML Holding","sector":"Semiconductor Equipment","risk":"Medium","reason":"EUV monopoly critical to chip manufacturing."},
            {"ticker":"TSM","company":"Taiwan Semiconductor","sector":"Semiconductors","risk":"Medium","reason":"Foundry for NVDA, AAPL, AMD chips."},
            {"ticker":"AVGO","company":"Broadcom Inc","sector":"Semiconductors","risk":"Medium","reason":"Custom AI chip design for hyperscalers."},
            {"ticker":"ARM","company":"ARM Holdings","sector":"Chip Architecture","risk":"High","reason":"CPU IP licensing for mobile and data centres."},
            {"ticker":"AMAT","company":"Applied Materials","sector":"Semiconductor Equipment","risk":"Medium","reason":"Equipment demand tied to AI chip capex."},
            {"ticker":"LRCX","company":"Lam Research","sector":"Semiconductor Equipment","risk":"Medium","reason":"Etch and deposition equipment for leading-edge chips."},
            {"ticker":"MRVL","company":"Marvell Technology","sector":"Semiconductors","risk":"High","reason":"Custom AI networking chips for hyperscalers."},
        ],
        "1year": [
            {"ticker":"IONQ","company":"IonQ Inc","sector":"Quantum Computing","risk":"Very High","reason":"Early stage quantum computing with government contracts."},
            {"ticker":"RXRX","company":"Recursion Pharma","sector":"AI Drug Discovery","risk":"Very High","reason":"AI-first drug discovery platform."},
            {"ticker":"RKLB","company":"Rocket Lab","sector":"Space","risk":"Very High","reason":"Small satellite launch monopoly growing fast."},
            {"ticker":"ACHR","company":"Archer Aviation","sector":"eVTOL","risk":"Very High","reason":"Air taxi with DOD contracts and United Airlines partnership."},
            {"ticker":"ALAB","company":"Astera Labs","sector":"AI Connectivity","risk":"High","reason":"AI data centre connectivity chips."},
            {"ticker":"APLD","company":"Applied Digital","sector":"AI Infrastructure","risk":"Very High","reason":"HPC data centres for AI training."},
            {"ticker":"VRT","company":"Vertiv Holdings","sector":"Data Centre Infra","risk":"Medium","reason":"Power and cooling for AI data centres."},
            {"ticker":"GEV","company":"GE Vernova","sector":"Energy/Grid","risk":"Medium","reason":"Grid infrastructure critical for AI power demand."},
            {"ticker":"CEG","company":"Constellation Energy","sector":"Nuclear Energy","risk":"Medium","reason":"Nuclear power deals for AI data centres."},
            {"ticker":"OKLO","company":"Oklo Inc","sector":"Small Nuclear","risk":"Very High","reason":"Small modular reactor for AI power needs."},
            {"ticker":"LUNR","company":"Intuitive Machines","sector":"Space","risk":"Very High","reason":"Lunar lander contracts with NASA."},
            {"ticker":"JOBY","company":"Joby Aviation","sector":"eVTOL","risk":"Very High","reason":"Air taxi with Toyota investment and FAA progress."},
        ]
    }

    # ── DAILY ROTATION ─────────────────────────────────────────────
    # Use date as seed so stocks rotate daily but are consistent within a day
    from datetime import date
    day_seed = int(date.today().strftime("%Y%m%d"))

    def pick_stocks(pool, n=10):
        """Pick n stocks from pool, rotated daily"""
        rng = random.Random(day_seed + hash(str(pool[0])) % 1000)
        shuffled = pool.copy()
        rng.shuffle(shuffled)
        return shuffled[:n]

    selected = {tf: pick_stocks(stocks) for tf, stocks in UNIVERSE.items()}

    # ── PARALLEL ENRICHMENT ────────────────────────────────────────
    all_tickers = list({s["ticker"] for stocks in selected.values() for s in stocks})

    def enrich_one(ticker):
        """Fetch quote + check indicator cache"""
        q = get_quote(ticker)
        ind = _ind_cache.get(ticker, {})
        return ticker, q, ind

    # Fetch all quotes in parallel
    quote_data = {}
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(enrich_one, t): t for t in all_tickers}
            for f in concurrent.futures.as_completed(futures, timeout=15):
                try:
                    ticker, q, ind = f.result()
                    quote_data[ticker] = {"quote": q, "ind": ind}
                except: pass
    except Exception as e:
        print(f"Suggestions enrich error: {e}")

    def build_suggestion(s, tf):
        ticker = s["ticker"]
        data   = quote_data.get(ticker, {})
        q      = data.get("quote", {})
        ind    = data.get("ind", {})

        # Owned badge
        owned = ticker in _bg_symbols

        # Past performance from live quote
        dp = float(q.get("dp", 0) or 0)
        perf = {
            "1d": round(dp, 1),
            "1w": 0, "1m": 0, "1y": 0
        }

        # Target forecast (simple signal-based)
        signal = ind.get("signal", "Neutral") if ind else "Neutral"
        mult = 1 if "Bullish" in signal else -1 if "Bearish" in signal else 0
        risk_mult = {"Low":0.5,"Medium":1.0,"High":1.5,"Very High":2.5}.get(s.get("risk","Medium"),1.0)
        tf_mult   = {"1day":0.5,"1week":1.5,"1month":4,"1year":15}.get(tf,1)
        base_fc   = round(mult * risk_mult * tf_mult * (0.5 + random.Random(day_seed+hash(ticker)).random()), 1)

        forecast = {
            "1d": round(base_fc * 0.1, 1),
            "1w": round(base_fc * 0.3, 1),
            "1m": round(base_fc * 1.0, 1),
            "1y": round(base_fc * 4.0, 1),
        }

        # Live notes
        live_notes = []
        if ind.get("rsi"):
            rsi = ind["rsi"]
            if rsi < 30:   live_notes.append(f"RSI oversold ({rsi:.0f})")
            elif rsi > 70: live_notes.append(f"RSI overbought ({rsi:.0f})")
        if ind.get("macd") and ind.get("macd_signal"):
            if ind["macd"] > ind["macd_signal"]: live_notes.append("MACD bullish")
            else:                                 live_notes.append("MACD bearish")
        if ind.get("ma50") and ind.get("ma200"):
            if ind["ma50"] > ind["ma200"]: live_notes.append("Golden Cross")
            else:                           live_notes.append("Death Cross")

        return {
            "ticker":       ticker,
            "company":      s["company"],
            "sector":       s["sector"],
            "risk":         s["risk"],
            "reason":       s["reason"],
            "owned":        owned,
            "currentPrice": round(q.get("c", 0) or 0, 2),
            "dayChange":    round(q.get("dp", 0) or 0, 2),
            "signal":       signal,
            "liveNote":     " | ".join(live_notes) if live_notes else "",
            "perf":         perf,
            "forecast":     forecast,
        }

    result = {}
    for tf, stocks in selected.items():
        result[tf] = [build_suggestion(s, tf) for s in stocks]

    # Cache for 1 hour
    _market_cache[cache_key] = {"data": result, "ts": time.time()}
    return jsonify(result)


@app.route('/api/watchlist/user', methods=['GET'])
@require_login
def get_user_watchlist():
    user = get_user(current_user())
    return jsonify(user.get('watchlist', []) if user else [])

@app.route('/api/watchlist/user', methods=['POST'])
@require_login
def save_user_watchlist():
    data = request.json or {}
    wl = data.get('watchlist', [])
    users = load_users()
    username = current_user()
    if username in users:
        users[username]['watchlist'] = wl
        save_users(users)
    return jsonify({'success': True})

@app.route('/api/market/indices')
@require_login  
def api_market_indices():
    """Fetch market data from multiple sources - Yahoo Finance primary, Stooq for global"""
    import concurrent.futures
    if cache_valid(_market_cache.get("data"), MARKET_TTL):
        return jsonify(_market_cache["data"])

    # Yahoo Finance symbols for each category
    MARKET_SYMS = {
        "us_indices": [
            {"key":"^GSPC",  "yf":"^GSPC",  "name":"S&P 500",       "currency":"USD","type":"index"},
            {"key":"^IXIC",  "yf":"^IXIC",  "name":"NASDAQ",         "currency":"USD","type":"index"},
            {"key":"^DJI",   "yf":"^DJI",   "name":"Dow Jones",      "currency":"USD","type":"index"},
            {"key":"^RUT",   "yf":"^RUT",   "name":"Russell 2000",   "currency":"USD","type":"index"},
            {"key":"^VIX",   "yf":"^VIX",   "name":"VIX Fear Index", "currency":"USD","type":"index"},
        ],
        "us_stocks": [
            {"key":"AAPL",   "yf":"AAPL",   "name":"Apple",          "currency":"USD","type":"stock"},
            {"key":"MSFT",   "yf":"MSFT",   "name":"Microsoft",      "currency":"USD","type":"stock"},
            {"key":"NVDA",   "yf":"NVDA",   "name":"NVIDIA",         "currency":"USD","type":"stock"},
            {"key":"AMZN",   "yf":"AMZN",   "name":"Amazon",         "currency":"USD","type":"stock"},
            {"key":"GOOGL",  "yf":"GOOGL",  "name":"Alphabet",       "currency":"USD","type":"stock"},
            {"key":"META",   "yf":"META",   "name":"Meta",           "currency":"USD","type":"stock"},
            {"key":"TSLA",   "yf":"TSLA",   "name":"Tesla",          "currency":"USD","type":"stock"},
            {"key":"AMD",    "yf":"AMD",    "name":"AMD",            "currency":"USD","type":"stock"},
        ],
        "uk_indices": [
            {"key":"^FTSE",  "yf":"^FTSE",  "name":"FTSE 100",       "currency":"GBP","type":"index"},
            {"key":"^FTMC",  "yf":"^FTMC",  "name":"FTSE 250",       "currency":"GBP","type":"index"},
        ],
        "uk_stocks": [
            {"key":"HSBA.L", "yf":"HSBA.L", "name":"HSBC",           "currency":"GBP","type":"stock"},
            {"key":"BP.L",   "yf":"BP.L",   "name":"BP",             "currency":"GBP","type":"stock"},
            {"key":"SHEL.L", "yf":"SHEL.L", "name":"Shell",          "currency":"GBP","type":"stock"},
            {"key":"AZN.L",  "yf":"AZN.L",  "name":"AstraZeneca",    "currency":"GBP","type":"stock"},
            {"key":"RIO.L",  "yf":"RIO.L",  "name":"Rio Tinto",      "currency":"GBP","type":"stock"},
        ],
        "india_indices": [
            {"key":"^BSESN", "yf":"^BSESN", "name":"BSE Sensex",     "currency":"INR","type":"index"},
            {"key":"^NSEI",  "yf":"^NSEI",  "name":"Nifty 50",       "currency":"INR","type":"index"},
        ],
        "india_stocks": [
            {"key":"TCS.NS", "yf":"TCS.NS", "name":"TCS",            "currency":"INR","type":"stock"},
            {"key":"RELIANCE.NS","yf":"RELIANCE.NS","name":"Reliance","currency":"INR","type":"stock"},
            {"key":"INFY.NS","yf":"INFY.NS","name":"Infosys",         "currency":"INR","type":"stock"},
            {"key":"HDFCBANK.NS","yf":"HDFCBANK.NS","name":"HDFC Bank","currency":"INR","type":"stock"},
        ],
        "europe_indices": [
            {"key":"^STOXX50E","yf":"^STOXX50E","name":"Euro Stoxx 50","currency":"EUR","type":"index"},
            {"key":"^GDAXI",  "yf":"^GDAXI",  "name":"DAX Germany",  "currency":"EUR","type":"index"},
            {"key":"^FCHI",   "yf":"^FCHI",   "name":"CAC 40 France","currency":"EUR","type":"index"},
            {"key":"^IBEX",   "yf":"^IBEX",   "name":"IBEX 35 Spain","currency":"EUR","type":"index"},
        ],
        "asia_indices": [
            {"key":"^N225",   "yf":"^N225",   "name":"Nikkei 225",   "currency":"JPY","type":"index"},
            {"key":"^HSI",    "yf":"^HSI",    "name":"Hang Seng",    "currency":"HKD","type":"index"},
            {"key":"000001.SS","yf":"000001.SS","name":"Shanghai Comp","currency":"CNY","type":"index"},
            {"key":"^KS11",   "yf":"^KS11",   "name":"KOSPI Korea",  "currency":"KRW","type":"index"},
        ],
        "crypto": [
            {"key":"BTC-USD", "yf":"BTC-USD", "name":"Bitcoin",      "currency":"USD","type":"crypto"},
            {"key":"ETH-USD", "yf":"ETH-USD", "name":"Ethereum",     "currency":"USD","type":"crypto"},
            {"key":"SOL-USD", "yf":"SOL-USD", "name":"Solana",       "currency":"USD","type":"crypto"},
            {"key":"BNB-USD", "yf":"BNB-USD", "name":"Binance Coin", "currency":"USD","type":"crypto"},
            {"key":"XRP-USD", "yf":"XRP-USD", "name":"XRP",          "currency":"USD","type":"crypto"},
        ],
        "commodities": [
            {"key":"GC=F",    "yf":"GC=F",    "name":"Gold ($/oz)",  "currency":"USD","type":"commodity"},
            {"key":"SI=F",    "yf":"SI=F",    "name":"Silver ($/oz)","currency":"USD","type":"commodity"},
            {"key":"CL=F",    "yf":"CL=F",    "name":"Crude Oil WTI","currency":"USD","type":"commodity"},
            {"key":"BZ=F",    "yf":"BZ=F",    "name":"Brent Crude",  "currency":"USD","type":"commodity"},
            {"key":"NG=F",    "yf":"NG=F",    "name":"Natural Gas",  "currency":"USD","type":"commodity"},
            {"key":"HG=F",    "yf":"HG=F",    "name":"Copper",       "currency":"USD","type":"commodity"},
        ],
        "forex": [
            {"key":"GBPUSD=X","yf":"GBPUSD=X","name":"GBP/USD",     "currency":"","type":"forex"},
            {"key":"EURUSD=X","yf":"EURUSD=X","name":"EUR/USD",     "currency":"","type":"forex"},
            {"key":"USDJPY=X","yf":"USDJPY=X","name":"USD/JPY",     "currency":"","type":"forex"},
            {"key":"GBPEUR=X","yf":"GBPEUR=X","name":"GBP/EUR",     "currency":"","type":"forex"},
            {"key":"USDINR=X","yf":"USDINR=X","name":"USD/INR",     "currency":"","type":"forex"},
            {"key":"GBPINR=X","yf":"GBPINR=X","name":"GBP/INR",     "currency":"","type":"forex"},
            {"key":"AUDUSD=X","yf":"AUDUSD=X","name":"AUD/USD",     "currency":"","type":"forex"},
            {"key":"USDCHF=X","yf":"USDCHF=X","name":"USD/CHF",     "currency":"","type":"forex"},
            {"key":"USDCAD=X","yf":"USDCAD=X","name":"USD/CAD",     "currency":"","type":"forex"},
        ],
    }

    def fetch_one(item):
        key   = item.get("yf") or item.get("key","")
        itype = item.get("type","stock")
        if not key: return {**item, "price":None, "change":0, "changePct":0}
        try:
            q = yf_quote(key)
            if q and q.get("c") and float(q.get("c",0)) > 0:
                dec = 4 if itype in ("forex","crypto") else 2
                price = float(q["c"])
                return {**item,
                    "price":     round(price, dec if price < 100 else 2),
                    "change":    round(float(q.get("d",0)),4),
                    "changePct": round(float(q.get("dp",0)),2),
                }
        except Exception as e:
            print(f"Market fetch error {key}: {e}")
        return {**item, "price":None, "change":0, "changePct":0}

    result = {k: [] for k in MARKET_SYMS}
    all_items = [(k, item) for k, items in MARKET_SYMS.items() for item in items]

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
            futures = {ex.submit(fetch_one, item): (k, item) for k,item in all_items}
            for f in concurrent.futures.as_completed(futures, timeout=30):
                k, item = futures[f]
                try:
                    r = f.result()
                    result[k].append(r)
                except:
                    result[k].append({**item, "price":None, "change":0, "changePct":0})
    except Exception as e:
        print(f"Market indices error: {e}")

    _market_cache["data"] = result
    _market_cache["ts"]   = time.time()
    return jsonify(result)


@app.route('/api/portfolio/quick')
@require_login
def api_portfolio_quick():
    """Return cached portfolio instantly if available"""
    username = current_user()
    cache_key = f"portfolio_{username}"
    if _portfolio_cache.get(cache_key):
        return jsonify({**_portfolio_cache[cache_key]['data'], "cached": True})
    return jsonify({"accounts": [], "summaries": [], "cached": False})

@app.route('/api/indicators/batch', methods=['POST'])
@require_login
def api_indicators_batch():
    """Fetch indicators for multiple symbols in parallel - main endpoint for dashboard"""
    import concurrent.futures
    data = request.json or {}
    symbols = data.get('symbols', [])[:50]
    if not symbols:
        return jsonify({})
    print(f"Batch indicators: fetching {len(symbols)} symbols: {symbols[:5]}...")

    result = {}

    def fetch_one(sym):
        try:
            return sym, get_indicators(sym)
        except Exception as e:
            print(f"Batch ind error {sym}: {e}")
            return sym, {}

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(fetch_one, sym): sym for sym in symbols}
            for f in concurrent.futures.as_completed(futures, timeout=30):
                try:
                    sym, ind = f.result()
                    if ind: result[sym] = ind
                except: pass
    except Exception as e:
        print(f"Batch indicators error: {e}")

    return jsonify(result)

@app.route('/api/analyst/<symbol>')
@require_login
def api_analyst(symbol):
    """Analyst ratings from TipRanks + FMP"""
    sym = clean_symbol(symbol)
    result = {}
    # Try TipRanks first (unofficial)
    tr = tipranks_data(sym)
    if tr: result['tipranks'] = tr
    # Try FMP
    fmp = fmp_analyst(sym)
    if fmp: result['fmp'] = fmp
    # Get StockTwits sentiment
    st = stocktwits_sentiment(sym)
    if st: result['stocktwits'] = st
    # Get fundamentals
    fund = fmp_fundamentals(sym)
    if fund: result['fundamentals'] = fund
    return jsonify(result)

@app.route('/api/macro')
@require_login
def api_macro():
    """FRED macro economic indicators"""
    if cache_valid(_fred_cache.get('macro'), 3600):
        return jsonify(_fred_cache['macro']['data'])
    data = get_macro_data()
    fg = fear_greed_index()
    result = {'macro': data, 'fearGreed': fg}
    _fred_cache['macro'] = {'data': result, 'ts': time.time()}
    return jsonify(result)

@app.route('/api/sentiment/<symbol>')
@require_login
def api_sentiment(symbol):
    """Social sentiment from StockTwits"""
    sym = clean_symbol(symbol)
    return jsonify(stocktwits_sentiment(sym))

@app.route('/api/cache/status')
@require_login
def api_cache_status():
    user_count = len(load_users())
    return jsonify({
        "candles":len(_candle_cache),"indicators":len(_ind_cache),
        "profiles":len(_profile_cache),"quotes":len(_quote_cache),
        "bg_symbols":len(_bg_symbols),
        "users":user_count,
        "db_connected":bool(DATABASE_URL),
        "finnhub_configured":bool(os.environ.get('FINNHUB_KEY','')),
        "twelvedata_configured":bool(TWELVE_KEY)
    })

@app.route('/api/news/<symbol>')
@require_login
def api_news(symbol):
    """Get news from all sources for a symbol"""
    sym = clean_symbol(symbol)
    cache_key = f"news_{sym}"
    if cache_valid(_news_cache.get(cache_key), 1800):
        return jsonify(_news_cache[cache_key]['data'])

    # Get company name for better news search
    profile = _profile_cache.get(sym, {})
    company = profile.get('name','')

    news = get_all_news(sym, company_name=company, limit=15)
    _news_cache[cache_key] = {'data': news, 'ts': time.time()}
    return jsonify(news)

# ── STARTUP ───────────────────────────────────────────────────────────
import threading as _threading
_bg_thread = _threading.Thread(target=background_prefetch, daemon=True)
_bg_thread.start()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
