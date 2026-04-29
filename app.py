import os
import time
import json
import threading
import logging
from datetime import datetime
from queue import Queue

from flask import Flask, request, jsonify
import ccxt

# ---------------------------------
# CONFIG
# ---------------------------------
RISK_PERCENT = 1.0       # %1 risk
ACCOUNT_BALANCE = 1000   # USDT
RETRY_COUNT = 3
RETRY_DELAY = 0.5
DUPLICATE_WINDOW = 2

# ---------------------------------
# LOG
# ---------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("engine")

# ---------------------------------
# APP
# ---------------------------------
app = Flask(__name__)
signal_queue = Queue()
processed_signals = {}

def normalize_symbol(ticker: str):
    ticker = ticker.replace(".P", "").upper()

    if ticker.endswith("USDTUSDT"):
        return ticker.replace("USDTUSDT", "USDT")

    if ticker.endswith("USDT"):
        return ticker

    return ticker + "USDT"
# ---------------------------------
# PARSE
# ---------------------------------
def parse_data(data):
    return {
        "id": data.get("id"),
        "symbol": data.get("ticker", "").replace(".P", ""),
        "side": data.get("side"),
        "price": float(data.get("price", 0)),
        "stopPrice": float(data.get("stopPrice", 0)),
        "usdt": float(data.get("quantity", 100)),
        "api_key": data.get("binanceApiKey"),
        "secret": data.get("binanceSecretKey"),
        "testnet": data.get("testnet", True),
        "positionSide": data.get("positionSide", "LONG")
    }

# ---------------------------------
# DUPLICATE
# ---------------------------------
def is_duplicate(signal_id):
    now = time.time()
    if signal_id in processed_signals:
        if now - processed_signals[signal_id] < DUPLICATE_WINDOW:
            return True
    processed_signals[signal_id] = now
    return False

# ---------------------------------
# BINANCE
# ---------------------------------
def get_exchange(api, secret, testnet):
    exchange = ccxt.binance({
        "apiKey": api,
        "secret": secret,
        "options": {"defaultType": "future"},
        "enableRateLimit": True
    })
    if testnet:
        exchange.set_sandbox_mode(True)
    exchange.load_markets()
    return exchange

# ---------------------------------
# POSITION
# ---------------------------------
def get_position(exchange, symbol):
    positions = exchange.fetch_positions([symbol])
    for p in positions:
        amt = float(p.get("contracts", 0))
        if amt != 0:
            return abs(amt)
    return 0

# ---------------------------------
# QTY CALC (HYBRID)
# ---------------------------------
def calculate_qty(price, stop, usdt, balance):

    # sabit USDT
    qty_usdt = usdt / price

    # risk bazlı
    if stop > 0:
        price_risk = abs(price - stop)
        risk_amount = balance * (RISK_PERCENT / 100)
        qty_risk = risk_amount / price_risk
    else:
        qty_risk = qty_usdt

    return min(qty_usdt, qty_risk)

# ---------------------------------
# EXECUTE
# ---------------------------------
def execute(exchange, d):

    symbol = d["symbol"]

    if symbol not in exchange.markets:
        logger.warning(f"INVALID SYMBOL: {symbol}")
        return

    price = d["price"]
    if price <= 0:
        ticker = exchange.fetch_ticker(symbol)
        price = ticker["last"]

    pos_amt = get_position(exchange, symbol)

    # ENTRY
    if d["side"] == "BUY":
        qty = calculate_qty(price, d["stopPrice"], d["usdt"], ACCOUNT_BALANCE)

        market = exchange.market(symbol)
        min_qty = market["limits"]["amount"]["min"]
        precision = market["precision"]["amount"]

        qty = max(qty, min_qty)
        qty = round(qty, precision)

        exchange.create_market_buy_order(symbol, qty)
        logger.info(f"BUY {symbol} qty={qty}")

    # TP1
    elif d["side"] == "TP1":
        if pos_amt == 0:
            return
        exchange.create_market_order(symbol, "SELL", pos_amt * 0.5, params={"reduceOnly": True})

    # TP2
    elif d["side"] == "TP2":
        if pos_amt == 0:
            return
        exchange.create_market_order(symbol, "SELL", pos_amt * 0.3, params={"reduceOnly": True})

    # STOP
    elif d["side"] == "STOP":
        if pos_amt == 0:
            return
        exchange.create_market_order(symbol, "SELL", pos_amt, params={"reduceOnly": True})

# ---------------------------------
# PROCESS
# ---------------------------------
def process_signal(data):

    d = parse_data(data)

    if is_duplicate(d["id"]):
        return

    for i in range(RETRY_COUNT):
        try:
            exchange = get_exchange(d["api_key"], d["secret"], d["testnet"])
            execute(exchange, d)
            return
        except Exception as e:
            logger.error(f"Retry {i+1}: {e}")
            time.sleep(RETRY_DELAY)

    logger.error(f"FAILED: {d}")

# ---------------------------------
# WORKER
# ---------------------------------
def worker():
    while True:
        data = signal_queue.get()
        process_signal(data)

threading.Thread(target=worker, daemon=True).start()

# ---------------------------------
# WEBHOOK
# ---------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    signal_queue.put(data)
    return jsonify({"status": "queued"})

# ---------------------------------
# RUN
# ---------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
