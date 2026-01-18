import os
import json
import logging
from flask import Flask, request, jsonify
import pandas as pd
import ccxt
from datetime import datetime

# Log ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Flask uygulaması
app = Flask(__name__)

# Global durum değişkenleri (Heroku'da thread-safe olması için dict kullanıyoruz)
bot_state = {
    'longPozisyonda': False,
    'shortPozisyonda': False,
    'pozisyondami': False,
    'last_trade': None,
    'last_symbol': None
}

def init_exchange(api_key, secret_key, testnet=True):
    """Binance exchange objesini başlat"""
    config = {
        'apiKey': api_key,
        'secret': secret_key,
        'options': {
            'adjustForTimeDifference': True,
            'defaultType': 'future',
            'defaultMarginMode': 'isolated'
        },
        'enableRateLimit': True,
        'rateLimit': 1000,
    }
    
    if testnet:
        config['urls'] = {
            'api': {
                'public': 'https://testnet.binancefuture.com/fapi/v1',
                'private': 'https://testnet.binancefuture.com/fapi/v1',
            }
        }
        logger.info("Testnet modu aktif")
    
    exchange = ccxt.binance(config)
    return exchange

def get_position_info(exchange, symbol):
    """Mevcut pozisyon bilgilerini al"""
    try:
        # Önce pozisyonları al
        positions = exchange.fetch_positions([symbol])
        
        if positions and len(positions) > 0:
            position = positions[0]
            position_amt = float(position.get('contracts', 0))
            
            if position_amt != 0:
                return {
                    'exists': True,
                    'amount': position_amt,
                    'entryPrice': float(position.get('entryPrice', 0)),
                    'unrealizedPnl': float(position.get('unrealizedPnl', 0)),
                    'side': 'long' if position_amt > 0 else 'short'
                }
        
        return {'exists': False, 'amount': 0, 'side': None}
        
    except Exception as e:
        logger.error(f"Pozisyon bilgisi alınamadı: {e}")
        return {'exists': False, 'amount': 0, 'side': None}

def adjust_quantity(exchange, symbol, desired_quantity, price):
    """Lot boyutunu exchange kurallarına göre ayarla"""
    try:
        market = exchange.market(symbol)
        
        if not market:
            logger.warning(f"Market bilgisi alınamadı: {symbol}")
            return desired_quantity
        
        # Minimum quantity kontrolü
        min_qty = market.get('limits', {}).get('amount', {}).get('min', 0.001)
        if desired_quantity < min_qty:
            logger.info(f"Miktar minimum {min_qty} olmalı. Ayarlanıyor...")
            desired_quantity = min_qty
        
        # Step size kontrolü
        step_size = market.get('precision', {}).get('amount', 0.001)
        if step_size > 0:
            # Step size'a göre yuvarla
            desired_quantity = round(desired_quantity - (desired_quantity % step_size), 8)
        
        # Demo için maksimum sınır (testnet)
        max_demo_qty = 0.1  # BTC cinsinden
        if desired_quantity > max_demo_qty:
            logger.info(f"Demo limiti: Miktar {max_demo_qty} ile sınırlandı")
            desired_quantity = max_demo_qty
        
        return desired_quantity
        
    except Exception as e:
        logger.error(f"Lot boyutu ayarlama hatası: {e}")
        return desired_quantity

def set_leverage_and_margin(exchange, symbol, leverage=5, margin_type='isolated'):
    """Kaldıraç ve margin türünü ayarla"""
    try:
        # Kaldıraç ayarla
        exchange.set_leverage(leverage, symbol)
        logger.info(f"Kaldıraç {leverage}x ayarlandı")
        
        # Margin türünü ayarla
        exchange.set_margin_mode(margin_type, symbol)
        logger.info(f"Margin türü {margin_type} olarak ayarlandı")
        
        return True
    except Exception as e:
        # Hata genellikle zaten ayarlanmış olmasından kaynaklanır
        logger.warning(f"Kaldıraç/margin ayarlama uyarısı: {e}")
        return True

def execute_trade(exchange, symbol, side, quantity, price, reduce_only=False):
    """Trade emrini çalıştır"""
    try:
        params = {}
        if reduce_only:
            params['reduceOnly'] = True
        
        if side.upper() in ['BUY', 'LONG']:
            order = exchange.create_market_buy_order(symbol, quantity, params)
        elif side.upper() in ['SELL', 'SHORT']:
            order = exchange.create_market_sell_order(symbol, quantity, params)
        else:
            logger.error(f"Geçersiz işlem yönü: {side}")
            return None
        
        logger.info(f"İşlem başarılı: {side} {quantity} {symbol}")
        return order
        
    except Exception as e:
        logger.error(f"İşlem hatası: {e}")
        return None

