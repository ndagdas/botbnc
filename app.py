from flask import Flask, request
import json
import pandas as pd
import ccxt
from datetime import datetime

longPozisyonda = False
shortPozisyonda = False
pozisyondami = False

app = Flask(__name__)

class MockBinanceExchange:
    """Test modu için mock Binance exchange sınıfı"""
    def __init__(self, symbol=""):
        self.positions = []
        self.orders_history = []
        self.balance = {
            'USDT': {'free': 10000, 'used': 0, 'total': 10000}
        }
        self.symbol = symbol
        print("⚠️  MOCK MODU: Sanal işlem yapılıyor")
    
    def fetch_balance(self, params=None):
        return {
            'USDT': self.balance['USDT'],
            'info': {
                'positions': self.positions,
                'totalWalletBalance': '10000'
            }
        }
    
    def create_market_buy_order(self, symbol, amount, params=None):
        order_id = f"mock_buy_{len(self.orders_history)}_{int(datetime.now().timestamp())}"
        reduce_only = params.get('reduceOnly', False) if params else False
        
        order = {
            'id': order_id,
            'symbol': symbol,
            'side': 'BUY',
            'amount': amount,
            'price': 0,
            'cost': 0,
            'status': 'closed',
            'timestamp': datetime.now().isoformat(),
            'params': params
        }
        
        self._update_position(symbol, amount, 'BUY', params)
        self.orders_history.append(order)
        
        print(f"✅ [MOCK] MARKET BUY: {symbol} - {amount:.6f} adet")
        print(f"   📋 Order ID: {order_id}")
        return order
    
    def create_market_sell_order(self, symbol, amount, params=None):
        order_id = f"mock_sell_{len(self.orders_history)}_{int(datetime.now().timestamp())}"
        reduce_only = params.get('reduceOnly', False) if params else False
        
        order = {
            'id': order_id,
            'symbol': symbol,
            'side': 'SELL',
            'amount': amount,
            'price': 0,
            'cost': 0,
            'status': 'closed',
            'timestamp': datetime.now().isoformat(),
            'params': params
        }
        
        self._update_position(symbol, amount, 'SELL', params)
        self.orders_history.append(order)
        
        print(f"✅ [MOCK] MARKET SELL: {symbol} - {amount:.6f} adet")
        print(f"   📋 Order ID: {order_id}")
        return order
    
    def _update_position(self, symbol, amount, side, params):
        reduce_only = params.get('reduceOnly', False) if params else False
        
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
            if side == 'BUY' and current_amount < 0:
                new_amount = min(current_amount + amount, 0)
                pos['positionAmt'] = str(new_amount)
                print(f"   📊 Short azaltıldı: {current_amount:.6f} → {new_amount:.6f}")
            elif side == 'SELL' and current_amount > 0:
                new_amount = max(current_amount - amount, 0)
                pos['positionAmt'] = str(new_amount)
                print(f"   📊 Long azaltıldı: {current_amount:.6f} → {new_amount:.6f}")
        else:
            if side == 'BUY':
                new_amount = current_amount + amount
                pos['positionAmt'] = str(new_amount)
                print(f"   📊 Long eklendi: {current_amount:.6f} → {new_amount:.6f}")
            elif side == 'SELL':
                new_amount = current_amount - amount
                pos['positionAmt'] = str(new_amount)
                print(f"   📊 Short eklendi: {current_amount:.6f} → {new_amount:.6f}")
        
        if float(pos['positionAmt']) == 0:
            self.positions.remove(pos)
    
    def print_summary(self):
        print("\n" + "="*60)
        print("📊 MOCK MODU ÖZETİ")
        print("="*60)
        print(f"Toplam İşlem: {len(self.orders_history)}")
        
        if self.positions:
            print("\n📈 AKTİF POZİSYONLAR:")
            for pos in self.positions:
                amount = float(pos['positionAmt'])
                side = "LONG" if amount > 0 else "SHORT"
                print(f"   {pos['symbol']}: {abs(amount):.6f} ({side})")
        else:
            print("\n📭 AKTİF POZİSYON YOK")
        
        print(f"\n💰 SANAL BAKİYE: {self.balance['USDT']['total']} USDT")
        print("="*60)

