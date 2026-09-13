# -*- coding: utf-8 -*-
"""生成扩展评测集（≥45 题），把样本从 21 题扩到 60+。

为什么必须扩
------------
v1.2 的 A/B 证明：**同配置下 D1 跨时段测出 1/10 与 3/5，差 50pp**——
系统自身的测量噪声大于我调过的任何一个旋钮的效应量。
那么在 21 题上做任何单变量 A/B 都是无效劳动。必须先拿到：
  ① 足够大的样本（≥60 题）
  ② 每题多次采样的稳定命中率

为什么用「引用可溯源」判定
--------------------------
手写 60 题标准答案不现实，且容易过拟合到评测集。这里换一个**客观、可自动判定、可复算**的口径：
  题目来自某一页 → 判定「答案的 references 里有没有这一页」。
它测的正是最近一直在出问题的两件事：**检索是否把对的那页捞出来** + **框架有没有正常跑完**。

局限（必须写清楚，不能拿它冒充原集）：
  - 题型单一（概念/实体定义题），**难度低于原 21 题**，不能用于报告「命中率」
  - 它的作用是**测量稳定性与框架失败率**，是原集的补充，不是替代
"""
import json
import os
import random
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI = os.path.join(BASE, 'projects', 'training-qa', 'training-qa', 'wiki')
OUT_JSON = os.path.join(BASE, 'eval', 'questions_ext.json')
OUT_MD = os.path.join(BASE, 'eval', '评测集_扩展.md')

N_CONCEPT = 27
N_ENTITY = 18
MIN_BODY = 150
SEED = 20260912

# 标题脱敏护栏：wiki 里有编译期从真实单据派生出来的实体页，标题会直接带上
# 真实订单号/编号（本项目踩过：v1.1 检索面变宽后这类页就进了产物）。
# 这里用**通用规则**过滤（连续 ≥5 位数字），不依赖私密词表——
# 私密词表在 gitignored 的 maps_local.py，公开脚本不能引用。
SUSPICIOUS_TITLE = re.compile(r'\d{5,}')

CONCEPT_TEMPLATES = ['什么是{title}？', '{title}是什么？', '请解释一下{title}']
ENTITY_TEMPLATES = ['{title}是什么？', '什么是{title}？', '{title}的定义是什么？']


def parse_page(path):
    txt = open(path, encoding='utf-8', errors='ignore').read()
    m = re.match(r'^---\n(.*?)\n---\n', txt, re.S)
    meta, body = {}, txt
    if m:
        for line in m.group(1).split('\n'):
            k, _, v = line.partition(':')
            meta[k.strip()] = v.strip()
        body = txt[m.end():]
    body = re.sub(r'\[\[|\]\]', '', body).strip()
    return meta, body


def collect(sub, limit, skipped):
    d = os.path.join(WIKI, sub)
    if not os.path.isdir(d):
        return []
    cands = []
    for f in sorted(os.listdir(d)):
        if not f.endswith('.md'):
            continue
        meta, body = parse_page(os.path.join(d, f))
        if len(body) < MIN_BODY:
            continue
        title = (meta.get('title') or os.path.splitext(f)[0]).strip()
        if not title:
            continue
        if SUSPICIOUS_TITLE.search(title):
            skipped.append((sub, f, title))
            continue
        cands.append({
            'title': title,
            'page': f'wiki/{sub}/{f}',
            'type': meta.get('type') or sub,
            'body_len': len(body),
        })
    random.Random(SEED).shuffle(cands)
    return cands[:limit]


def main():
    skipped = []
    items = collect('concepts', N_CONCEPT, skipped) + collect('entities', N_ENTITY, skipped)
    out = {}
    for i, it in enumerate(items, 1):
        key = 'A%02d' % i
        tpl = (CONCEPT_TEMPLATES if it['type'] == 'concept' else ENTITY_TEMPLATES)
        q = tpl[(i - 1) % len(tpl)].format(title=it['title'])
        out[key] = {
            'q': q,
            'page': it['page'],
            'title': it['title'],
            'type': it['type'],
            'body_len': it['body_len'],
        }
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    lines = [
        '# 扩展评测集（自动生成 · 用于稳定性与框架失败率测量）',
        '',
        '> 生成：`python scripts/gen_eval_set.py`（固定种子 %d，可复现）' % SEED,
        '> 数据：`eval/questions_ext.json`　|　共 **%d 题**（概念 %d + 实体 %d）' % (
            len(out), N_CONCEPT, N_ENTITY),
        '',
        '## 判定口径：「引用可溯源」',
        '',
        '题目由某一页生成 → 判定答案的 `references` 里**有没有这一页**。',
        '同时对「框架失败」（`limit` / `raw_dump` / `json_dump`）单独计数。',
        '',
        '## ⚠️ 局限（必须连带说明）',
        '',
        '- 题型单一（概念/实体定义题），**难度低于原 21 题**；',
        '- 因此**不能**用它报告「命中率」，它的作用是**测量稳定性与框架失败率**；',
        '- 它是原 `评测集.md` 的补充，不是替代。',
        '',
        '## 题目清单',
        '',
        '| # | 问题 | 期望来源页 | 类型 |',
        '|---|---|---|---|',
    ]
    for k, v in out.items():
        lines.append('| %s | %s | `%s` | %s |' % (k, v['q'], v['page'], v['type']))
    with open(OUT_MD, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print('已生成 %d 题 -> %s' % (len(out), OUT_JSON))
    print('        说明 -> %s' % OUT_MD)
    if skipped:
        print('⚠️ 已按标题护栏（连续 ≥5 位数字）跳过 %d 页：' % len(skipped))
        for sub, f, t in skipped:
            print('   - wiki/%s/%s' % (sub, f))
    from collections import Counter
    print('类型分布:', Counter(v['type'] for v in out.values()))
    for k in list(out)[:5]:
        print('  ', k, out[k]['q'], '->', out[k]['page'])


if __name__ == '__main__':
    main()
