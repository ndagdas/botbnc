import os
import json
import logging
import requests
from flask import Flask, request, jsonify
import ccxt
from datetime import datetime
import traceback
import threading

# Log ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
# TELEGRAM AYARLARI - BURAYI KENDİ BİLGİLERİNİZLE DOLDURUN!
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════

# Telegram Bot Token - @BotFather'dan alın
TELEGRAM_BOT_TOKEN = "8143581645:AAF5figZLC0p7oC6AzjBTGzTfWtOFCdHzRo"  # <-- BURAYA KENDİ BOT TOKEN'INI YAZ

# Telegram Chat ID - Botunuza mesaj gönderip https://api.telegram.org/bot<TOKEN>/getUpdates adresinden alın
TELEGRAM_CHAT_ID = "@sosyopump"  # <-- BURAYA KENDİ CHAT ID'NI YAZ

# Telegram bildirimlerini aktif et (True/False)
TELEGRAM_ENABLED = True  # Telegram bildirimlerini kapatmak için False yapın

# Binance Testnet API Key'leri (Opsiyonel - Webhook'tan da gelebilir)
DEFAULT_BINANCE_API_KEY = ""  # <-- Varsayılan API Key (boş bırakabilirsiniz)
DEFAULT_BINANCE_SECRET_KEY = ""  # <-- Varsayılan Secret Key (boş bırakabilirsiniz)

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
# AYARLAR SONU - AŞAĞIDAKİ KODU DEĞİŞTİRMEYİN!
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════

# Telegram kontrol
if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "7223290234:AAFTO2sB6bWk4y59jBpJTUwJ49K09d3Qk5s":
    logger.warning("⚠️  Telegram bot token ayarlanmamış! Lütfen yukarıdaki TELEGRAM_BOT_TOKEN değerini güncelleyin.")
    TELEGRAM_ENABLED = False

if not TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID == "6848467128":
    logger.warning("⚠️  Telegram chat ID ayarlanmamış! Lütfen yukarıdaki TELEGRAM_CHAT_ID değerini güncelleyin.")
    TELEGRAM_ENABLED = False

class TelegramNotifier:
    """Telegram bildirim sınıfı"""
    
    @staticmethod
    def send_message(message, parse_mode='HTML'):
        """Telegram'a mesaj gönder"""
        if not TELEGRAM_ENABLED:
            logger.warning("Telegram bildirimleri kapalı")
            return False
        
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                'chat_id': TELEGRAM_CHAT_ID,
                'text': message,
                'parse_mode': parse_mode,
                'disable_web_page_preview': True
            }
            
            logger.info(f"Telegram mesajı gönderiliyor: {message[:100]}...")
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                logger.info("✅ Telegram mesajı başarıyla gönderildi")
                return True
            else:
                logger.error(f"❌ Telegram mesaj gönderme hatası: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Telegram mesaj gönderme hatası: {e}")
            return False
    
    @staticmethod
    def send_trade_signal(data, result, position_info=None):
        """Trading sinyali bildirimi gönder"""
        try:
            symbol = data.get('symbol', 'N/A')
            side = data.get('side', 'N/A')
            price = data.get('price', 0)
            quantity = data.get('quantity_usdt', 0)
            testnet = data.get('testnet', True)
            
            # Emoji belirle
            emoji = "📊"
            if side == 'BUY':
                emoji = "🟢"
            elif side == 'SELL':
                emoji = "🔴"
            elif side in ['TP1', 'TP2']:
                emoji = "💰"
            elif side == 'STOP':
                emoji = "🛑"
            elif side == 'CLOSE_ALL':
                emoji = "🔒"
            
            mode = "🚀 <b>TESTNET</b>" if testnet else "💰 <b>REAL ACCOUNT</b>"
            
            # Mesaj oluştur
            message = f"""
{emoji} <b>TRADING SIGNAL</b> {emoji}

<b>Symbol:</b> {symbol}
<b>Action:</b> {side}
<b>Price:</b> ${price:,.8f}
<b>Quantity:</b> {quantity:,.2f} USDT
<b>Mode:</b> {mode}

<b>Result:</b> {result.get('status', 'N/A')}
<b>Message:</b> {result.get('message', 'N/A')}
"""
            
            if position_info:
                message += f"\n<b>Position:</b> {position_info.get('side', 'None')} - {position_info.get('amount', 0):.8f}"
            
            message += f"\n\n⏰ <i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</i>"
            
            # Thread'de gönder (async)
            thread = threading.Thread(
                target=TelegramNotifier.send_message,
                args=(message,)
            )
            thread.daemon = True
            thread.start()
            
            return True
            
        except Exception as e:
            logger.error(f"Telegram sinyal bildirimi hatası: {e}")
            return False
    
    @staticmethod
    def send_error_notification(error_message, data=None):
        """Hata bildirimi gönder"""
        try:
            message = f"""
🚨 <b>TRADING BOT ERROR</b> 🚨

<b>Error:</b> {error_message[:200]}

<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}
"""
            
            if data:
                message += f"\n<b>Symbol:</b> {data.get('symbol', 'N/A')}"
                message += f"\n<b>Action:</b> {data.get('side', 'N/A')}"
            
            thread = threading.Thread(
                target=TelegramNotifier.send_message,
                args=(message,)
            )
            thread.daemon = True
            thread.start()
            
            return True
            
        except Exception as e:
            logger.error(f"Telegram hata bildirimi hatası: {e}")
            return False

