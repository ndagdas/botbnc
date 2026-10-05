"""Price follow-up for WATCH decisions and a retryable Telegram outbox."""
import hashlib
import os
import time
from .market import fetch_snapshot, interval_for


def watch_seconds():
    return max(30, int(os.getenv('WATCH_INTERVAL_SECONDS', '60')))


def notify(store, conn, event_id, message):
    store.enqueue(conn, 'notify:' + event_id, 'notify', {'message': message})


def start_watch(store, conn, key, signal, review):
    if os.getenv('WATCH_ENABLED', 'true').lower() != 'true':
        return 'Fiyat takibi kapalı.'
    if signal.get('exchange', 'BINANCE').upper() != 'BINANCE' or not signal['symbol'].endswith('USDT'):
        return 'Bu kaynak/parite için Binance Futures fiyat takibi desteklenmiyor.'
    try:
        interval_for(signal['timeframe'])
    except ValueError:
        return 'Bu periyot için fiyat takibi desteklenmiyor.'
    reference = signal.get('price', signal.get('close', signal.get('entryPrice', 0)))
    if reference <= 0:
        return 'Referans fiyat eksik; takip başlatılamadı.'
    existing = store.active_watches(conn)
    if any(w['signal']['symbol'] == signal['symbol'] and w['signal']['side'] == signal['side']
           and w['signal']['timeframe'] == signal['timeframe'] for w in existing):
        return 'Bu paritenin önceki İZLE kaydı takip ediliyor; ilk referans korunuyor.'
    if len(existing) >= int(os.getenv('MAX_ACTIVE_WATCHES', '50')):
        return 'Aktif takip sınırı dolu; yeni takip başlatılamadı.'
    now = time.time()
    ttl = max(15, int(os.getenv('WATCH_TTL_MINUTES', '120')))
    state = {'signal': signal, 'reference': reference, 'started': now, 'expires': now + ttl * 60,
        'lastReviewBar': signal.get('barTime', 0), 'lastNotification': now,
        'latestPrice': reference, 'bestMovePct': 0.0, 'worstMovePct': 0.0,
        'marketFailures': 0, 'review': review}
    store.enqueue(conn, 'watch:' + key, 'watch', state, now + watch_seconds())
    return f'Fiyat takibi başladı: {watch_seconds()} sn kontrol, en çok {ttl} dk.'


def move_pct(price, reference, side):
    return (price / reference - 1) * 100 * (1 if side == 'LONG' else -1)


def followup_message(state, decision, reason):
    signal = state['signal']
    elapsed = int((time.time() - state['started']) / 60)
    movement = move_pct(state['latestPrice'], state['reference'], signal['side'])
    return ('📡 GÖZLEM — İZLE TAKİP GÜNCELLEMESİ\n'
        f"{signal['symbol']} · {signal['side']} · {signal['timeframe']}\n"
        f'Karar: {decision}\n'
        f"İlk fiyat: {state['reference']:g} · Güncel: {state['latestPrice']:g}\n"
        f'Yön bazında değişim: %{movement:+.2f} · Süre: {elapsed} dk\n'
        f"Örneklenen en iyi/en kötü: %{state['bestMovePct']:+.2f} / %{state['worstMovePct']:+.2f}\n"
        f'{reason}\nEmir gönderilmedi; değişim, gerçekleşmiş işlem getirisi değildir.')


