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


def _env(key: str) -> str:
    """读取单个环境变量：优先进程环境，其次 scripts/.env 文件（key=value 形式）。"""
    val = os.environ.get(key, "").strip()
    if val:
        return val
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def load_token() -> str:
    """读取 LLM Wiki API Token：优先环境变量，其次 scripts/.env 文件。"""
    return _env("LLM_WIKI_API_TOKEN")


def load_feishu() -> tuple:
    """读取飞书应用凭据 (App ID, App Secret)，返回 (app_id, app_secret)。"""
    return _env("FEISHU_APP_ID"), _env("FEISHU_APP_SECRET")


def load_zhipu() -> tuple:
    """读取智谱 (API Key, Base URL)，用于 GLM-4V 截图 OCR。"""
    base = _env("ZHIPU_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4"
    return _env("ZHIPU_API_KEY"), base


def load_app_state_path() -> Path:
    """定位 LLM-Wiki 的 app-state.json，允许环境变量覆盖默认位置。"""
    configured = _env("LLM_WIKI_STATE_FILE")
    if configured:
        return Path(configured).expanduser()

    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "com.llmwiki.app" / "app-state.json"

    # 非 Windows 环境下保留一个明确、可诊断的回退路径。
    return Path.home() / ".config" / "com.llmwiki.app" / "app-state.json"


def _int_env(key: str, default: int) -> int:
    """读取整数型环境变量，非法值回退默认。"""
    raw = _env(key)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def load_bot_settings() -> dict:
    """飞书机器人运行时参数（均可在 scripts/.env 覆盖，见 feishu_bot.py 文件头）。"""
    return {
        "wiki_timeout": _int_env("BOT_WIKI_TIMEOUT", 240),   # 单次 /chat 超时（秒）
        "wiki_retries": _int_env("BOT_WIKI_RETRIES", 2),     # 超时/网络错误重试次数
        "session_ttl": _int_env("BOT_SESSION_TTL", 3600),    # 多轮会话有效期（秒）
        "card_max_chars": _int_env("BOT_CARD_MAX_CHARS", 4500),  # 卡片正文上限（字）
        # 检索深度：chat 端点不传 topK 时实测只回 5 条，会把关键词命中的关键页挤出 Top-K
        # （C3/O4 零召回）；提高到 15 后关键页可召回、零召回消除（代价：P1 被稀释，见报告 §7）。
        "top_k": _int_env("BOT_TOP_K", 15),
    }
