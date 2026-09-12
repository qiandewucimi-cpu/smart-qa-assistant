# -*- coding: utf-8 -*-
"""拒答准确性评测：测「知识缺失时，系统会不会老实说不知道」。

背景：22 题评测里「幻觉率 0%」只说明「答了的都对」，不等于「不知道时会拒答」——
而企业场景最怕的恰恰是「语料里没有，模型却编一个像模像样的答案」。
本脚本用两组题把这件事量化：

- **X 类（语料外）**：语料里确实没有依据的问题，**正确行为是说不知道**。
  每题都已用 `check_refusal_bank.py` 验证过关键词在 119 份语料中命中为 0。
- **N 类（语料内对照）**：语料里有实据、但 22 题没问过的冷门模块，**正确行为是答出来**。
  用于测「误拒」——即本轮 C3/O4 那种「检索没召回 → 模型如实拒答」，本质是误伤。

三项指标：
- **正确拒答率** = X 类中明确表示「未找到 / 不知道」的比例 → 越高越好
- **拒答幻觉率** = X 类中仍然编造答案的比例 → 必须为 0
- **误拒率** = N 类中却说「不知道」的比例 → 越低越好

用法：
  python eval_refusal.py            # 跑全部题目
  python eval_refusal.py --probe X1 # 只跑一题
"""
import json
import sys
import time

from config import BASE
from eval_chat import chat, extract_answer

# X 类：语料外（应拒答）。括号内为验证依据。
OUT_OF_CORPUS = {
    "X1": "公司员工食堂在几楼？几点开饭？",                      # 食堂/开饭/餐厅/后勤 = 0 命中
    "X2": "怎么用 Python 写一个爬虫抓取网页数据？",              # 爬虫 = 0 命中
    "X3": "DLS 系统的服务器机房在哪里？硬件配置是什么样的？",     # 机房 = 0 命中
    "X4": "DLS 系统登录密码忘了，怎么找回或重置？",              # 找回密码/登录失败 = 0 命中
    "X5": "「核料单三级复核机制」是怎样设计的？",                # 三级复核/复核机制 = 0 命中
    "X6": "翻单时「改颜色」这个类型需要走什么审批流程？",        # 改颜色/改色 = 0 命中
    "X7": "DLS 系统的对外 API 接口文档在哪里可以看？",           # 接口文档/接口说明 = 0 命中
    "X8": "DLS 系统 2027 年计划上线哪些新功能？",                # 未来规划/路线图 = 0 命中
}

# N 类：语料内对照（应回答）——都是语料里有的模块，但 22 题从未问过
IN_CORPUS = {
    "N1": "仓储管理模块主要有哪些功能？",                        # 08-仓储管理
    "N2": "款式档案里包含哪些信息？",                            # 05-款式档案
    "N3": "物料出运管理和成品出运管理有什么区别？",              # 11/12-出运管理
    "N4": "大货技术资料包审核通过后会回写哪些数据？",            # 10-大货技术资料包
    "N5": "采购管理模块的核心流程是什么？",                      # 07-采购管理
}

# 判定「模型是否在拒答」的线索词（用于初筛，最终由人工复核）
# 注意：实测模型表达「不知道」的方式很丰富——除「未找到」外还有「未能找到」「未涉及」
# 「缺少以下信息」等，漏掉这些会把正确拒答误判成硬答（首轮 13 题里就误判了 2 题）。
REFUSAL_MARKERS = [
    "未找到", "未能找到", "没有找到", "找不到", "无法找到",
    "未包含", "不包含", "没有包含", "未涉及", "未覆盖", "未提供",
    "无法回答", "无法提供", "无法确定", "不能回答", "无法给出",
    "知识库中没有", "语料", "不存在相关", "没有相关", "缺少相关",
    "缺少以下", "缺失信息", "缺少这", "信息缺失",
    "does not contain", "No matching", "not found", "cannot find",
    "无法从", "no information", "没有提及", "未提及", "没有说明", "未说明",
]


def looks_like_refusal(answer: str) -> bool:
    a = answer.lower()
    return any(m.lower() in a for m in REFUSAL_MARKERS)


def main():
    probe = "--probe" in sys.argv
    target = sys.argv[sys.argv.index("--probe") + 1] if probe and len(sys.argv) > 2 else None

    bank = [("X", k, q) for k, q in OUT_OF_CORPUS.items()] + \
           [("N", k, q) for k, q in IN_CORPUS.items()]
    if target:
        bank = [b for b in bank if b[1] == target]

    results = {}
    for grp, key, q in bank:
        t0 = time.time()
        try:
            data = chat(q)
            parsed = extract_answer(data)
            parsed["group"] = grp
            parsed["question"] = q
            parsed["elapsed"] = round(time.time() - t0, 1)
            parsed["refusal_signal"] = looks_like_refusal(parsed["answer"])
            results[key] = parsed
            n_ref = len(parsed["references"]) or len(parsed["event_refs"])
            kind = "语料外·应拒答" if grp == "X" else "语料内·应回答"
            print(f"[{key}] {kind} | 用时 {parsed['elapsed']}s | 召回 {n_ref} 条 "
                  f"| 疑似拒答={parsed['refusal_signal']}")
            print(f"    {q}")
            print(f"    答: {parsed['answer'][:200].strip()}")
            print()
        except Exception as ex:  # noqa: BLE001
            results[key] = {"group": grp, "question": q, "error": str(ex),
                            "elapsed": round(time.time() - t0, 1)}
            print(f"[{key}] 失败: {ex}\n")

    if not probe:
        out = BASE / "eval" / "拒答评测结果_raw.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        x = [v for v in results.values() if v.get("group") == "X"]
        n = [v for v in results.values() if v.get("group") == "N"]
        x_ref = sum(1 for v in x if v.get("refusal_signal"))
        n_ref = sum(1 for v in n if v.get("refusal_signal"))
        print(f"X 类（语料外）{len(x)} 题：疑似拒答 {x_ref} → 需人工复核")
        print(f"N 类（语料内）{len(n)} 题：疑似拒答 {n_ref} → 需人工复核")
        print(f"结果已写入 {out}")


if __name__ == "__main__":
    main()
