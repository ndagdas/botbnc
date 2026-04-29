```python
from flask import Flask, request, jsonify
import json
import ccxt
import time
import threading
from queue import Queue

# ---------------------------------
# CONFIG
# ---------------------------------
RETRY = 3
DELAY = 0.5
DUPLICATE_WINDOW = 2

processed = {}
q = Queue()

app = Flask(__name__)

# ---------------------------------
# DUPLICATE
# ---------------------------------
def is_duplicate(signal_id):
    now = time.time()
    if signal_id in processed:
        if now - processed[signal_id] < DUPLICATE_WINDOW:
            return True
    processed[signal_id] = now
    return False

# ---------------------------------
# WORKER
# ---------------------------------
def worker():
    while True:
        data = q.get()
        process(data)

threading.Thread(target=worker, daemon=True).start()

# ---------------------------------
# PROCESS
# ---------------------------------
def process(data):

    for i in range(RETRY):
        try:
            ticker = data.get('ticker', '')
            symbol = ticker.split(".")[0]

            price = float(data.get('price', 0))
            side = data.get('side', '')
            usdt = float(data.get('quantity', 100))
            signal_id = data.get("id", str(time.time()))

            api = data.get('binanceApiKey')
            secret = data.get('binanceSecretKey')

            if is_duplicate(signal_id):
                return

            exchange = ccxt.binance({
                'apiKey': api,
                'secret': secret,
                'options': {'defaultType': 'future'},
                'enableRateLimit': True
            })

            balance = exchange.fetch_balance()
            positions = balance['info']['positions']

            pos_amt = 0
            for p in positions:
                if p['symbol'] == symbol:
                    pos_amt = float(p['positionAmt'])

            # fiyat yoksa çek
            if price <= 0:
                ticker_data = exchange.fetch_ticker(symbol)
                price = ticker_data['last']

            qty = usdt / price

            # ---------------- ENTRY ----------------
            if side == "BUY":
                if pos_amt < 0:
                    exchange.create_market_buy_order(symbol, abs(pos_amt), {"reduceOnly": True})

                exchange.create_market_buy_order(symbol, qty)
                print(f"BUY {symbol} {qty}")

            elif side == "SELL":
                if pos_amt > 0:
                    exchange.create_market_sell_order(symbol, pos_amt, {"reduceOnly": True})

                exchange.create_market_sell_order(symbol, qty)
                print(f"SELL {symbol} {qty}")

            # ---------------- TP ----------------
            elif side == "TP1":
                if pos_amt > 0:
                    exchange.create_market_sell_order(symbol, pos_amt * 0.5, {"reduceOnly": True})

            elif side == "TP2":
                if pos_amt > 0:
                    exchange.create_market_sell_order(symbol, pos_amt * 0.3, {"reduceOnly": True})

            # ---------------- STOP ----------------
            elif side == "STOP":
                if pos_amt > 0:
                    exchange.create_market_sell_order(symbol, pos_amt, {"reduceOnly": True})
                elif pos_amt < 0:
                    exchange.create_market_buy_order(symbol, abs(pos_amt), {"reduceOnly": True})

            return

        except Exception as e:
            print(f"Retry {i+1}: {e}")
            time.sleep(DELAY)

    print("FAILED:", data)

# ---------------------------------
# WEBHOOK
# ---------------------------------
@app.route("/webhook", methods=['POST'])
def webhook():
    data = request.get_json()
    q.put(data)
    return jsonify({"status": "queued"})

# ---------------------------------
# RUN
# ---------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```
