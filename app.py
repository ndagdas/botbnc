import os
import time
import json
import logging
import threading
from datetime import datetime
from queue import Queue, Empty

from flask import Flask, request, jsonify
import ccxt

# ================================
# LOG
# ================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("engine")

# ================================
# CONFIG
# ================================
RETRY_COUNT = 3
RETRY_DELAY = 0.5
WORKER_COUNT = int(os.environ.get("WORKERS", 3))
DUPLICATE_TTL = 10

USE_REDIS = False
REDIS_URL = os.environ.get("REDIS_URL")

# ================================
# OPTIONAL REDIS
# ================================
redis_client = None
if REDIS_URL:
    try:
        import redis
        redis_client = redis.from_url(REDIS_URL)
        USE_REDIS = True
        logger.info("Redis Queue Aktif")
    except Exception as e:
        logger.warning("Redis bağlanamadı, memory queue kullanılacak")

# ================================
# QUEUE
# ================================
memory_queue = Queue()

def push_queue(data):
    if USE_REDIS:
        redis_client.rpush("signals", json.dumps(data))
    else:
        memory_queue.put(data)

def pop_queue():
    if USE_REDIS:
        item = redis_client.blpop("signals", timeout=1)
        if item:
            return json.loads(item[1])
        return None
    else:
        try:
            return memory_queue.get(timeout=1)
        except Empty:
            return None

# ================================
# APP
# ================================
app = Flask(__name__)

# ================================
# EXCHANGE CACHE
# ================================
exchange_cache = {}

def get_exchange(api_key, secret, testnet=False):
    key = f"{api_key}_{secret}_{testnet}"

    if key in exchange_cache:
        return exchange_cache[key]

    config = {
        "apiKey": api_key,
        "secret": secret,
        "options": {"defaultType": "future"},
        "enableRateLimit": True
    }

    if testnet:
        config["urls"] = {
            "api": {
                "public": "https://testnet.binancefuture.com/fapi/v1",
                "private": "https://testnet.binancefuture.com/fapi/v1"
            }
        }

    exchange = ccxt.binance(config)

    if testnet:
        exchange.set_sandbox_mode(True)

    exchange.load_markets()

    exchange_cache[key] = exchange
    return exchange

# ================================
# DUPLICATE (IDEMPOTENT)
# ================================
processed = {}

def is_duplicate(signal_id):
    now = time.time()

    if signal_id in processed:
        if now - processed[signal_id] < DUPLICATE_TTL:
            return True

    processed[signal_id] = now
    return False

# ================================
# PARSE
# ================================
def parse_data(data):
    return {
        "id": data.get("id") or f"{data.get('ticker')}_{data.get('side')}_{time.time()}",
        "symbol": data.get("ticker", "BTCUSDT").replace(".P", ""),
        "side": data.get("side", "").upper(),
        "price": float(data.get("price", 0)),
        "quantity_usdt": float(data.get("quantity", 100)),
        "api_key": data.get("binanceApiKey"),
        "secret": data.get("binanceSecretKey"),
        "positionSide": data.get("positionSide", "LONG"),
        "testnet": data.get("testnet", True)
    }

# ================================
# EXECUTE
# ================================
def execute(exchange, d, quantity):
    side = d["side"]
    symbol = d["symbol"]
    if not symbol.endswith("USDT"):
        symbol += "USDT"

    if side == "BUY":
        return exchange.create_market_buy_order(symbol, quantity)

    elif side == "SELL":
        return exchange.create_market_sell_order(symbol, quantity)

    elif side in ["TP1", "TP2", "SL", "STOP", "CLOSE", "CLOSE_ALL"]:

        close_side = "SELL" if d["positionSide"] == "LONG" else "BUY"

        return exchange.create_market_order(
            symbol=symbol,
            side=close_side,
            amount=quantity,
            params={"reduceOnly": True}
        )

# ================================
# PROCESS
# ================================
def process_signal(data):

    d = parse_data(data)

    if is_duplicate(d["id"]):
        logger.info(f"DUPLICATE: {d['id']}")
        return

    for attempt in range(RETRY_COUNT):
        try:
            exchange = get_exchange(d["api_key"], d["secret"], d["testnet"])

            price = d["price"]
            symbol = d["symbol"] if d["symbol"].endswith("USDT") else d["symbol"] + "USDT"

            if price <= 0:
                ticker = exchange.fetch_ticker(symbol)
                price = ticker["last"]

            quantity = d["quantity_usdt"]

            market = exchange.market(symbol)
            min_qty = market["limits"]["amount"]["min"]
            quantity = max(quantity, min_qty)

            quantity = round(quantity, 6)

            execute(exchange, d, quantity)

            logger.info(f"EXECUTED {d['side']} {symbol} qty={quantity}")
            return

        except Exception as e:
            logger.error(f"Retry {attempt+1}: {e}")
            time.sleep(RETRY_DELAY)

    logger.error(f"FAILED: {d}")

# ================================
# WORKER
# ================================
def worker():
    while True:
        data = pop_queue()
        if data:
            process_signal(data)

# worker başlat
for _ in range(WORKER_COUNT):
    threading.Thread(target=worker, daemon=True).start()

# ================================
# WEBHOOK
# ================================
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()

        push_queue(data)

        return jsonify({
            "status": "queued",
            "time": datetime.now().isoformat()
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================================
# HEALTH
# ================================
@app.route("/")
def home():
    return "ENGINE RUNNING"

# ================================
# RUN
# ================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
