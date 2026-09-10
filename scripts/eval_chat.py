# -*- coding: utf-8 -*-
"""LLM Wiki chat 评测脚本：逐题调用本地 API，提取最终答案 + 来源引用。

用法：
  python eval_chat.py            # 跑全部 15 题
  python eval_chat.py --probe C1 # 只跑一题并 dump 完整事件结构
"""
import json
import sys
import time
import urllib.request
import urllib.error

from config import API_BASE, BASE, load_token

TOKEN = load_token()

# 评测集 15 题（编号 -> 问题）
QUESTIONS = {
    "C1": "什么是「订单理单待核单」？",
    "C2": "什么是「加放系数」？在哪里设置？",
    "C3": "什么是「短溢装」？",
    "C4": "采购核料单的核心作用是什么？",
    "D1": "加放系数和短溢装有什么区别？",
    "D2": "待核单和已核单有什么区别？",
    "D3": "综合损耗率和生产损耗率是什么关系？",
    "P1": "订单审核通过后，数据如何流转到采购核料单？",
    "P2": "核料纸样选「是」时，制版环节流程是怎样的？",
    "P3": "核单合并有哪几种条件？",
    "O1": "核料单的物料来源有哪几种方式？",
    "O2": "BOM 复制时应优先选哪种 BOM？",
    "O3": "翻单类型有哪些？",
    "O4": "色码明细是自动带入还是手动填写？",
    "O5": "生产损耗率怎么取值？",
}


def chat(message, timeout=180):
    url = f"{API_BASE}/api/v1/projects/current/chat"
    payload = json.dumps({"message": message, "stream": False}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def extract_answer(data):
    """从响应顶层提取最终答案文本与来源引用。

    实测响应结构（v0.6.11，stream:false）：
      message.content  -> 最终答案文本
      references[]     -> 来源引用（path/score）
      usage{}          -> token 统计
    """
    msg = data.get("message") or {}
    answer = msg.get("content", "") if isinstance(msg, dict) else str(msg)
    references = []
    for r in data.get("references", []):
        references.append({
            "path": r.get("path", ""),
            "title": r.get("title", ""),
            "score": r.get("score"),
        })
    usage = data.get("usage", {})
    return {
        "answer": answer,
        "references": references,
        "usage": usage,
    }


def main():
    probe = "--probe" in sys.argv
    target = None
    if probe and len(sys.argv) > 2:
        target = sys.argv[sys.argv.index("--probe") + 1]

    keys = [target] if target else list(QUESTIONS.keys())
    results = {}
    for key in keys:
        q = QUESTIONS[key]
        t0 = time.time()
        try:
            data = chat(q)
            parsed = extract_answer(data)
            parsed["elapsed"] = round(time.time() - t0, 1)
            results[key] = parsed
            if probe:
                print(f"===== {key} 完整事件结构 =====")
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(f"[{key}] 用时 {parsed['elapsed']}s | 来源 {len(parsed['references'])} 条")
                print(f"    答: {parsed['answer'][:120].strip()}")
        except Exception as ex:
            results[key] = {"error": str(ex), "elapsed": round(time.time() - t0, 1)}
            print(f"[{key}] 失败: {ex}")

    if not probe:
        out = BASE / "eval" / "评测结果_raw.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n结果已写入 {out}")


if __name__ == "__main__":
    main()
