# -*- coding: utf-8 -*-
"""评测共用判定逻辑。

单独成模块的原因：判定规则必须**只有一份**。
之前 `eval_chat.py` 里写一份、`ab_analysis.py` 里复制一份，两处一旦漂移
就会得出不同结论——而本项目最大的教训恰恰是「结论必须可复算」。
"""
import json
import math

# 框架失败的三种已观测模式（详见 eval/评测报告_v1.2_框架失败续跑.md §2.2）
LIMIT_MARK = "tool-iteration limit"
RAW_DUMP_MARK = "I found the following relevant project context"
JSON_DUMP_MARKS = ('```json', '{"action"')


def answer_text(data):
    """从 chat 响应里取最终答案文本（`message.content` 或顶层 `answer`）。"""
    m = data.get("message") or {}
    return (m.get("content") or data.get("answer") or "")


def framework_failure(data):
    """判断一次回答是否属于「框架没给出答案」。

    返回 None 表示正常；否则返回失败类型：
      - 'limit'    ：撞 8 步工具迭代上限，无答案
      - 'raw_dump' ：「原文直吐」——把检索片段原样吐出来，没有生成回答
      - 'json_dump'：把 agent loop 的 {"action":"final",...} 原样吐出来
                     （内容可能对，但用户看到的是裸 JSON，判失败）
    """
    a = answer_text(data)
    if LIMIT_MARK in a:
        return "limit"
    s = a.lstrip()
    if s.startswith(RAW_DUMP_MARK):
        return "raw_dump"
    if s.startswith(JSON_DUMP_MARKS):
        return "json_dump"
    return None


def framework_failure_of_answer(answer):
    """只给答案文本时的判定（用于分析已落盘的评测产物）。"""
    if LIMIT_MARK in (answer or ""):
        return "limit"
    s = (answer or "").lstrip()
    if s.startswith(RAW_DUMP_MARK):
        return "raw_dump"
    if s.startswith(JSON_DUMP_MARKS):
        return "json_dump"
    return None


# ---------------------------------------------------------------------------
# 原 21 题的关键词判定（窄口径：只看「关键概念有没有说到」）
#
# ⚠️ 这条口径**很粗**：`D1` 只要同时出现「短溢装」「加放」就算命中，分不清
#    「两个词都提了但解释反了」。所以它只能当**辅助指标**，主指标永远是
#    `usage.toolEventCount`（步数）与 `framework_failure`（框架有没有给答案）。
#    已知 D2 会被说反（「待核单」被解释成「还没审核」），故配一组负向标记。
#
# 放在这个模块里的原因同文件头：判定规则只有一份，`ab_analysis.py` 与
# `p0_step_reduction.py` 都从这里 import，避免各自复制一份后漂移。
# ---------------------------------------------------------------------------
KEY_TERMS = {
    "D1": ["短溢装", "加放"],       # 需同时出现
    "D2": ["待核单", "已核单"],
    "P1": ["CP确认", "次款号"],
}

# 语义与语料相反的「负向标记」：关键词说全了但意思反了 → 判 hallucination
NEG_TERMS = {
    "D2": ["尚未经过审核", "尚未审核", "等待审核", "待审核订单"],
}


def judge_keyword(key, answer):
    """原 21 题的关键词判定。返回 hit / miss / hallucination / refuse / 框架失败类型。

    `key` 可带 `#2` 之类的重复后缀；`answer` 是答案文本。
    """
    base = key.split("#")[0]
    why = framework_failure_of_answer(answer)
    if why:
        return why
    a = answer or ""
    if not a.strip():
        return "empty"
    if "我没有足够的信息" in a or ("没有找到" in a and len(a) < 200):
        return "refuse"
    if not all(t in a for t in KEY_TERMS.get(base, [])):
        return "miss"
    for n in NEG_TERMS.get(base, []):
        if n in a:
            return "hallucination"
    return "hit"


def wilson(k, n, z=1.96):
    """Wilson 比例置信区间（小样本比正态近似靠谱得多）。"""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, c - h), min(1.0, c + h))


def fisher(a, b, c, d):
    """2x2 单侧 Fisher 精确检验 P(X>=a)。表：[[a,b],[c,d]]。

    A/B 只做单侧：我们关心的是「B 臂比 A 臂好」这个方向。
    """
    n = a + b + c + d
    r1, r2 = a + b, c + d
    c1 = a + c
    tot = math.comb(n, c1)
    p = 0.0
    for x in range(a, min(r1, c1) + 1):
        if x > r1 or c1 - x > r2:
            continue
        p += math.comb(r1, x) * math.comb(r2, c1 - x) / tot
    return p
