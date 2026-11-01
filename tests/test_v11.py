import base64
import concurrent.futures
from contextlib import closing
import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research import Store,now,stamp
from valuation import DEFAULTS,dcf,scenarios
from app import Application,ai_chat,report_data
from maintenance import create_backup,restore_backup,list_backups,import_backup
from translation import import_document,start_translation,TranslationWorker,verify_numbers,split_text,extract_html,export_translation,document

class V11Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'data')
        self.co=self.store.add_company('AAPL');self.store.activate(self.co['id'])
    def tearDown(self):self.temp.cleanup()
    def fact(self,metric='revenue',value=100e6):
        return self.store.manual_fact(dict(metric=metric,value=value,start='2024-01-01',end='2024-12-31',filed='2025-02-01',url='https://www.sec.gov/example',location='test fixture'))
    def body(self):return dict(responses=['original-a','b','c'],counter='d',falsification='e',metrics=dict(revenue=100,operating_income=100,cfo=100,capex=100))
    def test_low_margin_base_unchanged(self):
        for margin in [.005,.00001,.01,.99]:
            a=dict(DEFAULTS,margin=margin);base=scenarios(a)['cases'][1]
            self.assertEqual(base['assumptions']['margin'],margin)
            self.assertAlmostEqual(base['per_share'],dcf(a)['per_share'])
    def test_revised_task_resumes_without_overwriting_old_answer(self):
        with self.store.connect() as c:c.execute('UPDATE companies SET stage=1 WHERE id=?',(self.co['id'],))
        original=self.fact();pack=self.store.pack();self.store.answer(pack['id'],self.body())
        with self.assertRaises(ValueError):self.store.complete(pack['id'])
        for metric in ['operating_income','cfo','capex']:self.fact(metric)
        new=self.store.refresh_pack();self.assertEqual(new['revision'],2)
        with self.assertRaisesRegex(ValueError,'当前资料版本'):self.store.complete(pack['id'])
        self.store.answer(pack['id'],self.body());self.store.complete(pack['id'])
        answers=self.store.state()['packs'][0]['answers']
        self.assertEqual(len(answers),2);self.assertEqual(answers[0]['snapshot_id'],original)
        self.assertNotEqual(answers[0]['snapshot_id'],answers[1]['snapshot_id'])
    def test_correction_changes_only_new_snapshot(self):
        first=self.fact(value=100);second=self.fact(value=200)
        self.assertEqual([f['value'] for f in self.store.get_facts(first)],[100])
        self.assertEqual([f['value'] for f in self.store.get_facts(second)],[200])
    def test_legacy_duplicate_gets_latest(self):
        sid=self.fact(value=100)
        with self.store.connect() as c:
            c.execute('INSERT INTO facts(snapshot_id,company_id,metric,value,unit,start,end,filed,form,tag,url,accession,status) SELECT snapshot_id,company_id,metric,200,unit,start,end,filed,form,tag,url,accession,status FROM facts WHERE snapshot_id=?',(sid,))
        self.assertEqual([f['value'] for f in self.store.get_facts(sid)],[200])
    def test_ai_can_see_answers_and_export_all_chats(self):
        self.fact();pack=self.store.pack();self.store.answer(pack['id'],self.body())
        self.store.set_settings(dict(model_enabled=True,model_provider='codex'))
        with mock.patch('app.codex_text',return_value='full-ai-reply') as model:
            ai_chat(self.store,'查看我的回答')
            self.assertIn('original-a',json.dumps(model.call_args.args[1]))
            self.assertEqual(model.call_args.args[1]['study_answers'][0]['snapshot_id'],pack['snapshot_id'])
        with self.store.connect() as c:
            for i in range(80):c.execute('INSERT INTO ai_messages(company_id,role,content,model,created) VALUES(?,?,?,?,?)',(self.co['id'],'user',f'message-{i}','test',stamp()))
        _,report,ledger=report_data(self.store)
        self.assertEqual(len(ledger['chats']),82);self.assertIn('full-ai-reply',report)
    def test_automatic_refresh_does_not_rebase_submitted_task(self):
        first=self.fact();p=self.store.pack();self.store.answer(p['id'],self.body());self.fact(value=200)
        self.store.refresh_pack(unanswered_only=True)
        self.assertEqual(self.store.pack()['snapshot_id'],first)
    def test_unanswered_task_rebinds(self):
        self.store.pack();sid=self.fact();self.store.refresh_pack(unanswered_only=True)
        self.assertEqual(self.store.pack()['snapshot_id'],sid)
    def test_backups_roundtrip_and_safety_copy(self):
        self.fact();backup=create_backup(self.store)
        other=self.store.add_company('NVDA');self.assertEqual(len(self.store.state()['companies']),2)
        result=restore_backup(self.store,backup)
        self.assertEqual(len(self.store.state()['companies']),1)
        self.assertTrue((self.store.folder/'backups'/result['safety_backup']).exists())
        self.assertEqual(len(list_backups(self.store)),2)
    def test_backup_includes_documents_and_translations(self):
        doc=import_document(self.store,dict(title='annual',kind='text',text='Revenue 100 USD.'))
        name=create_backup(self.store)
        with zipfile.ZipFile(self.store.folder/'backups'/name) as z:
            self.assertTrue(any(p.startswith('documents/') for p in z.namelist()))
            self.assertIn('manifest.json',z.namelist())
    def test_restore_rejects_traversal_before_changing_data(self):
        folder=self.store.folder/'backups';folder.mkdir()
        with zipfile.ZipFile(folder/'bad.zip','w') as z:z.writestr('../escape.txt','bad');z.writestr('research.sqlite3','bad')
        with self.assertRaises(ValueError):restore_backup(self.store,'bad.zip')
        self.assertEqual(self.store.company()['ticker'],'AAPL')
        self.assertFalse((self.store.folder/'escape.txt').exists())
    def test_auto_backup_once_daily(self):
        a=create_backup(self.store,automatic=True);b=create_backup(self.store,automatic=True)
        self.assertEqual(a,b);self.assertEqual(len(list_backups(self.store)),1)
    def test_parallel_auto_backup_once(self):
        with concurrent.futures.ThreadPoolExecutor(4) as executor:
            names=list(executor.map(lambda _:create_backup(self.store,automatic=True),range(4)))
        self.assertEqual(len(set(names)),1)
    def test_imported_backup_does_not_restore_until_requested(self):
        original=create_backup(self.store);raw=(self.store.folder/'backups'/original).read_bytes()
        self.store.add_company('NVDA')
        imported=import_backup(self.store,base64.b64encode(raw).decode())
        self.assertEqual(len(self.store.state()['companies']),2)
        restore_backup(self.store,imported['name'])
        self.assertEqual(len(self.store.state()['companies']),1)
    def test_tampered_backup_rejected(self):
        original=create_backup(self.store);file=self.store.folder/'backups'/original
        with zipfile.ZipFile(file) as z:items={p:z.read(p) for p in z.namelist()}
        items['research.sqlite3']=b'corrupted'
        with zipfile.ZipFile(file,'w') as z:
            for name,value in items.items():z.writestr(name,value)
        with self.assertRaisesRegex(ValueError,'完整性'):restore_backup(self.store,original)
        self.assertEqual(self.store.company()['ticker'],'AAPL')
    def test_stop_rejects_active_translation(self):
        app=Application(self.store.folder)
        with app.store.connect() as c:c.execute('INSERT INTO jobs(document_id,status,created) VALUES(?,?,?)',(1,'PENDING',stamp()))
        with self.assertRaisesRegex(ValueError,'暂停翻译'):app.action('/api/stop',{})
    def test_daily_retry_deferred_and_notification_not_duplicated(self):
        app=Application(self.store.folder)
        at=now().replace(hour=12,minute=0,second=0,microsecond=0)
        app.store.set_settings(dict(reminder_enabled=True,start_date=at.date().isoformat(),sec_identity='Fixture test@example.com'))
        with mock.patch('app.fetch_sec',side_effect=ValueError('offline')) as fetch:
            app.daily_tick(at);app.daily_tick(at+dt.timedelta(minutes=1))
            self.assertEqual(fetch.call_count,1)
            app.daily_tick(at+dt.timedelta(minutes=6));self.assertEqual(fetch.call_count,2)
        self.assertEqual(len(app.store.state()['notifications']),1)
    def test_additive_migration_keeps_old_evidence_cutoff(self):
        # Simulate a V1.0 SQLite archive without revision columns.
        folder=Path(self.temp.name)/'old';folder.mkdir()
        with closing(sqlite3.connect(folder/'research.sqlite3')) as c:
            c.executescript('CREATE TABLE packs(id INTEGER PRIMARY KEY,company_id INTEGER,snapshot_id INTEGER,study_date TEXT,stage INTEGER,status TEXT,payload TEXT,created TEXT,completed TEXT);CREATE TABLE answers(id INTEGER PRIMARY KEY,company_id INTEGER,pack_id INTEGER,body TEXT,feedback TEXT,created TEXT);INSERT INTO packs(id,snapshot_id) VALUES(1,12);INSERT INTO answers(id,pack_id) VALUES(1,1);');c.commit()
        upgraded=Store(folder)
        with upgraded.connect() as c:
            answer=c.execute('SELECT * FROM answers').fetchone();self.assertEqual(answer['snapshot_id'],12);self.assertEqual(answer['revision'],1)

