#!/usr/bin/env python3
# ============================================================
#  BOT.PY  —  Binance Futures Long + Short Bot
#  Platform   : Heroku
#  TP Sistemi : 25% / 30% / 25% / 20% trail
#  Mod        : Hedge Mode VE One-Way Mode otomatik algılanır
#               Hedge  → positionSide="LONG" / "SHORT" eklenir
#               One-Way→ positionSide gönderilmez
#  Telegram   : Mesajlarda lot yerine USDT tutarı, kapanışta
#               K/Z (USDT) ve güncel kasa bakiyesi gösterilir
#
#  DEĞİŞİKLİK (09.09.2026): Sembol doğrulaması artık HERHANGİ
#  bir canlı Binance API çağrısından ÖNCE yapılıyor. Önceki
#  sürümde get_position() (positionRisk endpoint'i) sembolü
#  doğrulanmadan önce çağrılıyordu, bu da geçersiz semboller
#  için -1121 hatasının Binance'ten canlı olarak dönmesine
#  sebep oluyordu. Artık cache'lenmiş exchangeInfo'ya karşı
#  önceden kontrol ediliyor; geçersizse hiçbir API çağrısı
#  yapılmadan sinyal atlanıyor.
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

PORT = int(os.environ.get("PORT", 5000))

# ── Lot Dağılımı ─────────────────────────────────────────────
TP1_RATIO = 0.50
TP2_RATIO = round(25/50, 6) # 0.5
TP3_RATIO = round(15/25, 6) # 0.6

# ── Binance ─────────────────────────────────────────────────
def get_client(api_key: str, api_secret: str, testnet: bool) -> UMFutures:
    if testnet:
        return UMFutures(
            key=api_key, secret=api_secret,
            base_url="https://testnet.binancefuture.com"
        )
    return UMFutures(key=api_key, secret=api_secret)

# ── Hedge Mode Algılama ──────────────────────────────────────
_hedge_cache: dict = {}

def is_hedge_mode(client: UMFutures, api_key: str) -> bool:
    if api_key in _hedge_cache:
        return _hedge_cache[api_key]
    try:
        result = client.get_position_mode()
        hedge  = result.get("dualSidePosition", False)
        _hedge_cache[api_key] = hedge
        log.info(f"Pozisyon modu: {'HEDGE' if hedge else 'ONE-WAY'} (api_key: ...{api_key[-6:]})")
        return hedge
    except Exception as e:
        log.warning(f"Pozisyon modu sorgulanamadı, ONE-WAY varsayıldı: {e}")
        _hedge_cache[api_key] = False
        return False

# ── Kasa Bakiyesi ─────────────────────────────────────────────
def get_usdt_balance(client: UMFutures) -> float:
    """
    Güncel USDT vadeli işlem cüzdan bakiyesini döndürür.
    Hata durumunda 0.0 döner — çağıran taraf mesajda '—' göstermeli.
    """
    try:
        balances = client.balance()
        for b in balances:
            if b.get("asset") == "USDT":
                return float(b.get("balance", 0))
    except Exception as e:
        log.warning(f"Bakiye sorgulanamadı: {e}")
    return 0.0

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

def fval(data: dict, *keys, default=0.0) -> float:
    for k in keys:
        v = data.get(k)
        if v is not None and v != "":
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return float(default)

def sval(data: dict, *keys, default="") -> str:
    for k in keys:
        v = data.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return str(default)

def parse_signal(data: dict) -> tuple[str, str]:
    action_raw = str(data.get("action", "")).strip().lower()
    side_raw   = str(data.get("side",   "")).strip().lower()

    if side_raw.startswith("short") or action_raw in ("sell", "short"):
        direction = "SHORT"
    else:
        direction = "LONG"

    action_map = {
        "buy"        : "open",
        "sell"       : "open",
        "short"      : "open",
        "tp1"        : "tp1",
        "short_tp1"  : "tp1",
        "tp2"        : "tp2",
        "short_tp2"  : "tp2",
        "tp3"        : "tp3",
        "short_tp3"  : "tp3",
        "stop"       : "stop",
        "short_stop" : "stop",
        "trail_exit" : "stop",
        "trail_update": "trail",
        "take_profit1": "tp1",
        "take_profit2": "tp2",
        "take_profit3": "tp3",
        "close"      : "stop",
    }

    action = action_map.get(side_raw) or action_map.get(action_raw, "")

    log.info(f"parse_signal: action_raw={action_raw} side_raw={side_raw} "
             f"→ action={action} direction={direction}")
    return action, direction

# ── Exchange Cache ───────────────────────────────────────────
_exchange_cache: dict = {}
CACHE_TTL = 300

def get_exchange_info(client: UMFutures, api_key: str,
                      force_refresh: bool = False) -> dict:
    import time
    now    = time.time()
    cached = _exchange_cache.get(api_key)
    if not force_refresh and cached and (now - cached["ts"]) < CACHE_TTL:
        log.info(f"Exchange cache hit ({int(now - cached['ts'])}s)")
        return cached["data"]
    log.info("Exchange info çekiliyor...")
    data = client.exchange_info()
    _exchange_cache[api_key] = {"data": data, "ts": now}
    return data

def _parse_symbol_info(s: dict) -> dict:
    """
    ÖNEMLİ: Bu bot SADECE market-tipi emirler kullanıyor (MARKET,
    STOP_MARKET, TAKE_PROFIT_MARKET). Binance'te bunlar LOT_SIZE
    filtresine değil, ayrı ve genelde çok daha düşük bir maxQty
    taşıyan MARKET_LOT_SIZE filtresine tabidir. Sadece LOT_SIZE'a
    bakmak, gerçekte izin verilenden büyük miktarların hesaplanıp
    Binance tarafından -4005 ile reddedilmesine yol açar. İkisi de
    varsa daha kısıtlayıcı (min max / max min) olanı kullanıyoruz.
    """
    lot_max = lot_min = None
    mkt_max = mkt_min = None
    for f in s.get("filters", []):
        if f["filterType"] == "LOT_SIZE":
            lot_max = float(f["maxQty"])
            lot_min = float(f["minQty"])
        elif f["filterType"] == "MARKET_LOT_SIZE":
            mkt_max = float(f["maxQty"])
            mkt_min = float(f["minQty"])

    candidates_max = [v for v in (lot_max, mkt_max) if v is not None]
    candidates_min = [v for v in (lot_min, mkt_min) if v is not None]
    max_qty = min(candidates_max) if candidates_max else None
    min_qty = max(candidates_min) if candidates_min else None

    return {
        "qty"    : s["quantityPrecision"],
        "prc"    : s["pricePrecision"],
        "max_qty": max_qty,
        "min_qty": min_qty,
        "status" : s.get("status", "UNKNOWN"),
    }

