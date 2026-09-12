# -*- coding: utf-8 -*-
"""A/B 判定与 Fisher 检验：续跑开 vs 关。"""
import json
import math
import os

B = r'C:\Users\31114\WorkBuddy\智能问答助手\eval'
STD = {
    'D1': ['短溢装', '加放'],          # 且要说清：短溢装=客户允差，加放=工厂损耗补偿
    'D2': ['待核单', '已核单'],        # 待核单=上游原始数据；已核单=合并后
    'P1': ['CP确认', '次款号'],        # 内部合同号+次款号，按单品款号生成
}
NEG = {
    'D2': ['尚未经过审核', '尚未审核', '等待审核', '待审核订单'],   # 与语料相反
}


def judge(key, ans):
    a = ans or ''
    base = key.split('#')[0]
    if 'tool-iteration limit' in a:
        return 'limit'
    if a.lstrip().startswith('I found the following relevant project context'):
        return 'raw_dump'
    if a.lstrip().startswith('```json') or a.lstrip().startswith('{"action"'):
        return 'json_dump'
    if not a.strip():
        return 'empty'
    if '我没有足够的信息' in a or '没有找到' in a and len(a) < 200:
        return 'refuse'
    hits = all(k in a for k in STD[base])
    if not hits:
        return 'miss'
    for n in NEG.get(base, []):
        if n in a:
            return 'hallucination'   # 说全了关键词但语义与语料相反
    return 'hit'


def fisher(a, b, c, d):
    """2x2 单侧 Fisher 精确检验 P(X>=a)。表：[[a,b],[c,d]]"""
    def C(n, k):
        return math.comb(n, k)
    n = a + b + c + d
    r1, r2 = a + b, c + d
    c1 = a + c
    tot = C(n, c1)
    p = 0.0
    for x in range(a, min(r1, c1) + 1):
        if x > r1 or c1 - x > r2:
            continue
        p += C(r1, x) * C(r2, c1 - x) / tot
    return p


VERDICT = {'hit'}
res = {}
for tag in ['ab_cont_on', 'ab_cont_off']:
    d = json.load(open(os.path.join(B, '评测结果_raw_%s.json' % tag), encoding='utf-8'))
    res[tag] = {k: judge(k, v.get('answer')) for k, v in d.items()}

print('%-8s %-14s %-14s' % ('题', '续跑开', '续跑关'))
allhit = {'ab_cont_on': 0, 'ab_cont_off': 0}
for base in ['D1', 'D2', 'P1']:
    row = []
    for tag in ['ab_cont_on', 'ab_cont_off']:
        vs = [res[tag][k] for k in res[tag] if k.split('#')[0] == base]
        n = sum(1 for v in vs if v in VERDICT)
        allhit[tag] += n
        row.append('%d/5  [%s]' % (n, ','.join(sorted(set(vs)))))
    print('%-8s %-30s %-30s' % (base, row[0], row[1]))

print()
print('合计：续跑开 %d/15，续跑关 %d/15' % (allhit['ab_cont_on'], allhit['ab_cont_off']))
p = fisher(allhit['ab_cont_on'], 15 - allhit['ab_cont_on'],
           allhit['ab_cont_off'], 15 - allhit['ab_cont_off'])
print('Fisher 单侧 p = %.3f  %s' % (p, '(不显著)' if p > 0.05 else '(显著)'))

print()
print('--- 失败模式分布（两臂合计）---')
from collections import Counter
c = Counter()
for tag in res:
    for v in res[tag].values():
        c[v] += 1
for k, v in c.most_common():
    print('  %-14s %d' % (k, v))

print()
print('--- 「坏答案」对比（幻觉/答偏 vs 无答案）---')
for tag in ['ab_cont_on', 'ab_cont_off']:
    bad = sum(1 for v in res[tag].values() if v in ('hallucination', 'miss'))
    none_ = sum(1 for v in res[tag].values() if v in ('limit', 'raw_dump', 'json_dump', 'refuse'))
    print('  %-10s 错误答案 %d | 无答案 %d' % (tag, bad, none_))
