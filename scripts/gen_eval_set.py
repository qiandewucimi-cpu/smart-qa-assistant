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

⚠️ 与已提交题集的关系（2026-09-13）
-----------------------------------
标题护栏在 2026-09-13 从「只挡连续 ≥5 位数字」升级为「再加一层：纯拉丁标题一律排除」，
起因是两个**真实客户名页面**混进了题集（详见下面 `suspicious_title` 的注释）。
**这会让本脚本现在选出与已提交的 `eval/questions_ext.json` 不同的一组 45 题**
（新护栏多排除了 26 页）。

已提交的那份题集**保持在现场**，因为 `评测结果_raw_ext_f1/f2.json` 是拿它跑的，
换掉就会让已有产物不可复算。代价是：**仓库里的题集 ≠ 现在重跑脚本的输出**——
这是**已知且有意保留**的差异，等下次重跑评测时一并换成新题集（届时要重跑两轮）。

用法（2026-09-14 起支持参数）
----------------------------
    python scripts/gen_eval_set.py                 # 默认 45 题 → eval/questions_ext.json
    python scripts/gen_eval_set.py --concept 40 --entity 25 \
        --out-json eval/questions_ext60.json --out-md eval/评测集_扩展60.md

扩样本时**新开一份文件**、不覆盖旧 45 题集，这样 `ext_f1/f2` 仍可复算；
新集用当前（已升级的）护栏生成，因此与旧集**不是**同一批题、两集分数**不可混算**。
"""
import argparse
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

# ---------------------------------------------------------------------------
# 标题脱敏护栏（两层）
#
# 背景：wiki 里有编译期从**真实单据/客户名**派生出来的实体页，标题会直接带上
# 真实订单号或客户名（本项目踩过两次：v1.1 检索面变宽后这类页进了产物；
# 2026-09-13 又发现两个**纯拉丁的客户名派生页**混进了题集，
# 而当时的护栏只挡「连续 ≥5 位数字」——**挡不住客户名**）。
#
# 第一层（通用、无外部依赖，任何机器都能跑）：
#   ① 标题含连续 ≥5 位数字 → 单据编号派生物；
#   ② 标题**一个中文字都没有** → 纯拉丁标题。正常业务概念页几乎都带中文
#      （如 `FOB贸易术语`/`CP节点`/`DHL`→这条会连 DHL 一起挡掉，属**故意保守**：
#      宁可少几页覆盖，也不能把真实标识符放进要公开的题集）。
SUSPICIOUS_TITLE = re.compile(r'\d{5,}')
HAS_CJK = re.compile(r'[\u4e00-\u9fff]')

# 第二层（可选、本机开发时生效）：标题命中**完整真实词表**也跳过。
# 词表在 gitignored 的 maps_local.py / clean_text.py，公开仓库里没有，
# 所以用 try/except 包住——缺失时脚本照常可跑，只是少了这层保险。
try:
    from clean_text import CLIENT_MAP, COMPANY_MAP, NAME_MAP
    _REAL_TERMS = sorted(
        set(NAME_MAP) | {k for k, _ in COMPANY_MAP} | set(CLIENT_MAP),
        key=len, reverse=True,
    )
except Exception:
    _REAL_TERMS = []


def _term_pattern(t):
    if re.fullmatch(r'[A-Za-z0-9 _\-\.]+', t):
        return re.compile(r'(?<![A-Za-z0-9])' + re.escape(t) + r'(?![A-Za-z0-9])', re.I)
    return re.compile(re.escape(t))


_REAL_PATS = [(t, _term_pattern(t)) for t in _REAL_TERMS]


def suspicious_title(title):
    """返回跳过原因；None 表示这个标题可以进题集。"""
    if SUSPICIOUS_TITLE.search(title):
        return '连续≥5位数字'
    if not HAS_CJK.search(title):
        return '纯拉丁标题（疑为真实标识符/客户名派生）'
    for t, p in _REAL_PATS:
        if p.search(title):
            return '命中私密词表'
    return None

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


def collect(sub, limit, skipped, seed=SEED):
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
        why = suspicious_title(title)
        if why:
            skipped.append((sub, f, title, why))
            continue
        cands.append({
            'title': title,
            'page': f'wiki/{sub}/{f}',
            'type': meta.get('type') or sub,
            'body_len': len(body),
        })
    random.Random(seed).shuffle(cands)
    return cands[:limit]


def main():
    ap = argparse.ArgumentParser(description='生成扩展评测集（固定种子可复现）')
    ap.add_argument('--concept', type=int, default=N_CONCEPT,
                    help='概念题数（默认 %d）' % N_CONCEPT)
    ap.add_argument('--entity', type=int, default=N_ENTITY,
                    help='实体题数（默认 %d）' % N_ENTITY)
    ap.add_argument('--seed', type=int, default=SEED, help='随机种子（默认 %d）' % SEED)
    ap.add_argument('--out-json', default=OUT_JSON, help='输出题集 JSON')
    ap.add_argument('--out-md', default=OUT_MD, help='输出题集说明 Markdown')
    args = ap.parse_args()

    skipped = []
    items = (collect('concepts', args.concept, skipped, args.seed)
             + collect('entities', args.entity, skipped, args.seed))
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
    with open(args.out_json, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    rel_json = os.path.relpath(args.out_json, BASE).replace('\\', '/')
    lines = [
        '# 扩展评测集（自动生成 · 用于稳定性与框架失败率测量）',
        '',
        '> 生成：`python scripts/gen_eval_set.py`（固定种子 %d，可复现）' % args.seed,
        '> 数据：`%s`　|　共 **%d 题**（概念 %d + 实体 %d）' % (
            rel_json, len(out), args.concept, args.entity),
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
        '## 标题护栏（题集只收「不会泄露真实标识符」的页）',
        '',
        '生成时跳过两类标题：① 含连续 ≥5 位数字（单据号派生物）；',
        '② **一个中文字都没有的纯拉丁标题**（几乎都是从真实客户名/品牌/标识符派生的实体页）。',
        '本机还会额外挂一层完整真实词表（词表在 gitignored 的 `maps_local.py`，仓库里没有）。',
        '> 这个护栏是踩坑补的：2026-09-13 曾发现两个**真实客户名页面**混进题集，',
        '> 而当时的护栏只挡数字、挡不住客户名。',
        '',
        '## 题目清单',
        '',
        '| # | 问题 | 期望来源页 | 类型 |',
        '|---|---|---|---|',
    ]
    for k, v in out.items():
        lines.append('| %s | %s | `%s` | %s |' % (k, v['q'], v['page'], v['type']))
    with open(args.out_md, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print('已生成 %d 题 -> %s' % (len(out), args.out_json))
    print('        说明 -> %s' % args.out_md)
    if skipped:
        print('⚠️ 已按标题护栏跳过 %d 页（按原因分组）：' % len(skipped))
        from collections import defaultdict
        by_why = defaultdict(list)
        for sub, f, t, why in skipped:
            by_why[why].append('wiki/%s/%s' % (sub, f))
        for why, names in by_why.items():
            print('   [%s] %d 页' % (why, len(names)))
            for n in names[:8]:
                print('        - %s' % n)
            if len(names) > 8:
                print('        … 另 %d 页' % (len(names) - 8))
    from collections import Counter
    print('类型分布:', Counter(v['type'] for v in out.values()))
    for k in list(out)[:5]:
        print('  ', k, out[k]['q'], '->', out[k]['page'])


if __name__ == '__main__':
    main()
