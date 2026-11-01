import concurrent.futures
import datetime as dt
import json
import math
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research import Store, extract_facts, now, fetch_sec
from valuation import DEFAULTS, dcf, scenarios, reverse_dcf
from app import Application, handler, ThreadingHTTPServer, model_review

def fixture():
    recent=dict(form=[],filingDate=[],reportDate=[],accessionNumber=[],primaryDocument=[])
    facts={'cik':1652044,'facts':{'us-gaap':{}}}
    tags=['RevenueFromContractWithCustomerExcludingAssessedTax','OperatingIncomeLoss','NetCashProvidedByUsedInOperatingActivities','PaymentsToAcquirePropertyPlantAndEquipment']
    for yr in [2022,2023,2024]:
        acc=f'0001652044-{str(yr+1)[2:]}-000001'
        for k,v in dict(form='10-K',filingDate=f'{yr+1}-02-01',reportDate=f'{yr}-12-31',accessionNumber=acc,primaryDocument='annual.htm').items():
            recent[k].append(v)
        for tag in tags:
            items=facts['facts']['us-gaap'].setdefault(tag,{'units':{'USD':[]}})['units']['USD']
            items.append(dict(start=f'{yr}-01-01',end=f'{yr}-12-31',filed=f'{yr+1}-02-01',accn=acc,form='10-K',val=1000e6))
            # Wrong period and later restatement must not win over original annual fact.
            items.append(dict(start=f'{yr}-10-01',end=f'{yr}-12-31',filed=f'{yr+1}-02-01',accn=acc,form='10-K',val=999))
            items.append(dict(start=f'{yr}-01-01',end=f'{yr}-12-31',filed='2026-02-01',accn='future',form='10-K',val=999))
    return facts,{'cik':'1652044','filings':{'recent':recent}}

class ValuationTests(unittest.TestCase):
    def test_independent_one_year(self):
        a=dict(DEFAULTS,years=1)
        r=dcf(a)
        nopat=1000*1.1*.25*.79
        fcff=nopat*(1-.1/.2)
        terminal=nopat*1.025*(1-.025/.12)/(.1-.025)
        self.assertAlmostEqual(r['per_share'],(fcff+terminal)/1.1/100,places=10)

    def test_bridge(self):
        a=dcf(DEFAULTS);b=dcf(dict(DEFAULTS,cash=20,debt=10,other_claims=5))
        self.assertAlmostEqual(b['equity']-a['equity'],5)

    def test_invalid_boundaries(self):
        for patch in [dict(wacc=.025),dict(wacc=.02),dict(shares=0),dict(roic=0),dict(years=2.5),dict(growth=.4),dict(revenue=float('nan')),dict(revenue=1e308),dict(tax=1),dict(cash=-1),dict(terminal_roic=.01),dict(margin=-.2)]:
            with self.subTest(patch=patch),self.assertRaises(ValueError):dcf(dict(DEFAULTS,**patch))

    def test_reverse_roundtrip(self):
        target=dcf(DEFAULTS)['per_share'];r=reverse_dcf(DEFAULTS,target)
        self.assertTrue(any(abs(g-DEFAULTS['growth'])<1e-7 for g in r['growth_solutions']))
        for g in r['growth_solutions']:
            self.assertAlmostEqual(dcf(dict(DEFAULTS,growth=g))['per_share'],target,places=6)

    def test_sensitive_and_case(self):
        r=scenarios(DEFAULTS)
        self.assertEqual(r['cases'][1]['per_share'],dcf(DEFAULTS)['per_share'])
        self.assertEqual(r['sensitivity'][2][2]['value'],dcf(DEFAULTS)['per_share'])
        self.assertGreater(r['sensitivity'][0][2]['value'],r['sensitivity'][4][2]['value'])

