from flask import Flask, request
import json
import pandas as pd
import ccxt

longPozisyonda = False
pozisyondami = False

app = Flask(__name__)

@app.route("/webhook", methods=['POST'])
def webhook():
    try:
        data = json.loads(request.data)
        print("Gelen Webhook Verisi:", data)  # Gelen veriyi kontrol et

        ticker = data.get('ticker', '')
        veri = ticker.split(".")
        symbol = veri[0] if veri else ''
        price = float(data.get('price', 0))
        islem = data.get('side', '')
        quantity = float(data.get('quantity', 0))
        binanceapi = data.get('binanceApiKey', '')
        binancesecret = data.get('binanceSecretKey', '')

        exchange = ccxt.binance({
            'apiKey': binanceapi,
            'secret': binancesecret,
            'options': {
                'adjustForTimeDifference': True,
                'defaultType': 'future'
            },
            'enableRateLimit': True
        })

        balance = exchange.fetch_balance()
        positions = balance['info'].get('positions', [])
        current_positions = [position for position in positions if float(position['positionAmt']) != 0 and position['symbol'] == symbol]
        position_bilgi = pd.DataFrame(current_positions)

        global pozisyondami, longPozisyonda
        if not position_bilgi.empty and float(position_bilgi.iloc[-1]['positionAmt']) != 0:
            pozisyondami = True
        else:
            pozisyondami = False
            longPozisyonda = False

        if pozisyondami and float(position_bilgi.iloc[-1]['positionAmt']) > 0:
            longPozisyonda = True

        print(f"İşlem: {islem}, Symbol: {symbol}, Fiyat: {price}, Miktar: {quantity}")

        if islem == "BUY":
            if not longPozisyonda:
                    order = exchange.create_market_buy_order(symbol, abs(float(position_bilgi.iloc[-1]['positionAmt'])), {"reduceOnly": True})
                alinacak_miktar = quantity / price
                order = exchange.create_market_buy_order(symbol, alinacak_miktar)
                print("BUY Order Başarılı:", order)

        if islem == "STOP":
            if longPozisyonda:
                order = exchange.create_market_sell_order(symbol, float(position_bilgi.iloc[-1]['positionAmt']), {"reduceOnly": True})
            print("STOP Order Başarılı:", order)

        if islem == "TP1":
            alinacak = (quantity / price) * 0.50
            if longPozisyonda:
                order = exchange.create_market_sell_order(symbol, alinacak)
            print("TP1 Order Başarılı:", order)

        if islem == "TP2":
            alinacak = ((quantity / price) / 2) * 0.50
            if longPozisyonda:
                order = exchange.create_market_sell_order(symbol, alinacak)
            print("TP2 Order Başarılı:", order)

    except Exception as e:
        print("Hata:", str(e))
    
    return {"code": "success"}
