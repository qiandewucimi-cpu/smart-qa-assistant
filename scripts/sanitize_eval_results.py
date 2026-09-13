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

**同一条规则也用在「出站」**（2026-09-14 起）：`scripts/feishu_bot.py` 在把答案发回飞书前
调用本模块的 `scrub_text()` 擦一遍——这是最后一道闸门，因为机器人**直接把答案答给终端用户**，
其输出不经过 `eval/` 那条链路，闸门②覆盖不到（见 `docs/复盘报告.md` 坑 19 的延伸）。
擦除规则只有 `scrub_text()` 一份实现，文件擦除与出站擦除共用，避免漂移。

**两层词表**（2026-09-13 补第二层）：
  ① `EVAL_SCRUB`——手工维护的「已观测到的派生专名」，带指定替换文案；
  ② 完整真实词表（`clean_text` 的 NAME/COMPANY/CLIENT 映射）——兜住第一批没预料到的
     形态，统一替换为 `<已脱敏>`。加它的直接原因：`audit_leaks.py` 全量跑发现
     **入库的 wiki 目录里还留着真实地名/客户名**，而它们不在手工清单里。
     ASCII 词用词边界匹配，防短词命中长词内部（如 `ABC` 命中 `ABCDEF`）。
"""
import argparse
import re
import sys
from pathlib import Path

from config import BASE

try:
    from maps_local import EVAL_SCRUB
except ImportError:  # maps_local 缺失时退化为空表（不擦任何东西，但脚本仍可跑）
    EVAL_SCRUB = []

# ---------------------------------------------------------------------------
# 第二层：通用真实词表（2026-09-13 补）
#
# 为什么需要第二层：`EVAL_SCRUB` 是**手工维护**的「已观测到的派生专名」清单——
# 只有被撞见过一次的泄漏才会进表。而 `audit_leaks.py` 全量跑下来证明
# **入库的 wiki 目录里还有一批真实地名/客户名**（它们本应由 clean_text 在
# 抽取阶段替换掉，但那批页面是旧编译产物）。一旦某次检索引用到这些页面，
# 产物就会带上它们，而**手工清单里没有** → 闸门②会放行。
#
# 所以这里直接挂上**完整真实词表**（来自 gitignored 的 maps_local，公开仓库无明文），
# 兜住第一批没预料到的形态。实测（2026-09-13）：对现有 29 个产物 0 误报。
#
# ⚠️ ASCII 词必须用**词边界**：否则短词会命中长词的一部分（如 `ABC` 命中 `ABCDEF`），
# 造成误报（这是本项目记忆里明确记过的坑）。
# ---------------------------------------------------------------------------
GENERIC_REPL = "<已脱敏>"

try:
    from clean_text import CLIENT_MAP, COMPANY_MAP, NAME_MAP
    _GENERIC_TERMS = (set(NAME_MAP) | {k for k, _ in COMPANY_MAP} | set(CLIENT_MAP))
except ImportError:
    _GENERIC_TERMS = set()

_COVERED = {t for t, _ in EVAL_SCRUB}

# 通用层最短词长。2026-09-14 由 3 降到 2：
# 起因是给飞书机器人加**出站擦除**时发现，`>=3` 会把 16 个**两字中文词条**
# （真实姓名 / 简称 / 地名，如两字姓名、`内地`）漏掉——它们在评测产物里恰好没出现，
# 所以闸门②一直"通过"，但**用户的答案里可能出现**。
# 降到 2 后对现有 32 个产物复跑仍是 **0 命中**（无新增误报）；
# 两字中文词条几乎不与正常业务词重叠，误伤风险（如 `内地市`→`内地市`）远小于漏 PII 的代价。
MIN_TERM_LEN = 2


def generic_terms():
    """完整真实词表里、且不被 EVAL_SCRUB 覆盖的词（长串优先）。"""
    return sorted((t for t in _GENERIC_TERMS
                   if t and len(t.strip()) >= MIN_TERM_LEN and t not in _COVERED),
                  key=len, reverse=True)


def generic_pattern(term):
    if re.fullmatch(r"[A-Za-z0-9 _\-\.]+", term):
        return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])",
                          re.IGNORECASE)
    return re.compile(re.escape(term))


_GENERIC = [(t, generic_pattern(t)) for t in generic_terms()]

EVAL_DIR = BASE / "eval"
SUFFIXES = (".json", ".md", ".csv")
LOG_SUFFIXES = (".log",)


def targets(include_logs: bool):
    if not EVAL_DIR.exists():
        return []
    exts = SUFFIXES + (LOG_SUFFIXES if include_logs else ())
    return sorted(p for p in EVAL_DIR.iterdir() if p.is_file() and p.suffix.lower() in exts)


def scan(path: Path):
    """返回 [(term, count, 替换目标)]，两层词表合并（EVAL_SCRUB + 通用真实词表）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for term, repl in EVAL_SCRUB:
        n = text.count(term)
        if n:
            out.append((term, n, repl))
    for term, p in _GENERIC:
        n = len(p.findall(text))
        if n:
            out.append((term, n, GENERIC_REPL))
    return out


def scrub_text(text: str):
    """对一段文本执行两层擦除，返回 ``(新文本, {命中词: 次数})``。

    **单一来源**：文件擦除（`apply_scrub`）与**飞书机器人出站**都走这里——
    擦除规则只能有一份实现，否则两条链路会漂移（同 `eval_common` 的约定）。
    """
    hits = {}
    for term, repl in EVAL_SCRUB:          # 顺序即长度优先，见 maps_local 注释
        n = text.count(term)
        if n:
            text = text.replace(term, repl)
            hits[term] = n
    for term, p in _GENERIC:               # 第二层：正则（ASCII 带词边界）
        text, n = p.subn(GENERIC_REPL, text)
        if n:
            hits[term] = n
    return text, hits


def apply_scrub(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    new, hits = scrub_text(text)
    if new != text:
        path.write_text(new, encoding="utf-8")
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="就地擦除（默认只检查）")
    ap.add_argument("--check", action="store_true", help="只检查（默认行为）")
    ap.add_argument("--include-logs", action="store_true", help="连 *.log 一起处理")
    args = ap.parse_args()

    if not EVAL_SCRUB and not _GENERIC:
        print("!! 词表为空（maps_local / clean_text 均不可用），未做任何擦除")
        return 1

    files = targets(args.include_logs)
    print(f"擦除词条 {len(EVAL_SCRUB)} 条（指定替换） + {len(_GENERIC)} 条（通用真实词表，替换为 {GENERIC_REPL}）"
          f" ｜ 目标 {len(files)} 个文件"
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
