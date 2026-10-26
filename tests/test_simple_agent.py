import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from simple_agent.core import (TZ, ema, indicators, flow_score, target_shares, exact_weight,
    project, active_share, construct, build_dplan, validate, schema_path, version_hash, reference_return)
from simple_agent.data import load_demo, atomic_json, yahoo_rows
from simple_agent.service import window_check, check_state, active_checks, send

ROOT=Path(__file__).resolve().parents[1]

class StrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle=load_demo(ROOT)
        cls.universe=json.loads((ROOT/'simple_agent/universe.json').read_text())
        cls.schema=json.loads(schema_path(ROOT).read_text())
        cls.state={'cash':1e9,'nav':1e9,'holdings':{},'trade_date':cls.bundle['as_of']}
        cls.result=construct(cls.bundle,cls.universe,cls.state)
        cls.plan=build_dplan(cls.result,cls.bundle,cls.state,'DEMO_ONLY',cls.schema,datetime.now(TZ).isoformat(timespec='seconds'),version_hash(ROOT))

    def test_ema_alpha_two_over_fifteen(self):
        self.assertAlmostEqual(ema([100,115],14)[-1],102)

    def test_price_scale_does_not_change_score(self):
        rows=copy.deepcopy(self.bundle['prices']['2454'])
        a=indicators(rows)
        for r in rows:
            for key in ('open','high','low','close','adjusted_close'):r[key]*=100
        b=indicators(rows)
        self.assertAlmostEqual(a['score'],b['score'],10)
        self.assertAlmostEqual(a['atr_pct'],b['atr_pct'],10)

    def test_prior_volume_average_excludes_today(self):
        rows=copy.deepcopy(self.bundle['prices']['2454'])
        for r in rows[-6:-1]:r['volume']=100
        rows[-1]['volume']=1000
        self.assertEqual(indicators(rows)['volume_ratio'],10)

    def test_flat_rsi_is_neutral(self):
        rows=[{'date':str(i),'open':100,'high':100,'low':100,'close':100,'adjusted_close':100,'volume':100} for i in range(130)]
        s=indicators(rows)
        self.assertEqual(s['rsi10'],50);self.assertEqual(s['rsi_score'],0)

    def test_cross_group_capped(self):
        rows=[{'date':str(i),'open':100,'high':100,'low':100,'close':100,'adjusted_close':100,'volume':100} for i in range(130)]
        rows[-1].update(open=110,high=110,low=110,close=110,adjusted_close=110)
        self.assertEqual(indicators(rows)['cross_score'],4)

    def test_adjustment_removes_split_jump(self):
        rows=[{'date':str(i),'open':100,'high':100,'low':100,'close':100,'adjusted_close':50,'volume':100} for i in range(130)]
        rows[-1].update(open=50,high=50,low=50,close=50)
        self.assertEqual(indicators(rows)['return_1d'],0)

    def test_entry_reference_rebased_after_split(self):
        rows=[{'date':'2026-10-20','adjusted_close':50},{'date':'2026-10-21','adjusted_close':50}]
        record={'reference_date':'2026-10-20','reference_adjusted':100}
        self.assertEqual(reference_return(rows,record,50),0)

    def test_future_source_rejected(self):
        p=copy.deepcopy(self.plan);p['sources'][0]['content_as_of']='2026-10-28T13:30:00+08:00'
        with self.assertRaisesRegex(ValueError,'Future'):
            validate(p,self.schema,self.state,self.result['prices'],self.universe)

    def test_incomplete_financial_ranking_rejected(self):
        b=copy.deepcopy(self.bundle);b['market_caps'].pop('2881',None)
        with self.assertRaisesRegex(ValueError,'ALL eligible'):
            construct(b,self.universe,self.state)

    def test_missing_flow_is_not_negative_signal(self):
        self.assertIsNone(flow_score([], 'Foreign_Investor','2026-10-22'))

    def test_decimal_floor_boundary(self):
        self.assertEqual(target_shares(.2,1e9,100),2_000_000)
        for q in (1000,10000,2000000):
            self.assertEqual(target_shares(exact_weight(q,1e9,102.3),1e9,102.3),q)

    def test_fees_and_sell_tax(self):
        s={'cash':100000,'nav':200000,'holdings':{'2330':1000}}
        p=project(s,[{'ticker':'2330','side':'SELL','shares':1000}],{'2330':100})
        self.assertAlmostEqual(p['cash'],199557.5)

    def test_dividends_receivable_not_spendable_cash(self):
        p=project({'cash':100,'nav':120,'holdings':{},'dividend_receivable':20},[],{})
        self.assertEqual(p['nav'],120);self.assertEqual(p['cash'],100)

    def test_short_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'Short'):
            project(self.state,[{'ticker':'2330','side':'SELL','shares':1000}],{'2330':100})

    def test_odd_lot_order_rejected(self):
        with self.assertRaises(ValueError):
            project(self.state,[{'ticker':'2330','side':'BUY','shares':999}],{'2330':100})

    def test_active_share_identical_and_disjoint(self):
        a={str(i):.1 for i in range(10)};b={str(i+10):.1 for i in range(10)}
        self.assertAlmostEqual(active_share(a,a),0)
        self.assertAlmostEqual(active_share(a,b,True),1)

    def test_missing_etf_blocks_submission(self):
        codes=json.loads((ROOT/'simple_agent/etfs.json').read_text())
        c=active_checks(self.result,self.bundle,codes)
        self.assertFalse(c['pass']);self.assertEqual(len(c['missing']),30)

    def test_all30_fresh_distinct_etfs_can_pass(self):
        codes=json.loads((ROOT/'simple_agent/etfs.json').read_text())
        b=copy.deepcopy(self.bundle)
        b['etfs']={c:{'date':b['as_of'],'weights':{f'FOREIGN{i}':.1 for i in range(10)}} for c in codes}
        self.assertTrue(active_checks(self.result,b,codes)['pass'])

    def test_initial_real_fixture_generates_valid26_holdings(self):
        p=validate(self.plan,self.schema,self.state,self.result['prices'],self.universe)
        self.assertEqual(len(p['holdings']),26)
        self.assertEqual(len(self.plan['orders']),26)
        self.assertTrue(0<=p['cash_pct']<.25)

    def test_schema_version_mismatch_never_filtered(self):
        p=copy.deepcopy(self.plan);p['schema_version']='4.1'
        with self.assertRaisesRegex(ValueError,'Schema'):
            validate(p,self.schema,self.state,self.result['prices'],self.universe)

    def test_broken_reference_rejected(self):
        p=copy.deepcopy(self.plan);p['observations'][0]['source_ref']=['S999']
        with self.assertRaisesRegex(ValueError,'Broken'):
            validate(p,self.schema,self.state,self.result['prices'],self.universe)

    def test_wrong_share_count_rejected(self):
        p=copy.deepcopy(self.plan);p['orders'][0]['shares']+=1000
        with self.assertRaisesRegex(ValueError,'derive-orders'):
            validate(p,self.schema,self.state,self.result['prices'],self.universe)

    def test_stale_held_quote_stops_instead_of_selling(self):
        b=copy.deepcopy(self.bundle);b['prices']['2330'][-1]['date']='2026-10-21'
        with self.assertRaisesRegex(ValueError,'Held'):
            construct(b,self.universe,{'nav':1e9,'cash':8e8,'holdings':{'2330':1000}})

    def test_second_day_preserves_count_and_no_double_vote(self):
        s={**self.result['projected'],'trade_date':self.bundle['as_of']}
        a=construct(self.bundle,self.universe,s,self.result['memory'])
        b=construct(self.bundle,self.universe,s,a['memory'])
        self.assertEqual(a['memory'],b['memory'])
        self.assertEqual(len(b['projected']['holdings']),26)

    def test_deadline_timezone(self):
        window_check('2026-10-27',datetime(2026,10,26,19,30,tzinfo=TZ))
        window_check('2026-10-27',datetime(2026,10,27,8,54,59,tzinfo=TZ))
        for d in [datetime(2026,10,26,19,29,tzinfo=TZ),datetime(2026,10,27,8,55,tzinfo=TZ)]:
            with self.assertRaises(ValueError):window_check('2026-10-27',d)

    def test_account_sync_rejects_stale_or_inconsistent_state(self):
        with self.assertRaises(ValueError):check_state({**self.state,'trade_date':'2026-10-21'},self.bundle,self.universe)
        with self.assertRaises(ValueError):check_state({**self.state,'cash':900000000},self.bundle,self.universe)

    def test_pending_submission_never_auto_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'plan.json';atomic_json(p,self.plan)
            atomic_json(root/'runtime/attempts/2026-10-27.json',{'count':1,'pending':True})
            with patch('simple_agent.service.window_check'),patch('simple_agent.service.call_json') as call:
                with self.assertRaisesRegex(ValueError,'unknown'):
                    send(root,self.plan,p,{}, {'pass':True},{})
                call.assert_not_called()

    def test_http200_without_receipt_never_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'plan.json';atomic_json(p,self.plan)
            a={'submit_url':'https://example.org/verified-test','body_mode':'json','accepted_field':'ok','accepted_value':True,'receipt_id_field':'id','team_field':'team','trade_date_field':'date'}
            with patch('simple_agent.service.window_check'),patch('simple_agent.service.auth_headers',return_value={}),patch('simple_agent.service.call_json',return_value={'ok':True}):
                with self.assertRaisesRegex(ValueError,'receipt'):
                    send(root,self.plan,p,a,{'pass':True},{})
            self.assertFalse((root/'runtime/receipts/2026-10-27.json').exists())

    def test_explicit_receipt_persists_and_second_call_noops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'plan.json';atomic_json(p,self.plan)
            a={'submit_url':'https://example.org/verified-test','body_mode':'json','accepted_field':'ok','accepted_value':True,'receipt_id_field':'id','team_field':'team','trade_date_field':'date'}
            response={'ok':True,'id':'test_receipt','team':'DEMO_ONLY','date':'2026-10-27'}
            with patch('simple_agent.service.window_check'),patch('simple_agent.service.auth_headers',return_value={}),patch('simple_agent.service.call_json',return_value=response) as call:
                first=send(root,self.plan,p,a,{'pass':True},{'sample':{}})
                second=send(root,self.plan,p,a,{'pass':True},{'sample':{}})
                self.assertEqual(first['status'],'accepted')
                self.assertEqual(second['status'],'already_accepted')
                self.assertEqual(call.call_count,1)
            self.assertTrue((root/'runtime/initial_used.json').exists())
            self.assertTrue((root/'runtime/strategy_memory.json').exists())

if __name__=='__main__':unittest.main()
