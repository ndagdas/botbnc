from flask import Flask, request, jsonify
import json
import pandas as pd
import ccxt
import logging
import os
from datetime import datetime

# Logging ayarı
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Global değişkenler
longPozisyonda = False
shortPozisyonda = False
pozisyondami = False
current_symbol = ""
exchange_instance = None

def validate_api_keys(api_key, secret_key):
    """API anahtarlarını kontrol et"""
    if not api_key or not secret_key:
        logger.error("API anahtarları eksik!")
        return False
    
    if len(api_key) < 20 or len(secret_key) < 20:
        logger.error("API anahtarları çok kısa!")
        return False
    
    return True

def initialize_exchange(api_key=None, secret_key=None, use_testnet=True):
    """Binance bağlantısını başlat (testnet veya mainnet)"""
    global exchange_instance
    
    try:
        # Önce environment variables'dan al
        env_api_key = os.environ.get("BINANCE_TESTNET_API_KEY", "")
        env_secret_key = os.environ.get("BINANCE_TESTNET_SECRET_KEY", "")
        
        # Kullanılacak anahtarları belirle
        if not api_key or not secret_key:
            api_key = env_api_key
            secret_key = env_secret_key
        
        # API anahtarlarını kontrol et
        if not validate_api_keys(api_key, secret_key):
            return None
        
        logger.info(f"API Key (ilk 10 karakter): {api_key[:10]}...")
        
        # Testnet veya Mainnet seçimi
        if use_testnet:
            logger.info("🔧 TESTNET modunda bağlanılıyor...")
            exchange_config = {
                'apiKey': api_key,
                'secret': secret_key,
                'options': {
                    'adjustForTimeDifference': True,
                    'defaultType': 'future',
                },
                'enableRateLimit': True,
                'urls': {
                    'api': {
                        'public': 'https://testnet.binancefuture.com/fapi/v1',
                        'private': 'https://testnet.binancefuture.com/fapi/v1',
                    }
                }
            }
        else:
            logger.info("🌐 MAINNET modunda bağlanılıyor...")
            exchange_config = {
                'apiKey': api_key,
                'secret': secret_key,
                'options': {
                    'adjustForTimeDifference': True,
                    'defaultType': 'future',
                },
                'enableRateLimit': True,
            }
        
        # Exchange instance oluştur
        exchange_instance = ccxt.binance(exchange_config)
        
        # Bağlantıyı test et
        logger.info("Bağlantı test ediliyor...")
        balance = exchange_instance.fetch_balance()
        
        # Bakiyeyi logla
        total_usdt = balance.get('USDT', {}).get('total', 0)
        logger.info(f"✅ Bağlantı başarılı! Bakiyeniz: {total_usdt} USDT")
        
        return exchange_instance
        
    except ccxt.AuthenticationError as e:
        logger.error(f"❌ Kimlik doğrulama hatası: {e}")
        logger.error("Lütfen API anahtarlarınızı kontrol edin:")
        logger.error("1. Testnet için doğru mu?")
        logger.error("2. Futures izni verildi mi?")
        logger.error("3. IP kısıtlaması var mı?")
        return None
    except Exception as e:
        logger.error(f"❌ Bağlantı hatası: {e}")
        return None

def check_position(symbol):
    """Mevcut pozisyonları kontrol et"""
    global longPozisyonda, shortPozisyonda, pozisyondami
    
    try:
        if not exchange_instance:
            return pd.DataFrame()
        
        balance = exchange_instance.fetch_balance()
        positions = balance['info'].get('positions', [])
        
        # Sembolü temizle (BTCUSDT.P -> BTCUSDT)
        clean_symbol = symbol.replace('.P', '')
        
        current_positions = [
            p for p in positions
            if float(p['positionAmt']) != 0 and p['symbol'] == clean_symbol
        ]
        
        position_bilgi = pd.DataFrame(current_positions)
        
        if not position_bilgi.empty:
            pozisyondami = True
            pos_amt = float(position_bilgi.iloc[-1]['positionAmt'])
            longPozisyonda = pos_amt > 0
            shortPozisyonda = pos_amt < 0
            logger.info(f"📊 Pozisyon: {clean_symbol}, Miktar: {pos_amt}, "
                       f"Long: {longPozisyonda}, Short: {shortPozisyonda}")
        else:
            pozisyondami = False
            longPozisyonda = False
            shortPozisyonda = False
        
        return position_bilgi
        
    except Exception as e:
        logger.error(f"Pozisyon kontrol hatası: {e}")
        return pd.DataFrame()

