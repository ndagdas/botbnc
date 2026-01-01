from flask import Flask, request, jsonify
import ccxt
import requests

app = Flask(__name__)

# =============================
# 🔑 BITGET DEMO API
# =============================
BITGET_API_KEY = "bg_ef42d04183294d11782767ded8b560dc"
BITGET_SECRET_KEY = "d883d8324fa83575bc4de104f9fc2ea229e3110e40d150d673408350b56769fe"
BITGET_PASSPHRASE = "93558287"

# =============================
# 📲 TELEGRAM
# =============================
TELEGRAM_TOKEN = "8143581645:AAF5figZLC0p7oC6AzjBTGzTfWtOFCdHzRo"
TELEGRAM_CHAT_ID = "@gridsystem"

# =============================
# 🔌 BITGET
# =============================
bitget = ccxt.bitget({
    'apiKey': BITGET_API_KEY,
    'secret': BITGET_SECRET_KEY,
    'password': BITGET_PASSPHRASE,
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

bitget.set_sandbox_mode(True)  # DEMO

# =============================
# 📲 TELEGRAM
# =============================
def telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg
    })

# =============================
# 🔁 SYMBOL FIX
# =============================
def tv_symbol_to_bitget(symbol):
    if not symbol:
        raise ValueError("symbol boş geldi")
    return symbol.replace("USDT", "/USDT:USDT")

# =============================
# 💰 USDT → AMOUNT
# =============================
def usdt_to_amount(symbol, usdt):
    price = bitget.fetch_ticker(symbol)["last"]
    amount = usdt / price
    return float(bitget.amount_to_precision(symbol, amount))

# =============================
# 🚀 WEBHOOK
# =============================
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)

        # 🔒 ZORUNLU ALANLAR
        required = ["action", "symbol", "usdt", "tp1", "tp2", "sl"]
        for r in required:
            if r not in data:
                raise ValueError(f"{r} JSON içinde yok")

        if data["action"] != "BUY":
            return jsonify({"status": "ignored"})

        symbol = tv_symbol_to_bitget(data["symbol"])
        usdt   = float(data["usdt"])
        tp1    = float(data["tp1"])
        tp2    = float(data["tp2"])
        sl     = float(data["sl"])

        amount = usdt_to_amount(symbol, usdt)

        # 🟢 MARKET BUY
        order = bitget.create_market_buy_order(symbol, amount)
        entry = order["average"]

        # 🎯 TP1 (%50)
        bitget.create_limit_sell_order(
            symbol,
            amount * 0.50,
            tp1
        )

        # 🎯 TP2 (%30)
        bitget.create_limit_sell_order(
            symbol,
            amount * 0.30,
            tp2
        )

        # 🛑 STOP LOSS (%100)
        bitget.create_order(
            symbol=symbol,
            type="market",
            side="sell",
            amount=amount,
            params={
                "stopLossPrice": sl,
                "reduceOnly": True
            }
        )

        telegram(
            f"🟢 LONG AÇILDI\n"
            f"{symbol}\n"
            f"Giriş: {entry}\n"
            f"USDT: {usdt}\n\n"
            f"🎯 TP1: {tp1}\n"
            f"🎯 TP2: {tp2}\n"
            f"🛑 SL: {sl}"
        )

        return jsonify({"status": "ok"})

    except Exception as e:
        telegram(f"❌ HATA:\n{str(e)}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

# ==================================================
# APP RUN
# ==================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000)