def _find_raw_symbol(client: UMFutures, symbol: str, api_key: str = "") -> dict:
    """exchangeInfo'daki HAM sembol kaydını döndürür (status dahil), yoksa None."""
    info = get_exchange_info(client, api_key)
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            return s
    info = get_exchange_info(client, api_key, force_refresh=True)
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            return s
    return None

def get_symbol_info(client: UMFutures, symbol: str, api_key: str = "") -> dict:
    """
    Sembolün var olduğunu VE durumunun TRADING olduğunu kontrol eder.
    Binance exchangeInfo'da sembol listede olabilir ama status'u
    CLOSE/BREAK/PENDING_TRADING olabilir (delist sürecinde, askıya
    alınmış, henüz açılmamış vb.) — bu durumda pozisyon açma denemesi
    -4140/-4141 gibi hatalarla reddedilir. Sadece "listede var mı"ya
    bakmak yetersiz; status'u da TRADING olmalı.
    """
    raw = _find_raw_symbol(client, symbol, api_key)
    if raw is None:
        raise ValueError(f"Sembol bulunamadı: {symbol}")
    parsed = _parse_symbol_info(raw)
    if parsed["status"] != "TRADING":
        raise RuntimeError(
            f"Sembol '{symbol}' Binance Futures'ta kayıtlı ama şu an "
            f"işlem için kapalı (status: {parsed['status']})"
        )
    return parsed

# ── YENİ: Canlı API çağrısından önce sembolü doğrula ─────────
def check_symbol_tradable(client: UMFutures, symbol: str, api_key: str = "") -> tuple:
    """
    Döndürür: (ok: bool, reason: str, detail: str)
    reason ∈ {"ok", "not_found", "not_trading", "error"}
    Sadece cache'lenmiş exchangeInfo'ya bakar, symbol parametreli
    canlı bir Binance isteği ATMAZ.
    """
    try:
        raw = _find_raw_symbol(client, symbol, api_key)
        if raw is None:
            return False, "not_found", ""
        status = raw.get("status", "UNKNOWN")
        if status != "TRADING":
            return False, "not_trading", status
        return True, "ok", status
    except Exception as e:
        log.warning(f"Sembol doğrulama beklenmeyen hata [{symbol}]: {e}")
        return False, "error", str(e)

def is_valid_symbol(client: UMFutures, symbol: str, api_key: str = "") -> bool:
    """Geriye dönük uyumluluk için basit bool sarmalayıcı."""
    ok, _, _ = check_symbol_tradable(client, symbol, api_key)
    return ok

def find_symbol_suggestions(client: UMFutures, symbol: str, api_key: str = "", limit: int = 5) -> list:
    """
    Sembol tam eşleşmezse, çekirdek coin adını (baştaki '1000' gibi
    çarpan öneklerini ve 'USDT'yi çıkararak) içeren diğer Futures
    sembollerini arar. Binance çoğu zaman çok düşük fiyatlı coinleri
    '1000XUSDT' gibi çarpanlı isimlerle listeler.

    ÖNEMLİ: Bu sadece TEŞHİS içindir — bulunan öneriler otomatik
    olarak İŞLEME SOKULMAZ. Çarpanlı bir kontratta fiyat/miktar
    ölçeği normal coinden 1000x (veya farklı) olabilir; Pine
    tarafındaki TP/SL seviyeleri ham coin fiyatına göre hesaplandığı
    için kör bir sembol değişimi miktar/fiyat hesaplarını tamamen
    yanlış yapıp gerçek parayla yanlış büyüklükte pozisyon açılmasına
    yol açabilir. Bu yüzden bulgu sadece Telegram'a bildirilir; asıl
    düzeltme TradingView alert şablonundaki sembol kaynağını
    (hangi borsa/format) kontrol etmek olmalı.
    """
    import re
    try:
        info = get_exchange_info(client, api_key)
        base = symbol.replace("USDT", "").replace("BUSD", "")
        base_core = re.sub(r'^[0-9]+', '', base)
        if not base_core:
            return []
        return [s["symbol"] for s in info["symbols"]
                if base_core in s["symbol"] and s["symbol"] != symbol][:limit]
    except Exception as e:
        log.warning(f"Sembol önerisi aranamadı [{symbol}]: {e}")
        return []

def floor_qty(val: float, precision: int) -> float:
    f = 10 ** precision
    return math.floor(val * f) / f

def mark_price(client: UMFutures, symbol: str) -> float:
    return float(client.mark_price(symbol=symbol)["markPrice"])

