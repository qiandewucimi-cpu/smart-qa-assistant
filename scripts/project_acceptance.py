# -*- coding: utf-8 -*-
"""项目一键验收：默认离线检查公开仓库，--live 追加本地 LLM Wiki 检查。"""
from __future__ import annotations

import argparse
import compileall
import json
import sys
import urllib.request
from pathlib import Path

from config import API_BASE, BASE, load_token


class Acceptance:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        mark = "PASS" if condition else "FAIL"
        suffix = f" — {detail}" if detail else ""
        print(f"[{mark}] {label}{suffix}")
        if not condition:
            self.failures.append(label)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def check_artifacts(result: Acceptance) -> None:
    primary_path = BASE / "eval" / "评测结果_raw.json"
    refusal_path = BASE / "eval" / "拒答评测结果_raw.json"
    primary = load_json(primary_path)
    refusal = load_json(refusal_path)

    scored = [item for item in primary.values() if item.get("scoring", True)]
    answered = [item for item in primary.values() if (item.get("answer") or "").strip()]
    traceable = [item for item in primary.values() if item.get("references")]
    groups = {name: sum(1 for item in refusal.values() if item.get("group") == name) for name in ("X", "N")}

    result.check("主评测产物可读取", isinstance(primary, dict), f"{len(primary)} 题")
    result.check(
        "评测规模符合口径",
        len(primary) == 22 and len(scored) == 21,
        "共 22 题（计分 21 题，1 题因业务口径冲突单列）",
    )
    result.check("主评测回答完整", len(answered) == 22, f"{len(answered)}/22")
    result.check("引用链完整", len(traceable) == 22, f"{len(traceable)}/22")
    result.check("拒答专项规模符合口径", groups == {"X": 8, "N": 5}, f"语料外 {groups['X']} / 语料内 {groups['N']}")


def check_public_files(result: Acceptance) -> None:
    required = (
        "README.md",
        "LICENSE",
        ".github/workflows/public-release-check.yml",
        "docs/架构与演示.md",
        "eval/README.md",
        "scripts/.env.example",
        "scripts/check_public_release.py",
        "scripts/setup_windows.ps1",
        "tests/test_core.py",
    )
    missing = [item for item in required if not (BASE / item).is_file()]
    result.check("公开交付文件完整", not missing, "无缺失" if not missing else ", ".join(missing))
    result.check("Python 源码可编译", compileall.compile_dir(BASE / "scripts", quiet=1))


def api_json(path: str):
    token = load_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(API_BASE + path, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def check_live(result: Acceptance) -> None:
    try:
        health = api_json("/api/v1/health")
        projects = api_json("/api/v1/projects")
    except Exception as exc:  # noqa: BLE001
        result.check("LLM Wiki API 在线", False, f"{type(exc).__name__}: {exc}")
        return

    result.check("LLM Wiki API 在线", health.get("status") == "running")
    current = next((item for item in projects.get("projects", []) if item.get("current")), None)
    result.check("当前知识库已打开", bool(current), current.get("name", "") if current else "未选择项目")

    project = BASE / "projects" / "training-qa" / "training-qa"
    sources = list((project / "raw" / "sources").rglob("*.md")) + list((project / "raw" / "sources").rglob("*.txt"))
    pages = list((project / "wiki").rglob("*.md"))
    result.check("语料编译覆盖", len(sources) == 119, f"{len(sources)}/119")
    result.check("Wiki 页面规模", len(pages) >= 822, f"{len(pages)} 页")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="追加本地 LLM Wiki API 与知识库检查")
    args = parser.parse_args()

    print("=== Smart QA Assistant 项目验收 ===")
    result = Acceptance()
    check_public_files(result)
    check_artifacts(result)
    if args.live:
        check_live(result)

    print("=" * 41)
    if result.failures:
        print(f"验收失败：{len(result.failures)} 项")
        return 1
    print("验收通过：公开交付与评测证据完整" + ("，本地服务正常" if args.live else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
