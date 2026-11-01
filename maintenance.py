"""Consistent local backups, validated restore and optional dependency diagnostics."""
from contextlib import closing
import datetime as dt
import hashlib
import base64
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

from research import Store, stamp, now

MAX_BACKUP_BYTES=250_000_000
MAX_BACKUP_FILES=5000

def file_sha256(file):
    digest=hashlib.sha256()
    with open(file,'rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

def check_capacity(count,size):
    if count>MAX_BACKUP_FILES or size>MAX_BACKUP_BYTES:
        raise ValueError(f'备份超过可恢复范围：最多{MAX_BACKUP_FILES}个文件、展开后{MAX_BACKUP_BYTES/1_000_000:g}MB。请减少原始文件或分开归档后再备份')

def validate_archive(z):
    entries=z.infolist();names=[entry.filename for entry in entries]
    check_capacity(len(entries),sum(entry.file_size for entry in entries))
    if 'research.sqlite3' not in names or len(names)!=len(set(names)):
        raise ValueError('备份缺少数据库或包含重复文件')
    for name in names:
        if name not in ['research.sqlite3','manifest.json'] and not re.fullmatch(r'(raw/[a-f0-9]{64}\.json|documents/[a-f0-9]{64}\.(pdf|txt|html))',name):
            raise ValueError('备份包含非预期文件')
    return entries

def list_backups(store):
    folder=store.folder/'backups'
    return [dict(name=p.name,bytes=p.stat().st_size,modified=dt.datetime.fromtimestamp(p.stat().st_mtime,now().tzinfo).isoformat()) for p in sorted(folder.glob('*.zip'),key=lambda p:p.stat().st_mtime,reverse=True)] if folder.exists() else []

def import_backup(store,encoded):
    try:
        raw=base64.b64decode(encoded,validate=True)
        if len(raw)>18_000_000:raise ValueError('上传备份不能超过18MB；更大的备份可放入本地data/backups目录')
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            validate_archive(z)
    except (zipfile.BadZipFile,TypeError):raise ValueError('不是有效的ZIP备份文件')
    folder=store.folder/'backups';folder.mkdir(exist_ok=True)
    name='imported-'+now().strftime('%Y%m%d-%H%M%S-%f')+'.zip'
    (folder/name).write_bytes(raw)
    return dict(name=name)

def create_backup(store,automatic=False,label='manual'):
    folder=store.folder/'backups';folder.mkdir(exist_ok=True)
    if automatic:
        existing=list(folder.glob('auto-'+now().strftime('%Y%m%d')+'-*.zip'))
        if existing:
            try:
                with zipfile.ZipFile(existing[0]) as z:validate_archive(z)
                return existing[0].name
            except (ValueError,zipfile.BadZipFile):pass
    name=('auto' if automatic else label)+'-'+now().strftime('%Y%m%d-%H%M%S-%f')+'.zip'
    with store.lock,tempfile.TemporaryDirectory(dir=folder) as temp:
        if automatic:
            existing=list(folder.glob('auto-'+now().strftime('%Y%m%d')+'-*.zip'))
            for previous in existing:
                try:
                    with zipfile.ZipFile(previous) as z:validate_archive(z)
                    return previous.name
                except (ValueError,zipfile.BadZipFile):pass
        db=Path(temp)/'research.sqlite3'
        with closing(sqlite3.connect(store.db)) as source,closing(sqlite3.connect(db)) as dest:
            source.backup(dest)
        files=[(db,'research.sqlite3')]
        for directory,extension in [('raw','*.json'),('documents','*')]:
            for p in (store.folder/directory).glob(extension):
                if p.is_file() and not p.is_symlink():
                    files.append((p,directory+'/'+p.name))
        # Apply the same budget used by import/restore, including the manifest entry.
        check_capacity(len(files)+1,sum(p.stat().st_size for p,_ in files))
        manifest=dict(format='equity-research-tutor-backup',version=11,created=stamp(),files={arc:file_sha256(p) for p,arc in files})
        manifest_bytes=json.dumps(manifest,ensure_ascii=False).encode('utf-8')
        check_capacity(len(files)+1,sum(p.stat().st_size for p,_ in files)+len(manifest_bytes))
        target=folder/name
        partial=target.with_suffix('.part')
        try:
            with zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED) as z:
                for p,arc in files:z.write(p,arc)
                z.writestr('manifest.json',manifest_bytes)
            with zipfile.ZipFile(partial) as z:validate_archive(z)
            partial.replace(target)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    if automatic:
        for p in sorted(folder.glob('auto-*.zip'),reverse=True)[7:]:
            p.unlink()
    return name

