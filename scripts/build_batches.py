# -*- coding: utf-8 -*-
"""从 data/clean 重新生成 data/import_batches 的 5 个批次目录。

清洗脚本（clean_text.py / extract_docx.py）更新后，重跑本脚本即可同步批次数据。
用法：python build_batches.py
"""
import shutil
from config import BASE
CLEAN = BASE / "data" / "clean"
BATCHES = BASE / "data" / "import_batches"

# (批次目录名, 源相对路径列表, 是否平铺)
# flat=True   ：把源目录下的文件平铺到批次根目录（适合单一主题目录）
# flat=False  ：保留源子目录结构（适合多主题合并的批次）
BATCH_DEF = [
    ("batch1_核心_会议纪要智能纪要", ["会议纪要/智能纪要"], True),
    ("batch2_会议纪要逐字稿", ["会议纪要/逐字稿（部分）"], True),
    ("batch3_单据重点与订单理单", ["单据重点", "订单培训文档"], False),
    ("batch4_DLS系统操作手册", ["培训文档"], True),
    ("batch5_辅助_日报聊天记录AI建议", ["每日日报", "聊天记录", "提出企业ai化建议"], False),
]
# batch5 额外的根目录单文件
BATCH5_EXTRA = ["提炼工作内容的行动路线.txt"]


def build():
    if BATCHES.exists():
        shutil.rmtree(BATCHES)
    BATCHES.mkdir(parents=True, exist_ok=True)

    for name, srcs, flat in BATCH_DEF:
        dst = BATCHES / name
        dst.mkdir(parents=True, exist_ok=True)
        for s in srcs:
            src = CLEAN / s
            if not src.exists():
                print(f"⚠️  源目录不存在: {s}")
                continue
            if flat:
                for f in sorted(src.iterdir()):
                    if f.is_file():
                        shutil.copy2(f, dst / f.name)
            else:
                shutil.copytree(src, dst / src.name, dirs_exist_ok=True)

    # batch5 额外单文件
    dst5 = BATCHES / "batch5_辅助_日报聊天记录AI建议"
    for fn in BATCH5_EXTRA:
        f = CLEAN / fn
        if f.exists():
            shutil.copy2(f, dst5 / fn)

    # 统计
    total = 0
    for name, _, _ in BATCH_DEF:
        n = sum(1 for p in (BATCHES / name).rglob("*") if p.is_file())
        total += n
        print(f"{name}: {n} 文件")
    print(f"合计: {total} 文件")


if __name__ == "__main__":
    build()
