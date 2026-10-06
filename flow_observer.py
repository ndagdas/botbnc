#!/usr/bin/env python3
"""Public-data research observer. GET-only Binance; never places orders."""
import argparse
import csv
import html
import io
import json
import math
import os
import sqlite3
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path

HOUR, MINUTE, DAY = 3600000, 60000, 86400000
VERSION = 'flow-observer-1.0'
SCHEMA = '''CREATE TABLE IF NOT EXISTS flow_records (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
 updated DOUBLE PRECISION NOT NULL)'''


def now_ms():
    return int(time.time() * 1000)


def number(value):
    x = float(value)
    if not math.isfinite(x):
        raise ValueError('Sonlu sayı gerekli')
    return x


class AccessBlocked(RuntimeError):
    pass


class PublicMarket:
    ALLOWED = {
        'futures': {'/fapi/v1/time', '/fapi/v1/exchangeInfo', '/fapi/v1/klines',
                    '/fapi/v1/premiumIndex', '/futures/data/openInterestHist'},
        'spot': {'/api/v3/exchangeInfo', '/api/v3/klines'},
    }
    BASE = {'futures': 'https://fapi.binance.com', 'spot': 'https://api.binance.com'}

    def __init__(self):
        self.lock = threading.Lock()
        self.next_request = 0.0
        self.blocked = set()

    def get(self, market, path, params=None):
        if path not in self.ALLOWED.get(market, set()):
            raise ValueError('Bu gözlemcide hesap veya emir uç noktası yok')
        if market in self.blocked:
            raise AccessBlocked(f'{market}: erişim engelli; otomatik yeniden deneme yok')
        with self.lock:
            slot = max(time.monotonic(), self.next_request)
            self.next_request = slot + 0.30
        time.sleep(max(0, slot - time.monotonic()))
        url = self.BASE[market] + path
        if params:
            url += '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={'User-Agent': VERSION}, method='GET')
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                return json.loads(r.read(8 * 1024 * 1024))
        except urllib.error.HTTPError as e:
            if e.code in (403, 418, 429, 451):
                self.blocked.add(market)
                raise AccessBlocked(f'{market}: HTTP {e.code}; tarama durduruldu') from None
            raise RuntimeError(f'{market}: HTTP {e.code}') from None


