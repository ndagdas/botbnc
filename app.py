from flask import Flask, request
import json
import pandas as pd
import ccxt

longPozisyonda = False
shortPozisyonda = False
pozisyondami = False

app = Flask(__name__)

@app.route("/webhook", methods=['POST'])
def webhook():
    try:
        data = json.loads(request.data)
        print("Gelen Webhook Verisi:", data)

        ticker = data.get('ticker', '')
        veri = ticker.split(".")
        symbol = veri[0] if veri else ''
        symbol = symbol.replace(".P", "")

        price = float(data.get('price', 0))
        islem = data.get('side', '')
        quantity = float(data.get('quantity', 0))

        binanceapi = data.get('binanceApiKey', '')
        binancesecret = data.get('binanceSecretKey', '')

        # ===== BINANCE FUTURES DEMO (TESTNET DEĞİL) =====
        exchange = ccxt.binance({
            'apiKey': binanceapi,
            'secret': binancesecret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',
                'adjustForTimeDifference': True
            }
        })

        # ===== POZİSYON BİLGİSİ =====
        balance = exchange.fetch_balance()
        positions = balance['info'].get('positions', [])
        current_positions = [
            p for p in positions
            if float(p['positionAmt']) != 0 and p['symbol'] == symbol
        ]

        position_bilgi = pd.DataFrame(current_positions)

        global pozisyondami, longPozisyonda, shortPozisyonda

        if not position_bilgi.empty:
            pozisyondami = True
            pos_amt = float(position_bilgi.iloc[-1]['positionAmt'])
            longPozisyonda = pos_amt > 0
            shortPozisyonda = pos_amt < 0
        else:
            pozisyondami = False
            longPozisyonda = False
            shortPozisyonda = False

        print(f"İşlem: {islem} | Symbol: {symbol}")

        # ================= BUY =================
        if islem == "BUY":
            if not longPozisyonda:
                if shortPozisyonda:
                    exchange.create_market_buy_order(
                        symbol,
                        abs(float(position_bilgi.iloc[-1]['positionAmt'])),
                        {"reduceOnly": True}
                    )

                alinacak_miktar = round(quantity / price, 3)
                order = exchange.create_market_buy_order(symbol, alinacak_miktar)
                print("BUY Order:", order)

        # ================= TP1 → %50 =================
        if islem == "TP1" and pozisyondami:
            pozisyon_miktari = abs(float(position_bilgi.iloc[-1]['positionAmt']))
            alinacak = round(pozisyon_miktari * 0.50, 3)

            if longPozisyonda:
                order = exchange.create_market_sell_order(
                    symbol, alinacak, {"reduceOnly": True}
                )
            else:
                order = exchange.create_market_buy_order(
                    symbol, alinacak, {"reduceOnly": True}
                )

            print("TP1 (%50) OK:", order)

        # ================= TP2 → %30 =================
        if islem == "TP2" and pozisyondami:
            pozisyon_miktari = abs(float(position_bilgi.iloc[-1]['positionAmt']))
            alinacak = round(pozisyon_miktari * 0.60, 3)  # kalan %50'nin %60'ı = toplam %30

            if longPozisyonda:
                order = exchange.create_market_sell_order(
                    symbol, alinacak, {"reduceOnly": True}
                )
            else:
                order = exchange.create_market_buy_order(
                    symbol, alinacak, {"reduceOnly": True}
                )

            print("TP2 (%30) OK:", order)

        # ================= STOP → TAM KAPAT =================
        if islem == "STOP" and pozisyondami:
            pozisyon_miktari = abs(float(position_bilgi.iloc[-1]['positionAmt']))

            if longPozisyonda:
                order = exchange.create_market_sell_order(
                    symbol, pozisyon_miktari, {"reduceOnly": True}
                )
            else:
                order = exchange.create_market_buy_order(
                    symbol, pozisyon_miktari, {"reduceOnly": True}
                )

            print("STOP OK:", order)

    except Exception as e:
        print("HATA:", str(e))

    return {"code": "success"}
