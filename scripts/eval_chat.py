# -*- coding: utf-8 -*-
"""LLM Wiki chat 评测脚本：逐题调用本地 API，提取最终答案 + 来源引用。

用法：
  python eval_chat.py                  # 跑全部题目
  python eval_chat.py --only C3,O4,P1  # 只跑指定题（快速复测/方差检查）
  python eval_chat.py --repeat 2       # 每题重复 N 次（测生成端采样波动）
  python eval_chat.py --probe C1       # 只跑一题并 dump 完整事件结构
  python eval_chat.py --tag v1.1向量   # 结果另存为 评测结果_raw_<tag>.json（不覆盖）

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
from eval_common import framework_failure  # 判定规则只有一份（见 eval_common.py）

TOKEN = load_token()

# 可重试的临时性故障：网关抖动（502/503/504）、限流（429）、以及连接超时
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_MAX = 3          # 总尝试次数（含首次）
RETRY_BASE_SLEEP = 4   # 指数退避基数（秒）：4s → 8s

# 检索深度：chat 端点不传 topK 时实测只回 5 条，会把关键词命中的关键页挤出 Top-K
# （实测：默认深度下 C3/O4 零召回、P1 答偏；topK=15 时 C3/O4 召回命中）。
# 代价：深度变大后 P1 被稀释（答偏→拒答），见 eval 报告 §7。可用 --topk N 覆盖。
TOP_K = 15

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

# 扩展集：21 题太小，噪声大于任何旋钮的效应量（见 eval/评测报告_v1.2）。
# 用 `python scripts/gen_eval_set.py` 生成 ≥45 题的自动集，配合 --set 跑。
_EXT_SET_CACHE = {}


def load_question_set(path):
    """从 JSON 载入题目集。值可以是字符串，也可以是 {"q": ..., ...} 字典。"""
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    qs = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            qs[k] = v.get('q') or v.get('question') or ''
        else:
            qs[k] = v
    return qs


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


def chat(message, timeout=180, session_id=None):
    """调用 chat API，对网关抖动/限流/超时做指数退避重试。

    session_id：传入则续接同一会话（框架 8 步上限续跑的关键——见 chat_resilient）。
    """
    path = (f"/api/v1/projects/{PROJECT_ID}/chat" if PROJECT_ID
            else "/api/v1/projects/current/chat")
    url = f"{API_BASE}{path}"
    payload = {"message": message, "stream": False, "topK": TOP_K}
    if session_id:
        payload["sessionId"] = session_id
    payload = json.dumps(payload).encode("utf-8")
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


# ---------------------------------------------------------------------------
# 框架失败续跑（v1.2）
#
# 背景：应用 agent 有 8 步工具迭代硬上限，且应用配置里没有任何可配项
# （已核对 %APPDATA%/com.llmwiki.app/app-state.json，无 agent/步数字段）。
# 但框架返回了 sessionId，且失败文案明说 "ask it to continue from the latest
# result" —— 实测：撞上限后带同一 sessionId 补一句「请直接给出最终答案」，
# 能在 ~23s 内拿到完整正确答案（对照组不带 sessionId 则答「没有收到问题」）。
#
# 所以正解不是换模型（那要在**所有**题上多付 3.4× 延迟），
# 而是**只对撞上限的那一小部分题补一轮**。
# ---------------------------------------------------------------------------
CONTINUE_MAX = 2          # 撞上限后最多补几轮
# ⚠️ 提示语必须带「证据不足就说明缺什么」——第一版只写「请直接给出最终答案」，
# A/B 实测把 2 次「框架无答案」变成了 2 次**自信的错答案**（与语料相反）。
CONTINUE_PROMPT_BASE = (
    "请基于上面已检索到的内容直接给出最终答案，不要再调用任何工具。"
    "如果已检索到的内容不足以回答，请明确说明缺少什么，不要推测或编造。"
)
CONTINUE_PROMPT = CONTINUE_PROMPT_BASE   # 向后兼容：无首轮引用时的兜底
CONTINUE_REF_MAX = 10     # 塞进续跑提示语的首轮引用条数上限（防提示语膨胀）
# ⚠️ 默认关闭（2026-09-14）：A/B 显示方向对但**不显著**，且延迟翻倍，故不上线。
#   2x2：带引用臂「自陈未找到」4/10 vs 不带 7/10，Fisher 单侧 p = 0.185；
#   P50 延迟 112s vs 54s。详见 eval/评测报告_v1.3 §12.8。
CONTINUE_WITH_REFS = False


def build_continue_prompt(refs):
    """构造续跑提示语：把**首轮检索到的引用**带给续跑轮。

    2026-09-14 修的问题：续跑提示语明令「不要再调用任何工具」，但首轮 references
    没有跟着传下去 → 模型在续跑轮里自陈「wiki 中没有找到相关资料」，
    可**首轮明明检索到了 10 条**（v1.3 §12.4）。结果续跑只把「框架崩了」
    换成「诚实说不知道」，没换来命中率（6/15 vs 5/15，Fisher p=0.775）。

    修法：把首轮引用拼进提示语，让模型有据可依，而不是被蒙着眼答题。

    ⚠️ **当前默认不启用**（`CONTINUE_WITH_REFS = False`）：A/B 未达显著且延迟翻倍。
    调用方用 `chat_resilient(..., with_refs=True)` 显式开启。
    """
    if not refs or not CONTINUE_WITH_REFS:
        return CONTINUE_PROMPT_BASE
    lines = []
    for r in refs[:CONTINUE_REF_MAX]:
        p = (r.get("path") or "").strip()
        t = (r.get("title") or "").strip()
        if not (p or t):
            continue
        lines.append("- " + (f"{t}（{p}）" if t and p and t != p else (t or p)))
    if not lines:
        return CONTINUE_PROMPT_BASE
    return (CONTINUE_PROMPT_BASE
            + "\n\n首轮已检索到以下资料，请优先依据它们作答：\n"
            + "\n".join(lines))
LIMIT_MARK = "tool-iteration limit"
RAW_DUMP_MARK = "I found the following relevant project context"
JSON_DUMP_MARKS = ('```json', '{"action"')

# ---------------------------------------------------------------------------
# 框架失败续跑（v1.2）
def chat_resilient(message, timeout=180, max_continue=CONTINUE_MAX, with_refs=None):
    """提问；若框架失败则带 sessionId 补跑，直到拿到答案或用尽补跑次数。

    续跑轮被明令「不要再调用任何工具」，所以可以把**首轮引用**塞进提示语，
    否则模型会以为「没检索到任何东西」（见 build_continue_prompt）。

    with_refs：是否启用「带首轮引用」。默认取全局 `CONTINUE_WITH_REFS`；
    ⚠️ 该开关**当前默认关闭**——A/B 未达显著（p=0.185）且延迟翻倍，见 v1.3 §12.8。
    """
    if with_refs is None:
        with_refs = CONTINUE_WITH_REFS
    data = chat(message, timeout=timeout)
    sid = data.get("sessionId")
    reasons = []
    first_refs = extract_answer(data).get("references") or [] if with_refs else []
    while sid and len(reasons) < max_continue:
        why = framework_failure(data)
        if not why:
            break
        reasons.append(why)
        print(f"    ↻ 框架失败（{why}），带 sessionId + 首轮 {len(first_refs)} 条引用"
              f"补第 {len(reasons)} 轮")
        data = chat(build_continue_prompt(first_refs), timeout=timeout, session_id=sid)
        sid = data.get("sessionId") or sid
    return data, reasons


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

    # --only C3,O4,P1  只跑指定题（快速复测）; --repeat N 每题重复; --tag X 另存结果
    only = None
    if "--only" in sys.argv:
        i = sys.argv.index("--only")
        only = [x.strip().upper() for x in sys.argv[i + 1].split(",") if x.strip()]
    repeat = 1
    if "--repeat" in sys.argv:
        repeat = max(1, int(sys.argv[sys.argv.index("--repeat") + 1]))
    tag = None
    if "--tag" in sys.argv:
        tag = sys.argv[sys.argv.index("--tag") + 1]

    global TOP_K
    if "--topk" in sys.argv:
        TOP_K = int(sys.argv[sys.argv.index("--topk") + 1])
    print(f"检索深度 topK = {TOP_K}")

    enable_continue = "--no-continue" not in sys.argv
    print(f"框架失败续跑 = {'开' if enable_continue else '关（对照臂）'}")

    global QUESTIONS, NON_SCORING
    if "--set" in sys.argv:
        set_path = sys.argv[sys.argv.index("--set") + 1]
        QUESTIONS = load_question_set(set_path)
        # 扩展集是自动生成的，判定口径不同（引用可溯源），没有「待业务确认」项
        NON_SCORING = set()
        print(f"题目集 = {set_path}（{len(QUESTIONS)} 题）")

    keys = [target] if target else list(QUESTIONS.keys())
    if only:
        unknown = [k for k in only if k not in QUESTIONS]
        if unknown:
            print(f"未知题号: {unknown}")
            return
        keys = only
    # 展开重复次数：C3、C3#2、C3#3 …
    run_keys = []
    for k in keys:
        for r in range(repeat):
            run_keys.append(k if r == 0 else f"{k}#{r + 1}")

    print(f"项目 ID: {PROJECT_ID or '(未解析到，回退 current)'}")
    print(f"共 {len(run_keys)} 次提问（{len(keys)} 题 × {repeat} 次），模型问答走 {API_BASE}")
    results = {}
    for key in run_keys:
        base_key = key.split("#")[0]
        q = QUESTIONS[base_key]
        t0 = time.time()
        try:
            data, reasons = chat_resilient(
                q, max_continue=CONTINUE_MAX if enable_continue else 0)
            parsed = extract_answer(data)
            parsed["elapsed"] = round(time.time() - t0, 1)
            parsed["scoring"] = base_key not in NON_SCORING
            parsed["base_key"] = base_key
            parsed["continue_rounds"] = len(reasons)
            parsed["continue_reasons"] = reasons
            results[key] = parsed
            if probe:
                print(f"===== {key} 完整事件结构 =====")
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                tag_s = "" if base_key not in NON_SCORING else "  [不计分·待业务确认]"
                n_ref = len(parsed["references"]) or len(parsed["event_refs"])
                src = "引用" if parsed["references"] else "召回"
                cs = f" | 续跑 {parsed['continue_rounds']} 轮" if parsed["continue_rounds"] else ""
                print(f"[{key}] 用时 {parsed['elapsed']}s | {src} {n_ref} 条{tag_s}{cs}")
                print(f"    答: {parsed['answer'][:120].strip()}")
        except Exception as ex:
            results[key] = {"error": str(ex), "elapsed": round(time.time() - t0, 1),
                            "base_key": base_key}
            print(f"[{key}] 失败: {ex}")

    if not probe:
        name = f"评测结果_raw_{tag}.json" if tag else "评测结果_raw.json"
        out = BASE / "eval" / name
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        n_scoring = len([k for k in keys if k not in NON_SCORING])
        print(f"\n共 {len(keys)} 题（计分 {n_scoring}，不计分 {len(keys) - n_scoring}）")
        print(f"结果已写入 {out}")


if __name__ == "__main__":
    main()