def parse_tradingview_data(data):
    """TradingView verisini parse et"""
    parsed = {}
    
    try:
        ticker = data.get('ticker', '')
        if '.' in ticker:
            parsed['symbol'] = ticker.split('.')[0]
        else:
            parsed['symbol'] = ticker
        
        price_str = data.get('price', '0')
        try:
            parsed['price'] = float(price_str)
        except:
            parsed['price'] = 0.0
        
        side = data.get('side', '').upper()
        parsed['side'] = side
        
        side_mapping = {
            'BUY': 'BUY', 'SELL': 'SELL', 'LONG': 'BUY', 'SHORT': 'SELL',
            'TP1': 'TP1', 'TP2': 'TP2', 'STOP': 'STOP', 
            'CLOSE': 'CLOSE_ALL', 'CLOSE_ALL': 'CLOSE_ALL', 'EXIT': 'CLOSE_ALL'
        }
        parsed['action'] = side_mapping.get(side, side)
        
        quantity_str = data.get('quantity', '100')
        try:
            parsed['quantity_usdt'] = float(quantity_str)
        except:
            parsed['quantity_usdt'] = 100.0
        
        # API key'leri al (önce istekten, sonra varsayılan, sonra ortam değişkeni)
        parsed['api_key'] = (
            data.get('binanceApiKey') or 
            data.get('binance_api_key') or 
            data.get('api_key') or
            DEFAULT_BINANCE_API_KEY or
            os.environ.get('BINANCE_API_KEY', '')
        )
        
        parsed['secret_key'] = (
            data.get('binanceSecretKey') or 
            data.get('binance_secret_key') or 
            data.get('secret_key') or
            DEFAULT_BINANCE_SECRET_KEY or
            os.environ.get('BINANCE_SECRET_KEY', '')
        )
        
        testnet = data.get('testnet', True)
        if isinstance(testnet, str):
            testnet = testnet.lower() in ['true', '1', 'yes']
        parsed['testnet'] = testnet
        
        logger.info(f"Parsed TradingView data: {parsed}")
        return parsed
        
    except Exception as e:
        logger.error(f"Parse error: {e}")
        return {
            'symbol': 'BTCUSDT',
            'price': 0.0,
            'side': 'BUY',
            'action': 'BUY',
            'quantity_usdt': 100.0,
            'api_key': '',
            'secret_key': '',
            'testnet': True
        }

