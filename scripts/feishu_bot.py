# -*- coding: utf-8 -*-
"""飞书机器人：长连接接收消息 → 调 LLM Wiki 问答 → 卡片回复。

依赖：
    pip install "lark-oapi>=1.7.0"     # 见 scripts/requirements.txt

凭据（scripts/.env，已 gitignore，不入库）：
    FEISHU_APP_ID=cli_xxxxxxxx
    FEISHU_APP_SECRET=xxxxxxxx
    LLM_WIKI_API_TOKEN=xxxxxxxx        # 与 eval_chat.py 共用

可选调参（scripts/.env，缺省即用默认值）：
    BOT_WIKI_TIMEOUT=240       单次 /chat 超时（秒）
    BOT_WIKI_RETRIES=2         超时/网络错误重试次数
    BOT_SESSION_TTL=3600       多轮会话有效期（秒）
    BOT_CARD_MAX_CHARS=4500    卡片正文上限（字）

运行：
    python scripts/feishu_bot.py                 # 启动机器人（长连接，阻塞）
    python scripts/feishu_bot.py --selftest      # 自检：凭据 / API / 项目解析
    python scripts/feishu_bot.py --ask "问题"     # 不开飞书，直接测一条问答链路

前提（一次性）：
    1. LLM Wiki 桌面端运行中，且已开启「本地 HTTP API」（token 写入 scripts/.env）。
    2. 飞书开发者后台：创建企业自建应用 → 记 App ID/Secret → 添加「机器人」能力 →
       开权限（im:message.p2p_msg:readonly / im:message.group_at_msg:readonly /
       im:message:send_as_bot）→ 事件订阅选「使用长连接接收事件」并添加
       im.message.receive_v1 → 发布版本（个人应用免审核）。

机制说明：
    - 飞书长连接要求「收到事件后 3 秒内返回」，否则超时重推。因此事件回调只做入队、
      立即返回；真正的「提问 → 问答 → 回复」由后台 worker 线程串行执行。
    - LLM 问答串行化（单 worker）可避免智谱账号并发≈1 触发限流。
    - 机器人不直接持有大模型 Key：模型由 LLM Wiki 统一配置，这里只调它的 /chat 接口。
    - v0.2 增强：
        ① 卡片消息（interactive card）：结构化答案 + 来源脚注；
        ② 进度反馈：先回「正在查询…」卡片，答案就绪后**原地更新**（patch）；
        ③ 多轮对话：复用 LLM Wiki /chat 的原生 session（回传 sessionId）；
        ④ @识别：群聊剥离 <at> 标记（群聊仅在 @机器人 时才会收到事件）；
        ⑤ 超时重试：超时/网络错误按退避自动重试；
        ⑥ 命令：/help（帮助）、/reset（新对话）；
        ⑦ 用量埋点：每次问答追加一行元数据到 data/usage_log.jsonl
           （只记耗时/来源数/拒答标记等，**不记问题原文**），
           供 scripts/usage_stats.py 统计真实用量。
"""
import hashlib
import json
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

import lark_oapi as lark

from config import API_BASE, BASE, load_token, load_feishu, load_bot_settings

TOKEN = load_token()
APP_ID, APP_SECRET = load_feishu()
CFG = load_bot_settings()

# 用量埋点文件（data/ 已被 .gitignore 忽略，不会入库）
USAGE_LOG = BASE / "data" / "usage_log.jsonl"

WIKI_PROJECT = "training-qa"          # 本项目唯一的知识库项目名
WIKI_TIMEOUT = CFG["wiki_timeout"]
WIKI_RETRIES = CFG["wiki_retries"]
SESSION_TTL = CFG["session_ttl"]
CARD_MAX = CFG["card_max_chars"]

# 去掉群聊里的 <at user_id="...">@xxx</at> 提及标记，只留问题正文
_AT_RE = re.compile(r"<at\b[^>]*>.*?</at>", re.S)
# 卡片不支持 markdown 标题，把 # 标题降级为加粗
_HEAD_RE = re.compile(r"^#{1,6}\s*(.+?)\s*$", re.M)
# 拒答信号词（与 eval_refusal.py 口径保持一致，取最强信号）
_REFUSAL_HINT = re.compile(r"未能找到|未找到|未涉及|没有找到|缺少以下信息|无法回答|无法确定|知识库中未")

