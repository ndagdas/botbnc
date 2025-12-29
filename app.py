# app.py - Binance Futures Testnet Trading Bot
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

def initialize_exchange(api_key=None, secret_key=None):
    """Binance Futures Testnet bağlantısını başlat"""
    global exchange_instance
    
    try:
        # Environment variables'den API anahtarlarını al
        if not api_key:
            api_key = os.environ.get("BINANCE_TESTNET_API_KEY", "")
        if not secret_key:
            secret_key = os.environ.get("BINANCE_TESTNET_SECRET_KEY", "")
        
        if not api_key or not secret_key:
            logger.error("API anahtarları bulunamadı!")
            return None
        
        # CCXT Binance Futures Testnet konfigürasyonu
        exchange_instance = ccxt.binance({
            'apiKey': api_key,
            'secret': secret_key,
            'options': {
                'adjustForTimeDifference': True,
                'defaultType': 'future',
                # 'testnet': True  # Bu satır artık kullanılmıyor
            },
            'enableRateLimit': True,
            'urls': {
                'api': {
                    'public': 'https://testnet.binancefuture.com/fapi/v1',
                    'private': 'https://testnet.binancefuture.com/fapi/v1',
                }
            }
        })
        
        # NOT: set_sandbox_mode(True) ARTIK KULLANILMIYOR!
        # exchange_instance.set_sandbox_mode(True)  # BU SATIRI SİLİN
        
        # Bağlantıyı test et
        exchange_instance.fetch_balance()
        logger.info("✅ Binance Futures Testnet bağlantısı başarılı")
        return exchange_instance
        
    except Exception as e:
        logger.error(f"❌ Exchange bağlantı hatası: {e}")
        return None

def check_position(symbol):
    """Mevcut pozisyonları kontrol et"""
    global longPozisyonda, shortPozisyonda, pozisyondami
    
    try:
        if not exchange_instance:
            return pd.DataFrame()
        
        balance = exchange_instance.fetch_balance()
        positions = balance['info'].get('positions', [])
        
        current_positions = [
            p for p in positions
            if float(p['positionAmt']) != 0 and p['symbol'] == symbol
        ]
        
        position_bilgi = pd.DataFrame(current_positions)
        
        if not position_bilgi.empty:
            pozisyondami = True
            pos_amt = float(position_bilgi.iloc[-1]['positionAmt'])
            longPozisyonda = pos_amt > 0
            shortPozisyonda = pos_amt < 0
            logger.info(f"📊 Pozisyon bulundu: {symbol}, Miktar: {pos_amt}")
        else:
            pozisyondami = False
            longPozisyonda = False
            shortPozisyonda = False
            logger.info(f"📊 {symbol} için pozisyon bulunamadı")
        
        return position_bilgi
        
    except Exception as e:
        logger.error(f"Pozisyon kontrol hatası: {e}")
        return pd.DataFrame()

def close_position(symbol, position_info):
    """Mevcut pozisyonu kapat"""
    try:
        if not position_info.empty:
            pos_amt = abs(float(position_info.iloc[-1]['positionAmt']))
            
            if longPozisyonda:
                order = exchange_instance.create_market_sell_order(
                    symbol, pos_amt, {"reduceOnly": True}
                )
                logger.info(f"📤 LONG pozisyon kapatıldı: {order}")
                return order
            elif shortPozisyonda:
                order = exchange_instance.create_market_buy_order(
                    symbol, pos_amt, {"reduceOnly": True}
                )
                logger.info(f"📤 SHORT pozisyon kapatıldı: {order}")
                return order
    except Exception as e:
        logger.error(f"Pozisyon kapatma hatası: {e}")
    return None

