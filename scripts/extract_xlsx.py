# -*- coding: utf-8 -*-
"""阶段 1 补充：xlsx 抽文字 + 脱敏（零依赖，标准库解析 xlsx 的 zip+XML）。

xlsx 本质是 zip 包：xl/sharedStrings.xml 存字符串池、xl/workbook.xml 定义工作表顺序、
xl/worksheets/sheetN.xml 存单元格、xl/styles.xml 提供数字格式（用于把日期序列号还原成日期）。

复用 clean_text.desensitize 与元数据规范，输出到 data/clean/。
用法：python extract_xlsx.py
"""
import datetime as _dt
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from clean_text import desensitize, topic_for, SRC, OUT

S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# Excel 内置的日期/时间数字格式 id（ECMA-376 规定，0~49 为内置）
_BUILTIN_DATE_IDS = set(range(14, 23)) | set(range(45, 48))
_DATE_TOKEN_RE = re.compile(r"(?<!\\)([ymdhs])", re.IGNORECASE)


def _col_index(ref: str) -> int:
    """把单元格引用（如 C12）里的列字母转成 0 基列号。"""
    letters = re.match(r"([A-Z]+)", ref.upper())
    if not letters:
        return 0
    n = 0
    for ch in letters.group(1):
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _serial_to_str(value: float) -> str:
    """Excel 日期序列号 → 可读日期字符串（基准 1899-12-30，兼容 1900 闰年 bug）。"""
    days = int(value)
    frac = value - days
    base = _dt.datetime(1899, 12, 30)
    if days == 60:  # Excel 虚构的 1900-02-29，直接夹逼掉
        days = 59
    dt = base + _dt.timedelta(days=days, seconds=round(frac * 86400))
    if frac > 0:
        return dt.strftime("%Y-%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d")


class Workbook:
    """一个 xlsx 文件的只读视图：字符串池 + 工作表顺序 + 日期格式集合。"""

    def __init__(self, path: Path):
        self.z = zipfile.ZipFile(path)
        self.shared = self._load_shared()
        self.date_xf = self._load_date_styles()
        self.sheets = self._load_sheet_order()

    # ---- sharedStrings：字符串池，单元格用 t="s" + 下标引用 ----
    def _load_shared(self) -> list:
        if "xl/sharedStrings.xml" not in self.z.namelist():
            return []
        root = ET.fromstring(self.z.read("xl/sharedStrings.xml"))
        out = []
        for si in root.findall(S + "si"):
            # 富文本会被拆成多个 <r><t>，全部拼起来
            out.append("".join(t.text or "" for t in si.iter(S + "t")))
        return out

    # ---- styles：找出哪些 xf 用了日期格式，用于还原序列号 ----
    def _load_date_styles(self) -> set:
        if "xl/styles.xml" not in self.z.namelist():
            return set()
        root = ET.fromstring(self.z.read("xl/styles.xml"))
        # 自定义格式 id → 格式串
        custom = {}
        for nf in root.iter(S + "numFmt"):
            try:
                custom[int(nf.get("numFmtId"))] = nf.get("formatCode") or ""
            except (TypeError, ValueError):
                continue
        date_ids = set(_BUILTIN_DATE_IDS)
        for fid, code in custom.items():
            # 去掉引号/方括号里的字面量后再看有没有 y/m/d/h/s
            probe = re.sub(r'"[^"]*"|\[[^\]]*\]', "", code)
            if _DATE_TOKEN_RE.search(probe):
                date_ids.add(fid)
        cell_xfs = root.find(S + "cellXfs")
        date_xf = set()
        if cell_xfs is not None:
            for i, xf in enumerate(cell_xfs.findall(S + "xf")):
                try:
                    if int(xf.get("numFmtId", "0")) in date_ids:
                        date_xf.add(i)
                except ValueError:
                    continue
        return date_xf

    # ---- workbook.xml + rels：拿到工作表名与对应 xml 路径（按显示顺序）----
    def _load_sheet_order(self) -> list:
        names = self.z.namelist()
        if "xl/workbook.xml" not in names:
            return []
        # rId → 目标路径
        targets = {}
        if "xl/_rels/workbook.xml.rels" in names:
            rels = ET.fromstring(self.z.read("xl/_rels/workbook.xml.rels"))
            for rel in rels:
                targets[rel.get("Id")] = rel.get("Target") or ""
        root = ET.fromstring(self.z.read("xl/workbook.xml"))
        sheets = []
        for sh in root.iter(S + "sheet"):
            rid = sh.get(R + "id")
            target = targets.get(rid, "")
            if not target:
                continue
            path = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
            sheets.append((sh.get("name") or path, path))
        if not sheets:  # 兜底：没有 workbook.xml 关系时按文件名排序
            sheets = [(p.rsplit("/", 1)[-1], p) for p in names if p.startswith("xl/worksheets/")]
        return sheets

    # ---- 单个工作表 → 行文本 ----
    def sheet_lines(self, path: str) -> list:
        if path not in self.z.namelist():
            return []
        root = ET.fromstring(self.z.read(path))
        lines = []
        for row in root.iter(S + "row"):
            cells = {}
            for c in row.findall(S + "c"):
                ref = c.get("r") or ""
                idx = _col_index(ref) if ref else len(cells)
                cells[idx] = self._cell_text(c)
            if not any(v.strip() for v in cells.values()):
                continue  # 整行为空则跳过
            width = max(cells) + 1
            line = " | ".join(cells.get(i, "").strip() for i in range(width))
            lines.append(line.rstrip(" |"))
        return lines

    def _cell_text(self, c) -> str:
        t = c.get("t")
        if t == "inlineStr":
            is_el = c.find(S + "is")
            return "".join(x.text or "" for x in is_el.iter(S + "t")) if is_el is not None else ""
        v = c.find(S + "v")
        if v is None or v.text is None:
            return ""
        raw = v.text
        if t == "s":  # 共享字符串下标
            try:
                i = int(raw)
            except ValueError:
                return raw
            return self.shared[i] if 0 <= i < len(self.shared) else ""
        if t in ("str", "e"):  # 公式结果串 / 错误值
            return raw
        if t == "b":  # 布尔
            return "TRUE" if raw == "1" else "FALSE"
        # 无 t 或 t="n"：数字，可能是日期序列号
        try:
            xf = int(c.get("s", "-1"))
        except ValueError:
            xf = -1
        if xf in self.date_xf:
            try:
                return _serial_to_str(float(raw))
            except ValueError:
                return raw
        return raw


