# -*- coding: utf-8 -*-
"""阶段 1 补充：docx 内嵌截图 OCR（智谱 GLM-4V）+ 脱敏。

背景：13 份 DLS 培训手册是「图文并茂」的——正文抽取只拿到几千字符，因为绝大部分
内容是内嵌的界面截图（实测 573 张 ≥20KB）。抽样确认 61 张独立截图 = 至少 40 张独立截图
与手册内嵌图内容一致，但手册内嵌图覆盖 13 个模块，是「操作类知识」的主体。

流程：
1. 从每个 docx 的 word/media/ 提取位图 → 落到 data/docx_media/<手册名>/
2. 全局按 md5 去重（同一张图在多份手册里重复，只 OCR 一次）
3. 调 GLM-4V 转写，结果缓存到 data/ocr_cache_docx/（断点续跑）
4. 按手册聚合 → data/clean/<原目录>/<手册名>_截图OCR.txt

不修改 工作内容/ 下任何原始文件（只读 zip，另存到 data/）。
用法：
    python ocr_docx_media.py --limit 10
    python ocr_docx_media.py --min-bytes 20480
"""
import argparse
import hashlib
import re
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ocr_images import call_vlm
from clean_text import desensitize, topic_for, SRC, OUT
from config import BASE, load_zhipu

STAGE = BASE / "data" / "docx_media"
CACHE = BASE / "data" / "ocr_cache_docx"
BITMAP = (".png", ".jpg", ".jpeg")
SUFFIX = re.compile(r"\d+|\D+")


def natkey(name: str):
    """自然排序：image2.png 排在 image10.png 之前（保持手册内的阅读顺序）。"""
    return [int(t) if t.isdigit() else t.lower() for t in SUFFIX.findall(name)]


def safe_name(name: str) -> str:
    """Windows 会静默去掉路径末尾的空格/点（如「…成品出运管理 」），这里先去掉，
    否则 mkdir 建出的目录名与后续写入用的路径不一致，会报 FileNotFoundError。"""
    return name.rstrip(" .") or "unnamed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="glm-4v-flash")
    ap.add_argument("--limit", type=int, default=0, help="只 OCR 前 N 张（试跑）")
    ap.add_argument("--min-bytes", type=int, default=20480, help="小于此体积视为图标，跳过")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--extract-only", action="store_true", help="只提取内嵌图到 data/docx_media/，不调 API")
    args = ap.parse_args()

    key, base = load_zhipu()
    if not key:
        print("✗ 未读到 ZHIPU_API_KEY（应在 scripts/.env）")
        sys.exit(1)

    # ---- 1) 提取内嵌图 + 全局去重 ----
    docs = sorted(SRC.rglob("*.docx"))
    seen = {}          # md5 -> 首次出现的 (docx_rel, staged_path)
    per_doc = {}       # docx_rel -> [(staged_path, name)]
    extracted = dup = tiny = 0

    for doc in docs:
        rel = doc.relative_to(SRC)
        with zipfile.ZipFile(doc) as z:
            media = [n for n in z.namelist() if n.startswith("word/media/")]
            for n in sorted(media, key=natkey):
                if Path(n).suffix.lower() not in BITMAP:
                    continue
                data = z.read(n)
                if len(data) < args.min_bytes:
                    tiny += 1
                    continue
                h = hashlib.md5(data).hexdigest()
                if h in seen:
                    dup += 1
                    continue
                dest = STAGE / safe_name(rel.stem) / Path(n).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                seen[h] = (rel, dest)
                per_doc.setdefault(rel, []).append(dest)
                extracted += 1

    print(f"docx: {len(docs)} 份 ｜ 取出位图 {extracted} 张（去重 {dup}，跳过小图 {tiny}）")
    if args.extract_only:
        print(f"已提取到 {STAGE}（未调 API）")
        for d, ps in sorted(per_doc.items()):
            print(f"  {d.stem[:44]:<46} {len(ps):>4} 张")
        return
    print(f"涉及手册: {len(per_doc)} 份 ｜ 模型 {args.model} ｜ 并发 {args.workers}")
    print("=" * 60)

    # ---- 2) OCR（带缓存）----
    jobs = [(d, p) for d, ps in per_doc.items() for p in ps]
    if args.limit:
        jobs = jobs[: args.limit]

    def work(doc_rel, img: Path):
        cache = CACHE / safe_name(doc_rel.stem) / img.name.replace(img.suffix, ".txt")
        if cache.exists() and not args.force:
            return doc_rel, img, cache, None
        try:
            text = call_vlm(img, key, base, args.model)
        except Exception as e:  # noqa: BLE001
            return doc_rel, img, cache, str(e)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
        return doc_rel, img, cache, None

    ok, failed = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (doc_rel, img, cache, err) in enumerate(
            pool.map(lambda j: work(*j), jobs), 1
        ):
            if err:
                failed.append((f"{doc_rel.name}/{img.name}", err))
                print(f"[{i}/{len(jobs)}] ✗ {img.name}  {err}", flush=True)
            else:
                ok.append((doc_rel, img, cache))
                print(f"[{i}/{len(jobs)}] ✓ {doc_rel.stem[:30]} / {img.name}", flush=True)

    # ---- 3) 按手册聚合写出 ----
    by_doc = {}
    for doc_rel, img, cache in ok:
        text = cache.read_text(encoding="utf-8").strip()
        if text:
            by_doc.setdefault(doc_rel, []).append((img, text))

    total_hits, written = {}, []
    for doc_rel, items in sorted(by_doc.items()):
        merged = "\n\n".join(f"【{img.name}】\n{t}" for img, t in items)
        cleaned, hits = desensitize(merged)
        for k, v in hits.items():
            total_hits[k] = total_hits.get(k, 0) + v
        src_dir, _ = desensitize(str(doc_rel.parent))
        src_name, _ = desensitize(doc_rel.name)
        header = (
            "# ---- 元数据 ----\n"
            f"# 来源目录: {src_dir}\n"
            f"# 业务主题: {topic_for(doc_rel)}\n"
            f"# 原文件名: {src_name}\n"
            f"# 图片数: {len(items)}（手册内嵌截图 OCR）\n"
            f"# OCR 模型: {args.model}\n"
            "# 已脱敏: 是\n"
            "# ---- 正文 ----\n\n"
        )
        rel_clean_dir = Path(*[desensitize(p)[0] for p in doc_rel.parent.parts])
        dest = OUT / rel_clean_dir / f"{safe_name(desensitize(doc_rel.stem)[0])}_截图OCR.txt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(header + cleaned, encoding="utf-8")
        written.append((str(dest.relative_to(OUT)), len(items), len(cleaned)))

    print("=" * 60)
    print(f"OCR 成功 {len(ok)} 张 → {len(written)} 个手册文件  失败 {len(failed)}")
    print("脱敏统计:", total_hits)
    print("-" * 60)
    for name, n, ch in written:
        print(f"  {name}  [{n} 张 / {ch} 字符]")
    if failed:
        print("\n失败清单:")
        for name, err in failed:
            print(f"  {name}  ->  {err}")


if __name__ == "__main__":
    main()
