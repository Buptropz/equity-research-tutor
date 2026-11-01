"""Conservative mechanical checks. Passing is not proof of semantic accuracy."""
from collections import Counter
import re

QUALITY_VERSION='quality-v2'
_NUMBER=r'[+\-−]?\d+(?:,\d{3})*(?:\.\d+)?(?:%|％)?'
_AMOUNT=re.compile(r'\(\s*(?:US\$|USD|EUR|\$|€|£|¥)?\s*'+_NUMBER+r'\s*(?:USD|EUR|美元|欧元)?\s*\)|(?:(?<![A-Za-z\d])|(?<=USD|EUR|GBP|CAD|AUD|CNY|RMB|JPY))'+_NUMBER,re.I)

def _normalized(text):
    return text.translate(str.maketrans({'（':'(', '）':')', '−':'-'}))

def amounts(text):
    text=_normalized(text);result=[]
    for match in _AMOUNT.finditer(text):
        raw=match.group();number=re.search(_NUMBER,raw).group()
        value='('+number+')' if raw.startswith('(') else number
        result.append(dict(value=value,start=match.start(),end=match.end()))
    return result

def numeric_tokens(text):
    return Counter(item['value'] for item in amounts(text))

def verify_numbers(original,translated):
    source=numeric_tokens(original);target=numeric_tokens(translated)
    return dict(ok=source==target,missing=list((source-target).elements()),added=list((target-source).elements()))

_CURRENCIES=[
 ('CAD',r'C\$|(?<![A-Za-z])CAD(?![A-Za-z])|Canadian\s+dollars?|加元|加拿大元'),
 ('AUD',r'A\$|(?<![A-Za-z])AUD(?![A-Za-z])|Australian\s+dollars?|澳元'),
 ('USD',r'US\$|(?<![A-Za-z])USD(?![A-Za-z])|U\.?S\.?\s+dollars?|\bdollars?\b|\$|美元'),
 ('EUR',r'(?<![A-Za-z])EUR(?![A-Za-z])|\beuros?\b|€|欧元'),
 ('GBP',r'(?<![A-Za-z])GBP(?![A-Za-z])|pounds?\s+sterling|£|英镑'),
 ('JPY',r'(?<![A-Za-z])JPY(?![A-Za-z])|\byen\b|日元'),
 ('CNY',r'(?<![A-Za-z])CNY(?![A-Za-z])|(?<![A-Za-z])RMB(?![A-Za-z])|人民币|¥|￥'),
]
_UNITS=[('billion',r'\bbillions?\b|十亿'),('million',r'\bmillions?\b|百万'),('thousand',r'\bthousands?\b|千'),('hundred_million',r'亿'),('ten_thousand',r'万')]
_METRICS=[
 ('operating_income',r'operating\s+(?:income|profit|loss)|营业(?:利润|亏损)|经营(?:利润|亏损)'),
 ('net_income',r'net\s+(?:income|profit|loss|earnings)|净(?:利润|亏损|收益|收入)'),
 ('gross_profit',r'gross\s+(?:profit|income|margin)|毛利润|毛利'),
 ('cfo',r'(?:cash\s+flows?\s+from\s+operating\s+activities|operating\s+cash\s+flows?)|经营活动.{0,16}现金(?:流量)?(?:净额)?|经营现金流'),
 ('capex',r'capital\s+expenditures?|\bcapex\b|资本(?:性)?(?:开支|支出)'),
 ('revenue',r'\brevenues?\b|\bsales\b|营业收入|销售收入|销售额|营收|营业额|收入'),
 ('profit',r'\bprofits?\b|\bearnings\b|利润|盈利|收益'),
 ('expense',r'\bexpenses?\b|\bcosts?\b|费用|成本|开支|支出'),
]
_LABELS=dict(operating_income='营业利润',net_income='净利润',gross_profit='毛利',cfo='经营现金流',capex='资本开支',revenue='收入',profit='利润',expense='费用')

def markers(text,groups):
    candidates=[]
    for priority,(key,pattern) in enumerate(groups):
        for match in re.finditer(pattern,text,re.I):
            candidates.append(dict(key=key,start=match.start(),end=match.end(),priority=priority))
    # Prefer a specific phrase over its generic substring (net profit over profit).
    selected=[]
    for marker in sorted(candidates,key=lambda m:(m['priority'],m['start'],-m['end'])):
        if not any(marker['start']<m['end'] and marker['end']>m['start'] for m in selected):selected.append(marker)
    return sorted(selected,key=lambda m:m['start'])