def restore_backup(store,name):
    if Path(name).name!=name or '/' in name or '\\' in name or not name.endswith('.zip'):
        raise ValueError('备份名称无效')
    archive=store.folder/'backups'/name
    if not archive.is_file():raise ValueError('备份不存在')
    with store.lock,tempfile.TemporaryDirectory(dir=store.folder/'backups') as temp:
        stage=Path(temp)/'data';stage.mkdir()
        with zipfile.ZipFile(archive) as z:
            entries=validate_archive(z)
            names=[i.filename for i in entries]
            if len(names)!=len(set(names)) or 'research.sqlite3' not in names:
                raise ValueError('备份缺少数据库或包含重复条目')
            for info in entries:
                item=info.filename
                allowed=item in ['research.sqlite3','manifest.json'] or re.fullmatch(r'(raw/[a-f0-9]{64}\.json|documents/[a-f0-9]{64}\.(pdf|txt|html))',item)
                if not allowed or PurePosixPath(item).is_absolute() or '..' in PurePosixPath(item).parts:
                    raise ValueError('备份包含非预期文件')
                target=stage/item;target.parent.mkdir(exist_ok=True,parents=True)
                with z.open(info) as source,open(target,'wb') as dest:shutil.copyfileobj(source,dest,1024*1024)
            if 'manifest.json' in names:
                manifest=json.loads((stage/'manifest.json').read_text(encoding='utf-8'))
                if manifest.get('format')!='equity-research-tutor-backup':raise ValueError('备份格式无效')
                actual=set(names)-{'manifest.json'}
                if actual!=set(manifest.get('files',{})):raise ValueError('备份文件清单不一致')
                for item,digest in manifest['files'].items():
                    if file_sha256(stage/item)!=digest:raise ValueError('备份完整性校验失败')
        with closing(sqlite3.connect(stage/'research.sqlite3')) as source:
            if source.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('备份数据库损坏')
            if source.execute('PRAGMA user_version').fetchone()[0]>11:raise ValueError('备份版本高于当前程序，请先升级程序')
            tables={r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'companies','settings','packs','snapshots','answers','facts'}<=tables:raise ValueError('不是本项目的研究数据库')
        migrated=Store(stage)
        # Interrupted translation is resumable; restoring never starts paid/model work automatically.
        with migrated.connect() as c:c.execute("UPDATE jobs SET status='PAUSED' WHERE status IN ('PENDING','RUNNING')")
        safety=create_backup(store,label='before-restore')
        for directory in ['raw','documents']:
            target=store.folder/directory;target.mkdir(exist_ok=True)
            for p in (stage/directory).glob('*'):
                shutil.copyfile(p,target/p.name)
        with closing(sqlite3.connect(migrated.db)) as source,closing(sqlite3.connect(store.db)) as dest:
            source.backup(dest)
        return dict(safety_backup=safety,message='已恢复研究记录；恢复前数据另有安全备份。翻译任务需手动续翻。')

def diagnostics(store):
    from ai_backend import find_codex
    checks=[dict(name='Python',ok=sys.version_info>=(3,11),detail=sys.version.split()[0])]
    try:
        with store.connect() as c:ok=c.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        checks.append(dict(name='研究数据库',ok=ok,detail='结构检查通过' if ok else '需要恢复备份'))
    except sqlite3.Error:checks.append(dict(name='研究数据库',ok=False,detail='无法读取，请查看备份'))
    try:
        with tempfile.TemporaryFile(dir=store.folder) as f:f.write(b'check')
        checks.append(dict(name='本地保存',ok=True,detail='可以写入研究目录'))
    except OSError:checks.append(dict(name='本地保存',ok=False,detail='研究目录不可写'))
    executable=find_codex();signed=False
    if executable:
        try:
            options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
            result=subprocess.run([executable,'login','status'],capture_output=True,text=True,encoding='utf-8',timeout=8,**options)
            signed=result.returncode==0
        except (OSError,subprocess.TimeoutExpired):pass
    checks.append(dict(name='Codex AI',ok=signed,detail='已找到Codex且已登录' if signed else '请打开Codex并登录；基础研究与保存仍可使用'))
    deps=Path(os.environ.get('USERPROFILE',''))/'.cache/codex-runtimes/codex-primary-runtime/dependencies'
    node=(deps/'node/bin/node.exe').exists() or bool(shutil.which('node'))
    modules=Path(os.environ.get('ERT_RUNTIME_MODULES',str(deps/'node/node_modules')))
    checks.append(dict(name='Excel导出',ok=node and (modules/'@oai/artifact-tool').exists(),detail='已找到导出运行库' if node and (modules/'@oai/artifact-tool').exists() else '缺少导出运行库；Markdown和JSON仍可导出'))
    try:
        import importlib.util
        pdf=bool(importlib.util.find_spec('pypdf'))
    except (ValueError,ImportError):pdf=False
    pdf=pdf or (deps/'python/Lib/site-packages/pypdf').exists()
    checks.append(dict(name='PDF财报读取',ok=pdf,detail='可提取文字型PDF' if pdf else '需安装pypdf；仍可粘贴文字或导入TXT/HTML'))
    return dict(checked_at=stamp(),checks=checks)
