# -*- coding: utf-8 -*-
"""脱敏巡检：在「入库前」和「入库后」两个位置搜真实敏感词，双保险。

为什么需要它：先处理再导入（脱敏闸门）是主防线，但闸门靠 rules 吃饭，rules 会漏
（本轮就漏过公司英文抬头、客户名、银行账号）。LLM Wiki 把语料编译成 461 个 wiki 页
后，敏感词可能已扩散到 entities / concepts / sources 多类页面——事后才发现成本极高。
本脚本用**真实词表 + 结构化正则**去反查，把「漏网」变成一条能进 CI 的命令：

    python audit_leaks.py            # 默认查 data/clean + LLM Wiki 项目目录
    python audit_leaks.py --only clean
    python audit_leaks.py --quiet    # 只输出汇总，适合挂 CI
    python audit_leaks.py --scan-unknown   # 挖「词表里没有的疑似专名」，补词表用

--scan-unknown 为什么存在：脱敏靠词表替换本质是打地鼠，词表外的新客户/公司名必然漏
（2026-09-11 实测：某客户名一路从 data/clean 漏进 wiki，还被编译成独立实体页）。
该模式扫「全大写拉丁词」，按出现文档数排序并给出上下文，供人工判断哪些要补进词表。

退出码：0 = 干净，1 = 发现残留（可直接用作流水线卡点）。
"""
import argparse
import re
import sys
from pathlib import Path

from clean_text import (
    BANK_ACCT_RE, CONTRACT_RE,
    CLIENT_MAP, COMPANY_MAP, NAME_MAP,
)
from config import BASE

# 巡检目标：入库前的清洗产物 + LLM Wiki 项目目录（raw 来源副本 + wiki 编译页）
TARGETS = {
    "clean": BASE / "data" / "clean",
    "wiki": BASE / "projects" / "training-qa" / "training-qa",
}
SKIP_DIRS = {"ocr_cache", "docx_media", "import_batches", ".obsidian", ".git", "node_modules"}
TEXT_EXT = {".txt", ".md", ".json", ".csv", ".log"}

# 真实敏感词（从 gitignored 的 maps_local 读，代码里不留明文）
REAL_TERMS = sorted(
    set(NAME_MAP) | {k for k, _ in COMPANY_MAP} | set(CLIENT_MAP),
    key=len, reverse=True,
)

# 巡检用的 IP 正则：**排除回环/占位地址**。文档与配置里大量出现 127.0.0.1:19828
# （本地 API 地址）、0.0.0.0（监听地址），它们不是敏感信息，否则每次巡检都是噪声。
IP_RE_AUDIT = re.compile(r"\b(?!127\.)(?!0\.0\.0\.0\b)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")

# 结构化规则：真实词表覆盖不到的形态
STRUCT_RULES = [
    ("内部合同号", CONTRACT_RE),
    ("内网IP", IP_RE_AUDIT),
    ("银行账号", BANK_ACCT_RE),
]


def iter_files(root: Path):
    if not root.exists():
        return
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXT:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        # maps_local 本身是词表，注定「命中」，跳过
        if p.name == "maps_local.py":
            continue
        yield p