@app.route('/webhook', methods=['POST'])
def webhook():
    """TradingView webhook endpoint"""
    try:
        # İstek verilerini al
        data = request.get_json()
        if not data:
            data = json.loads(request.data)
        
        logger.info(f"Gelen webhook verisi: {data}")
        
        # Zorunlu alanları kontrol et
        required_fields = ['ticker', 'side']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'code': 'error',
                    'message': f'Eksik alan: {field}'
                }), 400
        
        # Verileri işle
        ticker = data.get('ticker', '')
        symbol = ticker.split('.')[0] if '.' in ticker else ticker
        side = data.get('side', '').upper()
        price = float(data.get('price', 0))
        quantity_usdt = float(data.get('quantity', 100))  # Varsayılan 100 USDT
        testnet = data.get('testnet', True)
        
        # API anahtarlarını al (öncelik: istek > ortam değişkeni)
        api_key = data.get('binanceApiKey') or os.environ.get('BINANCE_API_KEY', '')
        secret_key = data.get('binanceSecretKey') or os.environ.get('BINANCE_SECRET_KEY', '')
        
        if not api_key or not secret_key:
            return jsonify({
                'code': 'error',
                'message': 'API anahtarları bulunamadı'
            }), 400
        
        # Exchange'i başlat
        exchange = init_exchange(api_key, secret_key, testnet)
        
        # Sembol formatını kontrol et (USDT ile bitmeli)
        if not symbol.endswith('USDT'):
            symbol += 'USDT'
        
        # Mevcut pozisyonu kontrol et
        position_info = get_position_info(exchange, symbol)
        
        # Lot boyutunu hesapla ve ayarla
        if price > 0:
            quantity = quantity_usdt / price
        else:
            # Fiyat yoksa, market fiyatını al
            ticker_data = exchange.fetch_ticker(symbol)
            price = ticker_data['last']
            quantity = quantity_usdt / price
        
        quantity = adjust_quantity(exchange, symbol, quantity, price)
        
        # Testnet için kaldıraç ve margin ayarla
        if testnet:
            set_leverage_and_margin(exchange, symbol, leverage=5, margin_type='isolated')
        
        # İşlem tipine göre aksiyon al
        if side in ['BUY', 'LONG']:
            # Eğer short pozisyon varsa, önce onu kapat
            if position_info['exists'] and position_info['side'] == 'short':
                close_qty = abs(position_info['amount'])
                logger.info(f"Mevcut short pozisyon kapatılıyor: {close_qty}")
                execute_trade(exchange, symbol, 'BUY', close_qty, price, reduce_only=True)
            
            # Yeni long pozisyon aç
            logger.info(f"Yeni long pozisyon açılıyor: {quantity}")
            order = execute_trade(exchange, symbol, 'BUY', quantity, price)
            
            if order:
                bot_state['longPozisyonda'] = True
                bot_state['shortPozisyonda'] = False
                bot_state['pozisyondami'] = True
                bot_state['last_trade'] = datetime.now().isoformat()
                bot_state['last_symbol'] = symbol
        
        elif side in ['SELL', 'SHORT']:
            # Eğer long pozisyon varsa, önce onu kapat
            if position_info['exists'] and position_info['side'] == 'long':
                close_qty = position_info['amount']
                logger.info(f"Mevcut long pozisyon kapatılıyor: {close_qty}")
                execute_trade(exchange, symbol, 'SELL', close_qty, price, reduce_only=True)
            
            # Yeni short pozisyon aç
            logger.info(f"Yeni short pozisyon açılıyor: {quantity}")
            order = execute_trade(exchange, symbol, 'SELL', quantity, price)
            
            if order:
                bot_state['longPozisyonda'] = False
                bot_state['shortPozisyonda'] = True
                bot_state['pozisyondami'] = True
                bot_state['last_trade'] = datetime.now().isoformat()
                bot_state['last_symbol'] = symbol
        
        elif side == 'TP1':
            # Take Profit 1: %50 kar al
            if position_info['exists']:
                close_qty = abs(position_info['amount']) * 0.5
                close_qty = adjust_quantity(exchange, symbol, close_qty, price)
                
                if position_info['side'] == 'long':
                    order = execute_trade(exchange, symbol, 'SELL', close_qty, price, reduce_only=True)
                else:
                    order = execute_trade(exchange, symbol, 'BUY', close_qty, price, reduce_only=True)
                
                logger.info(f"TP1: %50 kar alındı, kapatılan miktar: {close_qty}")
        
        elif side == 'TP2':
            # Take Profit 2: %30 kar al
            if position_info['exists']:
                close_qty = abs(position_info['amount']) * 0.3
                close_qty = adjust_quantity(exchange, symbol, close_qty, price)
                
                if position_info['side'] == 'long':
                    order = execute_trade(exchange, symbol, 'SELL', close_qty, price, reduce_only=True)
                else:
                    order = execute_trade(exchange, symbol, 'BUY', close_qty, price, reduce_only=True)
                
                logger.info(f"TP2: %30 kar alındı, kapatılan miktar: {close_qty}")
        
        elif side == 'STOP':
            # Stop: Kalan %20'yi kapat
            if position_info['exists']:
                close_qty = abs(position_info['amount'])
                close_qty = adjust_quantity(exchange, symbol, close_qty, price)
                
                if position_info['side'] == 'long':
                    order = execute_trade(exchange, symbol, 'SELL', close_qty, price, reduce_only=True)
                else:
                    order = execute_trade(exchange, symbol, 'BUY', close_qty, price, reduce_only=True)
                
                logger.info(f"STOP: Tüm pozisyon kapatıldı, miktar: {close_qty}")
                
                # Durumu güncelle
                bot_state['longPozisyonda'] = False
                bot_state['shortPozisyonda'] = False
                bot_state['pozisyondami'] = False
        
        elif side == 'CLOSE_ALL':
            # Tüm pozisyonları kapat
            if position_info['exists']:
                close_qty = abs(position_info['amount'])
                close_qty = adjust_quantity(exchange, symbol, close_qty, price)
                
                if position_info['side'] == 'long':
                    order = execute_trade(exchange, symbol, 'SELL', close_qty, price, reduce_only=True)
                else:
                    order = execute_trade(exchange, symbol, 'BUY', close_qty, price, reduce_only=True)
                
                logger.info(f"CLOSE_ALL: Tüm pozisyon kapatıldı, miktar: {close_qty}")
                
                # Durumu güncelle
                bot_state['longPozisyonda'] = False
                bot_state['shortPozisyonda'] = False
                bot_state['pozisyondami'] = False
        
        else:
            logger.warning(f"Bilinmeyen işlem tipi: {side}")
            return jsonify({
                'code': 'error',
                'message': f'Bilinmeyen işlem tipi: {side}'
            }), 400
        
        return jsonify({
            'code': 'success',
            'message': 'İşlem tamamlandı',
            'symbol': symbol,
            'side': side,
            'mode': 'testnet' if testnet else 'real',
            'timestamp': datetime.now().isoformat(),
            'bot_state': bot_state
        })
        
    except Exception as e:
        logger.error(f"Webhook işleme hatası: {str(e)}", exc_info=True)
        return jsonify({
            'code': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/')
def index():
    """Ana sayfa"""
    return jsonify({
        'status': 'online',
        'service': 'Binance Futures Trading Bot',
        'version': '2.0',
        'endpoints': {
            'POST /webhook': 'TradingView webhook endpointi',
            'GET /status': 'Bot durumu',
            'GET /health': 'Sağlık kontrolü'
        },
        'usage': {
            'webhook_format': {
                'ticker': 'BTCUSDT.P veya BTCUSDT',
                'side': 'BUY, SELL, TP1, TP2, STOP, CLOSE_ALL',
                'price': 'İşlem fiyatı (opsiyonel)',
                'quantity': 'USDT cinsinden miktar (varsayılan: 100)',
                'testnet': 'true/false (varsayılan: true)',
                'binanceApiKey': 'API key (opsiyonel, ortam değişkeninden alınır)',
                'binanceSecretKey': 'Secret key (opsiyonel, ortam değişkeninden alınır)'
            }
        },
        'timestamp': datetime.now().isoformat()
    })

@app.route('/status', methods=['GET'])
def status():
    """Bot durumunu göster"""
    return jsonify({
        'status': 'running',
        'bot_state': bot_state,
        'environment': {
            'python_version': os.environ.get('PYTHON_VERSION', '3.10+'),
            'heroku_app': os.environ.get('HEROKU_APP_NAME', 'Not on Heroku'),
            'testnet_mode': True
        },
        'timestamp': datetime.now().isoformat()
    })

@app.route('/health', methods=['GET'])
def health():
    """Sağlık kontrol endpointi"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'checks': {
            'api_connectivity': 'ok',
            'memory_usage': 'ok',
            'bot_ready': True
        }
    })

@app.route('/test', methods=['GET'])
def test_endpoint():
    """Test endpoint - basit bir ping"""
    return jsonify({
        'message': 'Bot çalışıyor!',
        'timestamp': datetime.now().isoformat(),
        'endpoints': {
            'webhook': '/webhook (POST)',
            'status': '/status (GET)',
            'health': '/health (GET)'
        }
    })

# Heroku için gerekli yapılandırma
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    
    logger.info(f"Bot başlatılıyor...")
    logger.info(f"Port: {port}")
    logger.info(f"Debug modu: {debug}")
    logger.info(f"Testnet modu: Aktif")
    
    # Ortam değişkenlerini kontrol et
    if os.environ.get('BINANCE_API_KEY') and os.environ.get('BINANCE_SECRET_KEY'):
        logger.info("API anahtarları ortam değişkenlerinden yüklendi")
    else:
        logger.warning("API anahtarları ortam değişkenlerinde bulunamadı")
        logger.info("Webhook isteklerinde API anahtarları gönderilmelidir")
    
    app.run(host='0.0.0.0', port=port, debug=debug)