class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'data')
        self.store.set_settings(dict(model_enabled=True,model_provider='codex',codex_model='test-model'))
        self.worker=TranslationWorker(self.store,threading.Event())
    def tearDown(self):self.temp.cleanup()
    def doc(self,text='Revenue was $1,250.5 million in 2024, up 12%.'):
        return import_document(self.store,dict(title='Annual report',kind='text',text=text,report_period='FY2024'))
    def test_numeric_check_exact(self):
        self.assertTrue(verify_numbers('Revenue $1,250.5 in 2024 up 12%.','2024年收入$1,250.5，增长12%。')['ok'])
        self.assertFalse(verify_numbers('Revenue 1,250.5','收入1250.5')['ok'])
        self.assertFalse(verify_numbers('Income (100)','收入(10)')['ok'])
        self.assertFalse(verify_numbers('Income (100)','收入100')['ok'])
        self.assertTrue(verify_numbers('In 2024, revenue was 10.','2024年，收入10。')['ok'])
    def test_html_ignores_hidden_and_preserves_cells(self):
        text=extract_html('<script>secret 999</script><ix:hidden>hidden 555</ix:hidden><h1>Income</h1><table><tr><th>Year</th><th>2024</th></tr><tr><td>Revenue</td><td>1,234</td></tr></table>')
        self.assertNotIn('999',text);self.assertNotIn('555',text);self.assertIn('1,234',text);self.assertIn('\t',text)
    def test_chunks_preserve_every_nonwhitespace_character(self):
        text=('Revenue 1,234 and expenses 100.\n\n'*400)
        chunks=split_text(text)
        self.assertGreater(len(chunks),1)
        self.assertEqual(''.join(text.split()),''.join(''.join(chunks).split()))
    def test_translation_saved_and_numeric_mismatch_flagged(self):
        doc=self.doc();start_translation(self.store,doc['id'])
        with mock.patch('translation.codex_text',return_value='收入1,250.5，但没有年份和百分比。'):
            self.worker.tick()
        chunk=self.store.state()['documents'][0]['chunks'][0]
        self.assertEqual(chunk['status'],'REVIEW');self.assertIn('2024',json.loads(chunk['number_check'])['missing'])
        self.assertIn('$1,250.5',chunk['original'])
    def test_failure_resume_skips_finished_chunks(self):
        doc=self.doc(('Revenue 100.\n'*400))
        start_translation(self.store,doc['id'])
        count=0
        def fail_after_first(*args):
            nonlocal count
            count+=1
            if count>1:raise ValueError('offline')
            return args[1]['original']
        with mock.patch('translation.codex_text',side_effect=fail_after_first):self.worker.tick()
        state=self.store.state()['documents'][0];self.assertEqual(state['job']['status'],'FAILED')
        first=state['chunks'][0]['translated'];start_translation(self.store,doc['id'])
        with mock.patch('translation.codex_text',side_effect=lambda *a:a[1]['original']) as model:self.worker.tick()
        after=self.store.state()['documents'][0];self.assertEqual(after['job']['status'],'COMPLETED')
        self.assertEqual(after['chunks'][0]['translated'],first)
        self.assertEqual(model.call_count,len(after['chunks'])-1)
    def test_restart_pauses_inflight_job(self):
        doc=self.doc();job=start_translation(self.store,doc['id'])
        with self.store.connect() as c:c.execute("UPDATE jobs SET status='RUNNING' WHERE id=?",(job['job_id'],))
        TranslationWorker(self.store,threading.Event())
        self.assertEqual(self.store.state()['documents'][0]['job']['status'],'PAUSED')
    def test_bilingual_export_keeps_original_and_status(self):
        doc=self.doc();start_translation(self.store,doc['id'])
        with mock.patch('translation.codex_text',return_value='2024年收入为$1,250.5百万，增长12%。'):self.worker.tick()
        result=export_translation(self.store,doc['id']);file=self.store.folder.parent/'exports'/result['files'][0]
        text=file.read_text(encoding='utf-8-sig');self.assertIn('### 原文',text);self.assertIn('### 中文译文',text);self.assertIn('2024年',text)
    def test_invalid_source_and_file_rejected(self):
        with self.assertRaises(ValueError):import_document(self.store,dict(title='x',kind='sec',source_url='https://127.0.0.1/private'))
        with self.assertRaises(ValueError):import_document(self.store,dict(title='x',kind='file',filename='file.exe',content_base64=base64.b64encode(b'bad').decode()))
    def test_general_document_remains_visible_then_can_be_assigned(self):
        doc=self.doc();co=self.store.add_company('AAPL');self.store.activate(co['id'])
        self.assertEqual(document(self.store,doc['id'])['company_id'],None)
        self.assertEqual(len(self.store.state()['documents']),1)
        app=Application(self.store.folder);app.action('/api/document-assign',dict(document_id=doc['id']))
        self.assertEqual(document(self.store,doc['id'])['company_id'],co['id'])
        other=self.store.add_company('NVDA');self.store.activate(other['id'])
        with self.assertRaises(ValueError):document(self.store,doc['id'])

if __name__=='__main__':unittest.main(verbosity=2)
