# -*- coding: utf-8 -*-
"""把 LLM Wiki 的「入库/编译模型」单独设为指定模型，实现分模型：
编译用轻量快模型（省时），问答仍用高质量模型（保效果）。

原理：应用配置在 %APPDATA%/com.llmwiki.app/app-state.json，
字段：
  customLlmPresets   —— 预设列表 [{id,label}]
  providerConfigs    —— {预设id: {apiKey, baseUrl, model, ...}}
  taskModelRouting   —— {chatPresetId, ingestPresetId}

本脚本会：① 备份 app-state.json ② 建/复用该模型的预设
③ 把 ingestPresetId 指向它

**只动入库模型，不动聊天**：chatPresetId 保持 null（= 跟随你在界面里选的
当前预设），这样你随时可以在设置里换聊天模型，不受本脚本约束。

⚠️ 必须先「完全退出 LLM Wiki」再运行——配置只在启动时读取，
运行中改会被应用退出时覆盖。

用法：
  python set_ingest_model.py glm-4.5-air          # 只把入库换成 air
  python set_ingest_model.py --show               # 只看当前配置，不修改
  python set_ingest_model.py --clear              # 取消入库独立模型（回退跟随当前）
"""
import json
import shutil
import sys
import time
from pathlib import Path

STATE = Path.home() / "AppData" / "Roaming" / "com.llmwiki.app" / "app-state.json"
ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"


def load():
    return json.loads(STATE.read_text(encoding="utf-8"))


def show(d):
    print("=== 当前配置 ===")
    print("activePresetId    :", d.get("activePresetId"))
    print("chatPresetId      :", d.get("taskModelRouting", {}).get("chatPresetId"))
    print("ingestPresetId    :", d.get("taskModelRouting", {}).get("ingestPresetId"))
    for pid, cfg in d.get("providerConfigs", {}).items():
        print(f"  preset {pid} -> model={cfg.get('model')}  key=...{str(cfg.get('apiKey'))[-8:]}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    d = load()
    if "--show" in sys.argv:
        show(d)
        return
    if "--clear" in sys.argv:
        bak = STATE.with_name(f"app-state.json.bak_{time.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(STATE, bak)
        d.setdefault("taskModelRouting", {})["ingestPresetId"] = None
        STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已备份 -> {bak.name}\n已取消入库独立模型（回退为跟随当前预设）")
        show(d)
        return

    model = sys.argv[1]
    active = d.get("activePresetId")
    base_pid = active
    base_cfg = d.get("providerConfigs", {}).get(base_pid, {})

    # 1) 备份
    bak = STATE.with_name(f"app-state.json.bak_{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(STATE, bak)
    print(f"已备份 -> {bak.name}")

    # 2) 建/复用预设
    pid = "custom-ingest-" + model.replace(".", "-")
    presets = d.setdefault("customLlmPresets", [])
    if not any(p.get("id") == pid for p in presets):
        presets.append({"id": pid, "label": f"{model}（入库专用）"})
    d.setdefault("providerConfigs", {})[pid] = {
        "apiKey": base_cfg.get("apiKey"),
        "baseUrl": base_cfg.get("baseUrl", ZHIPU_BASE),
        "customHeaders": {},
        "maxContextSize": base_cfg.get("maxContextSize", 204800),
        "model": model,
    }
    print(f"预设 {pid} -> model={model}（复用 {base_pid} 的 key）")

    # 3) 只设入库；聊天留 null = 跟随你在界面里选的当前预设
    routing = d.setdefault("taskModelRouting", {})
    routing["ingestPresetId"] = pid
    routing.setdefault("chatPresetId", None)
    print(f"taskModelRouting: chat={routing.get('chatPresetId')}（跟随当前预设，不受限）  ingest={pid}")

    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n已写入。请重新打开 LLM Wiki 使其生效。")
    show(d)


if __name__ == "__main__":
    main()
