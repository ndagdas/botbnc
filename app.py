from flask import Flask, request
import json
import pandas as pd
import ccxt

longPozisyonda = False
shortPozisyonda = False
pozisyondami = False

app = Flask(__name__)

class MockBinanceExchange:
    """Test modu için mock Binance exchange sınıfı"""
    def __init__(self):
        self.positions = []
        self.orders_history = []
        self.balance = {
            'USDT': {'free': 10000, 'used': 0, 'total': 10000}
        }
        print("⚠️  TEST MODU AKTİF: Mock Binance Exchange kullanılıyor")
    
    def fetch_balance(self):
        """Mock balance döndür"""
        return {
            'USDT': self.balance['USDT'],
            'info': {
                'positions': self.positions,
                'totalWalletBalance': '10000'
            }
        }
    
    def create_market_buy_order(self, symbol, amount, params=None):
        """Mock market buy order"""
        order_id = f"test_buy_{len(self.orders_history)}"
        order = {
            'id': order_id,
            'symbol': symbol,
            'side': 'BUY',
            'amount': amount,
            'price': 0,
            'cost': 0,
            'status': 'closed',
            'timestamp': pd.Timestamp.now(),
            'params': params,
            'reduceOnly': params.get('reduceOnly', False) if params else False
        }
        
        # Pozisyon güncelle
        self._update_position(symbol, amount, 'BUY', params)
        
        self.orders_history.append(order)
        print(f"✅ [TEST] MARKET BUY: {symbol} - {amount:.6f} adet (ReduceOnly: {order['reduceOnly']})")
        print(f"   📋 Order ID: {order_id}")
        return order
    
    def create_market_sell_order(self, symbol, amount, params=None):
        """Mock market sell order"""
        order_id = f"test_sell_{len(self.orders_history)}"
        order = {
            'id': order_id,
            'symbol': symbol,
            'side': 'SELL',
            'amount': amount,
            'price': 0,
            'cost': 0,
            'status': 'closed',
            'timestamp': pd.Timestamp.now(),
            'params': params,
            'reduceOnly': params.get('reduceOnly', False) if params else False
        }
        
        # Pozisyon güncelle
        self._update_position(symbol, amount, 'SELL', params)
        
        self.orders_history.append(order)
        print(f"✅ [TEST] MARKET SELL: {symbol} - {amount:.6f} adet (ReduceOnly: {order['reduceOnly']})")
        print(f"   📋 Order ID: {order_id}")
        return order
    
    def _update_position(self, symbol, amount, side, params):
        """Mock pozisyon güncelleme"""
        reduce_only = params.get('reduceOnly', False) if params else False
        
        # Mevcut pozisyonu bul
        pos = None
        for p in self.positions:
            if p['symbol'] == symbol:
                pos = p
                break
        
        if pos is None:
            pos = {
                'symbol': symbol,
                'positionAmt': '0',
                'entryPrice': '0',
                'unRealizedProfit': '0',
                'leverage': '10'
            }
            self.positions.append(pos)
        
        current_amount = float(pos['positionAmt'])
        
        if reduce_only:
            # Reduce only: pozisyonu azalt
            if side == 'BUY' and current_amount < 0:
                # Short pozisyonu kapat
                new_amount = current_amount + amount
                pos['positionAmt'] = str(new_amount)
                print(f"   📊 Short pozisyon azaltıldı: {current_amount} → {new_amount}")
            elif side == 'SELL' and current_amount > 0:
                # Long pozisyonu kapat
                new_amount = current_amount - amount
                pos['positionAmt'] = str(new_amount)
                print(f"   📊 Long pozisyon azaltıldı: {current_amount} → {new_amount}")
        else:
            # Yeni pozisyon aç
            if side == 'BUY':
                new_amount = current_amount + amount
                pos['positionAmt'] = str(new_amount)
                print(f"   📊 Long pozisyon açıldı: {current_amount} → {new_amount}")
            elif side == 'SELL':
                new_amount = current_amount - amount
                pos['positionAmt'] = str(new_amount)
                print(f"   📊 Short pozisyon açıldı: {current_amount} → {new_amount}")
        
        # Pozisyon sıfırlanmışsa listeden çıkar
        if float(pos['positionAmt']) == 0:
            self.positions.remove(pos)
    
    def print_summary(self):
        """Test özetini göster"""
        print("\n" + "="*60)
        print("📊 TEST MODU ÖZETİ")
        print("="*60)
        print(f"Toplam İşlem Sayısı: {len(self.orders_history)}")
        
        if self.positions:
            print("\n📈 AKTİF POZİSYONLAR:")
            for pos in self.positions:
                amount = float(pos['positionAmt'])
                side = "LONG" if amount > 0 else "SHORT"
                print(f"   {pos['symbol']}: {abs(amount):.6f} ({side})")
        else:
            print("\n📭 AKTİF POZİSYON YOK")
        
        print(f"\n💰 BAKİYE: {self.balance['USDT']['total']} USDT")
        print("="*60)

