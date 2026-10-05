import os
os.environ['OBSERVER_AUTOSTART'] = 'false'
import importlib
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from observer.storage import Store
from observer.tracking import process_job, watch_step, retry_job
from observer.market import candle_snapshot, public_get

app_module = importlib.import_module('observer.app')

def signal(**changes):
    value = dict(ticker='BTCUSDT.P', side='BUY', interval='15', price=100,
        signalId='test-entry', barTime=1000, stop=98, tp1=106,
        rsi=60, adx=25, volumeRatio=2.5, entryMovePct=1,
        rangeMult=1.3, breakoutConfirmed=True, barConfirmed=True, riskReward=3)
    value.update(changes)
    return value

def state():
    return dict(signal=app_module.normalize_signal(signal()), reference=100,
        started=100, expires=7300, lastReviewBar=1000, lastNotification=100,
        latestPrice=100, bestMovePct=0, worstMovePct=0, marketFailures=0)

def snapshot(**changes):
    value = dict(currentPrice=101, price=101, barTime=2000, high=102, low=99,
        rsi=60, adx=25, volumeRatio=2.5, entryMovePct=1, rangeMult=1.3,
        breakoutConfirmed=True, barConfirmed=True)
    value.update(changes)
    return value

class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(path=self.tmp.name+'/jobs.sqlite')
        self.env = patch.dict(os.environ, WEBHOOK_SECRET='test-only',
            TELEGRAM_BOT_TOKEN='test-only', TELEGRAM_CHAT_ID='test-only',
            AI_REVIEW_ENABLED='false', OPENAI_API_KEY='')
        self.env.start()
    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()
    def post(self, value):
        with patch('observer.storage.get_store',return_value=self.store), patch.object(app_module,'start_worker'):
            return app_module.app.test_client().post('/webhook',json=value)
    def test_auth_json_validation_and_management(self):
        self.assertEqual(self.post(signal()).status_code,401)
        self.assertEqual(self.post(dict(webhookSecret='test-only',action='tp2',side='TP2')).status_code,200)
        self.assertEqual(self.post(signal(webhookSecret='test-only',rsi=101)).status_code,400)
        self.assertEqual(self.post(signal(webhookSecret='test-only',price=0)).status_code,400)
    def test_accept_dedupe_and_secrets_removed(self):
        payload=signal(webhookSecret='test-only',binanceApiKey='must-not-store')
        self.assertEqual(self.post(payload).status_code,202)
        self.assertEqual(self.post(payload).status_code,200)
        job=self.store.claim()
        self.assertNotIn('webhookSecret',job['payload'])
        self.assertNotIn('binanceApiKey',job['payload'])
    def test_incomplete_and_unclosed_cannot_promote(self):
        for data in [signal(rsi=None),signal(barConfirmed=False)]:
            self.assertNotEqual(app_module.deterministic_review(app_module.normalize_signal(data))['decision'],'AL')
    def test_old_alert_timestamp_and_unbroken_band(self):
        data=app_module.normalize_signal(signal(signalTime=(time.time()-600)*1000))
        self.assertEqual(app_module.deterministic_review(data)['decision'],'RED')
        data=app_module.normalize_signal(signal(breakoutConfirmed=False))
        self.assertEqual(app_module.deterministic_review(data)['decision'],'İZLE')
    def test_large_pump_and_low_rr_rejected(self):
        for data in [signal(entryMovePct=17),signal(riskReward=.8)]:
            self.assertEqual(app_module.deterministic_review(app_module.normalize_signal(data))['decision'],'RED')
    def test_queue_overflow_does_not_consume_id(self):
        with patch.dict(os.environ,QUEUE_MAX='0'):
            self.assertEqual(self.post(signal(webhookSecret='test-only')).status_code,429)
        self.assertEqual(self.post(signal(webhookSecret='test-only')).status_code,202)
    def test_lease_reopen_and_budget(self):
        self.store.accept_review('a',signal())
        self.assertIsNotNone(self.store.claim())
        reopened=Store(path=self.store.path)
        self.assertIsNone(reopened.claim())
        self.assertEqual(reopened.counts()['review:pending'],1)
        self.assertTrue(reopened.consume_ai_budget(1))
        self.assertFalse(reopened.consume_ai_budget(1))
    def test_review_watch_outbox_delivery(self):
        data=app_module.normalize_signal(signal(breakoutConfirmed=False,volumeRatio=1,adx=18,rsi=50,rangeMult=1))
        self.store.accept_review('review:x',data)
        process_job(self.store,self.store.claim(),app_module.review_signal,app_module.telegram_message,lambda m:None)
        counts=self.store.counts()
        self.assertEqual(counts['watch:pending'],1)
        self.assertEqual(counts['notify:pending'],1)
        sent=[]
        process_job(self.store,self.store.claim(),None,None,sent.append)
        self.assertIn('Fiyat takibi başladı',sent[0])
    def test_watch_same_bar_does_not_call_ai(self):
        def fail(_): raise AssertionError('same bar review')
        _,terminal,_=watch_step(state(),snapshot(barTime=1000),fail,200)
        self.assertFalse(terminal)
    def test_watch_new_bar_rechecks_current_rr(self):
        seen=[]
        def reviewer(data):
            seen.append(data)
            return {'decision':'AL','score':85,'reasons':['fresh']}
        _,terminal,message=watch_step(state(),snapshot(),reviewer,200)
        self.assertTrue(terminal); self.assertIn('AL ADAYI',message)
        self.assertAlmostEqual(seen[0]['riskReward'],5/3)
        self.assertNotIn('pumpScore',seen[0])
    def test_watch_stop_chase_target_expiry_precede_ai(self):
        def fail(_):raise AssertionError('must not review')
        for snap,now in [(snapshot(currentPrice=97),200),(snapshot(currentPrice=117),200),
                         (snapshot(currentPrice=106),200),(snapshot(),8000)]:
            _,terminal,_=watch_step(state(),snap,fail,now)
            self.assertTrue(terminal)
    def test_old_wick_does_not_reject(self):
        _,terminal,_=watch_step(state(),snapshot(barTime=1000,low=90),lambda _:None,200)
        self.assertFalse(terminal)
    def test_short_stop(self):
        s=state();s['signal'].update(side='SHORT',stop=102,tp1=94)
        _,terminal,message=watch_step(s,snapshot(currentPrice=103),lambda _:None,200)
        self.assertTrue(terminal);self.assertIn('RED',message)
    def test_ai_cannot_promote_missing_data(self):
        data=app_module.normalize_signal(signal(rsi=None))
        baseline=app_module.deterministic_review(data)
        response={'output':[{'content':[{'type':'output_text','text':json.dumps({'decision':'AL','score':99,'reason':'test'})}]}]}
        with patch.dict(os.environ,AI_REVIEW_ENABLED='true',OPENAI_API_KEY='test-only'),patch('observer.storage.get_store',return_value=self.store),patch.object(app_module,'_http_post_json',return_value=response):
            result=app_module.ai_review(data,baseline)
        self.assertEqual(result['decision'],'İZLE')
    def test_ai_error_fallback_label(self):
        data=app_module.normalize_signal(signal());baseline=app_module.deterministic_review(data)
        with patch.dict(os.environ,AI_REVIEW_ENABLED='true',OPENAI_API_KEY='test-only'),patch('observer.storage.get_store',return_value=self.store),patch.object(app_module,'_http_post_json',side_effect=TimeoutError):
            result=app_module.ai_review(data,baseline)
        self.assertEqual(result['ai_status'],'error')
        self.assertEqual(result['decision'],baseline['decision'])
    def test_market_closed_candles_and_indicators(self):
        rows=[]
        for i in range(100):
            p=100+i
            rows.append([i*900000,str(p),str(p+2),str(p-.5),str(p+1),'10',i*900000+899999])
        result=candle_snapshot(rows,{},now_ms=99*900000)
        self.assertEqual(result['barTime'],98*900000)
        self.assertEqual(result['rsi'],100)
        self.assertEqual(result['adx'],100)
        self.assertAlmostEqual(result['volumeRatio'],1)
        self.assertTrue(result['barConfirmed'])
    def test_order_endpoint_blocked(self):
        with self.assertRaises(ValueError):public_get('/fapi/v1/order',{})

    def test_global_six_candidate_limit_and_idempotency(self):
        now=time.time()
        with self.store.connection() as conn:
            for i in range(6):
                self.assertEqual(self.store.reserve_candidate(conn,str(i),f'COIN{i}USDT',now),'accepted')
            self.assertEqual(self.store.reserve_candidate(conn,'six','OTHERUSDT',now),'day_limit')
            self.assertEqual(self.store.reserve_candidate(conn,'0','COIN0USDT',now),'duplicate')
        self.assertEqual(self.store.candidates_today(),6)

    def test_symbol_cooldown_survives_day_boundary(self):
        from datetime import datetime,timezone
        before=datetime(2026,10,5,20,59,tzinfo=timezone.utc).timestamp()
        after=before+120
        self.assertEqual(self.store.candidate_day(before),'2026-10-05')
        self.assertEqual(self.store.candidate_day(after),'2026-10-06')
        with self.store.connection() as conn:
            self.assertEqual(self.store.reserve_candidate(conn,'first','BTCUSDT',before),'accepted')
            self.assertEqual(self.store.reserve_candidate(conn,'later','BTCUSDT',after),'symbol_cooldown')
            self.assertEqual(self.store.reserve_candidate(conn,'next','ETHUSDT',after),'accepted')
            self.assertEqual(self.store.reserve_candidate(conn,'late','BTCUSDT',after+9*3600),'accepted')

    def test_concurrent_candidate_cap(self):
        from concurrent.futures import ThreadPoolExecutor
        def reserve(i):
            with self.store.connection() as conn:
                return self.store.reserve_candidate(conn,str(i),f'SYM{i}USDT')
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(reserve,range(8)))
        self.assertEqual(results.count('accepted'),6)
        self.assertEqual(results.count('day_limit'),2)

    def test_quota_suppresses_extra_telegram_candidates(self):
        with patch.dict(os.environ,MAX_AL_CANDIDATES_PER_DAY='1'):
            for i,symbol in enumerate(['BTCUSDT','ETHUSDT']):
                data=app_module.normalize_signal(signal(ticker=symbol,signalId=str(i)))
                self.store.accept_review(f'review:{i}',data)
                with self.store.connection() as conn:
                    job=dict(self.store.execute(conn,'SELECT * FROM observer_jobs WHERE id=?',(f'review:{i}',)).fetchone())
                    job['payload']=json.loads(job['payload'])
                process_job(self.store,job,app_module.review_signal,app_module.telegram_message,lambda _:None)
        self.assertEqual(self.store.counts()['notify:pending'],1)

    def test_selective_filter_failure_cannot_be_al(self):
        data=app_module.normalize_signal(signal(qualityFilterEnabled=True,qualityEntryPassed=False))
        self.assertEqual(app_module.deterministic_review(data)['decision'],'RED')

if __name__=='__main__':unittest.main()
