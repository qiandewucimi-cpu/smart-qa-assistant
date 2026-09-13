# -*- coding: utf-8 -*-
"""扩展集基线分析：每题稳定命中率 + 框架失败率 + 噪声估计。

用法：
  python judge_stability.py ext_r1                     # 单轮
  python judge_stability.py ext_r1 ext_r2              # 多轮合并（推荐）

判定口径（自动、客观、可复算）：
  题目由某一页生成 → 看答案的 references 里有没有这一页。
    cited              ：引用了目标页（可溯源命中）
    recalled_not_cited ：检索捞到了但模型没引用
    missed             ：答案正常，但目标页既没引用也没召回
    limit/raw_dump/json_dump ：框架失败（见 eval_common.framework_failure）

输出三件事：
  ① 总体「可溯源命中率」+ Wilson 置信区间（**不再报单点百分比**）
  ② 框架失败率 + 按模式拆分
  ③ **噪声估计**：多轮之间判定不一致的题占多少——这才是决定「要不要继续调参」的数字
"""
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_common import framework_failure_of_answer, wilson

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL = os.path.join(BASE, 'eval')


def load_targets():
    with open(os.path.join(EVAL, 'questions_ext.json'), encoding='utf-8') as f:
        return json.load(f)


def judge(item, target_page):
    a = item.get('answer') or ''
    why = framework_failure_of_answer(a)
    if why:
        return why
    if item.get('error') or not a.strip():
        # ⚠️ 必须单独成一类：API 报错（应用被关掉/连接被拒）与「框架给了答案但答偏」
        # 是两回事。早期版本会把无 answer 的记录当成「正常作答」，直接虚高成功率。
        return 'api_error'
    refs = [r.get('path', '') for r in (item.get('references') or [])]
    evs = [r.get('path', '') for r in (item.get('event_refs') or [])]
    if any(target_page in p for p in refs):
        return 'cited'
    if any(target_page in p for p in evs):
        return 'recalled_not_cited'
    return 'missed'


