import json
import tempfile
import unittest
from pathlib import Path
from flow_observer import (DAY, HOUR, MINUTE, AccessBlocked, Observer, PublicMarket,
                           Store, evaluate_minutes, features, rank_rows, spot_match,
                           universe, render_html)


def bars(count, step=HOUR, start=0, buy=70, price=100):
    return [[start+i*step, str(price), str(price+1), str(price-1), str(price),
             '1', start+(i+1)*step-1, '100', 5, '.7', str(buy), '0'] for i in range(count)]


def watch():
    return {'start':0, 'deadline':DAY, 'cursor':0, 'reference':None,
            'pump_pct':20, 'stop_pct':3, 'max_up_pct':0, 'max_down_pct':0}


class FlowTests(unittest.TestCase):
    def test_true_quote_flow_and_windows(self):
        f = features(bars(400), 400*HOUR)
        self.assertEqual(f['daily']['taker_buy_usdt'], 1680)
        self.assertEqual(f['daily']['taker_sell_usdt'], 720)
        self.assertEqual(f['daily']['delta_usdt'], 960)
        self.assertEqual(f['weekly']['delta_usdt'], 6720)
        self.assertEqual(f['positive_days_7d'], 7)
        self.assertEqual(f['daily']['volume_ratio'], 1)

    def test_future_candle_not_used(self):
        self.assertEqual(features(bars(25), 24*HOUR)['closed_hours'], 24)

    def test_gap_and_stale_data_are_errors(self):
        rows = bars(30)
        del rows[15]
        with self.assertRaises(ValueError):
            features(rows, 30*HOUR)
        with self.assertRaises(ValueError):
            features(bars(30), 31*HOUR)

    def test_missing_trade_fields_not_approximated(self):
        with self.assertRaises(ValueError):
            features([r[:6] for r in bars(30)], 30*HOUR)

    def test_new_listing_has_no_invented_week(self):
        f=features(bars(30),30*HOUR)
        self.assertIsNone(f['weekly'])
        self.assertIsNone(f['daily']['volume_ratio'])

    def test_all_501_symbols_visible_even_bad(self):
        rows=[{'symbol':f'X{i}USDT','status':'ERROR'} for i in range(501)]
        self.assertEqual(len(rank_rows(rows)),501)
        self.assertTrue(all(r['score'] is None for r in rows))

    def test_universe_and_explicit_spot_denominations(self):
        s={'symbol':'AUSDT','baseAsset':'A','quoteAsset':'USDT','contractType':'PERPETUAL','status':'TRADING'}
        u,x=universe({'symbols':[s,dict(s,symbol='BUSDT',status='SETTLING'),dict(s,symbol='CUSDC',quoteAsset='USDC')]})
        self.assertEqual(len(u),1)
        self.assertEqual(len(x),2)
        self.assertEqual(spot_match({'baseAsset':'1000PEPE'},[{'baseAsset':'PEPE','symbol':'PEPEUSDT'}]),('PEPEUSDT',1000))
        self.assertEqual(spot_match({'baseAsset':'PEPE2'},[{'baseAsset':'PEPE','symbol':'PEPEUSDT'}]),(None,None))

    def test_no_order_or_account_endpoints(self):
        with self.assertRaises(ValueError):
            PublicMarket().get('futures','/fapi/v1/order')
        with self.assertRaises(ValueError):
            PublicMarket().get('spot','/api/v3/account')

    def test_pump_after_stop_not_pump_first(self):
        r=bars(2,MINUTE)
        r[0][3]='96'
        r[1][2]='121'
        s=evaluate_minutes(watch(),r,2*MINUTE)
        self.assertTrue(s['pump_hit'])
        self.assertEqual(s['first_event'],'STOP_FIRST')
        self.assertEqual(s['max_down_pct'],-4.0000000000000036)

    def test_simultaneous_wicks_not_a_win(self):
        r=bars(1,MINUTE)
        r[0][2],r[0][3]='121','96'
        s=evaluate_minutes(watch(),r,MINUTE)
        self.assertEqual(s['first_event'],'SAME_BAR_UNCERTAIN')

    def test_cursor_survives_restart_and_whole_horizon(self):
        with tempfile.TemporaryDirectory() as d:
            db=str(Path(d)/'test.sqlite3')
            first=evaluate_minutes(watch(),bars(1000,MINUTE),1000*MINUTE)
            Store(db).put('w','watch',first)
            loaded=Store(db).get('w')
            self.assertFalse(loaded['complete'])
            last=evaluate_minutes(loaded,bars(440,MINUTE,start=1000*MINUTE),DAY)
            self.assertTrue(last['complete'])
            self.assertEqual(last['cursor'],DAY)
            self.assertFalse(last['pump_hit'])

    def test_missing_followup_candle_not_completed(self):
        with self.assertRaises(ValueError):
            evaluate_minutes(watch(),bars(2,MINUTE,start=MINUTE),3*MINUTE)

    def test_blocked_scan_has_no_synthetic_report(self):
        class Blocked:
            def get(self,*a,**kw):
                raise AccessBlocked('HTTP 451')
        with tempfile.TemporaryDirectory() as d:
            o=Observer(Store(str(Path(d)/'db')),Blocked())
            self.assertIsNone(o.scan())
            self.assertEqual(o.snapshot()['health']['status'],'BLOCKED')
            self.assertIsNone(o.snapshot()['run'])

    def test_embedded_content_cannot_break_script(self):
        page=render_html({'health':{'message':'</script><img src=x onerror=1>'}})
        self.assertNotIn('</script><img',page)
        self.assertIn('\\u003c/script>',page)

    def test_full_scan_registers_every_candidate_for_followup(self):
        class Market:
            blocked=set()
            def get(self,market,path,params=None):
                if path.endswith('/time'):
                    return {'serverTime':400*HOUR}
                if path.endswith('/exchangeInfo'):
                    return {'symbols': [] if market=='spot' else [
                        {'symbol':f'X{i}USDT','baseAsset':f'X{i}','quoteAsset':'USDT',
                         'contractType':'PERPETUAL','status':'TRADING'} for i in range(501)]}
                if path.endswith('/klines'):
                    return bars(400)
                return []
        with tempfile.TemporaryDirectory() as d:
            store=Store(str(Path(d)/'db'))
            run=Observer(store,Market()).scan()
            self.assertEqual(len(run['rows']),501)
            self.assertEqual(len(store.records('watch')),501)
            self.assertEqual(store.get('health')['status'],'READY')
            self.assertTrue(all(r['score_source']=='FUTURES_ONLY' for r in run['rows']))


if __name__=='__main__':
    unittest.main()
