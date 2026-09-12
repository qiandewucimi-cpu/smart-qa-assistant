# -*- coding: utf-8 -*-
"""阶段 1 补充：pdf 抽文字 + 脱敏。

PDF 不是 zip+XML，无法用标准库解析，故引入 pymupdf（PyMuPDF）作为**可选依赖**
——与 feishu_bot.py 需 lark-oapi 同理。用 pdf2md 虚拟环境运行：

    C:/Users/31114/.workbuddy/binaries/python/envs/pdf2md/Scripts/python.exe extract_pdf.py

本脚本不装依赖、不改源文件，只读 工作内容/**.pdf，输出到 data/clean/。

收录策略（重要）：
- 默认**排除**「客户原始订单」PDF（*OriginalOrder.pdf / order_*.pdf）：它们是客户真实
  单据样本（含英文客户抬头、真实公司名、地址、订单号），属于「备查原件」而非培训知识，
  与 docs/数据盘点表.md §六.6 的结论一致。加 `--include-orders` 可强制收录。
- 内容重复的 PDF（同 md5）只保留第一份。
- 没有文本层的 PDF 跳过并单独列出（留给阶段 3 的 OCR 处理）。
用法：python extract_pdf.py [--include-orders]
"""
import hashlib
import re
import sys
from pathlib import Path

import pymupdf

from clean_text import desensitize, topic_for, SRC, OUT

# 客户原始订单文件名特征：xxxOriginalOrder.pdf / order_xxx.pdf / 无名的 .pdf
ORIGINAL_ORDER_RE = re.compile(r"(OriginalOrder|^order[_\s]|^\.)", re.IGNORECASE)
# 低于这个字符数视为「无文本层」（扫描件/纯截图），交给 OCR 阶段
MIN_CHARS = 50


def extract_pdf_text(path: Path) -> tuple:
    """返回 (正文文本, 页数, 有文本层的页数)。"""
    doc = pymupdf.open(path)
    try:
        pages, with_text = [], 0
        for i, page in enumerate(doc, 1):
            t = page.get_text("text", sort=True).strip()
            if t:
                with_text += 1
            pages.append(f"【第 {i} 页】\n{t}")
        return "\n\n".join(pages).strip(), doc.page_count, with_text
    finally:
        doc.close()


def out_name(rel: Path) -> str:
    """输出文件名：正常取 stem；源文件名异常（以 . 开头、无 stem）时按目录名兜底。"""
    stem = rel.stem
    if not stem or stem.startswith("."):
        return f"{rel.parent.name}（无名文件）.txt"
    return stem + ".txt"


def main():
    include_orders = "--include-orders" in sys.argv
    files = sorted(SRC.rglob("*.pdf"))

    total_hits = {}
    done, failed, no_text, skipped_orders, dup = [], [], [], [], []
    seen = {}

    for f in files:
        rel = f.relative_to(SRC)
        if ORIGINAL_ORDER_RE.search(rel.name) and not include_orders:
            skipped_orders.append(str(rel))
            continue
        h = hashlib.md5(f.read_bytes()).hexdigest()
        if h in seen:
            dup.append(f"{rel}  ==  {seen[h]}")
            continue
        seen[h] = str(rel)

        try:
            text, n_pages, n_text = extract_pdf_text(f)
        except Exception as e:  # noqa: BLE001
            failed.append((str(rel), str(e)))
            continue
        if len(text) < MIN_CHARS:
            no_text.append(f"{rel}  (页 {n_pages} / 有文字页 {n_text})")
            continue

        cleaned, hits = desensitize(text)
        for k, v in hits.items():
            total_hits[k] = total_hits.get(k, 0) + v

        src_dir, _ = desensitize(str(rel.parent or "."))
        src_name, _ = desensitize(rel.name)
        header = (
            "# ---- 元数据 ----\n"
            f"# 来源目录: {src_dir}\n"
            f"# 业务主题: {topic_for(rel)}\n"
            f"# 原文件名: {src_name}\n"
            f"# 页数: {n_pages}\n"
            "# 已脱敏: 是\n"
            "# ---- 正文 ----\n\n"
        )
        rel_clean_dir = Path(*[desensitize(p)[0] for p in rel.parent.parts])
        dest = OUT / rel_clean_dir / desensitize(out_name(rel))[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(header + cleaned, encoding="utf-8")
        done.append((str(dest.relative_to(OUT)), n_pages, len(text)))

    print("=" * 60)
    print(f"pdf 抽文字完成: {len(done)} 个  失败: {len(failed)}  无文本层: {len(no_text)}")
    print(f"跳过·客户原始订单: {len(skipped_orders)}  跳过·重复内容: {len(dup)}")
    print("脱敏统计:", total_hits)
    print("-" * 60)
    for name, pg, ch in done:
        print(f"  {name}  [{pg} 页 / {ch} 字符]")
    if skipped_orders and not include_orders:
        print("\n跳过（客户原始订单 / 无名文件，属备查原件 —— 加 --include-orders 可强制收录）:")
        for s in skipped_orders:
            print("  " + s)
    if dup:
        print("\n跳过（内容重复，保留首份）:")
        for s in dup:
            print("  " + s)
    if no_text:
        print("\n无文本层（需 OCR，留给阶段 3）:")
        for s in no_text:
            print("  " + s)
    if failed:
        print("\n失败:")
        for rel, e in failed:
            print(f"  {rel}  ->  {e}")


if __name__ == "__main__":
    main()