def scan_file(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    findings = []
    for term in REAL_TERMS:
        # 英文词条用词边界，避免 specification 命中 ific 这类误报
        if re.fullmatch(r"[\x20-\x7e]+", term):
            pat = re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", re.IGNORECASE)
        else:
            pat = re.compile(re.escape(term))
        for m in pat.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            findings.append((line, "词表", term))
    for label, pat in STRUCT_RULES:
        for m in pat.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            findings.append((line, label, m.group()))
    return findings


# --- 未知专名扫描（补词表用）-------------------------------------------
# 只抓「全大写拉丁词」：OCR 出来的客户/公司名几乎都是这个形态
# （真实客户名均为全大写拉丁词，具体清单见 maps_local.py，此处不列举以免泄漏）。
UNKNOWN_RE = re.compile(r"\b[A-Z][A-Z0-9]{2,14}\b")

# 明显不是专名的通用缩写：贸易术语、单位、系统名、表格表头等。
# 宁可多列一些，让输出聚焦在真正可疑的词上。
KNOWN_CAPS = {
    # 贸易/物流术语
    "FOB", "FCA", "CIF", "CFR", "EXW", "DDP", "DDU", "DAP", "DAT", "LCL", "FCL",
    "ETD", "ETA", "ETS", "ATD", "ATA", "BL", "LC", "TT", "DP", "DA", "CAD",
    # 单位/包装
    "CBM", "KGS", "KG", "CTN", "PCS", "SETS", "PC", "CM", "MM", "INCH", "GSM",
    # 货币
    "USD", "EUR", "GBP", "HKD", "JPY", "CNY", "RMB", "TWD", "KRW",
    # 系统/技术
    "DLS", "IGST", "GST", "ERP", "PLM", "MRP", "WIP", "SOP", "BOM", "SKU", "MOQ",
    "OEM", "ODM", "QC", "QA", "IT", "IP", "ID", "URL", "HTML", "JSON", "XML",
    "HTTP", "HTTPS", "API", "SDK", "SQL", "PDF", "CSV", "XLSX", "DOCX", "XLS",
    "DOC", "OCR", "VAT", "OA", "CP", "PI", "CI", "PL", "GPS", "USB", "CPU",
    # 表格表头/通用词（OCR 里常全大写）
    "DATE", "NAME", "CODE", "TYPE", "ITEM", "STYLE", "SIZE", "COLOR", "QTY",
    "QUANTITY", "PRICE", "UNIT", "AMOUNT", "TOTAL", "WEIGHT", "REMARK", "REMARKS",
    "NOTE", "NOTES", "DESCRIPTION", "DESC", "DESTINATION", "PORT", "TERMS",
    "CONDITIONS", "SIGNATURE", "PAGE", "SHEET", "VERSION", "MODEL", "BRAND",
    "LABEL", "MATERIAL", "CUSTOMER", "SUPPLIER", "ORDER", "CONTRACT", "DELIVERY",
    "PAYMENT", "SHIPPING", "PACKING", "INVOICE", "MARKS", "NOS", "NO", "OK",
    "AM", "PM", "MAX", "MIN", "AVG", "SUM", "NUM", "REF", "SPEC", "STYLE",
    "NYLON", "COTTON", "POLYESTER", "SPANDEX", "VISCOSE", "MODAL", "TENCEL",
    "LACE", "MESH", "FOAM", "WIRE", "HOOK", "EYE", "RING", "SLIDER", "STRAP",
    "BAND", "CUP", "WING", "BRA", "PANTY", "GIRDLE", "SHAPEWEAR", "SWIM",
    "YES", "TRUE", "FALSE", "NULL", "NONE", "NEW", "OLD", "ALL", "AND",
    "THE", "FOR", "WITH", "FROM", "THIS", "THAT", "NOTE", "STEP", "TIPS",
}

# 已收进词表的词不再重复报（大小写不敏感）
_KNOWN_TERMS_UPPER = {t.upper() for t in REAL_TERMS if re.fullmatch(r"[A-Za-z0-9]+", t)}


def scan_unknown(root: Path, top: int = 60):
    """返回 [(token, 文档数, 总次数, 样例上下文)]，按文档数降序。"""
    stat = {}
    for f in iter_files(root):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        local = {}
        for m in UNKNOWN_RE.finditer(text):
            tok = m.group()
            if tok in KNOWN_CAPS or tok.upper() in _KNOWN_TERMS_UPPER:
                continue
            if tok.isdigit():
                continue
            s = text.rfind("\n", 0, m.start()) + 1
            e = text.find("\n", m.end())
            ctx = text[s:e if e > 0 else len(text)].strip()[:90]
            local.setdefault(tok, [0, ctx])
            local[tok][0] += 1
        for tok, (n, ctx) in local.items():
            d = stat.setdefault(tok, [0, 0, ctx])
            d[0] += 1
            d[1] += n
    ranked = sorted(stat.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))
    return [(t, v[0], v[1], v[2]) for t, v in ranked[:top]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=list(TARGETS), help="只查某一处")
    ap.add_argument("--quiet", action="store_true", help="只输出汇总")
    ap.add_argument("--scan-unknown", action="store_true",
                    help="扫描疑似专名（词表外的全大写拉丁词），用于补词表")
    ap.add_argument("--top", type=int, default=60, help="--scan-unknown 输出条数")
    args = ap.parse_args()

    if args.scan_unknown:
        roots = [TARGETS[args.only]] if args.only else list(TARGETS.values())
        for root in roots:
            if not root.exists():
                print(f"目录不存在，跳过：{root}")
                continue
            print(f"=== 疑似专名扫描：{root} ===")
            rows = scan_unknown(root, args.top)
            print(f"{'词':<18}{'文档数':>6}{'总次数':>8}  样例上下文")
            for tok, docs, n, ctx in rows:
                print(f"{tok:<18}{docs:>6}{n:>8}  {ctx}")
            print(f"（共 {len(rows)} 条；判断后把敏感词补进 maps_local.py）")
        return

    targets = {args.only: TARGETS[args.only]} if args.only else TARGETS
    print(f"巡检词条: 真实词表 {len(REAL_TERMS)} 条 + 结构化规则 {len(STRUCT_RULES)} 条")
    print(f"巡检目标: {', '.join(f'{k} -> {v}' for k, v in targets.items())}")
    print("=" * 64)

    total_files = total_hits = 0
    bad_files = []
    for label, root in targets.items():
        if not root.exists():
            print(f"[{label}] 目录不存在，跳过：{root}")
            continue
        n_files = n_hits = 0
        for f in iter_files(root):
            n_files += 1
            found = scan_file(f)
            if not found:
                continue
            n_hits += len(found)
            bad_files.append((label, f, found))
            if not args.quiet:
                print(f"\n✗ [{label}] {f.relative_to(root)}")
                for line, kind, val in found[:12]:
                    print(f"    L{line:<6} {kind:<8} {val!r}")
                if len(found) > 12:
                    print(f"    ... 另有 {len(found) - 12} 处")
        total_files += n_files
        total_hits += n_hits
        print(f"[{label}] 扫描 {n_files} 个文件，命中 {n_hits} 处")

    print("=" * 64)
    if total_hits:
        print(f"⚠️  巡检不通过：{len(bad_files)} 个文件共 {total_hits} 处残留")
        print("   处理建议：补 maps_local.py 词表 / 补 clean_text.py 结构化规则 → 重跑抽取 → 重导")
        sys.exit(1)
    print(f"✅ 巡检通过：{total_files} 个文件零残留")


if __name__ == "__main__":
    main()