def main():
    tags = sys.argv[1:]
    if not tags:
        print('用法: python judge_stability.py <tag1> [tag2 ...]')
        return
    targets = load_targets()
    per_q = defaultdict(list)          # key -> [verdict, ...]
    per_q_latency = defaultdict(list)
    n_calls = 0
    for tag in tags:
        p = os.path.join(EVAL, '评测结果_raw_%s.json' % tag)
        if not os.path.exists(p):
            print('缺文件:', p)
            return
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
        for k, it in data.items():
            base = k.split('#')[0]
            if base not in targets:
                # 原 21 题没有「目标页」这种可自动判定的标准答案，
                # 这里只做客观判定：框架失败 / API 报错 / 正常作答（不评判答案对错）。
                if it.get('error') or not (it.get('answer') or '').strip():
                    per_q[base].append('api_error')
                else:
                    per_q[base].append(
                        framework_failure_of_answer(it.get('answer')) or 'answered')
                if it.get('elapsed'):
                    per_q_latency[base].append(it['elapsed'])
                n_calls += 1
                continue
            per_q[base].append(judge(it, targets[base]['page']))
            if it.get('elapsed'):
                per_q_latency[base].append(it['elapsed'])
            n_calls += 1

    HIT = {'cited', 'answered'}   # 'answered'：原 21 题，只判「框架没崩」，不判对错
    total = len(per_q)
    # api_error（应用被关掉 / 连接被拒）不是模型的锅，也不该稀释成功率：
    # 单独计数，且**排除出分母**。
    def valid(vs):
        return [v for v in vs if v != 'api_error']

    n_api_err = sum(1 for vs in per_q.values() for v in vs if v == 'api_error')
    hits_per_q = {q: sum(1 for v in valid(vs) if v in HIT) for q, vs in per_q.items()}
    n_samples = {q: len(valid(vs)) for q, vs in per_q.items()}

    # ---- 总体：按「调用次数」计的命中率 ----
    tot_calls = sum(n_samples.values())
    tot_hits = sum(hits_per_q.values())
    lo, hi = wilson(tot_hits, tot_calls)
    # 主指标名取决于题集：扩展集看「引用可溯源」，原集只能看「框架正常作答」
    matched = sum(1 for q in per_q if q in targets)
    metric = '可溯源命中率' if matched else '框架正常作答率'

    print('=' * 62)
    print('run: %s  调用 %d 次 / 覆盖 %d 题' % (', '.join(tags), tot_calls, total))
    print('=' * 62)
    print('%s      %d/%d = %.1f%%   95%% Wilson [%.1f%%, %.1f%%]'
          % (metric, tot_hits, tot_calls, 100 * tot_hits / tot_calls, 100 * lo, 100 * hi))
    if n_api_err:
        print('（另有 %d 次 API 报错已排除出分母：应用被关掉/连接被拒，不是模型的问题）' % n_api_err)

    allv = [v for vs in per_q.values() for v in vs]
    c = Counter(allv)
    print()
    print('--- 判定分布 ---')
    for k, n in c.most_common():
        print('  %-20s %3d  (%.1f%%)' % (k, n, 100 * n / len(allv)))
    fw = sum(n for k, n in c.items() if k in ('limit', 'raw_dump', 'json_dump'))
    flo, fhi = wilson(fw, len(allv))
    print('  => 框架失败合计    %d/%d = %.1f%%   95%% Wilson [%.1f%%, %.1f%%]'
          % (fw, len(allv), 100 * fw / len(allv), 100 * flo, 100 * fhi))

    # ---- 噪声估计：多轮不一致 ----
    multi = {q: valid(vs) for q, vs in per_q.items() if len(valid(vs)) > 1}
    if multi:
        inconsistent = [q for q, vs in multi.items() if len(set(vs)) > 1]
        print()
        print('--- 噪声估计（多轮间判定不一致）---')
        print('  可比题目        %d 题（各 %d 次采样）' % (len(multi), len(next(iter(multi.values())))))
        print('  判定不一致      %d 题 = %.1f%%' % (
            len(inconsistent), 100 * len(inconsistent) / len(multi)))
        if inconsistent:
            print('  不一致的题：')
            for q in inconsistent:
                label = targets.get(q, {}).get('q', '(原集)')[:32]
                print('    %-5s %s -> %s' % (q, label, per_q[q]))

    # ---- 每题稳定性：列出有过失败的题 ----
    print()
    print('--- 每题命中率（只列非全中的）---')
    bad = [(q, hits_per_q[q], n_samples[q]) for q in per_q if hits_per_q[q] < n_samples[q]]
    bad.sort(key=lambda x: (x[1] / x[2], -x[2]))
    for q, h, n in bad:
        label = targets.get(q, {}).get('q', '(原集)')[:30]
        print('  %-5s %d/%d  %-30s %s' % (q, h, n, label, per_q[q]))
    if not bad:
        print('  （全部全中）')

    # ---- 失败是否集中在少数题上（决定「是全局噪声还是结构性问题」）----
    FAIL = {'limit', 'raw_dump', 'json_dump'}
    nfail_q = [q for q in per_q if any(v in FAIL for v in per_q[q])]
    ok_q = [q for q in per_q if q not in nfail_q]
    print()
    print('--- 失败是否集中在少数题上 ---')
    if nfail_q:
        print('  ⚠️ 有失败的题 %d 道：' % len(nfail_q))
        for q in sorted(nfail_q):
            n = n_samples[q]
            f = sum(1 for v in per_q[q] if v in FAIL)
            label = targets.get(q, {}).get('q', '(原集)')[:28]
            print('     %-5s 失败 %2d/%2d (%.0f%%)  %s' % (q, f, n, 100 * f / n, label))
    n_ok_samples = sum(n_samples[q] for q in ok_q)
    if n_ok_samples:
        ub = 1 - 0.05 ** (1.0 / n_ok_samples)
        if not nfail_q:
            print('  ✅ 本轮**没有任何题**出现框架失败：%d 题、%d 次采样全部正常作答'
                  % (len(ok_q), n_ok_samples))
            print('     （0/%d 的 95%% 置信上界 %.1f%% —— 这类题的步数离 8 步上限很远）'
                  % (n_ok_samples, 100 * ub))
        else:
            print('  ✅ 其余 %d 道题：%d 次采样、**0 次框架失败**（95%% 置信上界 %.1f%%）'
                  % (len(ok_q), n_ok_samples, 100 * ub))
            print('     → 失败不是全局随机噪声，而是**集中在少数题上**；')
            print('       结合 usage.toolEventCount（失败样本中位 26~27 vs 成功 10~17），')
            print('       成因是这些题需要的步数贴近应用 8 步工具迭代上限。')

    lat = [x for vs in per_q_latency.values() for x in vs]
    if lat:
        lat.sort()
        print()
        print('--- 延迟：P50 %.1fs / P90 %.1fs / max %.1fs ---'
              % (lat[len(lat) // 2], lat[int(len(lat) * 0.9)], lat[-1]))

    # ---- 步数预算：把「8 步上限」这个假设变成可复算的数字 ----
    # 说明：usage.toolEventCount 是「用了几步」的**结果**，不是被操纵的变量，
    # 所以这是**关联**证据；但分桶后若出现明显断崖，就足以否定「全局随机噪声」的解释。
    steps = []   # [(toolEventCount, verdict)]
    for tag in tags:
        p = os.path.join(EVAL, '评测结果_raw_%s.json' % tag)
        if not os.path.exists(p):
            continue
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
        for k, it in data.items():
            n = (it.get('usage') or {}).get('toolEventCount')
            if n is None:
                continue
            base = k.split('#')[0]
            if base in targets:
                v = judge(it, targets[base]['page'])
            else:
                v = (framework_failure_of_answer(it.get('answer'))
                     or ('api_error' if it.get('error') else 'answered'))
            if v in ('api_error',):
                continue
            steps.append((n, v))
    if steps:
        print()
        print('--- 步数预算（usage.toolEventCount）---')
        ok = [n for n, v in steps if v not in FAIL]
        bad = [n for n, v in steps if v in FAIL]
        def med(v):
            v = sorted(v)
            return v[len(v) // 2] if v else None
        print('  成功样本 %d 次：中位 %s  最大 %s' % (len(ok), med(ok), max(ok) if ok else '-'))
        if bad:
            print('  失败样本 %d 次：中位 %s  最小 %s' % (len(bad), med(bad), min(bad)))
        print('  分桶失败率：')
        # ⚠️ 变量名不能叫 lo/hi —— 会把上面算好的 Wilson 区间覆盖掉
        # （曾导致末尾打印出「区间宽度 97100.0pp」这种荒谬数字）
        for lo_b, hi_b in [(0, 15), (15, 20), (20, 24), (24, 28), (28, 999)]:
            b = [n for n, v in steps if lo_b <= n < hi_b]
            if not b:
                continue
            f = sum(1 for n, v in steps if lo_b <= n < hi_b and v in FAIL)
            print('    %2d~%-3d : %3d 样本  失败 %2d  (%3.0f%%)'
                  % (lo_b, min(hi_b, 99), len(b), f, 100 * f / len(b)))
        print('  （8 步 × ≈3.4 事件/步 ≈ 27，与应用内置的工具迭代上限吻合）')

    # ---- 结论 ----
    print()
    print('=' * 62)
    if multi:
        inc = 100 * len([q for q, vs in multi.items() if len(set(vs)) > 1]) / len(multi)
        span = 100 * (hi - lo)
        print('区间宽度 %.1fpp ｜ 多轮不一致率 %.1f%%' % (span, inc))
        if inc > 20:
            print('→ 噪声仍然很大：继续调参是无效劳动，先扩大每题采样次数。')
        else:
            print('→ 噪声可接受：可以开始做单变量 A/B。')


if __name__ == '__main__':
    main()
