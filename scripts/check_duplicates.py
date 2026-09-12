# -*- coding: utf-8 -*-
"""重复检测：在语料或 wiki 页面里找「完全相同」与「高度近似」的重复对。

为什么需要：同一份 docx 常散落在多个目录（会议纪要 / 单据重点），分别抽取/OCR
会得到内容不同的多份；同一场培训既有「智能纪要」又有「逐字稿」；编译时同一实体
会在多份来源里重复出现。这些都会污染知识库、拉高检索噪声。

原理：8-gram 倒排索引先筛候选对，再对候选精确算 Jaccard 相似度。
（不用 MinHash 是因为本规模文档数少，精确算更省事也更准。）

用法：
  python check_duplicates.py                     # 扫描 data/clean
  python check_duplicates.py --wiki              # 扫描 LLM Wiki 的 wiki/
  python check_duplicates.py --threshold 0.5     # 调近似阈值（默认 0.6）
  python check_duplicates.py --dir 某目录        # 指定目录

输出：完全相同（md5 一致）分组 + 近似重复（Jaccard ≥ 阈值）对，按相似度降序。
"""
import hashlib
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WIKI = BASE / "projects" / "training-qa" / "training-qa" / "wiki"
K = 8  # shingle 长度
MAX_DOC_FREQ = 25  # 出现在超过这么多文档里的 gram 视为噪声，跳过


def normalize(path: Path) -> str:
    """去掉元数据头、空白与标点，只留正文用于比较。"""
    t = path.read_text(encoding="utf-8", errors="ignore")
    if "# ---- 正文 ----" in t:
        t = t.split("# ---- 正文 ----", 1)[1]
    return "".join(ch for ch in t.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def shingles(text: str, k: int = K) -> set:
    if len(text) < k:
        return {text} if text else set()
    return {text[i:i + k] for i in range(len(text) - k + 1)}


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def collect(directory: Path):
    files = [p for p in sorted(directory.rglob("*")) if p.is_file() and p.suffix.lower() in (".txt", ".md")]
    return files


def main():
    argv = sys.argv[1:]
    threshold = 0.6
    if "--threshold" in argv:
        threshold = float(argv[argv.index("--threshold") + 1])
    root = WIKI if "--wiki" in argv else BASE / "data" / "clean"
    if "--dir" in argv:
        root = Path(argv[argv.index("--dir") + 1])

    if not root.exists():
        print(f"目录不存在: {root}")
        return
    files = collect(root)
    print(f"扫描目录: {root}")
    print(f"文件数: {len(files)}\n")

    # 1) 完全相同（md5 分组）
    by_hash = defaultdict(list)
    for f in files:
        by_hash[md5(f)].append(f)
    exact = {h: v for h, v in by_hash.items() if len(v) > 1}
    print(f"【完全相同】{len(exact)} 组")
    for v in exact.values():
        print(f"  · {v[0].name}")
        for f in v[1:]:
            print(f"    = {f.relative_to(root)}")
    if not exact:
        print("  （无）")

    # 2) 近似重复：8-gram 倒排索引 → 候选对 → 精确 Jaccard
    print(f"\n【近似重复】Jaccard ≥ {threshold}（{K}-gram）")
    sh = {}
    for f in files:
        sh[f] = shingles(normalize(f))

    inverted = defaultdict(list)
    for f, grams in sh.items():
        for g in grams:
            inverted[g].append(f)

    pair_hits = Counter()
    for g, docs in inverted.items():
        if 2 <= len(docs) <= MAX_DOC_FREQ:
            for a, b in combinations(sorted(set(docs)), 2):
                pair_hits[(a, b)] += 1

    results = []
    for (a, b), hits in pair_hits.items():
        sa, sb = sh[a], sh[b]
        if not sa or not sb:
            continue
        jac = hits / (len(sa) + len(sb) - hits) if (len(sa) + len(sb) - hits) else 0
        if jac >= threshold:
            results.append((jac, a, b))

    results.sort(reverse=True)
    print(f"  共 {len(results)} 对")
    for jac, a, b in results:
        print(f"  {jac:.2f}  {a.relative_to(root)}")
        print(f"        {b.relative_to(root)}")


if __name__ == "__main__":
    main()
