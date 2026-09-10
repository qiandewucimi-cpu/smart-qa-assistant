# -*- coding: utf-8 -*-
"""编译监控：检查 LLM Wiki 编译进度，编译完成后自动触发评测。

编译进度读自 .llm-wiki/ingest-cache.json（entries 数 = 已编译文件数）与
ingest-queue.json（processing/pending 任务数）。全部 62 文件编译完成后，
自动运行 eval_chat.py 评测 15 题。

用法：
  python monitor_compile.py             # 检查进度，完成则自动评测
  python monitor_compile.py --eval-only # 跳过进度检查，直接评测
"""
import json
import subprocess
import sys
from pathlib import Path

from config import BASE

PROJ = BASE / "projects" / "training-qa" / "training-qa"
LLM = PROJ / ".llm-wiki"
EXPECTED = 62  # batch1~5 去重后应编译的文件数


def ingest_status():
    """返回 (已编译条目数, processing 数, pending 数)"""
    cache = LLM / "ingest-cache.json"
    queue = LLM / "ingest-queue.json"
    entries = 0
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        e = d.get("entries", d)
        if isinstance(e, (dict, list)):
            entries = len(e)
    processing = pending = 0
    if queue.exists():
        q = json.loads(queue.read_text(encoding="utf-8"))
        if isinstance(q, list):
            for t in q:
                st = t.get("status")
                if st == "processing":
                    processing += 1
                else:
                    pending += 1
    return entries, processing, pending


def run_eval():
    print("编译完成，开始评测 15 题 ...")
    r = subprocess.run(
        [sys.executable, "eval_chat.py"],
        capture_output=True, text=True, cwd=Path(__file__).parent,
    )
    if r.stdout:
        print(r.stdout)
    if r.stderr:
        print("[stderr]", r.stderr)
    return r.returncode


def main():
    if "--eval-only" in sys.argv:
        run_eval()
        return
    entries, processing, pending = ingest_status()
    print(f"编译进度: {entries}/{EXPECTED} 已编译 | processing={processing} pending={pending}")
    if entries >= EXPECTED and processing == 0 and pending == 0:
        run_eval()
    else:
        print("编译未完成，本次仅报告进度。")


if __name__ == "__main__":
    main()
