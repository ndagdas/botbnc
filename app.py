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
        ticker = data['ticker']
        veri=ticker.split(".")
        symbol=veri[0]
        price = data['price']
        islem = data['side']
        quantity = data['quantity']
        binanceapi=data['binanceApiKey']
        binancesecret=data['binanceSecretKey']

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
        positions = balance['info']['positions']
        current_positions = [position for position in positions if float(position['positionAmt']) != 0 and position['symbol'] == symbol]
        position_bilgi = pd.DataFrame(current_positions, columns=["symbol", "entryPrice", "unrealizedProfit", "isolatedWallet", "positionAmt", "positionSide"])
        
        
        #Pozisyonda olup olmadığını kontrol etme
        if not position_bilgi.empty and position_bilgi["positionAmt"][len(position_bilgi.index) - 1] != 0:
            pozisyondami = True
        else: 
            pozisyondami = False
            shortPozisyonda = False
            longPozisyonda = False
        
        # Long pozisyonda mı?
        if pozisyondami and float(position_bilgi["positionAmt"][len(position_bilgi.index) - 1]) > 0:
            longPozisyonda = True
            shortPozisyonda = False
        # Short pozisyonda mı?
        if pozisyondami and float(position_bilgi["positionAmt"][len(position_bilgi.index) - 1]) < 0:
            shortPozisyonda = True
            longPozisyonda = False

        if islem=="BUY":
            if longPozisyonda == False:
                if shortPozisyonda:
                    order = exchange.create_market_buy_order(symbol, (float(position_bilgi["positionAmt"][len(position_bilgi.index) - 1]) * -1), {"reduceOnly": True})
                alinacak_miktar = float(quantity)/float(price)
                order = exchange.create_market_buy_order(symbol, alinacak_miktar)


        if islem=="SELL":
            if shortPozisyonda == False:
                if longPozisyonda:
                    order = exchange.create_market_sell_order(symbol, float(position_bilgi["positionAmt"][len(position_bilgi.index) - 1]), {"reduceOnly": True})
                alinacak_miktar = float(quantity)/float(price)
                order = exchange.create_market_sell_order(symbol, alinacak_miktar)


        if islem=="STOP":
            if longPozisyonda:
                order = exchange.create_market_sell_order(symbol, float(position_bilgi["positionAmt"][len(position_bilgi.index) - 1]), {"reduceOnly": True})

            if shortPozisyonda:
                order = exchange.create_market_buy_order(symbol, float(position_bilgi["positionAmt"][len(position_bilgi.index) - 1]), {"reduceOnly": True})

        if islem=="KAR":
            if longPozisyonda:
                alinacak = (float(quantity)/float(price))/2
                order = exchange.create_market_sell_order(symbol, alinacak)

            if shortPozisyonda:
                alinacak = (float(quantity)/float(price))/2
                order = exchange.create_market_buy_order(symbol, alinacak)


    except:
        pass
    return {
        "code": "success",
    }