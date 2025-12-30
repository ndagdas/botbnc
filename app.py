from flask import Flask, request, jsonify
from binance.client import Client

app = Flask(__name__)

# pozisyon takibi (symbol bazlı)
positions = {}

def get_client(api_key, secret_key):
    client = Client(api_key, secret_key)
    client.FUTURES_URL = "https://testnet.binancefuture.com"
    return client

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    signal = data.get("signal")
    symbol = data.get("symbol")
    api_key = data.get("apiKey")
    secret_key = data.get("secretKey")

    client = get_client(api_key, secret_key)

    # ===== BUY =====
    if signal == "BUY":
        qty = float(data.get("qty"))

        client.futures_create_order(
            symbol=symbol,
            side="BUY",
            type="MARKET",
            quantity=qty
        )

        positions[symbol] = qty
        return jsonify({"status": "LONG OPENED", "qty": qty})

    # ===== TP1 %50 =====
    if signal == "TP1" and symbol in positions:
        sell_qty = round(positions[symbol] * 0.5, 3)

        client.futures_create_order(
            symbol=symbol,
            side="SELL",
            type="MARKET",
            quantity=sell_qty
        )

        positions[symbol] -= sell_qty
        return jsonify({"status": "TP1 SOLD", "qty": sell_qty})

    # ===== TP2 %30 =====
    if signal == "TP2" and symbol in positions:
        sell_qty = round(positions[symbol] * 0.3, 3)

        client.futures_create_order(
            symbol=symbol,
            side="SELL",
            type="MARKET",
            quantity=sell_qty
        )

        positions[symbol] -= sell_qty
        return jsonify({"status": "TP2 SOLD", "qty": sell_qty})

    # ===== STOP %100 =====
    if signal == "STOP" and symbol in positions:
        client.futures_create_order(
            symbol=symbol,
            side="SELL",
            type="MARKET",
            quantity=positions[symbol]
        )

        positions.pop(symbol)
        return jsonify({"status": "POSITION CLOSED"})

    return jsonify({"status": "NO ACTION"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
