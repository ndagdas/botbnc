"""
TradingView → Binance Futures Webhook
Özellikler:
  - Exchange cache (API key başına 1 bağlantı)
  - Hızlı 200 yanıtı + arka plan işlem (timeout yok)
  - Telegram bildirimleri (ENTRY / TP1 / TP2 / STOP)
  - Duplicate alert koruması
  - Webhook secret doğrulaması
  - Doğru lot boyutu (amount_to_precision)
"""

import os
import json
import logging
import hashlib
import threading
import time
from datetime import datetime
from flask import Flask, request, jsonify
import ccxt
import requests
import traceback

# ─── LOGGING ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ─── EXCHANGE CACHE ───────────────────────────────────────────
# API key hash → ccxt exchange nesnesi
# Aynı kullanıcı her alert'te yeniden bağlanmak yerine
# önbelleklenmiş nesneyi kullanır → ~1-2 saniye tasarruf
_exchange_cache: dict = {}
_cache_lock = threading.Lock()

# ─── DUPLICATE KORUMASI ───────────────────────────────────────
# (ticker, side) çifti → son işlem zamanı (unix timestamp)
# Aynı sinyal 10 saniye içinde tekrar gelirse engellenir
_recent_signals: dict = {}
_signal_lock = threading.Lock()
DUPLICATE_WINDOW_SEC = 10


# ══════════════════════════════════════════════════════════════
# YARDIMCI FONKSİYONLAR
# ══════════════════════════════════════════════════════════════

