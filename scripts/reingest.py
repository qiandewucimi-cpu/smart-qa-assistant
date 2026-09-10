# -*- coding: utf-8 -*-
"""重导 LLM Wiki 项目：清空旧编译，用干净数据重新导入 batch1~5。

步骤：
1. 备份旧 wiki + raw/sources（可回滚）
2. 清空 raw/sources 与 wiki
3. 重置 .llm-wiki 的 file-snapshot / ingest-cache（让应用视为全新导入）
4. 复制 batch1~5 到 raw/sources（扁平，处理同名冲突）
5. 触发 sources/rescan

同名冲突处理：
- batch2（逐字稿）与 batch1（纪要）同名但内容不同 -> batch2 文件名加「逐字稿-」前缀
- batch3 有 2 个智能纪要与 batch1 正文完全相同 -> 跳过（去重）
- batch5 日报（纯日期文件名）加「日报-」前缀，聊天记录/AI建议原样

用法：python reingest.py  （可选 --no-rescan 只做文件操作不触发重扫）
"""
import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path

from config import BASE, API_BASE, load_token
PROJ = BASE / "projects" / "training-qa" / "training-qa"
WIKI = PROJ / "wiki"
SOURCES = PROJ / "raw" / "sources"
LLM = PROJ / ".llm-wiki"
BATCHES = BASE / "data" / "import_batches"
TOKEN = load_token()
API = f"{API_BASE}/api/v1/projects/current"

# batch1 已导入的正文 hash（用于 batch3 去重）
def body_hash(path: Path) -> str:
    t = path.read_text(encoding="utf-8")
    if "# ---- 正文 ----" in t:
        t = t.split("# ---- 正文 ----", 1)[1]
    return hashlib.md5(t.strip().encode("utf-8")).hexdigest()


def backup():
    dst = BASE / "data" / "backup_wiki_地名泄漏版_20260911"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(WIKI, dst / "wiki")
    shutil.copytree(SOURCES, dst / "raw_sources")
    print(f"已备份旧编译到 {dst.relative_to(BASE)}")


def reset_state():
    for fname, key in (("file-snapshot.json", "files"), ("ingest-cache.json", "entries")):
        p = LLM / fname
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            d[key] = {}
            p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已重置 file-snapshot / ingest-cache")


def collect_files():
    """返回要复制到 raw/sources 的文件列表 [(源路径, 目标文件名)]"""
    files = []

    # batch1：原样
    for f in (BATCHES / "batch1_核心_会议纪要智能纪要").rglob("*"):
        if f.is_file():
            files.append((f, f.name))

    # batch1 正文 hash 集合（用于 batch3 去重）
    batch1_hashes = {body_hash(f) for f, _ in files}

    # batch2：加「逐字稿-」前缀
    for f in (BATCHES / "batch2_会议纪要逐字稿").rglob("*"):
        if f.is_file():
            files.append((f, "逐字稿-" + f.name))

    # batch3：跳过与 batch1 正文重复的
    for f in (BATCHES / "batch3_单据重点与订单理单").rglob("*"):
        if f.is_file():
            if body_hash(f) in batch1_hashes:
                print(f"  去重跳过(batch3 与 batch1 正文相同): {f.name}")
                continue
            files.append((f, f.name))

    # batch4：原样
    for f in (BATCHES / "batch4_DLS系统操作手册").rglob("*"):
        if f.is_file():
            files.append((f, f.name))

    # batch5：辅助材料（日报加「日报-」前缀避免纯日期标题，聊天记录/AI建议原样）
    b5 = BATCHES / "batch5_辅助_日报聊天记录AI建议"
    for f in b5.rglob("*"):
        if not f.is_file():
            continue
        if f.parent.name == "每日日报":
            files.append((f, "日报-" + f.name))
        else:
            files.append((f, f.name))

    return files


def copy_files(files):
    for src, dst_name in files:
        shutil.copy2(src, SOURCES / dst_name)
    print(f"已复制 {len(files)} 个文件到 raw/sources")


def rescan():
    req = urllib.request.Request(
        API + "/sources/rescan", data=b"", method="POST",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("rescan 响应:", r.read().decode("utf-8")[:300])
    except Exception as e:
        print("rescan 调用异常（可能应用会自动 source-watch）:", e)


def main():
    no_rescan = "--no-rescan" in sys.argv
    backup()
    # 清空
    if WIKI.exists():
        shutil.rmtree(WIKI)
    WIKI.mkdir(parents=True)
    if SOURCES.exists():
        shutil.rmtree(SOURCES)
    SOURCES.mkdir(parents=True)
    reset_state()
    files = collect_files()
    copy_files(files)
    if not no_rescan:
        rescan()


if __name__ == "__main__":
    main()
