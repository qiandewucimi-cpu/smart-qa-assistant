# -*- coding: utf-8 -*-
"""P0 实验分析：把对比题的两个概念合并成一页 `queries/` 对照页，能否把步数压下来。

假设（来自 v1.3 报告）
----------------------
框架的 8 步工具迭代上限 ≈ 27 个 tool 事件（8 × ≈3.4）。D1/D2 这类**对比题**
要求模型把两个概念各查一次再比较，步数天然贴近上限，于是失败集中在这两题
（D1 8/15、D2 6/15，其余 18 题 0/90）。
→ 若把两个概念**并排放进同一页**，模型一次检索就能拿到两边内容，步数应下降。

为什么必须配 `--no-continue` 跑
-------------------------------
`eval_chat.py` 默认对框架失败**带 sessionId 续跑**。续跑后落盘的 `usage` 是
**补跑那一轮**的，`toolEventCount` 已不是「这题本来要用几步」——会污染主指标。
所以两臂都必须用 `--no-continue`，测的是**原始框架行为**：
    python eval_chat.py --only D1,D2 --repeat 10 --no-continue --tag p0_base
    python apply_faq_patch.py                     # 注入对照页
    python eval_chat.py --only D1,D2 --repeat 10 --no-continue --tag p0_faq

主指标 vs 辅助指标
------------------
主指标（客观、来自应用本身，不依赖人工判定）：
  ① 框架失败率（判定规则见 eval_common.framework_failure_of_answer）
  ② usage.toolEventCount 分布
辅助指标：关键词命中率（窄口径，见 eval_common.KEY_TERMS，分不清「提了但说反」）

用法：
  python p0_step_reduction.py p0_base p0_faq
  python p0_step_reduction.py p0_base p0_faq --only D1,D2
"""
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_common import (KEY_TERMS, fisher, framework_failure_of_answer,
                         judge_keyword, wilson)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL = os.path.join(BASE, 'eval')
FAIL = ('limit', 'raw_dump', 'json_dump')
# 8 步 × ≈3.4 事件/步；超过它基本必失败（v1.3 实测 24~28 桶失败率 93%）
STEP_CAP = 27


def load(tag):
    p = os.path.join(EVAL, '评测结果_raw_%s.json' % tag)
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def collect(tag, bases):
    """返回 {base: [(key, verdict, toolEventCount_or_None, continue_rounds)]}"""
    out = defaultdict(list)
    for k, it in load(tag).items():
        base = k.split('#')[0]
        if base not in bases:
            continue
        ans = it.get('answer') or ''
        v = ('api_error' if it.get('error') or not ans.strip()
             else (judge_keyword(k, ans) or 'miss'))
        steps = (it.get('usage') or {}).get('toolEventCount')
        out[base].append((k, v, steps, it.get('continue_rounds', 0)))
    return out


