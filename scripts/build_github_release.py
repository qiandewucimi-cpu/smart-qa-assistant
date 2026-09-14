# -*- coding: utf-8 -*-
"""生成肉眼可见的 GitHub 发布预览目录。

源文件始终以项目根目录为准；github-release/ 是可删除、可重建的派生快照，
禁止在其中直接维护代码。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
OUTPUT = BASE / "github-release"
MARKER = OUTPUT / "RELEASE_PREVIEW.md"

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
    ".git",
    ".workbuddy",
    "__pycache__",
    "data",
    "github-release",
    "llm_wiki-src",
    "local-only",
    "projects",
    "tools",
    "工作内容",
}

FORBIDDEN_NAMES = {
    ".env",
    "maps_local.py",
    "简历素材.md",
    "面试自述提纲.md",
}

FORBIDDEN_SUFFIXES = {".bak", ".log", ".pyc", ".tmp"}

TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_NAMES = {".gitattributes", ".gitignore", "LICENSE"}


def candidate_files() -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.quotepath=false",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=BASE,
        check=True,
        capture_output=True,
    )
    return [Path(raw.decode("utf-8")) for raw in result.stdout.split(b"\0") if raw]


def is_public(rel: Path) -> bool:
    if not rel.parts or rel.parts[0] not in ALLOWED_ROOTS:
        return False
    if FORBIDDEN_PARTS.intersection(rel.parts):
        return False
    if rel.name in FORBIDDEN_NAMES:
        return False
    if rel.name.startswith("_") or rel.suffix.lower() in FORBIDDEN_SUFFIXES:
        return False
    return True


def reset_output() -> None:
    resolved = OUTPUT.resolve()
    if resolved.parent != BASE.resolve() or resolved.name != "github-release":
        raise RuntimeError(f"拒绝清理非预期目录：{resolved}")
    if not OUTPUT.exists():
        return
    if not MARKER.is_file():
        raise RuntimeError(
            "github-release/ 已存在但缺少 RELEASE_PREVIEW.md；"
            "为保护未知文件，拒绝覆盖。"
        )
    shutil.rmtree(OUTPUT)


def copy_public_file(source: Path, target: Path) -> None:
    """复制公开文件；文本统一为 LF，与仓库 .gitattributes 保持一致。"""
    if source.name in TEXT_NAMES or source.suffix.lower() in TEXT_SUFFIXES:
        data = source.read_bytes().replace(b"\r\n", b"\n")
        target.write_bytes(data)
        shutil.copystat(source, target)
        return
    shutil.copy2(source, target)


def main() -> int:
    check = subprocess.run(
        [sys.executable, str(BASE / "scripts" / "check_public_release.py"), "--include-untracked"],
        cwd=BASE,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    if check.returncode:
        print("发布检查未通过，未生成预览。")
        return check.returncode

    public_files = [rel for rel in candidate_files() if is_public(rel) and (BASE / rel).is_file()]
    reset_output()
    OUTPUT.mkdir(parents=True)

    copied_bytes = 0
    for rel in sorted(public_files, key=lambda item: item.as_posix()):
        source = BASE / rel
        target = OUTPUT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_public_file(source, target)
        copied_bytes += source.stat().st_size

    marker_text = (
        "# GitHub 发布预览\n\n"
        "此目录由 `scripts/build_github_release.py` 自动生成。\n\n"
        "- 不要直接编辑这里的文件；请修改上一级本地完整版后重新生成。\n"
        "- 此目录不包含原始业务资料、本地知识库、密钥、缓存或个人求职材料。\n"
        f"- 本次包含 {len(public_files)} 个源文件，约 {copied_bytes / 1024 / 1024:.2f} MiB。\n"
    )
    MARKER.write_text(marker_text, encoding="utf-8")
    print(f"GitHub 预览已生成：{OUTPUT}")
    print(f"文件：{len(public_files)} 个源文件 + 1 个预览说明，约 {copied_bytes / 1024 / 1024:.2f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
