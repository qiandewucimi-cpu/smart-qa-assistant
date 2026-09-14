# -*- coding: utf-8 -*-
"""LLM Wiki 状态自检：API 健康 + 编译进度 + 入库模型确认。

用法：
  python wiki_status.py                      # 打印 health / 进度 / 入库模型
  python wiki_status.py --watch              # 每 30s 采样一次，直到 Ctrl+C
  python wiki_status.py --samples 10 --interval 60   # 有界采样：10 次 × 60s（适合后台监控）

只读，不修改任何文件。
"""
import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

from config import BASE, API_BASE, load_token

PROJ = BASE / "projects" / "training-qa" / "training-qa"
LLM = PROJ / ".llm-wiki"
SOURCES = PROJ / "raw" / "sources"
WIKI = PROJ / "wiki"
STATE = Path.home() / "AppData" / "Roaming" / "com.llmwiki.app" / "app-state.json"


def http_get(path: str, token: str = ""):
    req = urllib.request.Request(API_BASE + path)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8")


def health():
    try:
        return json.loads(http_get("/api/v1/health"))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def progress():
    entries = 0
    cache = LLM / "ingest-cache.json"
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        e = d.get("entries", d)
        entries = len(e) if isinstance(e, (dict, list)) else 0
    processing = pending = 0
    q = LLM / "ingest-queue.json"
    if q.exists():
        arr = json.loads(q.read_text(encoding="utf-8"))
        if isinstance(arr, list):
            for t in arr:
                if t.get("status") == "processing":
                    processing += 1
                else:
                    pending += 1
    total = sum(1 for _ in SOURCES.rglob("*") if _.is_file()
                and _.suffix.lower() in (".md", ".txt")) if SOURCES.exists() else 0
    return entries, processing, pending, total


def wiki_pages():
    """按 Wiki 一级目录统计 Markdown 页数。"""
    counts = Counter()
    if WIKI.exists():
        for path in WIKI.rglob("*.md"):
            rel = path.relative_to(WIKI)
            counts[rel.parts[0] if len(rel.parts) > 1 else "(root)"] += 1
    return counts


def ingest_model():
    if not STATE.exists():
        return "(app-state.json 不存在)"
    d = json.loads(STATE.read_text(encoding="utf-8"))
    pid = d.get("taskModelRouting", {}).get("ingestPresetId")
    chat = d.get("taskModelRouting", {}).get("chatPresetId")
    if not pid:
        return f"跟随当前预设（未单独配）"
    cfg = d.get("providerConfigs", {}).get(pid, {})
    return f"{cfg.get('model')}（preset={pid}）  聊天={chat or '跟随当前'}"


def sample():
    h = health()
    entries, processing, pending, total = progress()
    pages = wiki_pages()
    print(f"[{time.strftime('%H:%M:%S')}] health={h.get('status', h.get('error'))} "
          f"auth={h.get('authConfigured')} | 进度 {entries}/{total} "
          f"| wiki={sum(pages.values())} 页 "
          f"| processing={processing} pending={pending} | 入库模型={ingest_model()}")
    if pages:
        print("  Wiki 分类：" + " | ".join(f"{name}={count}" for name, count in sorted(pages.items())))
    return entries, total


def _arg_num(flag, default):
    if flag in sys.argv:
        try:
            return int(sys.argv[sys.argv.index(flag) + 1])
        except (IndexError, ValueError):
            pass
    return default


def main():
    if "--watch" in sys.argv:
        try:
            while True:
                sample()
                time.sleep(30)
        except KeyboardInterrupt:
            print("已停止采样。")
        return
    if "--samples" in sys.argv:
        n = _arg_num("--samples", 10)
        iv = _arg_num("--interval", 60)
        start_entries = None
        t0 = time.time()
        for i in range(n):
            entries, total = sample()
            if start_entries is None:
                start_entries = entries
            if i < n - 1:
                time.sleep(iv)
        entries, _p, _pd, total = progress()
        dt = (time.time() - t0) / 60.0
        gained = entries - start_entries
        print("")
        if gained > 0 and dt > 0:
            print(f"=== 汇总：{dt:.0f} 分钟内 +{gained} 份，约 {dt / gained:.1f} 分/份"
                  f"（按此速率，剩余 {total - entries} 份还需 ≈{(total - entries) * dt / gained / 60:.1f} 小时）")
        else:
            print(f"=== 汇总：{dt:.0f} 分钟内无新增，需排查 worker 是否卡住")
        print(f"当前进度 {entries}/{total}")
        return
    sample()


if __name__ == "__main__":
    main()
