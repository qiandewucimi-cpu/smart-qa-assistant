# -*- coding: utf-8 -*-
"""评测共用判定逻辑。

单独成模块的原因：判定规则必须**只有一份**。
之前 `eval_chat.py` 里写一份、`ab_analysis.py` 里复制一份，两处一旦漂移
就会得出不同结论——而本项目最大的教训恰恰是「结论必须可复算」。
"""
import json

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


def wilson(k, n, z=1.96):
    """Wilson 比例置信区间（小样本比正态近似靠谱得多）。"""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, c - h), min(1.0, c + h))