@app.route("/webhook", methods=['POST'])
def webhook():
    """TradingView webhook sinyallerini işle"""
    global longPozisyonda, shortPozisyonda, pozisyondami, current_symbol, exchange_instance
    
    try:
        data = json.loads(request.data)
        logger.info(f"📩 Webhook alındı. İşlem: {data.get('side')}, "
                   f"Sembol: {data.get('ticker')}")
        
        # Verileri çıkar
        ticker = data.get('ticker', 'BTCUSDT.P')
        price = float(data.get('price', 0))
        islem = data.get('side', '').upper()
        quantity_usd = float(data.get('quantity', 0))
        use_testnet = data.get('useTestnet', True)
        
        # Sembolü temizle
        symbol = ticker.replace('.P', '') if '.P' in ticker else ticker
        current_symbol = symbol
        
        # API anahtarlarını al
        binanceapi = data.get('binanceApiKey', '')
        binancesecret = data.get('binanceSecretKey', '')
        
        # Exchange'i başlat
        exchange_instance = initialize_exchange(binanceapi, binancesecret, use_testnet)
        
        if not exchange_instance:
            return jsonify({
                "error": "Exchange bağlantısı kurulamadı",
                "details": "API anahtarlarınızı kontrol edin. "
                          "Testnet için yeni anahtar oluşturmanız gerekebilir."
            }), 500
        
        # Mevcut pozisyonu kontrol et
        position_bilgi = check_position(symbol)
        
        # İşlem mantığı (önceki kodun aynısı)
        # ================= BUY İŞLEMİ =================
        if islem == "BUY":
            if not longPozisyonda:
                if shortPozisyonda:
                    # SHORT pozisyonu kapat
                    pos_amt = abs(float(position_bilgi.iloc[-1]['positionAmt']))
                    exchange_instance.create_market_buy_order(
                        symbol, pos_amt, {"reduceOnly": True}
                    )
                    logger.info(f"📤 SHORT pozisyon kapatıldı: {pos_amt} {symbol}")
                
                # Yeni LONG pozisyon aç
                alinacak_miktar = quantity_usd / price
                order = exchange_instance.create_market_buy_order(symbol, alinacak_miktar)
                logger.info(f"✅ BUY emri başarılı: {alinacak_miktar} {symbol}")
        
        # ================= SELL İŞLEMİ =================
        elif islem == "SELL":
            if not shortPozisyonda:
                if longPozisyonda:
                    # LONG pozisyonu kapat
                    pos_amt = float(position_bilgi.iloc[-1]['positionAmt'])
                    exchange_instance.create_market_sell_order(
                        symbol, pos_amt, {"reduceOnly": True}
                    )
                    logger.info(f"📤 LONG pozisyon kapatıldı: {pos_amt} {symbol}")
                
                # Yeni SHORT pozisyon aç
                alinacak_miktar = quantity_usd / price
                order = exchange_instance.create_market_sell_order(symbol, alinacak_miktar)
                logger.info(f"✅ SELL emri başarılı: {alinacak_miktar} {symbol}")
        
        # ================= TP1, TP2, STOP İŞLEMLERİ =================
        # (Önceki koddaki aynı mantık buraya gelecek)
        # ... TP1, TP2, STOP işlemleri ...
        
        # Pozisyon durumunu güncelle
        check_position(symbol)
        
        return jsonify({
            "code": "success",
            "message": f"{islem} işlemi tamamlandı",
            "symbol": symbol,
            "has_position": pozisyondami
        }), 200
    
    except Exception as e:
        logger.error(f"❌ Webhook hatası: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/test-connection", methods=['GET'])
def test_connection():
    """API bağlantısını test et"""
    try:
        # Environment variables'dan anahtarları al
        api_key = os.environ.get("BINANCE_TESTNET_API_KEY", "")
        secret_key = os.environ.get("BINANCE_TESTNET_SECRET_KEY", "")
        
        if not api_key or not secret_key:
            return jsonify({
                "status": "error",
                "message": "API anahtarları environment variables'da bulunamadı"
            }), 400
        
        # Bağlantıyı test et
        exchange = initialize_exchange(api_key, secret_key, use_testnet=True)
        
        if exchange:
            # Bakiye bilgisini al
            balance = exchange.fetch_balance()
            total_usdt = balance.get('USDT', {}).get('total', 0)
            
            return jsonify({
                "status": "success",
                "message": "✅ Binance Futures Testnet bağlantısı başarılı",
                "balance_usdt": total_usdt,
                "api_key_prefix": api_key[:8] + "..."
            }), 200
        else:
            return jsonify({
                "status": "error",
                "message": "❌ Bağlantı kurulamadı. API anahtarlarınızı kontrol edin."
            }), 500
            
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route("/", methods=['GET'])
def index():
    """Ana sayfa ve bağlantı testi"""
    return """
    <h1>Binance Futures Testnet Trading Bot</h1>
    <p>Bot çalışıyor. Endpoint'ler:</p>
    <ul>
        <li><strong>POST /webhook</strong> - TradingView sinyalleri</li>
        <li><strong>GET /test-connection</strong> - API bağlantı testi</li>
        <li><strong>GET /health</strong> - Sağlık kontrolü</li>
        <li><strong>GET /balance</strong> - Bakiye sorgulama</li>
    </ul>
    <p><a href="/test-connection">API Bağlantı Testi Yap</a></p>
    """

# Diğer endpoint'ler (health, balance, position) önceki koddaki gibi kalacak
# ...

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🚀 Trading bot başlatılıyor (Port: {port})...")
    app.run(host="0.0.0.0", port=port, debug=False)
