# -*- coding: utf-8 -*-
"""阶段 1 补充：docx 抽文字 + 脱敏（零依赖，标准库解析 docx 的 XML）。

复用 clean_text.desensitize 与元数据规范，输出到 data/clean/。
用法：python extract_docx.py
"""
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from clean_text import desensitize, topic_for, SRC, OUT

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def para_text(p):
    return "".join(t.text or "" for t in p.iter(W + "t")).strip()


def extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        if "word/document.xml" not in z.namelist():
            return ""
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    body = root.find(W + "body")
    if body is None:
        return ""
    lines = []
    for child in body:
        if child.tag == W + "p":
            t = para_text(child)
            if t:
                lines.append(t)
        elif child.tag == W + "tbl":
            for row in child.findall(W + "tr"):
                cells = []
                for tc in row.findall(W + "tc"):
                    cells.append(" ".join(para_text(p) for p in tc.findall(W + "p")))
                line = " | ".join(c for c in cells if c).strip()
                if line:
                    lines.append(line)
    return "\n".join(lines)


def main():
    files = sorted(SRC.rglob("*.docx"))
    total_hits = {}
    done, failed, empty = [], [], []
    for f in files:
        rel = f.relative_to(SRC)
        try:
            text = extract_docx_text(f)
        except Exception as e:  # noqa: BLE001
            failed.append((str(rel), str(e)))
            continue
        if not text.strip():
            empty.append(str(rel))
            continue
        cleaned, hits = desensitize(text)
        for k, v in hits.items():
            total_hits[k] = total_hits.get(k, 0) + v
        # 元数据头的「来源目录 / 原文件名」也可能含真实姓名，一并脱敏
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
        # 输出路径的每一级目录/文件名也脱敏
        rel_clean = Path(*[desensitize(p)[0] for p in rel.parts])
        dest = OUT / rel_clean.with_suffix(".txt")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(header + cleaned, encoding="utf-8")
        done.append(str(rel_clean))

    print("=" * 50)
    print(f"docx 抽文字完成: {len(done)} 个  失败: {len(failed)}  空: {len(empty)}")
    print("脱敏统计:", total_hits)
    if failed:
        print("失败文件:")
        for rel, e in failed:
            print(f"  {rel}  ->  {e}")
    if empty:
        print("无文字文件:", empty)


if __name__ == "__main__":
    main()