@app.route("/webhook", methods=['POST'])
def webhook():
    """TradingView webhook sinyallerini işle"""
    global longPozisyonda, shortPozisyonda, pozisyondami, current_symbol
    
    try:
        data = json.loads(request.data)
        logger.info(f"📩 Gelen webhook verisi: {data}")
        
        # Verileri çıkar
        ticker = data.get('ticker', 'BTCUSDT.P')
        price = float(data.get('price', 0))
        islem = data.get('side', '').upper()
        quantity_usd = float(data.get('quantity', 0))
        
        # Sembolü düzelt (BTCUSDT.P -> BTCUSDT)
        symbol = ticker.replace('.P', '') if '.P' in ticker else ticker
        current_symbol = symbol
        
        # API anahtarlarını al (webhook'tan veya environment'dan)
        binanceapi = data.get('binanceApiKey') or os.environ.get("BINANCE_TESTNET_API_KEY", "")
        binancesecret = data.get('binanceSecretKey') or os.environ.get("BINANCE_TESTNET_SECRET_KEY", "")
        
        # Exchange'i başlat
        if not initialize_exchange(binanceapi, binancesecret):
            return jsonify({"error": "Exchange bağlantısı kurulamadı"}), 500
        
        # Mevcut pozisyonu kontrol et
        position_bilgi = check_position(symbol)
        
        logger.info(f"🎯 İşlem: {islem}, Sembol: {symbol}, Fiyat: ${price}, Miktar: ${quantity_usd}")
        
        # ================= BUY İŞLEMİ =================
        if islem == "BUY":
            if not longPozisyonda:
                # Karşıt pozisyon varsa kapat
                if shortPozisyonda:
                    close_position(symbol, position_bilgi)
                    position_bilgi = check_position(symbol)
                
                # Miktarı hesapla
                alinacak_miktar = quantity_usd / price
                
                # LONG pozisyon aç
                order = exchange_instance.create_market_buy_order(symbol, alinacak_miktar)
                logger.info(f"✅ BUY emri başarılı: {order}")
        
        # ================= SELL İŞLEMİ =================
        elif islem == "SELL":
            if not shortPozisyonda:
                # Karşıt pozisyon varsa kapat
                if longPozisyonda:
                    close_position(symbol, position_bilgi)
                    position_bilgi = check_position(symbol)
                
                # Miktarı hesapla
                alinacak_miktar = quantity_usd / price
                
                # SHORT pozisyon aç
                order = exchange_instance.create_market_sell_order(symbol, alinacak_miktar)
                logger.info(f"✅ SELL emri başarılı: {order}")
        
        # ================= TP1 → %50 KAR AL =================
        elif islem == "TP1" and pozisyondami:
            pozisyon_miktari = abs(float(position_bilgi.iloc[-1]['positionAmt']))
            alinacak = pozisyon_miktari * 0.50  # %50
            
            if longPozisyonda:
                order = exchange_instance.create_market_sell_order(
                    symbol, alinacak, {"reduceOnly": True}
                )
            elif shortPozisyonda:
                order = exchange_instance.create_market_buy_order(
                    symbol, alinacak, {"reduceOnly": True}
                )
            
            logger.info(f"🎯 TP1 (%50) KAR emri başarılı: {order}")
        
        # ================= TP2 → %30 KAR AL =================
        elif islem == "TP2" and pozisyondami:
            pozisyon_miktari = abs(float(position_bilgi.iloc[-1]['positionAmt']))
            alinacak = pozisyon_miktari * 0.30  # %30 (önceden %50 yazıyordu, düzeltildi)
            
            if longPozisyonda:
                order = exchange_instance.create_market_sell_order(
                    symbol, alinacak, {"reduceOnly": True}
                )
            elif shortPozisyonda:
                order = exchange_instance.create_market_buy_order(
                    symbol, alinacak, {"reduceOnly": True}
                )
            
            logger.info(f"🎯 TP2 (%30) KAR emri başarılı: {order}")
        
        # ================= STOP → KALAN %20 =================
        elif islem == "STOP" and pozisyondami:
            pozisyon_miktari = abs(float(position_bilgi.iloc[-1]['positionAmt']))
            
            if longPozisyonda:
                order = exchange_instance.create_market_sell_order(
                    symbol, pozisyon_miktari, {"reduceOnly": True}
                )
            elif shortPozisyonda:
                order = exchange_instance.create_market_buy_order(
                    symbol, pozisyon_miktari, {"reduceOnly": True}
                )
            
            logger.info(f"🛑 STOP emri başarılı: {order}")
        
        else:
            logger.warning(f"⚠️ Bilinmeyen işlem: {islem}")
            return jsonify({"error": "Bilinmeyen işlem türü"}), 400
        
        # İşlem sonrası pozisyon durumunu güncelle
        check_position(symbol)
        
        return jsonify({
            "code": "success",
            "action": islem,
            "symbol": symbol,
            "has_position": pozisyondami,
            "is_long": longPozisyonda,
            "is_short": shortPozisyonda
        }), 200
    
    except Exception as e:
        logger.error(f"❌ Webhook hatası: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/health", methods=['GET'])
def health_check():
    """Sağlık kontrol endpoint'i"""
    try:
        if exchange_instance:
            exchange_instance.fetch_balance()
            status = "healthy"
        else:
            status = "exchange_not_initialized"
        
        return jsonify({
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "service": "binance-futures-testnet-bot"
        }), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

@app.route("/balance", methods=['GET'])
def get_balance():
    """Hesap bakiyesini getir"""
    try:
        if not exchange_instance:
            initialize_exchange()
        
        if exchange_instance:
            balance = exchange_instance.fetch_balance()
            
            # Sadece sıfırdan büyük bakiyeleri filtrele
            filtered_balance = {}
            for asset, info in balance['total'].items():
                if info > 0:
                    filtered_balance[asset] = {
                        'total': info,
                        'free': balance['free'].get(asset, 0),
                        'used': balance['used'].get(asset, 0)
                    }
            
            return jsonify(filtered_balance), 200
        else:
            return jsonify({"error": "Exchange başlatılamadı"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/position", methods=['GET'])
def get_position():
    """Mevcut pozisyonu getir"""
    try:
        symbol = request.args.get('symbol', current_symbol or 'BTCUSDT')
        
        if not exchange_instance:
            initialize_exchange()
        
        if exchange_instance:
            position_bilgi = check_position(symbol)
            
            if pozisyondami and not position_bilgi.empty:
                pos_info = position_bilgi.iloc[-1]
                return jsonify({
                    "symbol": symbol,
                    "positionAmt": float(pos_info['positionAmt']),
                    "entryPrice": float(pos_info['entryPrice']),
                    "unrealizedProfit": float(pos_info['unRealizedProfit']),
                    "is_long": longPozisyonda,
                    "is_short": shortPozisyonda,
                    "has_position": True
                }), 200
            else:
                return jsonify({
                    "symbol": symbol,
                    "positionAmt": 0,
                    "has_position": False
                }), 200
        else:
            return jsonify({"error": "Exchange başlatılamadı"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/", methods=['GET'])
def index():
    """Ana sayfa"""
    return jsonify({
        "message": "Binance Futures Testnet Trading Bot",
        "endpoints": {
            "webhook": "POST /webhook",
            "health": "GET /health",
            "balance": "GET /balance",
            "position": "GET /position?symbol=BTCUSDT"
        }
    })

# Heroku için port ayarı
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🚀 Trading bot başlatılıyor (Port: {port})...")
    
    # Başlangıçta exchange'i başlat
    initialize_exchange()
    
    app.run(host="0.0.0.0", port=port, debug=False)
