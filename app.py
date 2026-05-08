#!/usr/bin/env python3
# ============================================================
#  BIST_BOT.PY  —  AlgoLab / BIST Spot Trading Bot
#  Platform   : Heroku
#  Credentials: TradingView webhook + Telegram SMS doğrulama
# ============================================================

import logging
import math
import os
import time
import requests
import hashlib
import json
import base64
from threading import Thread, Lock
from flask import Flask, request, jsonify
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# ── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", 5000))

# ── Algolab Sabitler ─────────────────────────────────────────
API_HOSTNAME  = "www.algolab.com.tr"
API_URL       = f"https://{API_HOSTNAME}/api"
PING_INTERVAL = 60   # saniye (oturum canlı tutma)

# ── TP Oranları ──────────────────────────────────────────────
TP1_RATIO = 0.25
TP2_RATIO = round(30 / 75, 6)   # 0.40
TP3_RATIO = round(25 / 45, 6)   # 0.5556

# ── Oturum Durumu ────────────────────────────────────────────
session = {
    "api_key"    : "",
    "hash"       : "",
    "tg_token"   : "",
    "tg_chat"    : "",
    "logged_in"  : False,
    "temp_token" : "",   # SMS doğrulama için geçici token
}
session_lock = Lock()

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

def tg_s(msg: str):
    """Session'daki token/chat ile gönder."""
    if session["tg_token"] and session["tg_chat"]:
        tg(session["tg_token"], session["tg_chat"], msg)

# ── AES Şifreleme ────────────────────────────────────────────
def encrypt(text: str, api_key: str) -> str:
    """AlgoLab AES-256-CBC şifreleme."""
    try:
        key_code = api_key.replace("API-", "")
        key      = hashlib.sha256(key_code.encode()).digest()
        iv       = key[:16]
        cipher   = AES.new(key, AES.MODE_CBC, iv)
        padded   = pad(text.encode("utf-8"), AES.block_size)
        ct_bytes = cipher.encrypt(padded)
        return base64.b64encode(ct_bytes).decode("utf-8")
    except Exception as e:
        log.error(f"Şifreleme hatası: {e}")
        return ""

def make_checker(endpoint: str, payload: dict, api_key: str) -> str:
    """AlgoLab Checker header'ı oluştur."""
    body    = json.dumps(payload, separators=(",", ":"))
    raw     = f"{api_key}{endpoint}{body}"
    checker = hashlib.sha256(raw.encode()).hexdigest()
    return checker

# ── AlgoLab API İstekleri ────────────────────────────────────
def algolab_post(endpoint: str, payload: dict, login: bool = False) -> dict:
    """AlgoLab REST API POST."""
    url = f"{API_URL}{endpoint}"
    if login:
        headers = {"APIKEY": session["api_key"]}
    else:
        checker = make_checker(endpoint, payload, session["api_key"])
        headers = {
            "APIKEY"       : session["api_key"],
            "Checker"      : checker,
            "Authorization": session["hash"],
        }
    resp = requests.post(url, json=payload, headers=headers, timeout=15)
    return resp.json()

def algolab_get(endpoint: str, params: dict = None) -> dict:
    url     = f"{API_URL}{endpoint}"
    checker = make_checker(endpoint, params or {}, session["api_key"])
    headers = {
        "APIKEY"       : session["api_key"],
        "Checker"      : checker,
        "Authorization": session["hash"],
    }
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    return resp.json()

# ── Oturum Yönetimi ──────────────────────────────────────────
def login_step1(api_key: str, username: str, password: str) -> bool:
    """
    Login adım 1: SMS gönder, geçici token al.
    username = TC Kimlik No
    password = Denizbank hesap şifresi
    """
    session["api_key"] = api_key if api_key.startswith("API-") else f"API-{api_key}"
    enc_user = encrypt(username, session["api_key"])
    enc_pass = encrypt(password, session["api_key"])
    payload  = {"username": enc_user, "password": enc_pass}
    try:
        result = algolab_post("/LoginUser", payload, login=True)
        if result.get("Success"):
            session["temp_token"] = result["Content"]["token"]
            log.info("Login adım 1 başarılı — SMS gönderildi")
            return True
        else:
            log.error(f"Login adım 1 başarısız: {result.get('Message')}")
            return False
    except Exception as e:
        log.error(f"Login adım 1 hatası: {e}")
        return False

