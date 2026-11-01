"""Extract text only; scanned/encrypted PDFs are never presented as translated."""
import json
import sys
from pypdf import PdfReader

def extract(path):
    reader=PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(''):
        raise ValueError('PDF有密码保护，请先提供可读取的文件')
    if len(reader.pages)>1500:raise ValueError('PDF页数过多，请按章节拆分后导入')
    result=[];empty=[];total=0
    for i,page in enumerate(reader.pages):
        try:text=page.extract_text(extraction_mode='layout') or ''
        except TypeError:text=page.extract_text() or ''
        if len(text.strip())<10:empty.append(i+1)
        else:result.append(f'【原文第{i+1}页】\n'+text.strip())
        total+=len(text)
        if total>2_000_000:raise ValueError('提取文字过多，请按章节拆分')
    if not result:raise ValueError('PDF未提取到可读文字，可能是扫描件；请先做OCR，或粘贴已识别原文')
    return dict(text='\n\n'.join(result),pages=len(reader.pages),empty_pages=empty)

if __name__=='__main__':
    try:
        result=extract(sys.argv[1])
        print(json.dumps(result,ensure_ascii=True))
    except Exception as exc:
        print(json.dumps(dict(error=str(exc)[:300]),ensure_ascii=True));sys.exit(1)