def _year(value):
    return value.isdigit() and 1900<=int(value)<=2100

def adjacent_bindings(text,groups):
    text=_normalized(text);numbers=amounts(text);found=markers(text,groups);bindings=Counter()
    for number in numbers:
        if _year(number['value']):continue
        choices=[]
        for marker in found:
            if marker['end']<=number['start']:gap=text[marker['end']:number['start']]
            elif marker['start']>=number['end']:gap=text[number['end']:marker['start']]
            else:gap=''
            if len(gap)<=12 and '\n' not in gap and re.fullmatch(r'[\s:$€£¥￥()]*',gap):choices.append((len(gap),marker['key']))
        if choices:
            distance,key=min(choices);bindings[(number['value'],key)]+=1
    return bindings

def metric_values(text):
    values=Counter()
    for clause in re.split(r'[;；\n。!?！？]|\.(?!\d)(?:\s+|$)',_normalized(text)):
        found=markers(clause,_METRICS);numbers=[n for n in amounts(clause) if not _year(n['value'])]
        if not found:continue
        keys={m['key'] for m in found}
        for number in numbers:
            if len(keys)==1:key=next(iter(keys))
            else:
                preceding=[m for m in found if m['start']<=number['start']]
                key=(preceding[-1] if preceding else found[0])['key']
            values[(key,number['value'])]+=1
    return values

def check_translation(original,translated):
    numeric=verify_numbers(original,translated);issues=[]
    if not numeric['ok']:issues.append('数字、小数、百分比或括号负数与原文不一致')
    words=re.findall(r'\b[A-Za-z]{2,}\b',original)
    cjk=len(re.findall(r'[\u3400-\u9fff]',translated))
    english_prose=len(words)>=2 or bool(re.search(r'\b(revenue|profit|income|expenses?)\b',original,re.I))
    content_issues=[]
    if not translated.strip():content_issues.append('没有返回译文')
    if english_prose and re.sub(r'\s+','',original).casefold()==re.sub(r'\s+','',translated).casefold():content_issues.append('英文原文被原样返回，尚未翻译')
    if (english_prose or re.search(r'[\u3400-\u9fff]',original)) and cjk==0:content_issues.append('未检测到中文正文')
    if re.match(r"\s*(?:抱歉|对不起|I\s+(?:cannot|can't|am unable)|Sorry\b|I'm sorry|I am sorry)",translated,re.I):content_issues.append('返回内容可能是拒答或错误说明')
    if len(words)>=80 and cjk+len(re.findall(r'\b[A-Za-z]{2,}\b',translated))<len(words)*.2:content_issues.append('译文明显过短，可能缺段或仅返回摘要')
    issues.extend(content_issues)
    checks={}
    for name,groups,label in [('currency',_CURRENCIES,'币种'),('units',_UNITS,'数量单位')]:
        source=Counter(m['key'] for m in markers(original,groups));target=Counter(m['key'] for m in markers(translated,groups))
        ok=source==target
        left=adjacent_bindings(original,groups);right=adjacent_bindings(translated,groups)
        associations_ok=not (left and right and left!=right)
        if not ok:issues.append(label+'标记与原文不对应，请核对')
        elif not associations_ok:issues.append('金额对应的'+label+'可能错位，请对照原文行')
        checks[name]=dict(ok=ok and associations_ok,source=dict(source),translated=dict(target))
    source_rows=metric_values(original);target_rows=metric_values(translated)
    row_ok=not source_rows or source_rows==target_rows
    if not row_ok:
        changed={key for key,value in (source_rows-target_rows)}|{key for key,value in (target_rows-source_rows)}
        issues.append('财务科目对应金额可能错位或缺失：'+'、'.join(_LABELS.get(key,key) for key in sorted(changed)))
    return dict(version=QUALITY_VERSION,ok=not issues,missing=numeric['missing'],added=numeric['added'],numbers=numeric,
                content=dict(ok=not content_issues,issues=content_issues),currency=checks['currency'],units=checks['units'],rows=dict(ok=row_ok),issues=issues)
