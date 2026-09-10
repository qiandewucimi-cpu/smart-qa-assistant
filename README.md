# 智能问答助手（Smart QA Assistant）

> 基于 LLM-Wiki 的企业知识库问答系统：把散落的工作文档清洗、脱敏、分批导入知识库，再通过对话式检索回答业务问题，并用一套评测集量化问答质量。

## 项目简介

企业内部培训资料、会议纪要、聊天记录等非结构化文档散落在各处，新人学习成本高、知识难以复用。本项目把「文档 → 知识库 → 可问答」的完整链路工程化：

1. 从 docx/txt/md 抽取正文并**自动脱敏**（人名、公司、客户、订单号、内网 IP、链接）；
2. 按主题分批构建导入包，写入 LLM-Wiki 知识库；
3. 通过本地 HTTP API 做机器评测，输出命中率 / 幻觉率等量化指标。

## 核心亮点

- **数据脱敏**：一键把人名/公司/客户泛化为化名，保留对话角色结构，避免敏感信息泄漏（`clean_text.py`）。
- **零依赖**：全部脚本只用 Python 标准库，无需 `pip install`。
- **可复现**：清洗 → 分批 → 重导 → 评测每一步都有独立脚本，参数化、幂等。
- **量化评测**：15 题评测集，按「命中 / 答偏 / 幻觉」三档打分，形成可对比的质量报告（`eval/`）。

## 整体流程

```text
原始文档(docx/txt/md)
        │
        ▼
  extract_docx.py ── 抽取 docx 正文
        │
        ▼
  clean_text.py  ── 脱敏 + 元数据标注  →  data/clean/
        │
        ▼
  build_batches.py ── 按主题分批      →  data/import_batches/
        │
        ▼
  reingest.py  ── 清空旧库 → 复制 → 触发重扫（写入 LLM-Wiki）
        │
        ▼
  eval_chat.py  ── 调用本地 API 逐题评测 →  eval/评测结果_raw.json
```

| 阶段 | 脚本 | 输入 → 输出 |
|------|------|-------------|
| 文档抽取 | `scripts/extract_docx.py` | `工作内容/*.docx` → `data/clean/` |
| 文本清洗/脱敏 | `scripts/clean_text.py` | `工作内容/*.txt|md` → `data/clean/` |
| 批次构建 | `scripts/build_batches.py` | `data/clean/` → `data/import_batches/` |
| 知识入库 | `scripts/reingest.py` | `data/import_batches/` → LLM-Wiki 项目 |
| 效果评测 | `scripts/eval_chat.py` | 15 题 → `eval/评测结果_raw.json` |

## 目录结构

```text
智能问答助手/
├── scripts/            # 核心代码（数据处理与评测，零依赖）
│   ├── config.py       # 项目路径 + Token 加载（Token 不入库）
│   ├── extract_docx.py # docx 抽文字 + 脱敏
│   ├── clean_text.py   # 脱敏 + 元数据标注
│   ├── build_batches.py# 按主题分批
│   ├── reingest.py     # 重导 LLM-Wiki
│   └── eval_chat.py    # 机器评测
├── docs/               # 需求、计划、运行手册等文档
├── eval/               # 评测集、评测报告
└── README.md
```

> 知识库原始数据与工作资料属于敏感内容，已通过 `.gitignore` 排除，不纳入版本库。

## 快速开始

### 环境要求

- Python 3.10+
- 本地运行中的 LLM-Wiki（默认 API `http://127.0.0.1:19828`，需在设置里开启「本地 HTTP API」）

### 1. 配置 API Token

在 `scripts/.env` 里填入 LLM-Wiki 的 API Token（该文件已被 `.gitignore` 忽略，不会入库）：

```bash
# scripts/.env
LLM_WIKI_API_TOKEN=你的token
```

也可用环境变量代替：`export LLM_WIKI_API_TOKEN=你的token`。

### 2. 运行流程

```bash
cd scripts

# 1) 抽取 docx 正文并脱敏
python extract_docx.py

# 2) 清洗 txt/md 并脱敏
python clean_text.py

# 3) 按主题分批
python build_batches.py

# 4) 重导 LLM-Wiki（备份旧库 → 清空 → 复制 → 触发重扫）
python reingest.py

# 5) 跑 15 题评测
python eval_chat.py
```

## 数据脱敏

`clean_text.py` 内置一套映射表，把真实信息泛化为化名：

- **人名**：真实姓名 → `导师A~E` / `学员A~I`（保留「导师/学员」角色结构）
- **公司**：真实公司名 → `某外贸公司`（含内地/海外工厂）
- **客户**：客户名 → `客户A` / `客户B`
- **其他**：客户订单号 → `[订单号]`、URL → `[链接]`、内网 IP → `[内网地址]`

真实映射表存于本地 `scripts/maps_local.py`（已被 `.gitignore` 忽略、不入库），公开仓库不含任何真实敏感词。替换按**关键词长度降序**执行，避免简称先命中全名。脱敏统计报告会输出到控制台。

## 评测方法

评测集共 15 题，覆盖概念（C）、对比（D）、流程（P）、操作（O）四类，每题带标准答案要点与来源。`eval_chat.py` 逐题调用 LLM-Wiki 的 chat API，提取最终答案 + 来源引用，并按三档归类：

- **命中**：答案与标准要点一致
- **答偏**：方向对但关键信息缺失/错误
- **幻觉**：给出语料中不存在的信息

详细结论见 `eval/评测报告.md`。

## 技术栈

- Python（标准库）
- LLM-Wiki（本地知识库引擎，`glm-4.6` 模型）

## License

[MIT](./LICENSE)
