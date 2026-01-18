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

# Telegram Bot Konfigürasyonu
TELEGRAM_BOT_TOKEN = os.environ.get('8143581645:AAF5figZLC0p7oC6AzjBTGzTfWtOFCdHzRo', '')
TELEGRAM_CHAT_ID = os.environ.get('@sosyopump', '')
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

class TelegramNotifier:
    """Telegram bildirim sınıfı"""
    
    @staticmethod
    def send_message(message, parse_mode='HTML'):
        """Telegram'a mesaj gönder"""
        if not TELEGRAM_ENABLED:
            logger.warning("Telegram bot token veya chat ID ayarlanmamış")
            return False
        
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                'chat_id': TELEGRAM_CHAT_ID,
                'text': message,
                'parse_mode': parse_mode,
                'disable_web_page_preview': True
            }
            
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("Telegram mesajı gönderildi")
                return True
            else:
                logger.error(f"Telegram mesaj gönderme hatası: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Telegram mesaj gönderme hatası: {e}")
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
    
    @staticmethod
    def send_position_update(position_info, action="update"):
        """Pozisyon güncelleme bildirimi"""
        try:
            if not position_info.get('exists', False):
                return False
            
            symbol = position_info.get('symbol', 'N/A')
            side = position_info.get('side', 'N/A')
            amount = position_info.get('amount', 0)
            entry_price = position_info.get('entry_price', 0)
            
            side_emoji = "📈" if side == 'long' else "📉"
            action_emoji = "🔄" if action == "update" else "✅" if action == "closed" else "🚀"
            
            message = f"""
{action_emoji} <b>POSITION {action.upper()}</b> {action_emoji}

<b>Symbol:</b> {symbol}
<b>Side:</b> {side_emoji} {side.upper()}
<b>Amount:</b> {amount:.8f}
<b>Entry Price:</b> ${entry_price:,.8f}

⏰ <i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</i>
"""
            
            thread = threading.Thread(
                target=TelegramNotifier.send_message,
                args=(message,)
            )
            thread.daemon = True
            thread.start()
            
            return True
            
        except Exception as e:
            logger.error(f"Telegram pozisyon bildirimi hatası: {e}")
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
        
        parsed['api_key'] = data.get('binanceApiKey') or data.get('binance_api_key') or data.get('api_key', '')
        parsed['secret_key'] = data.get('binanceSecretKey') or data.get('binance_secret_key') or data.get('secret_key', '')
        
        testnet = data.get('testnet', True)
        if isinstance(testnet, str):
            testnet = testnet.lower() in ['true', '1', 'yes']
        parsed['testnet'] = testnet
        
        logger.info(f"Parsed TradingView data: {parsed}")
        return parsed
        
    except Exception as e:
        logger.error(f"Parse error: {e}")
        TelegramNotifier.send_error_notification(f"Parse error: {e}", data)
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
        exchange.fetch_time()
        
        logger.info(f"Binance client initialized successfully. Testnet: {testnet}")
        
        # Telegram bildirimi
        if TELEGRAM_ENABLED:
            mode = "TESTNET" if testnet else "REAL ACCOUNT"
            TelegramNotifier.send_message(f"🤖 <b>Binance Bot Started</b>\n\nMode: {mode}\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        return exchange, None
        
    except ccxt.AuthenticationError as e:
        error_msg = f"API Authentication failed: {str(e)}"
        logger.error(error_msg)
        TelegramNotifier.send_error_notification(error_msg)
        return None, error_msg
    except Exception as e:
        error_msg = f"Failed to initialize Binance client: {str(e)}"
        logger.error(error_msg)
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
        TelegramNotifier.send_error_notification(f"Position info error: {e}")
        return {'exists': False, 'amount': 0, 'side': None, 'symbol': symbol}

def execute_order(exchange, symbol, side, quantity, reduce_only=False, order_type='market'):
    """Emir gönder"""
    try:
        params = {}
        if reduce_only:
            params['reduceOnly'] = True
        
        if order_type == 'market':
            if side.upper() in ['BUY', 'LONG']:
                order = exchange.create_market_buy_order(symbol, quantity, params)
                action = "BUY"
            elif side.upper() in ['SELL', 'SHORT']:
                order = exchange.create_market_sell_order(symbol, quantity, params)
                action = "SELL"
            else:
                return None
        else:
            # Limit order için
            # Not: Bu örnekte sadece market order kullanıyoruz
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
<b>Type:</b> {order_type.upper()}
<b>Order ID:</b> {order.get('id', 'N/A')}

⏰ <i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</i>
"""
            TelegramNotifier.send_message(message)
        
        return order
        
    except ccxt.InsufficientFunds as e:
        error_msg = f"Insufficient funds: {str(e)}"
        logger.error(error_msg)
        TelegramNotifier.send_error_notification(error_msg)
        return None
    except ccxt.NetworkError as e:
        error_msg = f"Network error: {str(e)}"
        logger.error(error_msg)
        TelegramNotifier.send_error_notification(error_msg)
        return None
    except Exception as e:
        error_msg = f"Order execution error: {str(e)}"
        logger.error(error_msg)
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
        
        logger.info(f"Raw TradingView Data: {json.dumps(data, indent=2)}")
        
        # Telegram bildirimi - Sinyal alındı
        if TELEGRAM_ENABLED:
            ticker = data.get('ticker', 'N/A')
            side = data.get('side', 'N/A')
            TelegramNotifier.send_message(f"📨 <b>Signal Received</b>\n\nTicker: {ticker}\nAction: {side}")
        
        # 2. Parse et
        tv_data = parse_tradingview_data(data)
        
        # 3. API key kontrolü
        if not tv_data['api_key'] or not tv_data['secret_key']:
            error_msg = "API keys missing in request"
            logger.error(error_msg)
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
            TelegramNotifier.send_error_notification(error_msg, tv_data)
            
            # Popüler sembolleri listele
            popular = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'SOLUSDT']
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
                    TelegramNotifier.send_position_update(position_info, "closed")
                else:
                    result = {'status': 'error', 'message': 'Failed to close short position'}
            
            # Yeni long aç
            order_result = execute_order(exchange, symbol, 'BUY', quantity)
            if order_result:
                result = {'status': 'success', 'message': 'Opened long position'}
                # Yeni pozisyon bilgisi al
                new_position = get_position_info(exchange, symbol)
                if new_position['exists']:
                    TelegramNotifier.send_position_update(new_position, "opened")
            else:
                result = {'status': 'error', 'message': 'Failed to open long position'}
            
        elif action == 'SELL':
            # Long varsa kapat
            if position_info['exists'] and position_info['side'] == 'long':
                close_qty = position_info['amount']
                close_result = execute_order(exchange, symbol, 'SELL', close_qty, reduce_only=True)
                if close_result:
                    result = {'status': 'success', 'message': 'Closed long position and opened new short'}
                    TelegramNotifier.send_position_update(position_info, "closed")
                else:
                    result = {'status': 'error', 'message': 'Failed to close long position'}
            
            # Yeni short aç
            order_result = execute_order(exchange, symbol, 'SELL', quantity)
            if order_result:
                result = {'status': 'success', 'message': 'Opened short position'}
                new_position = get_position_info(exchange, symbol)
                if new_position['exists']:
                    TelegramNotifier.send_position_update(new_position, "opened")
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
                # Pozisyon güncelle
                updated_position = get_position_info(exchange, symbol)
                if updated_position['exists']:
                    TelegramNotifier.send_position_update(updated_position, "updated")
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
                updated_position = get_position_info(exchange, symbol)
                if updated_position['exists']:
                    TelegramNotifier.send_position_update(updated_position, "updated")
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
                TelegramNotifier.send_position_update(position_info, "closed")
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
                TelegramNotifier.send_position_update(position_info, "closed")
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
        
        logger.info(f"Webhook processed successfully: {response}")
        
        # 12. Telegram'a sonuç bildirimi gönder
        if TELEGRAM_ENABLED:
            TelegramNotifier.send_trade_signal(tv_data, result, position_info)
        
        return jsonify(response), 200
        
    except json.JSONDecodeError as e:
        error_msg = f"JSON decode error: {str(e)}"
        logger.error(error_msg)
        TelegramNotifier.send_error_notification(error_msg)
        return jsonify({
            'status': 'error',
            'message': error_msg,
            'timestamp': datetime.now().isoformat()
        }), 400
        
    except Exception as e:
        error_msg = f"Webhook error: {str(e)}"
        logger.error(f"{error_msg}\n{traceback.format_exc()}")
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
        'service': 'Binance Futures Trading Bot with Telegram Notifications',
        'version': '2.0',
        'telegram_enabled': TELEGRAM_ENABLED,
        'endpoints': {
            'POST /webhook': 'TradingView webhook endpoint',
            'GET /telegram-test': 'Test Telegram notifications',
            'GET /status': 'Bot status',
            'GET /health': 'Health check'
        },
        'timestamp': datetime.now().isoformat()
    })

@app.route('/telegram-test', methods=['GET'])
def telegram_test():
    """Telegram test endpoint"""
    if not TELEGRAM_ENABLED:
        return jsonify({
            'status': 'error',
            'message': 'Telegram not configured',
            'instructions': 'Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables'
        }), 400
    
    test_message = f"""
🔔 <b>Telegram Bot Test</b> 🔔

This is a test message from your Binance Trading Bot.

✅ <b>Bot Status:</b> Online
⏰ <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

If you receive this message, Telegram notifications are working correctly!
"""
    
    success = TelegramNotifier.send_message(test_message)
    
    if success:
        return jsonify({
            'status': 'success',
            'message': 'Telegram test message sent',
            'timestamp': datetime.now().isoformat()
        })
    else:
        return jsonify({
            'status': 'error',
            'message': 'Failed to send Telegram message',
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/status', methods=['GET'])
def status():
    """Bot durumu"""
    return jsonify({
        'status': 'running',
        'telegram_enabled': TELEGRAM_ENABLED,
        'telegram_configured': bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        'timestamp': datetime.now().isoformat()
    })

@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'telegram': 'enabled' if TELEGRAM_ENABLED else 'disabled'
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    
    logger.info("=" * 60)
    logger.info("BINANCE FUTURES BOT WITH TELEGRAM NOTIFICATIONS")
    logger.info(f"Port: {port}")
    logger.info(f"Telegram Enabled: {TELEGRAM_ENABLED}")
    logger.info("=" * 60)
    
    if TELEGRAM_ENABLED:
        logger.info("✅ Telegram notifications are ENABLED")
        # Başlangıç mesajı gönder
        try:
            TelegramNotifier.send_message(
                f"🤖 <b>Binance Trading Bot Started</b>\n\n"
                f"✅ Bot is now online and ready to receive TradingView signals.\n"
                f"⏰ Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                f"🌐 URL: https://{os.environ.get('HEROKU_APP_NAME', 'localhost')}.herokuapp.com"
            )
        except Exception as e:
            logger.error(f"Failed to send startup Telegram message: {e}")
    else:
        logger.warning("⚠️ Telegram notifications are DISABLED")
        logger.info("To enable Telegram, set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables")
    
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)
