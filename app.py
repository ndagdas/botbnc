from flask import Flask, request
import json
import ccxt

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():

    try:
        data = request.json

        ticker = data["ticker"]
        symbol = ticker.split(".")[0]
        price = float(data["price"])
        side = data["side"]
        quantity = float(data["quantity"])
        api_key = data["binanceApiKey"]
        api_secret = data["binanceSecretKey"]

        # Binance Future bağlan
        exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "options": {
                "defaultType": "future"
            },
            "enableRateLimit": True
        })

        # ---- 1) Pozisyon kontrolü (FAST + AZ WEIGHT) ----
        positions = exchange.fetch_positions([symbol])

        longPos = 0
        shortPos = 0

        for pos in positions:
            amt = float(pos["contracts"])
            if amt > 0:
                longPos = amt
            elif amt < 0:
                shortPos = abs(amt)

        # ---- 2) İşlemleri yönet ----

        # LONG AÇ
        if side == "BUY":
            # varsa SHORT kapat
            if shortPos > 0:
                exchange.create_order(symbol, "market", "buy", shortPos, params={"reduceOnly": True})

            # yeni long aç
            amount = quantity / price
            exchange.create_order(symbol, "market", "buy", amount)

        # SHORT AÇ
        elif side == "SELL":
            # varsa LONG kapat
            if longPos > 0:
                exchange.create_order(symbol, "market", "sell", longPos, params={"reduceOnly": True})

            # yeni short aç
            amount = quantity / price
            exchange.create_order(symbol, "market", "sell", amount)

        # STOP → Pozisyon kapatma
        elif side == "STOP":
            if longPos > 0:
                exchange.create_order(symbol, "market", "sell", longPos, params={"reduceOnly": True})
            if shortPos > 0:
                exchange.create_order(symbol, "market", "buy", shortPos, params={"reduceOnly": True})

        # KAR → Yarım kapatma
        elif side == "KAR":
            half_amount = (quantity / price) / 2

            if longPos > 0:
                exchange.create_order(symbol, "market", "sell", half_amount)
                
            if shortPos > 0:
                exchange.create_order(symbol, "market", "buy", half_amount)

        return {"status": "OK"}

    except Exception as e:
        print("HATA:", e)
        return {"status": "ERROR", "message": str(e)}