class ExtractionTests(unittest.TestCase):
    def test_cutoff_period_provenance(self):
        facts,subs=fixture();r=extract_facts(facts,subs,'2024-06-01','1652044')
        self.assertEqual(len(r),8)
        self.assertTrue(all(f['end']<='2023-12-31' and f['value']==1e9 for f in r))
        self.assertTrue(all('annual.htm' in f['url'] and f['tag'].startswith('us-gaap:') for f in r))

    def test_conflict_not_silently_verified(self):
        facts,subs=fixture();items=facts['facts']['us-gaap']['OperatingIncomeLoss']['units']['USD']
        items.append(dict(items[-3],val=55))
        r=extract_facts(facts,subs,'2025-06-01','1652044')
        self.assertEqual(next(f for f in r if f['metric']=='operating_income' and f['end']=='2024-12-31')['status'],'conflict')

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name)/'data')
        self.co=self.s.add_company('GOOGL');self.s.activate(self.co['id'])
    def tearDown(self):self.temp.cleanup()
    def evidence(self):
        return self.s.manual_fact(dict(metric='revenue',value=1e9,start='2024-01-01',end='2024-12-31',filed='2025-02-01',url='https://www.sec.gov/example',location='Income statement'))
    def body(self):return dict(responses=['观察','解释','判断'],counter='竞争变化',falsification='下期收入下降',metrics={})
    def test_empty_no_auto_pick(self):
        with tempfile.TemporaryDirectory() as p:
            s=Store(p);self.assertIsNone(s.company());self.assertFalse(s.settings()['reminder_enabled'])
            with self.assertRaises(ValueError):s.pack()
    def test_resume_and_idempotence(self):
        p=self.s.pack('2026-11-02');q=self.s.pack('2026-11-03');self.assertEqual(p['id'],q['id'])
        self.assertEqual(len(self.s.state()['packs']),1)
    def test_parallel_trigger(self):
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            ids=list(ex.map(lambda _:self.s.pack('2026-11-02')['id'],range(20)))
        self.assertEqual(len(set(ids)),1)
    def test_immutable_answers_and_snapshot(self):
        first=self.evidence();p=self.s.pack();self.s.answer(p['id'],self.body());self.evidence()
        self.s.answer(p['id'],dict(self.body(),counter='新反方'))
        self.assertEqual(len(self.s.state()['packs'][0]['answers']),2)
        self.assertEqual(self.s.state()['packs'][0]['snapshot_id'],first)
        with self.assertRaises(ValueError):self.s.refresh_pack()
    def test_complete_requires_evidence_answers(self):
        p=self.s.pack()
        with self.assertRaises(ValueError):self.s.complete(p['id'])
        self.s.answer(p['id'],self.body())
        with self.assertRaises(ValueError):self.s.complete(p['id'])
    def test_complete_once_and_next_day(self):
        self.evidence();p=self.s.pack('2026-11-02');self.s.answer(p['id'],self.body());self.s.complete(p['id']);self.s.complete(p['id'])
        self.assertEqual(self.s.company()['stage'],1)
        self.assertEqual(self.s.pack('2026-11-02')['id'],p['id'])
        self.assertEqual(self.s.pack('2026-11-03')['stage'],1)
    def test_switch_keeps_progress(self):
        self.evidence();p=self.s.pack();other=self.s.add_company('NVDA');self.s.activate(other['id'])
        self.assertNotEqual(self.s.pack()['id'],p['id'])
        self.s.activate(self.co['id']);self.assertEqual(self.s.pack()['id'],p['id'])
    def test_reminder_once_and_resume(self):
        self.s.set_settings(dict(reminder_enabled=True,reminder_time='10:00',start_date='2026-11-02'))
        tz=now().tzinfo
        self.assertFalse(self.s.schedule_tick(dt.datetime(2026,11,2,9,59,tzinfo=tz)))
        self.assertTrue(self.s.schedule_tick(dt.datetime(2026,11,2,10,0,tzinfo=tz)))
        self.assertFalse(self.s.schedule_tick(dt.datetime(2026,11,2,11,0,tzinfo=tz)))
        self.assertTrue(self.s.schedule_tick(dt.datetime(2026,11,3,10,0,tzinfo=tz)))
        self.assertEqual(len(self.s.state()['packs']),1)
        self.assertEqual(len(self.s.state()['notifications']),2)
    def test_pause_and_weekend(self):
        self.assertFalse(self.s.schedule_tick(dt.datetime(2026,11,2,12,0,tzinfo=now().tzinfo)))
        self.s.set_settings(dict(reminder_enabled=True,frequency='weekdays',start_date='2026-11-01'))
        self.assertFalse(self.s.schedule_tick(dt.datetime(2026,11,7,12,0,tzinfo=now().tzinfo)))
    def test_manual_status_and_history(self):
        first=self.evidence();second=self.evidence();self.assertNotEqual(first,second)
        self.assertEqual(len(self.s.get_facts(first)),1)
        self.assertEqual(self.s.get_facts(first)[0]['status'],'manual_unverified')
    def test_invalid_setting_and_fact(self):
        for d in [dict(reminder_time='25:00'),dict(frequency='hourly'),dict(mode='bogus'),dict(model_url='http://example.com'),dict(reminder_enabled='yes')]:
            with self.assertRaises(ValueError):self.s.set_settings(d)
        with self.assertRaises(ValueError):self.s.add_company('<script>')
    def test_durable_restart(self):
        self.evidence();p=self.s.pack();self.s.answer(p['id'],self.body())
        restored=Store(self.s.folder);self.assertEqual(restored.state()['packs'][0]['answers'][0]['body'],self.body())
    def test_review_deduplicated_and_durable(self):
        self.evidence();p=self.s.pack();body=dict(self.body(),review_concepts='ROIC，SBC，ROIC')
        self.s.answer(p['id'],body);self.s.answer(p['id'],body)
        self.assertEqual(len(self.s.state()['reviews']),2)
    def test_stage_one_cannot_skip_core_metrics(self):
        self.evidence()
        with self.s.connect() as c:c.execute('UPDATE companies SET stage=1 WHERE id=?',(self.co['id'],))
        p=self.s.pack();self.s.answer(p['id'],self.body())
        with self.assertRaisesRegex(ValueError,'四项财务'):self.s.complete(p['id'])
    def test_quarter_manual_rejected(self):
        with self.assertRaisesRegex(ValueError,'年度'):
            self.s.manual_fact(dict(metric='revenue',value=10,start='2024-10-01',end='2024-12-31',filed='2025-02-01',url='https://www.sec.gov/example'))
    def test_fetch_mocked_and_raw_hash(self):
        facts,subs=fixture();subs['tickers']=['GOOGL','GOOG']
        self.s.set_settings(dict(sec_identity='Integration Test test@example.com'))
        class Response:
            def __init__(self,data):self.data=json.dumps(data).encode()
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,*a):return self.data
        with mock.patch('research.urllib.request.urlopen',side_effect=[Response(subs),Response(facts)]),mock.patch('research.time.sleep'):
            result=fetch_sec(self.s)
        self.assertEqual(result['count'],12)
        snap=self.s.latest_snapshot(self.co['id']);meta=json.loads(snap['payload'])
        self.assertTrue((self.s.folder/'raw'/meta['raw_file']).exists())
        self.assertEqual(len(meta['sha256']),64)
    def test_fetch_failure_retains_old_snapshot(self):
        first=self.evidence();self.s.set_settings(dict(sec_identity='Integration Test test@example.com'))
        with mock.patch('research.urllib.request.urlopen',side_effect=TimeoutError),mock.patch('research.time.sleep'):
            with self.assertRaisesRegex(ValueError,'保留旧资料'):fetch_sec(self.s)
        self.assertEqual(self.s.latest_snapshot(self.co['id'])['id'],first)
    def test_model_disabled_no_request(self):
        with mock.patch('app.urllib.request.urlopen') as fetch:
            with self.assertRaises(ValueError):model_review(self.s,1)
            fetch.assert_not_called()

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=Application(Path(self.tmp.name)/'data')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.app));self.app.server=self.server
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def req(self,path,data=None,token=True,origin=None):
        headers={'X-App-Token':self.app.token} if token else {}
        if origin:headers['Origin']=origin
        request=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(request) as r:return r.read()
        except urllib.error.HTTPError as exc:
            exc.close()
            raise
    def test_token_and_origin(self):
        for kwargs in [dict(token=False),dict(origin='https://example.com')]:
            with self.assertRaises(urllib.error.HTTPError) as err:self.req('/api/company',dict(ticker='AAPL'),**kwargs)
            self.assertEqual(err.exception.code,403)
    def test_end_to_end_api(self):
        self.assertIn(self.app.token.encode(),self.req('/'))
        co=json.loads(self.req('/api/company',dict(ticker='GOOGL')));self.req('/api/activate',dict(id=co['id']))
        self.req('/api/fact',dict(metric='revenue',value=1e9,start='2024-01-01',end='2024-12-31',filed='2025-02-01',url='https://www.sec.gov/example',location='test fixture only'))
        p=json.loads(self.req('/api/pack',{}));self.req('/api/answer',dict(pack_id=p['id'],body=dict(responses=['a','b','c'],counter='d',falsification='e',metrics={})))
        self.req('/api/complete',dict(pack_id=p['id']))
        state=json.loads(self.req('/api/state'));self.assertEqual(state['active']['stage'],1)
        self.assertEqual(state['packs'][0]['status'],'COMPLETED')
    def test_path_traversal(self):
        for path in ['/downloads/../app.py','/data/research.sqlite3','/downloads/%2e%2e%2fapp.py']:
            with self.assertRaises(urllib.error.HTTPError):self.req(path)

if __name__=='__main__':unittest.main(verbosity=2)