def get_exchange(data):
    """Exchange objesini döndürür - test modu data içindeki flag'e göre"""
    test_mode = data.get('testMode', False)
    
    if test_mode:
        return MockBinanceExchange()
    
    # Gerçek mod
    binanceapi = data.get('binanceApiKey', '')
    binancesecret = data.get('binanceSecretKey', '')
    
    if not binanceapi or not binancesecret:
        print("⚠️  API key bulunamadı! Test moduna geçiliyor...")
        return MockBinanceExchange()
    
    return ccxt.binance({
        'apiKey': binanceapi,
        'secret': binancesecret,
        'options': {
            'adjustForTimeDifference': True,
            'defaultType': 'future'
        },
        'enableRateLimit': True
    })

@app.route("/webhook", methods=['POST'])
def webhook():
    global longPozisyonda, shortPozisyonda, pozisyondami
    
    try:
        data = json.loads(request.data)
        print("\n" + "="*60)
        print("📨 WEBHOOK ALINDI")
        print("="*60)
        
        # Test modunu kontrol et
        test_mode = data.get('testMode', False)
        mode_text = "TEST" if test_mode else "GERÇEK"
        print(f"🔧 MOD: {mode_text}")
        
        # Verileri al
        ticker = data.get('ticker', 'BTCUSDT.P')
        veri = ticker.split(".")
        symbol = veri[0] if veri else ''
        
        price = float(data.get('price', 0))
        islem = data.get('side', '')
        quantity = float(data.get('quantity', 0))
        
        if not price or not islem or not quantity:
            print("❌ Eksik veri! price, side veya quantity bulunamadı.")
            return {"code": "error", "message": "Eksik veri"}
        
        print(f"📊 Sembol: {symbol}")
        print(f"💰 Fiyat: {price}")
        print(f"🎯 İşlem: {islem}")
        print(f"📦 Miktar: {quantity} USDT")
        
        # Exchange objesini al
        exchange = get_exchange(data)
        
        # Pozisyon bilgilerini al
        balance = exchange.fetch_balance()
        positions = balance['info'].get('positions', [])
        
        current_positions = [
            p for p in positions
            if float(p.get('positionAmt', 0)) != 0 and p.get('symbol') == symbol
        ]
        
        position_bilgi = pd.DataFrame(current_positions)
        
        # Pozisyon durumunu güncelle
        if not position_bilgi.empty and not position_bilgi.iloc[-1].empty:
            pozisyondami = True
            pos_amt = float(position_bilgi.iloc[-1].get('positionAmt', 0))
            longPozisyonda = pos_amt > 0
            shortPozisyonda = pos_amt < 0
            
            print(f"📈 POZİSYON DURUMU:")
            print(f"   Aktif: {'EVET' if pozisyondami else 'HAYIR'}")
            print(f"   Tip: {'LONG' if longPozisyonda else 'SHORT' if shortPozisyonda else 'YOK'}")
            print(f"   Miktar: {abs(pos_amt):.6f}")
        else:
            pozisyondami = False
            longPozisyonda = False
            shortPozisyonda = False
            print(f"📭 AKTİF POZİSYON YOK")
        
        # İşlemleri yap
        print("\n⚡ İŞLEM YÜRÜTÜLÜYOR...")
        
        # ================= BUY =================
        if islem == "BUY":
            if not longPozisyonda:
                if shortPozisyonda:
                    print("🔄 Short pozisyon kapatılıyor...")
                    exchange.create_market_buy_order(
                        symbol,
                        abs(float(position_bilgi.iloc[-1].get('positionAmt', 0))) if not position_bilgi.empty else 0,
                        {"reduceOnly": True}
                    )
                
                alinacak_miktar = quantity / price
                print(f"🟢 Long pozisyon açılıyor: {alinacak_miktar:.6f} adet")
                order = exchange.create_market_buy_order(symbol, alinacak_miktar)
                print(f"✅ BUY Order Tamamlandı")
            else:
                print("ℹ️  Zaten LONG pozisyonda, işlem yapılmadı")
        
        # ================= SELL =================
        elif islem == "SELL":
            if not shortPozisyonda:
                if longPozisyonda:
                    print("🔄 Long pozisyon kapatılıyor...")
                    exchange.create_market_sell_order(
                        symbol,
                        float(position_bilgi.iloc[-1].get('positionAmt', 0)) if not position_bilgi.empty else 0,
                        {"reduceOnly": True}
                    )
                
                alinacak_miktar = quantity / price
                print(f"🔴 Short pozisyon açılıyor: {alinacak_miktar:.6f} adet")
                order = exchange.create_market_sell_order(symbol, alinacak_miktar)
                print(f"✅ SELL Order Tamamlandı")
            else:
                print("ℹ️  Zaten SHORT pozisyonda, işlem yapılmadı")
        
        # ================= TP1 → %50 KAR =================
        elif islem == "TP1" and pozisyondami:
            pozisyon_miktari = abs(float(position_bilgi.iloc[-1].get('positionAmt', 0)))
            alinacak = pozisyon_miktari * 0.50
            
            print(f"🎯 TP1 (%50) kar alınıyor: {alinacak:.6f} adet")
            
            if longPozisyonda:
                order = exchange.create_market_sell_order(
                    symbol, alinacak, {"reduceOnly": True}
                )
            elif shortPozisyonda:
                order = exchange.create_market_buy_order(
                    symbol, alinacak, {"reduceOnly": True}
                )
            
            print(f"✅ TP1 Order Tamamlandı")
        
        # ================= TP2 → %30 KAR =================
        elif islem == "TP2" and pozisyondami:
            pozisyon_miktari = abs(float(position_bilgi.iloc[-1].get('positionAmt', 0)))
            alinacak = pozisyon_miktari * 0.30
            
            print(f"🎯 TP2 (%30) kar alınıyor: {alinacak:.6f} adet")
            
            if longPozisyonda:
                order = exchange.create_market_sell_order(
                    symbol, alinacak, {"reduceOnly": True}
                )
            elif shortPozisyonda:
                order = exchange.create_market_buy_order(
                    symbol, alinacak, {"reduceOnly": True}
                )
            
            print(f"✅ TP2 Order Tamamlandı")
        
        # ================= STOP → KALAN %20 =================
        elif islem == "STOP" and pozisyondami:
            pozisyon_miktari = abs(float(position_bilgi.iloc[-1].get('positionAmt', 0)))
            
            print(f"🛑 STOP ile pozisyon kapatılıyor: {pozisyon_miktari:.6f} adet")
            
            if longPozisyonda:
                order = exchange.create_market_sell_order(
                    symbol, pozisyon_miktari, {"reduceOnly": True}
                )
            elif shortPozisyonda:
                order = exchange.create_market_buy_order(
                    symbol, pozisyon_miktari, {"reduceOnly": True}
                )
            
            print(f"✅ STOP Order Tamamlandı")
        else:
            print(f"⚠️  Geçersiz işlem veya pozisyon yok: {islem}")
        
        # Test modunda özet göster
        if test_mode and hasattr(exchange, 'print_summary'):
            exchange.print_summary()
        
        print("="*60 + "\n")
        
        return {"code": "success", "mode": mode_text}
    
    except Exception as e:
        print(f"❌ HATA: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"code": "error", "message": str(e)}

@app.route('/test', methods=['GET'])
def test_webhook():
    """Test webhook'u için GET endpoint"""
    return {
        "status": "running",
        "endpoints": {
            "webhook": "POST /webhook",
            "test": "GET /test"
        },
        "usage": {
            "test_mode": "Add 'testMode': true to your webhook payload",
            "example": {
                "ticker": "BTCUSDT.P",
                "price": 50000,
                "side": "BUY",
                "quantity": 100,
                "testMode": true
            }
        }
    }

if __name__ == "__main__":
    print("🚀 TradingView Webhook Bot Başlatılıyor...")
    print("ℹ️  Test modu için webhook mesajına 'testMode': true ekleyin")
    print("🌐 Sunucu: http://localhost:5000")
    print("📌 Test endpoint: http://localhost:5000/test")
    print("\n" + "="*60)
    app.run(host="0.0.0.0", port=5000, debug=True)
