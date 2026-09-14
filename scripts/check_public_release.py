# -*- coding: utf-8 -*-
"""GitHub 发布前只读检查。

检查 Git 已跟踪内容，而不是整个本地工作区；因此不会读取工作资料、知识库、
本地密钥或缓存。退出码为 1 表示存在不应发布或不利于复现的问题。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
MAX_PUBLIC_FILE_BYTES = 5 * 1024 * 1024

ALLOWED_ROOTS = {
    ".gitattributes",
    ".github",
    ".gitignore",
    "LICENSE",
    "README.md",
    "docs",
    "eval",
    "scripts",
    "tests",
    "wiki-patches",
}

FORBIDDEN_PARTS = {
    ".workbuddy",
    "data",
    "llm_wiki-src",
    "local-only",
    "projects",
    "tools",
    "工作内容",
}

FORBIDDEN_NAMES = {
    ".env",
    "maps_local.py",
}

FORBIDDEN_PUBLIC_PATHS = {
    "docs/简历素材.md",
    "docs/面试自述提纲.md",
}

REQUIRED_FILES = {
    "LICENSE",
    "README.md",
    "docs/GITHUB发布清单.md",
    "scripts/.env.example",
    "scripts/check_public_release.py",
    "scripts/config.py",
    "scripts/requirements.txt",
    "scripts/project_acceptance.py",
    "tests/test_core.py",
}

# 拆开本机路径模式，避免检查器把自己的规则误判为泄漏。
LOCAL_USER_PATH = re.compile(r"[A-Za-z]:" + re.escape("\\") + "Users" + re.escape("\\"), re.I)
PRIVATE_KEY_MARKER = "-----BEGIN " + "PRIVATE KEY-----"
SECRET_ASSIGNMENT = re.compile(
    r"(?im)^\s*(LLM_WIKI_API_TOKEN|ZHIPU_API_KEY|FEISHU_APP_SECRET)\s*=\s*(\S+)\s*$"
)
PLACEHOLDER_PREFIXES = ("你的", "xxxx", "<", "${", "$env:")


def git_files(include_untracked: bool = False) -> list[str]:
    args = ["git", "-c", "core.quotepath=false", "ls-files"]
    if include_untracked:
        args.extend(["--cached", "--others", "--exclude-standard"])
    result = subprocess.run(
        args,
        cwd=BASE,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def read_public_text(path: Path) -> str | None:
    if path.stat().st_size > MAX_PUBLIC_FILE_BYTES:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def check_readme_links(errors: list[str]) -> None:
    readme = BASE / "README.md"
    if not readme.is_file():
        return
    text = readme.read_text(encoding="utf-8")
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        target = target.strip().strip("<>").split("#", 1)[0]
        if not target or re.match(r"^[a-z]+://", target, re.I):
            continue
        if not (BASE / target).exists():
            errors.append(f"README 本地链接失效：{target}")


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    include_untracked = "--include-untracked" in sys.argv[1:]
    tracked = git_files(include_untracked=include_untracked)
    tracked_set = set(tracked)

    for required in sorted(REQUIRED_FILES - tracked_set):
        errors.append(f"缺少发布必需文件：{required}")

    for rel in tracked:
        path = Path(rel)
        full_path = BASE / path
        root = path.parts[0]

        if root not in ALLOWED_ROOTS:
            errors.append(f"不在发布白名单：{rel}")
        if FORBIDDEN_PARTS.intersection(path.parts):
            errors.append(f"本地专用路径被 Git 跟踪：{rel}")
        if path.name in FORBIDDEN_NAMES:
            errors.append(f"敏感配置被 Git 跟踪：{rel}")
        if path.name.startswith("_") or path.suffix.lower() in {".log", ".bak", ".tmp"}:
            errors.append(f"临时或日志文件被 Git 跟踪：{rel}")
        if not full_path.is_file():
            warnings.append(f"索引中存在但工作区缺失：{rel}")
            continue
        if rel in FORBIDDEN_PUBLIC_PATHS:
            errors.append(f"个人材料仍在 GitHub 发布路径：{rel}")
        if full_path.stat().st_size > MAX_PUBLIC_FILE_BYTES:
            errors.append(f"文件超过 5 MiB：{rel}")

        text = read_public_text(full_path)
        if text is None:
            continue
        if LOCAL_USER_PATH.search(text):
            errors.append(f"包含本机用户绝对路径：{rel}")
        if PRIVATE_KEY_MARKER in text:
            errors.append(f"疑似包含私钥：{rel}")
        for match in SECRET_ASSIGNMENT.finditer(text):
            value = match.group(2).strip().strip('"\'')
            if value and not value.lower().startswith(tuple(p.lower() for p in PLACEHOLDER_PREFIXES)):
                errors.append(f"疑似包含真实密钥：{rel}（{match.group(1)}）")

    check_readme_links(errors)

    scope = "Git 跟踪及未忽略文件" if include_untracked else "Git 跟踪文件"
    print(f"已检查 {len(tracked)} 个 {scope}。")
    for item in warnings:
        print(f"警告：{item}")
    if errors:
        print(f"发布检查失败，共 {len(errors)} 项：")
        for item in errors:
            print(f"  - {item}")
        return 1

    print("发布边界检查通过：未发现本地专用路径、明显密钥、本机用户路径或超大文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
