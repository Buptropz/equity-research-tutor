"""Local evidence, immutable snapshots and explicit research progression."""
import datetime as dt
import hashlib
import json
import re
import sqlite3
import threading
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

TZ = dt.timezone(dt.timedelta(hours=8))
STAGES = ['理解企业', '阅读财报', '研究增长', '建立估值', '形成判断', '长期复盘']
QUESTIONS = [
    ['这家公司向谁提供什么产品或服务？请定位收入分部披露。', '最主要的收入来源是什么？竞争优势有什么证据？', '什么变化可能削弱这种优势？写出可观察的指标。'],
    ['定位同一年度的收入、营业利润、经营现金流和资本开支，核对单位。', '利润与经营现金流为什么不同？至少引用一项附注。', '资本开支增加可能改善还是削弱未来回报？列出反方解释。'],
    ['找出两个可观察的增长驱动及其官方证据。', '增长需要多少再投资？历史ROIC能否持续？', '区分事实和假设，写出增长不及预期的条件。'],
    ['选择增长、利润率、ROIC和WACC假设，并说明依据。', '比较三情景及敏感性结果：哪项假设最影响估值？', '解释终值占比、SBC稀释与EV到股权价值桥接的限制。'],
    ['用证据写出投资论点，并注明尚未解决的问题。', '提出一个有说服力的反方解释，哪些证据支持它？', '写出可观察、可追踪的证伪条件。'],
    ['比较原始判断和新披露，注明两个快照的时点。', '区分事实误读、预测偏差和估值假设变化。', '写出观点修订依据及下一次复盘触发条件。']]
HINTS = [
    ['从10-K的Item 1（Business）与分部附注开始。', '护城河需由留存率、定价能力、成本或转换成本等证据支持。', '比较不同年份和分部，寻找能推翻优势假设的反例。'],
    ['打开利润表和现金流量表；CFO和CapEx通常均为全年累计数。', 'CFO与利润之差可能来自折旧、SBC和营运资本变化；CFO减CapEx不自动等于FCFF。', 'SBC是非现金费用但可能稀释股东；别把加回SBC直接当作免费现金流。'],
    ['查看收入分解、经营讨论和资本配置披露。', '稳态下增长≈再投资率×ROIC；高增长需要投入资本。', '把量、价、产品组合分开，测试增长驱动是否可持续。'],
    ['先核对输入的币种、期间及百万单位，再选择假设。', 'FCFF=NOPAT−净再投资；模型以增长/ROIC估计再投资率。终值使用单独的稳态ROIC。', 'WACC必须大于永续增长率；反向DCF固定其他假设，增长率可能多解。'],
    ['每条论点对应来源；将假设写成可以被检验的句子。', '优秀企业不一定在任意价格下值得投资；区分企业质量与价格假设。', '用最强的反方观点检验自己，保留尚未解决的证据冲突。'],
    ['先回看原始日期及当时已公开的资料。', '未来披露不能回填旧快照；修订应新建版本。', '把预测误差拆分为量、价、利润率、资本需求及估值因素。']]

def now():
    return dt.datetime.now(TZ)

def stamp():
    return now().isoformat(timespec='seconds')

