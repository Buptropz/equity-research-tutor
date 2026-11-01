import base64
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock
import zipfile
import json

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Application
from research import Store,stamp
from context_helpers import context_for
from translation import import_document,start_translation,TranslationWorker,recheck_saved_translations,export_translation
from translation_quality import check_translation
from maintenance import create_backup,import_backup,restore_backup,list_backups

class ContextFixTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=Application(Path(self.temp.name)/'data');self.s=self.app.store
        self.a=self.s.add_company('AAPL');self.b=self.s.add_company('NVDA');self.s.activate(self.a['id'])
        self.s.set_settings(dict(model_enabled=True,model_provider='codex'))
    def tearDown(self):self.temp.cleanup()
    def fact(self):return dict(metric='revenue',value=100,start='2024-01-01',end='2024-12-31',filed='2025-02-01',url='https://www.sec.gov/example')
    def test_stale_company_rejected_for_fact_report_valuation_and_ai(self):
        context=context_for(self.s);self.s.activate(self.b['id'])
        for path,body in [('/api/fact',self.fact()),('/api/document-import',dict(title='Apple report',kind='text',text='Apple revenue 100.')),('/api/valuation',{}),('/api/ai-chat',dict(message='Review Apple'))]:
            with self.subTest(path=path),mock.patch('app.codex_text') as model:
                with self.assertRaisesRegex(ValueError,'其他页面切换'):self.app.action(path,dict(body,_context=context))
                model.assert_not_called()
        with self.s.connect() as c:
            for table in ['facts','documents','valuations','ai_messages']:self.assertEqual(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
    def test_context_required(self):
        with self.assertRaisesRegex(ValueError,'页面版本'):self.app.action('/api/fact',self.fact())
    def test_unassigned_page_cannot_silently_import_under_new_company(self):
        with self.s.connect() as c:c.execute('UPDATE settings SET value=? WHERE key=?',('null','active_id'))
        context=context_for(self.s);self.s.activate(self.a['id'])
        with self.assertRaisesRegex(ValueError,'其他页面切换'):self.app.action('/api/document-import',dict(title='Unassigned',kind='text',text='Revenue 100.',_context=context))
    def test_same_company_stale_evidence_rejected(self):
        context=context_for(self.s);self.s.manual_fact(self.fact())
        with self.assertRaisesRegex(ValueError,'财务资料已更新'):self.app.action('/api/fact',dict(self.fact(),_context=context))
        with self.s.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0],1)
    def test_stale_task_revision_rejected(self):
        self.s.manual_fact(self.fact());pack=self.s.pack();context=context_for(self.s)
        self.s.manual_fact(dict(self.fact(),value=200));self.s.refresh_pack()
        with self.assertRaisesRegex(ValueError,'资料版本已更新'):self.app.action('/api/answer',dict(pack_id=pack['id'],body={},_context=context))
    def test_valid_context_saves_original_company(self):
        result=self.app.action('/api/fact',dict(self.fact(),_context=context_for(self.s)))
        self.assertEqual(self.s.get_facts(result['snapshot_id'])[0]['company_id'],self.a['id'])
    def test_company_is_pinned_during_long_model_call(self):
        context=context_for(self.s)
        def reply(*args):
            self.assertEqual(args[1]['company']['ticker'],'AAPL')
            self.s.activate(self.b['id'])
            return 'Apple response'
        with mock.patch('app.codex_text',side_effect=reply):self.app.action('/api/ai-chat',dict(message='Review Apple',_context=context))
        self.assertEqual(self.s.company()['id'],self.b['id'])
        with self.s.connect() as c:self.assertEqual([r['company_id'] for r in c.execute('SELECT * FROM ai_messages')],[self.a['id'],self.a['id']])
    def test_company_is_pinned_during_pdf_extraction(self):
        context=context_for(self.s)
        def read(*args):self.s.activate(self.b['id']);return dict(text='Revenue 100.',pages=1,empty_pages=[])
        with mock.patch('translation.read_pdf',side_effect=read):
            result=self.app.action('/api/document-import',dict(title='Apple report',kind='file',filename='sample.pdf',content_base64=base64.b64encode(b'PDF fixture').decode(),_context=context))
        with self.s.connect() as c:self.assertEqual(c.execute('SELECT company_id FROM documents WHERE id=?',(result['id'],)).fetchone()[0],self.a['id'])
        self.assertEqual(self.s.company()['id'],self.b['id'])

class TranslationFixTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name)/'data')
        self.s.set_settings(dict(model_enabled=True,model_provider='codex'))
        self.worker=TranslationWorker(self.s,threading.Event())
    def tearDown(self):self.temp.cleanup()
    def doc(self,text):return import_document(self.s,dict(kind='text',title='Synthetic fixture',text=text))
    def test_unchanged_english_is_failure_not_success(self):
        original='Management continues to monitor operating expenses.';doc=self.doc(original);start_translation(self.s,doc['id'])
        with mock.patch('translation.codex_text',return_value=original):self.worker.tick()
        doc=self.s.state()['documents'][0]
        self.assertEqual(doc['job']['status'],'FAILED');self.assertEqual(doc['chunks'][0]['status'],'FAILED')
        self.assertEqual(doc['chunks'][0]['translated'],original)
    def test_english_refusal_and_short_summary_rejected(self):
        source='Revenue increased steadily. '+('Management reviews operating performance and allocation. '*30)
        for target in ["I cannot translate this report.",'收入增加。']:
            with self.subTest(target=target):self.assertFalse(check_translation(source,target)['content']['ok'])
    def test_currency_and_unit_change_flagged(self):
        result=check_translation('Revenue was $100 million.','收入为€100千。')
        self.assertTrue(result['numbers']['ok']);self.assertFalse(result['ok']);self.assertFalse(result['currency']['ok']);self.assertFalse(result['units']['ok'])
    def test_metrics_with_same_numbers_swapped_flagged(self):
        result=check_translation('Revenue 100; profit 20.','收入20；利润100。')
        self.assertTrue(result['numbers']['ok']);self.assertFalse(result['rows']['ok'])
    def test_currency_assignment_swapped_flagged(self):
        result=check_translation('Revenue USD 100; expenses EUR 20.','收入EUR100；费用USD20。')
        self.assertTrue(result['numbers']['ok']);self.assertFalse(result['currency']['ok'])
    def test_unit_assignment_swapped_flagged(self):
        result=check_translation('Revenue 100 million; expenses 20 thousand.','收入100千；费用20百万。')
        self.assertTrue(result['numbers']['ok']);self.assertFalse(result['units']['ok'])
    def test_valid_currency_units_and_year_reordering_pass(self):
        for original,translated in [('Revenue for 2024 was $1,250.5 million, up 12% from 2023.','2024年收入为$1,250.5百万，较2023年增长12%。'),('Operating loss (100) million.','营业亏损（100）百万。'),('Revenue $100 million.','收入100百万美元。'),('2024 2023\n100 80','2024 2023\n100 80')]:
            with self.subTest(original=original):self.assertTrue(check_translation(original,translated)['ok'],check_translation(original,translated)['issues'])
    def test_review_can_be_retranslated_without_losing_history(self):
        doc=self.doc('Revenue 100; profit 20.');start_translation(self.s,doc['id'])
        with mock.patch('translation.codex_text',return_value='收入20；利润100。'):self.worker.tick()
        self.assertEqual(self.s.state()['documents'][0]['chunks'][0]['status'],'REVIEW')
        start_translation(self.s,doc['id'],1,retry=True)
        with mock.patch('translation.codex_text',return_value='收入100；利润20。'):self.worker.tick()
        chunk=self.s.state()['documents'][0]['chunks'][0]
        self.assertEqual(chunk['status'],'DONE');self.assertEqual(chunk['versions'][0]['translated'],'收入20；利润100。')
        self.assertEqual(chunk['versions'][0]['status'],'REVIEW')
        file=export_translation(self.s,doc['id'])['files'][0]
        self.assertIn('收入20；利润100。',(self.s.folder.parent/'exports'/file).read_text(encoding='utf-8-sig'))
    def test_old_translations_rechecked_without_model_or_overwrite(self):
        source='Revenue increased as management continued to monitor demand.';doc=self.doc(source)
        with self.s.connect() as c:c.execute('UPDATE translation_chunks SET translated=?,status=?,number_check=? WHERE document_id=?',(source,'DONE',json.dumps(dict(ok=True,missing=[],added=[])),doc['id']))
        with mock.patch('translation.codex_text') as model:
            self.assertEqual(recheck_saved_translations(self.s),1);model.assert_not_called()
        chunk=self.s.state()['documents'][0]['chunks'][0]
        self.assertEqual(chunk['status'],'FAILED');self.assertEqual(chunk['translated'],source);self.assertEqual(chunk['original'],source)
        self.assertEqual(chunk['versions'][0]['status'],'DONE');self.assertEqual(recheck_saved_translations(self.s),0)

class BackupCapacityFixTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name)/'data')
    def tearDown(self):self.temp.cleanup()
    def test_over_budget_create_import_restore_all_rejected(self):
        name=create_backup(self.s);file=self.s.folder/'backups'/name;raw=file.read_bytes()
        with zipfile.ZipFile(file) as z:expanded=sum(i.file_size for i in z.infolist())
        with mock.patch('maintenance.MAX_BACKUP_BYTES',expanded-1):
            for action in [lambda:create_backup(self.s),lambda:import_backup(self.s,base64.b64encode(raw).decode()),lambda:restore_backup(self.s,name)]:
                with self.assertRaisesRegex(ValueError,'超过可恢复范围'):action()
        self.assertEqual(len(list_backups(self.s)),1);self.assertEqual(list((self.s.folder/'backups').glob('*.part')),[])
    def test_manifest_counts_towards_file_budget(self):
        with mock.patch('maintenance.MAX_BACKUP_FILES',1):
            with self.assertRaisesRegex(ValueError,'超过可恢复范围'):create_backup(self.s)
        self.assertEqual(list_backups(self.s),[])
    def test_archive_at_shared_budget_can_restore(self):
        with mock.patch('maintenance.stamp',return_value='2026-11-01T10:00:00+08:00'):
            name=create_backup(self.s)
            with zipfile.ZipFile(self.s.folder/'backups'/name) as z:expanded=sum(i.file_size for i in z.infolist())
            with mock.patch('maintenance.MAX_BACKUP_BYTES',expanded):
                next_name=create_backup(self.s)
                restore_backup(self.s,next_name)
        with self.s.connect() as c:self.assertEqual(c.execute('PRAGMA quick_check').fetchone()[0],'ok')

if __name__=='__main__':unittest.main(verbosity=2)
