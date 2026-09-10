# -*- coding: utf-8 -*-
"""共享配置：项目根目录定位 + LLM Wiki 本地 API 配置。

Token 只从环境变量 LLM_WIKI_API_TOKEN 或 scripts/.env 读取（.env 已被 .gitignore
忽略，不会入库），绝不在代码里硬编码密钥。
"""
import os
from pathlib import Path

# 项目根目录（scripts/ 的上一级），用相对定位避免硬编码绝对路径
BASE = Path(__file__).resolve().parent.parent

# LLM Wiki 本地 HTTP API（默认关闭，需在 LLM Wiki 设置里手动开启）
API_BASE = "http://127.0.0.1:19828"


def load_token() -> str:
    """读取 LLM Wiki API Token：优先环境变量，其次 scripts/.env 文件。"""
    tok = os.environ.get("LLM_WIKI_API_TOKEN", "").strip()
    if tok:
        return tok
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("LLM_WIKI_API_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""
