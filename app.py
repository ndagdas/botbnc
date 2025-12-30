from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import math

app = Flask(__name__)

def get_client(api_key, api_secret):
    client = Client(api_key, api_secret)
    client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
    return client

def round_qty(qty, step):
    return math.floor(qty / step) * step

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    symbol = data["symbol"]
    quantity = float(data["quantity"])
    tp1 = float(data["tp1"])
    tp2 = float(data["tp2"])
    stop = float(data["stop"])
    api_key = data["api_key"]
    api_secret = data["api_secret"]

    client = get_client(api_key, api_secret)

    # LOT SIZE bilgisi
    exchange_info = client.futures_exchange_info()
    for s in exchange_info["symbols"]:
        if s["symbol"] == symbol:
            step_size = float(s["filters"][1]["stepSize"])

    quantity = round_qty(quantity, step_size)

    # === MARKET BUY (LONG) ===
    client.futures_create_order(
        symbol=symbol,
        side=SIDE_BUY,
        type=ORDER_TYPE_MARKET,
        quantity=quantity
    )

    # === TP1 %50 ===
    client.futures_create_order(
        symbol=symbol,
        side=SIDE_SELL,
        type=ORDER_TYPE_LIMIT,
        price=tp1,
        quantity=round_qty(quantity * 0.5, step_size),
        timeInForce=TIME_IN_FORCE_GTC,
        reduceOnly=True
    )

    # === TP2 %30 ===
    client.futures_create_order(
        symbol=symbol,
        side=SIDE_SELL,
        type=ORDER_TYPE_LIMIT,
        price=tp2,
        quantity=round_qty(quantity * 0.3, step_size),
        timeInForce=TIME_IN_FORCE_GTC,
        reduceOnly=True
    )

    # === STOP %100 ===
    client.futures_create_order(
        symbol=symbol,
        side=SIDE_SELL,
        type=FUTURE_ORDER_TYPE_STOP_MARKET,
        stopPrice=stop,
        closePosition=True
    )

    return jsonify({"status": "OK"}), 200

if __name__ == "__main__":
    app.run()
