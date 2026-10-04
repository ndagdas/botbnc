#!/usr/bin/env python3
"""Observation-only TradingView signal filter. This app never submits exchange orders."""

import hashlib
import hmac
import json
import logging
import os
import queue
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone

from flask import Flask, jsonify, request

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("signal_observer")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

# A strict allowlist means API keys or arbitrary webhook text never reach the
# scoring model, logs, database, or Telegram.
TEXT_FIELDS = {
    "signalId", "strategyVersion", "timeframe", "interval", "marketRegime",
    "regime", "action", "side", "symbol", "ticker", "trend", "emaTrend",
    "btcTrend", "breakout", "barConfirmed", "direction",
}
NUMBER_FIELDS = {
    "signalTime", "barTime", "entryPrice", "price", "close", "high", "low",
    "volumeRatio", "bbWidth", "rangeMult", "rsi", "adx", "atr", "pumpScore",
    "score", "entryMovePct", "candlePct", "signalCandlePct",
    "riskReward", "rr", "rangeHigh", "rangeLow", "emaFast", "emaSlow",
}
BOOL_FIELDS = {"breakoutConfirmed", "volumeConfirmed", "barConfirmed", "confirmed"}
SYMBOL_RE = re.compile(r"^[A-Z0-9_]{2,40}$")
SIGNAL_QUEUE = queue.Queue(maxsize=int(os.getenv("QUEUE_MAX", "500")))
_seen = {}
_seen_lock = threading.Lock()
_worker_started = False
_worker_lock = threading.Lock()


def _number(value):
    try:
        n = float(value)
        return n if n == n and abs(n) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _http_post_json(url, headers, payload, timeout):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_signal(raw):
    if not isinstance(raw, dict):
        raise ValueError("JSON nesnesi gerekli")
    data = {k: str(v).strip()[:100] for k, v in raw.items()
            if k in TEXT_FIELDS and v is not None}
    for key in NUMBER_FIELDS:
        if raw.get(key) not in (None, ""):
            number = _number(raw[key])
            if number is None:
                raise ValueError(f"Geçersiz sayı: {key}")
            data[key] = number
    for key in BOOL_FIELDS:
        if key in raw:
            value = raw[key]
            if isinstance(value, bool):
                data[key] = value
            elif str(value).lower() in {"true", "1", "yes"}:
                data[key] = True
            elif str(value).lower() in {"false", "0", "no"}:
                data[key] = False
            else:
                raise ValueError(f"Geçersiz boolean: {key}")

    symbol = data.get("symbol", data.get("ticker", "")).upper()
    symbol = symbol.removesuffix(".P")
    if ":" in symbol:
        symbol = symbol.rsplit(":", 1)[-1]
    if not SYMBOL_RE.fullmatch(symbol):
        raise ValueError("symbol/ticker geçersiz")
    data["symbol"] = symbol

    raw_side = (data.get("side") or data.get("direction") or data.get("action") or "").upper()
    if raw_side in {"BUY", "LONG", "OPEN_LONG", "LONG_ENTRY"}:
        data["side"] = "LONG"
    elif raw_side in {"SELL", "SHORT", "OPEN_SHORT", "SHORT_ENTRY"}:
        data["side"] = "SHORT"
    else:
        raise ValueError("side/action LONG veya SHORT olmalı")
    data["timeframe"] = data.get("timeframe", data.get("interval", "belirtilmedi"))
    return data


