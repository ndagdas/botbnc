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

        price = float(data.get('price', 0))
        islem = data.get('side', '')
        quantity = float(data.get('quantity', 0))

        # Demo modu kontrolü - eğer demo API key gelirse testnet kullan
        binanceapi = data.get('binanceApiKey', '')
        binancesecret = data.get('binanceSecretKey', '')
        
        # Testnet mi gerçek mi kontrol et (opsiyonel)
        use_testnet = data.get('testnet', True)  # Varsayılan olarak testnet kullan
        
        if use_testnet:
            print("DEMO MOD: Testnet kullanılıyor")
            # Testnet için özel konfigürasyon
            exchange = ccxt.binance({
                'apiKey': binanceapi,
                'secret': binancesecret,
                'options': {
                    'adjustForTimeDifference': True,
                    'defaultType': 'future'
                },
                'enableRateLimit': True,
                # TESTNET için özel URL'ler
                'urls': {
                    'api': {
                        'public': 'https://testnet.binancefuture.com/fapi/v1',
                        'private': 'https://testnet.binancefuture.com/fapi/v1',
                    }
                }
            })
        else:
            print("REAL MOD: Gerçek API kullanılıyor")
            exchange = ccxt.binance({
                'apiKey': binanceapi,
                'secret': binancesecret,
                'options': {
                    'adjustForTimeDifference': True,
                    'defaultType': 'future'
                },
                'enableRateLimit': True
            })
        
        # Testnet için margin ve kaldıraç ayarları
        if use_testnet:
            try:
                # Kaldıraç ayarla (testnet'te güvenli kaldıraç)
                leverage = 5  # Demo için düşük kaldıraç
                exchange.fapiPrivate_post_leverage({
                    'symbol': symbol,
                    'leverage': leverage
                })
                print(f"Testnet için kaldıraç {leverage}x ayarlandı")
                
                # Margin türünü ayarla
                exchange.fapiPrivate_post_margintype({
                    'symbol': symbol,
                    'marginType': 'ISOLATED'  # ISOLATED veya CROSS
                })
                print("Margin türü ISOLATED olarak ayarlandı")
            except Exception as e:
                print(f"Kaldıraç/margin ayarlama hatası (testnet): {e}")

        # Bakiye kontrolü - testnet için özel log
        try:
            balance = exchange.fetch_balance()
            if use_testnet:
                print(f"TESTNET Bakiye: {balance.get('USDT', {}).get('free', 0)} USDT")
            else:
                print(f"REAL Bakiye: {balance.get('USDT', {}).get('free', 0)} USDT")
                
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
                print(f"Pozisyon durumu: {symbol} - Miktar: {pos_amt}")
            else:
                pozisyondami = False
                longPozisyonda = False
                shortPozisyonda = False
                print(f"Pozisyon yok: {symbol}")
                
        except Exception as e:
            print(f"Bakiye/pozisyon kontrol hatası: {e}")
            return {"code": "error", "message": f"Bakiye kontrol hatası: {str(e)}"}

        print(f"İşlem: {islem}, Symbol: {symbol}, Fiyat: {price}, Miktar: {quantity}")

        # Lot boyutu kontrolü (testnet için)
        def adjust_quantity(symbol, quantity, price):
            """Testnet'te lot boyutunu ayarla"""
            try:
                market = exchange.market(symbol)
                if market:
                    # Minimum lot kontrolü
                    min_qty = market.get('limits', {}).get('amount', {}).get('min', 0.001)
                    if quantity < min_qty:
                        print(f"Uyarı: Miktar minimum {min_qty} olmalı. Ayarlanıyor...")
                        quantity = min_qty
                    
                    # Step size kontrolü
                    step_size = market.get('precision', {}).get('amount', 0.001)
                    if step_size > 0:
                        quantity = round(quantity - (quantity % step_size), 8)
                    
                    # Demo için maksimum sınır
                    max_demo_qty = 0.1  # Testnet için maksimum
                    if quantity > max_demo_qty:
                        print(f"Demo limiti: Miktar {max_demo_qty} ile sınırlandı")
                        quantity = max_demo_qty
            except Exception as e:
                print(f"Lot boyutu ayarlama hatası: {e}")
            
            return quantity

        # ================= BUY =================
        if islem == "BUY":
            if not longPozisyonda:
                if shortPozisyonda:
                    # Short pozisyonu kapat
                    try:
                        close_amount = abs(float(position_bilgi.iloc[-1]['positionAmt']))
                        print(f"Short pozisyon kapatılıyor: {close_amount}")
                        order = exchange.create_market_buy_order(
                            symbol,
                            close_amount,
                            {"reduceOnly": True}
                        )
                        print("Short pozisyon kapatıldı:", order)
                    except Exception as e:
                        print(f"Short kapatma hatası: {e}")

                # Yeni long pozisyon aç
                alinacak_miktar = quantity / price
                alinacak_miktar = adjust_quantity(symbol, alinacak_miktar, price)
                
                print(f"Yeni long pozisyon açılıyor: {alinacak_miktar}")
                order = exchange.create_market_buy_order(symbol, alinacak_miktar)
                print("BUY Order Başarılı:", order)
                
                # Demo için TP/SL emirleri ekle (opsiyonel)
                if use_testnet:
                    try:
                        # Örnek: %2 stop-loss, %5 take-profit
                        sl_price = price * 0.98
                        tp_price = price * 1.05
                        
                        # Stop-loss emri
                        exchange.create_order(
                            symbol, 
                            'STOP_MARKET', 
                            'SELL', 
                            alinacak_miktar,
                            None,
                            {
                                'stopPrice': sl_price,
                                'reduceOnly': True
                            }
                        )
                        print(f"Stop-loss emri verildi: {sl_price}")
                    except Exception as e:
                        print(f"TP/SL emri hatası: {e}")

        # ================= SELL (SHORT) =================
        if islem == "SELL":
            if not shortPozisyonda:
                if longPozisyonda:
                    # Long pozisyonu kapat
                    try:
                        close_amount = float(position_bilgi.iloc[-1]['positionAmt'])
                        print(f"Long pozisyon kapatılıyor: {close_amount}")
                        order = exchange.create_market_sell_order(
                            symbol,
                            close_amount,
                            {"reduceOnly": True}
                        )
                        print("Long pozisyon kapatıldı:", order)
                    except Exception as e:
                        print(f"Long kapatma hatası: {e}")

                # Yeni short pozisyon aç
                alinacak_miktar = quantity / price
                alinacak_miktar = adjust_quantity(symbol, alinacak_miktar, price)
                
                print(f"Yeni short pozisyon açılıyor: {alinacak_miktar}")
                order = exchange.create_market_sell_order(symbol, alinacak_miktar)
                print("SELL Order Başarılı:", order)
                
                # Demo için TP/SL emirleri ekle
                if use_testnet:
                    try:
                        # Örnek: %2 stop-loss, %5 take-profit
                        sl_price = price * 1.02  # Short için ters
                        tp_price = price * 0.95
                        
                        # Stop-loss emri
                        exchange.create_order(
                            symbol, 
                            'STOP_MARKET', 
                            'BUY', 
                            alinacak_miktar,
                            None,
                            {
                                'stopPrice': sl_price,
                                'reduceOnly': True
                            }
                        )
                        print(f"Stop-loss emri verildi: {sl_price}")
                    except Exception as e:
                        print(f"TP/SL emri hatası: {e}")

        # ================= TP1 → %50 KAR =================
        if islem == "TP1" and pozisyondami:
            try:
                pozisyon_miktari = abs(float(position_bilgi.iloc[-1]['positionAmt']))
                alinacak = pozisyon_miktari * 0.50
                alinacak = adjust_quantity(symbol, alinacak, price)
                
                print(f"TP1: Pozisyonun %50'si kapatılıyor: {alinacak}")

                if longPozisyonda:
                    order = exchange.create_market_sell_order(
                        symbol, alinacak, {"reduceOnly": True}
                    )
                if shortPozisyonda:
                    order = exchange.create_market_buy_order(
                        symbol, alinacak, {"reduceOnly": True}
                    )

                print("TP1 (%50) KAR Order Başarılı:", order)
            except Exception as e:
                print(f"TP1 hatası: {e}")

        # ================= TP2 → %30 KAR =================
        if islem == "TP2" and pozisyondami:
            try:
                pozisyon_miktari = abs(float(position_bilgi.iloc[-1]['positionAmt']))
                alinacak = pozisyon_miktari * 0.30
                alinacak = adjust_quantity(symbol, alinacak, price)
                
                print(f"TP2: Pozisyonun %30'u kapatılıyor: {alinacak}")

                if longPozisyonda:
                    order = exchange.create_market_sell_order(
                        symbol, alinacak, {"reduceOnly": True}
                    )
                if shortPozisyonda:
                    order = exchange.create_market_buy_order(
                        symbol, alinacak, {"reduceOnly": True}
                    )

                print("TP2 (%30) KAR Order Başarılı:", order)
            except Exception as e:
                print(f"TP2 hatası: {e}")

        # ================= STOP → KALAN %20 =================
        if islem == "STOP" and pozisyondami:
            try:
                pozisyon_miktari = abs(float(position_bilgi.iloc[-1]['positionAmt']))
                pozisyon_miktari = adjust_quantity(symbol, pozisyon_miktari, price)
                
                print(f"STOP: Tüm pozisyon kapatılıyor: {pozisyon_miktari}")

                if longPozisyonda:
                    order = exchange.create_market_sell_order(
                        symbol, pozisyon_miktari, {"reduceOnly": True}
                    )
                if shortPozisyonda:
                    order = exchange.create_market_buy_order(
                        symbol, pozisyon_miktari, {"reduceOnly": True}
                    )

                print("STOP Order Başarılı:", order)
            except Exception as e:
                print(f"STOP hatası: {e}")
                
        # ================= CLOSE ALL (yeni eklenen) =================
        if islem == "CLOSE_ALL" and pozisyondami:
            try:
                pozisyon_miktari = abs(float(position_bilgi.iloc[-1]['positionAmt']))
                pozisyon_miktari = adjust_quantity(symbol, pozisyon_miktari, price)
                
                print(f"CLOSE_ALL: Tüm pozisyonlar kapatılıyor: {pozisyon_miktari}")

                if longPozisyonda:
                    order = exchange.create_market_sell_order(
                        symbol, pozisyon_miktari, {"reduceOnly": True}
                    )
                if shortPozisyonda:
                    order = exchange.create_market_buy_order(
                        symbol, pozisyon_miktari, {"reduceOnly": True}
                    )

                print("CLOSE_ALL Order Başarılı:", order)
            except Exception as e:
                print(f"CLOSE_ALL hatası: {e}")

    except Exception as e:
        print("Hata:", str(e))
        return {"code": "error", "message": str(e)}

    return {"code": "success", "mode": "testnet" if use_testnet else "real"}


if __name__ == "__main__":
    print("=== BINANCE FUTURES TRADING BOT ===")
    print("Mod: DEMO (Testnet)")
    print("Port: 5000")
    print("Webhook Endpoint: /webhook")
    print("Örnek TradingView alert formatı:")
    print("""
    {
        "ticker": "BTCUSDT.P",
        "price": 50000,
        "side": "BUY",
        "quantity": 100,
        "binanceApiKey": "your_testnet_api_key",
        "binanceSecretKey": "your_testnet_secret_key",
        "testnet": true
    }
    """)
    
    # Testnet için gereklilikleri kontrol et
    print("\nGereklilikler:")
    print("1. pip install ccxt flask pandas")
    print("2. Testnet API anahtarları: https://testnet.binancefuture.com")
    print("3. TradingView'de webhook URL: http://your_ip:5000/webhook")
    
    app.run(host='0.0.0.0', port=5000, debug=True)
