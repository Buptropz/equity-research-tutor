"""Source-preserving, resumable bilingual financial report reading."""
import base64
from collections import Counter
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import urllib.request
from urllib.parse import urlparse

from ai_backend import codex_text,is_busy
from research import stamp

MAX_FILE=18_000_000

class FilingText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[];self.ignored=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        hidden=tag in ['script','style','noscript','ix:hidden'] or 'display:none' in attrs.get('style','').replace(' ','').lower() or 'hidden' in attrs
        void=tag in ['br','hr','meta','link','img','input','wbr']
        if self.ignored:
            if not void:self.ignored.append(tag)
        elif hidden:
            if not void:self.ignored.append(tag)
        elif tag in ['td','th']:self.parts.append('\t')
        elif tag in ['p','div','br','tr','h1','h2','h3','h4','li','table','section']:self.parts.append('\n')
    def handle_endtag(self,tag):
        if self.ignored:
            if tag in self.ignored:
                self.ignored=self.ignored[:self.ignored.index(tag)]
        elif tag in ['p','div','tr','h1','h2','h3','h4','li','table','section']:self.parts.append('\n')
    def handle_data(self,data):
        if not self.ignored:self.parts.append(data)
    def text(self):
        text=''.join(self.parts).replace('\xa0',' ')
        return re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+','\n\n',text).strip()

def extract_html(raw):
    parser=FilingText();parser.feed(raw);return parser.text()

