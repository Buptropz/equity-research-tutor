import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research import Store
from ai_backend import codex_text
from app import ai_chat, model_review

class AITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'data')
        self.store.set_settings(dict(model_enabled=True,model_provider='codex',codex_model='gpt-6.1-sol'))
    def tearDown(self):self.temp.cleanup()
    def test_cli_args_and_result(self):
        def run(args,**kwargs):
            Path(args[args.index('--output-last-message')+1]).write_text('连接成功',encoding='utf-8')
            self.assertIn('--ignore-user-config',args)
            self.assertIn('--ephemeral',args)
            self.assertEqual(args[args.index('--sandbox')+1],'read-only')
            self.assertIn('forced_login_method="chatgpt"',args)
            self.assertIn('shell_tool',args)
            self.assertIn('中文问题',kwargs['input'])
            self.assertNotIn('OPENAI_API_KEY',kwargs['env'])
            self.assertNotIn('CODEX_API_KEY',kwargs['env'])
            return SimpleNamespace(returncode=0)
        with mock.patch('ai_backend.find_codex',return_value='codex.exe'),mock.patch('ai_backend.subprocess.run',side_effect=run):
            self.assertEqual(codex_text('导师',{'q':'中文问题'},'gpt-6.1-sol',Path(self.temp.name)/'runs'),'连接成功')
    def test_chat_persistence_and_company_isolation(self):
        with mock.patch('app.codex_text',return_value='通用解释'):
            ai_chat(self.store,'你好')
        self.assertEqual(len(self.store.state()['chats']),2)
        company=self.store.add_company('AAPL');self.store.activate(company['id'])
        self.assertEqual(self.store.state()['chats'],[])
        with mock.patch('app.codex_text',return_value='公司问题反馈') as model:
            ai_chat(self.store,'解释财报')
            self.assertEqual(model.call_args.args[1]['company']['ticker'],'AAPL')
        self.assertEqual(len(self.store.state()['chats']),2)
    def test_failure_does_not_save_fake_reply(self):
        with mock.patch('app.codex_text',side_effect=ValueError('连接失败')):
            with self.assertRaises(ValueError):ai_chat(self.store,'测试')
        self.assertEqual(self.store.state()['chats'],[])
    def test_disabled_does_not_call(self):
        self.store.set_settings(dict(model_enabled=False))
        with mock.patch('app.codex_text') as call:
            with self.assertRaises(ValueError):ai_chat(self.store,'你好')
            call.assert_not_called()
    def test_review_uses_codex_and_keeps_history(self):
        co=self.store.add_company('AAPL');self.store.activate(co['id']);pack=self.store.pack()
        self.store.answer(pack['id'],dict(responses=['观察','解释','判断'],counter='反方',falsification='条件',metrics={}))
        with mock.patch('app.codex_text',return_value='证据不足，请补充来源'),mock.patch('app.urllib.request.urlopen') as api:
            model_review(self.store,pack['id']);model_review(self.store,pack['id'])
            api.assert_not_called()
        answer=self.store.state()['packs'][0]['answers'][0]
        self.assertEqual(len(answer['feedback']['model_reviews']),2)
        self.assertEqual(answer['body']['responses'][0],'观察')

if __name__=='__main__':unittest.main()