HELP_TEXT = (
    "**智能问答助手** 使用说明\n"
    "- 直接提问：我在企业培训知识库里检索并回答，附来源引用。\n"
    "- 多轮追问：同一会话 1 小时内保留上下文（如「那它呢？」）。\n"
    "- `/reset` 或 「新对话」：清空上下文，重新开始。\n"
    "- `/help` 或 「帮助」：显示本说明。"
)


# ---------------------------------------------------------------------------
# 用量埋点：只落「元数据」，绝不落问题原文
# ---------------------------------------------------------------------------
# 为什么记元数据不记原文：业务提问可能自带客户名/订单号等敏感信息，
# 与项目「脱敏后才落盘」的口径保持一致。这里只记长度与单向哈希，
# 既能统计去重提问数、又不会把敏感内容写进日志文件。
def _h(text: str) -> str:
    """单向短哈希：用于会话/问题去重，不落原文。"""
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:10]


def _log_usage(**rec) -> None:
    """追加一行用量记录；任何异常都吞掉，绝不影响问答主链路。"""
    try:
        rec["ts"] = datetime.now().astimezone().isoformat(timespec="seconds")
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as ex:
        print("[bot] 用量埋点写入失败（已忽略）:", ex)


# ---------------------------------------------------------------------------
# LLM Wiki 本地 HTTP 客户端（标准库零依赖；超时/网络错误自动退避重试）
# ---------------------------------------------------------------------------
def _api(path, method="GET", payload=None, retries=None):
    if retries is None:
        retries = WIKI_RETRIES
    url = f"{API_BASE}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    last_exc = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Authorization": f"Bearer {TOKEN}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=WIKI_TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError:
            raise  # 4xx/5xx 是确定性错误，重试无意义
        except (urllib.error.URLError, TimeoutError, OSError) as ex:
            last_exc = ex
            if attempt < retries:
                wait = 2 * (attempt + 1)
                print(f"[wiki] 请求异常（{ex}），{wait}s 后重试 {attempt + 1}/{retries}")
                time.sleep(wait)
    raise last_exc


_PID = None


def _resolve_project_id():
    """定位目标项目：优先 current，其次按名字匹配，最后取第一个。结果缓存。"""
    global _PID
    if _PID:
        return _PID
    d = _api("/api/v1/projects")
    projects = d.get("projects", [])
    pid = None
    for p in projects:
        if p.get("current"):
            pid = p["id"]
            break
    if not pid:
        for p in projects:
            if WIKI_PROJECT.lower() in (p.get("name") or "").lower():
                pid = p["id"]
                break
    if not pid and projects:
        pid = projects[0]["id"]
    if not pid:
        raise RuntimeError("LLM Wiki 里没有找到任何项目")
    _PID = pid
    return pid


def wiki_health():
    """探测 LLM Wiki 本地 API 是否在线。"""
    for path in ("/api/v1/health", "/api/v1/projects"):
        try:
            return {"ok": True, "path": path, **(_api(path, retries=0) if path.endswith("health") else {})}
        except Exception as ex:
            last = ex
    return {"ok": False, "error": str(last)}


def ask_wiki(question, session_id=None):
    """调用 LLM Wiki 内置 Agent 问答。

    Returns: (答案文本, [来源标题...], sessionId)
    传 session_id 即在同一会话里追问；返回值里的 sessionId 供下次回传。
    """
    pid = _resolve_project_id()
    payload = {"message": question, "stream": False, "persistSession": True}
    if session_id:
        payload["sessionId"] = session_id
    d = _api(f"/api/v1/projects/{pid}/chat", method="POST", payload=payload)
    msg = d.get("message") or {}
    answer = msg.get("content", "") if isinstance(msg, dict) else str(msg)
    refs = [r.get("title", "") for r in d.get("references", []) if r.get("title")]
    return answer.strip(), refs, (d.get("sessionId") or session_id)


# ---------------------------------------------------------------------------
# 卡片构造
# ---------------------------------------------------------------------------
def _dump(card) -> str:
    return json.dumps(card, ensure_ascii=False)


def _norm_md(text: str) -> str:
    """把卡片不支持的 markdown 语法降级：标题 → 加粗；压缩多余空行。"""
    text = _HEAD_RE.sub(lambda m: f"**{m.group(1)}**", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _card_status(text, template="grey"):
    """简短状态卡片（进度 / 命令回执 / 错误）。"""
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {"template": template,
                   "title": {"tag": "plain_text", "content": "智能问答助手"}},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": text}}],
    }