# ── Kaldıraç Bracket Limiti ──────────────────────────────────
def get_max_notional(client: UMFutures, symbol: str, leverage: int) -> float:
    """
    Verilen kaldıraçta izin verilen MAKSİMUM notional'ı döndürür.
    ÖNEMLİ: Binance'in bracket listesi düşük notional/yüksek kaldıraçtan
    yüksek notional/düşük kaldıraca doğru sıralıdır. Kaldıracı destekleyen
    ilk (en küçük) bracket'i değil, kaldıracı destekleyen TÜM bracket'ler
    arasından en büyük cap'i almak gerekir — aksi halde gereksiz yere çok
    düşük bir limit uygulanır (veya hiç eşleşme yoksa hatalı biçimde
    sınırsız/"inf" dönüp limit tamamen devre dışı kalır).
    """
    try:
        brackets = client.leverage_brackets(symbol=symbol)
        if isinstance(brackets, dict):
            brackets = [brackets]
        best_cap = 0.0
        for item in brackets:
            for b in item.get("brackets", []):
                if b.get("initialLeverage", 0) >= leverage:
                    cap = float(b.get("notionalCap", 0))
                    if cap > best_cap:
                        best_cap = cap
        if best_cap > 0:
            # Market emri fiyatı, mark_price'ı çektiğimiz an ile emrin
            # gerçekleşme anı arasında kayabilir (özellikle 10sn scalp +
            # düşük likiditeli altcoinlerde). Cap'in tam sınırında kalmak
            # yerine küçük bir güvenlik payı bırakıyoruz (%3), aksi halde
            # kırpılmış miktar bile sınırı marjinal aşıp -2027 üretebiliyor.
            safe_cap = best_cap * 0.97
            log.info(f"Bracket limiti: {symbol} x{leverage} → ham {best_cap} / "
                     f"güvenlik payıyla {safe_cap:.2f} USDT notional")
            return safe_cap
        # Hiçbir bracket bu kaldıracı desteklemiyor — sembolün izin verdiği
        # tavandan daha yüksek bir kaldıraç istenmiş demektir. Sınırsız
        # (inf) DÖNMÜYORUZ; bunun yerine 0 dönüp çağıran tarafın en düşük
        # bracket'in cap'ine (en güvenli değer) düşmesini sağlıyoruz.
        log.warning(f"{symbol}: x{leverage} kaldıracını destekleyen bracket bulunamadı")
        return 0.0
    except Exception as e:
        log.warning(f"Bracket sorgulanamadı [{symbol}]: {e}")
        return float("inf")

def get_max_leverage(client: UMFutures, symbol: str) -> int:
    """Sembolün Binance Futures'ta izin verdiği maksimum kaldıracı döndürür."""
    try:
        brackets = client.leverage_brackets(symbol=symbol)
        if isinstance(brackets, dict):
            brackets = [brackets]
        max_lev = 0
        for item in brackets:
            for b in item.get("brackets", []):
                lev = int(b.get("initialLeverage", 0))
                if lev > max_lev:
                    max_lev = lev
        return max_lev
    except Exception as e:
        log.warning(f"Max kaldıraç sorgulanamadı [{symbol}]: {e}")
        return 0

# ── Pozisyon Sorgula ────────────────────────────────────────
def get_position(client: UMFutures, symbol: str, direction: str):
    for p in client.get_position_risk(symbol=symbol):
        pos_side = p.get("positionSide", "BOTH")
        amt      = float(p.get("positionAmt", 0))

        if pos_side == direction and abs(amt) > 0:
            return p

        if pos_side == "BOTH":
            if direction == "LONG"  and amt > 0:
                return p
            if direction == "SHORT" and amt < 0:
                return p

    return None

# ── Lot Yardımcısı ──────────────────────────────────────────
def safe_qty(val: float, info: dict) -> float:
    v = floor_qty(val, info["qty"])
    if info["max_qty"] and v > info["max_qty"]:
        v = floor_qty(info["max_qty"], info["qty"])
    if info["min_qty"] and v < info["min_qty"]:
        return 0.0
    return v

# ── Kısmi Kapatma ───────────────────────────────────────────
def market_close_ratio(client: UMFutures, symbol: str,
                       ratio: float, info: dict,
                       direction: str, hedge: bool) -> tuple:
    """Döner: (qty, order_id). Emir başarısız/atlanırsa order_id=None."""
    pos = get_position(client, symbol, direction)
    if not pos:
        log.info(f"Kapatma atlandı: {symbol} {direction} pozisyon yok")
        return 0.0, None

    total = abs(float(pos["positionAmt"]))
    qty   = safe_qty(total * ratio, info)
    if qty <= 0:
        log.warning(f"Kapatma qty küçük: {symbol} qty={qty}")
        return 0.0, None

    close_side = "SELL" if direction == "LONG" else "BUY"
    params = dict(symbol=symbol, side=close_side, type="MARKET", quantity=qty)
    if hedge:
        params["positionSide"] = direction
    else:
        params["reduceOnly"]   = "true"
    try:
        result = client.new_order(**params)
        log.info(f"{direction} kısmi kapat: {symbol} {qty} lot ({ratio*100:.0f}%)")
        return qty, result.get("orderId")
    except Exception as e:
        log.error(f"Kısmi kapatma hatası [{symbol} {direction}]: {e}")
        return 0.0, None

# ── Stop Güncelle ────────────────────────────────────────────
def update_stop_order(client: UMFutures, symbol: str,
                      new_stop: float, info: dict,
                      direction: str, testnet: bool = False,
                      hedge: bool = True):
    if testnet:
        log.info(f"[TESTNET] Stop güncelleme atlandı: {symbol} @ {new_stop}")
        return

    try:
        for o in client.get_orders(symbol=symbol):
            if o.get("status") == "NEW" and o.get("type") == "STOP_MARKET":
                if hedge and o.get("positionSide") != direction:
                    continue
                client.cancel_order(symbol=symbol, orderId=o["orderId"])
                log.info(f"Eski {direction} STOP iptal: {o['orderId']}")
    except Exception as e:
        log.warning(f"Stop iptali [{symbol} {direction}]: {e}")

    pos = get_position(client, symbol, direction)
    if not pos:
        return

    try:
        pp         = info["prc"]
        qty        = abs(float(pos["positionAmt"]))
        close_side = "SELL" if direction == "LONG" else "BUY"

        params = dict(
            symbol=symbol, side=close_side, type="STOP_MARKET",
            stopPrice=round(new_stop, pp),
            quantity=qty, timeInForce="GTE_GTC"
        )
        if hedge:
            params["positionSide"] = direction
        else:
            params["reduceOnly"] = "true"

        client.new_order(**params)
        log.info(f"Yeni {direction} STOP: {symbol} @ {round(new_stop, pp)}")
    except Exception as e:
        log.error(f"Stop koyulamadı [{symbol} {direction}]: {e}")

