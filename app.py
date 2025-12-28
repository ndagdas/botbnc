from flask import Flask, request
import json
import pandas as pd
import ccxt
import hmac
import hashlib

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
    
    def fetch_balance(self, params=None):
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
    test_mode = data.get('testMode', True)  # Varsayılan olarak True
    
    print(f"🔧 Test modu: {test_mode}")
    
    if test_mode:
        return MockBinanceExchange()
    
    # GERÇEK MOD - Binance Futures API için doğru konfigürasyon
    binanceapi = data.get('binanceApiKey', '')
    binancesecret = data.get('binanceSecretKey', '')
    
    if not binanceapi or not binancesecret:
        print("⚠️  API key bulunamadı! Test moduna geçiliyor...")
        return MockBinanceExchange()
    
    try:
        # Binance Futures için doğru konfigürasyon
        exchange = ccxt.binance({
            'apiKey': binanceapi.strip(),
            'secret': binancesecret.strip(),
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',  # FUTURES trading
                'adjustForTimeDifference': True,
            },
            'urls': {
                'api': {
                    'public': 'https://fapi.binance.com',
                    'private': 'https://fapi.binance.com',
                }
            }
        })
        
        # API key kontrolü
        print("🔑 API Key doğrulanıyor...")
        exchange.check_required_credentials()
        
        # Basit bir test yapalım
        print("🔍 Bağlantı testi yapılıyor...")
        exchange.fetch_time()
        print("✅ Binance API bağlantısı başarılı!")
        
        return exchange
        
    except Exception as e:
        print(f"❌ Binance API bağlantı hatası: {str(e)}")
        print("⚠️  Test moduna geçiliyor...")
        return MockBinanceExchange()

@app.route("/webhook", methods=['POST'])
def webhook():
    global longPozisyonda, shortPozisyonda, pozisyondami
    
    try:
        data = json.loads(request.data)
        print("\n" + "="*60)
        print("📨 WEBHOOK ALINDI")
        print("="*60)
        
        # Test modunu kontrol et (varsayılan: test modu)
        test_mode = data.get('testMode', True)
        mode_text = "TEST" if test_mode else "GERÇEK"
        print(f"🔧 MOD: {mode_text}")
        
        # Güvenlik kontrolü - test modunda bile API key gönderilmişse uyar
        if test_mode and ('binanceApiKey' in data or 'binanceSecretKey' in data):
            print("⚠️  DİKKAT: Test modunda ama API key gönderildi!")
            print("⚠️  API key'ler göz ardı edilecek...")
        
        # Verileri al
        ticker = data.get('ticker', 'BTCUSDT.P')
        veri = ticker.split(".")
        symbol = veri[0] if veri else ''
        
        # Binance Futures sembol formatına çevir (BTCUSDT.P → BTCUSDT)
        if symbol.endswith('.P'):
            symbol = symbol.replace('.P', '')
        
        price = float(data.get('price', 0))
        islem = data.get('side', '')
        quantity = float(data.get('quantity', 0))
        
        if not price or not islem:
            print("❌ Eksik veri! price veya side bulunamadı.")
            return {"code": "error", "message": "Eksik veri"}
        
        print(f"📊 Sembol: {symbol}")
        print(f"💰 Fiyat: {price}")
        print(f"🎯 İşlem: {islem}")
        print(f"📦 Miktar: {quantity} USDT")
        
        # Exchange objesini al
        exchange = get_exchange(data)
        
        # Pozisyon bilgilerini al
        try:
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
                
        except Exception as e:
            print(f"⚠️  Pozisyon bilgisi alınamadı: {str(e)}")
            pozisyondami = False
            longPozisyonda = False
            shortPozisyonda = False
            position_bilgi = pd.DataFrame()
        
        # İşlemleri yap
        print("\n⚡ İŞLEM YÜRÜTÜLÜYOR...")
        
        # ================= BUY =================
        if islem == "BUY":
            if not longPozisyonda:
                if shortPozisyonda and not position_bilgi.empty:
                    print("🔄 Short pozisyon kapatılıyor...")
                    exchange.create_market_buy_order(
                        symbol,
                        abs(float(position_bilgi.iloc[-1].get('positionAmt', 0))),
                        {"reduceOnly": True}
                    )
                
                if quantity > 0:
                    alinacak_miktar = quantity / price
                    print(f"🟢 Long pozisyon açılıyor: {alinacak_miktar:.6f} adet")
                    order = exchange.create_market_buy_order(symbol, alinacak_miktar)
                    print(f"✅ BUY Order Tamamlandı")
                else:
                    print("⚠️  Quantity 0, işlem yapılmadı")
            else:
                print("ℹ️  Zaten LONG pozisyonda, işlem yapılmadı")
        
        # ================= SELL =================
        elif islem == "SELL":
            if not shortPozisyonda:
                if longPozisyonda and not position_bilgi.empty:
                    print("🔄 Long pozisyon kapatılıyor...")
                    exchange.create_market_sell_order(
                        symbol,
                        float(position_bilgi.iloc[-1].get('positionAmt', 0)),
                        {"reduceOnly": True}
                    )
                
                if quantity > 0:
                    alinacak_miktar = quantity / price
                    print(f"🔴 Short pozisyon açılıyor: {alinacak_miktar:.6f} adet")
                    order = exchange.create_market_sell_order(symbol, alinacak_miktar)
                    print(f"✅ SELL Order Tamamlandı")
                else:
                    print("⚠️  Quantity 0, işlem yapılmadı")
            else:
                print("ℹ️  Zaten SHORT pozisyonda, işlem yapılmadı")
        
        # ================= TP1 → %50 KAR =================
        elif islem == "TP1" and pozisyondami and not position_bilgi.empty:
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
        elif islem == "TP2" and pozisyondami and not position_bilgi.empty:
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
        
        # ================= STOP → KALAN TÜM POZİSYON =================
        elif islem == "STOP" and pozisyondami and not position_bilgi.empty:
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
            "test_mode": "Varsayılan olarak test modu aktif",
            "gercek_mod": "Gerçek mod için 'testMode': false ve API key'ler gerekli",
            "example_test": {
                "ticker": "BTCUSDT.P",
                "price": 50000,
                "side": "BUY",
                "quantity": 100,
                "testMode": true
            },
            "example_real": {
                "ticker": "BTCUSDT.P",
                "price": 50000,
                "side": "BUY",
                "quantity": 100,
                "testMode": false,
                "binanceApiKey": "API_KEY_HERE",
                "binanceSecretKey": "SECRET_KEY_HERE"
            }
        }
    }

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "tradingview-webhook-bot"}

if __name__ == "__main__":
    print("🚀 TradingView Webhook Bot Başlatılıyor...")
    print("⚠️  UYARI: Varsayılan olarak TEST MODU aktif!")
    print("ℹ️  Gerçek işlem yapmak için 'testMode': false gönderin ve API key'lerinizi ekleyin")
    print("🌐 Sunucu: http://localhost:5000")
    print("📌 Test endpoint: http://localhost:5000/test")
    print("❤️  Health check: http://localhost:5000/health")
    print("\n" + "="*60)
    app.run(host="0.0.0.0", port=5000, debug=True)
