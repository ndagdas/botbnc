import os
import json
import logging
from flask import Flask, request, jsonify
import ccxt
from datetime import datetime
import traceback

# Log ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# TradingView formatına uygun parse fonksiyonu
def parse_tradingview_data(data):
    """
    TradingView'den gelen JSON'u standart formata çevir
    TradingView formatı:
    {
        "ticker": "BTCUSDT.P",  # .P futures anlamında
        "price": "50000.5",     # String olarak gelir
        "side": "BUY",          # BUY, SELL, TP1, TP2, STOP
        "quantity": "100",      # USDT cinsinden, string
        "binanceApiKey": "...",
        "binanceSecretKey": "..."
    }
    """
    parsed = {}
    
    try:
        # Ticker/Symbol
        ticker = data.get('ticker', '')
        if '.' in ticker:
            parsed['symbol'] = ticker.split('.')[0]
        else:
            parsed['symbol'] = ticker
        
        # Price - string'den float'a çevir
        price_str = data.get('price', '0')
        try:
            parsed['price'] = float(price_str)
        except:
            parsed['price'] = 0.0
        
        # Side/İşlem - TradingView 'side' gönderiyor
        side = data.get('side', '').upper()
        parsed['side'] = side
        
        # TradingView'den gelen isimlendirmeleri eşle
        # TradingView'deki 'side' -> bizim 'islem' alanımız
        side_mapping = {
            'BUY': 'BUY',
            'SELL': 'SELL', 
            'LONG': 'BUY',
            'SHORT': 'SELL',
            'TP1': 'TP1',
            'TP2': 'TP2',
            'STOP': 'STOP',
            'CLOSE': 'CLOSE_ALL',
            'CLOSE_ALL': 'CLOSE_ALL',
            'EXIT': 'CLOSE_ALL'
        }
        parsed['islem'] = side_mapping.get(side, side)
        
        # Quantity - USDT cinsinden, string'den float'a
        quantity_str = data.get('quantity', '100')  # Varsayılan 100 USDT
        try:
            parsed['quantity_usdt'] = float(quantity_str)
        except:
            parsed['quantity_usdt'] = 100.0
        
        # API Key'ler - TradingView 'binanceApiKey' ve 'binanceSecretKey' gönderiyor
        parsed['api_key'] = data.get('binanceApiKey') or data.get('binance_api_key') or data.get('api_key', '')
        parsed['secret_key'] = data.get('binanceSecretKey') or data.get('binance_secret_key') or data.get('secret_key', '')
        
        # Testnet modu
        testnet = data.get('testnet', True)
        if isinstance(testnet, str):
            testnet = testnet.lower() in ['true', '1', 'yes']
        parsed['testnet'] = testnet
        
        logger.info(f"Parsed TradingView data: {parsed}")
        return parsed
        
    except Exception as e:
        logger.error(f"TradingView data parse error: {e}")
        # Varsayılan değerlerle devam et
        return {
            'symbol': 'BTCUSDT',
            'price': 0.0,
            'side': 'BUY',
            'islem': 'BUY',
            'quantity_usdt': 100.0,
            'api_key': '',
            'secret_key': '',
            'testnet': True
        }