def deterministic_review(data):
    """Transparent baseline ranking: no market data is invented or fetched."""
    side = data["side"]
    score = 50
    reasons = []
    hard_reject = []

    # Avoid entering after the move is already extended. The threshold is
    # configurable so the first observation period can collect evidence.
    late_move = next((data[k] for k in ("entryMovePct", "signalCandlePct", "candlePct")
                      if k in data), None)
    max_move = float(os.getenv("MAX_ENTRY_CANDLE_PCT", "4.0"))
    if late_move is not None:
        if abs(late_move) >= max_move * 2:
            hard_reject.append(f"mum hareketi geç giriş eşiğini aştı (%{late_move:.2f})")
        elif abs(late_move) > max_move:
            score -= 20
            reasons.append(f"mum hareketi yüksek (%{late_move:.2f})")
        else:
            score += 5
            reasons.append("mum uzaması sınırlı")

    vr = data.get("volumeRatio")
    if vr is not None:
        if vr >= 2:
            score += 18
            reasons.append(f"hacim artışı güçlü ({vr:.2f}x)")
        elif vr >= 1.3:
            score += 10
            reasons.append(f"hacim artışı var ({vr:.2f}x)")
        elif vr < 0.8:
            score -= 15
            reasons.append(f"hacim zayıf ({vr:.2f}x)")
        else:
            reasons.append("hacim teyidi sınırlı")
    else:
        reasons.append("hacim oranı verisi yok")

    rsi = data.get("rsi")
    if rsi is not None:
        if side == "LONG":
            if 52 <= rsi <= 70:
                score += 12
                reasons.append(f"RSI yönü destekliyor ({rsi:.1f})")
            elif rsi > 78:
                score -= 15
                reasons.append(f"RSI aşırı yüksek ({rsi:.1f})")
            elif rsi < 45:
                score -= 8
                reasons.append(f"RSI momentum teyidi zayıf ({rsi:.1f})")
        else:
            if 30 <= rsi <= 48:
                score += 12
                reasons.append(f"RSI yönü destekliyor ({rsi:.1f})")
            elif rsi < 22:
                score -= 15
                reasons.append(f"RSI aşırı düşük ({rsi:.1f})")
            elif rsi > 55:
                score -= 8
                reasons.append(f"RSI momentum teyidi zayıf ({rsi:.1f})")

    adx = data.get("adx")
    if adx is not None:
        if adx >= 22:
            score += 10
            reasons.append(f"ADX trend gücü yeterli ({adx:.1f})")
        elif adx < 12:
            score -= 10
            reasons.append(f"ADX düşük, piyasa yatay olabilir ({adx:.1f})")

    range_mult = data.get("rangeMult")
    if range_mult is not None:
        if range_mult >= 1.2:
            score += 10
            reasons.append(f"mum aralığı genişliyor ({range_mult:.2f}x)")
        elif range_mult < 0.8:
            score -= 5
            reasons.append("mum aralığı dar; kırılım teyidi eksik")

    breakout = data.get("breakoutConfirmed", data.get("breakout"))
    if isinstance(breakout, bool):
        if breakout:
            score += 15
            reasons.append("bant kırılımı teyitli")
        else:
            score -= 10
            reasons.append("bant kırılımı henüz teyitli değil")

    closed_bar = data.get("barConfirmed", data.get("confirmed"))
    if closed_bar is False:
        score -= 20
        reasons.append("mum kapanışı teyit edilmedi; bekle")

    rr = data.get("riskReward", data.get("rr"))
    if rr is not None:
        if rr < 1:
            hard_reject.append(f"risk/getiri oranı yetersiz ({rr:.2f})")
        elif rr >= 1.5:
            score += 10
            reasons.append(f"risk/getiri uygun ({rr:.2f})")
        else:
            reasons.append(f"risk/getiri sınırlı ({rr:.2f})")

    trend = (data.get("btcTrend") or data.get("emaTrend") or data.get("trend") or "").upper()
    if trend:
        if trend in {side, "BULLISH" if side == "LONG" else "BEARISH", "UP" if side == "LONG" else "DOWN"}:
            score += 8
            reasons.append("trend yönü sinyalle uyumlu")
        elif trend in {"LONG" if side == "SHORT" else "SHORT", "BULLISH" if side == "SHORT" else "BEARISH",
                       "UP" if side == "SHORT" else "DOWN"}:
            score -= 12
            reasons.append("trend yönü sinyale ters")

    score = max(0, min(100, score))
    if hard_reject or score < 45:
        decision = "RED"
    elif score >= 70:
        decision = "AL"
    else:
        decision = "İZLE"
    if closed_bar is False and decision == "AL":
        decision = "İZLE"
    return {"decision": decision, "score": score, "reasons": reasons,
            "hard_reject": hard_reject, "reviewer": "rules"}


