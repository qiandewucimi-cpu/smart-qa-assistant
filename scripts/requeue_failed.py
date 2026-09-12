# -*- coding: utf-8 -*-
"""把 LLM Wiki 编译队列里 status=failed 的源文件重新排入队列。

为什么需要它
------------
ingest-queue.json 里的条目重试耗尽后会变成 status="failed"，应用**不会**再自动重试，
文件也不会写进 ingest-cache。结果是这份知识只落了一部分 wiki 页（source 页 +
少量派生页），却在台账上永远算「未编译」——会让 monitor_compile.py 的
`entries < EXPECTED` 判定永远差一份，自动评测永久不触发。

原理（沿用本项目验证过的「file-snapshot 手术」手法）
--------------------------------------------------
rescan 是「拿磁盘文件与 file-snapshot.json 做 diff」。把失败文件从 snapshot 里摘掉，
rescan 就会把它判成「新增」→ 重新入队编译。只动这一个文件，不影响已完成/在排队的其它文件。

用法
----
  python requeue_failed.py --plan     # 只读：列出 failed 条目与对应磁盘文件
  python requeue_failed.py --apply    # 备份 → 摘 snapshot 条目 → 删 failed 条目 → rescan

注意：--apply 会调用应用的重扫接口，建议在应用运行中执行（应用未运行时重开应用也会自动重扫）。
"""
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

from config import BASE, API_BASE, load_token

PROJ = BASE / "projects" / "training-qa" / "training-qa"
LLM = PROJ / ".llm-wiki"
SOURCES = PROJ / "raw" / "sources"
QUEUE = LLM / "ingest-queue.json"
SNAP = LLM / "file-snapshot.json"


def load(p: Path, default):
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def failed_entries():
    q = load(QUEUE, [])
    if not isinstance(q, list):
        return []
    return [t for t in q if t.get("status") == "failed"]


def rescan():
    token = load_token()
    req = urllib.request.Request(
        API_BASE + "/api/v1/projects/current/sources/rescan", data=b"{}", method="POST"
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="ignore")


def plan():
    fe = failed_entries()
    print(f"队列文件: {QUEUE}")
    print(f"failed 条目数: {len(fe)}")
    for t in fe:
        sp = t.get("sourcePath", "")
        on_disk = (PROJ / sp).exists()
        print(f"  - status={t.get('status')} retry={t.get('retryCount')} 磁盘存在={on_disk}")
        print(f"    {sp}")
        err = (t.get("error") or "").replace("\n", " ")
        if err:
            print(f"    err: {err[:160]}")
    snap = load(SNAP, {})
    files = snap.get("files", {})
    for t in fe:
        sp = t.get("sourcePath", "")
        print(f"  snapshot 是否登记 {sp} -> {sp in files}")
    if not fe:
        print("没有 failed 条目，无需处理。")


def apply():
    fe = failed_entries()
    if not fe:
        print("没有 failed 条目，无需处理。")
        return
    bak = LLM / f"_requeue_bak_{time.strftime('%Y%m%d_%H%M%S')}"
    bak.mkdir(parents=True, exist_ok=True)
    for f in (QUEUE, SNAP):
        if f.exists():
            shutil.copy2(f, bak / f.name)
    print(f"已备份 -> {bak}")

    paths = [t.get("sourcePath") for t in fe]

    q = load(QUEUE, [])
    kept = [t for t in q if t.get("status") != "failed"]
    QUEUE.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"队列: {len(q)} -> {len(kept)}（删除 failed {len(q) - len(kept)} 条）")

    snap = load(SNAP, {})
    files = snap.get("files", {})
    removed = 0
    for p in paths:
        if p in files:
            files.pop(p)
            removed += 1
    SNAP.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f" snapshot: 摘除 {removed} 条（rescan 将把它们判为新增）")

    try:
        resp = rescan()
        print(f"rescan 响应: {resp[:600]}")
    except Exception as e:  # noqa: BLE001
        print(f"rescan 失败（应用未运行？重开应用会自动重扫）: {type(e).__name__}: {e}")


def main():
    if "--apply" in sys.argv:
        apply()
    else:
        plan()
        if "--plan" not in sys.argv:
            print("\n（只读模式。要执行请加 --apply）")


if __name__ == "__main__":
    main()
