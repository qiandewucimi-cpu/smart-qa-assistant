# -*- coding: utf-8 -*-
"""LLM Wiki chat 评测脚本：逐题调用本地 API，提取最终答案 + 来源引用。

用法：
  python eval_chat.py            # 跑全部题目
  python eval_chat.py --probe C1 # 只跑一题并 dump 完整事件结构

题目与标准答案要点见 eval/评测集.md（v0.2，22 题）。
P2 口径待业务确认，属「不计分项」，这里保留提问便于人工核对。
"""
import json
import socket
import sys
import time
import urllib.request
import urllib.error

from config import API_BASE, BASE, load_token

TOKEN = load_token()

# 可重试的临时性故障：网关抖动（502/503/504）、限流（429）、以及连接超时
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_MAX = 3          # 总尝试次数（含首次）
RETRY_BASE_SLEEP = 4   # 指数退避基数（秒）：4s → 8s

# 评测集 v0.2（编号 -> 问题）
# C=概念 D=对比 P=流程 O=操作/字段（batch1~3）；Q=报价/配比、M=机制/操作（v0.2 新增）
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
    "Q1": "什么是「配比报价」？包含哪几类？",
    "Q2": "组合报价和套装报价有什么区别？",
    "Q3": "什么是「公共成本」？举例说明。",
    "Q4": "报价单成本由哪几块构成？国内成本和国外成本怎么区分？",
    "Q5": "样板单的「溯源复制」和「非溯源复制」有什么区别？",
    "Q6": "报价单的状态流转经过哪些状态？各由谁负责？",
    "M1": "报价单里为什么每个模块都要单独点保存，而不是一次保存整页？",
}

# 不计分项（标准答案待业务确认），跑完人工核对、不计入命中率分母
NON_SCORING = {"P2"}


def _get_json(path):
    """GET 一个 JSON 端点（带 token）。"""
    req = urllib.request.Request(API_BASE + path,
                                 headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def resolve_project_id():
    """解析当前项目 ID。

    `/api/v1/projects/current` 在「未从 GUI 打开过项目」时会 404（实测：
    app-state.json 的 currentProject 为 null 即复现）。因此优先从
    `/api/v1/projects` 的 currentProject.id 取；都拿不到才回退 current。
    """
    try:
        d = _get_json("/api/v1/projects")
        cp = d.get("currentProject") or {}
        if cp.get("id"):
            return cp["id"]
        for p in d.get("projects", []):
            if p.get("current") and p.get("id"):
                return p["id"]
        if d.get("projects"):
            return d["projects"][0]["id"]
    except Exception:
        pass
    return None


PROJECT_ID = resolve_project_id()


def chat(message, timeout=180):
    """调用 chat API，对网关抖动/限流/超时做指数退避重试。"""
    path = (f"/api/v1/projects/{PROJECT_ID}/chat" if PROJECT_ID
            else "/api/v1/projects/current/chat")
    url = f"{API_BASE}{path}"
    payload = json.dumps({"message": message, "stream": False}).encode("utf-8")
    last_err = None
    for attempt in range(RETRY_MAX):
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            retryable = e.code in RETRY_STATUS
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            last_err = f"{type(e).__name__}: {e}"
            retryable = True
        if retryable and attempt < RETRY_MAX - 1:
            wait = RETRY_BASE_SLEEP * (2 ** attempt)
            print(f"    ! {last_err}，{wait}s 后重试（第 {attempt + 2} 次）")
            time.sleep(wait)
            continue
        raise RuntimeError(f"chat 失败（已重试 {attempt + 1} 次）：{last_err}")
    raise RuntimeError(f"chat 失败：{last_err}")


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
    # 兜底：v0.6.11 有时顶层 references 为空，但 events 里带 wiki.search 召回。
    # 单独记一份，用于区分「检索到但模型未采用」与「检索零召回」。
    event_refs = []
    for e in data.get("events", []):
        ref = e.get("reference") if isinstance(e, dict) else None
        if isinstance(ref, dict) and ref.get("path"):
            event_refs.append({
                "path": ref.get("path", ""),
                "kind": ref.get("kind", ""),
                "score": ref.get("score"),
            })
    usage = data.get("usage", {})
    return {
        "answer": answer,
        "references": references,
        "event_refs": event_refs,
        "usage": usage,
    }


def main():
    probe = "--probe" in sys.argv
    target = None
    if probe and len(sys.argv) > 2:
        target = sys.argv[sys.argv.index("--probe") + 1]

    keys = [target] if target else list(QUESTIONS.keys())
    print(f"项目 ID: {PROJECT_ID or '(未解析到，回退 current)'}")
    print(f"共 {len(keys)} 题，模型问答走 {API_BASE}")
    results = {}
    for key in keys:
        q = QUESTIONS[key]
        t0 = time.time()
        try:
            data = chat(q)
            parsed = extract_answer(data)
            parsed["elapsed"] = round(time.time() - t0, 1)
            parsed["scoring"] = key not in NON_SCORING
            results[key] = parsed
            if probe:
                print(f"===== {key} 完整事件结构 =====")
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                tag = "" if key not in NON_SCORING else "  [不计分·待业务确认]"
                n_ref = len(parsed["references"]) or len(parsed["event_refs"])
                src = "引用" if parsed["references"] else "召回"
                print(f"[{key}] 用时 {parsed['elapsed']}s | {src} {n_ref} 条{tag}")
                print(f"    答: {parsed['answer'][:120].strip()}")
        except Exception as ex:
            results[key] = {"error": str(ex), "elapsed": round(time.time() - t0, 1)}
            print(f"[{key}] 失败: {ex}")

    if not probe:
        out = BASE / "eval" / "评测结果_raw.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        n_scoring = len([k for k in keys if k not in NON_SCORING])
        print(f"\n共 {len(keys)} 题（计分 {n_scoring}，不计分 {len(keys) - n_scoring}）")
        print(f"结果已写入 {out}")


if __name__ == "__main__":
    main()