def ai_review(data, baseline):
    """Optional JSON-only second opinion; no tools, orders, or account access."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if os.getenv("AI_REVIEW_ENABLED", "false").lower() != "true" or not api_key:
        return baseline
    # Deterministic safety gates always win; the model can only lower a decision.
    payload = {k: v for k, v in data.items() if k in TEXT_FIELDS | NUMBER_FIELDS | BOOL_FIELDS}
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "decision": {"type": "string", "enum": ["AL", "İZLE", "RED"]},
            "score": {"type": "integer", "minimum": 0, "maximum": 100},
            "reason": {"type": "string"},
        },
        "required": ["decision", "score", "reason"],
    }
    prompt = (
        "Gelen TradingView teknik sinyalini yalnızca verilen alanlara dayanarak değerlendir. "
        "Veri yoksa uydurma. Kaldıraç, getiri garantisi veya emir önerisi verme. "
        "AL yalnızca güçlü ve teyitli sinyal için, belirsizlikte İZLE, zayıf/geç kalmış sinyalde RED seç. "
        f"Ön puan: {baseline['score']}; ön karar: {baseline['decision']}; "
        f"ön gerekçeler: {baseline['reasons']}\nSinyal JSON: {json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        body = _http_post_json(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload={"model": os.getenv("OPENAI_MODEL", "gpt-5-mini"), "store": False,
                  "input": prompt,
                  "text": {"format": {"type": "json_schema", "name": "signal_review",
                                        "strict": True, "schema": schema}}},
            timeout=12,
        )
        output = "".join(part.get("text", "") for item in body.get("output", [])
                         for part in item.get("content", []) if part.get("type") == "output_text")
        result = json.loads(output)
        if result["decision"] not in {"AL", "İZLE", "RED"}:
            return baseline
        if baseline["hard_reject"]:
            result["decision"] = "RED"
            result["score"] = min(25, result["score"])
        elif {"AL": 2, "İZLE": 1, "RED": 0}[result["decision"]] > {"AL": 2, "İZLE": 1, "RED": 0}[baseline["decision"]]:
            result["decision"] = baseline["decision"]
            result["score"] = min(result["score"], baseline["score"])
        return {**baseline, "decision": result["decision"], "score": result["score"],
                "reasons": baseline["reasons"] + ["AI: " + result["reason"][:180]], "reviewer": "rules+ai"}
    except Exception as exc:
        log.warning("AI değerlendirmesi yapılamadı; kural puanlaması kullanılıyor (%s)", type(exc).__name__)
        return baseline


def review_signal(data):
    baseline = deterministic_review(data)
    return ai_review(data, baseline)


def telegram_message(data, review):
    title = {"AL": "🟢 AL adayı", "İZLE": "🟡 İZLE", "RED": "🔴 RED"}[review["decision"]]
    price = data.get("price", data.get("close", data.get("entryPrice")))
    lines = ["📡 GÖZLEM MODU — EMİR GÖNDERİLMEDİ", title,
             f"{data['symbol']} · {data['side']} · {data['timeframe']}",
             f"Puan: {review['score']}/100"]
    if price is not None:
        lines.append(f"Fiyat: {price:g}")
    lines.extend("• " + reason for reason in review["reasons"][:6])
    if review["hard_reject"]:
        lines.extend("⛔ " + reason for reason in review["hard_reject"])
    lines.append("İzleme değerlendirmesidir; otomatik işlem yapılmaz.")
    return "\n".join(lines)


def send_telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID gerekli")
    response = _http_post_json(f"https://api.telegram.org/bot{token}/sendMessage",
                               {"Content-Type": "application/json"},
                               {"chat_id": chat_id, "text": message}, timeout=10)
    if not response.get("ok"):
        raise RuntimeError("Telegram mesajı kabul etmedi")


def process_signal(data):
    review = review_signal(data)
    send_telegram(telegram_message(data, review))
    return review


def _worker():
    while True:
        data = SIGNAL_QUEUE.get()
        try:
            process_signal(data)
        except Exception:
            log.exception("Sinyal değerlendirme/Telegram teslim hatası")
        finally:
            SIGNAL_QUEUE.task_done()


def start_worker():
    global _worker_started
    with _worker_lock:
        if not _worker_started:
            threading.Thread(target=_worker, name="telegram-observer", daemon=True).start()
            _worker_started = True


def _is_duplicate(data):
    now = time.time()
    raw_id = data.get("signalId") or "|".join(str(data.get(k, "")) for k in
                                              ("symbol", "side", "timeframe", "barTime", "signalTime", "price", "close"))
    key = hashlib.sha256(raw_id.encode()).hexdigest()
    ttl = int(os.getenv("DEDUP_TTL_SECONDS", "180"))
    with _seen_lock:
        for old_key, timestamp in list(_seen.items()):
            if now - timestamp > ttl:
                _seen.pop(old_key, None)
        if key in _seen:
            return True
        if len(_seen) >= 5000:
            _seen.pop(next(iter(_seen)))
        _seen[key] = now
    return False


@app.get("/health")
def health():
    return jsonify(status="running", mode="observation_only", binance_orders_enabled=False,
                   ai_review_enabled=os.getenv("AI_REVIEW_ENABLED", "false").lower() == "true",
                   queue_depth=SIGNAL_QUEUE.qsize()), 200


@app.post("/webhook")
@app.post("/monitor/webhook")
def webhook():
    expected = os.getenv("WEBHOOK_SECRET", "")
    raw = request.get_json(silent=True)
    supplied = str((raw or {}).get("webhookSecret", (raw or {}).get("webhook_secret", ""))) if isinstance(raw, dict) else ""
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        return jsonify(error="Unauthorized"), 401
    try:
        data = normalize_signal(raw)
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400
    if _is_duplicate(data):
        return jsonify(status="duplicate", mode="observation_only"), 200
    try:
        SIGNAL_QUEUE.put_nowait(data)
    except queue.Full:
        return jsonify(error="Gözlem kuyruğu dolu; sinyal kabul edilmedi"), 429
    start_worker()
    return jsonify(status="accepted", mode="observation_only", binance_orders_enabled=False), 202


if __name__ == "__main__":
    start_worker()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
