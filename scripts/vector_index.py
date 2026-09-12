# -*- coding: utf-8 -*-
"""向量索引管理：开启 embedding 配置并把 wiki 页批量灌入 LanceDB 向量索引。

背景：本项目原先 `embeddingConfig.enabled = false`，检索只剩「关键词 + 图谱」两路，
导致语义相近但字面不同的提问偶发零召回（评测中 C3「短溢装」、O4「色码明细」两题）。
本脚本用于开启向量检索并重建索引。

LLM Wiki 的 embedding 配置存在桌面端 `app-state.json`（本脚本只改 embeddingConfig，
其余原样保留）；启用后调用 `POST /api/v1/projects/{id}/pages/embed` 给每页建向量。

用法：
    python vector_index.py status                  # 看当前配置与索引进度
    python vector_index.py enable                  # 写入 embedding 配置（model=embedding-3）
    python vector_index.py embed-one <path>        # 单页试跑（验证 endpoint 是否可用）
    python vector_index.py embed-all               # 批量建索引（可续跑，已完成的跳过）
    python vector_index.py embed-all --force       # 强制重建
    python vector_index.py embed-all --limit 20    # 先跑 20 页验证
    python vector_index.py restore                 # 还原 app-state.json 备份

进度状态存 `data/vector_index_state.json`（gitignored），中断后重跑自动跳过已完成页。
"""
import argparse
import concurrent.futures as cf
import glob
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

from config import API_BASE, BASE, load_token

STATE_FILE = r"C:\Users\31114\AppData\Roaming\com.llmwiki.app\app-state.json"
PROGRESS = BASE / "data" / "vector_index_state.json"
WIKI_DIR = BASE / "projects" / "training-qa" / "training-qa" / "wiki"

# 聚合页由 app 维护、不参与向量索引（实测返回 400）
SKIP_NAMES = {"index.md", "log.md", "overview.md"}

EMBED_MODEL = "embedding-3"          # 智谱 embedding-3，2048 维（实测可用）
EMBED_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/embeddings"


