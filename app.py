from flask import Flask, request
import json
import ccxt

app = Flask(__name__)

# ---------------------------------
# HELPER
# ---------------------------------
def fix_symbol(symbol):
    symbol = symbol.replace(".P", "")
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    return symbol

def get_exchange(api, secret):
    exchange = ccxt.binance({
        'apiKey': api,
        'secret': secret,
        'options': {
            'adjustForTimeDifference': True,
            'defaultType': 'future'
        },
        'enableRateLimit': True
    })

    exchange.set_sandbox_mode(True) 

    return exchange

def get_position(exchange, symbol):
    balance = exchange.fetch_balance()
    positions = balance['info'].get('positions', [])

    for p in positions:
        if p['symbol'] == symbol:
            amt = float(p['positionAmt'])
            if amt != 0:
                return amt

    return 0

# ---------------------------------
# WEBHOOK
# ---------------------------------
@app.route("/webhook", methods=['POST'])
def webhook():
    try:
        data = json.loads(request.data)

        print("GELEN:", data)

        symbol = fix_symbol(data.get("ticker", "BTCUSDT"))
        side = data.get("side")
        price = float(data.get("price", 0))
        usdt = float(data.get("quantity", 100))

        api = data.get("binanceApiKey")
        secret = data.get("binanceSecretKey")

        exchange = get_exchange(api, secret)

        if price <= 0:
            ticker = exchange.fetch_ticker(symbol)
            price = ticker["last"]

        position_amt = get_position(exchange, symbol)

        print(f"{symbol} | {side} | price={price} | usdt={usdt} | pos={position_amt}")

        # ---------------------------------
        # BUY
        # ---------------------------------
        if side == "BUY":
            if position_amt < 0:
                exchange.create_market_buy_order(symbol, abs(position_amt), {"reduceOnly": True})

            if position_amt <= 0:
                market = exchange.market(symbol)

                raw_qty = usdt / price
                min_qty = market['limits']['amount']['min']
                precision = market['precision']['amount']

                qty = max(raw_qty, min_qty)
                qty = round(qty, precision)

                exchange.create_market_buy_order(symbol, qty)
                print(f"BUY OK | qty={qty}")
        # ---------------------------------
        # SELL (SHORT)
        # ---------------------------------
        elif side == "SELL":
            if position_amt > 0:
                exchange.create_market_sell_order(symbol, position_amt, {"reduceOnly": True})

            if position_amt >= 0:
                market = exchange.market(symbol)

                raw_qty = usdt / price
                min_qty = market['limits']['amount']['min']
                precision = market['precision']['amount']

                qty = max(raw_qty, min_qty)
                qty = round(qty, precision)

                exchange.create_market_sell_order(symbol, qty)
                print(f"SELL OK | qty={qty}")
        # ---------------------------------
        # TP1
        # ---------------------------------
        elif side == "TP1":
            if position_amt > 0:
                exchange.create_market_sell_order(symbol, position_amt * 0.5, {"reduceOnly": True})
                print("TP1 LONG")

            elif position_amt < 0:
                exchange.create_market_buy_order(symbol, abs(position_amt) * 0.5, {"reduceOnly": True})
                print("TP1 SHORT")

        # ---------------------------------
        # TP2
        # ---------------------------------
        elif side == "TP2":
            if position_amt > 0:
                exchange.create_market_sell_order(symbol, position_amt * 0.3, {"reduceOnly": True})
                print("TP2 LONG")

            elif position_amt < 0:
                exchange.create_market_buy_order(symbol, abs(position_amt) * 0.3, {"reduceOnly": True})
                print("TP2 SHORT")

        # ---------------------------------
        # STOP / CLOSE
        # ---------------------------------
        elif side in ["STOP", "CLOSE"]:
            if position_amt > 0:
                exchange.create_market_sell_order(symbol, position_amt, {"reduceOnly": True})
                print("STOP LONG")

            elif position_amt < 0:
                exchange.create_market_buy_order(symbol, abs(position_amt), {"reduceOnly": True})
                print("STOP SHORT")

        return {"status": "ok"}

    except Exception as e:
        print("HATA:", str(e))
        return {"error": str(e)}

# ---------------------------------
# RUN
# ---------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