def login_step2(sms_code: str) -> bool:
    """Login adım 2: SMS kodunu doğrula, hash al."""
    enc_token = encrypt(session["temp_token"], session["api_key"])
    enc_sms   = encrypt(sms_code, session["api_key"])
    payload   = {"token": enc_token, "Password": enc_sms}
    try:
        result = algolab_post("/LoginUserControl", payload, login=True)
        if result.get("Success"):
            session["hash"]      = result["Content"]["Hash"]
            session["logged_in"] = True
            session["temp_token"] = ""
            log.info("Login tamamlandı ✅")
            return True
        else:
            log.error(f"SMS doğrulama başarısız: {result.get('Message')}")
            return False
    except Exception as e:
        log.error(f"Login adım 2 hatası: {e}")
        return False

def ping_loop():
    """Oturumu canlı tut."""
    while True:
        time.sleep(PING_INTERVAL)
        if session["logged_in"]:
            try:
                algolab_post("/Ping", {})
                log.info("Ping OK")
            except Exception as e:
                log.warning(f"Ping başarısız: {e}")
                session["logged_in"] = False
                tg_s("⚠️ <b>Algolab oturumu kapandı!</b>\nYeniden login gerekli.")

# ── Portföy / Pozisyon ───────────────────────────────────────
def get_position(symbol: str) -> dict:
    """Portföydeki hisse miktarını al."""
    try:
        result = algolab_get("/GetEquityInfo", {"symbol": symbol})
        if result.get("Success") and result.get("Content"):
            for item in result["Content"]:
                if item.get("symbol") == symbol or item.get("Symbol") == symbol:
                    qty = float(item.get("qty", item.get("Qty", 0)))
                    cost = float(item.get("avgCost", item.get("AvgCost", 0)))
                    return {"qty": qty, "avg_cost": cost}
    except Exception as e:
        log.error(f"Pozisyon alınamadı [{symbol}]: {e}")
    return {"qty": 0, "avg_cost": 0}

def get_last_price(symbol: str) -> float:
    """Anlık fiyat al."""
    try:
        result = algolab_get("/GetEquities", {"symbol": symbol, "period": "1d"})
        if result.get("Success") and result.get("Content"):
            return float(result["Content"][0].get("c", 0))
    except Exception as e:
        log.error(f"Fiyat alınamadı [{symbol}]: {e}")
    return 0.0

# ── Emir Fonksiyonları ───────────────────────────────────────
def market_buy(symbol: str, tl_amount: float) -> dict:
    """Piyasa emriyle hisse al."""
    price = get_last_price(symbol)
    if not price:
        raise ValueError(f"Fiyat alınamadı: {symbol}")
    lot = math.floor(tl_amount / price)
    if lot <= 0:
        raise ValueError(f"Lot sıfır: {symbol} fiyat={price} miktar={tl_amount}")
    payload = {
        "symbol"    : symbol,
        "direction" : "Buy",
        "pricetype" : "piyasa",
        "lot"       : str(lot),
        "price"     : "0",
        "sms"       : "false",
        "email"     : "false",
        "subAccount": ""
    }
    result = algolab_post("/SendOrder", payload)
    if not result.get("Success"):
        raise Exception(f"Emir hatası: {result.get('Message')}")
    log.info(f"BUY: {symbol} {lot} lot @ ~{price} TL")
    return {"lot": lot, "price": price, "total": round(lot * price, 2)}

def market_sell(symbol: str, lot: int) -> dict:
    """Piyasa emriyle hisse sat."""
    if lot <= 0:
        return {"lot": 0}
    payload = {
        "symbol"    : symbol,
        "direction" : "Sell",
        "pricetype" : "piyasa",
        "lot"       : str(lot),
        "price"     : "0",
        "sms"       : "false",
        "email"     : "false",
        "subAccount": ""
    }
    result = algolab_post("/SendOrder", payload)
    if not result.get("Success"):
        raise Exception(f"Satış emir hatası: {result.get('Message')}")
    log.info(f"SELL: {symbol} {lot} lot")
    return {"lot": lot}

def sell_ratio(symbol: str, ratio: float) -> int:
    """Pozisyonun belirli oranını sat."""
    pos = get_position(symbol)
    if not pos["qty"]:
        log.info(f"Satış atlandı: {symbol} pozisyon yok")
        return 0
    lot = math.floor(pos["qty"] * ratio)
    if lot <= 0:
        return 0
    market_sell(symbol, lot)
    return lot