# --------------------------------------------------------------------------
# app-state.json 读写
# --------------------------------------------------------------------------
def read_state():
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def write_state(d):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def latest_backup():
    baks = sorted(glob.glob(STATE_FILE + ".bak_*"))
    return baks[-1] if baks else None


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def resolve_pid():
    tok = load_token()
    req = urllib.request.Request(
        API_BASE + "/api/v1/projects", headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    cur = d.get("currentProject") or {}
    for p in d.get("projects", []):
        if p.get("current") or p.get("id") == cur.get("id"):
            return p["id"]
    return (d.get("projects") or [{}])[0].get("id")


def embed_page(pid, rel_path, force=False, timeout=180):
    tok = load_token()
    body = json.dumps({"path": rel_path, "force": force}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/api/v1/projects/{pid}/pages/embed", data=body, method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return False, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:200]


# --------------------------------------------------------------------------
# 枚举 wiki 页
# --------------------------------------------------------------------------
def list_pages():
    out = []
    for dp, dn, fn in os.walk(WIKI_DIR):
        for f in fn:
            if not f.endswith(".md") or f in SKIP_NAMES:
                continue
            abs_p = os.path.join(dp, f)
            rel = os.path.relpath(abs_p, WIKI_DIR.parent).replace("\\", "/")
            out.append(rel)
    return sorted(out)


def load_progress():
    if PROGRESS.exists():
        try:
            return json.loads(PROGRESS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"done": {}, "failed": {}, "model": EMBED_MODEL}


def save_progress(p):
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def cmd_status(pid):
    d = read_state()
    ec = d.get("embeddingConfig", {})
    print("=== embeddingConfig ===")
    print(json.dumps({k: ("***" if "key" in k.lower() else v) for k, v in ec.items()},
                     ensure_ascii=False, indent=2))
    pages = list_pages()
    prog = load_progress()
    print(f"\n=== 索引进度 ===")
    print(f"wiki 可索引页: {len(pages)}（已排除 {', '.join(sorted(SKIP_NAMES))}）")
    print(f"本地记录 已完成: {len(prog.get('done', {}))}  失败: {len(prog.get('failed', {}))}")


def cmd_enable(pid):
    d = read_state()
    key = d.get("llmConfig", {}).get("apiKey", "")
    if not key:
        print("!! llmConfig.apiKey 为空，无法复用为 embedding key")
        return 1
    d["embeddingConfig"] = {
        "apiKey": key, "batchSize": 16, "concurrency": 2,
        "enabled": True, "endpoint": EMBED_ENDPOINT,
        "extraHeaders": {}, "model": EMBED_MODEL,
    }
    write_state(d)
    print(f"已启用 embedding: model={EMBED_MODEL} endpoint={EMBED_ENDPOINT}")
    print("（app 会热加载该配置，无需重启）")
    return 0


def cmd_embed_one(pid, path, force):
    t0 = time.time()
    ok, res = embed_page(pid, path, force=force)
    dt = round(time.time() - t0, 1)
    print(f"{'OK ' if ok else 'FAIL'} {path}  ({dt}s)")
    print(json.dumps(res, ensure_ascii=False, indent=2)[:600] if isinstance(res, dict) else res)
    return 0 if ok else 1


def cmd_embed_all(pid, force, limit, workers):
    pages = list_pages()
    prog = load_progress()
    done = prog.get("done", {})
    todo = [p for p in pages if force or p not in done]
    if limit:
        todo = todo[:limit]
    print(f"总页 {len(pages)}｜已完成 {len(done)}｜本次待处理 {len(todo)}｜并发 {workers}｜force={force}")
    if not todo:
        print("没有需要处理的页。")
        return 0

    ok_n = fail_n = 0
    failed = dict(prog.get("failed", {}))
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(embed_page, pid, p, force): p for p in todo}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            p = futs[fut]
            try:
                ok, res = fut.result()
            except Exception as e:
                ok, res = False, str(e)[:150]
            if ok:
                ok_n += 1
                done[p] = (res.get("result") or {}).get("status", "ok") if isinstance(res, dict) else "ok"
                failed.pop(p, None)
            else:
                fail_n += 1
                failed[p] = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)[:200]
            if i % 25 == 0 or i == len(todo):
                el = round(time.time() - t0, 1)
                rate = i / el if el else 0
                eta = round((len(todo) - i) / rate / 60, 1) if rate else "?"
                print(f"  [{i}/{len(todo)}] ok={ok_n} fail={fail_n}  {el}s  {rate:.1f}页/s  ETA {eta}min")
                prog["done"] = done
                prog["failed"] = failed
                prog["model"] = EMBED_MODEL
                prog["updated"] = datetime.now().isoformat(timespec="seconds")
                save_progress(prog)
    prog.update({"done": done, "failed": failed, "model": EMBED_MODEL,
                 "updated": datetime.now().isoformat(timespec="seconds")})
    save_progress(prog)
    print(f"\n完成：成功 {ok_n} / 失败 {fail_n}；累计已完成 {len(done)} / {len(pages)}")
    if failed:
        print("失败样例：")
        for k, v in list(failed.items())[:5]:
            print(f"  {k}: {str(v)[:150]}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "enable", "embed-one", "embed-all", "restore"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()

    if a.cmd == "restore":
        bak = latest_backup()
        if not bak:
            print("无备份")
            return 1
        shutil.copy2(bak, STATE_FILE)
        print("已还原:", os.path.basename(bak))
        return 0

    if a.cmd == "enable":
        return cmd_enable(None)

    pid = resolve_pid()
    if a.cmd == "status":
        cmd_status(pid)
        return 0
    if a.cmd == "embed-one":
        if not a.path:
            print("用法：embed-one <wiki/xxx.md>")
            return 1
        return cmd_embed_one(pid, a.path, a.force)
    if a.cmd == "embed-all":
        return cmd_embed_all(pid, a.force, a.limit, a.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
