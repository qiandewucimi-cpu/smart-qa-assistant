# -*- coding: utf-8 -*-
"""阶段 1 清洗脚本：对纯文字文件（txt/md）做脱敏 + 元数据标注。

只读源文件，输出到 data/clean/，绝不修改工作内容/ 下的任何原始文件。
用法：python clean_text.py
"""
import re
from pathlib import Path

from config import BASE
SRC = BASE / "工作内容"
OUT = BASE / "data" / "clean"

# 脱敏映射从 maps_local.py 加载（含真实姓名/公司/客户/地名，该文件被 .gitignore
# 忽略、绝不入库）。若 maps_local.py 缺失，退化为空映射（脚本可运行但不脱敏）。
try:
    from maps_local import NAME_MAP, COMPANY_MAP, CLIENT_MAP, CLIENT_NAMES
except ImportError:
    NAME_MAP, COMPANY_MAP, CLIENT_MAP, CLIENT_NAMES = {}, [], {}, []

# 主题映射（按顶层目录）
TOPIC_MAP = {
    "会议纪要": "培训会议纪要",
    "聊天记录": "群聊答疑记录",
    "每日日报": "学员学习笔记",
    "单据重点": "单据理解",
    "培训文档": "DLS系统操作手册",
    "提出企业ai化建议": "项目背景方案",
    "订单培训文档": "订单理单实操",
    "": "项目规划",
}

IP_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
URL_RE = re.compile(r"https?://\S+")
# 客户订单号正则（客户名 + 6+ 位数字），由 CLIENT_NAMES 动态生成，避免在代码里硬编码真实客户名
if CLIENT_NAMES:
    _client_pat = "|".join(re.escape(n) for n in CLIENT_NAMES)
    CLIENT_ORDER_RE = re.compile(rf"\b(?:{_client_pat})\s?\d{{6,}}\b", re.IGNORECASE)
else:
    CLIENT_ORDER_RE = re.compile(r"(?!)")  # 无客户名时永不匹配


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def desensitize(text: str) -> tuple[str, dict]:
    hits = {}

    # 1) 带客户前缀的订单号（先处理，避免客户名替换后丢失前缀）
    text, n = CLIENT_ORDER_RE.subn("[订单号]", text)
    hits["客户订单号"] = n
    # 2) 人名（长词优先，避免简称先命中全名）
    for k in sorted(NAME_MAP, key=len, reverse=True):
        text, n = re.subn(re.escape(k), NAME_MAP[k], text)
        hits["人名"] = hits.get("人名", 0) + n
    # 3) 公司/地名（长词优先，避免公司名先拆掉带地名前缀的实体）
    for k, v in sorted(COMPANY_MAP, key=lambda x: len(x[0]), reverse=True):
        text, n = re.subn(re.escape(k), v, text)
        hits["公司名"] = hits.get("公司名", 0) + n
    # 4) 客户
    for k, v in CLIENT_MAP.items():
        text, n = re.subn(re.escape(k), v, text)
        hits["客户名"] = hits.get("客户名", 0) + n
    # 5) URL / IP
    text, n = URL_RE.subn("[链接]", text)
    hits["链接"] = hits.get("链接", 0) + n
    text, n = IP_RE.subn("[内网地址]", text)
    hits["内网IP"] = hits.get("内网IP", 0) + n
    return text, hits


def topic_for(rel: Path) -> str:
    parts = rel.parts
    if not parts:
        return TOPIC_MAP[""]
    top = parts[0]
    return TOPIC_MAP.get(top, "其他")


def main():
    files = [p for p in SRC.rglob("*") if p.suffix.lower() in (".txt", ".md")]
    files.sort()
    out_files = []
    total_hits = {}
    skipped = []

    for f in files:
        rel = f.relative_to(SRC)
        text = read_text(f)
        if not text.strip():
            skipped.append(str(rel))
            continue
        cleaned, hits = desensitize(text)
        for k, v in hits.items():
            total_hits[k] = total_hits.get(k, 0) + v

        # 元数据头的「来源目录 / 原文件名」也可能含真实姓名（如目录名「学员A笔记」），一并脱敏
        src_dir, _ = desensitize(str(rel.parent or "."))
        src_name, _ = desensitize(rel.name)
        header = (
            "# ---- 元数据 ----\n"
            f"# 来源目录: {src_dir}\n"
            f"# 业务主题: {topic_for(rel)}\n"
            f"# 原文件名: {src_name}\n"
            "# 已脱敏: 是\n"
            "# ---- 正文 ----\n\n"
        )
        # 输出路径的每一级目录/文件名也脱敏（如真实姓名目录 → 化名目录）
        rel_clean = Path(*[desensitize(p)[0] for p in rel.parts])
        dest = OUT / rel_clean
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(header + cleaned, encoding="utf-8")
        out_files.append(str(rel_clean))

    # 打印报告
    print("=" * 50)
    print(f"处理文件数: {len(out_files)}  跳过空文件: {len(skipped)}")
    print("脱敏统计:")
    for k in sorted(total_hits, key=lambda x: -total_hits[x]):
        print(f"  {k}: {total_hits[k]} 处")
    print("=" * 50)
    if skipped:
        print("跳过的空文件:")
        for s in skipped:
            print("  " + s)


if __name__ == "__main__":
    main()
