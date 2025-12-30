from flask import Flask, request
import json
import pandas as pd
import ccxt

app = Flask(__name__)

longPozisyonda = False
pozisyondami = False

@app.route("/webhook", methods=["POST"])
def webhook():
    global longPozisyonda, pozisyondami

    try:
        data = json.loads(request.data)
        print("Gelen Webhook:", data)

        # ===== JSON'DAN GELENLER =====
        ticker = data.get("ticker", "")
        side = data.get("side", "")
        price = float(data.get("price", 0))
        quantity_usdt = float(data.get("quantity", 0))

        api_key = data.get("binanceApiKey", "")
        api_secret = data.get("binanceSecretKey", "")

        # BTCUSDT.P -> BTCUSDT
        symbol = ticker.split(".")[0]

        # ===== CCXT BINANCE FUTURES TESTNET =====
        exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "future",
                "adjustForTimeDifference": True
            }
        })

        # 🔴 TESTNET AKTİF
        exchange.set_sandbox_mode(True)

        # ===== POZİSYON KONTROL =====
        balance = exchange.fetch_balance()
        positions = balance["info"]["positions"]

        current_pos = [
            p for p in positions
            if p["symbol"] == symbol and float(p["positionAmt"]) != 0
        ]

        if current_pos:
            pozisyondami = True
            pos_amt = float(current_pos[0]["positionAmt"])
            longPozisyonda = pos_amt > 0
        else:
            pozisyondami = False
            longPozisyonda = False

        print(f"Side: {side}, Symbol: {symbol}, Pozisyon: {pozisyondami}")

        # ================= BUY =================
        if side == "BUY" and not pozisyondami:
            qty = quantity_usdt / price
            order = exchange.create_market_buy_order(symbol, qty)
            print("BUY OK:", order)

        # ================= TP1 %50 =================
        if side == "TP1" and pozisyondami and longPozisyonda:
            pos_qty = abs(float(current_pos[0]["positionAmt"]))
            sell_qty = pos_qty * 0.50

            order = exchange.create_market_sell_order(
                symbol,
                sell_qty,
                {"reduceOnly": True}
            )
            print("TP1 OK:", order)

        # ================= TP2 %30 =================
        if side == "TP2" and pozisyondami and longPozisyonda:
            pos_qty = abs(float(current_pos[0]["positionAmt"]))
            sell_qty = pos_qty * 0.30

            order = exchange.create_market_sell_order(
                symbol,
                sell_qty,
                {"reduceOnly": True}
            )
            print("TP2 OK:", order)

        # ================= STOP %100 =================
        if side == "STOP" and pozisyondami and longPozisyonda:
            pos_qty = abs(float(current_pos[0]["positionAmt"]))

            order = exchange.create_market_sell_order(
                symbol,
                pos_qty,
                {"reduceOnly": True}
            )
            print("STOP OK:", order)

        return {"status": "success"}

    except Exception as e:
        print("HATA:", str(e))
        return {"status": "error", "message": str(e)}, 500
