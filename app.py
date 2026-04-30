import json
import logging
import threading
import time
import sys
import math
import requests
from flask import Flask, request, jsonify
import ccxt

# ------------------- KONFİGÜRASYON -------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Aktif işlemler: key = (api_key, coin)  -> değer = Trade object
active_trades = {}
trade_lock = threading.Lock()

# ------------------- YARDIMCI FONKSİYONLAR -------------------
def send_telegram(telegram_token, chat_id, message):
    """Telegram mesajı gönderir"""
    if not telegram_token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        data = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=data, timeout=5)
    except Exception as e:
        logger.error(f"Telegram hatası: {e}")

def create_futures_client(api_key, api_secret, testnet=False):
    """CCXT kullanarak Binance Futures istemcisi oluşturur"""
    exchange = ccxt.binance({
        'apiKey': api_key,
        'secret': api_secret,
        'options': {
            'defaultType': 'future',  # Futures piyasasını işaret et
        },
        'enableRateLimit': True,
    })
    if testnet:
        exchange.set_sandbox_mode(True)  # Testnet modu
    return exchange

def set_leverage(exchange, symbol, leverage):
    """Belirtilen sembol için kaldıraç oranını ayarlar"""
    try:
        # Binance Futures için kaldıraç ayarlama endpoint'i
        exchange.fapiPrivate_post_leverage({
            'symbol': symbol.replace('/', ''),  # örn: 'BTC/USDT' -> 'BTCUSDT'
            'leverage': leverage
        })
        logger.info(f"✅ Kaldıraç {leverage}x olarak ayarlandı: {symbol}")
        return True
    except Exception as e:
        logger.error(f"❌ Kaldıraç ayarlanamadı {symbol}: {e}")
        return False

def calculate_position_size(client, coin, usdt_amount, leverage, stop_percent, risk_percent):
    """Risk yönetimine göre pozisyon büyüklüğünü hesaplar"""
    ticker = client.fetch_ticker(coin)
    entry_price = ticker['last']
    
    # Risk hesaplama: risk oranına göre pozisyon büyüklüğü
    # Önce kullanıcının futures cüzdanındaki bakiyeyi al
    balance = client.fetch_balance()
    usdt_balance = balance['total'].get('USDT', 0)
    
    if usdt_balance <= 0:
        raise ValueError("Futures cüzdanında USDT bakiyesi bulunamadı!")
    
    # Risk yönetimi: hesap bakiyesinin risk_oranı kadarını riske et
    risk_amount = usdt_balance * (risk_percent / 100)
    
    # Stop loss mesafesi (yüzde olarak girilen değer)
    stop_distance_percent = stop_percent / 100
    stop_loss_price = entry_price * (1 - stop_distance_percent)
    stop_distance_usdt = entry_price - stop_loss_price
    
    # Pozisyon büyüklüğü = Risk edilen miktar / Stop loss mesafesi (USDT cinsinden)
    position_size = risk_amount / stop_distance_usdt
    
    # Kaldıraç uygulanmış marjin miktarı
    margin = position_size / leverage
    
    # Kullanıcının belirttiği USDT miktarını aşmasın
    if margin > usdt_amount:
        margin = usdt_amount
        position_size = margin * leverage
    
    logger.info(f"💰 Hesap bakiyesi: {usdt_balance} USDT | Risk: {risk_amount} USDT")
    logger.info(f"🎯 Hesaplanan pozisyon: {position_size} {coin} (Marjin: {margin} USDT)")
    
    return position_size, margin, entry_price

def place_futures_order(client, coin, side, quantity, position_side):
    """Futures piyasa emri gönderir (Long/Short)"""
    try:
        # CCXT ile futures market order
        order = client.create_order(
            symbol=coin,
            type='market',
            side=side,
            amount=quantity,
            params={
                'positionSide': position_side  # LONG veya SHORT
            }
        )
        logger.info(f"✅ {side} {position_side} emri: {quantity} {coin} - {order}")
        return order
    except Exception as e:
        logger.error(f"❌ Emir hatası: {e}")
        raise