# ── İşlem Handler'ları ───────────────────────────────────────
def open_long(symbol: str, tl_amount: float,
              tp1: float, tp2: float, tp3: float, stop: float,
              tg_token: str, tg_chat: str):
    try:
        result = market_buy(symbol, tl_amount)
        pos    = get_position(symbol)
        tg(tg_token, tg_chat,
           f"🟢 <b>{symbol} ALINDI</b>\n"
           f"━━━━━━━━━━━━━━━━━\n"
           f"💰 Miktar : <b>{tl_amount} TL</b>\n"
           f"📦 Lot    : <b>{result['lot']}</b>\n"
           f"💵 Fiyat  : <b>{result['price']} TL</b>\n"
           f"━━━━━━━━━━━━━━━━━\n"
           f"🎯 TP1    : <b>{tp1}</b>  → %25\n"
           f"🎯 TP2    : <b>{tp2}</b>  → %30\n"
           f"🎯 TP3    : <b>{tp3}</b>  → %25\n"
           f"🛑 Stop   : <b>{stop}</b>\n"
           f"🏦 BIST / Algolab"
        )
    except Exception as e:
        log.error(f"open_long [{symbol}]: {e}")
        tg(tg_token, tg_chat, f"❌ <b>{symbol} alım hatası</b>\n🔍 {e}")

def handle_tp1(symbol: str, tg_token: str, tg_chat: str):
    try:
        sold = sell_ratio(symbol, TP1_RATIO)
        pos  = get_position(symbol)
        tg(tg_token, tg_chat,
           f"🎯 <b>{symbol} TP1 HİT</b>\n"
           f"━━━━━━━━━━━━━━━━━\n"
           f"✅ <b>{sold} lot (%25)</b> satıldı\n"
           f"📦 Kalan: <b>{pos['qty']} lot</b>"
        )
    except Exception as e:
        log.error(f"TP1 [{symbol}]: {e}")
        tg(tg_token, tg_chat, f"❌ <b>{symbol} TP1 hatası</b>\n{e}")

def handle_tp2(symbol: str, tg_token: str, tg_chat: str):
    try:
        sold = sell_ratio(symbol, TP2_RATIO)
        pos  = get_position(symbol)
        tg(tg_token, tg_chat,
           f"🎯 <b>{symbol} TP2 HİT</b>\n"
           f"━━━━━━━━━━━━━━━━━\n"
           f"✅ <b>{sold} lot (%30)</b> satıldı\n"
           f"📦 Kalan: <b>{pos['qty']} lot</b>"
        )
    except Exception as e:
        log.error(f"TP2 [{symbol}]: {e}")
        tg(tg_token, tg_chat, f"❌ <b>{symbol} TP2 hatası</b>\n{e}")

def handle_tp3(symbol: str, tg_token: str, tg_chat: str):
    try:
        sold = sell_ratio(symbol, TP3_RATIO)
        pos  = get_position(symbol)
        tg(tg_token, tg_chat,
           f"🎯 <b>{symbol} TP3 HİT</b>\n"
           f"━━━━━━━━━━━━━━━━━\n"
           f"✅ <b>{sold} lot (%25)</b> satıldı\n"
           f"📦 Kalan: <b>{pos['qty']} lot (trail)</b>"
        )
    except Exception as e:
        log.error(f"TP3 [{symbol}]: {e}")
        tg(tg_token, tg_chat, f"❌ <b>{symbol} TP3 hatası</b>\n{e}")

def handle_stop(symbol: str, tg_token: str, tg_chat: str):
    try:
        pos  = get_position(symbol)
        lot  = int(pos["qty"])
        if lot > 0:
            market_sell(symbol, lot)
        tg(tg_token, tg_chat,
           f"🛑 <b>{symbol} STOP HİT</b>\n"
           f"━━━━━━━━━━━━━━━━━\n"
           f"❌ <b>{lot} lot</b> satıldı, pozisyon kapatıldı"
        )
    except Exception as e:
        log.error(f"STOP [{symbol}]: {e}")
        tg(tg_token, tg_chat, f"❌ <b>{symbol} STOP hatası</b>\n{e}")

# ── Yardımcılar ─────────────────────────────────────────────
def sval(data: dict, *keys, default="") -> str:
    for k in keys:
        v = data.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return str(default)

def fval(data: dict, *keys, default=0.0) -> float:
    for k in keys:
        v = data.get(k)
        if v is not None and v != "":
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return float(default)

