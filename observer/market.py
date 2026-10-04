"""Public Binance USD-M futures data only. No account, key or order endpoints."""
import json
import math
import re
import time
import urllib.parse
import urllib.request

BASE_URL = 'https://fapi.binance.com'
INTERVALS = {'1': '1m', '3': '3m', '5': '5m', '15': '15m', '30': '30m',
             '60': '1h', '120': '2h', '240': '4h', 'D': '1d'}


def interval_for(timeframe):
    value = str(timeframe)
    value = INTERVALS.get(value, value)
    if value not in INTERVALS.values():
        raise ValueError('Desteklenmeyen takip periyodu')
    return value


def public_get(path, params):
    if path not in {'/fapi/v2/ticker/price', '/fapi/v1/klines'}:
        raise ValueError('Yalnızca fiyat ve mum verileri kullanılabilir')
    url = BASE_URL + path + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'Botreel-Observer/2'})
    with urllib.request.urlopen(req, timeout=6) as response:
        return json.loads(response.read(512 * 1024))


def rma(values, length):
    seed, last, result = [], None, []
    for value in values:
        if value is not None:
            if last is None:
                seed.append(value)
                if len(seed) == length:
                    last = sum(seed) / length
            else:
                last = (last * (length - 1) + value) / length
        result.append(last)
    return result


def ema(values, length):
    alpha, last = 2 / (length + 1), values[0]
    for value in values[1:]:
        last = alpha * value + (1 - alpha) * last
    return last


def parameter(data, key, default, minimum=2, maximum=200):
    number = data.get(key, default)
    if not isinstance(number, (int, float)) or not math.isfinite(number):
        return default
    return max(minimum, min(maximum, int(number)))


def candle_snapshot(rows, data, now_ms=None):
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    rows = [r for r in rows if int(r[6]) < now_ms]
    if len(rows) < 60:
        raise ValueError('Kapalı mum geçmişi yetersiz')
    opens = [float(r[1]) for r in rows]
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    closes = [float(r[4]) for r in rows]
    volumes = [float(r[5]) for r in rows]
    if not all(math.isfinite(v) and v > 0 for series in (opens, highs, lows, closes) for v in series):
        raise ValueError('Geçersiz fiyat verisi')
    if not all(math.isfinite(v) and v >= 0 for v in volumes):
        raise ValueError('Geçersiz hacim verisi')
    rl = parameter(data, 'rsiLength', 14)
    dl = parameter(data, 'adxLength', 14)
    smooth = parameter(data, 'adxSmoothing', 14)
    al = parameter(data, 'atrLength', 14)
    vl = parameter(data, 'volumeLength', 20)
    bl = parameter(data, 'bbLength', 20)
    breakout_len = parameter(data, 'breakoutLength', 6)
    gains = [None] + [max(closes[i] - closes[i-1], 0) for i in range(1, len(rows))]
    losses = [None] + [max(closes[i-1] - closes[i], 0) for i in range(1, len(rows))]
    gain, loss = rma(gains, rl)[-1], rma(losses, rl)[-1]
    if gain is None or loss is None:
        raise ValueError('RSI geçmişi yetersiz')
    rsi = 50 if gain == loss == 0 else 100 if loss == 0 else 100 - 100 / (1 + gain / loss)
    tr = [highs[0] - lows[0]] + [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]),
          abs(lows[i]-closes[i-1])) for i in range(1, len(rows))]
    plus, minus = [None], [None]
    for i in range(1, len(rows)):
        up, down = highs[i] - highs[i-1], lows[i-1] - lows[i]
        plus.append(up if up > down and up > 0 else 0)
        minus.append(down if down > up and down > 0 else 0)
    tr_s, p_s, m_s = rma(tr, dl), rma(plus, dl), rma(minus, dl)
    dx = []
    for t, p, m in zip(tr_s, p_s, m_s):
        if t is None or p is None or m is None:
            dx.append(None)
        else:
            dx.append(0 if p + m == 0 else 100 * abs(p - m) / (p + m))
    adx = rma(dx, smooth)[-1]
    if adx is None or len(rows) < max(vl, bl, breakout_len) + 1:
        raise ValueError('Gösterge geçmişi yetersiz')
    fast = ema(closes, parameter(data, 'emaFastLength', 9, 1))
    slow = ema(closes, parameter(data, 'emaSlowLength', 21, 1))
    base = sum(closes[-bl:]) / bl
    stdev = math.sqrt(sum((v - base) ** 2 for v in closes[-bl:]) / bl)
    bb_mult = max(0.1, min(5.0, float(data.get('bbMultiplier', 1.5))))
    avg_volume = sum(volumes[-vl:]) / vl
    range_mean = sum(highs[i] - lows[i] for i in range(len(rows)-vl-1, len(rows)-1)) / vl
    prior_high, prior_low = max(highs[-breakout_len-1:-1]), min(lows[-breakout_len-1:-1])
    side = data.get('side', 'LONG')
    return {'price': closes[-1], 'close': closes[-1], 'open': opens[-1],
        'high': highs[-1], 'low': lows[-1], 'barTime': int(rows[-1][0]),
        'barCloseTime': int(rows[-1][6]), 'rsi': rsi, 'adx': adx,
        'atr': rma(tr, al)[-1], 'volumeRatio': volumes[-1] / avg_volume if avg_volume else 0,
        'bbWidthPct': 2 * bb_mult * stdev / base * 100,
        'rangeMult': (highs[-1] - lows[-1]) / range_mean if range_mean else 0,
        'entryMovePct': (closes[-1] - opens[-1]) / opens[-1] * 100,
        'emaFast': fast, 'emaSlow': slow,
        'emaTrend': 'LONG' if fast > slow and closes[-1] > slow else 'SHORT' if fast < slow and closes[-1] < slow else 'FLAT',
        'rangeHigh': prior_high, 'rangeLow': prior_low,
        'breakoutConfirmed': closes[-1] > prior_high if side == 'LONG' else closes[-1] < prior_low,
        'barConfirmed': True, 'dataSource': 'BINANCE_FUTURES_CLOSED_CANDLES'}


def fetch_snapshot(data):
    symbol = data['symbol']
    if not re.fullmatch(r'[A-Z0-9_]{2,40}', symbol) or not symbol.endswith('USDT'):
        raise ValueError('Takip yalnız USD-M USDT futures pariteleri için')
    ticker = public_get('/fapi/v2/ticker/price', {'symbol': symbol})
    if ticker.get('symbol') != symbol:
        raise ValueError('Fiyat sembolü eşleşmiyor')
    price = float(ticker['price'])
    if not math.isfinite(price) or price <= 0:
        raise ValueError('Geçersiz güncel fiyat')
    rows = public_get('/fapi/v1/klines', {'symbol': symbol,
        'interval': interval_for(data['timeframe']), 'limit': 499})
    snapshot = candle_snapshot(rows, data)
    minutes = {'1m':1, '3m':3, '5m':5, '15m':15, '30m':30, '1h':60, '2h':120, '4h':240, '1d':1440}[interval_for(data['timeframe'])]
    if time.time() * 1000 - snapshot['barCloseTime'] > (minutes * 60 + 120) * 1000:
        raise ValueError('Piyasa mum verisi güncel değil')
    snapshot['currentPrice'] = price
    snapshot['observedAt'] = int(time.time() * 1000)
    return snapshot