def fmt_steps(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return '无数据'
    vals.sort()
    return '中位 %g  区间 [%g, %g]' % (vals[len(vals) // 2], vals[0], vals[-1])


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(args) < 2:
        print(__doc__)
        return 1
    arm_a, arm_b = args[0], args[1]
    bases = ['D1', 'D2']
    if '--only' in sys.argv:
        bases = [x.strip().upper()
                 for x in sys.argv[sys.argv.index('--only') + 1].split(',') if x.strip()]

    A, B = collect(arm_a, bases), collect(arm_b, bases)

    print('=' * 66)
    print('P0 实验：对照页注入 前(%s) vs 后(%s)' % (arm_a, arm_b))
    print('题：%s   主指标：框架失败率 / toolEventCount（两臂均 --no-continue）'
          % ','.join(bases))
    print('=' * 66)

    # ---- 逐题 ----
    print()
    print('--- 逐题 ---')
    for base in bases:
        print('  [%s] %s' % (base, '/'.join(KEY_TERMS.get(base, ['?']))))
        for name, arm in ((arm_a, A), (arm_b, B)):
            rows = arm.get(base, [])
            if not rows:
                print('    %-10s 无数据' % name)
                continue
            c = Counter(v for _, v, _, _ in rows)
            nf = sum(n for k, n in c.items() if k in FAIL)
            nh = c.get('hit', 0)
            api = c.get('api_error', 0)
            valid = len(rows) - api
            steps_ok = [s for _, v, s, _ in rows if v not in FAIL]
            steps_bad = [s for _, v, s, _ in rows if v in FAIL]
            print('    %-10s n=%d(有效 %d)  框架失败 %d  关键词命中 %d'
                  '  步数(成功) %s' % (name, len(rows), valid, nf, nh, fmt_steps(steps_ok)))
            if steps_bad:
                print('    %-10s 失败样本步数：%s  判定：%s'
                      % ('', fmt_steps(steps_bad),
                         ','.join(sorted(set(v for _, v, _, _ in rows if v in FAIL)))))
            if api:
                print('    %-10s ⚠️ %d 次 API 报错（已排除，不计入分母）' % ('', api))

    # ---- 主指标 ①：框架失败率 ----
    print()
    print('--- 主指标①：框架失败率（API 报错已排除出分母）---')
    fa = sum(1 for base in bases for _, v, _, _ in A.get(base, []) if v in FAIL)
    na = sum(1 for base in bases for _, v, _, _ in A.get(base, []) if v != 'api_error')
    fb = sum(1 for base in bases for _, v, _, _ in B.get(base, []) if v in FAIL)
    nb = sum(1 for base in bases for _, v, _, _ in B.get(base, []) if v != 'api_error')
    for name, f, n in ((arm_a, fa, na), (arm_b, fb, nb)):
        lo, hi = wilson(f, n)
        print('  %-10s %d/%d = %.1f%%   95%% Wilson [%.1f%%, %.1f%%]'
              % (name, f, n, 100 * f / n if n else 0, 100 * lo, 100 * hi))
    if na and nb:
        # 单侧 Fisher：H1 = 注入后失败更少
        p = fisher(na - fa, fa, nb - fb, fb)
        print('  Fisher 单侧 p = %.3f  %s（H1：注入后失败更少）'
              % (p, '不显著' if p > 0.05 else '显著'))

    # ---- 主指标 ②：步数分布 ----
    print()
    print('--- 主指标②：usage.toolEventCount 分布 ---')
    buckets = [(0, 15), (15, 20), (20, 24), (24, 28), (28, 999)]
    for name, arm in ((arm_a, A), (arm_b, B)):
        steps = [(s, v) for base in bases for _, v, s, _ in arm.get(base, [])
                 if s is not None and v != 'api_error']
        if not steps:
            continue
        print('  %s：' % name)
        for lo, hi in buckets:
            b = [v for s, v in steps if lo <= s < hi]
            if not b:
                continue
            f = sum(1 for v in b if v in FAIL)
            print('    %2d~%-4s : %2d 样本  失败 %2d (%3.0f%%)'
                  % (lo, min(hi, 99), len(b), f, 100 * f / len(b)))
    print('  （上限 ≈ %d 事件 = 8 步 × ≈3.4 事件/步）' % STEP_CAP)

    # ---- 辅助指标：关键词命中 ----
    print()
    print('--- 辅助指标：关键词命中率（窄口径，分不清「提了但说反」）---')
    for name, arm in ((arm_a, A), (arm_b, B)):
        h = sum(1 for base in bases for _, v, _, _ in arm.get(base, []) if v == 'hit')
        n = sum(1 for base in bases for _, v, _, _ in arm.get(base, []) if v != 'api_error')
        lo, hi = wilson(h, n)
        print('  %-10s %d/%d = %.1f%%   95%% Wilson [%.1f%%, %.1f%%]'
              % (name, h, n, 100 * h / n if n else 0, 100 * lo, 100 * hi))

    print()
    print('=' * 66)
    print('读法：若注入后「24~28 桶」样本明显减少、且失败率下降，则假设成立；')
    print('      若步数没降 → 说明对照页没被检索到（回去查 references 里有没有它）。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