# ── K/Z Hesaplama ─────────────────────────────────────────────
def calc_pnl_usdt(entry_price: float, exit_price: float,
                  qty: float, direction: str) -> float:
    """
    YEDEK/TAHMİNİ hesap — SADECE Binance'in gerçek kayıtları
    okunamazsa (API hatası vb.) son çare olarak kullanılır.
    Basit (çıkış - giriş) × miktar; komisyon/fonlama dahil DEĞİLDİR,
    kısmi doluşları da yansıtmaz. Asıl kaynak get_order_realized_pnl.
    """
    if entry_price <= 0 or exit_price <= 0 or qty <= 0:
        return 0.0
    diff = (exit_price - entry_price) if direction == "LONG" else (entry_price - exit_price)
    return diff * qty

def get_order_realized_pnl(client: UMFutures, symbol: str, order_id):
    """
    Bir emrin GERÇEK gerçekleşen K/Z'sini Binance'in kendi userTrades
    (fill) kayıtlarından okur. Bu, Binance'in Transaction History'de
    gösterdiği rakamla birebir eşleşir — kısmi doluşlar, birden fazla
    fiyat seviyesinden gerçekleşme, komisyon vb. hiçbir şey client
    tarafında yeniden hesaplanmaz/tahmin edilmez; Binance'in kendi
    muhasebesi olduğu gibi okunur.
    Emrin fill kayıtları henüz Binance tarafında işlenmemiş olabilir
    diye kısa bir gecikmeyle tek retry yapılır. Bulunamazsa None döner
    (çağıran taraf o zaman tahmini hesaba düşer).
    """
    if not order_id:
        return None
    import time as _time
    for attempt in range(2):
        try:
            trades = client.get_account_trades(symbol=symbol, orderId=order_id)
            if trades:
                pnl = sum(float(t.get("realizedPnl", 0)) for t in trades)
                log.info(f"Gerçek K/Z (Binance kayıtlarından) [{symbol} order={order_id}]: {pnl}")
                return pnl
            if attempt == 0:
                _time.sleep(0.5)
        except Exception as e:
            log.warning(f"Order realized PNL okunamadı [{symbol} order={order_id}]: {e}")
            return None
    return None

def fmt_pnl(pnl: float) -> str:
    sign = "🟢+" if pnl >= 0 else "🔴"
    return f"{sign}{round(pnl, 2)} USDT"

# ── Bilinen Binance hata kodları için okunur teşhis ──────────
_ERROR_DIAGNOSES = {
    "-1121": "Sembol Binance Futures'ta yok (spot'ta olabilir, ya da "
             "yanlış formatlanmış).\n",
    "-2027": "Bu kaldıraçta izin verilen maksimum pozisyon büyüklüğü "
             "aşıldı (bracket limiti). Kaldıracı düşürmeyi veya işlem "
             "miktarını küçültmeyi düşün.\n",
    "-4005": "Miktar, sembolün market emirleri için izin verdiği "
             "maksimumu aşıyor (MARKET_LOT_SIZE).\n",
    "-4164": "Notional, sembolün minimum emir büyüklüğünün altında "
             "kaldı.\n",
    "-2019": "Marjin yetersiz — bakiye bu pozisyonu karşılamıyor.\n",
}

def _diagnose_error(err_str: str) -> str:
    for code, msg in _ERROR_DIAGNOSES.items():
        if code in err_str:
            return f"💡 {msg}"
    return ""