def init_binance_client(api_key, secret_key, testnet=True):
    """Binance Futures client başlat - TradingView uyumlu"""
    try:
        config = {
            'apiKey': api_key.strip(),
            'secret': secret_key.strip(),
            'options': {
                'defaultType': 'future',
                'adjustForTimeDifference': True
            },
            'enableRateLimit': True,
        }
        
        if testnet:
            # TESTNET için
            config['urls'] = {
                'api': {
                    'public': 'https://testnet.binancefuture.com/fapi/v1',
                    'private': 'https://testnet.binancefuture.com/fapi/v1'
                }
            }
            logger.info("Using Binance Testnet Futures")
        
        exchange = ccxt.binance(config)
        
        # Testnet modunu aç
        if testnet:
            exchange.set_sandbox_mode(True)
        
        # Markets yükle
        exchange.load_markets()
        
        # Bağlantı testi
        exchange.fetch_time()
        
        logger.info(f"Binance client initialized successfully. Testnet: {testnet}")
        return exchange, None
        
    except ccxt.AuthenticationError as e:
        error_msg = f"API Authentication failed: {str(e)}"
        logger.error(error_msg)
        return None, error_msg
    except Exception as e:
        error_msg = f"Failed to initialize Binance client: {str(e)}"
        logger.error(error_msg)
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
                return {
                    'exists': True,
                    'amount': abs(position_amt),
                    'side': 'long' if position_amt > 0 else 'short',
                    'entry_price': float(pos.get('entryPrice', 0)),
                    'symbol': symbol
                }
        
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
        elif side.upper() in ['SELL', 'SHORT']:
            order = exchange.create_market_sell_order(symbol, quantity, params)
        else:
            return None
        
        logger.info(f"Order executed: {side} {quantity} {symbol}")
        return order
        
    except Exception as e:
        logger.error(f"Order execution error: {e}")
        return None