def create_exchange(data):
    """Exchange objesi oluştur - DOĞRU ENDPOINT'lerle"""
    test_mode = data.get('testMode', True)
    
    if test_mode:
        symbol = data.get('ticker', '').replace('.P', '')
        return MockBinanceExchange(symbol)
    
    api_key = data.get('binanceApiKey', '').strip()
    secret_key = data.get('binanceSecretKey', '').strip()
    
    if not api_key or not secret_key:
        print("❌ API key eksik! Mock moda geçiliyor...")
        symbol = data.get('ticker', '').replace('.P', '')
        return MockBinanceExchange(symbol)
    
    # Testnet mi gerçek mi?
    use_testnet = data.get('useTestnet', False)
    
    try:
        print(f"🔑 API Key: {api_key[:8]}...{api_key[-4:] if len(api_key) > 12 else ''}")
        
        if use_testnet:
            print("🌐 BINANCE FUTURES TESTNET kullanılıyor")
            exchange = ccxt.binance({
                'apiKey': api_key,
                'secret': secret_key,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future',
                    'adjustForTimeDifference': True,
                },
                'urls': {
                    'api': {
                        'public': 'https://testnet.binancefuture.com/fapi/v1',
                        'private': 'https://testnet.binancefuture.com/fapi/v1',
                        'test': 'https://testnet.binancefuture.com/fapi/v1',
                    }
                }
            })
        else:
            print("🚀 GERÇEK BINANCE FUTURES kullanılıyor")
            exchange = ccxt.binance({
                'apiKey': api_key,
                'secret': secret_key,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future',  # BU ÇOK ÖNEMLİ!
                    'adjustForTimeDifference': True,
                },
                'urls': {
                    'api': {
                        'public': 'https://fapi.binance.com/fapi/v1',
                        'private': 'https://fapi.binance.com/fapi/v1',
                        'test': 'https://fapi.binance.com/fapi/v1',
                    }
                }
            })
        
        # Bağlantı testi - futures için özel endpoint
        print("🔍 Futures API test ediliyor...")
        exchange.fetch_time()
        
        # Markets yükle (futures için)
        print("📊 Markets yükleniyor...")
        markets = exchange.load_markets()
        
        # Hesap bilgilerini al
        print("👤 Futures hesap bilgileri alınıyor...")
        balance = exchange.fetch_balance()
        
        if 'info' in balance:
            print(f"✅ Binance Futures bağlantısı başarılı!")
            if 'totalWalletBalance' in balance['info']:
                print(f"💰 Wallet Balance: {balance['info']['totalWalletBalance']} USDT")
            return exchange
        else:
            raise Exception("Futures hesap bilgisi alınamadı")
            
    except ccxt.AuthenticationError as e:
        print(f"❌ API KEY HATASI: {str(e)}")
        print("⚠️  API key kontrol listesi:")
        print("   1. Binance Futures API oluşturun")
        print("   2. 'Enable Futures' seçeneğini aktif edin")
        print("   3. IP whitelist'i devre dışı bırakın")
        print("   4. API key'iniz Futures için yetkili mi?")
        symbol = data.get('ticker', '').replace('.P', '')
        return MockBinanceExchange(symbol)
        
    except Exception as e:
        print(f"❌ Binance bağlantı hatası: {str(e)}")
        print("⚠️  Mock moda geçiliyor...")
        symbol = data.get('ticker', '').replace('.P', '')
        return MockBinanceExchange(symbol)