# ── Flask ────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/init", methods=["POST"])
def init_login():
    """
    Bot'u başlat. Telegram ve Algolab bilgileri verilir.
    {
      "api_key"   : "API-XXXX",
      "username"  : "TC Kimlik No",
      "password"  : "Denizbank şifre",
      "tg_token"  : "...",
      "tg_chat_id": "..."
    }
    """
    data = request.get_json(force=True)
    api_key  = sval(data, "api_key")
    username = sval(data, "username")
    password = sval(data, "password")
    tg_token = sval(data, "tg_token")
    tg_chat  = sval(data, "tg_chat_id")

    if not all([api_key, username, password, tg_token, tg_chat]):
        return jsonify({"error": "Eksik alan"}), 400

    with session_lock:
        session["tg_token"] = tg_token
        session["tg_chat"]  = tg_chat

    ok = login_step1(api_key, username, password)
    if ok:
        tg(tg_token, tg_chat,
           "📱 <b>Algolab Login</b>\n"
           "Telefonunuza SMS kodu gönderildi.\n"
           "Kodu <code>/sms KODUNUZ</code> şeklinde Telegram'dan gönderin veya\n"
           f"POST /sms endpoint'ine gönderin.")
        return jsonify({"status": "sms_sent"}), 200
    return jsonify({"error": "Login başarısız"}), 500

@app.route("/sms", methods=["POST"])
def sms_confirm():
    """
    SMS kodunu doğrula.
    { "code": "123456" }
    """
    data = request.get_json(force=True)
    code = sval(data, "code")
    if not code:
        return jsonify({"error": "code gerekli"}), 400
    ok = login_step2(code)
    if ok:
        tg_s("✅ <b>Algolab oturumu açıldı!</b>\nBot hazır, webhook'lar aktif.")
        return jsonify({"status": "logged_in"}), 200
    return jsonify({"error": "SMS doğrulama başarısız"}), 401

@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView webhook — Binance bot ile aynı format.
    Ek alan: symbol BIST formatında (örn: THYAO, GARAN)
    Miktar TL olarak: usdt/quantity alanı TL miktarı
    """
    if not session["logged_in"]:
        return jsonify({"error": "Oturum açık değil. /init endpoint'i kullan."}), 401

    try:
        raw_body = request.get_data(as_text=True)
        log.info(f"RAW: {raw_body[:300]}")

        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "Geçersiz JSON"}), 400

        # Webhook secret kontrolü
        expected = os.environ.get("WEBHOOK_SECRET", "")
        if expected and sval(data, "webhookSecret") != expected:
            return jsonify({"error": "Unauthorized"}), 401

        action   = str(data.get("action", data.get("side", ""))).lower()
        symbol   = str(data.get("symbol", data.get("ticker", ""))).upper().replace(".P", "")
        tg_token = sval(data, "tg_token",   "telegramBotToken") or session["tg_token"]
        tg_chat  = sval(data, "tg_chat_id", "telegramChatId")   or session["tg_chat"]

        if not action or not symbol:
            return jsonify({"error": "action ve symbol zorunlu"}), 400

        log.info(f"▶ {action.upper()} | {symbol}")

        if action in ("buy", "long"):
            open_long(
                symbol,
                tl_amount = fval(data, "usdt", "quantity"),
                tp1       = fval(data, "tp1"),
                tp2       = fval(data, "tp2"),
                tp3       = fval(data, "tp3"),
                stop      = fval(data, "stop", "sl"),
                tg_token  = tg_token,
                tg_chat   = tg_chat
            )
        elif action == "tp1":
            handle_tp1(symbol, tg_token, tg_chat)
        elif action == "tp2":
            handle_tp2(symbol, tg_token, tg_chat)
        elif action == "tp3":
            handle_tp3(symbol, tg_token, tg_chat)
        elif action in ("stop", "sell", "close"):
            handle_stop(symbol, tg_token, tg_chat)
        elif action == "trail_update":
            pass  # sadece bilgi, işlem yok
        else:
            return jsonify({"error": f"Bilinmeyen action: {action}"}), 400

        return jsonify({"status": "ok", "action": action, "symbol": symbol}), 200

    except Exception as e:
        log.error(f"Webhook hatası: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "logged_in" : session["logged_in"],
        "platform"  : "heroku",
        "api_key"   : session["api_key"][:15] + "..." if session["api_key"] else "",
    }), 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "market": "BIST"}), 200

if __name__ == "__main__":
    # Ping thread başlat
    Thread(target=ping_loop, daemon=True).start()
    log.info(f"BIST Bot başlatıldı | Port: {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