class Store:
    def __init__(self, path='flow.sqlite3', url=None):
        self.path = path
        self.url = url or os.getenv('FLOW_DATABASE_URL')
        if os.getenv('DYNO') and not self.url:
            raise RuntimeError('Heroku takibi için kalıcı FLOW_DATABASE_URL gerekli')
        if not self.url:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            self.execute(c, SCHEMA)

    @contextmanager
    def connection(self):
        if self.url:
            import psycopg
            c = psycopg.connect(self.url, connect_timeout=8)
        else:
            c = sqlite3.connect(self.path, timeout=20)
            c.execute('PRAGMA journal_mode=WAL')
        try:
            with c:
                yield c
        finally:
            c.close()

    def execute(self, c, sql, args=()):
        return c.execute(sql.replace('?', '%s') if self.url else sql, args)

    def put(self, key, kind, payload):
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        with self.connection() as c:
            self.execute(c, '''INSERT INTO flow_records VALUES (?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated=excluded.updated''',
                (key, kind, encoded, time.time()))

    def get(self, key):
        with self.connection() as c:
            r = self.execute(c, 'SELECT payload FROM flow_records WHERE id=?', (key,)).fetchone()
        return json.loads(r[0]) if r else None

    def records(self, kind):
        with self.connection() as c:
            rows = self.execute(c, 'SELECT payload FROM flow_records WHERE kind=? ORDER BY updated DESC', (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]


def normalize_rows(rows, asof, step=HOUR):
    """Reject duplicate/gapped/corrupt candles; use only fully closed bars."""
    result = []
    for raw in rows:
        if len(raw) < 11:
            raise ValueError('Gerçek işlem tutarı alanları eksik')
        start, end = int(raw[0]), int(raw[6])
        if end >= asof:
            continue
        if end != start + step - 1:
            raise ValueError('Mum zamanı/periyodu uyuşmuyor')
        if result and start != result[-1]['time'] + step:
            raise ValueError('Mum verisinde boşluk veya tekrar var')
        o, hi, lo, close = [number(raw[i]) for i in (1, 2, 3, 4)]
        quote, buy = number(raw[7]), number(raw[10])
        if not (0 < lo <= min(o, close) <= max(o, close) <= hi):
            raise ValueError('Geçersiz OHLC')
        if not (quote >= 0 and 0 <= buy <= quote * 1.000001):
            raise ValueError('Alış/toplam işlem tutarı geçersiz')
        result.append({'time': start, 'end': end, 'open': o, 'high': hi,
                       'low': lo, 'close': close, 'quote': quote, 'buy': min(buy, quote)})
    return result


def flow_window(rows):
    quote = sum(r['quote'] for r in rows)
    buy = sum(r['buy'] for r in rows)
    sell = quote - buy
    return {'quote_usdt': quote, 'taker_buy_usdt': buy, 'taker_sell_usdt': sell,
            'delta_usdt': buy - sell, 'buy_share': buy / quote if quote else None,
            'return_pct': (rows[-1]['close'] / rows[0]['open'] - 1) * 100}


def features(raw, asof):
    rows = normalize_rows(raw, asof)
    if len(rows) < 24:
        raise ValueError('24 saatlik kapalı mum geçmişi yok')
    if rows[-1]['end'] != asof - 1:
        raise ValueError('Son kapalı saat eksik; eski veri kullanılmadı')
    daily = flow_window(rows[-24:])
    weekly = flow_window(rows[-168:]) if len(rows) >= 168 else None
    prior = rows[-192:-24]
    daily_volumes = [sum(r['quote'] for r in prior[i:i+24]) for i in range(0, len(prior)-23, 24)]
    baseline = statistics.median(daily_volumes) if daily_volumes else None
    daily['volume_ratio'] = daily['quote_usdt'] / baseline if baseline and baseline > 0 else None
    days = [rows[i:i+24] for i in range(len(rows)-168, len(rows)-23, 24)] if weekly else []
    weekly_positive = sum(flow_window(d)['delta_usdt'] > 0 for d in days) if days else None
    result = {'asof': asof, 'closed_hours': len(rows), 'daily': daily,
              'weekly': weekly, 'positive_days_7d': weekly_positive,
              'last_closed_price': rows[-1]['close'], 'complete_week': weekly is not None,
              'baseline_days': len(daily_volumes)}
    return result


def universe(info):
    eligible, excluded = [], []
    for s in info['symbols']:
        if s.get('quoteAsset') == 'USDT' and s.get('contractType') == 'PERPETUAL' and s.get('status') == 'TRADING':
            # Include every active USDT perpetual in the report; tag non-crypto.
            eligible.append(s)
        else:
            excluded.append({'symbol': s['symbol'], 'status': s.get('status'),
                             'contractType': s.get('contractType'), 'quoteAsset': s.get('quoteAsset')})
    return eligible, excluded


def spot_match(future, spot):
    base = future.get('baseAsset', '')
    exact = next((s for s in spot if s.get('baseAsset') == base), None)
    if exact:
        return exact['symbol'], 1
    # Explicit 1000-token futures denomination, not fuzzy ticker matching.
    if base.startswith('1000'):
        item = next((s for s in spot if s.get('baseAsset') == base[4:]), None)
        if item:
            return item['symbol'], 1000
    return None, None


def rank_rows(rows):
    """An exploratory score, explicitly not a calibrated probability."""
    for row in rows:
        if row.get('status') != 'OK':
            row['score'] = None
            row['classification'] = 'VERİ EKSİK'
            continue
        spot = row.get('spot')
        source = spot or row['futures']
        d, w = source['daily'], source['weekly']
        reasons, score = [], 0
        if d['delta_usdt'] > 0:
            score += 20
            reasons.append('Son 24 saatte agresif alış farkı pozitif')
        if w and w['delta_usdt'] > 0:
            score += 20
            reasons.append('Son 7 günde agresif alış farkı pozitif')
        if (source['positive_days_7d'] or 0) >= 4:
            score += 15
            reasons.append('7 günlük pencerenin en az 4 gününde alış baskısı')
        if (d['volume_ratio'] or 0) >= 1.5:
            score += 15
            reasons.append('24 saatlik işlem tutarı önceki günlerin medyanından yüksek')
        if 0 < d['return_pct'] < 10:
            score += 10
            reasons.append('Günlük fiyat yükseliyor; hareket henüz %10 altında')
        if w and w['return_pct'] > 0:
            score += 10
            reasons.append('Haftalık fiyat yönü yukarı')
        if spot:
            score += 10
            reasons.append('Spot gerçekleşmiş işlem verisi mevcut')
        if d['return_pct'] >= 20:
            reasons.append('Günlük hareket zaten %20 üzerinde; geç kalma riski')
        row.update(score=score, reasons=reasons,
                   score_source='SPOT' if spot else 'FUTURES_ONLY',
                   classification='GÜÇLÜ TAKİP' if spot and score >= 70 else 'TAKİP' if score >= 40 else 'ZAYIF')
    # No top-N truncation, no hiding poor candidates.
    return sorted(rows, key=lambda r: (r.get('score') is not None, r.get('score') or -1), reverse=True)


def evaluate_minutes(state, raw, asof):
    """Exact aligned 24h OHLC follow-up; ambiguous intra-bar ordering is retained."""
    state = dict(state)
    rows = normalize_rows(raw, asof, MINUTE)
    due = state['cursor']
    for r in rows:
        if r['time'] < due or r['time'] >= state['deadline']:
            continue
        if r['time'] != due:
            raise ValueError('Takipte mum boşluğu: sonuç tamamlanmış sayılmadı')
        if state.get('reference') is None:
            state['reference'] = r['open']
        ref = state['reference']
        high, low = (r['high'] / ref - 1) * 100, (r['low'] / ref - 1) * 100
        state['max_up_pct'] = max(state['max_up_pct'], high)
        state['max_down_pct'] = min(state['max_down_pct'], low)
        pump, stop = high >= state['pump_pct'], low <= -state['stop_pct']
        if pump and state.get('first_pump_bar') is None:
            state['first_pump_bar'] = r['time']
            state['drawdown_before_pump_pct'] = min(state.get('prior_drawdown_pct', 0), low)
        if stop and state.get('first_stop_bar') is None:
            state['first_stop_bar'] = r['time']
        if not state.get('first_event') and (pump or stop):
            state['first_event'] = 'SAME_BAR_UNCERTAIN' if pump and stop else 'PUMP_FIRST' if pump else 'STOP_FIRST'
        state['prior_drawdown_pct'] = state['max_down_pct']
        state['last_price'] = r['close']
        due += MINUTE
    state['cursor'] = due
    state['complete'] = due >= state['deadline']
    state['pump_hit'] = state.get('first_pump_bar') is not None
    state['tracking_status'] = 'TAMAMLANDI' if state['complete'] else 'TAKİPTE' if state.get('reference') else 'BAŞLANGIÇ BEKLENİYOR'
    return state


class Observer:
    def __init__(self, store, market=None):
        self.store, self.market = store, market or PublicMarket()

    def health(self, status, message=None):
        value = {'status': status, 'message': message, 'updated': now_ms(), 'version': VERSION,
                 'orders_enabled': False, 'universe_limit': None,
                 'sources': {'binance': status, 'news': 'NOT_CONNECTED',
                             'whales': 'NOT_CONNECTED', 'groups': 'NOT_CONNECTED'},
                 'score_is_probability': False}
        self.store.put('health', 'health', value)
        return value

    def scan(self):
        self.health('SCANNING')
        try:
            clock = self.market.get('futures', '/fapi/v1/time')['serverTime']
            asof = int(clock) // HOUR * HOUR
            eligible, excluded = universe(self.market.get('futures', '/fapi/v1/exchangeInfo'))
        except Exception as e:
            self.health('BLOCKED' if isinstance(e, AccessBlocked) else 'ERROR', str(e))
            return None
        run_id = str(now_ms())
        run = {'id': run_id, 'asof': asof, 'created': now_ms(), 'status': 'SCANNING',
               'universe_count': len(eligible), 'excluded': excluded, 'rows': [],
               'windows': 'Son kapalı saat itibarıyla 24 saat / 7 gün; takvim mumu değil',
               'score_is_probability': False}
        self.store.put('run:' + run_id, 'run', run)
        spot_error, spot_symbols = None, []
        try:
            spot_symbols = [s for s in self.market.get('spot', '/api/v3/exchangeInfo')['symbols']
                            if s.get('status') == 'TRADING' and s.get('quoteAsset') == 'USDT']
        except Exception as e:
            spot_error = str(e)
        try:
            premiums = self.market.get('futures', '/fapi/v1/premiumIndex')
            premiums = {s['symbol']: s for s in premiums}
        except Exception:
            premiums = {}

        def one(s):
            sym = s['symbol']
            row = {'symbol': sym, 'underlying_type': s.get('underlyingType'),
                   'observed_at': now_ms(), 'status': 'OK', 'spot': None,
                   'news': None, 'whales': None, 'groups': None}
            try:
                raw = self.market.get('futures', '/fapi/v1/klines',
                                      {'symbol': sym, 'interval': '1h', 'endTime': asof - 1, 'limit': 400})
                row['futures'] = features(raw, asof)
            except Exception as e:
                row.update(status='ERROR', error=str(e))
                return row
            ss, mult = spot_match(s, spot_symbols)
            row['spot_symbol'], row['spot_multiplier'] = ss, mult
            row['spot_status'] = 'UNAVAILABLE' if spot_error else 'NO_PAIR' if not ss else 'OK'
            if spot_error:
                row['spot_error'] = spot_error
            if ss:
                try:
                    raw = self.market.get('spot', '/api/v3/klines',
                                          {'symbol': ss, 'interval': '1h', 'endTime': asof-1, 'limit': 400})
                    row['spot'] = features(raw, asof)
                except Exception as e:
                    row.update(spot_status='ERROR', spot_error=str(e))
            try:
                oi = self.market.get('futures', '/futures/data/openInterestHist',
                                     {'symbol': sym, 'period': '1h', 'endTime': asof-1, 'limit': 180})
                if not oi or int(oi[-1]['timestamp']) < asof - 2*HOUR:
                    raise ValueError('OI verisi eksik veya eski')
                row['open_interest'] = {'unit': 'base_asset', 'latest': number(oi[-1]['sumOpenInterest'])}
                for hours in (24, 168):
                    # Use timestamps, not row count; incomplete history stays null.
                    prior = [x for x in oi if int(x['timestamp']) <= int(oi[-1]['timestamp'])-hours*HOUR]
                    base = number(prior[-1]['sumOpenInterest']) if prior else 0
                    row['open_interest'][f'change_{hours}h_pct'] = (row['open_interest']['latest']/base-1)*100 if base > 0 else None
            except Exception as e:
                row['oi_error'] = str(e)
            p = premiums.get(sym)
            row['funding_rate'] = number(p['lastFundingRate']) if p else None
            row['observed_at'] = now_ms()
            return row

        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [pool.submit(one, s) for s in eligible]
            for f in as_completed(jobs):
                run['rows'].append(f.result())
                if len(run['rows']) % 25 == 0:
                    self.store.put('run:' + run_id, 'run', run)
        run['rows'] = rank_rows(run['rows'])
        run['finished'] = now_ms()
        start = (run['finished'] // MINUTE + 1) * MINUTE
        run['tracking_start'] = start
        run['tracking_reference_rule'] = 'Tarama sonrası ilk tam 1dk mumun açılışı; varsayımsal referans, emir değil'
        run['status'] = 'COMPLETE' if all(r['status'] == 'OK' for r in run['rows']) else 'PARTIAL'
        self.store.put('run:' + run_id, 'run', run)
        self.store.put('latest', 'latest', run)
        for row in run['rows']:
            if row['status'] != 'OK':
                continue
            key = run_id + ':' + row['symbol']
            self.store.put('watch:' + key, 'watch', {
                'id': key, 'run_id': run_id, 'symbol': row['symbol'], 'score': row['score'],
                'start': start, 'deadline': start + DAY, 'cursor': start, 'reference': None,
                'pump_pct': float(os.getenv('FLOW_PUMP_PCT', '20')),
                'stop_pct': float(os.getenv('FLOW_RESEARCH_STOP_PCT', '3')),
                'max_up_pct': 0, 'max_down_pct': 0, 'complete': False,
                'tracking_status': 'BAŞLANGIÇ BEKLENİYOR'})
        ok = sum(r['status'] == 'OK' for r in run['rows'])
        state = 'BLOCKED' if 'futures' in self.market.blocked else 'READY' if ok == len(run['rows']) else 'DEGRADED'
        self.health(state, f'{len(run["rows"])} parite kaydedildi; {ok} veri hazır; liste sınırı yok')
        return run

    def follow(self):
        clock = self.market.get('futures', '/fapi/v1/time')['serverTime']
        end = int(clock) // MINUTE * MINUTE
        for w in self.store.records('watch'):
            if w['complete'] or w['cursor'] >= min(end, w['deadline']):
                continue
            try:
                # Paginated catch-up persists a cursor after every page; restarts do not lose candles.
                while w['cursor'] < min(end, w['deadline']):
                    raw = self.market.get('futures', '/fapi/v1/klines', {
                        'symbol': w['symbol'], 'interval': '1m', 'startTime': w['cursor'],
                        'endTime': min(end, w['deadline'])-1, 'limit': 1000})
                    before = w['cursor']
                    w = evaluate_minutes(w, raw, end)
                    if w['cursor'] == before:
                        raise ValueError('Takip mumları henüz sağlanmadı')
                    w['updated'] = now_ms()
                    w.pop('error', None)
                    self.store.put('watch:' + w['id'], 'watch', w)
            except AccessBlocked:
                raise
            except Exception as e:
                w.update(error=str(e), updated=now_ms(), tracking_status='VERİ EKSİK')
                self.store.put('watch:' + w['id'], 'watch', w)

    def snapshot(self):
        return {'health': self.store.get('health'), 'run': self.store.get('latest'),
                'runs': [{k: v for k, v in r.items() if k != 'rows'} for r in self.store.records('run')],
                'watches': self.store.records('watch')}

    def worker(self):
        scan_interval = max(3600, int(os.getenv('FLOW_SCAN_SECONDS', '86400')))
        follow_interval = max(60, int(os.getenv('FLOW_FOLLOW_SECONDS', '300')))
        while True:
            try:
                latest = self.store.get('latest')
                if not latest or now_ms() - latest['finished'] >= scan_interval*1000:
                    if self.scan() is None:
                        return  # Restricted access does not trigger repeated requests.
                self.follow()
            except AccessBlocked as e:
                self.health('BLOCKED', str(e))
                return
            except Exception as e:
                self.health('ERROR', str(e))
            time.sleep(follow_interval)


HTML = '''<!doctype html><html lang="tr"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Para Akışı Gözlemcisi</title><style>body{font:15px system-ui;background:#101925;color:#e7edf5;margin:24px}input,select,button{padding:10px;margin:5px;background:#233448;color:inherit;border:1px solid #53718b;border-radius:8px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:10px;border-bottom:1px solid #33475b;text-align:right}td:first-child,th:first-child{text-align:left}th{position:sticky;top:0;background:#233448}.note{color:#b8cadb}#status{padding:16px;background:#233448;border-radius:10px;margin-bottom:16px}.wrap{overflow:auto}a{color:#8fcaff}</style>
<h1>Para Akışı Gözlemcisi</h1><p class="note">Tüm uygun Binance USDT perpetual pariteleri · Son 24 saat / 7 gün · Emir göndermez</p>
<div id="status"></div><p class="note">Puan bir araştırma sıralamasıdır; pump olasılığı veya kazanç garantisi değildir. Alış farkı, dışarıdan yatırılan net para değildir. Spot ve futures ayrı ölçülür.</p>
<input id="q" placeholder="Parite ara"><select id="filter"><option value="">Hepsi</option><option>GÜÇLÜ TAKİP</option><option>TAKİP</option><option>ZAYIF</option><option>VERİ EKSİK</option></select><button id="download">Tüm tabloyu CSV indir</button><div class="wrap"><table><thead><tr><th>Parite</th><th>Durum</th><th>Puan</th><th>Kaynak</th><th>Spot Δ 24s USDT</th><th>Spot Δ 7g USDT</th><th>Futures Δ 24s USDT</th><th>Hacim x</th><th>OI 24s %</th><th>24s fiyat %</th><th>Takip max %</th><th>Takip min %</th><th>%20 görüldü</th><th>İlk olay</th><th>Gerekçe / eksik</th></tr></thead><tbody id="rows"></tbody></table></div>
<script>let data=__DATA__;const esc=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const n=x=>x==null?'—':Number(x).toLocaleString('tr-TR',{maximumFractionDigits:2});
function values(r){const w=(data.watches||[]).find(x=>x.run_id===data.run?.id&&x.symbol===r.symbol);const src=r.spot||r.futures;return [r.symbol,r.classification,r.score,r.score_source,r.spot?.daily.delta_usdt,r.spot?.weekly?.delta_usdt,r.futures?.daily.delta_usdt,src?.daily.volume_ratio,r.open_interest?.change_24h_pct,r.futures?.daily.return_pct,w?.max_up_pct,w?.max_down_pct,w?.pump_hit?'EVET':w?.complete?'HAYIR':'BEKLENİYOR',w?.first_event||w?.tracking_status,[...(r.reasons||[]),r.error,r.spot_error,r.oi_error,w?.error].filter(Boolean).join(' · ')]}
function render(){let h=data.health||{};document.getElementById('status').textContent=`Durum: ${h.status||'BAŞLATILMADI'} · ${h.message||''} · Parite: ${data.run?.universe_count??'henüz doğrulanmadı'} · Haber / balina / grup kaynakları: bağlı değil · Son durum: ${h.updated?new Date(h.updated).toLocaleString('tr-TR',{timeZone:'Europe/Istanbul'}):'—'}`;let q=document.getElementById('q').value.toUpperCase(),f=document.getElementById('filter').value;document.getElementById('rows').innerHTML=(data.run?.rows||[]).filter(r=>r.symbol.includes(q)&&(!f||r.classification===f)).map(r=>'<tr>'+values(r).map((x,i)=>'<td>'+esc([2,4,5,6,7,8,9,10,11].includes(i)?n(x):x)+'</td>').join('')+'</tr>').join('')}
document.getElementById('q').oninput=render;document.getElementById('filter').onchange=render;document.getElementById('download').onclick=()=>{let rows=[Array.from(document.querySelectorAll('th')).map(x=>x.textContent),...(data.run?.rows||[]).map(values)];let csv='\\ufeff'+rows.map(r=>r.map(x=>'"'+String(x??'').replaceAll('"','""')+'"').join(';')).join('\\n');let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));a.download='Tum_Pariteler_Para_Akisi.csv';a.click();URL.revokeObjectURL(a.href)};render();if(location.protocol!=='file:'&&!data.static_export)setInterval(async()=>{try{let r=await fetch('/api/snapshot');if(r.ok){data=await r.json();render()}}catch(e){}},60000);</script></html>'''


def render_html(snapshot):
    return HTML.replace('__DATA__', json.dumps(snapshot, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c'))


def export(observer, directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    snap = observer.snapshot()
    snap['static_export'] = True
    (root/'Takip_Ekrani.html').write_text(render_html(snap), encoding='utf-8')
    (root/'durum.json').write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding='utf-8')
    columns = ['symbol','classification','score','score_source','status','asof_utc_ms',
               'spot_delta_24h_usdt','spot_delta_7d_usdt','futures_delta_24h_usdt',
               'futures_delta_7d_usdt','volume_ratio','futures_return_24h_pct',
               'oi_change_24h_pct','funding_rate','tracking_reference','tracking_start_utc_ms',
               'tracking_max_up_pct','tracking_max_down_pct','pump_hit','first_event',
               'tracking_complete','reasons','error']
    watches = {w['symbol']: w for w in snap['watches']
               if w['run_id'] == (snap.get('run') or {}).get('id')}
    with (root/'Tum_Pariteler.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for r in (snap.get('run') or {}).get('rows', []):
            out = {k: r.get(k) for k in columns}
            source = r.get('spot') or r.get('futures') or {}
            watch = watches.get(r['symbol'], {})
            out.update(spot_delta_24h_usdt=r.get('spot', {}).get('daily', {}).get('delta_usdt') if r.get('spot') else None,
                       spot_delta_7d_usdt=(r.get('spot', {}).get('weekly') or {}).get('delta_usdt') if r.get('spot') else None,
                       futures_delta_24h_usdt=r.get('futures', {}).get('daily', {}).get('delta_usdt'),
                       futures_delta_7d_usdt=(r.get('futures', {}).get('weekly') or {}).get('delta_usdt'),
                       asof_utc_ms=(snap.get('run') or {}).get('asof'),
                       volume_ratio=source.get('daily', {}).get('volume_ratio'),
                       futures_return_24h_pct=r.get('futures', {}).get('daily', {}).get('return_pct'),
                       oi_change_24h_pct=r.get('open_interest', {}).get('change_24h_pct'),
                       tracking_reference=watch.get('reference'), tracking_start_utc_ms=watch.get('start'),
                       tracking_max_up_pct=watch.get('max_up_pct'), tracking_max_down_pct=watch.get('max_down_pct'),
                       pump_hit=watch.get('pump_hit'), first_event=watch.get('first_event'),
                       tracking_complete=watch.get('complete'), reasons=' | '.join(r.get('reasons', [])))
            writer.writerow(out)
    return snap


def create_app():
    from flask import Flask, jsonify, Response
    app = Flask(__name__)
    observer = Observer(Store(os.getenv('FLOW_DB_PATH', 'flow.sqlite3')))

    @app.get('/')
    def index():
        return Response(render_html(observer.snapshot()), mimetype='text/html')

    @app.get('/api/snapshot')
    def snapshot():
        return jsonify(observer.snapshot())

    @app.get('/health')
    def health():
        h = observer.store.get('health') or {'status':'STARTING','orders_enabled':False}
        return jsonify(h), 200 if h['status'] == 'READY' else 503

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    if os.getenv('FLOW_RUN_WORKER') == 'true':
        threading.Thread(target=observer.worker, daemon=True, name='flow-observer').start()
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['scan','follow','worker','export'])
    parser.add_argument('--db', default='flow.sqlite3')
    parser.add_argument('--output', default='reports')
    args = parser.parse_args()
    obs = Observer(Store(args.db))
    if args.command == 'scan':
        run = obs.scan()
        print(json.dumps({'health':obs.store.get('health'),'count':len(run['rows']) if run else 0}, ensure_ascii=False))
    elif args.command == 'follow':
        try:
            obs.follow()
        except Exception as e:
            obs.health('BLOCKED' if isinstance(e, AccessBlocked) else 'ERROR', str(e))
    elif args.command == 'worker':
        obs.worker()
    export(obs, args.output)