# ════════════════════════════════════════════════════════════
#  LONG / SHORT AÇ
# ════════════════════════════════════════════════════════════
def open_position(client, token, chat, testnet, api_key,
                  symbol, usdt, leverage, tp1, tp2, tp3, stop,
                  direction: str, hedge: bool, suggested_qty: float = 0.0):
    emoji = "🟢" if direction == "LONG" else "🔴"
    mode_label = "Hedge Mode" if hedge else "One-Way Mode"
    try:
        # Not: sembol burada zaten webhook() içinde önceden doğrulandı.
        if get_position(client, symbol, direction):
            tg(token, chat,
               f"⚠️ <b>{symbol}</b>\nAçık {direction} var, sinyal atlandı.")
            return

        # ── Kaldıracı ayarla; reddedilirse SESSİZCE GEÇME — sembolün
        # gerçekten desteklediği maksimum kaldıraca düş ve kullanıcıyı
        # bilgilendir. Aksi halde kod, Binance'in aslında uygulamadığı
        # yüksek bir kaldıraçla notional hesaplamaya devam eder ve
        # bracket limiti (get_max_notional) de aynı yanlış kaldıraçla
        # sorgulandığı için hiçbir üst sınır uygulanmamış olur — tam
        # olarak -2027 hatasına yol açan zincir budur.
        effective_leverage = leverage
        try:
            client.change_leverage(symbol=symbol, leverage=leverage)
        except ClientError as e:
            max_lev = get_max_leverage(client, symbol)
            effective_leverage = max_lev if max_lev > 0 else leverage
            log.warning(
                f"{symbol}: x{leverage} kaldıraç reddedildi ({e}); "
                f"x{effective_leverage} kullanılacak"
            )
            tg(token, chat,
               f"⚠️ <b>{symbol}</b>\n"
               f"x{leverage} kaldıraç bu sembolde desteklenmiyor.\n"
               f"Sembolün izin verdiği maksimum olan x{effective_leverage} "
               f"kullanılıyor.")

        info     = get_symbol_info(client, symbol, api_key)
        price    = mark_price(client, symbol)

        sizing_method = "sabit_usdt"
        if suggested_qty and suggested_qty > 0:
            qty           = floor_qty(suggested_qty, info["qty"])
            notional      = qty * price
            sizing_method = "atr_risk_bazli"
            log.info(f"Risk-bazlı boyutlandırma kullanıldı: "
                     f"{symbol} suggestedQty={suggested_qty} → qty={qty}")
        else:
            notional = usdt * effective_leverage
            qty      = floor_qty(notional / price, info["qty"])

        max_notional = get_max_notional(client, symbol, effective_leverage)
        if notional > max_notional:
            old_notional = notional
            notional     = max_notional
            qty          = floor_qty(notional / price, info["qty"])
            log.warning(
                f"{symbol} bracket limiti aşıldı: "
                f"{old_notional} → {notional} USDT (x{effective_leverage} max)"
            )
            tg(token, chat,
               f"⚠️ <b>{symbol}</b> bracket limiti\n"
               f"x{effective_leverage} kaldıraçta max <b>{notional:.0f} USDT</b> notional\n"
               f"Miktar otomatik düşürüldü.")

        log.info(f"{direction} | {symbol} | sizing={sizing_method} | "
                 f"notional={round(notional,2)} USDT | fiyat={price} | lot={qty}")

        if qty <= 0:
            raise ValueError(f"Lot sıfır — fiyat:{price} notional:{notional}")
        if info["max_qty"] and qty > info["max_qty"]:
            qty = floor_qty(info["max_qty"], info["qty"])
        if info["min_qty"] and qty < info["min_qty"]:
            raise ValueError(f"Min lot altında: {qty} < {info['min_qty']}")

        pp          = info["prc"]
        q           = info["qty"]
        entry_side  = "BUY"  if direction == "LONG" else "SELL"
        close_side  = "SELL" if direction == "LONG" else "BUY"

        # ── Giriş emri: -2027 (bracket/leverage) veya -4005 (max qty)
        # gibi miktar-kaynaklı Binance reddi alırsak, bunun sebebi
        # genelde client tarafında tam öngöremediğimiz şeylerdir
        # (mark_price ile gerçek eşleşme fiyatı arasındaki kayma,
        # ondalık yuvarlama, ya da hesap genelindeki agregat marjin
        # durumu). Böyle bir ret alırsak miktarı kademeli küçültüp
        # birkaç kez tekrar deniyoruz; her ihtimalde tamamen
        # başarısız olup sinyali kaçırmaktansa daha küçük de olsa
        # pozisyonu açmayı tercih ediyoruz.
        max_retries = 3
        last_err = None
        opened = False
        for attempt in range(max_retries):
            entry_params = dict(symbol=symbol, side=entry_side,
                                type="MARKET", quantity=qty)
            if hedge:
                entry_params["positionSide"] = direction
            try:
                client.new_order(**entry_params)
                opened = True
                break
            except ClientError as e:
                err_str = str(e)
                if "-2027" in err_str or "-4005" in err_str:
                    last_err = e
                    qty = floor_qty(qty * 0.75, q)
                    log.warning(
                        f"{symbol} giriş reddedildi ({err_str[:60]}), "
                        f"deneme {attempt+1}/{max_retries}, yeni qty={qty}"
                    )
                    if info["min_qty"] and qty < info["min_qty"]:
                        log.warning(f"{symbol}: küçültme min lot altına düştü, vazgeçiliyor")
                        break
                    continue
                raise

        if not opened:
            raise ValueError(
                f"Giriş emri {max_retries} denemede de reddedildi "
                f"(son hata: {last_err}). Muhtemel sebep: bracket/kaldıraç "
                f"limiti veya sembolün max emir miktarı."
            )

        notional = qty * price
        log.info(f"{direction} açıldı: {symbol} {qty} lot x{effective_leverage}")

        qty_tp1       = safe_qty(qty * TP1_RATIO, info)
        qty_after_tp1 = floor_qty(qty - qty_tp1, q)
        qty_tp2       = safe_qty(qty_after_tp1 * TP2_RATIO, info)
        qty_after_tp2 = floor_qty(qty_after_tp1 - qty_tp2, q)
        qty_tp3       = safe_qty(qty_after_tp2 * TP3_RATIO, info)
        qty_trail     = floor_qty(qty_after_tp2 - qty_tp3, q)

        # ── USDT karşılıkları (mesajda lot yerine bunlar gösterilir) ──
        usdt_tp1   = round(qty_tp1   * price, 2)
        usdt_tp2   = round(qty_tp2   * price, 2)
        usdt_tp3   = round(qty_tp3   * price, 2)
        usdt_trail = round(qty_trail * price, 2)

        if testnet:
            log.info(f"[TESTNET] TP emirleri atlandı: {symbol}")
            if stop > 0:
                log.info(f"[TESTNET] STOP atlandı: {symbol} @ {stop}")
        else:
            for tp_price, tp_qty, tp_name in [
                (tp1, qty_tp1, "TP1"),
                (tp2, qty_tp2, "TP2"),
                (tp3, qty_tp3, "TP3"),
            ]:
                if tp_price > 0 and tp_qty > 0:
                    try:
                        tp_params = dict(
                            symbol=symbol, side=close_side,
                            type="TAKE_PROFIT_MARKET",
                            stopPrice=round(tp_price, pp),
                            quantity=tp_qty, timeInForce="GTE_GTC"
                        )
                        if hedge:
                            tp_params["positionSide"] = direction
                        else:
                            tp_params["reduceOnly"] = "true"
                        client.new_order(**tp_params)
                    except Exception as e:
                        log.error(f"{tp_name} emri [{symbol} {direction}]: {e}")

            if stop > 0:
                try:
                    stop_params = dict(
                        symbol=symbol, side=close_side, type="STOP_MARKET",
                        stopPrice=round(stop, pp),
                        quantity=qty, timeInForce="GTE_GTC"
                    )
                    if hedge:
                        stop_params["positionSide"] = direction
                    else:
                        stop_params["reduceOnly"] = "true"
                    client.new_order(**stop_params)
                except Exception as e:
                    log.error(f"İlk STOP emri [{symbol} {direction}]: {e}")

        sizing_note = ("📐 ATR/Risk bazlı" if sizing_method == "atr_risk_bazli"
                       else "💰 Sabit teminat")

        # ── Gerçek teminat/likidasyon bilgisi — TAHMİN DEĞİL, doğrudan
        # Binance'in pozisyon risk verisinden okunuyor. "İşlem
        # Büyüklüğü" kaldıraçlı TOPLAM pozisyon değeridir (notional);
        # cepten/teminattan giden gerçek miktar bunun kaldıraca
        # bölünmüş halidir ve likidasyon riski o rakama göre
        # değerlendirilmelidir — bu yüzden ikisini birbirinden
        # net ayırarak gösteriyoruz.
        real_pos = get_position(client, symbol, direction)
        margin_line = ""
        liq_line = ""
        if real_pos:
            real_notional = abs(float(real_pos.get("notional", notional)))
            liq_price     = float(real_pos.get("liquidationPrice", 0) or 0)
            margin_type   = real_pos.get("marginType", "")
            if margin_type == "isolated":
                real_margin = abs(float(real_pos.get("isolatedMargin", 0) or 0))
                margin_line = f"💳 Kullanılan Teminat: <b>{round(real_margin, 2)} USDT</b> (isolated, gerçek)\n"
            else:
                approx_margin = real_notional / effective_leverage if effective_leverage else 0
                margin_line = f"💳 Kullanılan Teminat: <b>≈{round(approx_margin, 2)} USDT</b> (cross, yaklaşık)\n"
            if liq_price > 0:
                liq_line = f"⚠️ Likidasyon Fiyatı: <b>{liq_price}</b>\n"

        tg(token, chat,
           f"{emoji} <b>{symbol} {direction} AÇILDI</b> [{mode_label}]\n"
           f"━━━━━━━━━━━━━━━━━\n"
           f"{sizing_note}\n"
           f"⚡ Kaldıraç: <b>x{effective_leverage}</b>\n"
           f"📊 İşlem Büyüklüğü (notional, kaldıraçlı): <b>{round(notional, 2)} USDT</b>\n"
           f"{margin_line}"
           f"{liq_line}"
           f"💵 Giriş   : <b>{price}</b>\n"
           f"━━━━━━━━━━━━━━━━━\n"
           f"🎯 TP1 : <b>{tp1}</b>  → ~{usdt_tp1} USDT (%25)\n"
           f"🎯 TP2 : <b>{tp2}</b>  → ~{usdt_tp2} USDT (%30)\n"
           f"🎯 TP3 : <b>{tp3}</b>  → ~{usdt_tp3} USDT (%25)\n"
           f"🔄 Trail: <b>~{usdt_trail} USDT (%20)</b>\n"
           f"🛑 Stop : <b>{stop}</b>\n"
           f"{'🔴 TESTNET' if testnet else '🟢 GERÇEK HESAP'}"
        )

    except ValueError as e:
        log.error(f"open_position [{symbol} {direction}]: {e}")
        tg(token, chat,
           f"❌ <b>{symbol} {direction} açılamadı</b>\n🔍 {e}")
    except Exception as e:
        log.error(f"open_position [{symbol} {direction}]: {e}")
        tg(token, chat,
           f"❌ <b>{symbol} {direction} açılamadı</b>\n"
           f"{_diagnose_error(str(e))}"
           f"🔍 {e}")