def split_text(text,limit=3200):
    chunks=[];pending=text.replace('\r\n','\n').replace('\r','\n').strip()
    while pending:
        if len(pending)<=limit:chunks.append(pending);break
        cut=max(pending.rfind('\n',limit//2,limit),pending.rfind(' ',limit//2,limit))
        if cut<0:
            match=re.search(r'\s',pending[limit:])
            cut=limit+match.start() if match else len(pending)
        chunks.append(pending[:cut].strip());pending=pending[cut:].lstrip()
    return chunks

def numeric_tokens(text):
    # Exact digits, punctuation and percent signs: currency/unit conversion is forbidden.
    number=r'[-−]?\d+(?:,\d{3})*(?:\.\d+)?(?:%|％)?'
    tokens=re.findall(r'\(\s*'+number+r'\s*\)|(?<![A-Za-z\d])'+number,text)
    return Counter(re.sub(r'\s+','',token) for token in tokens)

def verify_numbers(original,translated):
    source=numeric_tokens(original);target=numeric_tokens(translated)
    return dict(ok=source==target,missing=list((source-target).elements()),added=list((target-source).elements()))

def read_pdf(path):
    if importlib.util.find_spec('pypdf'):
        from pdf_reader import extract
        return extract(path)
    runtime=Path(os.environ.get('USERPROFILE',''))/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if not runtime.exists():raise ValueError('缺少PDF读取组件pypdf；可先粘贴原文或导入TXT/HTML')
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    try:
        result=subprocess.run([str(runtime),str(Path(__file__).with_name('pdf_reader.py')),str(path)],capture_output=True,text=True,encoding='utf-8',timeout=90,**options)
        output=json.loads(result.stdout)
    except (OSError,ValueError,subprocess.TimeoutExpired):raise ValueError('PDF读取失败；可尝试按章节拆分或粘贴原文')
    if output.get('error'):raise ValueError(output['error'])
    return output

def import_document(store,data):
    company=store.company();source_url=str(data.get('source_url','')).strip()
    if source_url and (urlparse(source_url).scheme!='https' or not urlparse(source_url).hostname):raise ValueError('来源链接须为HTTPS')
    title=str(data.get('title','')).strip()[:180]
    if not title:raise ValueError('请填写财报或章节标题')
    kind=data.get('kind','text');raw=None;ext='txt';extraction={}
    if kind=='sec':
        u=urlparse(source_url)
        if u.hostname not in ['www.sec.gov','sec.gov'] or u.port not in [None,443] or u.username or u.password or not re.fullmatch(r'/Archives/edgar/data/\d+/\d+/[A-Za-z0-9_.-]+\.html?',u.path):
            raise ValueError('自动读取目前只支持SEC Archives中的财报HTML原文链接；其他来源可上传PDF或粘贴文字')
        identity=store.settings()['sec_identity'].strip()
        if '@' not in identity:raise ValueError('请先在设置填写SEC访问身份')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        opener=urllib.request.build_opener(NoRedirect())
        req=urllib.request.Request(source_url,headers={'User-Agent':'EquityResearchTutor/1.1 '+identity})
        try:
            with opener.open(req,timeout=30) as response:raw=response.read(MAX_FILE+1)
        except Exception:raise ValueError('SEC原文暂时无法读取；请稍后重试，或下载后上传PDF/HTML')
        if len(raw)>MAX_FILE:raise ValueError('原文文件过大，请按章节导入')
        text=extract_html(raw.decode('utf-8-sig',errors='replace'));ext='html'
    elif kind=='file':
        try:raw=base64.b64decode(data.get('content_base64',''),validate=True)
        except (ValueError,TypeError):raise ValueError('上传文件编码无效')
        if not raw or len(raw)>MAX_FILE:raise ValueError('文件须大于0且不超过18MB')
        ext=Path(str(data.get('filename',''))).suffix.lower().lstrip('.')
        if ext not in ['pdf','txt','html','htm']:raise ValueError('支持PDF、TXT和HTML财报文件')
        if ext=='htm':ext='html'
        folder=store.folder/'documents';folder.mkdir(exist_ok=True)
        path=folder/(hashlib.sha256(raw).hexdigest()+'.'+ext);path.write_bytes(raw)
        if ext=='pdf':
            extraction=read_pdf(path);text=extraction['text']
        else:
            try:text=raw.decode('utf-8-sig')
            except UnicodeDecodeError:raise ValueError('TXT/HTML请使用UTF-8编码')
            if ext=='html':text=extract_html(text)
    elif kind=='text':
        text=data.get('text','')
        if not isinstance(text,str):raise ValueError('原文格式无效')
        raw=text.encode('utf-8')
    else:raise ValueError('未知财报导入方式')
    if not text.strip() or len(text)>2_000_000:raise ValueError('原文为空或超过200万字符，请按章节导入')
    digest=hashlib.sha256(raw).hexdigest();folder=store.folder/'documents';folder.mkdir(exist_ok=True)
    (folder/(digest+'.'+ext)).write_bytes(raw)
    chunks=split_text(text)
    if any(len(chunk)>16000 for chunk in chunks):raise ValueError('原文含过长连续文字，请按章节或段落拆分后导入')
    with store.connect() as c:
        c.execute('INSERT INTO documents(company_id,title,source_url,source_kind,report_period,sha256,text,created) VALUES(?,?,?,?,?,?,?,?)',(company['id'] if company else None,title,source_url,kind+':'+ext,str(data.get('report_period',''))[:100],digest,text,stamp()))
        doc_id=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        for i,chunk in enumerate(chunks):
            c.execute('INSERT INTO translation_chunks(document_id,ordinal,original,status,updated) VALUES(?,?,?,?,?)',(doc_id,i+1,chunk,'PENDING',stamp()))
    return dict(id=doc_id,chunks=len(chunks),pages=extraction.get('pages'),empty_pages=extraction.get('empty_pages',[]),warning='PDF文字提取不保证表格排版一致；请对照原PDF。' if ext=='pdf' else None)

def document(store,doc_id):
    with store.connect() as c:
        row=c.execute('SELECT * FROM documents WHERE id=? AND (company_id IS ? OR company_id IS NULL)',(doc_id,store.settings(c)['active_id'])).fetchone()
        if not row:raise ValueError('当前公司档案中没有这份财报')
        return dict(row)

def start_translation(store,doc_id,ordinal=None):
    doc=document(store,doc_id);settings=store.settings()
    if not settings['model_enabled'] or settings['model_provider']!='codex':raise ValueError('请先启用本机Codex AI，再翻译财报')
    with store.connect() as c:
        old=c.execute("SELECT id FROM jobs WHERE document_id=? AND status IN ('PENDING','RUNNING')",(doc['id'],)).fetchone()
        if old:return dict(job_id=old['id'])
        if ordinal is not None:
            ordinal=int(ordinal)
            exists=c.execute('SELECT 1 FROM translation_chunks WHERE document_id=? AND ordinal=?',(doc_id,ordinal)).fetchone()
            if not exists:raise ValueError('原文段落不存在')
        # Selected scope is persisted in error-independent JSON using a dedicated job column.
        total=c.execute('SELECT COUNT(*) FROM translation_chunks WHERE document_id=?'+(' AND ordinal=?' if ordinal is not None else ''),(doc_id,ordinal) if ordinal is not None else (doc_id,)).fetchone()[0]
        c.execute('INSERT INTO jobs(document_id,status,completed,total,created,updated,scope) VALUES(?,?,?,?,?,?,?)',(doc_id,'PENDING',0,total,stamp(),stamp(),json.dumps(dict(ordinal=ordinal))))
        return dict(job_id=c.execute('SELECT last_insert_rowid()').fetchone()[0])

def translate_chunk(store,chunk,doc):
    settings=store.settings()
    if not settings['model_enabled'] or settings['model_provider']!='codex':raise ValueError('AI已停用；启用本机Codex后可续翻')
    prompt='你是严谨的上市公司财报英译中译者。逐句完整翻译所给原文，不总结、不删减、不添加分析或外部事实。保留标题、段落、表格行列、GAAP/Non-GAAP区别、币种、单位、否定和条件措辞。所有阿拉伯数字及其逗号、小数点、正负号、百分号必须逐字保持，不换算为亿万，不新增数字。只输出中文译文正文，不输出前言、说明或代码围栏。保留原文已含中文页码标记。'
    translated=codex_text(prompt,dict(title=doc['title'],report_period=doc['report_period'],source_url=doc['source_url'],original=chunk['original']),settings['codex_model'],store.folder/'ai-runs')
    check=verify_numbers(chunk['original'],translated)
    with store.connect() as c:
        c.execute('UPDATE translation_chunks SET translated=?,status=?,number_check=?,error=NULL,model=?,updated=? WHERE id=?',(translated,'DONE' if check['ok'] else 'REVIEW',json.dumps(check,ensure_ascii=False),settings['codex_model'],stamp(),chunk['id']))

class TranslationWorker:
    def __init__(self,store,stop_event):
        self.store=store;self.stop_event=stop_event
        with store.connect() as c:
            c.execute("UPDATE jobs SET status='PAUSED',error='服务重启，可从已保存段落继续' WHERE status='RUNNING'")
    def tick(self):
        s=self.store
        if is_busy():return False
        with s.connect() as c:
            job=c.execute("SELECT * FROM jobs WHERE status='PENDING' ORDER BY id LIMIT 1").fetchone()
            if not job:return False
            job=dict(job);doc=dict(c.execute('SELECT * FROM documents WHERE id=?',(job['document_id'],)).fetchone())
            ordinal=json.loads(job['scope'] or '{}').get('ordinal')
            chunks=[dict(r) for r in c.execute('SELECT * FROM translation_chunks WHERE document_id=?'+(' AND ordinal=?' if ordinal is not None else '')+' ORDER BY ordinal',(doc['id'],ordinal) if ordinal is not None else (doc['id'],))]
            c.execute("UPDATE jobs SET status='RUNNING',updated=? WHERE id=?",(stamp(),job['id']))
        done=0
        for chunk in chunks:
            with s.connect() as c:status=c.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]
            if status=='PAUSED' or self.stop_event.is_set():
                with s.connect() as c:c.execute("UPDATE jobs SET status='PAUSED',updated=? WHERE id=?",(stamp(),job['id']))
                return True
            try:
                if chunk['status'] not in ['DONE','REVIEW']:translate_chunk(s,chunk,doc)
                done+=1
                with s.connect() as c:c.execute('UPDATE jobs SET completed=?,updated=? WHERE id=?',(done,stamp(),job['id']))
            except Exception as exc:
                if isinstance(exc,ValueError) and '正在处理上一个请求' in str(exc):
                    with s.connect() as c:c.execute("UPDATE jobs SET status='PENDING',updated=? WHERE id=?",(stamp(),job['id']))
                    return True
                error=str(exc)[:400] if isinstance(exc,ValueError) else '翻译未完成，可稍后重试'
                with s.connect() as c:
                    c.execute("UPDATE translation_chunks SET status='FAILED',error=?,updated=? WHERE id=?",(error,stamp(),chunk['id']))
                    c.execute("UPDATE jobs SET status='FAILED',error=?,updated=? WHERE id=?",(error,stamp(),job['id']))
                return True
        with s.connect() as c:
            c.execute("UPDATE jobs SET status='COMPLETED',error=NULL,updated=? WHERE id=? AND status='RUNNING'",(stamp(),job['id']))
        return True
    def run(self):
        while not self.stop_event.wait(1):
            try:self.tick()
            except Exception:
                with self.store.connect() as c:c.execute("UPDATE jobs SET status='FAILED',error='翻译任务中断，可重新续翻' WHERE status='RUNNING'")

def export_translation(store,doc_id):
    doc=document(store,doc_id)
    with store.connect() as c:chunks=[dict(r) for r in c.execute('SELECT * FROM translation_chunks WHERE document_id=? ORDER BY ordinal',(doc_id,))]
    lines=['# '+doc['title']+'｜中英对照',f"报告期：{doc['report_period'] or '未注明'}",f"来源：{doc['source_url'] or '用户上传或粘贴，来源待核实'}",f"原文件SHA-256：{doc['sha256']}",'机器译文，数字一致检查不代表翻译准确；请对照原始披露。','']
    for chunk in chunks:
        lines.extend([f"## 第{chunk['ordinal']}段 · {chunk['status']}",'### 原文',chunk['original'],'### 中文译文',chunk['translated'] or '尚未翻译',''])
        if chunk['number_check'] and not json.loads(chunk['number_check'])['ok']:lines.append('数字检查有差异，请逐项对照原文。\n')
    folder=store.folder.parent/'exports';folder.mkdir(exist_ok=True)
    name=f"translation_{doc_id}_{__import__('uuid').uuid4().hex[:8]}.md"
    (folder/name).write_text('\n'.join(lines),encoding='utf-8-sig')
    return dict(files=[name])