@app.route("/webhook", methods=['POST'])
def webhook():
    global longPozisyonda, shortPozisyonda, pozisyondami
    
    try:
        data = json.loads(request.data)
        print("\n" + "="*60)
        print(f"📨 WEBHOOK - {datetime.now().strftime('%H:%M:%S')}")
        print("="*60)
        
        # Mod kontrolü
        test_mode = data.get('testMode', True)
        use_testnet = data.get('useTestnet', False)
        
        if test_mode:
            mode_text = "MOCK TEST"
        elif use_testnet:
            mode_text = "BINANCE TESTNET"
        else:
            mode_text = "GERÇEK BINANCE FUTURES"
        
        print(f"🔧 MOD: {mode_text}")
        
        if not test_mode and not use_testnet:
            print("⚠️  DİKKAT: Gerçek Futures işlemi! Para kaybedebilirsiniz!")
        
        # Verileri al
        ticker = data.get('ticker', '')
        veri = ticker.split(".")
        symbol = veri[0] if veri else ''
        
        # .P uzantısını kaldır
        if symbol.endswith('.P'):
            symbol = symbol.replace('.P', '')
        
        price = float(data.get('price', 0))
        islem = data.get('side', '').upper()
        quantity = float(data.get('quantity', 0))
        
        print(f"📊 Sembol: {symbol}")
        print(f"💰 Fiyat: {price}")
        print(f"🎯 İşlem: {islem}")
        print(f"📦 Miktar: {quantity} USDT")
        
        # Exchange objesini oluştur
        exchange = create_exchange(data)
        
        # Mock mu gerçek mi kontrol et
        is_mock = isinstance(exchange, MockBinanceExchange)
        
        # Pozisyon bilgilerini al
        try:
            balance = exchange.fetch_balance()
            positions = balance['info'].get('positions', [])
            
            current_positions = [
                p for p in positions
                if float(p.get('positionAmt', 0)) != 0 and p.get('symbol') == symbol
            ]
            
            position_bilgi = pd.DataFrame(current_positions)
            
            if not position_bilgi.empty:
                pozisyondami = True
                pos_amt = float(position_bilgi.iloc[-1].get('positionAmt', 0))
                longPozisyonda = pos_amt > 0
                shortPozisyonda = pos_amt < 0
                
                print(f"📈 POZİSYON: {abs(pos_amt):.6f} ({'LONG' if longPozisyonda else 'SHORT'})")
                if not is_mock and not position_bilgi.iloc[-1].empty:
                    entry_price = position_bilgi.iloc[-1].get('entryPrice', 'N/A')
                    unrealized_pnl = position_bilgi.iloc[-1].get('unRealizedProfit', 'N/A')
                    print(f"   🏷️  Entry: {entry_price}")
                    print(f"   📈 PnL: {unrealized_pnl}")
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
        
        # İşlem yap
        print("\n⚡ İŞLEM YÜRÜTÜLÜYOR...")
        
        if islem == "BUY":
            if not longPozisyonda:
                # Short pozisyon varsa kapat
                if shortPozisyonda and not position_bilgi.empty:
                    short_amount = abs(float(position_bilgi.iloc[-1].get('positionAmt', 0)))
                    print(f"🔄 Short kapatılıyor: {short_amount:.6f} adet")
                    exchange.create_market_buy_order(
                        symbol, short_amount, {"reduceOnly": True}
                    )
                
                # Yeni long aç
                if quantity > 0:
                    alinacak_miktar = quantity / price
                    print(f"🟢 Long açılıyor: {alinacak_miktar:.6f} adet")
                    order = exchange.create_market_buy_order(symbol, alinacak_miktar)
                    print(f"✅ BUY tamamlandı")
                else:
                    print("⚠️  Quantity 0, işlem yapılmadı")
            else:
                print("ℹ️  Zaten LONG pozisyonda")
        
        elif islem == "SELL":
            if not shortPozisyonda:
                # Long pozisyon varsa kapat
                if longPozisyonda and not position_bilgi.empty:
                    long_amount = float(position_bilgi.iloc[-1].get('positionAmt', 0))
                    print(f"🔄 Long kapatılıyor: {long_amount:.6f} adet")
                    exchange.create_market_sell_order(
                        symbol, long_amount, {"reduceOnly": True}
                    )
                
                # Yeni short aç
                if quantity > 0:
                    alinacak_miktar = quantity / price
                    print(f"🔴 Short açılıyor: {alinacak_miktar:.6f} adet")
                    order = exchange.create_market_sell_order(symbol, alinacak_miktar)
                    print(f"✅ SELL tamamlandı")
                else:
                    print("⚠️  Quantity 0, işlem yapılmadı")
            else:
                print("ℹ️  Zaten SHORT pozisyonda")
        
        elif islem in ["TP1", "TP2", "STOP"]:
            if pozisyondami and not position_bilgi.empty:
                pozisyon_miktari = abs(float(position_bilgi.iloc[-1].get('positionAmt', 0)))
                
                if islem == "TP1":
                    alinacak = pozisyon_miktari * 0.50
                    print(f"🎯 TP1 (%50): {alinacak:.6f} adet")
                elif islem == "TP2":
                    alinacak = pozisyon_miktari * 0.30
                    print(f"🎯 TP2 (%30): {alinacak:.6f} adet")
                elif islem == "STOP":
                    alinacak = pozisyon_miktari
                    print(f"🛑 STOP: {alinacak:.6f} adet")
                
                if longPozisyonda:
                    exchange.create_market_sell_order(
                        symbol, alinacak, {"reduceOnly": True}
                    )
                elif shortPozisyonda:
                    exchange.create_market_buy_order(
                        symbol, alinacak, {"reduceOnly": True}
                    )
                
                print(f"✅ {islem} tamamlandı")
            else:
                print(f"⚠️  {islem} için aktif pozisyon yok")
        
        else:
            print(f"❌ Geçersiz işlem: {islem}")
        
        # Mock moddaysa özet göster
        if is_mock:
            exchange.print_summary()
        
        print("="*60)
        
        return {
            "code": "success",
            "mode": mode_text,
            "symbol": symbol,
            "action": islem,
            "timestamp": datetime.now().isoformat()
        }
    
    except Exception as e:
        print(f"❌ HATA: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "code": "error",
            "message": str(e),
            "timestamp": datetime.now().isoformat()
        }

@app.route('/test', methods=['GET'])
def test_endpoint():
    """Test endpoint"""
    return {
        "status": "running",
        "time": datetime.now().isoformat(),
        "endpoints": {
            "webhook": "POST /webhook",
            "test": "GET /test"
        },
        "modes": {
            "mock": "testMode: true",
            "testnet": "testMode: false, useTestnet: true",
            "real": "testMode: false, useTestnet: false"
        }
    }

if __name__ == "__main__":
    print("🚀 TradingView Webhook Bot Başlatıldı")
    print("📅", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("\n⚠️  ÖNEMLİ UYARILAR:")
    print("1. Varsayılan MOCK modda çalışır (güvenli)")
    print("2. Binance Testnet için: useTestnet: true")
    print("3. Gerçek Futures için: testMode: false, useTestnet: false")
    print("\n🌐 Endpoint: POST /webhook")
    print("🔗 Health check: GET /test")
    print("="*60)
    app.run(host="0.0.0.0", port=5000, debug=True)
