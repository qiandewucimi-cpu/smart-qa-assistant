# -*- coding: utf-8 -*-
"""阶段 1 补充：截图 OCR（智谱 GLM-4V）+ 脱敏。

DLS 系统界面截图无法用文本抽取（pdf 抽取会丢图片），只能用视觉模型读。
本脚本零依赖：只用 urllib + base64 调智谱 OpenAI 兼容接口。

三个设计要点：
1. **按页聚合**：图片名形如 p05_X121.png（手册第 5 页的第 N 张图），61 张碎图若各出一个
   文件，会变成 61 个 400 字的碎片，检索质量差。按页合并成 19 个页级文件（约 2~4KB/页）。
2. **结果缓存**：原图 OCR 结果落 data/ocr_cache/，重跑直接复用，不重复烧 token；
   只有 cache 缺失的图才会调 API（断点续跑）。
3. **脱敏**：OCR 文本同样过 clean_text.desensitize——截图里有银行账号、SWIFT 码、
   内网 IP、登录用户名，脱敏面比文本文档更大。

用法：
    python ocr_images.py                 # 全量（走缓存，缺的才调 API）
    python ocr_images.py --limit 3       # 只处理前 3 张（试跑）
    python ocr_images.py --model glm-4v-plus
    python ocr_images.py --force         # 忽略缓存，全部重跑
"""
import argparse
import base64
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from clean_text import desensitize, topic_for, SRC, OUT
from config import BASE, load_zhipu

CACHE = BASE / "data" / "ocr_cache"
IMG_EXT = (".png", ".jpg", ".jpeg")

# 提示词：要「忠实转写」而不是「概括」，这是知识库语料，失真等于污染。
PROMPT = (
    "你是文档数字化助手。请把这张系统界面截图的内容**完整、忠实**地转写成文字：\n"
    "1. 保留所有可见文字原文，包括字段名、按钮名、页签名、表头、单元格内容、提示语；\n"
    "2. 表格一律输出为 markdown 表格，保持原有的行列结构；\n"
    "3. 不要翻译、不要总结、不要补充推断，不要加任何解释或前后缀；\n"
    "4. 图中文字模糊无法辨认处，用 [模糊] 占位，**绝对不要编造**；\n"
    "5. 只输出转写结果本身。"
)

# 页级分组的键：p05_X121.png -> p05
PAGE_RE = re.compile(r"^(p\d+)_", re.IGNORECASE)

_print_lock = threading.Lock()


def log(msg: str):
    with _print_lock:
        print(msg, flush=True)


def page_key(name: str) -> str:
    m = PAGE_RE.match(name)
    return m.group(1).lower() if m else Path(name).stem


def call_vlm(img: Path, key: str, base: str, model: str, retries: int = 3) -> str:
    """调一次 GLM-4V，返回转写文本。带指数退避重试。"""
    b64 = base64.b64encode(img.read_bytes()).decode()
    body = json.dumps({
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
                {"type": "text", "text": PROMPT},
            ],
        }],
        "temperature": 0.01,
    }).encode()

    last = ""
    for attempt in range(retries):
        req = urllib.request.Request(
            base.rstrip("/") + "/chat/completions",
            data=body,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read().decode())
            return (d["choices"][0]["message"]["content"] or "").strip()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:160]
            last = f"HTTP {e.code}: {detail}"
            # 429 限流 / 5xx 服务端错误 → 退避重试；4xx 其他 → 直接失败
            if e.code == 429 or e.code >= 500:
                time.sleep(2 ** attempt * 2)
                continue
            break
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 ** attempt * 2)
    raise RuntimeError(last or "unknown error")


def ocr_one(img: Path, key: str, base: str, model: str, force: bool) -> tuple:
    """返回 (相对路径, 缓存文件, 是否命中缓存, 错误)。"""
    rel = img.relative_to(SRC)
    cache = CACHE / rel.with_suffix(".txt")
    if cache.exists() and not force:
        return rel, cache, True, None
    try:
        text = call_vlm(img, key, base, model)
    except Exception as e:  # noqa: BLE001
        return rel, cache, False, str(e)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return rel, cache, False, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="glm-4v-flash")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 张（试跑）")
    ap.add_argument("--force", action="store_true", help="忽略缓存重跑")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    key, base = load_zhipu()
    if not key:
        print("✗ 未读到 ZHIPU_API_KEY（应在 scripts/.env）")
        sys.exit(1)

    imgs = sorted(p for p in SRC.rglob("*") if p.suffix.lower() in IMG_EXT)
    if args.limit:
        imgs = imgs[: args.limit]
    if not imgs:
        print("没有找到图片")
        return

    print(f"模型: {args.model} ｜ 图片: {len(imgs)} 张 ｜ 并发: {args.workers}")
    print("=" * 60)

    results, failed = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(ocr_one, im, key, base, args.model, args.force) for im in imgs]
        for i, fut in enumerate(futures, 1):
            rel, cache, cached, err = fut.result()
            if err:
                failed.append((str(rel), err))
                log(f"[{i}/{len(imgs)}] ✗ {rel.name}  {err}")
            else:
                results.append((rel, cache))
                log(f"[{i}/{len(imgs)}] {'缓存' if cached else 'OCR '} {rel.name}")

    # 按页聚合写出
    groups = {}
    for rel, cache in results:
        text = cache.read_text(encoding="utf-8").strip()
        if not text:
            continue
        groups.setdefault(page_key(rel.name), []).append((rel, text))

    total_hits, written = {}, []
    for gkey, items in sorted(groups.items()):
        items.sort(key=lambda x: x[0].name)
        parts = []
        for rel, text in items:
            parts.append(f"【{rel.name}】\n{text}")
        merged = "\n\n".join(parts)
        cleaned, hits = desensitize(merged)
        for k, v in hits.items():
            total_hits[k] = total_hits.get(k, 0) + v

        first: Path = items[0][0]
        rel_dir = first.parent
        src_dir, _ = desensitize(str(rel_dir))
        header = (
            "# ---- 元数据 ----\n"
            f"# 来源目录: {src_dir}\n"
            f"# 业务主题: {topic_for(rel_dir)}\n"
            f"# 原文件名: {gkey}（{len(items)} 张截图的 OCR 合并）\n"
            f"# 图片数: {len(items)}\n"
            f"# OCR 模型: {args.model}\n"
            "# 已脱敏: 是\n"
            "# ---- 正文 ----\n\n"
        )
        rel_clean_dir = Path(*[desensitize(p)[0] for p in rel_dir.parts])
        dest = OUT / rel_clean_dir / f"{gkey}_OCR.txt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(header + cleaned, encoding="utf-8")
        written.append((str(dest.relative_to(OUT)), len(items), len(cleaned)))

    print("=" * 60)
    print(f"OCR 完成: 成功 {len(results)} 张 → 聚合为 {len(written)} 个页级文件  失败 {len(failed)}")
    print("脱敏统计:", total_hits)
    print("-" * 60)
    for name, n, ch in written:
        print(f"  {name}  [{n} 张 / {ch} 字符]")
    if failed:
        print("\n失败清单:")
        for rel, err in failed:
            print(f"  {rel}  ->  {err}")


if __name__ == "__main__":
    main()
