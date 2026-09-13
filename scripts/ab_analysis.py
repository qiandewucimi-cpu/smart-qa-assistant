# -*- coding: utf-8 -*-
"""A/B 判定与 Fisher 检验：续跑开 vs 关。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_common import fisher, judge_keyword as judge  # 判定规则只有一份（见 eval_common.py）

B = r'C:\Users\31114\WorkBuddy\智能问答助手\eval'


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
