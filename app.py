#!/usr/bin/env python3
# ============================================================
#  BOT.PY  —  Binance Futures Long Bot
#  Platform : Heroku
#  Credentials: TradingView webhook payload'dan gelir
# ============================================================

import logging
import math
import os
import requests
from flask import Flask, request, jsonify
from binance.um_futures import UMFutures
from binance.error import ClientError

# ── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

PORT         = int(os.environ.get("PORT", 5000))
TP1_CLOSE    = 0.50   # Pozisyonun %50'si
TP2_CLOSE    = 0.30   # Kalanın %30'u

# ── Binance ─────────────────────────────────────────────────
def get_client(api_key: str, api_secret: str, testnet: bool) -> UMFutures:
    if testnet:
        return UMFutures(
            key=api_key, secret=api_secret,
            base_url="https://testnet.binancefuture.com"
        )
    return UMFutures(key=api_key, secret=api_secret)

# ── Telegram ────────────────────────────────────────────────
def tg(token: str, chat: str, msg: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        log.error(f"Telegram: {e}")

# ── Yardımcılar ─────────────────────────────────────────────
def clean_symbol(raw: str) -> str:
    s = raw.upper().strip()
    return s[:-2] if s.endswith(".P") else s

# Exchange info cache — her API anahtarı için ayrı tutulur
# { api_key: {"data": {...}, "ts": timestamp} }
_exchange_cache: dict = {}
CACHE_TTL = 300   # 5 dakika — yeni listelenen coinler için kısa tutuldu

def get_exchange_info(client: UMFutures, api_key: str, force_refresh: bool = False) -> dict:
    """Exchange bilgisini cache'le, TTL dolunca veya force_refresh=True ise tazele."""
    import time
    now = time.time()
    cached = _exchange_cache.get(api_key)

    if not force_refresh and cached and (now - cached["ts"]) < CACHE_TTL:
        log.info(f"Exchange cache hit ({int(now - cached['ts'])}s önce alındı)")
        return cached["data"]

    log.info("Exchange info taze çekiliyor...")
    data = client.exchange_info()
    _exchange_cache[api_key] = {"data": data, "ts": now}
    return data

def _parse_symbol(s: dict) -> dict:
    """Exchange info içindeki sembol objesini parse et."""
    max_qty = min_qty = None
    for f in s.get("filters", []):
        if f["filterType"] == "LOT_SIZE":
            max_qty = float(f["maxQty"])
            min_qty = float(f["minQty"])
            break
    return {
        "qty"    : s["quantityPrecision"],
        "prc"    : s["pricePrecision"],
        "max_qty": max_qty,
        "min_qty": min_qty,
    }

def get_symbol_info(client: UMFutures, symbol: str, api_key: str = "") -> dict:
    """
    Sembol bilgisini döndür.
    Sembol cache'de yoksa → taze exchange_info çek, tekrar dene.
    Yeni listelenen coinler (UAIUSDT gibi) böylece yakalanır.
    """
    info = get_exchange_info(client, api_key)

    for s in info["symbols"]:
        if s["symbol"] == symbol:
            return _parse_symbol(s)

    # Cache'de bulunamadı → taze çek ve bir kez daha dene
    log.warning(f"{symbol} cache'de yok, exchange_info tazele ve tekrar ara...")
    info = get_exchange_info(client, api_key, force_refresh=True)

    for s in info["symbols"]:
        if s["symbol"] == symbol:
            log.info(f"{symbol} taze exchange_info'da bulundu (yeni listelenmiş olabilir)")
            return _parse_symbol(s)

    raise ValueError(f"Binance Futures'da sembol bulunamadı: {symbol} "
                     f"(cache yenilendi, yine de yok)")

def floor_qty(val: float, precision: int) -> float:
    f = 10 ** precision
    return math.floor(val * f) / f

def mark_price(client: UMFutures, symbol: str) -> float:
    return float(client.mark_price(symbol=symbol)["markPrice"])

def open_position(client: UMFutures, symbol: str):
    for p in client.get_position_risk(symbol=symbol):
        if float(p["positionAmt"]) > 0:
            return p
    return None

# ── LONG AÇ ─────────────────────────────────────────────────
def open_long(client, token, chat, testnet, api_key,
              symbol, usdt, leverage, tp1, tp2, stop):
    try:
        if open_position(client, symbol):
            tg(token, chat,
               f"⚠️ <b>{symbol}</b>\nAçık pozisyon var, sinyal atlandı.")
            return

        # Kaldıraç
        try:
            client.change_leverage(symbol=symbol, leverage=leverage)
        except ClientError:
            pass

        info     = get_symbol_info(client, symbol, api_key)   # Sembol yoksa burada ValueError fırlatır
        price    = mark_price(client, symbol)

        # ✅ DOĞRU FORMÜL: notional = usdt × kaldıraç, qty = notional / fiyat
        notional = usdt * leverage                             # örn: 100 × 20 = 2000 USDT
        qty      = floor_qty(notional / price, info["qty"])   # örn: 2000 / 0.05 = 40000 lot

        log.info(f"Hesap: {usdt} USDT × x{leverage} = {notional} USDT notional | "
                 f"Fiyat: {price} | Ham lot: {notional/price:.4f} | Son lot: {qty}")

        if qty <= 0:
            raise ValueError(f"Lot sıfır hesaplandı — fiyat:{price} notional:{notional}")

        # Max lot kontrolü (KASUSDT gibi küçük lotlu semboller için)
        if info["max_qty"] and qty > info["max_qty"]:
            log.warning(f"{symbol} max lot aşıldı: {qty} > {info['max_qty']}, max'a kırpılıyor")
            qty = floor_qty(info["max_qty"], info["qty"])
            # Gerçek USDT miktarını yeniden hesapla
            actual_usdt = round((qty * price) / leverage, 2)
            log.info(f"{symbol} kırpılmış lot: {qty} = yaklaşık {actual_usdt} USDT margin")

        # Min lot kontrolü
        if info["min_qty"] and qty < info["min_qty"]:
            raise ValueError(f"{symbol} min lot altında: {qty} < {info['min_qty']}")

        # Market ile aç
        client.new_order(symbol=symbol, side="BUY",
                         type="MARKET", quantity=qty)

        pp = info["prc"]

        # TP1 — %50
        qty_tp1 = floor_qty(qty * TP1_CLOSE, info["qty"])
        client.new_order(
            symbol=symbol, side="SELL", type="TAKE_PROFIT_MARKET",
            stopPrice=round(tp1, pp), quantity=qty_tp1,
            timeInForce="GTE_GTC", reduceOnly="true"
        )

        # TP2 — kalan %30
        qty_rest = floor_qty(qty - qty_tp1, info["qty"])
        qty_tp2  = floor_qty(qty_rest * TP2_CLOSE, info["qty"])
        client.new_order(
            symbol=symbol, side="SELL", type="TAKE_PROFIT_MARKET",
            stopPrice=round(tp2, pp), quantity=qty_tp2,
            timeInForce="GTE_GTC", reduceOnly="true"
        )

        # STOP — tamamını kapat
        client.new_order(
            symbol=symbol, side="SELL", type="STOP_MARKET",
            stopPrice=round(stop, pp), closePosition="true",
            timeInForce="GTE_GTC"
        )

        log.info(f"LONG: {symbol} {qty} lot x{leverage}")
        notional_display = round(usdt * leverage, 2)
        tg(token, chat,
           f"🟢 <b>{symbol} LONG AÇILDI</b>\n"
           f"━━━━━━━━━━━━━━━━━\n"
           f"💰 Teminat : <b>{usdt} USDT</b>\n"
           f"⚡ Kaldıraç: <b>x{leverage}</b>\n"
           f"📊 Notional: <b>{notional_display} USDT</b>\n"
           f"📦 Lot     : <b>{qty}</b>\n"
           f"💵 Giriş   : <b>{price}</b>\n"
           f"━━━━━━━━━━━━━━━━━\n"
           f"🎯 TP1     : <b>{tp1}</b>  → %50\n"
           f"🎯 TP2     : <b>{tp2}</b>  → kalanın %30'u\n"
           f"🛑 STOP    : <b>{stop}</b>\n"
           f"{'🔴 TESTNET' if testnet else '🟢 GERÇEK HESAP'}"
        )

    except ValueError as e:
        # Sembol geçersiz veya lot hatası — Telegram'a bildir
        log.error(f"open_long [{symbol}]: {e}")
        tg(token, chat,
           f"❌ <b>{symbol} LONG açılamadı</b>\n"
           f"━━━━━━━━━━━━━━━━━\n"
           f"🔍 Sebep: {e}")
    except Exception as e:
        log.error(f"open_long [{symbol}]: {e}")
        tg(token, chat,
           f"❌ <b>{symbol} LONG açılamadı</b>\n"
           f"━━━━━━━━━━━━━━━━━\n"
           f"🔍 Sebep: {e}")


# ── TP / STOP BİLDİRİMLERİ ──────────────────────────────────
def handle_tp1(client, token, chat, symbol):
    pos = open_position(client, symbol)
    rem = float(pos["positionAmt"]) if pos else 0
    tg(token, chat,
       f"🎯 <b>{symbol} TP1 HİT</b>\n"
       f"━━━━━━━━━━━━━━━━━\n"
       f"✅ Pozisyonun <b>%50'si</b> kapatıldı\n"
       f"📦 Kalan lot: <b>{rem}</b>"
    )

def handle_tp2(client, token, chat, symbol):
    pos = open_position(client, symbol)
    rem = float(pos["positionAmt"]) if pos else 0
    tg(token, chat,
       f"🎯 <b>{symbol} TP2 HİT</b>\n"
       f"━━━━━━━━━━━━━━━━━\n"
       f"✅ Kalanın <b>%30'u</b> kapatıldı\n"
       f"📦 Kalan lot: <b>{rem}</b>"
    )

def handle_stop(client, token, chat, symbol):
    try:
        client.cancel_open_orders(symbol=symbol)
    except Exception as e:
        log.warning(f"Emir iptal [{symbol}]: {e}")
    tg(token, chat,
       f"🛑 <b>{symbol} STOP HİT</b>\n"
       f"━━━━━━━━━━━━━━━━━\n"
       f"❌ Tüm pozisyon kapatıldı"
    )


# ── FLASK ────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "Boş payload"}), 400

        action = str(data.get("action", "")).lower()
        symbol = clean_symbol(str(data.get("symbol", "")))

        # Credentials
        api_key    = str(data.get("api_key", ""))
        api_secret = str(data.get("api_secret", ""))
        tg_token   = str(data.get("tg_token", ""))
        tg_chat    = str(data.get("tg_chat_id", ""))
        testnet    = str(data.get("testnet", "true")).lower() == "true"

        if not all([action, symbol, api_key, api_secret, tg_token, tg_chat]):
            return jsonify({"error": "Eksik alan var"}), 400

        log.info(f"▶ {action.upper()} | {symbol} | testnet={testnet}")
        client = get_client(api_key, api_secret, testnet)

        if action == "buy":
            open_long(
                client, tg_token, tg_chat, testnet, api_key, symbol,
                usdt     = float(data.get("usdt", 0)),
                leverage = int(data.get("leverage", 1)),
                tp1      = float(data.get("tp1", 0)),
                tp2      = float(data.get("tp2", 0)),
                stop     = float(data.get("stop", 0))
            )
        elif action == "tp1":
            handle_tp1(client, tg_token, tg_chat, symbol)
        elif action == "tp2":
            handle_tp2(client, tg_token, tg_chat, symbol)
        elif action == "stop":
            handle_stop(client, tg_token, tg_chat, symbol)
        else:
            return jsonify({"error": f"Bilinmeyen action: {action}"}), 400

        return jsonify({"status": "ok", "action": action, "symbol": symbol}), 200

    except Exception as e:
        log.error(f"Webhook hatası: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "platform": "heroku"}), 200

if __name__ == "__main__":
    log.info(f"Bot başlatıldı | Port: {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
