"""Deterministic FCFF model, USD millions, end-year discounting, version 1."""
import math

VERSION = 'fcff-roic-v1.1'
DEFAULTS = dict(revenue=1000, cash=0, debt=0, other_claims=0, shares=100,
                growth=.10, margin=.25, tax=.21, roic=.20, wacc=.10,
                terminal_growth=.025, terminal_roic=.12, years=5)

def validate(raw):
    a = {}
    for key in DEFAULTS:
        if key not in raw or raw[key] in ('', None):
            raise ValueError('缺少估值输入：' + key)
        try:
            a[key] = float(raw[key])
        except (ValueError, TypeError):
            raise ValueError('估值输入必须是数字：' + key)
        if not math.isfinite(a[key]):
            raise ValueError('估值输入必须是有限数字：' + key)
    if a['years'] != int(a['years']) or not 1 <= a['years'] <= 15:
        raise ValueError('预测年限须为1—15年的整数')
    a['years'] = int(a['years'])
    if a['revenue'] <= 0 or a['shares'] <= 0:
        raise ValueError('收入和稀释后股数必须大于0')
    if min(a['cash'], a['debt'], a['other_claims']) < 0:
        raise ValueError('现金、债务与其他权益请求权不得为负数')
    if not 0 <= a['growth'] <= 1 or not 0 <= a['terminal_growth'] <= .08:
        raise ValueError('简化模型仅支持0—100%预测增长及0—8%永续增长')
    if not 0 < a['margin'] <= 1 or not 0 <= a['tax'] < 1:
        raise ValueError('本简化模型要求正营业利润率及有效税率')
    if not 0 < a['wacc'] <= .5 or a['wacc'] <= a['terminal_growth']:
        raise ValueError('必须满足 WACC > 永续增长率，且WACC不超过50%')
    if not 0 < a['roic'] <= 1 or not 0 < a['terminal_roic'] <= 1:
        raise ValueError('ROIC须大于0且不超过100%')
    if a['growth'] > a['roic'] or a['terminal_growth'] > a['terminal_roic']:
        raise ValueError('本模型要求增长率不超过ROIC，避免隐含再投资率超过100%')
    return a

def dcf(raw):
    a = validate(raw)
    rows = []
    revenue = a['revenue']
    for t in range(1, a['years'] + 1):
        revenue *= 1 + a['growth']
        ebit = revenue * a['margin']
        nopat = ebit * (1 - a['tax'])
        reinvestment = nopat * a['growth'] / a['roic']
        fcff = nopat - reinvestment
        rows.append(dict(year=t, revenue=revenue, ebit=ebit, nopat=nopat,
                         reinvestment=reinvestment, fcff=fcff,
                         pv=fcff / (1 + a['wacc']) ** t))
    terminal_nopat = rows[-1]['nopat'] * (1 + a['terminal_growth'])
    terminal_fcff = terminal_nopat * (1 - a['terminal_growth'] / a['terminal_roic'])
    terminal_value = terminal_fcff / (a['wacc'] - a['terminal_growth'])
    terminal_pv = terminal_value / (1 + a['wacc']) ** a['years']
    ev = sum(r['pv'] for r in rows) + terminal_pv
    equity = ev + a['cash'] - a['debt'] - a['other_claims']
    if any(not math.isfinite(v) for r in rows for v in r.values()) or any(not math.isfinite(v) for v in [terminal_fcff,terminal_value,terminal_pv,ev,equity,equity/a['shares']]):
        raise ValueError('输入量级过大，计算超出有限数值范围；请核对百万美元单位')
    return dict(version=VERSION, assumptions=a, rows=rows, terminal_fcff=terminal_fcff,
                terminal_value=terminal_value, terminal_pv=terminal_pv, ev=ev,
                equity=equity, per_share=equity / a['shares'],
                terminal_weight=terminal_pv / ev if ev else None)

def scenarios(raw):
    a = validate(raw)
    cases = []
    for name, delta in [('保守', -.03), ('基准', 0), ('乐观', .03)]:
        b = dict(a) if delta == 0 else dict(a, growth=max(0, min(a['roic'], a['growth'] + delta)),
                 margin=max(.000001, min(1, a['margin'] + delta)))
        cases.append(dict(name=name, **dcf(b)))
    sensitivity = []
    for dw in [-.02, -.01, 0, .01, .02]:
        line = []
        for dg in [-.01, -.005, 0, .005, .01]:
            b = dict(a, wacc=a['wacc'] + dw, terminal_growth=a['terminal_growth'] + dg)
            try:
                value = dcf(b)['per_share']
            except ValueError:
                value = None
            line.append(dict(wacc=b['wacc'], g=b['terminal_growth'], value=value))
        sensitivity.append(line)
    return dict(cases=cases, sensitivity=sensitivity)

def reverse_dcf(raw, price):
    a = validate(raw)
    price = float(price)
    if not math.isfinite(price) or price <= 0:
        raise ValueError('目标价格必须为有限正数')
    # FCFF falls when growth consumes reinvestment. Never assume monotonicity.
    low, high = 0., min(a['roic'], 1.)
    points = [(low + (high-low)*i/400) for i in range(401)]
    roots = []
    f = lambda g: dcf(dict(a, growth=g))['per_share'] - price
    for x, y in zip(points, points[1:]):
        fx, fy = f(x), f(y)
        if abs(fx) < 1e-8:
            roots.append(x)
        elif fx * fy < 0:
            for _ in range(55):
                mid = (x+y)/2
                fm = f(mid)
                if fx * fm <= 0:
                    y = mid
                else:
                    x, fx = mid, fm
            roots.append((x+y)/2)
    if abs(f(high)) < 1e-8:
        roots.append(high)
    roots = sorted({round(r, 10) for r in roots})
    return dict(growth_solutions=roots, price=price,
                note='仅反解预测期收入增长率；其他假设固定。可能无解或多解，不能视为唯一市场预期。')