# ════════════════════════════════════════════════════════════
#  TP1 / TP2 / TP3
# ════════════════════════════════════════════════════════════
def handle_tp(client, token, chat, symbol, tp_num: int,
              new_stop: float, direction: str, testnet: bool,
              ratio: float, pct_label: str, hedge: bool,
              entry_price: float = 0.0, exit_price: float = 0.0):
    pos = get_position(client, symbol, direction)
    if not pos:
        tg(token, chat,
           f"⚠️ <b>{symbol} TP{tp_num}</b> — {direction} pozisyon bulunamadı")
        return

    info = get_symbol_info(client, symbol)
    sold, order_id = market_close_ratio(client, symbol, ratio, info, direction, hedge)

    if new_stop > 0:
        update_stop_order(client, symbol, new_stop, info, direction, testnet, hedge)

    pos_after = get_position(client, symbol, direction)
    rem = abs(float(pos_after["positionAmt"])) if pos_after else 0

    # ── K/Z (bu kısmi kapatma için) + güncel kasa ──────────────
    # Önce Binance'in KENDİ fill kayıtlarından gerçek K/Z okunur;
    # sadece bu okunamazsa (API hatası vb.) tahmini hesaba düşülür.
    real_pnl = get_order_realized_pnl(client, symbol, order_id)
    if real_pnl is not None:
        pnl_usdt = real_pnl
    else:
        px_for_pnl = exit_price if exit_price > 0 else mark_price(client, symbol)
        pnl_usdt   = calc_pnl_usdt(entry_price, px_for_pnl, sold, direction)
        log.warning(f"{symbol} TP{tp_num}: gerçek K/Z okunamadı, tahmini değer kullanıldı")

    px_for_display = exit_price if exit_price > 0 else mark_price(client, symbol)
    usdt_sold  = round(sold * px_for_display, 2)
    balance    = get_usdt_balance(client)

    tg(token, chat,
       f"🎯 <b>{symbol} {direction} TP{tp_num} HİT</b>\n"
       f"━━━━━━━━━━━━━━━━━\n"
       f"✅ <b>~{usdt_sold} USDT ({pct_label})</b> kapatıldı\n"
       f"💰 Bu Kapatma K/Z: <b>{fmt_pnl(pnl_usdt)}</b>\n"
       f"🔒 Stop güncellendi: <b>{new_stop}</b>\n"
       f"🏦 Güncel Kasa: <b>{round(balance, 2)} USDT</b>"
    )