def init_binance_client(api_key, secret_key, testnet=True):
    """Binance client başlat"""
    try:
        config = {
            'apiKey': api_key.strip(),
            'secret': secret_key.strip(),
            'options': {
                'defaultType': 'future',
                'adjustForTimeDifference': True
            },
            'enableRateLimit': True,
            'timeout': 30000,
        }
        
        if testnet:
            config['urls'] = {
                'api': {
                    'public': 'https://testnet.binancefuture.com/fapi/v1',
                    'private': 'https://testnet.binancefuture.com/fapi/v1'
                }
            }
            logger.info("Using Binance Testnet Futures")
        
        exchange = ccxt.binance(config)
        
        if testnet:
            exchange.set_sandbox_mode(True)
        
        exchange.load_markets()
        
        logger.info(f"Binance client initialized successfully. Testnet: {testnet}")
        
        # Telegram bildirimi (sadece ilk başlatmada)
        if TELEGRAM_ENABLED:
            mode = "TESTNET" if testnet else "REAL ACCOUNT"
            welcome_msg = f"""
🤖 <b>Binance Trading Bot Started</b> 🤖

✅ <b>Status:</b> Online
🌐 <b>Mode:</b> {mode}
⏰ <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

<i>Ready to receive TradingView signals!</i>
"""
            TelegramNotifier.send_message(welcome_msg)
        
        return exchange, None
        
    except ccxt.AuthenticationError as e:
        error_msg = f"API Authentication failed: {str(e)}"
        logger.error(error_msg)
        if TELEGRAM_ENABLED:
            TelegramNotifier.send_error_notification(error_msg)
        return None, error_msg
    except Exception as e:
        error_msg = f"Failed to initialize Binance client: {str(e)}"
        logger.error(error_msg)
        if TELEGRAM_ENABLED:
            TelegramNotifier.send_error_notification(error_msg)
        return None, error_msg

def get_position_info(exchange, symbol):
    """Mevcut pozisyon bilgilerini al"""
    try:
        if not symbol.endswith('USDT'):
            symbol = f"{symbol}USDT"
        
        positions = exchange.fetch_positions([symbol])
        
        for pos in positions:
            position_amt = float(pos.get('contracts', 0))
            if position_amt != 0:
                position_info = {
                    'exists': True,
                    'amount': abs(position_amt),
                    'side': 'long' if position_amt > 0 else 'short',
                    'entry_price': float(pos.get('entryPrice', 0)),
                    'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                    'symbol': symbol
                }
                
                logger.info(f"Position found: {position_info}")
                return position_info
        
        logger.info(f"No position found for {symbol}")
        return {'exists': False, 'amount': 0, 'side': None, 'symbol': symbol}
        
    except Exception as e:
        logger.error(f"Position info error: {e}")
        return {'exists': False, 'amount': 0, 'side': None, 'symbol': symbol}

def execute_order(exchange, symbol, side, quantity, reduce_only=False):
    """Emir gönder"""
    try:
        params = {}
        if reduce_only:
            params['reduceOnly'] = True
        
        if side.upper() in ['BUY', 'LONG']:
            order = exchange.create_market_buy_order(symbol, quantity, params)
            action = "BUY"
        elif side.upper() in ['SELL', 'SHORT']:
            order = exchange.create_market_sell_order(symbol, quantity, params)
            action = "SELL"
        else:
            return None
        
        logger.info(f"Order executed: {action} {quantity} {symbol}")
        
        # Telegram bildirimi
        if TELEGRAM_ENABLED:
            emoji = "🟢" if action == "BUY" else "🔴"
            message = f"""
{emoji} <b>ORDER EXECUTED</b> {emoji}

<b>Symbol:</b> {symbol}
<b>Action:</b> {action}
<b>Quantity:</b> {quantity:.8f}
<b>Order ID:</b> {order.get('id', 'N/A')}
<b>Status:</b> {order.get('status', 'N/A')}

⏰ <i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</i>
"""
            TelegramNotifier.send_message(message)
        
        return order
        
    except Exception as e:
        error_msg = f"Order execution error: {str(e)}"
        logger.error(error_msg)
        if TELEGRAM_ENABLED:
            TelegramNotifier.send_error_notification(error_msg)
        return None

def calculate_quantity(exchange, symbol, usdt_amount, price):
    """USDT miktarından coin miktarını hesapla"""
    try:
        if price <= 0:
            ticker = exchange.fetch_ticker(symbol)
            price = ticker['last']
        
        quantity = usdt_amount / price
        
        # Market lot boyutuna göre ayarla
        market = exchange.market(symbol)
        if market:
            # Minimum quantity
            min_qty = market.get('limits', {}).get('amount', {}).get('min', 0.001)
            if quantity < min_qty:
                quantity = min_qty
            
            # Step size
            step_size = market.get('precision', {}).get('amount', 0.001)
            if step_size > 0:
                quantity = round(quantity - (quantity % step_size), 8)
            
            # Maximum quantity (demo için)
            if 'testnet' in str(exchange.urls.get('api', {}).get('public', '')):
                max_qty = 0.1  # Testnet için maksimum
                if quantity > max_qty:
                    quantity = max_qty
        
        return round(quantity, 8), price
        
    except Exception as e:
        logger.error(f"Quantity calculation error: {e}")
        return usdt_amount / 100, price  # Fallback