@app.route('/webhook', methods=['POST'])
def webhook():
    """TradingView Webhook Endpoint - Tam Uyumlu"""
    try:
        # 1. TradingView'den gelen veriyi al
        if request.is_json:
            data = request.get_json()
        else:
            # Eski format için
            raw_data = request.data.decode('utf-8')
            data = json.loads(raw_data)
        
        logger.info(f"Raw TradingView Data: {json.dumps(data, indent=2)}")
        
        # 2. TradingView formatını parse et
        tv_data = parse_tradingview_data(data)
        
        # 3. Gerekli alanları kontrol et
        if not tv_data['api_key'] or not tv_data['secret_key']:
            logger.error("API keys missing in request")
            return jsonify({
                'status': 'error',
                'message': 'API keys are required',
                'received_data': data,
                'timestamp': datetime.now().isoformat()
            }), 400
        
        # 4. Binance client başlat
        exchange, error = init_binance_client(
            tv_data['api_key'],
            tv_data['secret_key'],
            tv_data['testnet']
        )
        
        if error:
            return jsonify({
                'status': 'error',
                'message': f'Binance connection failed: {error}',
                'hint': 'Check if you are using TESTNET API keys for testnet=true',
                'timestamp': datetime.now().isoformat()
            }), 400
        
        # 5. Sembolü hazırla
        symbol = tv_data['symbol']
        if not symbol.endswith('USDT'):
            symbol = f"{symbol}USDT"
        
        # 6. Mevcut pozisyonu kontrol et
        position = get_position_info(exchange, symbol)
        logger.info(f"Current position: {position}")
        
        # 7. Fiyat bilgisi
        price = tv_data['price']
        if price <= 0:
            # Fiyat yoksa market fiyatını al
            ticker = exchange.fetch_ticker(symbol)
            price = ticker['last']
        
        # 8. Miktarı hesapla (USDT -> Coin miktarı)
        quantity_usdt = tv_data['quantity_usdt']
        quantity = quantity_usdt / price if price > 0 else quantity_usdt / 100
        
        # Lot boyutu ayarla
        market = exchange.market(symbol)
        if market:
            min_qty = market.get('limits', {}).get('amount', {}).get('min', 0.001)
            quantity = max(quantity, min_qty)
            # Yuvarla
            quantity = round(quantity, 8)
        
        # 9. İşlemi yap
        islem = tv_data['islem']
        result = None
        
        if islem == 'BUY':
            # Short varsa kapat
            if position['exists'] and position['side'] == 'short':
                close_qty = position['amount']
                execute_order(exchange, symbol, 'BUY', close_qty, reduce_only=True)
                logger.info(f"Closed short position: {close_qty}")
            
            # Yeni long aç
            result = execute_order(exchange, symbol, 'BUY', quantity)
            
        elif islem == 'SELL':
            # Long varsa kapat
            if position['exists'] and position['side'] == 'long':
                close_qty = position['amount']
                execute_order(exchange, symbol, 'SELL', close_qty, reduce_only=True)
                logger.info(f"Closed long position: {close_qty}")
            
            # Yeni short aç
            result = execute_order(exchange, symbol, 'SELL', quantity)
            
        elif islem == 'TP1' and position['exists']:
            # %50 kar al
            close_qty = position['amount'] * 0.5
            if position['side'] == 'long':
                result = execute_order(exchange, symbol, 'SELL', close_qty, reduce_only=True)
            else:
                result = execute_order(exchange, symbol, 'BUY', close_qty, reduce_only=True)
                
        elif islem == 'TP2' and position['exists']:
            # %30 kar al
            close_qty = position['amount'] * 0.3
            if position['side'] == 'long':
                result = execute_order(exchange, symbol, 'SELL', close_qty, reduce_only=True)
            else:
                result = execute_order(exchange, symbol, 'BUY', close_qty, reduce_only=True)
                
        elif islem == 'STOP' and position['exists']:
            # Tüm pozisyonu kapat
            close_qty = position['amount']
            if position['side'] == 'long':
                result = execute_order(exchange, symbol, 'SELL', close_qty, reduce_only=True)
            else:
                result = execute_order(exchange, symbol, 'BUY', close_qty, reduce_only=True)
                
        elif islem == 'CLOSE_ALL' and position['exists']:
            # Tüm pozisyonu kapat
            close_qty = position['amount']
            if position['side'] == 'long':
                result = execute_order(exchange, symbol, 'SELL', close_qty, reduce_only=True)
            else:
                result = execute_order(exchange, symbol, 'BUY', close_qty, reduce_only=True)
        
        # 10. Yanıtı hazırla
        response = {
            'status': 'success',
            'message': f'TradingView signal processed: {islem} {symbol}',
            'signal': {
                'original': data,
                'parsed': tv_data
            },
            'position_before': position,
            'order_result': 'executed' if result else 'no_action',
            'mode': 'testnet' if tv_data['testnet'] else 'real',
            'timestamp': datetime.now().isoformat()
        }
        
        logger.info(f"Webhook processed successfully: {response}")
        return jsonify(response), 200
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        return jsonify({
            'status': 'error',
            'message': f'Invalid JSON format: {str(e)}',
            'timestamp': datetime.now().isoformat()
        }), 400
        
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}\n{traceback.format_exc()}")
        return jsonify({
            'status': 'error',
            'message': f'Internal server error: {str(e)}',
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/test-webhook', methods=['GET', 'POST'])
def test_webhook():
    """Test endpoint - TradingView formatında test yap"""
    if request.method == 'GET':
        return jsonify({
            'message': 'Send a POST request with TradingView format',
            'example': {
                'ticker': 'BTCUSDT.P',
                'price': '50000.5',
                'side': 'BUY',
                'quantity': '100',
                'binanceApiKey': 'your_testnet_api_key',
                'binanceSecretKey': 'your_testnet_secret_key',
                'testnet': True
            }
        })
    
    # POST ise webhook'u test et
    return webhook()

@app.route('/')
def index():
    return jsonify({
        'service': 'TradingView to Binance Futures Webhook',
        'version': '1.0',
        'endpoints': {
            'POST /webhook': 'Main TradingView webhook endpoint',
            'GET /test-webhook': 'Test the webhook with sample data',
            'GET /health': 'Health check'
        },
        'supported_format': {
            'ticker': 'Symbol with .P for futures (e.g., BTCUSDT.P)',
            'price': 'Price as string or number',
            'side': 'BUY, SELL, TP1, TP2, STOP, CLOSE_ALL',
            'quantity': 'Amount in USDT as string or number',
            'binanceApiKey': 'Binance API Key (testnet for demo)',
            'binanceSecretKey': 'Binance Secret Key',
            'testnet': 'true/false (default: true)'
        },
        'timestamp': datetime.now().isoformat()
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    
    logger.info("=" * 60)
    logger.info("TRADINGVIEW TO BINANCE FUTURES WEBHOOK")
    logger.info(f"Port: {port}")
    logger.info("Ready to receive TradingView alerts!")
    logger.info("=" * 60)
    
    app.run(host='0.0.0.0', port=port, debug=debug)
