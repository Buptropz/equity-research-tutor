"""Double-click launcher; an existing healthy process is reused."""
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT=Path(__file__).resolve().parent
URL='http://127.0.0.1:8765'

def healthy():
    try:
        with urllib.request.urlopen(URL+'/api/health',timeout=1) as r:
            return json.loads(r.read()).get('app')=='equity-research-tutor'
    except Exception:
        return False

def main():
    if not healthy():
        (ROOT/'data').mkdir(exist_ok=True)
        log=open(ROOT/'data'/'runtime.log','a',encoding='utf-8')
        kwargs=dict(cwd=ROOT,stdout=log,stderr=log)
        if os.name=='nt':
            kwargs['creationflags']=subprocess.CREATE_NO_WINDOW
        subprocess.Popen([sys.executable,str(ROOT/'app.py')],**kwargs)
        for _ in range(40):
            if healthy():
                break
            time.sleep(.25)
        else:
            if os.name=='nt':
                ctypes.windll.user32.MessageBoxW(0,'本地服务未能启动。请查看程序目录 data/runtime.log；8765端口可能被其他程序占用。','研习 · 启动提示',0)
            return
    webbrowser.open(URL)

if __name__=='__main__':
    main()
