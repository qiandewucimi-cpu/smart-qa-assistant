# -*- coding: utf-8 -*-
"""模型延迟基准：用同一段真实语料对比智谱各模型的耗时/吞吐，用于选型。

背景：LLM Wiki 编译慢的头号原因是「重推理模型每次吐几千字思维链」。
本脚本量化各模型在同一抽取任务上的耗时，帮助决定入库模型与是否关推理。

用法：
  python bench_models.py                        # 默认跑全部候选
  python bench_models.py glm-4.5-air glm-4.7    # 指定模型
  python bench_models.py --thinking-off         # 额外测「关思考」的效果
  python bench_models.py --chars 3000           # 指定语料截取长度

依赖：仅标准库。key 从同目录 .env 的 ZHIPU_API_KEY 读取（不硬编码）。
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from config import load_zhipu

DEFAULT_SRC = (
    "data/clean/培训文档/"
    "07-DLS系统上线培训手册 - 采购管理 20250826更新_截图OCR.txt"
)
DEFAULT_MODELS = ["glm-4.7", "glm-4.6", "glm-4.5-air", "glm-4-flash", "glm-4.7-flash", "glm-5.3-flash"]

SYS = "你是知识库编译助手。请从给定文本中抽取实体与概念，输出简洁的结构化要点。"
USER_TMPL = "以下是企业培训资料片段，请抽取其中的实体（系统/单据/角色/客户）与关键操作概念，分条列出：\n\n{text}"


def call(api_key, base_url, model, user_msg, thinking_off=False):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user_msg}],
        "max_tokens": 800,
        "temperature": 0.3,
        "stream": False,
    }
    if thinking_off:
        body["thinking"] = {"type": "disabled"}
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read().decode("utf-8"))
        dt = time.time() - t0
        u = d.get("usage", {})
        m = d["choices"][0]["message"]
        return {
            "ok": True,
            "sec": round(dt, 1),
            "pt": u.get("prompt_tokens"),
            "ct": u.get("completion_tokens"),
            "reasoning_chars": len(m.get("reasoning_content") or ""),
            "out_chars": len(m.get("content") or ""),
        }
    except urllib.error.HTTPError as e:
        return {"ok": False, "sec": round(time.time() - t0, 1),
                "err": f"HTTP {e.code}: {e.read().decode('utf-8')[:120]}"}
    except Exception as e:
        return {"ok": False, "sec": round(time.time() - t0, 1), "err": str(e)[:120]}


def main():
    argv = sys.argv[1:]
    chars = 3000
    if "--chars" in argv:
        i = argv.index("--chars")
        chars = int(argv[i + 1])
        del argv[i:i + 2]
    thinking_off = "--thinking-off" in argv
    argv = [a for a in argv if not a.startswith("--")]
    models = argv or DEFAULT_MODELS

    src = Path(__file__).resolve().parent.parent / DEFAULT_SRC
    if not src.exists():
        print(f"语料不存在: {src}")
        return
    text = src.read_text(encoding="utf-8")[:chars]
    user_msg = USER_TMPL.format(text=text)

    api_key, base_url = load_zhipu()
    cases = [(m, False) for m in models]
    if thinking_off:
        cases += [("glm-4.7", True)]

    print(f"语料: {src.name}  截取 {len(text)} 字符\n")
    print(f"{'模型':<22}{'耗时s':>7}{'输入':>7}{'输出':>7}{'思考字':>8}{'正文':>7}  结果")
    print("-" * 74)
    for model, off in cases:
        label = f"{model} 关思考" if off else model
        r = call(api_key, base_url, model, user_msg, off)
        if r["ok"]:
            print(f"{label:<22}{r['sec']:>7}{r['pt']:>7}{r['ct']:>7}{r['reasoning_chars']:>8}{r['out_chars']:>7}  OK")
        else:
            print(f"{label:<22}{r['sec']:>7}{'-':>7}{'-':>7}{'-':>8}{'-':>7}  {r['err']}")


if __name__ == "__main__":
    main()