# ------------------- İŞLEM YÖNETİCİSİ SINIFI -------------------
class TradeMonitor:
    """Açık bir pozisyonu TP/SL için izler"""
    def __init__(self, client, telegram_token, chat_id, trade_id, coin, position_side, 
                 entry_price, quantity, tp1_price, tp2_price, stop_price,
                 tp1_quantity, tp2_quantity):
        self.client = client
        self.telegram_token = telegram_token
        self.chat_id = chat_id
        self.trade_id = trade_id
        self.coin = coin
        self.position_side = position_side
        self.entry_price = entry_price
        self.total_quantity = quantity
        self.remaining_quantity = quantity
        self.tp1_price = tp1_price
        self.tp2_price = tp2_price
        self.stop_price = stop_price
        self.tp1_qty = tp1_quantity
        self.tp2_qty = tp2_quantity
        self.tp1_triggered = False
        self.tp2_triggered = False
        self.closed = False
        
        self._send_notification(f"🟢 **Yeni {position_side} İşlem Açıldı**\n"
                                f"Coin: {coin}\n"
                                f"Miktar: {quantity}\n"
                                f"Giriş: {entry_price}\n"
                                f"TP1: {tp1_price} (satılacak: {tp1_qty})\n"
                                f"TP2: {tp2_price} (satılacak: {tp2_qty})\n"
                                f"Stop: {stop_price}")

    def _send_notification(self, message):
        send_telegram(self.telegram_token, self.chat_id, message)

    def _close_position(self, quantity, reason):
        """Pozisyonu kapat (Long ise sell, Short ise buy)"""
        if quantity <= 0 or self.remaining_quantity <= 0:
            return
        qty = min(quantity, self.remaining_quantity)
        try:
            side = 'sell' if self.position_side == 'LONG' else 'buy'
            order = place_futures_order(self.client, self.coin, side, qty, self.position_side)
            self.remaining_quantity -= qty
            self._send_notification(f"{reason}\n💰 Kapatılan: {qty} {self.coin}\n📊 Kalan: {self.remaining_quantity}")
            if self.remaining_quantity <= 0:
                self.closed = True
                self._send_notification(f"✅ {self.coin} işlemi tamamen kapatıldı.")
        except Exception as e:
            self._send_notification(f"❌ Kapatma hatası: {str(e)[:100]}")

    def execute_tp1(self):
        if not self.tp1_triggered and self.remaining_quantity > 0:
            self.tp1_triggered = True
            self._close_position(self.tp1_qty, "📈 **TP1 gerçekleşti** (yarısı kapatıldı)")

    def execute_tp2(self):
        if self.tp1_triggered and not self.tp2_triggered and self.remaining_quantity > 0:
            self.tp2_triggered = True
            self._close_position(self.tp2_qty, "📈 **TP2 gerçekleşti** (kalanın %30'u kapatıldı)")

    def execute_stop(self):
        if not self.closed and self.remaining_quantity > 0:
            self._close_position(self.remaining_quantity, "🛑 **Stop Loss tetiklendi** (tümü kapatıldı)")
            self.closed = True

    def monitor(self):
        """Her saniye fiyatı kontrol eden döngü"""
        while not self.closed and self.remaining_quantity > 0:
            try:
                ticker = self.client.fetch_ticker(self.coin)
                price = ticker['last']
                
                # LONG pozisyon için fiyat kontrolü
                if self.position_side == 'LONG':
                    if not self.tp1_triggered and price >= self.tp1_price:
                        self.execute_tp1()
                    elif self.tp1_triggered and not self.tp2_triggered and price >= self.tp2_price:
                        self.execute_tp2()
                    elif price <= self.stop_price:
                        self.execute_stop()
                else:  # SHORT pozisyon
                    if not self.tp1_triggered and price <= self.tp1_price:
                        self.execute_tp1()
                    elif self.tp1_triggered and not self.tp2_triggered and price <= self.tp2_price:
                        self.execute_tp2()
                    elif price >= self.stop_price:
                        self.execute_stop()
                        
            except Exception as e:
                logger.error(f"Monitör hatası {self.coin}: {e}")
            time.sleep(1)

