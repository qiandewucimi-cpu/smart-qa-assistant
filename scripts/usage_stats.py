# -*- coding: utf-8 -*-
"""用量统计：读 `data/usage_log.jsonl`，输出真实使用数据。

回答阶段 7 的问题：「到底服务了多少人、被问了多少次、延迟多少、多少题答不上来」。

数据来自 `feishu_bot.py` 的用量埋点（每次问答追加一行 JSON）。埋点**只记元数据**
（耗时 / 来源数 / 拒答标记 / 会话哈希），**不记问题原文**——业务提问可能自带敏感信息，
与项目「脱敏后才落盘」的口径一致。因此本脚本也无法还原问题内容，只能按哈希统计去重与频次。

用法：
    python usage_stats.py                 # 默认只统计飞书群聊真实用量
    python usage_stats.py --source cli    # 只看命令行自测
    python usage_stats.py --source all    # 全部
    python usage_stats.py --md            # 额外输出 Markdown 表格（贴进复盘报告用）
    python usage_stats.py --file <path>   # 指定日志文件

退出码：0 = 正常（含无数据）；1 = 日志文件不存在。
"""
import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

from config import BASE

DEFAULT_LOG = BASE / "data" / "usage_log.jsonl"


def load(path: Path):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # 写坏的行（如进程被杀）直接跳过
    return rows


def pct(n, d) -> str:
    return "—" if not d else f"{n / d * 100:.1f}%"


def percentile(vals, q):
    if not vals:
        return None
    vs = sorted(vals)
    if len(vs) == 1:
        return vs[0]
    idx = (len(vs) - 1) * q
    lo, hi = int(idx), min(int(idx) + 1, len(vs) - 1)
    return vs[lo] + (vs[hi] - vs[lo]) * (idx - lo)


def summarize(rows):
    ok = [r for r in rows if r.get("ok")]
    lat = [r["latency_s"] for r in ok if isinstance(r.get("latency_s"), (int, float))]
    refused = [r for r in ok if r.get("refused")]
    multi = [r for r in ok if r.get("multi_turn")]
    q_hashes = Counter(r.get("q_hash", "") for r in ok if r.get("q_hash"))
    chats = {r.get("chat", "") for r in rows if r.get("chat")}
    days = Counter((r.get("ts") or "")[:10] for r in rows if r.get("ts"))
    refs = [r.get("n_refs", 0) for r in ok if isinstance(r.get("n_refs"), int)]

    def r1(x):
        return "—" if x is None else round(x, 1)

    return {
        "total": len(rows),
        "ok": len(ok),
        "failed": len(rows) - len(ok),
        "unique_q": len(q_hashes),
        "unique_chats": len(chats),
        "p50": percentile(lat, 0.50),
        "p90": percentile(lat, 0.90),
        "mean": statistics.fmean(lat) if lat else None,
        "refused": len(refused),
        "multi": len(multi),
        "avg_refs": statistics.fmean(refs) if refs else None,
        "days": sorted(days.items()),
        "top_q": q_hashes.most_common(5),
        "span": (min(days) if days else "—", max(days) if days else "—"),
    }


def report(s: dict, source: str, path: Path):
    print("=" * 56)
    print(f"用量统计  source={source}")
    print(f"日志文件  {path}")
    print("=" * 56)
    if not s["total"]:
        print("（暂无数据。机器人跑起来并有人提问后，这里就会有数字。）")
        return
    print(f"时间范围          {s['span'][0]} ~ {s['span'][1]}")
    print(f"总提问数          {s['total']}")
    print(f"  成功 / 失败     {s['ok']} / {s['failed']}（成功率 {pct(s['ok'], s['total'])}）")
    print(f"去重提问数        {s['unique_q']}（去重率 {pct(s['unique_q'], max(s['ok'], 1))}）")
    print(f"唯一会话数        {s['unique_chats']}")
    print(f"延迟 P50/P90/均值 {s['p50']}s / {s['p90']}s / {round(s['mean'], 1) if s['mean'] else '—'}s")
    print(f"平均来源数        {round(s['avg_refs'], 1) if s['avg_refs'] else '—'}")
    print(f"拒答标记          {s['refused']}（{pct(s['refused'], s['ok'])}）")
    print(f"多轮追问占比      {pct(s['multi'], s['ok'])}（{s['multi']}/{s['ok']}）")
    print("按天分布          " + "，".join(f"{d}: {n}" for d, n in s["days"]))
    if s["top_q"]:
        print("高频提问（哈希后 5，去重计数）")
        for h, n in s["top_q"]:
            print(f"  {h}  ×{n}")


def markdown(s: dict, source: str) -> str:
    if not s["total"]:
        return "> 暂无用量数据（埋点已就绪，机器人运行后自动累积）。\n"
    def f(x, u=""):
        return "—" if x is None else f"{round(x, 1)}{u}"
    lines = [
        f"##### 真实用量（source={source}，{s['span'][0]} ~ {s['span'][1]}）",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 总提问数 | {s['total']}（成功 {s['ok']} / 失败 {s['failed']}） |",
        f"| 去重提问数 | {s['unique_q']} |",
        f"| 唯一会话数（人数近似） | {s['unique_chats']} |",
        f"| 延迟 P50 / P90 / 均值 | {f(s['p50'],'s')} / {f(s['p90'],'s')} / {f(s['mean'],'s')} |",
        f"| 平均来源引用数 | {f(s['avg_refs'])} |",
        f"| 拒答标记 | {s['refused']}（{pct(s['refused'], s['ok'])}） |",
        f"| 多轮追问占比 | {pct(s['multi'], s['ok'])} |",
        "",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["feishu", "cli", "all"], default="feishu")
    ap.add_argument("--file", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--md", action="store_true", help="额外输出 Markdown 表格")
    args = ap.parse_args()

    rows = load(args.file)
    if args.source != "all":
        rows = [r for r in rows if r.get("source") == args.source]
    s = summarize(rows)
    report(s, args.source, args.file)
    if args.md:
        print("\n---- Markdown ----\n")
        print(markdown(s, args.source))


if __name__ == "__main__":
    sys.exit(main())