def _card_answer(answer, refs, elapsed=None):
    """答案卡片：正文 + 来源脚注。"""
    body = _norm_md(answer)[:CARD_MAX] or "（知识库里没有找到相关内容）"
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": body}}]
    notes = []
    if refs:
        notes.append("📎 来源：" + "、".join(refs[:3]))
    if elapsed is not None:
        notes.append(f"耗时 {elapsed}s")
    if notes:
        elements.append({"tag": "hr"})
        elements.append({"tag": "note",
                         "elements": [{"tag": "plain_text", "content": " · ".join(notes)}]})
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {"template": "blue",
                   "title": {"tag": "plain_text", "content": "智能问答助手"}},
        "elements": elements,
    }


# ---------------------------------------------------------------------------
# 飞书发送：回复卡片 / 更新卡片 / 回复纯文本（降级用）
# ---------------------------------------------------------------------------
def _reply_card(client, message_id, card) -> str:
    """回复一张卡片，返回新消息 id（用于后续 patch）。"""
    body = lark.im.v1.ReplyMessageRequestBody.builder() \
        .content(_dump(card)).msg_type("interactive").build()
    req = lark.im.v1.ReplyMessageRequest.builder() \
        .message_id(message_id).request_body(body).build()
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        raise RuntimeError(f"reply 失败 code={getattr(resp, 'code', '?')} msg={getattr(resp, 'msg', '?')}")
    return (resp.data.message_id if resp.data else "") or ""


def _patch_card(client, message_id, card) -> bool:
    """原地更新一张卡片（进度卡片 → 答案卡片）。"""
    try:
        body = lark.im.v1.PatchMessageRequestBody.builder().content(_dump(card)).build()
        req = lark.im.v1.PatchMessageRequest.builder() \
            .message_id(message_id).request_body(body).build()
        return bool(client.im.v1.message.patch(req).success())
    except Exception as ex:
        print("[bot] 更新卡片失败，将改用新消息回复:", ex)
        return False


# ---------------------------------------------------------------------------
# 多轮会话：chat_id -> sessionId（带 TTL）
# ---------------------------------------------------------------------------
_sessions = {}
_sessions_lock = threading.Lock()


def _get_session(chat_id):
    with _sessions_lock:
        rec = _sessions.get(chat_id)
        if not rec:
            return None
        if time.time() - rec["ts"] > SESSION_TTL:
            _sessions.pop(chat_id, None)
            return None
        return rec["sessionId"]


def _set_session(chat_id, session_id):
    if not session_id:
        return
    with _sessions_lock:
        _sessions[chat_id] = {"sessionId": session_id, "ts": time.time()}


def _reset_session(chat_id) -> bool:
    """清空该会话上下文，返回此前是否存在会话。"""
    with _sessions_lock:
        return _sessions.pop(chat_id, None) is not None


# ---------------------------------------------------------------------------
# 后台 worker：串行处理提问队列（3 秒约束 + 智谱并发≈1 都由这里消化）
# ---------------------------------------------------------------------------
_task_queue = queue.Queue()

_RESET_WORDS = {"/reset", "新对话", "重置"}
_HELP_WORDS = {"/help", "帮助", "help", "?", "？"}


def _handle_command(client, message_id, chat_id, text) -> bool:
    """处理内置命令；命中返回 True。"""
    cmd = text.strip().lower()
    if cmd in _HELP_WORDS:
        _reply_card(client, message_id, _card_status(HELP_TEXT, "turquoise"))
        return True
    if cmd in _RESET_WORDS:
        had = _reset_session(chat_id)
        tip = "✅ 已开始新对话" if had else "当前已是新对话（无历史上下文）"
        _reply_card(client, message_id, _card_status(tip, "green"))
        return True
    return False