# ════════════════════════════════════════════════════════════
#  STOP / TRAIL EXIT
# ════════════════════════════════════════════════════════════
def handle_stop(client, token, chat, symbol, direction: str, hedge: bool,
                entry_price: float = 0.0, exit_price: float = 0.0):
    close_side = "SELL" if direction == "LONG" else "BUY"
    closed_qty = 0.0
    close_order_id = None

    pos = get_position(client, symbol, direction)

    if not pos:
        # ── Pozisyon zaten kapalı: muhtemelen Binance'in kendi
        # STOP_MARKET/TAKE_PROFIT_MARKET emri fiyata anlık dokunup
        # pozisyonu ÇOKTAN kapatmış, ama Pine'ın bar-kapanışı bazlı
        # kontrolü aynı koşulu bir sonraki barda da true görüp
        # GEÇ/MÜKERRER bir "stop" sinyali daha göndermiş. Bu durumda
        # yeni bir kapatma denemesi YAPMIYORUZ (zaten yapacak bir şey
        # yok) ve yanıltıcı "STOP HİT, K/Z: 0" mesajı basmıyoruz —
        # sadece geride kalmış olabilecek (artık karşılığı olmayan)
        # TP/STOP emirlerini temizliyoruz.
        cancelled = 0
        try:
            for o in client.get_orders(symbol=symbol):
                if o.get("status") == "NEW" and \
                   o.get("type") in ("TAKE_PROFIT_MARKET", "STOP_MARKET"):
                    if hedge and o.get("positionSide") != direction:
                        continue
                    client.cancel_order(symbol=symbol, orderId=o["orderId"])
                    cancelled += 1
        except Exception as e:
            log.warning(f"Geç sinyal temizliği [{symbol} {direction}]: {e}")
        log.info(f"{symbol} {direction}: pozisyon zaten kapalıydı, "
                 f"geç/mükerrer stop sinyali atlandı ({cancelled} bekleyen emir temizlendi)")
        return

    try:
        closed_qty = abs(float(pos["positionAmt"]))
        stop_params = dict(symbol=symbol, side=close_side,
                           type="MARKET", quantity=closed_qty)
        if hedge:
            stop_params["positionSide"] = direction
        else:
            stop_params["reduceOnly"] = "true"
        result = client.new_order(**stop_params)
        close_order_id = result.get("orderId")
        log.info(f"{direction} STOP: {symbol} {closed_qty} lot kapatıldı")
    except Exception as e:
        log.warning(f"{direction} kapama [{symbol}]: {e}")

    cancelled = 0
    try:
        for o in client.get_orders(symbol=symbol):
            if o.get("status") == "NEW" and \
               o.get("type") in ("TAKE_PROFIT_MARKET", "STOP_MARKET"):
                if hedge and o.get("positionSide") != direction:
                    continue
                client.cancel_order(symbol=symbol, orderId=o["orderId"])
                cancelled += 1
    except Exception as e:
        log.warning(f"Emir iptal [{symbol} {direction}]: {e}")

    # ── K/Z (kalan pozisyonun kapanışı) + güncel kasa ──────────
    # Önce Binance'in KENDİ fill kayıtlarından gerçek K/Z okunur;
    # sadece bu okunamazsa tahmini hesaba düşülür.
    real_pnl = get_order_realized_pnl(client, symbol, close_order_id)
    if real_pnl is not None:
        pnl_usdt = real_pnl
    else:
        px_for_pnl = exit_price if exit_price > 0 else mark_price(client, symbol)
        pnl_usdt   = calc_pnl_usdt(entry_price, px_for_pnl, closed_qty, direction)
        log.warning(f"{symbol} STOP: gerçek K/Z okunamadı, tahmini değer kullanıldı")
    balance    = get_usdt_balance(client)

    extra = f"\n🔧 {cancelled} emir iptal edildi" if cancelled else ""
    tg(token, chat,
       f"🛑 <b>{symbol} {direction} STOP HİT</b>\n"
       f"━━━━━━━━━━━━━━━━━\n"
       f"❌ Tüm {direction} pozisyon kapatıldı{extra}\n"
       f"💰 Bu Kapatma K/Z: <b>{fmt_pnl(pnl_usdt)}</b>\n"
       f"🏦 Güncel Kasa: <b>{round(balance, 2)} USDT</b>"
    )

