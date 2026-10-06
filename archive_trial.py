"""Official archive smoke trial; explicitly historical, not live forecasting."""
import argparse
import csv
import hashlib
import io
import json
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from flow_observer import (DAY, HOUR, MINUTE, Observer, Store, evaluate_minutes,
                           export, features, rank_rows)


def archive(cache, market, kind, symbol, interval, period):
    name=f'{symbol}-{interval}-{period}.zip'
    target=cache/market/kind/name
    target.parent.mkdir(parents=True,exist_ok=True)
    prefix='futures/um' if market=='futures' else 'spot'
    url=f'https://data.binance.vision/data/{prefix}/{kind}/klines/{urllib.parse.quote(symbol)}/{interval}/{urllib.parse.quote(name)}'
    if not target.exists():
        with urllib.request.urlopen(url,timeout=15) as r:
            payload=r.read(16*1024*1024)
        target.write_bytes(payload)
    payload=target.read_bytes()
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        rows=list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]),encoding='utf-8')))
    if rows and not rows[0][0].isdigit():
        rows=rows[1:]
    # Since Jan 2025 spot archives use microseconds; futures archives use milliseconds.
    for row in rows:
        if int(row[0])>10**14:
            row[0],row[6]=str(int(row[0])//1000),str(int(row[6])//1000)
    return rows,{'url':url,'sha256':hashlib.sha256(payload).hexdigest()}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--universe',required=True)
    p.add_argument('--cache',default='archive_cache')
    p.add_argument('--db',default='archive.sqlite3')
    p.add_argument('--output',default='archive_report')
    args=p.parse_args()
    symbols=json.loads(Path(args.universe).read_text())
    asof=int(datetime(2026,10,1,tzinfo=timezone.utc).timestamp()*1000)
    cache=Path(args.cache)
    store=Store(args.db)
    obs=Observer(store)
    run={'id':'archive-2026-10-01','asof':asof,'created':int(time.time()*1000),
         'status':'ARCHIVE_TRIAL','universe_count':len(symbols),'rows':[],
         'mode':'HISTORICAL_ARCHIVE_NOT_LIVE',
         'universe_source':'Previously saved Binance futures universe; retrospective membership, survivorship bias possible',
         'tracking_start':asof,
         'tracking_reference_rule':'1 Ekim 2026 00:00 UTC 1dk açılışı; varsayımsal referans',
         'windows':'30 Eylül günlük / 24–30 Eylül haftalık; yalnız rapor zamanından önceki veri',
         'score_is_probability':False}
    evidences=[]
    def one(s):
        sym=s['symbol']
        row={'symbol':sym,'status':'OK','spot':None,'spot_status':'NO_ARCHIVE',
             'underlying_type':s.get('underlyingType'),'news':None,'whales':None,'groups':None}
        evidence=[]
        try:
            raw,e=archive(cache,'futures','monthly',sym,'1h','2026-09')
            evidence.append(e)
            row['futures']=features(raw,asof)
        except Exception as e:
            row.update(status='ERROR',error=f'Arşiv verisi yok/geçersiz: {type(e).__name__}')
            return row,None,evidence
        # Known exact base/symbol mapping only. Do not invent spot identity.
        ss=sym[4:] if s.get('baseAsset','').startswith('1000') else sym
        try:
            raw,e=archive(cache,'spot','monthly',ss,'1h','2026-09')
            evidence.append(e)
            row.update(spot=features(raw,asof),spot_symbol=ss,spot_status='OK')
        except Exception:
            pass
        w={'id':run['id']+':'+sym,'run_id':run['id'],'symbol':sym,
           'start':asof,'deadline':asof+DAY,'cursor':asof,'reference':None,
           'pump_pct':20,'stop_pct':3,'max_up_pct':0,'max_down_pct':0,'complete':False}
        try:
            raw,e=archive(cache,'futures','daily',sym,'1m','2026-10-01')
            evidence.append(e)
            w=evaluate_minutes(w,raw,asof+DAY)
        except Exception as e:
            w.update(error=f'Takip arşivi eksik: {type(e).__name__}',tracking_status='VERİ EKSİK')
        return row,w,evidence
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures=[pool.submit(one,s) for s in symbols]
        for i,f in enumerate(as_completed(futures),1):
            row,w,e=f.result()
            run['rows'].append(row)
            evidences.extend(e)
            if w:
                store.put('watch:'+w['id'],'watch',w)
            if i%25==0 or i==len(symbols):
                print(f'ARCHIVE {i}/{len(symbols)}',flush=True)
                store.put('run:'+run['id'],'run',run)
    run['rows']=rank_rows(run['rows'])
    run['finished']=int(time.time()*1000)
    store.put('run:'+run['id'],'run',run)
    store.put('latest','latest',run)
    obs.health('ARCHIVE_ONLY','1 Ekim 2026 arşiv denemesi; canlı tarama değildir. Güncel API bu ortamda HTTP 451.')
    snap=export(obs,args.output)
    root=Path(args.output)
    (root/'kaynaklar.json').write_text(json.dumps(evidences,ensure_ascii=False,indent=2))
    complete=[w for w in snap['watches'] if w['complete']]
    summary={'historical':True,'universe':len(symbols),'data_ok':sum(r['status']=='OK' for r in run['rows']),
             'spot_available':sum(bool(r.get('spot')) for r in run['rows']),
             'followup_complete':len(complete),'pump_20_count':sum(w['pump_hit'] for w in complete),
             'pump_first':sum(w.get('first_event')=='PUMP_FIRST' for w in complete),
             'ambiguous':sum(w.get('first_event')=='SAME_BAR_UNCERTAIN' for w in complete),
             'stop_first':sum(w.get('first_event')=='STOP_FIRST' for w in complete)}
    (root/'ozet.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    watches={w['symbol']:w for w in snap['watches']}
    groups=[]
    for name in ('GÜÇLÜ TAKİP','TAKİP','ZAYIF','VERİ EKSİK'):
        selected=[r for r in run['rows'] if r['classification']==name]
        groups.append({'group':name,'count':len(selected),
                       'pump':sum(watches.get(r['symbol'],{}).get('pump_hit',False) for r in selected),
                       'pump_first':sum(watches.get(r['symbol'],{}).get('first_event')=='PUMP_FIRST' for r in selected)})
    (root/'grup_sonuclari.json').write_text(json.dumps(groups,ensure_ascii=False,indent=2))
    heading='<h2>1 Ekim 2026 arşiv denemesi</h2><p>Ölçüm: 30 Eylül ve 24–30 Eylül; takip: 1 Ekim 00:00–2 Ekim 00:00 UTC. Eski parite listesi kullanıldı; güncel evren doğrulanmadı.</p>'
    table='<table><tr><th>Önceden belirlenen grup</th><th>Parite</th><th>%20 gördü</th><th>%3 düşüşten önce %20</th></tr>'
    table+=''.join('<tr><td>'+g['group']+'</td><td>'+str(g['count'])+'</td><td>'+str(g['pump'])+'</td><td>'+str(g['pump_first'])+'</td></tr>' for g in groups)
    table+='</table><p>Tek günlük araştırma denemesi; giriş kuralı doğrulanmadı. Arşiv denemesinde OI, funding, haber ve balina verisi yoktur. İlk olay, referans fiyata göre varsayımsal eşik sırasıdır.</p>'
    report=root/'Takip_Ekrani.html'
    report.write_text(report.read_text().replace('<input id="q"',heading+table+'<input id="q"'))
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
