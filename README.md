# tradingassist-ntv01
FilePurposeapp.pyPython backend — connects Trading212 + Finnhub APIsindex.htmlFull dashboard frontend — dark theme, all sectionsrequirements.txtPython libraries for Render to installrender.yamlTells Render how to deploy your appmanifest.jsonPWA config — enables Add to Home Screensw.jsService worker — makes it work like a native app


# TradingAssist-NTv0.1

A personal AI-powered trading dashboard that connects to Trading212, 
providing live portfolio analysis, technical signals, news and AI stock suggestions.

## Features
- Live portfolio view — ISA and Invest accounts separately
- Bullish/Bearish signals with RSI, MACD, Bollinger Bands
- AI stock suggestions with risk labels and timeframe targets
- Financial news feed per stock
- Earnings calendar
- Watchlist with manual add/remove
- PWA — works on Mac and phone

## Tech Stack
- Backend: Python (Flask)
- Frontend: HTML/CSS/JS
- APIs: Trading212, Finnhub
- Hosting: Render.com

## Setup
Configure these environment variables in Render:
- ISA_AUTH — Base64 encoded ISA API key:secret
- INVEST_AUTH — Base64 encoded Invest API key:secret  
- FINNHUB_KEY — Finnhub API key

## Version
v0.2 — Live web app (in progress)