def _key_hash(api_key: str) -> str:
    """API key'in SHA-256 özeti — cache anahtarı olarak kullanılır."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def get_or_create_exchange(api_key: str, secret_key: str, testnet: bool) -> ccxt.binance:
    """
    Exchange nesnesini önbellekten alır ya da yeni oluşturur.
    markets() ilk oluşturmada bir kere yüklenir, sonra atlanır.
    """
    key_id = _key_hash(api_key)

    with _cache_lock:
        if key_id in _exchange_cache:
            logger.info(f"Exchange cache hit: {key_id}")
            return _exchange_cache[key_id]

    # Cache'de yok — yeni oluştur
    config = {
        "apiKey": api_key.strip(),
        "secret": secret_key.strip(),
        "options": {
            "defaultType": "future",
            "adjustForTimeDifference": True,
        },
        "enableRateLimit": True,
    }

    exchange = ccxt.binance(config)

    if testnet:
        exchange.set_sandbox_mode(True)
        logger.info("Testnet modu aktif")

    # Markets yükle (sadece ilk seferinde ~1-2sn sürer)
    exchange.load_markets()
    exchange.fetch_time()  # Bağlantı testi

    with _cache_lock:
        _exchange_cache[key_id] = exchange

    logger.info(f"Yeni exchange oluşturuldu ve cache'lendi: {key_id}")
    return exchange


def is_duplicate(ticker: str, side: str) -> bool:
    """Son DUPLICATE_WINDOW_SEC saniye içinde aynı sinyal geldiyse True."""
    sig_key = f"{ticker}:{side}"
    now = time.time()

    with _signal_lock:
        last = _recent_signals.get(sig_key, 0)
        if now - last < DUPLICATE_WINDOW_SEC:
            return True
        _recent_signals[sig_key] = now
        return False


def parse_bool(val) -> bool:
    """'true', True, '1', 'yes' → True; diğerleri → False."""
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "1", "yes")


# ══════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════

def send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    """
    Telegram mesajı gönderir. Hata olursa sadece loglar, fırlatmaz.
    HTML parse modu aktif — <b>, <i>, <code> kullanılabilir.
    """
    if not bot_token or not chat_id:
        logger.warning("Telegram credentials eksik — mesaj gönderilmedi.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=5)
        if not resp.ok:
            logger.error(f"Telegram hata: {resp.status_code} {resp.text}")
        else:
            logger.info(f"Telegram mesajı gönderildi → {chat_id}")
    except Exception as e:
        logger.error(f"Telegram bağlantı hatası: {e}")


def tg_entry(bot_token, chat_id, ticker, interval, price, sl, tp1, tp2, qty, side="LONG"):
    text = (
        f"🚀 <b>YENİ POZİSYON AÇILDI</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>Sembol:</b> <code>{ticker}</code>  |  {interval}m\n"
        f"📈 <b>Yön:</b> {side}\n"
        f"💵 <b>Giriş:</b> <code>{price}</code>\n"
        f"🎯 <b>TP1:</b> <code>{tp1}</code>\n"
        f"🎯 <b>TP2:</b> <code>{tp2}</code>\n"
        f"🛑 <b>Stop:</b> <code>{sl}</code>\n"
        f"📦 <b>Miktar:</b> <code>{qty} $</code>\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )
    send_telegram(bot_token, chat_id, text)


def tg_tp(bot_token, chat_id, ticker, tp_num, price, entry_price):
    pct = ((float(price) - float(entry_price)) / float(entry_price) * 100) if entry_price else 0
    text = (
        f"✅ <b>TP{tp_num} ULAŞILDI</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🪙 <code>{ticker}</code>\n"
        f"💵 <b>Fiyat:</b> <code>{price}</code>\n"
        f"📊 <b>Kâr:</b> +{pct:.2f}%\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )
    send_telegram(bot_token, chat_id, text)


def tg_stop(bot_token, chat_id, ticker, exit_price, entry_price, exit_type="STOP"):
    pct = ((float(exit_price) - float(entry_price)) / float(entry_price) * 100) if entry_price else 0
    icon = "🛑" if pct < 0 else "🔒"
    label = "STOP LOSS" if exit_type == "STOP" else "TRAILING EXIT"
    text = (
        f"{icon} <b>{label}</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🪙 <code>{ticker}</code>\n"
        f"💵 <b>Çıkış:</b> <code>{exit_price}</code>\n"
        f"📊 <b>Sonuç:</b> {pct:+.2f}%\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )
    send_telegram(bot_token, chat_id, text)


# ══════════════════════════════════════════════════════════════
# EMIR FONKSİYONLARI
# ══════════════════════════════════════════════════════════════

def get_position(exchange: ccxt.binance, symbol: str) -> dict:
    """Mevcut açık pozisyonu döner."""
    try:
        positions = exchange.fetch_positions([symbol])
        for pos in positions:
            amt = float(pos.get("contracts", 0) or 0)
            if amt != 0:
                return {
                    "exists": True,
                    "amount": abs(amt),
                    "side": "long" if amt > 0 else "short",
                    "entry_price": float(pos.get("entryPrice", 0) or 0),
                }
    except Exception as e:
        logger.error(f"Pozisyon sorgulama hatası: {e}")
    return {"exists": False, "amount": 0, "side": None, "entry_price": 0}


def place_order(exchange: ccxt.binance, symbol: str, side: str, qty: float, reduce_only: bool = False):
    """Market emir gönderir. Hata olursa loglar ve None döner."""
    try:
        params = {"reduceOnly": True} if reduce_only else {}
        if side.upper() in ("BUY", "LONG"):
            order = exchange.create_market_buy_order(symbol, qty, params)
        else:
            order = exchange.create_market_sell_order(symbol, qty, params)
        logger.info(f"Emir: {side} {qty} {symbol} | reduceOnly={reduce_only}")
        return order
    except Exception as e:
        logger.error(f"Emir hatası: {e}")
        return None


def calc_qty(exchange: ccxt.binance, symbol: str, usdt_amount: float, price: float) -> float:
    """
    USDT miktarını, Binance'in stepSize kuralına uygun coin miktarına çevirir.
    round(x, 8) yerine amount_to_precision kullanılır.
    """
    raw_qty = usdt_amount / price
    return float(exchange.amount_to_precision(symbol, raw_qty))


# ══════════════════════════════════════════════════════════════
# ANA İŞLEM MANTIĞI (arka plan thread'inde çalışır)
# ══════════════════════════════════════════════════════════════

def process_signal(data: dict) -> None:
    """
    TradingView'den gelen JSON'u işler.
    Webhook handler'ı bekletmemek için ayrı thread'de çağrılır.
    """
    # ── Alanları oku ──────────────────────────────────────────
    ticker     = data.get("ticker", "").upper().replace(".P", "")
    price_str  = data.get("price", "0")
    side       = data.get("side", "").upper()
    qty_usdt   = float(data.get("quantity", 100))
    api_key    = data.get("binanceApiKey", "").strip()
    secret_key = data.get("binanceSecretKey", "").strip()
    tg_token   = data.get("telegramBotToken", "").strip()
    tg_chat    = data.get("telegramChatId", "").strip()
    testnet    = parse_bool(data.get("testnet", True))
    interval   = data.get("interval", "?")
    sl_price   = data.get("sl", "")
    tp1_price  = data.get("tp1", "")
    tp2_price  = data.get("tp2", "")
    entry_price= data.get("entryPrice", "")
    exit_price = data.get("exitPrice", "")
    exit_type  = data.get("exitType", "STOP")
    sug_qty    = data.get("suggestedQty", qty_usdt)

    try:
        price = float(price_str)
    except ValueError:
        price = 0.0

    if not ticker or not side:
        logger.error("ticker veya side eksik — sinyal atlandı.")
        return

    # ── Duplicate koruması ───────────────────────────────────
    if is_duplicate(ticker, side):
        logger.warning(f"Duplicate sinyal engellendi: {ticker} {side}")
        return

    # ── Sembol düzenle ───────────────────────────────────────
    symbol = ticker if ticker.endswith("USDT") else f"{ticker}USDT"

    # ── API key kontrolü ─────────────────────────────────────
    if not api_key or not secret_key:
        logger.error("API key eksik — işlem yapılamaz.")
        send_telegram(tg_token, tg_chat,
            f"⚠️ <b>HATA</b> — API key eksik!\nSembol: <code>{ticker}</code>")
        return

    # ── Exchange bağlantısı ──────────────────────────────────
    try:
        exchange = get_or_create_exchange(api_key, secret_key, testnet)
    except Exception as e:
        logger.error(f"Exchange bağlantı hatası: {e}")
        send_telegram(tg_token, tg_chat,
            f"⚠️ <b>BAĞLANTI HATASI</b>\n<code>{str(e)[:200]}</code>")
        return

    # ── Güncel fiyat (gönderilmediyse) ───────────────────────
    if price <= 0:
        try:
            ticker_data = exchange.fetch_ticker(symbol)
            price = ticker_data["last"]
        except Exception as e:
            logger.error(f"Fiyat alınamadı: {e}")
            return

    # ── Miktar hesapla ───────────────────────────────────────
    try:
        qty = calc_qty(exchange, symbol, qty_usdt, price)
    except Exception as e:
        logger.error(f"Miktar hesaplama hatası: {e}")
        qty = round(qty_usdt / price, 6)

    # ── Mevcut pozisyon ──────────────────────────────────────
    pos = get_position(exchange, symbol)
    logger.info(f"Mevcut pozisyon: {pos}")

    # ══════════════════════════════════════════════════════
    # SİNYAL TÜRÜNE GÖRE İŞLEM
    # ══════════════════════════════════════════════════════

    if side == "BUY":
        # Açık short varsa önce kapat
        if pos["exists"] and pos["side"] == "short":
            close_qty = float(exchange.amount_to_precision(symbol, pos["amount"]))
            place_order(exchange, symbol, "BUY", close_qty, reduce_only=True)
            logger.info(f"Short kapatıldı: {close_qty} {symbol}")

        # Long aç
        result = place_order(exchange, symbol, "BUY", qty)
        if result:
            tg_entry(
                tg_token, tg_chat,
                ticker, interval, price,
                sl=sl_price, tp1=tp1_price, tp2=tp2_price,
                qty=qty_usdt
            )

    elif side == "SELL":
        if pos["exists"] and pos["side"] == "long":
            close_qty = float(exchange.amount_to_precision(symbol, pos["amount"]))
            place_order(exchange, symbol, "SELL", close_qty, reduce_only=True)
        place_order(exchange, symbol, "SELL", qty)

    elif side == "TP1" and pos["exists"]:
        close_qty = float(exchange.amount_to_precision(symbol, pos["amount"] * 0.5))
        close_side = "SELL" if pos["side"] == "long" else "BUY"
        result = place_order(exchange, symbol, close_side, close_qty, reduce_only=True)
        if result:
            tg_tp(tg_token, tg_chat, ticker, 1, price, entry_price or pos["entry_price"])

    elif side == "TP2" and pos["exists"]:
        close_qty = float(exchange.amount_to_precision(symbol, pos["amount"] * 0.5))
        close_side = "SELL" if pos["side"] == "long" else "BUY"
        result = place_order(exchange, symbol, close_side, close_qty, reduce_only=True)
        if result:
            tg_tp(tg_token, tg_chat, ticker, 2, price, entry_price or pos["entry_price"])

    elif side == "STOP" and pos["exists"]:
        close_qty = float(exchange.amount_to_precision(symbol, pos["amount"]))
        close_side = "SELL" if pos["side"] == "long" else "BUY"
        result = place_order(exchange, symbol, close_side, close_qty, reduce_only=True)
        if result:
            ep = exit_price or price
            tg_stop(tg_token, tg_chat, ticker, ep,
                    entry_price or pos["entry_price"], exit_type)

    elif side in ("CLOSE_ALL", "CLOSE") and pos["exists"]:
        close_qty = float(exchange.amount_to_precision(symbol, pos["amount"]))
        close_side = "SELL" if pos["side"] == "long" else "BUY"
        place_order(exchange, symbol, close_side, close_qty, reduce_only=True)
        send_telegram(tg_token, tg_chat,
            f"🔒 <b>POZİSYON KAPATILDI</b>\n🪙 <code>{ticker}</code>")

    else:
        logger.warning(f"Bilinmeyen side veya pozisyon yok: {side} | pos={pos}")


# ══════════════════════════════════════════════════════════════
# WEBHOOK ENDPOINT
# ══════════════════════════════════════════════════════════════

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView'den gelen alert'i alır.
    1) Anında 200 döner (TradingView timeout almaz)
    2) İşlemi arka plan thread'inde yapar
    """
    # ── Webhook secret doğrulama ─────────────────────────────
    # Pine Script, JSON body içinde 'webhookSecret' gönderiyor.
    # Ek güvenlik için sunucuya X-Webhook-Token header'ı da eklenebilir.
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"status": "error", "message": "Geçersiz JSON"}), 400

    if WEBHOOK_SECRET:
        incoming_secret = (
            data.get("webhookSecret", "")
            or request.headers.get("X-Webhook-Token", "")
        )
        if incoming_secret != WEBHOOK_SECRET:
            logger.warning("Geçersiz webhook secret — istek reddedildi.")
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

    logger.info(f"Webhook alındı: {data.get('ticker')} {data.get('side')}")

    # ── Hemen 200 döndür ─────────────────────────────────────
    # TradingView 3 saniye içinde yanıt bekler.
    # İşlem arka planda yapılır → timeout sorunu ortadan kalkar.
    t = threading.Thread(target=_safe_process, args=(data,), daemon=True)
    t.start()

    return jsonify({"status": "received", "timestamp": datetime.now().isoformat()}), 200


