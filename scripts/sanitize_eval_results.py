# -*- coding: utf-8 -*-
"""评测产物擦除：把「编译阶段派生的真实专名」从 eval/ 产物里擦掉（提交前必跑）。

为什么需要它
------------
原始语料在入库前已由 `clean_text.py` 脱敏，所以 `data/clean/` 是干净的。
但 LLM 编译时会从**截图 OCR** 里派生出**新的真实实体页**——真实订单号、
配送中心代码、第三方物流商名、客户产品系列。这些页面平时只存在于
**gitignored** 的 `projects/.../wiki/` 里，不构成泄漏。

问题出在**检索面变宽之后**：把检索深度（topK）从默认值提到 15 后，chat API 的
`references` 不再只返回少数关键词命中页，而是会把语义相近的**真实实体页**也带回来，
于是它们的 `path` / `title` 就写进了 `eval/评测结果_raw*.json`——而 `eval/` 是**入库的**。

实测（2026-09-12）：v1.0 评测结果里出现了 `wiki/entities/purchase-order-<真实订单号>.md`、
`wiki/entities/<真实物流商>-LOGISTICS.md`，而上一轮（默认检索深度、引用面窄）的产物是干净的。
即 **检索面变宽扩大了外泄面**，必须加一道擦除闸门。
（注：当时以为是「开向量检索」所致，后经复核证伪——向量那一路全程未生效。）

用法
----
    python sanitize_eval_results.py --check   # 只报告，发现残留退出码 1（可挂 CI）
    python sanitize_eval_results.py --apply   # 就地擦除（长串优先，见 maps_local.EVAL_SCRUB）
    python sanitize_eval_results.py --apply --include-logs   # 连 *.log 一起擦

被擦的词来自 gitignored 的 `maps_local.py`（`EVAL_SCRUB`），公开仓库里不留明文。
"""
import argparse
import sys
from pathlib import Path

from config import BASE

try:
    from maps_local import EVAL_SCRUB
except ImportError:  # maps_local 缺失时退化为空表（不擦任何东西，但脚本仍可跑）
    EVAL_SCRUB = []

EVAL_DIR = BASE / "eval"
SUFFIXES = (".json", ".md", ".csv")
LOG_SUFFIXES = (".log",)


def targets(include_logs: bool):
    if not EVAL_DIR.exists():
        return []
    exts = SUFFIXES + (LOG_SUFFIXES if include_logs else ())
    return sorted(p for p in EVAL_DIR.iterdir() if p.is_file() and p.suffix.lower() in exts)


def scan(path: Path):
    """返回 [(term, count, 替换目标)]。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for term, repl in EVAL_SCRUB:
        n = text.count(term)
        if n:
            out.append((term, n, repl))
    return out


def apply_scrub(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    before = text
    hits = {}
    for term, repl in EVAL_SCRUB:          # 顺序即长度优先，见 maps_local 注释
        n = text.count(term)
        if n:
            text = text.replace(term, repl)
            hits[term] = n
    if text != before:
        path.write_text(text, encoding="utf-8")
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="就地擦除（默认只检查）")
    ap.add_argument("--check", action="store_true", help="只检查（默认行为）")
    ap.add_argument("--include-logs", action="store_true", help="连 *.log 一起处理")
    args = ap.parse_args()

    if not EVAL_SCRUB:
        print("!! maps_local.EVAL_SCRUB 为空（词表缺失），未做任何擦除")
        return 1

    files = targets(args.include_logs)
    print(f"擦除词条 {len(EVAL_SCRUB)} 条 ｜ 目标 {len(files)} 个文件"
          f" ｜ 模式 {'apply' if args.apply else 'check'}")
    print("=" * 64)

    total = 0
    bad = []
    for p in files:
        if args.apply:
            hits = apply_scrub(p)
        else:
            hits = dict((t, n) for t, n, _ in scan(p))
        if not hits:
            continue
        total += sum(hits.values())
        bad.append(p)
        print(f"\n{'✎' if args.apply else '✗'} {p.name}")
        for t, n in hits.items():
            print(f"    {n:>3} 处  {t!r}")

    print("=" * 64)
    if args.apply:
        print(f"{'✅ 已擦除' if total else '✅ 无需擦除'}：{len(bad)} 个文件、{total} 处")
        return 0
    if total:
        print(f"⚠️  发现残留：{len(bad)} 个文件、{total} 处 → 跑 --apply 擦除")
        return 1
    print(f"✅ 无残留：扫描 {len(files)} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
