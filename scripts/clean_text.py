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

# 内部合同号：公司/系列前缀 + 款号编码，如 AB1CDE23-0059。
# 客户订单号规则要求「客户名 + 长数字」，覆盖不到这种内部编号，故单列一条，
# 前缀泛化为任意 2 位大写字母，换公司/换系列都不用改代码。
CONTRACT_RE = re.compile(r"\b[A-Z]{2}\d[A-Z]{3}\d{2}-\d{4}\b")

# 英文法人主体：任意大写起头的词（最多 5 个）+ 公司后缀。
# 客户原始订单/英文单据里会出现「某外贸公司英文抬头」「海外工厂 Lingerie Ltd.」
# 「境外货代 Fulfilment GmbH」这类主体，逐个补词是打地鼠，改用结构规则。
LEGAL_ENTITY_RE = re.compile(
    r"\b[A-Z][\w&.,'’()-]*(?:\s+(?:&\s+)?[A-Z][\w&.,'’()-]*){0,4}"
    r"(?:\s+(?:GmbH|Ltd\.?|LLC|Inc\.?|KGaA|S\.?A\.?|Co\.|Corp\.?|B\.?V\.?|Pte\.?))+",
)

# ---- 截图 OCR 专用的结构化规则 ----
# DLS 系统界面里会出现银行信息与官网，这些在文本文档里没有，词表也覆盖不到。
# 中文法人主体：与英文 LEGAL_ENTITY_RE 对称，覆盖「XX国贸 / XX实业 / XX有限公司」。
# 不包含裸「贸易」——否则「国际贸易」「一般贸易」这类通用词会被误伤。
# 前缀逐字排除虚词/动词（的、按、为、默…），否则会把「的返佣按集团」整段吞掉。
CN_ENTITY_RE = re.compile(
    r"(?:(?!的|了|和|与|按|为|有|是|但|们|个|默|会|说|在|等|被|把)"
    r"[\u4e00-\u9fa5]){2,6}"
    r"(?:国贸|实业|集团有限公司|有限公司|集团)"
)
# 银行账号：形如 0000-486217-837
BANK_ACCT_RE = re.compile(r"\b\d{3,4}-\d{6}-\d{2,5}\b")
# SWIFT/BIC 码：**必须上下文锚定**。裸的 8/11 位大写串会误伤正常业务词——
# 实测 SHIPPING / STANDARD / MATERIAL / PRODUCTS 这类正常业务词全部符合 BIC 的字面格式。
BIC_RE = re.compile(r"((?:SWIFT\s*(?:Code)?|BIC)\s*[:：]\s*)([A-Z0-9]{8,11})", re.IGNORECASE)
# 裸域名（无 http:// 前缀）：www.example.com、example-brands.com 之类
DOMAIN_RE = re.compile(
    r"\b(?:www\.)?[\w-]+\.(?:com|cn|de|net|org|io|co)(?:\.[a-z]{2})?\b", re.IGNORECASE
)


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _key_re(key: str) -> re.Pattern:
    """构造词条匹配正则。

    - 纯 ASCII 词条（英文公司名/客户名）：**忽略大小写 + 加词边界**。
      OCR 输出的大小写不可控（截图里是全大写抬头，文档里是首字母大写形式），
      大小写敏感会漏；而英文词不加边界又会误伤（如 ABC 命中 ABCDEF）。
    - 中文词条：保持子串匹配（中文没有词边界概念）。
    """
    esc = re.escape(key)
    if re.fullmatch(r"[\x20-\x7e]+", key):
        return re.compile(rf"(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(esc)


def desensitize(text: str) -> tuple[str, dict]:
    hits = {}

    # 1) 带客户前缀的订单号（先处理，避免客户名替换后丢失前缀）
    text, n = CLIENT_ORDER_RE.subn("[订单号]", text)
    hits["客户订单号"] = n
    # 1.5) 内部合同号（公司前缀 + 款号编码）
    text, n = CONTRACT_RE.subn("[内部合同号]", text)
    hits["内部合同号"] = n
    # 2) 人名（长词优先，避免简称先命中全名）
    for k in sorted(NAME_MAP, key=len, reverse=True):
        text, n = _key_re(k).subn(NAME_MAP[k], text)
        hits["人名"] = hits.get("人名", 0) + n
    # 3) 英文法人主体（客户/供应商/货代抬头），放在中文公司名之前
    text, n = LEGAL_ENTITY_RE.subn("[企业主体]", text)
    hits["英文主体"] = n
    # 4) 公司/地名（长词优先，避免公司名先拆掉带地名前缀的实体）
    for k, v in sorted(COMPANY_MAP, key=lambda x: len(x[0]), reverse=True):
        text, n = _key_re(k).subn(v, text)
        hits["公司名"] = hits.get("公司名", 0) + n
    # 5) 客户
    for k in sorted(CLIENT_MAP, key=len, reverse=True):
        text, n = _key_re(k).subn(CLIENT_MAP[k], text)
        hits["客户名"] = hits.get("客户名", 0) + n
    # 6) 银行账号 / SWIFT / 裸域名 / 中文法人主体（截图里常见，文本语料里没有）
    text, n = CN_ENTITY_RE.subn("[企业主体]", text)
    hits["中文主体"] = n
    text, n = BANK_ACCT_RE.subn("[银行账号]", text)
    hits["银行账号"] = n
    text, n = BIC_RE.subn(lambda m: m.group(1) + "[银行代码]", text)
    hits["银行代码"] = n
    text, n = DOMAIN_RE.subn("[链接]", text)
    hits["裸域名"] = n
    # 7) URL / IP
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
