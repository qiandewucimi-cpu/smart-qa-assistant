# -*- coding: utf-8 -*-
"""端到端问答延迟基准：切换问答模型配置，实测 /chat 的耗时与答案质量取舍。

背景：当前问答用 glm-4.7（推理型），端到端 P50 61.5s，是项目唯一未达标项。
本脚本在同一组真实问题上对比多个「问答模型配置」的**端到端耗时**与**答案要点覆盖**，
用于回答「换快模型会不会把质量换没了」——而不是拍脑袋选型。

做法：改 app-state.json 的 `llmConfig`（model / reasoning.mode），app 会热加载；
然后逐题调 `/api/v1/projects/{id}/chat`，记录耗时、引用数、是否拒答、要点命中。

用法：
    python bench_chat_latency.py                       # 跑全部预设配置
    python bench_chat_latency.py --configs 0,1         # 只跑第 0、1 个配置
    python bench_chat_latency.py --repeat 1            # 每配置每题的重复次数
    python bench_chat_latency.py --restore             # 跑完还原 app-state.json

结果写 `eval/延迟基准_<时间戳>.json`（含全部原文答案，供人工复核）。
"""
import argparse
import glob
import json
import shutil
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime

from config import API_BASE, BASE, load_app_state_path, load_token

STATE = str(load_app_state_path())
RETRY_STATUS = {429, 500, 502, 503, 504}

# 被测配置：(标签, model, reasoning_mode)
# reasoning_mode: "auto" 保持原样 / "off" 关思考
CONFIGS = [
    ("glm-4.7（当前基线）", "glm-4.7", "auto"),
    ("glm-4.5-air（快模型）", "glm-4.5-air", "auto"),
    ("glm-4.7 关思考", "glm-4.7", "off"),
]

# 抽样题：覆盖概念/流程/操作，且标准答案要点明确，便于判断质量是否掉
PROBES = [
    ("C1", "什么是「订单理单待核单」？", ["待核单", "生成", "制款"]),
    ("C3", "什么是「短溢装」？", ["溢装", "短装", "百分比"]),
    ("O2", "BOM 复制时应优先选哪种 BOM？", ["BOM", "优先"]),
    ("P3", "核单合并有哪几种条件？", ["合并", "条件"]),
    ("D2", "待核单和已核单有什么区别？", ["待核单", "已核单", "区别"]),
    ("Q3", "什么是「公共成本」？举例说明。", ["公共成本", "分摊"]),
]

REFUSAL_HINT = ["未能找到", "未找到", "未涉及", "没有找到", "缺少以下信息", "无法回答", "无法确定"]


def read_state():
    with open(STATE, encoding="utf-8") as f:
        return json.load(f)


def write_state(d):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def latest_backup():
    b = sorted(glob.glob(STATE + ".bak_*"))
    return b[-1] if b else None


def resolve_pid():
    tok = load_token()
    req = urllib.request.Request(API_BASE + "/api/v1/projects",
                                 headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    cp = d.get("currentProject") or {}
    if cp.get("id"):
        return cp["id"]
    for p in d.get("projects", []):
        if p.get("current"):
            return p["id"]
    return (d.get("projects") or [{}])[0].get("id")


def apply_config(model, reasoning_mode):
    d = read_state()
    d.setdefault("llmConfig", {})["model"] = model
    d["llmConfig"].setdefault("reasoning", {})["mode"] = reasoning_mode
    write_state(d)


def chat(pid, msg, timeout=300, top_k=15):
    tok = load_token()
    body = json.dumps({"message": msg, "stream": False, "topK": top_k}).encode()
    last = None
    for attempt in range(3):
        req = urllib.request.Request(
            f"{API_BASE}/api/v1/projects/{pid}/chat", data=body, method="POST",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            return round(time.time() - t0, 1), d, None
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code not in RETRY_STATUS:
                break
        except Exception as e:
            last = f"{type(e).__name__}: {e}"[:120]
        time.sleep(4 * (attempt + 1))
    return None, None, last


def extract(d):
    msg = d.get("message") or {}
    ans = msg.get("content", "") if isinstance(msg, dict) else str(msg)
    refs = d.get("references", []) or []
    usage = d.get("usage", {}) or {}
    return ans.strip(), len(refs), usage


def score(answer, keywords):
    if not answer:
        return 0.0, []
    hit = [k for k in keywords if k in answer]
    return len(hit) / len(keywords), hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()

    if a.restore:
        b = latest_backup()
        if b:
            shutil.copy2(b, STATE)
            print("已还原:", b.split("\\")[-1])
        return

    idx = [int(x) for x in a.configs.split(",")] if a.configs else list(range(len(CONFIGS)))
    pid = resolve_pid()
    print(f"项目 {pid}｜配置 {[CONFIGS[i][0] for i in idx]}｜每题重复 {a.repeat} 次\n")

    out = {"ts": datetime.now().isoformat(timespec="seconds"), "project": pid,
           "probes": [{"id": p[0], "q": p[1], "keywords": p[2]} for p in PROBES], "runs": []}

    for i in idx:
        label, model, rmode = CONFIGS[i]
        print("=" * 70)
        print(f"### {label}   (model={model}, reasoning={rmode})")
        apply_config(model, rmode)
        time.sleep(2)          # 等 app 热加载
        rows = []
        for qid, q, kws in PROBES:
            for rep in range(a.repeat):
                sec, d, err = chat(pid, q)
                if err:
                    print(f"  [{qid}] 失败: {err}")
                    rows.append({"id": qid, "sec": None, "error": err})
                    continue
                ans, nref, usage = extract(d)
                cov, hit = score(ans, kws)
                refused = any(h in ans for h in REFUSAL_HINT)
                rows.append({"id": qid, "sec": sec, "n_refs": nref, "len": len(ans),
                             "refused": refused, "coverage": round(cov, 2), "hit": hit,
                             "answer": ans, "usage": usage})
                print(f"  [{qid}] {sec:>6}s  引用{nref:>3}  要点{cov:>4.0%}  "
                      f"{'拒答' if refused else '作答'}  {len(ans)}字")
        secs = [r["sec"] for r in rows if r.get("sec")]
        covs = [r["coverage"] for r in rows if "coverage" in r]
        refs = [r["refused"] for r in rows if "refused" in r]
        summary = {
            "label": label, "model": model, "reasoning": rmode,
            "n": len(secs),
            "p50": round(statistics.median(secs), 1) if secs else None,
            "mean": round(statistics.fmean(secs), 1) if secs else None,
            "min": min(secs) if secs else None,
            "max": max(secs) if secs else None,
            "coverage_mean": round(statistics.fmean(covs), 3) if covs else None,
            "refusals": sum(1 for x in refs if x),
        }
        print(f"  → P50 {summary['p50']}s / 均值 {summary['mean']}s ｜ "
              f"要点覆盖均值 {summary['coverage_mean']} ｜ 拒答 {summary['refusals']}/{summary['n']}")
        out["runs"].append({"summary": summary, "rows": rows})

    outp = BASE / "eval" / f"延迟基准_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    outp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {outp}")

    print("\n" + "=" * 70)
    print(f"{'配置':<24}{'P50':>8}{'均值':>8}{'要点覆盖':>10}{'拒答':>6}")
    print("-" * 58)
    for r in out["runs"]:
        s = r["summary"]
        print(f"{s['label']:<24}{str(s['p50']):>8}{str(s['mean']):>8}"
              f"{str(s['coverage_mean']):>10}{str(s['refusals']):>6}")


if __name__ == "__main__":
    main()
