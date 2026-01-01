from flask import Flask, request, jsonify
import ccxt
import requests
import json

app = Flask(__name__)

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
bitget.set_sandbox_mode(True)

# =============================
# 📲 TELEGRAM
# =============================
def telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=5
        )
    except:
        pass

# =============================
# 🔁 SYMBOL FIX
# =============================
def tv_symbol_to_bitget(symbol):
    if not symbol:
        raise ValueError("symbol boş")
    symbol = symbol.replace(".P", "")
    return symbol.replace("USDT", "/USDT:USDT")

# =============================
# 💰 USDT → AMOUNT
# =============================
def usdt_to_amount(symbol, usdt):
    price = bitget.fetch_ticker(symbol)["last"]
    return float(bitget.amount_to_precision(symbol, usdt / price))

# =============================
# 📌 AÇIK POZİSYON MİKTARI
# =============================
def get_open_amount(symbol):
    positions = bitget.fetch_positions([symbol])
    for p in positions:
        amt = float(p.get("contracts", 0))
        if amt > 0:
            return amt
    return 0.0


# =============================
# 🚀 WEBHOOK
# =============================
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw = request.data.decode("utf-8").strip()
        data = json.loads(raw)

        print("GELEN RAW:", data)

        action = data.get("action")
        symbol_raw = data.get("symbol")

        symbol = tv_symbol_to_bitget(symbol_raw)

        # ================= BUY =================
        if action == "BUY":
            usdt = float(data.get("usdt"))
            amount = usdt_to_amount(symbol, usdt)

            bitget.create_market_buy_order(symbol, amount)

            telegram(f"🟢 BUY\n{symbol}\nUSDT: {usdt}")
            return jsonify({"status": "BUY OK"})

        # ================= TP1 =================
        if action == "TP1":
            open_amount = get_open_amount(symbol)

            if open_amount <= 0:
                telegram(f"⚠️ TP1 geldi ama pozisyon yok\n{symbol}")
                return jsonify({"status": "TP1 ignored"})

            amount = open_amount * 0.50

            bitget.create_market_sell_order(
                symbol,
                amount,
                params={"reduceOnly": True}
            )

            telegram(f"🎯 TP1 %50\n{symbol}")
            return jsonify({"status": "TP1 OK"})

        # ================= TP2 =================
         if action == "TP2":
            open_amount = get_open_amount(symbol)

            if open_amount <= 0:
                telegram(f"⚠️ TP2 geldi ama pozisyon yok\n{symbol}")
                return jsonify({"status": "TP1 ignored"})

            amount = open_amount * 0.30

            bitget.create_market_sell_order(
                symbol,
                amount,
                params={"reduceOnly": True}
            )

            telegram(f"🎯 TP2 %30\n{symbol}")
            return jsonify({"status": "TP2 OK"})

        # ================= STOP =================
         if action == "STOP":
            open_amount = get_open_amount(symbol)

            if open_amount <= 0:
                telegram(f"⚠️ STOP geldi ama pozisyon yok\n{symbol}")
                return jsonify({"status": "STOP ignored"})

            amount = open_amount * 0.20

            bitget.create_market_sell_order(
                symbol,
                amount,
                params={"reduceOnly": True}
            )

            telegram(f"🎯 STOP %20\n{symbol}")
            return jsonify({"status": "STOP OK"})
    except Exception as e:
        telegram(f"❌ HATA:\n{str(e)}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