@app.route('/webhook', methods=['POST'])
def webhook():
    """TradingView Webhook Endpoint"""
    start_time = datetime.now()
    
    try:
        # 1. Veriyi al
        if request.is_json:
            data = request.get_json()
        else:
            raw_data = request.data.decode('utf-8')
            data = json.loads(raw_data)
        
        logger.info(f"📨 Webhook received: {json.dumps(data, indent=2)}")
        
        # 2. Parse et
        tv_data = parse_tradingview_data(data)
        
        # 3. API key kontrolü
        if not tv_data['api_key'] or not tv_data['secret_key']:
            error_msg = "API keys missing in request"
            logger.error(error_msg)
            if TELEGRAM_ENABLED:
                TelegramNotifier.send_error_notification(error_msg, tv_data)
            return jsonify({
                'status': 'error',
                'message': error_msg,
                'timestamp': datetime.now().isoformat()
            }), 400
        
        # 4. Binance client başlat
        exchange, error = init_binance_client(
            tv_data['api_key'],
            tv_data['secret_key'],
            tv_data['testnet']
        )
        
        if error:
            if TELEGRAM_ENABLED:
                TelegramNotifier.send_error_notification(f"Binance connection failed: {error}", tv_data)
            return jsonify({
                'status': 'error',
                'message': f'Binance connection failed: {error}',
                'timestamp': datetime.now().isoformat()
            }), 400
        
        # 5. Sembolü hazırla
        symbol = tv_data['symbol']
        if not symbol.endswith('USDT'):
            symbol = f"{symbol}USDT"
        
        # 6. Sembol kontrolü
        if symbol not in exchange.markets:
            error_msg = f"Symbol {symbol} not available on Binance"
            logger.error(error_msg)
            if TELEGRAM_ENABLED:
                TelegramNotifier.send_error_notification(error_msg, tv_data)
            
            # Popüler sembolleri listele
            popular = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'SOLUSDT', 'XRPUSDT']
            available = [s for s in popular if s in exchange.markets]
            
            return jsonify({
                'status': 'error',
                'message': error_msg,
                'available_symbols': available,
                'timestamp': datetime.now().isoformat()
            }), 400
        
        # 7. Pozisyon kontrolü
        position_info = get_position_info(exchange, symbol)
        
        # 8. Fiyat ve miktarı hesapla
        usdt_amount = tv_data['quantity_usdt']
        price = tv_data['price']
        quantity, current_price = calculate_quantity(exchange, symbol, usdt_amount, price)
        
        # 9. İşlemi gerçekleştir
        action = tv_data['action']
        result = {'status': 'no_action', 'message': 'No action taken'}
        
        if action == 'BUY':
            # Short varsa kapat
            if position_info['exists'] and position_info['side'] == 'short':
                close_qty = position_info['amount']
                close_result = execute_order(exchange, symbol, 'BUY', close_qty, reduce_only=True)
                if close_result:
                    result = {'status': 'success', 'message': 'Closed short position and opened new long'}
                else:
                    result = {'status': 'error', 'message': 'Failed to close short position'}
            
            # Yeni long aç
            order_result = execute_order(exchange, symbol, 'BUY', quantity)
            if order_result:
                result = {'status': 'success', 'message': 'Opened long position'}
            else:
                result = {'status': 'error', 'message': 'Failed to open long position'}
            
        elif action == 'SELL':
            # Long varsa kapat
            if position_info['exists'] and position_info['side'] == 'long':
                close_qty = position_info['amount']
                close_result = execute_order(exchange, symbol, 'SELL', close_qty, reduce_only=True)
                if close_result:
                    result = {'status': 'success', 'message': 'Closed long position and opened new short'}
                else:
                    result = {'status': 'error', 'message': 'Failed to close long position'}
            
            # Yeni short aç
            order_result = execute_order(exchange, symbol, 'SELL', quantity)
            if order_result:
                result = {'status': 'success', 'message': 'Opened short position'}
            else:
                result = {'status': 'error', 'message': 'Failed to open short position'}
            
        elif action == 'TP1' and position_info['exists']:
            # %50 kar al
            close_qty = position_info['amount'] * 0.5
            if position_info['side'] == 'long':
                order_result = execute_order(exchange, symbol, 'SELL', close_qty, reduce_only=True)
            else:
                order_result = execute_order(exchange, symbol, 'BUY', close_qty, reduce_only=True)
            
            if order_result:
                result = {'status': 'success', 'message': 'Take Profit 1 executed (50%)'}
            else:
                result = {'status': 'error', 'message': 'Failed to execute TP1'}
                
        elif action == 'TP2' and position_info['exists']:
            # %30 kar al
            close_qty = position_info['amount'] * 0.3
            if position_info['side'] == 'long':
                order_result = execute_order(exchange, symbol, 'SELL', close_qty, reduce_only=True)
            else:
                order_result = execute_order(exchange, symbol, 'BUY', close_qty, reduce_only=True)
            
            if order_result:
                result = {'status': 'success', 'message': 'Take Profit 2 executed (30%)'}
            else:
                result = {'status': 'error', 'message': 'Failed to execute TP2'}
                
        elif action == 'STOP' and position_info['exists']:
            # Tüm pozisyonu kapat
            close_qty = position_info['amount']
            if position_info['side'] == 'long':
                order_result = execute_order(exchange, symbol, 'SELL', close_qty, reduce_only=True)
            else:
                order_result = execute_order(exchange, symbol, 'BUY', close_qty, reduce_only=True)
            
            if order_result:
                result = {'status': 'success', 'message': 'Stop loss executed (100%)'}
            else:
                result = {'status': 'error', 'message': 'Failed to execute stop loss'}
                
        elif action == 'CLOSE_ALL' and position_info['exists']:
            # Tüm pozisyonu kapat
            close_qty = position_info['amount']
            if position_info['side'] == 'long':
                order_result = execute_order(exchange, symbol, 'SELL', close_qty, reduce_only=True)
            else:
                order_result = execute_order(exchange, symbol, 'BUY', close_qty, reduce_only=True)
            
            if order_result:
                result = {'status': 'success', 'message': 'All positions closed'}
            else:
                result = {'status': 'error', 'message': 'Failed to close all positions'}
        else:
            result = {'status': 'no_action', 'message': 'No position to act on'}
        
        # 10. İşlem süresi
        processing_time = (datetime.now() - start_time).total_seconds()
        
        # 11. Yanıt hazırla
        response = {
            'status': 'success',
            'message': f'TradingView signal processed: {action} {symbol}',
            'signal': tv_data,
            'position_before': position_info,
            'order_result': result,
            'processing_time_seconds': round(processing_time, 3),
            'mode': 'testnet' if tv_data['testnet'] else 'real',
            'timestamp': datetime.now().isoformat()
        }
        
        logger.info(f"✅ Webhook processed successfully: {response}")
        
        # 12. Telegram'a sonuç bildirimi gönder
        if TELEGRAM_ENABLED:
            TelegramNotifier.send_trade_signal(tv_data, result, position_info)
        
        return jsonify(response), 200
        
    except json.JSONDecodeError as e:
        error_msg = f"JSON decode error: {str(e)}"
        logger.error(error_msg)
        if TELEGRAM_ENABLED:
            TelegramNotifier.send_error_notification(error_msg)
        return jsonify({
            'status': 'error',
            'message': error_msg,
            'timestamp': datetime.now().isoformat()
        }), 400
        
    except Exception as e:
        error_msg = f"Webhook error: {str(e)}"
        logger.error(f"{error_msg}\n{traceback.format_exc()}")
        if TELEGRAM_ENABLED:
            TelegramNotifier.send_error_notification(error_msg)
        return jsonify({
            'status': 'error',
            'message': error_msg,
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/')
def index():
    """Ana sayfa"""
    return jsonify({
        'service': 'Binance Futures Trading Bot with Telegram',
        'version': '2.1',
        'telegram_enabled': TELEGRAM_ENABLED,
        'endpoints': {
            'POST /webhook': 'TradingView webhook endpoint',
            'GET /telegram-test': 'Test Telegram notifications',
            'GET /status': 'Bot status',
            'GET /health': 'Health check'
        },
        'settings': {
            'telegram_bot_token_set': bool(TELEGRAM_BOT_TOKEN),
            'telegram_chat_id_set': bool(TELEGRAM_CHAT_ID),
            'default_api_key_set': bool(DEFAULT_BINANCE_API_KEY),
            'testnet_mode': True
        },
        'timestamp': datetime.now().isoformat()
    })

@app.route('/telegram-test', methods=['GET'])
def telegram_test():
    """Telegram test endpoint"""
    if not TELEGRAM_ENABLED:
        return jsonify({
            'status': 'error',
            'message': 'Telegram not enabled',
            'current_token': TELEGRAM_BOT_TOKEN[:10] + '...' if TELEGRAM_BOT_TOKEN else 'Not set',
            'current_chat_id': TELEGRAM_CHAT_ID if TELEGRAM_CHAT_ID else 'Not set',
            'instructions': 'Please update TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the code'
        }), 400
    
    test_message = f"""
🔔 <b>Telegram Bot Test</b> 🔔

✅ <b>Bot Status:</b> Online
🤖 <b>Bot Name:</b> Binance Trading Bot
⏰ <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

If you receive this message, Telegram notifications are working correctly!

<i>This is an automated test message.</i>
"""
    
    success = TelegramNotifier.send_message(test_message)
    
    if success:
        return jsonify({
            'status': 'success',
            'message': 'Telegram test message sent successfully!',
            'telegram_enabled': TELEGRAM_ENABLED,
            'timestamp': datetime.now().isoformat()
        })
    else:
        return jsonify({
            'status': 'error',
            'message': 'Failed to send Telegram message',
            'telegram_enabled': TELEGRAM_ENABLED,
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/status', methods=['GET'])
def status():
    """Bot durumu"""
    return jsonify({
        'status': 'running',
        'telegram_enabled': TELEGRAM_ENABLED,
        'telegram_token_set': bool(TELEGRAM_BOT_TOKEN),
        'telegram_chat_id_set': bool(TELEGRAM_CHAT_ID),
        'default_api_key_set': bool(DEFAULT_BINANCE_API_KEY),
        'server_time': datetime.now().isoformat(),
        'timestamp': datetime.now().isoformat()
    })

@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'telegram': 'enabled' if TELEGRAM_ENABLED else 'disabled',
        'server_time': datetime.now().isoformat(),
        'timestamp': datetime.now().isoformat()
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    
    print("=" * 70)
    print("🤖 BINANCE FUTURES TRADING BOT WITH TELEGRAM")
    print("=" * 70)
    
    # Telegram durumu
    if TELEGRAM_ENABLED:
        print("✅ Telegram notifications: ENABLED")
        print(f"   Bot Token: {TELEGRAM_BOT_TOKEN[:10]}...{TELEGRAM_BOT_TOKEN[-5:]}")
        print(f"   Chat ID: {TELEGRAM_CHAT_ID}")
    else:
        print("⚠️  Telegram notifications: DISABLED")
        print("   To enable, update TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the code")
    
    # Binance API durumu
    if DEFAULT_BINANCE_API_KEY:
        print(f"✅ Default Binance API Key: {DEFAULT_BINANCE_API_KEY[:10]}...")
    else:
        print("⚠️  Default Binance API Key: Not set")
        print("   You can set it in DEFAULT_BINANCE_API_KEY variable or send via webhook")
    
    print(f"🌐 Webhook URL: http://localhost:{port}/webhook")
    print(f"🔧 Debug Mode: {debug}")
    print("=" * 70)
    print("📨 Ready to receive TradingView alerts!")
    print("=" * 70)
    
    # Başlangıç mesajı gönder
    if TELEGRAM_ENABLED:
        startup_msg = f"""
🚀 <b>Binance Trading Bot Started</b> 🚀

✅ <b>Status:</b> Online and Ready
🌐 <b>Server:</b> Heroku
⏰ <b>Start Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

<b>Configuration:</b>
• Telegram: ✅ Enabled
• Testnet Mode: ✅ Active
• Webhook: ✅ Ready

<i>Waiting for TradingView signals...</i>
"""
        TelegramNotifier.send_message(startup_msg)
    
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)
