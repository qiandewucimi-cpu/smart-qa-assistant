# -*- coding: utf-8 -*-
"""把安全预览同步到同级 GitHub 发布仓库，并保留其 .git 元数据。"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from build_github_release import ALLOWED_ROOTS, BASE, MARKER, OUTPUT


DESTINATION = BASE.parent / "smart-qa-assistant"
EXPECTED_REMOTE_SUFFIX = "smart-qa-assistant.git"


def verify_destination() -> None:
    if BASE.resolve().name != "smart-qa-local":
        raise RuntimeError(
            "同步脚本只能从 smart-qa-local 本地完整版运行；"
            "禁止从 GitHub 发布仓库反向同步。"
        )
    destination = DESTINATION.resolve()
    if destination == BASE.resolve():
        raise RuntimeError("拒绝把发布仓库同步到自身")
    if destination.parent != BASE.resolve().parent or destination.name != "smart-qa-assistant":
        raise RuntimeError(f"拒绝同步到非预期目录：{destination}")
    if not (destination / ".git").is_dir():
        raise RuntimeError(f"目标不是独立 Git 仓库：{destination}")

    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={destination.as_posix()}",
            "remote",
            "get-url",
            "origin",
        ],
        cwd=destination,
        check=True,
        capture_output=True,
    )
    remote_url = result.stdout.decode("utf-8", errors="replace").strip().rstrip("/")
    if not remote_url.endswith(EXPECTED_REMOTE_SUFFIX):
        raise RuntimeError("目标仓库的 origin 不是预期的 smart-qa-assistant.git")


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def main() -> int:
    verify_destination()

    build = subprocess.run(
        [sys.executable, str(BASE / "scripts" / "build_github_release.py")],
        cwd=BASE,
    )
    if build.returncode:
        return build.returncode

    for name in sorted(ALLOWED_ROOTS):
        source = OUTPUT / name
        target = DESTINATION / name
        remove_path(target)
        if source.is_dir():
            shutil.copytree(source, target)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    preview_marker = DESTINATION / MARKER.name
    if preview_marker.exists():
        preview_marker.unlink()

    print(f"GitHub 发布仓库已同步：{DESTINATION}")
    print("下一步：进入该目录检查 git status 与 git diff；本脚本不会提交或推送。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