def _worker(client):
    while True:
        job = _task_queue.get()
        if job is None:            # 收到停止信号
            break
        message_id, chat_id, text = job
        try:
            if _handle_command(client, message_id, chat_id, text):
                continue

            # 1) 先回进度卡片
            prog_id = ""
            try:
                prog_id = _reply_card(client, message_id,
                                      _card_status("🔍 正在查询知识库，请稍候…", "grey"))
            except Exception as ex:
                print("[bot] 进度卡片发送失败（继续）:", ex)

            # 2) 调知识库（带重试）
            t0 = time.time()
            sid = _get_session(chat_id)
            answer, refs, sess = ask_wiki(text, sid)
            elapsed = round(time.time() - t0, 1)
            _set_session(chat_id, sess)
            card = _card_answer(answer, refs, elapsed)

            # 用量埋点（元数据；失败不影响回复）
            _log_usage(source="feishu", chat=_h(chat_id), q_hash=_h(text),
                       q_len=len(text), latency_s=elapsed, n_refs=len(refs),
                       multi_turn=bool(sid), refused=bool(_REFUSAL_HINT.search(answer or "")),
                       ok=True)

            # 3) 原地更新进度卡片；失败则退回「新消息回复」
            if not prog_id or not _patch_card(client, prog_id, card):
                _reply_card(client, message_id, card)
        except Exception as ex:    # 任何异常都回一条提示，别让用户干等
            _log_usage(source="feishu", chat=_h(chat_id), q_hash=_h(text),
                       q_len=len(text or ""), ok=False, error=str(ex)[:120])
            try:
                _reply_card(client, message_id, _card_status(f"❌ 问答出错了：{ex}", "red"))
            except Exception:
                print("[bot] 错误提示发送失败:", ex)


# ---------------------------------------------------------------------------
# 事件处理：只入队，立即返回（满足飞书 3 秒内返回的要求）
# ---------------------------------------------------------------------------
def do_p2_im_message_receive_v1(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
    try:
        msg = data.event.message
        if msg.message_type != "text":      # 只处理文本，图片/文件等先忽略
            return
        content = msg.content or "{}"
        try:
            text = json.loads(content).get("text", "")
        except Exception:
            text = content
        text = _AT_RE.sub("", text).strip()   # 剥离 @机器人 提及
        if not text:
            return
        _task_queue.put((msg.message_id, msg.chat_id or "", text))
    except Exception as ex:
        # 事件回调绝不允许抛异常（否则长连接中断），只打印
        print("[handler] 处理失败（已忽略）:", ex)


# ---------------------------------------------------------------------------
# 自检 / 单次问答（不开飞书，用于验证链路）
# ---------------------------------------------------------------------------
def _selftest():
    print("== 飞书机器人自检 ==")
    print("FEISHU_APP_ID    :", "已配置" if APP_ID else "❌ 缺失")
    print("FEISHU_APP_SECRET:", "已配置" if APP_SECRET else "❌ 缺失")
    print("LLM_WIKI_API_TOKEN:", "已配置" if TOKEN else "❌ 缺失")
    print("运行时参数        :", CFG)
    print("LLM Wiki API     :", json.dumps(wiki_health(), ensure_ascii=False))
    try:
        print("项目 ID          :", _resolve_project_id())
    except Exception as ex:
        print("项目解析失败      :", ex)


def _ask_once(question):
    print(f"提问：{question}")
    t0 = time.time()
    answer, refs, sess = ask_wiki(question)
    elapsed = round(time.time() - t0, 1)
    print(f"（耗时 {elapsed}s，sessionId={sess}）\n")
    print(answer or "（知识库里没有找到相关内容）")
    if refs:
        print("\n📎 来源：" + "、".join(refs[:3]))
    # 命令行自测也埋点，但标 source=cli，避免与真实群聊用量混在一起
    _log_usage(source="cli", chat=_h(question), q_hash=_h(question), q_len=len(question),
               latency_s=elapsed, n_refs=len(refs), multi_turn=False,
               refused=bool(_REFUSAL_HINT.search(answer or "")), ok=True)


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        _selftest()
        return
    if "--ask" in args:
        i = args.index("--ask")
        q = args[i + 1] if len(args) > i + 1 else ""
        if not q:
            print('用法：python scripts/feishu_bot.py --ask "你的问题"')
            return
        _ask_once(q)
        return

    if not APP_ID or not APP_SECRET:
        print("请先在 scripts/.env 填入 FEISHU_APP_ID / FEISHU_APP_SECRET（见文件头注释）")
        return
    if not TOKEN:
        print("请先在 scripts/.env 填入 LLM_WIKI_API_TOKEN")
        return

    client = lark.Client.builder() \
        .app_id(APP_ID).app_secret(APP_SECRET) \
        .log_level(lark.LogLevel.INFO).build()

    threading.Thread(target=_worker, args=(client,), daemon=True).start()

    event_handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1) \
        .build()

    print("正在建立飞书长连接……")
    print("连接成功后，到飞书群 @机器人 提问；支持多轮追问，/reset 可开新对话。")
    ws_client = lark.ws.Client(APP_ID, APP_SECRET, event_handler=event_handler,
                               log_level=lark.LogLevel.INFO)
    ws_client.start()      # 阻塞主线程，直到进程结束


if __name__ == "__main__":
    main()