def extract_xlsx_text(path: Path) -> tuple:
    """返回 (正文文本, 工作表数, 行数)。"""
    wb = Workbook(path)
    blocks = []
    total_rows = 0
    for name, spath in wb.sheets:
        lines = wb.sheet_lines(spath)
        total_rows += len(lines)
        if lines:
            blocks.append(f"【工作表：{name}】\n" + "\n".join(lines))
    return "\n\n".join(blocks), len(wb.sheets), total_rows


def main():
    files = sorted(SRC.rglob("*.xlsx"))
    total_hits = {}
    done, failed, empty = [], [], []
    for f in files:
        rel = f.relative_to(SRC)
        try:
            text, n_sheets, n_rows = extract_xlsx_text(f)
        except Exception as e:  # noqa: BLE001
            failed.append((str(rel), str(e)))
            continue
        if not text.strip():
            empty.append(str(rel))
            continue
        cleaned, hits = desensitize(text)
        for k, v in hits.items():
            total_hits[k] = total_hits.get(k, 0) + v
        src_dir, _ = desensitize(str(rel.parent or "."))
        src_name, _ = desensitize(rel.name)
        header = (
            "# ---- 元数据 ----\n"
            f"# 来源目录: {src_dir}\n"
            f"# 业务主题: {topic_for(rel)}\n"
            f"# 原文件名: {src_name}\n"
            f"# 工作表数: {n_sheets}\n"
            "# 已脱敏: 是\n"
            "# ---- 正文 ----\n\n"
        )
        rel_clean = Path(*[desensitize(p)[0] for p in rel.parts])
        dest = OUT / rel_clean.with_suffix(".txt")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(header + cleaned, encoding="utf-8")
        done.append((str(rel_clean), n_sheets, n_rows))

    print("=" * 50)
    print(f"xlsx 抽文字完成: {len(done)} 个  失败: {len(failed)}  空: {len(empty)}")
    print("脱敏统计:", total_hits)
    print("-" * 50)
    for name, ns, nr in done:
        print(f"  {name}  [表 {ns} / 行 {nr}]")
    if failed:
        print("失败文件:")
        for rel, e in failed:
            print(f"  {rel}  ->  {e}")
    if empty:
        print("无内容文件:", empty)


if __name__ == "__main__":
    main()
