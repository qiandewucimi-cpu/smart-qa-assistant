# -*- coding: utf-8 -*-
"""续跑导入：不重做已编译文件，把「未编译」文件重新排入 LLM Wiki 编译队列。

背景（关键陷阱）
--------------
LLM Wiki 的 rescan 走的是「按 file-snapshot 做 diff」——只把 snapshot 里
**没有**（或 hash 变了）的文件排进编译队列。全量重导时 snapshot 被清空，
119 份一次性全排队；跑了 16 份后中断。此时 snapshot 里 119 份**全都在**
（含那 103 份没编译的），于是再 rescan 会判定「无变化」→ 不排队 →
worker 空转，编译彻底卡死。

手术法
------
  未编译集合 U = raw/sources 磁盘文件 − ingest-cache 已编译条目
  U ∩ snapshot 就是「留在 snapshot 里、但实际没编译」的坑。

  ① 从 file-snapshot.json 摘掉 U 的条目（已编译的 16 份 + wiki 页全部保留）
  ② 把 ingest-queue.json 清成 []
  ③ 触发 rescan → 应用把 U 当成「新文件」重新排队，已编译的 16 份不会重做

用法（做文件手术前请**完全退出** LLM Wiki，避免被应用退出时回写覆盖）：
  python resume_ingest.py --plan     # 只打印将要摘除的文件，不写盘
  python resume_ingest.py --apply    # 备份 + 执行手术（不触发 rescan）
  python resume_ingest.py --rescan   # 触发 rescan（应用需已打开且 API 已启用）
  python resume_ingest.py            # 等价于 --apply
"""
import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

from config import BASE, API_BASE, load_token

PROJ = BASE / "projects" / "training-qa" / "training-qa"
LLM = PROJ / ".llm-wiki"
SOURCES = PROJ / "raw" / "sources"
API = f"{API_BASE}/api/v1/projects/current"


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(p: Path, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def disk_sources() -> dict:
    """返回 {basename: 绝对路径}"""
    res = {}
    for root, _dirs, files in os.walk(SOURCES):
        for fn in files:
            if fn.lower().endswith((".md", ".txt")):
                res[fn] = os.path.join(root, fn)
    return res


def compiled_names() -> set:
    """ingest-cache entries 的 key / sourcePath basename 集合 = 已编译文件。"""
    p = LLM / "ingest-cache.json"
    if not p.exists():
        return set()
    entries = load_json(p).get("entries", {})
    names = set()
    if isinstance(entries, dict):
        for k, v in entries.items():
            sp = v.get("sourcePath") if isinstance(v, dict) else None
            names.add(os.path.basename(sp) if sp else os.path.basename(k))
    elif isinstance(entries, list):
        for v in entries:
            sp = v.get("sourcePath") if isinstance(v, dict) else None
            if sp:
                names.add(os.path.basename(sp))
    return names


def compute_plan():
    disk = disk_sources()
    done = compiled_names()
    pending = {n for n in disk if n not in done}
    snap = load_json(LLM / "file-snapshot.json")
    files = snap.get("files", {})
    snap_src = {os.path.basename(k): k for k in files if k.startswith("raw/sources/")}
    # 留在 snapshot 里、待摘除的条目
    to_strip = {snap_src[n]: n for n in pending if n in snap_src}
    return disk, done, pending, snap, files, snap_src, to_strip


def main():
    mode = "--plan"
    if "--apply" in sys.argv or len(sys.argv) == 1:
        mode = "--apply"
    elif "--rescan" in sys.argv:
        mode = "--rescan"

    if mode == "--rescan":
        token = load_token()
        req = urllib.request.Request(
            API + "/sources/rescan", data=b"", method="POST",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                print("rescan 响应:", r.read().decode("utf-8")[:500])
        except Exception as e:  # noqa: BLE001
            print("rescan 调用异常:", e)
        return

    disk, done, pending, snap, files, snap_src, to_strip = compute_plan()

    print(f"磁盘源文件      : {len(disk)}")
    print(f"已编译(缓存)    : {len(done)}")
    print(f"未编译(待续跑)  : {len(pending)}")
    print(f"snapshot raw 条目: {len(snap_src)}")
    print(f"待从 snapshot 摘除: {len(to_strip)}")
    print(f"已编译仍在 snapshot(保留): {len(done & set(snap_src))}")
    print("")

    if mode == "--plan":
        print("=== 待摘除清单（前 15） ===")
        for k in sorted(to_strip)[:15]:
            print("  " + k)
        if len(to_strip) > 15:
            print(f"  ... 共 {len(to_strip)} 条")
        print("\n[plan 模式] 未写盘。")
        return

    # ---- apply ----
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bak_dir = LLM / f"_resume_bak_{stamp}"
    bak_dir.mkdir(exist_ok=True)
    for fn in ("file-snapshot.json", "ingest-queue.json", "ingest-cache.json"):
        src = LLM / fn
        if src.exists():
            shutil.copy2(src, bak_dir / fn)
    print(f"已备份 3 个状态文件 -> {bak_dir.name}")

    # ① 摘除未编译条目
    for k in to_strip:
        files.pop(k, None)
    snap["files"] = files
    snap["updatedAt"] = int(time.time() * 1000)
    save_json(LLM / "file-snapshot.json", snap)
    print(f"已从 file-snapshot 摘除 {len(to_strip)} 条")

    # ② 清空队列
    save_json(LLM / "ingest-queue.json", [])
    print("已清空 ingest-queue.json")

    print("\n手术完成。下一步：")
    print("  1) 打开 LLM Wiki（配置在启动时读取，分模型入库=glm-4.5-air 生效）")
    print("  2) 运行  python resume_ingest.py --rescan   触发重建队列")
    print(f"  预期重排 {len(pending)} 份（已编译的 {len(done)} 份不重做）")


if __name__ == "__main__":
    main()
