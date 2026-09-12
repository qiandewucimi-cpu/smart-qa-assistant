# -*- coding: utf-8 -*-
"""拒答测试题库验证：确认「语料外」问题在语料里确实搜不到依据。

设计拒答测试的前提是「语料外问题必须真的语料外」——否则测出来的不是拒答能力，
而是检索能力。本脚本对每个候选关键词在 119 份语料里做全文检索，给出命中数，
命中为 0（或只命中无关上下文）才可用作语料外题目。
"""
import os
import re

P = r"C:\Users\31114\WorkBuddy\智能问答助手\projects\training-qa\training-qa\raw\sources"

# 候选：语料外问题涉及的关键词
CANDIDATES = {
    "X1 食堂/后勤": ["食堂", "开饭", "餐厅", "后勤"],
    "X2 Python/爬虫": ["爬虫", "Python", "脚本语言"],
    "X3 机房/部署/服务器": ["机房", "部署", "服务器", "运维"],
    "X4 密码/重置/账号找回": ["密码", "重置", "找回密码", "登录失败"],
    "X5 三级复核": ["三级复核", "三级审核", "复核机制"],
    "X6 翻单第五种/改颜色": ["改颜色", "改色"],
    "X7 对外API/接口文档": ["API", "接口文档", "开放接口", "接口说明"],
    "X8 2027/未来规划": ["2027", "未来规划", "路线图", "roadmap"],
    # 对照：语料内模块（应能搜到）
    "N1 仓储管理": ["仓储管理", "仓库"],
    "N2 款式档案": ["款式档案"],
    "N3 物料出运/成品出运": ["物料出运", "成品出运"],
    "N4 大货技术资料包": ["大货技术资料包"],
    "N5 采购管理": ["采购管理"],
}

texts = {}
for f in os.listdir(P):
    texts[f] = open(os.path.join(P, f), encoding="utf-8", errors="ignore").read()

print("=" * 70)
print("拒答测试题库验证：关键词在 119 份语料中的命中情况")
print("=" * 70)
for label, words in CANDIDATES.items():
    print(f"\n【{label}】")
    for w in words:
        n_file = 0
        n_hit = 0
        samples = []
        for f, t in texts.items():
            c = t.count(w)
            if c:
                n_file += 1
                n_hit += c
                if len(samples) < 2:
                    i = t.find(w)
                    samples.append(f"{f[:28]}… «{t[max(0,i-25):i+25].strip()}»")
        flag = "✅ 语料外" if n_hit == 0 else f"⚠️ 命中 {n_hit}"
        print(f"  {w:<12} {flag:<14} 文件 {n_file}")
        for s in samples:
            print(f"      {s}")
