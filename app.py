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

def telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=5
        )
    except:
        pass

def tv_symbol_to_bitget(symbol):
    if not symbol:
        raise ValueError("symbol boş geldi")
    return symbol.replace("USDT", "/USDT:USDT")

def usdt_to_amount(symbol, usdt):
    price = bitget.fetch_ticker(symbol)["last"]
    return float(bitget.amount_to_precision(symbol, usdt / price))

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw = request.data.decode("utf-8").strip()
        if not raw:
            raise ValueError("Boş payload geldi")

        data = json.loads(raw)

        print("GELEN RAW:", raw)

        if data.get("action") != "BUY":
            return jsonify({"status": "ignored"})

        symbol_raw = data.get("symbol")
        usdt = data.get("usdt")
        tp1 = data.get("tp1")
        tp2 = data.get("tp2")
        sl  = data.get("sl")

        if None in [symbol_raw, usdt, tp1, tp2, sl]:
            raise ValueError(f"Eksik alan var: {data}")

        symbol = tv_symbol_to_bitget(symbol_raw)

        usdt = float(usdt)
        tp1 = float(tp1)
        tp2 = float(tp2)
        sl  = float(sl)

        amount = usdt_to_amount(symbol, usdt)

        order = bitget.create_market_buy_order(symbol, amount)
        entry = order["average"]

        bitget.create_limit_sell_order(symbol, amount * 0.50, tp1)
        bitget.create_limit_sell_order(symbol, amount * 0.30, tp2)

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
            f"TP1: {tp1}\n"
            f"TP2: {tp2}\n"
            f"SL: {sl}"
        )

        return jsonify({"status": "ok"})

    except Exception as e:
        telegram(f"❌ WEBHOOK HATASI:\n{str(e)}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

