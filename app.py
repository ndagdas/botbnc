from flask import Flask, request, jsonify
import ccxt
import requests

app = Flask(__name__)

# ==================================================
# BITGET DEMO (SANDBOX) AYARLARI
# ==================================================
bitget = ccxt.bitget({
    'apiKey': 'bg_ef42d04183294d11782767ded8b560dc',
    'secret': 'd883d8324fa83575bc4de104f9fc2ea229e3110e40d150d673408350b56769fe',
    'password': '93558287',
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap'  # USDT-M Futures
    }
})

bitget.set_sandbox_mode(True)  # ✅ DEMO MODE

# ==================================================
# TELEGRAM AYARLARI
# ==================================================
TELEGRAM_TOKEN = "8143581645:AAF5figZLC0p7oC6AzjBTGzTfWtOFCdHzRo"
CHAT_ID = "@gridsystem"

def telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": message
    })

# ==================================================
# YARDIMCI FONKSİYONLAR
# ==================================================
def tv_symbol_to_bitget(symbol: str) -> str:
    # BTCUSDT -> BTC/USDT:USDT
    return symbol.replace("USDT", "/USDT:USDT")

def usdt_to_amount(symbol: str, usdt: float) -> float:
    ticker = bitget.fetch_ticker(symbol)
    price = ticker["last"]
    amount = usdt / price
    return round(amount, 6)

def get_long_position(symbol: str):
    positions = bitget.fetch_positions([symbol])
    for p in positions:
        if p["side"] == "long" and float(p["contracts"]) > 0:
            return p
    return None

# ==================================================
# WEBHOOK
# ==================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    action = data.get("action")
    symbol = tv_symbol_to_bitget(data.get("symbol"))

    try:
        # ================= BUY (LONG ONLY) =================
        if action == "BUY":
            usdt = float(data["usdt"])
            amount = usdt_to_amount(symbol, usdt)

            # Aynı anda tek LONG (koruma)
            if get_long_position(symbol):
                return jsonify({"status": "already in position"})

            bitget.create_market_buy_order(symbol, amount)

            telegram(
                f"🟢 LONG AÇILDI\n"
                f"{symbol}\n"
                f"USDT: {usdt}\n"
                f"Miktar: {amount}"
            )

        # ================= TP1 %50 =================
        elif action == "TP1":
            pos = get_long_position(symbol)
            if pos:
                qty = float(pos["contracts"]) * 0.50
                bitget.create_market_sell_order(symbol, qty)

                telegram(
                    f"🎯 TP1 (%50)\n"
                    f"{symbol}\n"
                    f"Kapanan: {round(qty, 6)}"
                )

        # ================= TP2 %30 =================
        elif action == "TP2":
            pos = get_long_position(symbol)
            if pos:
                qty = float(pos["contracts"]) * 0.30
                bitget.create_market_sell_order(symbol, qty)

                telegram(
                    f"🎯 TP2 (%30)\n"
                    f"{symbol}\n"
                    f"Kapanan: {round(qty, 6)}"
                )

        # ================= STOP (FULL EXIT) =================
        elif action == "STOP":
            pos = get_long_position(symbol)
            if pos:
                qty = float(pos["contracts"])
                bitget.create_market_sell_order(symbol, qty)

                telegram(
                    f"🛑 STOP LOSS\n"
                    f"{symbol}\n"
                    f"Kapanan: {round(qty, 6)}"
                )

        else:
            return jsonify({"error": "invalid action"})

        return jsonify({"status": "ok"})

    except Exception as e:
        telegram(f"❌ HATA\n{str(e)}")
        return jsonify({"error": str(e)})

# ==================================================
# APP RUN
# ==================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000)