# ------------------- WEBHOOK ANA İŞLEYİCİ -------------------
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            data = request.form.to_dict()
        if not data:
            return jsonify({"status": "error", "message": "No data"}), 400

        logger.info(f"📩 Gelen sinyal: {data.get('action')} - {data.get('coin')}")

        # Zorunlu alanlar
        required = ['apiKey', 'apiSecret', 'action', 'coin', 'usdtAmount', 
                   'leverage', 'tp1Percent', 'tp2Percent', 'stopPercent']
        for field in required:
            if field not in data:
                return jsonify({"status": "error", "message": f"Eksik alan: {field}"}), 400

        api_key = data['apiKey']
        api_secret = data['apiSecret']
        telegram_token = data.get('telegramToken', '')
        telegram_chatid = data.get('telegramChatId', '')
        testnet = data.get('testnet', True)
        action = data['action']
        coin = data['coin']
        usdt_amount = float(data['usdtAmount'])
        leverage = int(data['leverage'])
        tp1_percent = float(data['tp1Percent'])
        tp2_percent = float(data['tp2Percent'])
        stop_percent = float(data['stopPercent'])
        risk_percent = float(data.get('riskPercent', 2.0))  # Varsayılan %2 risk
        
        # Unique key for this user+coin
        user_coin_key = (api_key, coin)
        
        # CCXT futures client oluştur
        client = create_futures_client(api_key, api_secret, testnet)
        
        # --- ACTION: BUY (LONG) ---
        if action == 'buy':
            with trade_lock:
                if user_coin_key in active_trades and not active_trades[user_coin_key].closed:
                    msg = f"⚠️ {coin} için zaten açık işlem var, yeni buy reddedildi."
                    send_telegram(telegram_token, telegram_chatid, msg)
                    return jsonify({"status": "error", "message": "Active trade exists"}), 409
            
            # Kaldıraç ayarla
            set_leverage(client, coin, leverage)
            
            # Pozisyon büyüklüğünü hesapla
            try:
                position_size, margin, entry_price = calculate_position_size(
                    client, coin, usdt_amount, leverage, stop_percent, risk_percent
                )
            except Exception as e:
                send_telegram(telegram_token, telegram_chatid, f"❌ Hesaplama hatası: {str(e)[:200]}")
                return jsonify({"status": "error", "message": str(e)}), 500
            
            # LONG pozisyon aç
            try:
                order = place_futures_order(client, coin, 'buy', position_size, 'LONG')
                trade_id = order.get('id', str(time.time()))
            except Exception as e:
                send_telegram(telegram_token, telegram_chatid, f"❌ LONG açma hatası: {str(e)[:200]}")
                return jsonify({"status": "error", "message": str(e)}), 500
            
            # TP/SL fiyatlarını hesapla
            tp1_price = entry_price * (1 + tp1_percent / 100)
            tp2_price = entry_price * (1 + tp2_percent / 100)
            stop_price = entry_price * (1 - stop_percent / 100)
            
            # Satılacak miktarlar
            tp1_qty = position_size * 0.5  # %50
            tp2_qty = position_size * 0.3  # kalanın %30'u
            
            # Trade monitor oluştur
            monitor = TradeMonitor(
                client, telegram_token, telegram_chatid, trade_id, coin, 'LONG',
                entry_price, position_size, tp1_price, tp2_price, stop_price,
                tp1_qty, tp2_qty
            )
            
            with trade_lock:
                active_trades[user_coin_key] = monitor
            
            # Monitör thread'i başlat
            monitor_thread = threading.Thread(target=monitor.monitor, daemon=True)
            monitor_thread.start()
            
            send_telegram(telegram_token, telegram_chatid, f"✅ **LONG pozisyon açıldı!**\n"
                          f"Coin: {coin}\n"
                          f"Pozisyon: {position_size:.4f} {coin}\n"
                          f"Marjin: {margin:.2f} USDT\n"
                          f"Kaldıraç: {leverage}x\n"
                          f"Giriş: {entry_price:.2f}")
            
            return jsonify({"status": "success", "message": f"LONG opened: {position_size} {coin}"}), 200
            
        # --- ACTION: TP1, TP2, STOP (bu sinyaller opsiyonel, monitor zaten yapıyor) ---
        # Bu sinyaller güvenlik yedeği olarak tutulabilir, ancak monitor daha öncelikli
        else:
            with trade_lock:
                trade = active_trades.get(user_coin_key)
            if trade:
                if action == 'tp1':
                    trade.execute_tp1()
                elif action == 'tp2':
                    trade.execute_tp2()
                elif action == 'stop':
                    trade.execute_stop()
                    with trade_lock:
                        if user_coin_key in active_trades:
                            del active_trades[user_coin_key]
            return jsonify({"status": "success", "message": f"{action} signal received"}), 200
            
    except Exception as e:
        logger.error(f"Webhook hatası: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ------------------- SAĞLIK KONTROLÜ -------------------
@app.route('/health', methods=['GET'])
def health():
    with trade_lock:
        active_count = len(active_trades)
    return jsonify({"status": "running", "active_trades": active_count}), 200

# ------------------- WORKER VE WEB AYRIŞTIRMASI -------------------
# NOT: Bu bot Heroku'da hem web (gunicorn) hem de worker olarak çalışacak şekilde tasarlanmıştır.
# Worker sürekli çalışarak monitor thread'lerini yönetir.

if __name__ == '__main__':
    # Eğer doğrudan çalıştırılırsa (development) Flask sunucusunu başlat
    # Production'da gunicorn kullanılacak
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