def _safe_process(data: dict) -> None:
    """Hata yakalama katmanı — process_signal'ı sarmalar."""
    try:
        process_signal(data)
    except Exception as e:
        logger.error(f"İşlem hatası:\n{traceback.format_exc()}")
        # Telegram'a hata bildir (mümkünse)
        try:
            send_telegram(
                data.get("telegramBotToken", ""),
                data.get("telegramChatId", ""),
                f"❌ <b>SUNUCU HATASI</b>\n<code>{str(e)[:300]}</code>"
            )
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
# YARDIMCI ENDPOINT'LER
# ══════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "cached_exchanges": len(_exchange_cache),
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "TradingView → Binance Futures Webhook",
        "version": "2.0",
        "endpoints": {
            "POST /webhook": "TradingView alert endpoint",
            "GET  /health":  "Durum kontrolü",
        },
        "expected_fields": {
            "ticker":           "BTCUSDT veya BTCUSDT.P",
            "price":            "Mevcut fiyat (string)",
            "side":             "BUY | TP1 | TP2 | STOP | CLOSE_ALL",
            "quantity":         "USDT miktarı",
            "binanceApiKey":    "Binance API Key",
            "binanceSecretKey": "Binance Secret Key",
            "telegramBotToken": "Telegram Bot Token",
            "telegramChatId":   "Telegram Chat/Kanal ID",
            "webhookSecret":    "Sunucu doğrulama tokeni",
            "testnet":          "true / false",
            "sl":               "Stop loss fiyatı (opsiyonel)",
            "tp1":              "TP1 fiyatı (opsiyonel)",
            "tp2":              "TP2 fiyatı (opsiyonel)",
        },
    })


# ══════════════════════════════════════════════════════════════
# BAŞLATMA
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"

    logger.info("=" * 55)
    logger.info("  TradingView → Binance Futures Webhook v2.0")
    logger.info(f"  Port    : {port}")
    logger.info(f"  Secret  : {'ayarlı ✓' if WEBHOOK_SECRET else 'AYARLANMADI ⚠'}")
    logger.info("=" * 55)

    app.run(host="0.0.0.0", port=port, debug=debug)
