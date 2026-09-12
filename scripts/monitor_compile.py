# -*- coding: utf-8 -*-
"""编译监控：检查 LLM Wiki 编译进度，编译完成后自动触发评测。

编译进度读自 .llm-wiki/ingest-cache.json（entries 数 = 已编译文件数）与
ingest-queue.json（processing/pending 任务数）。全部文件编译完成后，
自动运行 eval_chat.py 评测 **22 题**（v0.2 评测集，六类），结果写入 eval/。

EXPECTED = reingest.py 去重后实际写入 raw/sources 的文件数（121 批次份
→ 去重跳过 2 份 → 119 份；同名不同内容的 2 组合并改名后仍是 119 个文件）。

幂等：同一轮编译只评测一次（完成标记 .llm-wiki/.eval_done.json），
避免定时自动化每小时重复跑评测、反复覆盖评测报告。

用法：
  python monitor_compile.py             # 检查进度，完成则自动评测（幂等）
  python monitor_compile.py --eval-only # 跳过进度检查，直接评测
  python monitor_compile.py --force     # 忽略完成标记，强制重跑评测
"""
import json
import subprocess
import sys
from pathlib import Path

from config import BASE

PROJ = BASE / "projects" / "training-qa" / "training-qa"
LLM = PROJ / ".llm-wiki"
EXPECTED = 119  # reingest 去重后应编译的文件数（见模块 docstring）
# 评测完成标记：记录「已针对 >=EXPECTED 的编译跑过评测」，实现幂等。
# 没有它，定时自动化会每小时重跑一次评测并反复覆盖 eval/评测报告.md（本项目踩过的坑）。


def _marker_path():
    return LLM / ".eval_done.json"


def _already_evaluated(entries):
    p = _marker_path()
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if int(d.get("entries", 0)) >= EXPECTED and entries >= EXPECTED:
            return d.get("at")
    except (ValueError, TypeError):
        pass
    return None


def _write_marker(entries):
    import datetime
    _marker_path().write_text(
        json.dumps({"entries": entries, "at": datetime.datetime.now().isoformat(timespec="seconds")},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def ingest_status():
    """返回 (已编译条目数, processing 数, pending 数, failed 列表)

    failed 必须与 pending 分开计数：重试耗尽的条目会永远停在 failed，
    应用不再重试、也不会进 ingest-cache。若把它算进 pending，或只比 entries，
    就会因「永远差一份」导致自动评测永不触发。
    """
    cache = LLM / "ingest-cache.json"
    queue = LLM / "ingest-queue.json"
    entries = 0
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        e = d.get("entries", d)
        if isinstance(e, (dict, list)):
            entries = len(e)
    processing = pending = 0
    failed = []
    if queue.exists():
        q = json.loads(queue.read_text(encoding="utf-8"))
        if isinstance(q, list):
            for t in q:
                st = t.get("status")
                if st == "processing":
                    processing += 1
                elif st == "failed":
                    failed.append(t.get("sourcePath", "(未知)"))
                else:
                    pending += 1
    return entries, processing, pending, failed


def run_eval():
    print("编译完成，开始评测 22 题 ...")
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
    entries, processing, pending, failed = ingest_status()
    print(f"编译进度: {entries}/{EXPECTED} 已编译 | processing={processing} "
          f"pending={pending} failed={len(failed)}")
    if failed:
        print("⚠ 以下文件重试耗尽、未进台账（知识可能只落了一部分页）：")
        for p in failed:
            print(f"    - {p}")
        print("  处理方式: python requeue_failed.py --apply （重排后再编译）")
    # 注意：ingest-queue 的 processing/pending 标记会滞后（实测 worker 已推进到别的文件，
    # 队列仍标着旧文件 processing）。故以 ingest-cache 的 entries 数为准判完成，
    # 队列计数仅作参考，不参与判定，否则会因残留 processing 永久卡住评测。
    # failed 计入「已终结」：否则一个永久失败的文件会让 entries 永远 < EXPECTED，
    # 自动评测永久不触发（2026-09-11 实测踩到）。
    done = entries + len(failed)
    if done < EXPECTED:
        print(f"编译未完成（还差 {EXPECTED - done} 份），本次仅报告进度。")
        return
    # 已完成：幂等保护——同一轮编译只评测一次
    at = _already_evaluated(done)
    if at and "--force" not in sys.argv:
        print(f"本轮编译已评测过（{at}），跳过。如需强制重跑加 --force。")
        return
    if failed:
        print(f"注：{len(failed)} 份以 failed 收尾，评测结果会反映这部分覆盖缺口。")
    rc = run_eval()
    if rc == 0 and "--force" not in sys.argv:
        _write_marker(done)
        print(f"已记录评测完成标记 -> {_marker_path().name}")


if __name__ == "__main__":
    main()