class Store:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db = self.folder / 'research.sqlite3'
        self.lock = threading.RLock()
        with self.connect() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY,ticker TEXT UNIQUE,name TEXT,cik TEXT,stage INTEGER DEFAULT 0,created TEXT);
            CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY,company_id INTEGER,as_of TEXT,created TEXT,status TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY,snapshot_id INTEGER,company_id INTEGER,metric TEXT,value REAL,unit TEXT,start TEXT,end TEXT,filed TEXT,form TEXT,tag TEXT,url TEXT,accession TEXT,status TEXT);
            CREATE TABLE IF NOT EXISTS packs(id INTEGER PRIMARY KEY,company_id INTEGER,snapshot_id INTEGER,study_date TEXT,stage INTEGER,status TEXT,payload TEXT,created TEXT,completed TEXT,UNIQUE(company_id,study_date));
            CREATE TABLE IF NOT EXISTS answers(id INTEGER PRIMARY KEY,company_id INTEGER,pack_id INTEGER,body TEXT,feedback TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS hints(id INTEGER PRIMARY KEY,pack_id INTEGER,level INTEGER,created TEXT);
            CREATE TABLE IF NOT EXISTS valuations(id INTEGER PRIMARY KEY,company_id INTEGER,snapshot_id INTEGER,payload TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY,company_id INTEGER,study_date TEXT,pack_id INTEGER,status TEXT,created TEXT,UNIQUE(company_id,study_date));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,kind TEXT,detail TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS review_items(id INTEGER PRIMARY KEY,company_id INTEGER,pack_id INTEGER,concept TEXT,status TEXT,created TEXT,reviewed TEXT);
            CREATE TABLE IF NOT EXISTS ai_messages(id INTEGER PRIMARY KEY,company_id INTEGER,role TEXT,content TEXT,model TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS daily_runs(company_id INTEGER,study_date TEXT,attempts INTEGER DEFAULT 0,status TEXT,next_retry TEXT,error TEXT,PRIMARY KEY(company_id,study_date));
            CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY,company_id INTEGER,title TEXT,source_url TEXT,source_kind TEXT,report_period TEXT,sha256 TEXT,text TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS translation_chunks(id INTEGER PRIMARY KEY,document_id INTEGER,ordinal INTEGER,original TEXT,translated TEXT,status TEXT DEFAULT 'PENDING',number_check TEXT,error TEXT,model TEXT,updated TEXT,UNIQUE(document_id,ordinal));
            CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,document_id INTEGER,status TEXT,completed INTEGER DEFAULT 0,total INTEGER,error TEXT,created TEXT,updated TEXT);
            ''')
            # Additive migration; existing answers keep the evidence cutoff they were submitted with.
            for table,name,definition in [('packs','revision','INTEGER DEFAULT 1'),('answers','snapshot_id','INTEGER'),('answers','revision','INTEGER DEFAULT 1'),('jobs','scope','TEXT')]:
                if name not in [r['name'] for r in c.execute('PRAGMA table_info('+table+')')]:
                    c.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+definition)
                    if table=='answers' and name=='snapshot_id':
                        c.execute('UPDATE answers SET snapshot_id=(SELECT snapshot_id FROM packs WHERE packs.id=answers.pack_id)')
            c.execute('PRAGMA user_version=11')
            defaults = {'active_id': None, 'reminder_enabled': False, 'reminder_time': '10:00',
                        'frequency': 'daily', 'start_date': '2026-11-02', 'sec_identity': '',
                        'mode': 'tutor', 'model_enabled': False, 'model_url': '', 'model_name': '',
                        'model_provider':'api','codex_model':''}
            for k,v in defaults.items():
                c.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', (k,json.dumps(v)))

    @contextmanager
    def connect(self):
        with self.lock:
            c = sqlite3.connect(self.db, timeout=20)
            c.row_factory = sqlite3.Row
            c.execute('PRAGMA foreign_keys=ON')
            try:
                with c:
                    yield c
            finally:
                c.close()

    def settings(self, c=None):
        if c is None:
            with self.connect() as c:
                return self.settings(c)
        return {r['key']: json.loads(r['value']) for r in c.execute('SELECT * FROM settings')}

    def set_settings(self, data):
        allowed = {'reminder_enabled','reminder_time','frequency','start_date','sec_identity','mode','model_enabled','model_url','model_name','model_provider','codex_model'}
        if set(data) - allowed:
            raise ValueError('未知设置项')
        if 'reminder_time' in data and not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', str(data['reminder_time'])):
            raise ValueError('提醒时间格式应为HH:MM')
        if 'start_date' in data:
            dt.date.fromisoformat(data['start_date'])
        if 'frequency' in data and data['frequency'] not in ['daily','weekdays']:
            raise ValueError('频率应为每日或工作日')
        if 'mode' in data and data['mode'] not in ['tutor','assistant']:
            raise ValueError('未知交互模式')
        if 'model_provider' in data and data['model_provider'] not in ['api','codex']:
            raise ValueError('未知AI接入方式')
        for key in ['model_name','codex_model']:
            if key in data and (not isinstance(data[key],str) or len(data[key])>100):
                raise ValueError('模型名称无效')
        if data.get('codex_model') and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,99}',data['codex_model']):
            raise ValueError('Codex模型名称格式无效')
        for k in ['reminder_enabled','model_enabled']:
            if k in data and type(data[k]) is not bool:
                raise ValueError('开关值无效')
        if data.get('model_url'):
            from urllib.parse import urlparse
            u=urlparse(data['model_url'])
            if u.scheme != 'https' and not (u.scheme=='http' and u.hostname in ['127.0.0.1','localhost']):
                raise ValueError('模型接口须为HTTPS，或本机HTTP地址')
        with self.connect() as c:
            for k,v in data.items():
                c.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(v),k))
        return self.settings()

    def add_company(self, ticker, name='', cik=''):
        ticker=ticker.strip().upper()
        if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,11}',ticker):
            raise ValueError('请输入有效的美股代码，例如GOOGL')
        known = {'GOOGL':('Alphabet','1652044'),'GOOG':('Alphabet','1652044'), 'NVDA':('NVIDIA','1045810'),'AVGO':('Broadcom','1730168'),'AAPL':('Apple','320193'),'MSFT':('Microsoft','789019')}
        preset=known.get(ticker, (ticker,''))
        name=(name.strip() or preset[0])[:100]
        cik=(str(cik).strip() or preset[1]).lstrip('0')
        if cik and (not cik.isdigit() or len(cik)>10):
            raise ValueError('CIK应为不超过10位的数字')
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO companies(ticker,name,cik,created) VALUES(?,?,?,?)',(ticker,name,cik,stamp()))
            return dict(c.execute('SELECT * FROM companies WHERE ticker=?',(ticker,)).fetchone())

    def activate(self, company_id):
        with self.lock,self.connect() as c:
            if not c.execute('SELECT 1 FROM companies WHERE id=?',(company_id,)).fetchone():
                raise ValueError('公司不存在')
            c.execute('UPDATE settings SET value=? WHERE key="active_id"',(json.dumps(int(company_id)),))

    def company(self, company_id=None):
        company_id=company_id or self.settings()['active_id']
        with self.connect() as c:
            row=c.execute('SELECT * FROM companies WHERE id=?',(company_id,)).fetchone()
            return dict(row) if row else None

    def latest_snapshot(self, company_id):
        with self.connect() as c:
            row=c.execute('SELECT * FROM snapshots WHERE company_id=? ORDER BY id DESC LIMIT 1',(company_id,)).fetchone()
            return dict(row) if row else None

    def get_facts(self,snapshot_id):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM facts WHERE id IN (SELECT MAX(id) FROM facts WHERE snapshot_id=? GROUP BY metric,unit,start,end) ORDER BY end DESC,metric',(snapshot_id,))]

    def pack(self, day=None):
        company=self.company()
        if not company:
            raise ValueError('请先选择当前研究股票')
        day=day or now().date().isoformat()
        dt.date.fromisoformat(day)
        with self.lock, self.connect() as c:
            # Resuming an unfinished task never creates a new accumulating task.
            old=c.execute('SELECT * FROM packs WHERE company_id=? AND status!=? ORDER BY id DESC LIMIT 1',(company['id'],'COMPLETED')).fetchone()
            if old:
                return dict(old)
            old=c.execute('SELECT * FROM packs WHERE company_id=? AND study_date=?',(company['id'],day)).fetchone()
            if old:
                return dict(old)
            if company['stage']>=len(STAGES):
                raise ValueError('本轮六阶段已完成。请手动选择复盘或切换公司。')
            snap=self.latest_snapshot(company['id'])
            facts=self.get_facts(snap['id']) if snap else []
            ready=any(f['status']=='official_xbrl' for f in facts)
            payload=dict(topic=STAGES[company['stage']], questions=QUESTIONS[company['stage']], minutes=45,
                         completion='填写三道问题、一个反方解释及一个可观察证伪条件；核对证据后手动确认完成。',
                         verification='官方XBRL已提取，仍需核对原表与附注' if ready else '资料待核实：请抓取官方披露或添加证据',
                         previous=(dict(c.execute('SELECT id,completed FROM packs WHERE company_id=? AND status=? ORDER BY id DESC LIMIT 1',(company['id'],'COMPLETED')).fetchone()) if c.execute('SELECT 1 FROM packs WHERE company_id=? AND status=?',(company['id'],'COMPLETED')).fetchone() else None))
            c.execute('INSERT INTO packs(company_id,snapshot_id,study_date,stage,status,payload,created) VALUES(?,?,?,?,?,?,?)',
                      (company['id'],snap['id'] if snap else None,day,company['stage'],'MATERIALS_READY' if ready else 'BLOCKED',json.dumps(payload,ensure_ascii=False),stamp()))
            return dict(c.execute('SELECT * FROM packs WHERE id=last_insert_rowid()').fetchone())

    def refresh_pack(self, unanswered_only=False):
        p=self.pack()
        if p['status']=='COMPLETED':
            raise ValueError('当天任务已完成，旧任务不会被改写')
        snap=self.latest_snapshot(p['company_id'])
        if not snap:
            raise ValueError('尚无资料快照')
        with self.lock,self.connect() as c:
            answered=c.execute('SELECT 1 FROM answers WHERE pack_id=? AND revision=?',(p['id'],p['revision'])).fetchone()
            if snap['id']==p['snapshot_id'] or (unanswered_only and answered):
                return p
            payload=json.loads(p['payload'])
            official=any(f['status']=='official_xbrl' for f in self.get_facts(snap['id']))
            payload['verification']='官方XBRL已提取，仍需核对原表与附注' if official else '手动证据待核实'
            payload['verification']+='；更新资料后须提交本版本回答' if answered else ''
            c.execute('UPDATE packs SET snapshot_id=?,status=?,payload=?,revision=revision+1 WHERE id=?',(snap['id'],'MATERIALS_READY' if official else 'BLOCKED',json.dumps(payload,ensure_ascii=False),p['id']))
        return self.pack()

    def answer(self,pack_id,body):
        with self.lock,self.connect() as c:
            p=c.execute('SELECT * FROM packs WHERE id=?',(pack_id,)).fetchone()
            if not p or p['status']=='COMPLETED':
                raise ValueError('任务不存在或已完成；旧回答不会覆盖')
            if p['company_id'] != self.settings(c)['active_id']:
                raise ValueError('请先切换到该任务所属公司')
            answers=body.get('responses',[])
            if not isinstance(answers,list) or len(answers)!=3 or not all(isinstance(v,str) and v.strip() for v in answers):
                raise ValueError('请先填写全部三道思考题')
            if not all(isinstance(body.get(k),str) and body[k].strip() for k in ['counter','falsification']):
                raise ValueError('请填写反方解释和可观察的证伪条件')
            checks=[]
            facts=self.get_facts(p['snapshot_id']) if p['snapshot_id'] else []
            latest=max((f['end'] for f in facts),default=None)
            numeric=body.get('metrics',{})
            for key in ['revenue','operating_income','cfo','capex']:
                if numeric.get(key) not in ('',None):
                    expected=next((f for f in facts if f['metric']==key and f['end']==latest and f['status']=='official_xbrl'),None)
                    try:
                        value=float(numeric[key])
                        if not __import__('math').isfinite(value):
                            raise ValueError()
                    except (ValueError,TypeError):
                        raise ValueError('四项财务定位须填写有限数字，单位为百万美元')
                    correct=abs(value-expected['value']/1e6)<=max(.01,abs(expected['value']/1e6)*.0001) if expected else None
                    checks.append(dict(metric=key,correct=correct,error_type=None if correct else ('数值、期间或单位待核对' if expected else '证据不足')))
            feedback=dict(kind='规则检查',checks=checks,note='已保存原始回答。数值可与所绑定快照比较；文字推理尚需你审核，或主动请求模型点评。',
                          unresolved=['核对引用位置与原始财报','检查反方解释是否能挑战论点','确认不是只因做完题就视为完成尽调'])
            c.execute('INSERT INTO answers(company_id,pack_id,body,feedback,created,snapshot_id,revision) VALUES(?,?,?,?,?,?,?)',(p['company_id'],p['id'],json.dumps(body,ensure_ascii=False),json.dumps(feedback,ensure_ascii=False),stamp(),p['snapshot_id'],p['revision']))
            answer_id=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            concepts=body.get('review_concepts','')
            if not isinstance(concepts,str):
                raise ValueError('待复习概念请填写为文字')
            for concept in re.split(r'[,，、\n]',concepts):
                concept=concept.strip()[:100]
                if concept and not c.execute('SELECT 1 FROM review_items WHERE company_id=? AND concept=? AND status=?',(p['company_id'],concept,'OPEN')).fetchone():
                    c.execute('INSERT INTO review_items(company_id,pack_id,concept,status,created) VALUES(?,?,?,?,?)',(p['company_id'],p['id'],concept,'OPEN',stamp()))
            c.execute('UPDATE packs SET status=? WHERE id=?',('SUBMITTED',p['id']))
            return dict(id=answer_id, **feedback)

    def complete(self,pack_id):
        with self.lock,self.connect() as c:
            p=c.execute('SELECT * FROM packs WHERE id=?',(pack_id,)).fetchone()
            if not p:
                raise ValueError('任务不存在')
            if p['status']=='COMPLETED':
                return
            if p['company_id']!=self.settings(c)['active_id']:
                raise ValueError('请切换到任务所属公司')
            a=c.execute('SELECT * FROM answers WHERE pack_id=? AND revision=? ORDER BY id DESC LIMIT 1',(pack_id,p['revision'])).fetchone()
            if not a:
                raise ValueError('请先提交当前资料版本的回答，再审核并确认完成')
            facts=self.get_facts(p['snapshot_id']) if p['snapshot_id'] else []
            if not facts:
                raise ValueError('尚无证据，请先补充资料；已有回答将保留')
            if any(x.get('correct') is False for x in json.loads(a['feedback'])['checks']):
                raise ValueError('财务数值校验存在差异，请核对后提交新版本')
            if p['stage']==1:
                values=json.loads(a['body']).get('metrics',{})
                if any(values.get(k) in ('',None) for k in ['revenue','operating_income','cfo','capex']):
                    raise ValueError('阅读财报阶段须先提交四项财务定位结果，不能把空白计为已完成')
                latest=max(f['end'] for f in facts)
                if any(not any(f['metric']==k and f['end']==latest and f['status']!='conflict' for f in facts) for k in ['revenue','operating_income','cfo','capex']):
                    raise ValueError('任务快照中尚缺同一年度的四项核心事实，请先补齐证据')
            if p['stage']==3 and not c.execute('SELECT 1 FROM valuations WHERE company_id=? AND snapshot_id=?',(p['company_id'],p['snapshot_id'])).fetchone():
                raise ValueError('估值阶段需先保存与本任务快照绑定的估值版本')
            c.execute('UPDATE packs SET status=?,completed=? WHERE id=?',('COMPLETED',stamp(),pack_id))
            c.execute('UPDATE companies SET stage=MAX(stage,?) WHERE id=?',(p['stage']+1,p['company_id']))

    def manual_fact(self,data):
        co=self.company()
        if not co:
            raise ValueError('请先选择股票')
        import math
        from urllib.parse import urlparse
        value=float(data['value'])
        if not math.isfinite(value):
            raise ValueError('指标必须为有限数字')
        if data['metric'] not in METRICS:
            raise ValueError('未知指标')
        if urlparse(data['url']).scheme!='https' or not urlparse(data['url']).hostname:
            raise ValueError('来源须为HTTPS链接')
        start=dt.date.fromisoformat(data['start']); end=dt.date.fromisoformat(data['end']); filed=dt.date.fromisoformat(data['filed'])
        if not start<=end<=filed<=now().date():
            raise ValueError('须满足开始日≤结束日≤披露日≤今天')
        if not 350 <= (end-start).days <= 380:
            raise ValueError('首版仅支持年度事实；请勿将季度或年初至今数值混入年度记录')
        with self.lock,self.connect() as c:
            previous=self.latest_snapshot(co['id'])
            c.execute('INSERT INTO snapshots(company_id,as_of,created,status,payload) VALUES(?,?,?,?,?)',(co['id'],now().date().isoformat(),stamp(),'manual_unverified',json.dumps(dict(note='用户录入，未经官方抓取核验'),ensure_ascii=False)))
            sid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            if previous:
                c.execute('INSERT INTO facts(snapshot_id,company_id,metric,value,unit,start,end,filed,form,tag,url,accession,status) SELECT ?,company_id,metric,value,unit,start,end,filed,form,tag,url,accession,status FROM facts WHERE snapshot_id=? AND NOT(metric=? AND start=? AND end=? AND unit=?)',(sid,previous['id'],data['metric'],str(start),str(end),'USD'))
            c.execute('INSERT INTO facts(snapshot_id,company_id,metric,value,unit,start,end,filed,form,tag,url,accession,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(sid,co['id'],data['metric'],value,'USD',str(start),str(end),str(filed),'手动录入',str(data.get('location',''))[:500],data['url'],'','manual_unverified'))
        return sid

    def state(self):
        s=self.settings(); co=self.company()
        with self.connect() as c:
            companies=[dict(r) for r in c.execute('SELECT * FROM companies ORDER BY id')]
            packs=[dict(r) for r in c.execute('SELECT * FROM packs WHERE company_id=? ORDER BY id DESC',(co['id'] if co else None,))]
            for p in packs:
                p['payload']=json.loads(p['payload'])
                p['facts']=self.get_facts(p['snapshot_id'])
                p['answers']=[{**dict(a),'body':json.loads(a['body']),'feedback':json.loads(a['feedback'])} for a in c.execute('SELECT * FROM answers WHERE pack_id=? ORDER BY id',(p['id'],))]
                p['hint_count']=c.execute('SELECT COUNT(*) FROM hints WHERE pack_id=?',(p['id'],)).fetchone()[0]
            vals=[{**dict(r),'payload':json.loads(r['payload'])} for r in c.execute('SELECT * FROM valuations WHERE company_id=? ORDER BY id DESC',(co['id'] if co else None,))]
            notifications=[dict(r) for r in c.execute('SELECT * FROM notifications ORDER BY id DESC LIMIT 20')]
            events=[dict(r) for r in c.execute('SELECT * FROM events ORDER BY id DESC LIMIT 10')]
            reviews=[dict(r) for r in c.execute('SELECT * FROM review_items WHERE company_id=? ORDER BY id DESC',(co['id'] if co else None,))]
            chats=[dict(r) for r in c.execute('SELECT * FROM (SELECT * FROM ai_messages WHERE company_id IS ? ORDER BY id DESC LIMIT 60) ORDER BY id',(co['id'] if co else None,))]
            documents=[dict(r) for r in c.execute('SELECT id,company_id,title,source_url,source_kind,report_period,sha256,created FROM documents WHERE company_id IS ? OR company_id IS NULL ORDER BY id DESC',(co['id'] if co else None,))]
            for doc in documents:
                doc['chunks']=[dict(r) for r in c.execute('SELECT * FROM translation_chunks WHERE document_id=? ORDER BY ordinal',(doc['id'],))]
                job=c.execute('SELECT * FROM jobs WHERE document_id=? ORDER BY id DESC LIMIT 1',(doc['id'],)).fetchone()
                doc['job']=dict(job) if job else None
            daily_runs=[dict(r) for r in c.execute('SELECT * FROM daily_runs ORDER BY study_date DESC LIMIT 10')]
        snap=self.latest_snapshot(co['id']) if co else None
        return dict(settings=s,companies=companies,active=co,packs=packs,snapshot=snap,
                    facts=self.get_facts(snap['id']) if snap else [], valuations=vals,reviews=reviews,chats=chats,documents=documents,daily_runs=daily_runs,notifications=notifications,events=events,stages=STAGES,today=now().date().isoformat(),version='1.1')

    def schedule_tick(self, at=None):
        at=at or now(); s=self.settings()
        if not s['reminder_enabled'] or at.date().isoformat()<s['start_date'] or at.strftime('%H:%M')<s['reminder_time']:
            return False
        if s['frequency']=='weekdays' and at.weekday()>4:
            return False
        co=self.company()
        if not co or co['stage']>=6:
            return False
        with self.lock:
            p=self.pack(at.date().isoformat())
            if p['status']=='COMPLETED':
                return False
            with self.connect() as c:
                cur=c.execute('INSERT OR IGNORE INTO notifications(company_id,study_date,pack_id,status,created) VALUES(?,?,?,?,?)',(co['id'],at.date().isoformat(),p['id'],'应用内待查看',stamp()))
                return cur.rowcount>0

METRICS={
    'revenue':('营业收入',['RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet']),
    'operating_income':('营业利润',['OperatingIncomeLoss']),
    'cfo':('经营现金流',['NetCashProvidedByUsedInOperatingActivities']),
    'capex':('资本开支',['PaymentsToAcquirePropertyPlantAndEquipment']),
    'net_income':('净利润',['NetIncomeLoss']),
    'sbc':('股份支付',['ShareBasedCompensation']),
    'depreciation':('折旧与摊销',['DepreciationDepletionAndAmortization','DepreciationDepletionAndAmortizationPropertyPlantAndEquipment']),
    'rd':('研发费用',['ResearchAndDevelopmentExpense'])}

def extract_facts(companyfacts, submissions, as_of, cik):
    """Only original annual filings known by cutoff; no quarters/YTD mixing."""
    dt.date.fromisoformat(as_of)
    recent=submissions.get('filings',{}).get('recent',{})
    filings=[]
    for i,form in enumerate(recent.get('form',[])):
        if form=='10-K' and recent['filingDate'][i]<=as_of:
            filings.append({k:recent[k][i] for k in ['filingDate','reportDate','accessionNumber','primaryDocument']})
    filings=sorted(filings,key=lambda x:x['reportDate'],reverse=True)[:5]
    result=[]
    gaap=companyfacts.get('facts',{}).get('us-gaap',{})
    for filing in filings:
        for metric,(_,tags) in METRICS.items():
            candidates=[]
            for tag in tags:
                for item in gaap.get(tag,{}).get('units',{}).get('USD',[]):
                    if item.get('accn')!=filing['accessionNumber'] or item.get('form')!='10-K' or item.get('filed','9999')>as_of:
                        continue
                    if item.get('end')!=filing['reportDate'] or not item.get('start'):
                        continue
                    days=(dt.date.fromisoformat(item['end'])-dt.date.fromisoformat(item['start'])).days
                    if 350<=days<=380 and item.get('filed')==filing['filingDate']:
                        candidates.append((tags.index(tag),item,tag))
            if not candidates:
                continue
            candidates.sort(key=lambda x:x[0])
            _,item,tag=candidates[0]
            same=[x[1]['val'] for x in candidates if x[0]==candidates[0][0]]
            conflict=len(set(same))>1
            url=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{filing['accessionNumber'].replace('-','')}/{filing['primaryDocument']}"
            result.append(dict(metric=metric,value=item['val'],unit='USD',start=item['start'],end=item['end'],filed=item['filed'],form=item['form'],tag='us-gaap:'+tag,url=url,accession=item['accn'],status='conflict' if conflict else 'official_xbrl'))
    return result

def fetch_sec(store):
    co=store.company(); identity=store.settings()['sec_identity'].strip()
    if not co:
        raise ValueError('请先选择股票')
    if not co['cik']:
        raise ValueError('请添加该公司的SEC CIK，当前首版以美股非金融公司为范围')
    if '@' not in identity or len(identity)<8:
        raise ValueError('请在设置填写SEC访问身份：姓名或组织及联系邮箱')
    cutoff=now().date().isoformat(); raw={}
    for kind,url in [('submissions',f"https://data.sec.gov/submissions/CIK{int(co['cik']):010d}.json"),('companyfacts',f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(co['cik']):010d}.json")]:
        last=None
        for attempt in range(3):
            try:
                req=urllib.request.Request(url,headers={'User-Agent':'EquityResearchTutor/1.0 '+identity,'Accept':'application/json'})
                with urllib.request.urlopen(req,timeout=25) as response:
                    data=response.read(30_000_000)
                raw[kind]=json.loads(data)
                break
            except Exception as exc:
                last=exc
                if attempt<2:
                    time.sleep(1+attempt)
        else:
            with store.connect() as c:
                c.execute('INSERT INTO events(kind,detail,created) VALUES(?,?,?)',('资料获取失败',f'{kind}: {type(last).__name__}；已保留既有快照',stamp()))
            raise ValueError('SEC暂时无法访问，已重试并保留旧资料；请稍后重试或手动录入官方证据')
        time.sleep(.25)
    if int(raw['companyfacts'].get('cik',0))!=int(co['cik']) or int(raw['submissions'].get('cik',0))!=int(co['cik']):
        raise ValueError('披露CIK与当前公司不一致，已拒绝保存')
    if co['ticker'] not in raw['submissions'].get('tickers',[]):
        raise ValueError('所选股票代码与SEC披露中的代码不一致，请核对CIK；已拒绝保存')
    facts=extract_facts(raw['companyfacts'],raw['submissions'],cutoff,co['cik'])
    if not facts:
        raise ValueError('未找到满足同口径条件的年度事实；不会用季度或未知标签替代')
    encoded=json.dumps(raw,ensure_ascii=False).encode()
    digest=hashlib.sha256(encoded).hexdigest()
    cache=store.folder/'raw';cache.mkdir(exist_ok=True)
    path=cache/(digest+'.json');path.write_bytes(encoded)
    with store.lock,store.connect() as c:
        c.execute('INSERT INTO snapshots(company_id,as_of,created,status,payload) VALUES(?,?,?,?,?)',(co['id'],cutoff,stamp(),'official_xbrl',json.dumps(dict(sha256=digest,raw_file=path.name,fact_count=len(facts),basis='US-GAAP/USD/年度原始10-K披露；未纳入重述和自定义标签',fetched_at=stamp()),ensure_ascii=False)))
        sid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        for f in facts:
            c.execute('INSERT INTO facts(snapshot_id,company_id,metric,value,unit,start,end,filed,form,tag,url,accession,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(sid,co['id'],*[f[k] for k in ['metric','value','unit','start','end','filed','form','tag','url','accession','status']]))
    return dict(snapshot_id=sid,count=len(facts))
