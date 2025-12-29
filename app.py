from flask import Flask, request, jsonify
import json
import pandas as pd
import ccxt
import logging
from datetime import datetime

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('testnet_trading.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Testnet configuration
TESTNET_URL = "https://testnet.binancefuture.com"  # Testnet API URL
TESTNET_WS_URL = "wss://stream.binancefuture.com/ws"  # WebSocket URL

class TestnetTradingBot:
    def __init__(self):
        self.longPozisyonda = False
        self.shortPozisyonda = False
        self.pozisyondami = False
        self.current_symbol = ""
        self.exchange = None
    
    def initialize_exchange(self, api_key, secret_key):
        """Initialize testnet exchange connection"""
        try:
            self.exchange = ccxt.binance({
                'apiKey': api_key,
                'secret': secret_key,
                'options': {
                    'adjustForTimeDifference': True,
                    'defaultType': 'future',
                    'testnet': True  # Enable testnet mode
                },
                'enableRateLimit': True,
                'urls': {
                    'api': {
                        'public': 'https://testnet.binancefuture.com/fapi/v1',
                        'private': 'https://testnet.binancefuture.com/fapi/v1',
                    }
                }
            })
            
            # Set sandbox mode
            self.exchange.set_sandbox_mode(True)
            
            # Test connection
            self.exchange.fetch_balance()
            logger.info("Testnet exchange initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize exchange: {e}")
            return False
    
    def check_position(self, symbol):
        """Check current positions"""
        try:
            balance = self.exchange.fetch_balance()
            positions = balance['info'].get('positions', [])
            
            current_positions = [
                p for p in positions
                if float(p['positionAmt']) != 0 and p['symbol'] == symbol
            ]
            
            position_bilgi = pd.DataFrame(current_positions)
            
            if not position_bilgi.empty:
                self.pozisyondami = True
                pos_amt = float(position_bilgi.iloc[-1]['positionAmt'])
                self.longPozisyonda = pos_amt > 0
                self.shortPozisyonda = pos_amt < 0
                logger.info(f"Position found: {symbol}, Amount: {pos_amt}, "
                           f"Long: {self.longPozisyonda}, Short: {self.shortPozisyonda}")
            else:
                self.pozisyondami = False
                self.longPozisyonda = False
                self.shortPozisyonda = False
                logger.info(f"No position found for {symbol}")
            
            return position_bilgi
        
        except Exception as e:
            logger.error(f"Error checking position: {e}")
            return pd.DataFrame()
    
    def calculate_quantity(self, symbol, usd_amount, price):
        """Calculate quantity based on USD amount and price"""
        try:
            # Get symbol info for lot size
            markets = self.exchange.load_markets()
            market = markets[symbol]
            
            # Calculate quantity
            quantity = usd_amount / price
            
            # Apply lot size precision
            lot_size = market['limits']['amount']['min']
            if lot_size:
                # Round down to nearest lot size
                quantity = (quantity // lot_size) * lot_size
            
            # Apply precision
            quantity = self.exchange.amount_to_precision(symbol, quantity)
            
            logger.info(f"Calculated quantity: {quantity} for ${usd_amount} at ${price}")
            return float(quantity)
        
        except Exception as e:
            logger.error(f"Error calculating quantity: {e}")
            return 0
    
    def execute_market_buy(self, symbol, quantity):
        """Execute market buy order"""
        try:
            order = self.exchange.create_market_buy_order(symbol, quantity)
            logger.info(f"Market BUY executed: {order}")
            return order
        except Exception as e:
            logger.error(f"Error executing market buy: {e}")
            return None
    
    def execute_market_sell(self, symbol, quantity):
        """Execute market sell order"""
        try:
            order = self.exchange.create_market_sell_order(symbol, quantity)
            logger.info(f"Market SELL executed: {order}")
            return order
        except Exception as e:
            logger.error(f"Error executing market sell: {e}")
            return None
    
    def close_position(self, symbol, position_info):
        """Close current position"""
        try:
            if not position_info.empty:
                pos_amt = abs(float(position_info.iloc[-1]['positionAmt']))
                
                if self.longPozisyonda:
                    order = self.exchange.create_market_sell_order(
                        symbol, pos_amt, {"reduceOnly": True}
                    )
                    logger.info(f"Closed LONG position: {order}")
                elif self.shortPozisyonda:
                    order = self.exchange.create_market_buy_order(
                        symbol, pos_amt, {"reduceOnly": True}
                    )
                    logger.info(f"Closed SHORT position: {order}")
                return order
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return None
    
    def execute_tp1(self, symbol, position_info):
        """Execute TP1 (50% of position)"""
        try:
            if not position_info.empty:
                pos_amt = abs(float(position_info.iloc[-1]['positionAmt']))
                close_amount = pos_amt * 0.50
                
                if self.longPozisyonda:
                    order = self.exchange.create_market_sell_order(
                        symbol, close_amount, {"reduceOnly": True}
                    )
                elif self.shortPozisyonda:
                    order = self.exchange.create_market_buy_order(
                        symbol, close_amount, {"reduceOnly": True}
                    )
                
                logger.info(f"TP1 executed: {close_amount} {symbol}")
                return order
        except Exception as e:
            logger.error(f"Error executing TP1: {e}")
            return None
    
    def execute_tp2(self, symbol, position_info):
        """Execute TP2 (30% of position)"""
        try:
            if not position_info.empty:
                pos_amt = abs(float(position_info.iloc[-1]['positionAmt']))
                close_amount = pos_amt * 0.30
                
                if self.longPozisyonda:
                    order = self.exchange.create_market_sell_order(
                        symbol, close_amount, {"reduceOnly": True}
                    )
                elif self.shortPozisyonda:
                    order = self.exchange.create_market_buy_order(
                        symbol, close_amount, {"reduceOnly": True}
                    )
                
                logger.info(f"TP2 executed: {close_amount} {symbol}")
                return order
        except Exception as e:
            logger.error(f"Error executing TP2: {e}")
            return None
    
    def execute_stop(self, symbol, position_info):
        """Execute STOP (remaining 20% of position)"""
        try:
            if not position_info.empty:
                pos_amt = abs(float(position_info.iloc[-1]['positionAmt']))
                
                if self.longPozisyonda:
                    order = self.exchange.create_market_sell_order(
                        symbol, pos_amt, {"reduceOnly": True}
                    )
                elif self.shortPozisyonda:
                    order = self.exchange.create_market_buy_order(
                        symbol, pos_amt, {"reduceOnly": True}
                    )
                
                logger.info(f"STOP executed: {pos_amt} {symbol}")
                return order
        except Exception as e:
            logger.error(f"Error executing STOP: {e}")
            return None

# Initialize trading bot
bot = TestnetTradingBot()

@app.route("/webhook", methods=['POST'])
def webhook():
    """Handle TradingView webhook signals"""
    try:
        data = json.loads(request.data)
        logger.info(f"Received webhook data: {data}")
        
        # Extract data
        ticker = data.get('ticker', '')
        price = float(data.get('price', 0))
        islem = data.get('side', '').upper()
        quantity_usd = float(data.get('quantity', 0))  # USD amount
        
        # Parse symbol
        veri = ticker.split(".")
        symbol = veri[0] if veri else ''
        
        if not symbol:
            return jsonify({"error": "No symbol provided"}), 400
        
        # Get API keys from webhook
        binanceapi = data.get('binanceApiKey', '')
        binancesecret = data.get('binanceSecretKey', '')
        
        # Use testnet API keys if provided, otherwise use environment/default
        if not binanceapi or not binancesecret:
            # You can set default testnet keys here or use environment variables
            binanceapi = "cxqCDAcDQ18uEaJxJMrcHbFegbrixSi0trVczalLjToHZUwCBg9QPDr8y77bbgwT"  # Replace with your testnet API key
            binancesecret = "1HePKBX7LzqbKMFJi4NXhvmMCuc41BXxs1E5WkBYKRnCHvLEZUzW99TWN9mzEiyc"  # Replace with your testnet secret
        
        # Initialize exchange with testnet
        if not bot.initialize_exchange(binanceapi, binancesecret):
            return jsonify({"error": "Failed to initialize exchange"}), 500
        
        # Check current position
        position_bilgi = bot.check_position(symbol)
        
        logger.info(f"Action: {islem}, Symbol: {symbol}, Price: ${price}, USD Amount: ${quantity_usd}")
        
        # Process signals
        if islem == "BUY":
            # Close opposite position if exists
            if bot.shortPozisyonda:
                bot.close_position(symbol, position_bilgi)
                # Update position info after closing
                position_bilgi = bot.check_position(symbol)
            
            # Calculate quantity
            quantity = bot.calculate_quantity(symbol, quantity_usd, price)
            if quantity <= 0:
                return jsonify({"error": "Invalid quantity"}), 400
            
            # Open long position
            order = bot.execute_market_buy(symbol, quantity)
            if order:
                logger.info(f"BUY order successful: {order}")
        
        elif islem == "SELL":
            # Close opposite position if exists
            if bot.longPozisyonda:
                bot.close_position(symbol, position_bilgi)
                # Update position info after closing
                position_bilgi = bot.check_position(symbol)
            
            # Calculate quantity
            quantity = bot.calculate_quantity(symbol, quantity_usd, price)
            if quantity <= 0:
                return jsonify({"error": "Invalid quantity"}), 400
            
            # Open short position
            order = bot.execute_market_sell(symbol, quantity)
            if order:
                logger.info(f"SELL order successful: {order}")
        
        elif islem == "TP1":
            # Take profit 1 (50% of position)
            if bot.pozisyondami:
                order = bot.execute_tp1(symbol, position_bilgi)
                if order:
                    logger.info(f"TP1 order successful: {order}")
            else:
                logger.warning("TP1 signal received but no position found")
        
        elif islem == "TP2":
            # Take profit 2 (30% of position)
            if bot.pozisyondami:
                order = bot.execute_tp2(symbol, position_bilgi)
                if order:
                    logger.info(f"TP2 order successful: {order}")
            else:
                logger.warning("TP2 signal received but no position found")
        
        elif islem == "STOP":
            # Stop loss (remaining 20% of position)
            if bot.pozisyondami:
                order = bot.execute_stop(symbol, position_bilgi)
                if order:
                    logger.info(f"STOP order successful: {order}")
            else:
                logger.warning("STOP signal received but no position found")
        
        else:
            logger.warning(f"Unknown action: {islem}")
            return jsonify({"error": "Unknown action"}), 400
        
        # Update position status after trade
        bot.check_position(symbol)
        
        return jsonify({
            "code": "success",
            "action": islem,
            "symbol": symbol,
            "has_position": bot.pozisyondami,
            "is_long": bot.longPozisyonda,
            "is_short": bot.shortPozisyonda
        }), 200
    
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/health", methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "binance-testnet-future-bot"
    }), 200

@app.route("/balance", methods=['GET'])
def get_balance():
    """Get account balance"""
    try:
        if bot.exchange:
            balance = bot.exchange.fetch_balance()
            
            # Filter only non-zero balances
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
            return jsonify({"error": "Exchange not initialized"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/position/<symbol>", methods=['GET'])
def get_position(symbol):
    """Get current position for a symbol"""
    try:
        if bot.exchange:
            position_bilgi = bot.check_position(symbol)
            
            if bot.pozisyondami and not position_bilgi.empty:
                pos_info = position_bilgi.iloc[-1]
                return jsonify({
                    "symbol": symbol,
                    "positionAmt": float(pos_info['positionAmt']),
                    "entryPrice": float(pos_info['entryPrice']),
                    "unrealizedProfit": float(pos_info['unRealizedProfit']),
                    "is_long": bot.longPozisyonda,
                    "is_short": bot.shortPozisyonda
                }), 200
            else:
                return jsonify({
                    "symbol": symbol,
                    "positionAmt": 0,
                    "has_position": False
                }), 200
        else:
            return jsonify({"error": "Exchange not initialized"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    logger.info("Starting Binance Testnet Future Trading Bot...")
    app.run(host="0.0.0.0", port=5000, debug=False)