# ════════════════════════════════════════════════════════════
#  FLASK
# ════════════════════════════════════════════════════════════
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw_body = request.get_data(as_text=True)
        log.info(f"RAW: {raw_body[:500]}")

        data = request.get_json(force=True, silent=True)
        if not data:
            log.error(f"JSON okunamadı: {raw_body[:300]}")
            return jsonify({"error": "Geçersiz JSON"}), 400

        expected = os.environ.get("WEBHOOK_SECRET", "")
        incoming = sval(data, "webhookSecret", "webhook_secret")
        if expected and incoming != expected:
            log.warning("Geçersiz webhook secret!")
            return jsonify({"error": "Unauthorized"}), 401

        api_key    = sval(data, "api_key",    "binanceApiKey")
        api_secret = sval(data, "api_secret", "binanceSecretKey")
        tg_token   = sval(data, "tg_token",   "telegramBotToken")
        tg_chat    = sval(data, "tg_chat_id", "telegramChatId")
        symbol     = clean_symbol(sval(data, "symbol", "ticker"))
        testnet    = sval(data, "testnet", default="true").lower() == "true"

        missing = []
        if not symbol:     missing.append("symbol/ticker")
        if not api_key:    missing.append("api_key/binanceApiKey")
        if not api_secret: missing.append("api_secret/binanceSecretKey")
        if not tg_token:   missing.append("tg_token/telegramBotToken")
        if not tg_chat:    missing.append("tg_chat_id/telegramChatId")
        if missing:
            return jsonify({"error": f"Eksik: {missing}"}), 400

        action, direction = parse_signal(data)
        if not action:
            return jsonify({"error": "Bilinmeyen action/side"}), 400

        log.info(f"▶ {direction} {action.upper()} | {symbol} | testnet={testnet}")
        client = get_client(api_key, api_secret, testnet)

        # ── Sembolü, herhangi bir canlı emir çağrısından ÖNCE doğrula ──
        # Sadece "isim listede var mı" değil, sembolün status'unun da
        # TRADING olduğunu kontrol ediyoruz (bkz. check_symbol_tradable).
        # MAVIAUSDT vakası: sembol exchangeInfo'da vardı ama status'u
        # TRADING değildi (kapalı/delist sürecinde) — bu kontrol
        # olmadan her API çağrısı ayrı ayrı -4140/-4141/-4028 ile patladı.
        ok, reason, detail = check_symbol_tradable(client, symbol, api_key)
        if not ok:
            if reason == "not_trading":
                log.warning(f"Sembol kapalı, sinyal atlandı: {symbol} (status={detail})")
                msg = (f"⚠️ <b>{symbol}</b>\nBinance Futures'ta kayıtlı ama şu an "
                       f"işlem için KAPALI (status: <b>{detail}</b>) — askıya "
                       f"alınmış veya delist sürecinde olabilir. Sinyal atlandı, "
                       f"bu durum bot tarafında düzeltilemez.")
                tg(tg_token, tg_chat, msg)
                return jsonify({"status": "skipped", "reason": "symbol_not_trading",
                                "symbol": symbol, "binance_status": detail}), 200
            elif reason == "not_found":
                suggestions = find_symbol_suggestions(client, symbol, api_key)
                log.warning(f"Geçersiz sembol, sinyal atlandı: {symbol} | öneriler: {suggestions}")
                if suggestions:
                    sugg_txt = ", ".join(suggestions)
                    msg = (f"⚠️ <b>{symbol}</b>\nBinance Futures'ta bu isimle yok.\n"
                           f"🔎 Yakın eşleşme(ler): <b>{sugg_txt}</b>\n"
                           f"Bunlardan biri gerçek sembol olabilir (örn. çarpanlı "
                           f"'1000X' kontrat) — ama fiyat/miktar ölçeği farklı "
                           f"olabileceğinden otomatik geçiş YAPILMADI. TradingView "
                           f"alert şablonundaki sembol kaynağını kontrol et.")
                else:
                    msg = (f"⚠️ <b>{symbol}</b>\nBinance Futures'ta bu sembol bulunamadı, "
                           f"yakın bir eşleşme de yok. Muhtemelen bu coin Futures'ta "
                           f"hiç listeli değil (sadece spot'ta olabilir).")
                tg(tg_token, tg_chat, msg)
                return jsonify({"status": "skipped", "reason": "invalid_symbol",
                                "symbol": symbol, "suggestions": suggestions}), 200
            else:
                # reason == "error": exchangeInfo sorgusu başarısız oldu —
                # sembolün gerçekten geçersiz olduğunu KANITLAYAMADIK, o
                # yüzden sinyali atlamak yerine devam ediyoruz; asıl emir
                # denemesi kendi hata mesajını üretecek.
                log.warning(f"Sembol doğrulaması yapılamadı [{symbol}]: {detail}, devam ediliyor")



        hedge = is_hedge_mode(client, api_key)

        if action == "open":
            open_position(
                client, tg_token, tg_chat, testnet, api_key, symbol,
                usdt          = fval(data, "usdt", "quantity"),
                leverage      = int(fval(data, "leverage", default=1)),
                tp1           = fval(data, "tp1"),
                tp2           = fval(data, "tp2"),
                tp3           = fval(data, "tp3"),
                stop          = fval(data, "stop", "sl", "exitPrice", "stopPrice"),
                direction     = direction,
                hedge         = hedge,
                suggested_qty = fval(data, "suggestedQty", default=0.0)
            )

        elif action == "tp1":
            handle_tp(client, tg_token, tg_chat, symbol,
                      tp_num      = 1,
                      new_stop    = fval(data, "new_stop"),
                      direction   = direction,
                      testnet     = testnet,
                      ratio       = TP1_RATIO,
                      pct_label   = "%25",
                      hedge       = hedge,
                      entry_price = fval(data, "entryPrice"),
                      exit_price  = fval(data, "tp1"))

        elif action == "tp2":
            handle_tp(client, tg_token, tg_chat, symbol,
                      tp_num      = 2,
                      new_stop    = fval(data, "new_stop"),
                      direction   = direction,
                      testnet     = testnet,
                      ratio       = TP2_RATIO,
                      pct_label   = "%30",
                      hedge       = hedge,
                      entry_price = fval(data, "entryPrice"),
                      exit_price  = fval(data, "tp2"))

        elif action == "tp3":
            handle_tp(client, tg_token, tg_chat, symbol,
                      tp_num      = 3,
                      new_stop    = fval(data, "new_stop"),
                      direction   = direction,
                      testnet     = testnet,
                      ratio       = TP3_RATIO,
                      pct_label   = "%25",
                      hedge       = hedge,
                      entry_price = fval(data, "entryPrice"),
                      exit_price  = fval(data, "tp3"))

        elif action == "stop":
            handle_stop(client, tg_token, tg_chat, symbol, direction, hedge,
                        entry_price = fval(data, "entryPrice"),
                        exit_price  = fval(data, "exitPrice", "stopPrice"))

        elif action == "trail":
            log.info(f"Trail bilgi: {symbol} {direction} @ {fval(data, 'new_stop')}")

        else:
            return jsonify({"error": f"Bilinmeyen action: {action}"}), 400

        return jsonify({
            "status"   : "ok",
            "action"   : action,
            "direction": direction,
            "symbol"   : symbol
        }), 200

    except Exception as e:
        err_str = str(e)
        if "-1121" in err_str:
            log.warning(f"Geçersiz sembol atlandı (yedek yakalama): {err_str[:80]}")
            return jsonify({"status": "skipped", "reason": "invalid_symbol"}), 200
        log.error(f"Webhook hatası: {e}")
        return jsonify({"error": err_str}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status"  : "running",
        "mode"    : "LONG+SHORT",
        "hedge"   : True,
        "platform": "heroku"
    }), 200

if __name__ == "__main__":
    log.info(f"Long+Short Bot başlatıldı | Port: {PORT} | Hedge Mode: ON")
    app.run(host="0.0.0.0", port=PORT, debug=False)