def watch_step(state, snapshot, reviewer, now=None):
    """Return updated state, terminal status and optional event notification."""
    now = time.time() if now is None else now
    signal = state['signal']
    state = dict(state)
    state['latestPrice'] = snapshot['currentPrice']
    state['marketFailures'] = 0
    movement = move_pct(state['latestPrice'], state['reference'], signal['side'])
    state['bestMovePct'] = max(state['bestMovePct'], movement)
    state['worstMovePct'] = min(state['worstMovePct'], movement)
    stop = signal.get('stop', signal.get('sl'))
    target = signal.get('tp1')
    long = signal['side'] == 'LONG'
    reason, decision = None, None
    if stop is not None and (state['latestPrice'] <= stop if long else state['latestPrice'] >= stop):
        decision, reason = 'RED', 'İlk sinyalin stop/geçersizlik seviyesi aşıldı.'
    # Only use OHLC bars wholly after watch creation, never a wick from before the signal.
    elif stop is not None and snapshot['barTime'] >= state['started'] * 1000 and (
            snapshot['low'] <= stop if long else snapshot['high'] >= stop):
        decision, reason = 'RED', 'Takip başladıktan sonraki kapalı mum stop bölgesine dokundu.'
    elif movement >= float(os.getenv('MAX_WATCH_CHASE_PCT', '4.0')):
        decision, reason = 'RED', 'İlk sinyalden sonra hareket uzadı; fiyat kovalanmıyor.'
    elif target is not None and (state['latestPrice'] >= target if long else state['latestPrice'] <= target):
        decision, reason = 'RED', 'İlk hedefe girişten önce ulaşıldı; eski sinyal geç kaldı.'
    elif now >= state['expires']:
        decision, reason = 'SÜRE DOLDU', 'Takip süresi içinde yeterli giriş teyidi oluşmadı.'
    elif snapshot['barTime'] > state['lastReviewBar']:
        # Recalculate on a newly closed bar; do not relabel original data as current.
        current = {k: v for k, v in signal.items() if k.endswith('Length') or k in {
            'adxSmoothing', 'bbMultiplier', 'symbol', 'side', 'timeframe', 'strategyVersion', 'signalId'}}
        current.update(snapshot)
        current['price'] = state['latestPrice']
        current['signalAgeMinutes'] = (now - state['started']) / 60
        current['followupMovePct'] = movement
        if stop is not None and target is not None:
            risk = state['latestPrice'] - stop if long else stop - state['latestPrice']
            reward = target - state['latestPrice'] if long else state['latestPrice'] - target
            if risk > 0:
                current['riskReward'] = max(0, reward / risk)
        review = reviewer(current)
        state['review'] = review
        state['lastReviewBar'] = snapshot['barTime']
        if review['decision'] != 'İZLE':
            decision = 'AL ADAYI' if review['decision'] == 'AL' else 'RED'
            reason = f"Yeni kapalı mum puanı: {review['score']}/100. " + '; '.join(review['reasons'][-4:])
    if decision is not None:
        state['outcome'] = decision
        return state, True, followup_message(state, decision, reason)
    if now - state['lastNotification'] >= max(5, int(os.getenv('WATCH_NOTIFY_MINUTES', '15'))) * 60:
        state['lastNotification'] = now
        return state, False, followup_message(state, 'İZLE', 'Yeni teyit bekleniyor; fiyat takibi sürüyor.')
    return state, False, None


def process_job(store, job, reviewer, message_builder, telegram_sender):
    payload, key = job['payload'], job['id']
    now = time.time()
    if job['kind'] == 'review':
        review = reviewer(payload)
        message = message_builder(payload, review)
        with store.connection() as conn:
            if review['decision'] == 'AL':
                candidate = store.reserve_candidate(conn, key, payload['symbol'])
                if candidate not in {'accepted', 'duplicate'}:
                    store.finish(conn, key, payload, result={**review, 'delivery': 'suppressed', 'limit_reason': candidate})
                    return
            if review['decision'] == 'İZLE':
                message += '\n' + start_watch(store, conn, key, payload, review)
            notify(store, conn, key, message)
            store.finish(conn, key, payload, result=review)
    elif job['kind'] == 'notify':
        telegram_sender(payload['message'])
        with store.connection() as conn:
            store.finish(conn, key, payload)
    elif job['kind'] == 'watch':
        if now >= payload['expires']:
            updated = dict(payload)
            updated['outcome'] = 'SÜRE DOLDU'
            terminal, message = True, followup_message(updated, 'SÜRE DOLDU', 'Takip süresi doldu; son örneklenen fiyat gösteriliyor.')
        else:
            snapshot = fetch_snapshot(payload['signal'])
            updated, terminal, message = watch_step(payload, snapshot, reviewer, now)
        with store.connection() as conn:
            if terminal and updated.get('outcome') == 'AL ADAYI':
                candidate = store.reserve_candidate(conn, key, updated['signal']['symbol'])
                if candidate not in {'accepted', 'duplicate'}:
                    updated['outcome'] = 'ADAY SINIRI'
                    updated['limitReason'] = candidate
                    message = None
            if message:
                tag = hashlib.sha256(message.encode()).hexdigest()[:16]
                notify(store, conn, key + ':' + tag, message)
            store.finish(conn, key, updated, state='done' if terminal else 'pending',
                         due=now + watch_seconds(), result=updated.get('outcome'))


def retry_job(store, job, error_type):
    payload = dict(job['payload'])
    with store.connection() as conn:
        if job['kind'] == 'watch':
            payload['marketFailures'] = payload.get('marketFailures', 0) + 1
            if payload['marketFailures'] == 3:
                notify(store, conn, job['id'] + ':market-outage', followup_message(payload, 'İZLE',
                    'Piyasa verisine erişilemiyor. Son fiyat gösteriliyor; yeni AL kararı verilmiyor.'))
            store.finish(conn, job['id'], payload, state='pending', due=time.time() + watch_seconds())
        else:
            failed = job['attempts'] >= 8
            store.finish(conn, job['id'], payload, state='failed' if failed else 'pending',
                         due=time.time() + min(300, 2 ** job['attempts']),
                         result={'error_type': error_type})
