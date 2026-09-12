# -*- coding: utf-8 -*-
"""知识覆盖度检查：源素材 vs 知识库实际入库情况。

回答一个核心问题：**「工作内容/ 里的东西，是不是都进知识库了？」**

做法：不是简单比文件名（脱敏会重命名，如「张三」→「学员A」），而是解析
每份入库语料头部元数据里的「原文件名」字段，反向追溯到源素材。同时单独统计
图片（OCR 产物）、PDF、XLSX 三类容易漏掉的素材。

用法：
  python check_coverage.py            # 打印覆盖度报告
  python check_coverage.py --missing  # 只列未导入的文件

只读，不修改任何文件。
"""
import os
import re
import sys
import json
import collections

from config import BASE

PROJ = BASE / "projects" / "training-qa" / "training-qa"
WIKI = PROJ / ".llm-wiki"
SOURCES = PROJ / "raw" / "sources"
SRC = BASE / "工作内容"

IMAGE_EXT = (".png", ".jpg", ".jpeg")
# 自己写的处理脚本，不是业务知识，本就不应入库
TOOL_EXT = (".py",)


def norm(name):
    """规范化文件名用于比对：脱敏会改名（张三→学员A），故抹掉易变部分。

    例：核价重点_v1_张三.xlsx 与 核价重点_v1_学员A.xlsx → 均归一为「核价重点」
    """
    s = os.path.splitext(os.path.basename(name))[0]
    s = re.sub(r"_v\d+_.*$", "", s)          # 去掉 _v1_学员A 之类版本+署名
    s = re.sub(r"\(\d+\)$", "", s).strip()   # 去掉 (1) 副本标记
    return s


def origin_map():
    """入库文件 -> 原文件名（从元数据头解析）。"""
    out = {}
    if not SOURCES.exists():
        return out
    for f in sorted(os.listdir(SOURCES)):
        head = (SOURCES / f).read_text(encoding="utf-8", errors="ignore")[:3000]
        m = re.search(r"原文件名:\s*(.+)", head)
        out[f] = m.group(1).strip() if m else ""
    return out


def compile_progress():
    """(已编译份数, 队列 processing, 队列 pending)。"""
    entries, processing, pending = 0, 0, 0
    cache = WIKI / "ingest-cache.json"
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        e = d.get("entries", d)
        entries = len(e) if isinstance(e, (dict, list)) else 0
    q = WIKI / "ingest-queue.json"
    if q.exists():
        arr = json.loads(q.read_text(encoding="utf-8"))
        if isinstance(arr, list):
            for t in arr:
                if t.get("status") == "processing":
                    processing += 1
                else:
                    pending += 1
    return entries, processing, pending


def walk_files(base, exts=None):
    out = []
    if not base.exists():
        return out
    for dp, _dn, fn in os.walk(base):
        for f in fn:
            if exts is None or os.path.splitext(f)[1].lower() in exts:
                out.append(os.path.join(dp, f))
    return out


def main():
    only_missing = "--missing" in sys.argv

    origin = origin_map()
    entries, processing, pending = compile_progress()
    staged = sorted(os.listdir(SOURCES)) if SOURCES.exists() else []

    print("=" * 66)
    print("知识覆盖度检查")
    print("=" * 66)
    print(f"  源素材目录   : {SRC}")
    print(f"  入库语料目录 : {SOURCES}")
    print(f"  编译进度     : {entries} 份已编译 | processing={processing} pending={pending}")
    wiki_pages = walk_files(PROJ / "wiki", (".md",))
    print(f"  wiki 页面    : {len(wiki_pages)} 页")

    # --- 按扩展名统计源素材，逐类追溯 ---
    all_src = walk_files(SRC)
    by_ext = collections.defaultdict(list)
    for p in all_src:
        by_ext[os.path.splitext(p)[1].lower()].append(p)

    # 已导入的原文件名（去重）
    imported = {o for o in origin.values() if o}

    print()
    print("-" * 66)
    print("【按类型的入库情况】")
    print("-" * 66)
    missing_all = []
    imported_norm = {norm(o) for o in imported if o}
    empty_files, tool_files, img_files = [], [], []

    for ext in sorted(by_ext, key=lambda e: -len(by_ext[e])):
        files = by_ext[ext]
        # 工具脚本 / 图片单独归类，不计入「知识缺口」
        if ext in TOOL_EXT:
            tool_files += files
            print(f"  {ext:<8} 源 {len(files):>3} 份 | 工具脚本，不入库（非业务知识）")
            continue
        if ext in IMAGE_EXT:
            img_files += files
            print(f"  {ext:<8} 源 {len(files):>3} 份 | 经 OCR 转文本入库（见下节）")
            continue

        done, miss, empty = [], [], []
        for p in files:
            base = os.path.basename(p)
            if os.path.getsize(p) == 0:          # 空文件本身就无内容可导
                empty.append(p)
            elif norm(base) in imported_norm:
                done.append(p)
            else:
                miss.append(p)
        missing_all += miss
        empty_files += empty
        note = f" | 空文件 {len(empty)}" if empty else ""
        print(f"  {ext or '(无扩展)':<8} 源 {len(files):>3} 份 | 已入 {len(done):>3}"
              f" | 未入 {len(miss):>3}{note}")
        for p in sorted(empty):
            print(f"      o 空文件（无需导入）: {os.path.relpath(p, BASE)}")
        if miss and not only_missing:
            for p in sorted(miss):
                print(f"      x {os.path.relpath(p, BASE)}")

    # --- 图片 OCR 单独看 ---
    print()
    print("-" * 66)
    print("【图片 / 截图 OCR】")
    print("-" * 66)
    imgs = walk_files(SRC, IMAGE_EXT)
    ocr_files = [f for f in staged if "OCR" in f]
    total_ocr_imgs = 0
    for f in ocr_files:
        head = (SOURCES / f).read_text(encoding="utf-8", errors="ignore")[:4000]
        m = re.search(r"图片数:\s*(\d+)", head)
        if m:
            total_ocr_imgs += int(m.group(1))
    print(f"  源目录内图片文件     : {len(imgs)}")
    print(f"  OCR 产物文件         : {len(ocr_files)}（已入库）")
    print(f"  OCR 覆盖的图片总数   : {total_ocr_imgs}")
    if imgs:
        d = collections.Counter(os.path.dirname(os.path.relpath(p, BASE)) for p in imgs)
        for k, n in d.most_common():
            print(f"      {n:>3}  {k}")

    # --- 未导入汇总 ---
    print()
    print("-" * 66)
    total_src = len(all_src)
    print(f"【未导入汇总】共 {len(missing_all)} 份 / 源素材 {total_src} 份")
    print(f"                （不计入：空文件 {len(empty_files)}、工具脚本 {len(tool_files)}、"
          f"图片 {len(img_files)}）")
    print("-" * 66)
    if not missing_all:
        print("  无 —— 源素材已全部入库。")
    else:
        buckets = collections.defaultdict(list)
        for p in missing_all:
            name = os.path.basename(p)
            if re.search(r"OriginalOrder|order_\d", name, re.I):
                buckets["客户原始订单 PDF（有意排除的备查原件）"].append(p)
            else:
                buckets["其他"].append(p)
        for k, v in buckets.items():
            print(f"  · {k}: {len(v)} 份")
            if not only_missing:
                for p in sorted(v)[:30]:
                    print(f"      - {os.path.relpath(p, BASE)}")
                if len(v) > 30:
                    print(f"      ...（共 {len(v)} 份）")
    print()


if __name__ == "__main__":
    main()
