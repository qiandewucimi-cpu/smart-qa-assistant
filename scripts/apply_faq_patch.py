# -*- coding: utf-8 -*-
"""把 `wiki-patches/` 里人工沉淀的 wiki 页注入到知识库的 `wiki/queries/` 目录。

为什么需要它
------------
批量编译的语料覆盖度已经是 100%，但**用户说法与资料说法不一致**时检索仍会答偏：
业务同学习惯说「订单审核通过」，而语料里写的是「CP 确认 / 订单理单完成」，
关键词检索桥接不了这层同义关系。

LLM Wiki 的方法论里本来就有 `queries/`（高频查询）这一页类型，所以正解不是改检索器，
而是**把术语对照沉淀成一页知识**——这也让修复可复现：知识库在 gitignored 的 `projects/` 里，
页面原文存在仓库的 `wiki-patches/` 下，任何人 clone 后跑一次本脚本就能得到同样的知识库。
（⚠️ 别把原文放 `data/`——那一整个目录都被 .gitignore 挡着，发不出去。）

用法
----
    python apply_faq_patch.py            # 注入（幂等：内容相同则跳过）
    python apply_faq_patch.py --check    # 只检查是否已注入、内容是否一致（可挂 CI）
    python apply_faq_patch.py --force    # 内容不一致时以 data/faq 为准覆盖

退出码：`--check` 时若有缺失或不一致返回 1，否则 0。
"""
import sys
from pathlib import Path

from config import BASE

FAQ_DIR = BASE / "wiki-patches" / "queries"
WIKI_QUERIES = BASE / "projects" / "training-qa" / "training-qa" / "wiki" / "queries"


def main():
    args = sys.argv[1:]
    check = "--check" in args
    force = "--force" in args

    if not FAQ_DIR.is_dir():
        print(f"✗ 找不到 FAQ 目录：{FAQ_DIR}")
        return 1
    if not WIKI_QUERIES.is_dir():
        print(f"✗ 找不到 wiki/queries 目录（知识库未初始化？）：{WIKI_QUERIES}")
        return 1

    srcs = sorted(FAQ_DIR.glob("*.md"))
    if not srcs:
        print(f"· {FAQ_DIR} 下没有 .md 文件，无事可做")
        return 0

    applied = uptodate = drift = 0
    for src in srcs:
        dst = WIKI_QUERIES / src.name
        want = src.read_text(encoding="utf-8")
        if not dst.exists():
            if check:
                print(f"✗ 缺失：{dst.name}")
                drift += 1
            else:
                dst.write_text(want, encoding="utf-8")
                print(f"＋ 注入：{dst.name}")
                applied += 1
            continue
        have = dst.read_text(encoding="utf-8")
        if have == want:
            print(f"· 已是最新：{dst.name}")
            uptodate += 1
        else:
            if check:
                print(f"✗ 内容不一致：{dst.name}（仓库版 ≠ 知识库版）")
                drift += 1
            elif force:
                dst.write_text(want, encoding="utf-8")
                print(f"↻ 覆盖：{dst.name}")
                applied += 1
            else:
                print(f"! 内容不一致，已跳过：{dst.name}（加 --force 覆盖）")
                drift += 1

    print("-" * 60)
    print(f"共 {len(srcs)} 页：注入/覆盖 {applied}、已是最新 {uptodate}、待处理 {drift}")
    return 1 if (check and drift) else 0


if __name__ == "__main__":
    raise SystemExit(main())
