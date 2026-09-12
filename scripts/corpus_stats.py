# -*- coding: utf-8 -*-
"""语料与脱敏统计（**只读**，不写任何文件）。

用途：给简历/报告回填可复算的数字。
    python corpus_stats.py

统计内容：
  1. data/clean 的成品规模（文件数 / 字符数 / 按主题分布）
  2. 脱敏命中复算：对「原始语料」（工作内容 txt+md、截图 OCR 原始缓存）重跑
     clean_text.desensitize，得到**真实命中次数**（可按类别汇总）
     —— 注意：只覆盖「可复算」的来源；docx/xlsx/PDF 抽取阶段走同一套规则，
        但其中间文本未缓存，故不重复计数（避免与 txt 重叠导致虚高）。

设计原则：只读。绝不写入 data/clean 或工作内容/。
"""
import os
from collections import Counter
from pathlib import Path

from config import BASE
from clean_text import desensitize, SRC

CLEAN = BASE / "data" / "clean"
OCR_CACHES = [BASE / "data" / "ocr_cache", BASE / "data" / "ocr_cache_docx"]

TEXT_EXT = {".txt", ".md"}


def count_clean():
    files = [p for p in CLEAN.rglob("*") if p.is_file()]
    chars = 0
    by_topic = Counter()
    for p in files:
        rel = p.relative_to(CLEAN)
        top = rel.parts[0] if len(rel.parts) > 1 else "(根)"
        by_topic[top] += 1
        try:
            chars += len(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return files, chars, by_topic


def recount(paths, label):
    """对一批原始文件重跑 desensitize，累计命中。"""
    hits = Counter()
    n_files = 0
    for root in paths:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in TEXT_EXT:
                continue
            if p.name == "maps_local.py":
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not text.strip():
                continue
            _, h = desensitize(text)
            n_files += 1
            for k, v in h.items():
                hits[k] += v
    print(f"[{label}] 复算文件 {n_files} 个，命中合计 {sum(hits.values())} 处")
    for k, v in hits.most_common():
        print(f"    {k}: {v}")
    return hits


def main():
    print("=" * 60)
    print("1) data/clean 成品规模")
    files, chars, by_topic = count_clean()
    print(f"   文件数: {len(files)}   总字符: {chars:,}")
    print("   按顶层目录:")
    for k, v in by_topic.most_common():
        print(f"     {k}: {v}")

    print()
    print("2) 脱敏命中复算（对原始语料重跑规则，只读）")
    h_text = recount([SRC], "文本语料 工作内容 txt/md")
    print()
    h_ocr = recount(OCR_CACHES, "截图 OCR 原始缓存")
    total = sum(h_text.values()) + sum(h_ocr.values())
    print()
    print("=" * 60)
    print(f"复算脱敏命中合计: {total:,} 处"
          f"（文本 {sum(h_text.values()):,} + 截图OCR {sum(h_ocr.values()):,}）")
    print("说明：docx/xlsx/PDF 抽取阶段同样过 clean_text.desensitize，"
          "但中间文本未缓存，未纳入复算以避免重复计数。")


if __name__ == "__main__":
    main()
