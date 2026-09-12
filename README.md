# 智能问答助手（Smart QA Assistant）

> 基于 LLM-Wiki 的企业知识库问答系统：把散落的工作文档清洗、脱敏、分批导入知识库，通过对话式检索回答业务问题，最终以**飞书机器人**落地到真实聊天场景，并用一套评测集量化问答质量。

## 项目简介

企业内部培训资料、会议纪要、聊天记录等非结构化文档散落在各处，新人学习成本高、知识难以复用。本项目把「文档 → 知识库 → 可问答」的完整链路工程化：

1. 从 docx / xlsx / pdf / txt / md 抽取正文，并用视觉大模型 OCR 界面截图（含 docx 内嵌图），统一**自动脱敏**（人名、公司、客户、订单号、内部合同号、企业主体、银行账号、SWIFT、内网 IP、链接）；
2. 按主题分批构建导入包，写入 LLM-Wiki 知识库（编译成可溯源、互链的结构化 Wiki）；
3. 通过本地 HTTP API 做机器评测，输出命中率 / 幻觉率等量化指标；
4. 以飞书机器人的形式对外提供问答（群内 @机器人，卡片回复 + 来源引用 + 多轮追问）。

## 核心亮点

- **数据脱敏**：一键把人名/公司/客户/内部合同号泛化为化名，保留对话角色结构，避免敏感信息泄漏（`clean_text.py`）。
- **零依赖**：docx / xlsx 抽取、截图 OCR、清洗、巡检、分批、评测脚本只用 Python 标准库；仅 `extract_pdf.py` 需 `pymupdf`、`feishu_bot.py` 需 `lark-oapi`。
- **可复现**：清洗 → 分批 → 重导 → 评测每一步都有独立脚本，参数化、幂等。
- **量化评测**：22 题评测集（概念/对比/流程/操作/报价配比/机制），按「命中 / 答偏 / 幻觉」三档打分，形成可对比的质量报告（`eval/`）。

## 量化结果（实测，均可复算）

| 维度 | 结果 | 口径 / 来源 |
|---|---|---|
| 语料 | **121 份 / 105.2 万字符**（源 173 份 / 153MB） | `corpus_stats.py` |
| 脱敏 | 命中 **4,667 处**，残留 **0** | `corpus_stats.py` / `audit_leaks.py` |
| 知识库 | **119/119** 编译完成，**819 页**结构化 Wiki | `wiki_status.py` |
| 命中率 | **85.7%**（18/21）｜有效 **94.7%**（18/19） | `eval/评测报告_v0.3_复测.md` |
| 幻觉 | **0/21**（95% 置信上界 **13.3%**） | 同上 |
| 拒答 | 语料外 **8/8** 正确拒答、0 编造；语料内对照 **5/5** 无误拒 | `eval/拒答评测报告.md` |
| 引用可溯源 | **90.9%**（20/22） | `eval/评测结果_raw.json` |
| 延迟 | P50 **61.5s**（**未达标**，架构性 trade-off，已如实标注） | 同上 |

> 完整复盘（目标 vs 结果逐条对照 / 归因 / 踩坑 / 方法论沉淀 / 数字来源索引）见 [`docs/复盘报告.md`](docs/复盘报告.md)。
> 质量维度对标企业参考门槛见 [`docs/质量门槛对标.md`](docs/质量门槛对标.md)。

## 整体流程

```text
原始文档(docx/xlsx/pdf/txt/md) + 界面截图(png/jpg)
        │
        ├─ extract_docx.py ─┐
        ├─ extract_xlsx.py ─┤ 抽取正文（零依赖，直接解析 zip+XML）
        ├─ extract_pdf.py  ─┤ 抽取正文（pymupdf）
        ├─ ocr_images.py   ─┤ 截图 OCR（GLM-4V，独立图片）
        ├─ ocr_docx_media.py┤ 截图 OCR（docx 内嵌图）
        └─ clean_text.py   ─┘ 脱敏 + 元数据标注  →  data/clean/
        │
        ▼
  build_batches.py ── 按主题分批      →  data/import_batches/
        │
        ▼
  audit_leaks.py  ── 入库前巡检（脱敏闸门的兜底）
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
| 文档抽取 | `scripts/extract_xlsx.py` | `工作内容/*.xlsx` → `data/clean/` |
| 文档抽取 | `scripts/extract_pdf.py` | `工作内容/*.pdf` → `data/clean/`（需 pymupdf） |
| 截图 OCR | `scripts/ocr_images.py` | 独立截图 → `data/clean/`（需智谱 key） |
| 截图 OCR | `scripts/ocr_docx_media.py` | docx 内嵌截图 → `data/clean/`（需智谱 key） |
| 文本清洗/脱敏 | `scripts/clean_text.py` | `工作内容/*.txt\|md` → `data/clean/` |
| 脱敏巡检 | `scripts/audit_leaks.py` | `data/clean/` + LLM-Wiki 项目 → 残留报告（可用作 CI 卡点） |
| 重复检测 | `scripts/check_duplicates.py` | 8-gram 倒排 + 精确 Jaccard，找完全相同/近似重复（语料或 wiki） |
| 批次构建 | `scripts/build_batches.py` | `data/clean/` → `data/import_batches/` |
| 知识入库 | `scripts/reingest.py` | `data/import_batches/` → LLM-Wiki 项目（清空旧库 → 复制 → 重置状态 → 触发重扫） |
| 续跑导入 | `scripts/resume_ingest.py` | 中断后续跑：摘除未编译条目 + 清队列 → 重扫重建，已编译的不重做（`--plan`/`--apply`/`--rescan`） |
| 分模型 | `scripts/set_ingest_model.py` | 单独指定「入库/编译」模型（编译用轻量、聊天跟随当前预设），`--show`/`--clear` |
| 编译监控 | `scripts/monitor_compile.py` | 读 `ingest-cache`/`ingest-queue` 看进度，编完自动触发 22 题评测（幂等：同一轮只评一次，`--force` 可强制重跑）；`failed` 单独计为「已终结」，避免一份永久失败卡死自动评测 |
| 失败重排 | `scripts/requeue_failed.py` | 队列里 `status=failed`（重试耗尽）的源文件重新排入编译：`--plan` 只读，`--apply` 摘 snapshot 条目 + 删 failed 条目 + 重扫 |
| 状态自检 | `scripts/wiki_status.py` | 一次打印 API health + 编译进度 + 当前入库模型；`--samples N --interval S` 可做有界后台采样 |
| 语料统计 | `scripts/corpus_stats.py` | 只读复算语料规模与脱敏命中数（给报告/简历回填数字） |
| 覆盖度检查 | `scripts/check_coverage.py` | 源素材 vs 实际入库：按元数据「原文件名」反向追溯（脱敏会改名），单独统计图片 OCR / PDF / XLSX |
| 模型选型 | `scripts/bench_models.py` | 同一语料对比各模型耗时/吞吐（解释编译慢的根因） |
| 效果评测 | `scripts/eval_chat.py` | 22 题 → `eval/评测结果_raw.json` |
| 拒答评测 | `scripts/eval_refusal.py` | 语料外问题（应拒答）+ 语料内冷门模块对照（应回答），量化**拒答率 / 误拒率 / 拒答幻觉率** |
| 拒答题库校验 | `scripts/check_refusal_bank.py` | 验证「语料外」题的关键词在 119 份语料中确实命中为 0（拒答测试成立的前提） |
| 飞书机器人 | `scripts/feishu_bot.py` | 飞书消息 → LLM-Wiki 问答 → 卡片回复（多轮会话 / 进度更新 / 超时重试 / **用量埋点**；`--selftest`、`--ask` 可离线验证） |
| 用量统计 | `scripts/usage_stats.py` | 读 `data/usage_log.jsonl`：提问数 / 去重提问数 / **唯一会话数（人数近似）** / 延迟分位 / 拒答率 / 多轮占比 |

## 目录结构

```text
智能问答助手/
├── scripts/            # 核心代码（数据处理与评测）
│   ├── config.py       # 项目路径 + 密钥加载（密钥不入库）
│   ├── extract_docx.py # docx 抽文字 + 脱敏（零依赖）
│   ├── extract_xlsx.py # xlsx 抽文字 + 脱敏（零依赖）
│   ├── extract_pdf.py  # pdf 抽文字 + 脱敏（需 pymupdf）
│   ├── ocr_images.py   # 独立截图 OCR（需智谱 GLM-4V）
│   ├── ocr_docx_media.py# docx 内嵌截图 OCR
│   ├── clean_text.py   # 脱敏 + 元数据标注
│   ├── audit_leaks.py  # 脱敏巡检（入库前兜底，可作 CI 卡点）
│   ├── corpus_stats.py # 语料/脱敏统计（只读复算）
│   ├── check_coverage.py # 覆盖度检查（源素材 vs 入库，追溯原文件名）
│   ├── build_batches.py# 按主题分批
│   ├── reingest.py     # 重导 LLM-Wiki
│   ├── resume_ingest.py # 中断后续跑（file-snapshot 手术法，不重做已编译）
│   ├── set_ingest_model.py # 分模型：入库模型独立设置
│   ├── monitor_compile.py # 编译进度监控（编完自动评测）
│   ├── requeue_failed.py  # 失败文件重排入队（file-snapshot 手术法）
│   ├── wiki_status.py  # 状态自检（health + 进度 + 入库模型）
│   ├── bench_models.py # 模型耗时对比
│   ├── eval_chat.py    # 机器评测（22 题）
│   ├── eval_refusal.py # 拒答评测（语料外 vs 语料内对照）
│   ├── usage_stats.py  # 用量统计（读机器人埋点，出真实用量）
│   └── feishu_bot.py   # 飞书机器人（需 pip install lark-oapi）
├── docs/               # 需求、计划、运行手册、复盘报告等文档
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

# 1) 抽取 docx / xlsx 正文并脱敏（零依赖）
python extract_docx.py
python extract_xlsx.py

# 2) 抽取 pdf 正文（需 pymupdf；默认跳过客户原始订单，加 --include-orders 可强制收录）
python extract_pdf.py

# 3) 截图 OCR（需智谱 key；独立截图 + docx 内嵌图，结果带缓存可断点续跑）
python ocr_images.py
python ocr_docx_media.py

# 4) 清洗 txt/md 并脱敏
python clean_text.py

# 5) 脱敏巡检（发现残留会以退出码 1 失败，可直接挂 CI）
python audit_leaks.py

# 6) 按主题分批
python build_batches.py

# 7) 重导 LLM-Wiki（备份旧库 → 清空 → 复制 → 触发重扫）
python reingest.py

# 8) 跑 22 题评测
python eval_chat.py

# 9) （可选）复算语料规模与脱敏命中数
python corpus_stats.py
```

### 3. 飞书机器人（可选）

机器人只依赖本地 API，**不需要额外的大模型 Key**（模型由 LLM-Wiki 统一配置）：

```bash
pip install "lark-oapi>=1.7.0"

# 先自检：凭据是否齐全 / 本地 API 是否在线 / 项目能否解析
python scripts/feishu_bot.py --selftest

# 不开飞书，直接验证一条问答链路
python scripts/feishu_bot.py --ask "什么是短溢装"

# 启动机器人（长连接，阻塞；群内 @机器人 提问）
python scripts/feishu_bot.py

# 统计真实用量（机器人跑起来、有人提问后即可出数）
python scripts/usage_stats.py           # 飞书群聊用量
python scripts/usage_stats.py --md      # 额外输出 Markdown 表
```

> 需在 `scripts/.env` 里追加 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`；飞书后台配置见 `docs/机器人接入调研.md`。
> 支持多轮追问（同一会话 1 小时）、`/reset` 开新对话；调参用 `BOT_WIKI_TIMEOUT` / `BOT_WIKI_RETRIES` / `BOT_SESSION_TTL` / `BOT_CARD_MAX_CHARS`。
>
> **用量埋点**：每次问答会向 `data/usage_log.jsonl`（已 gitignore）追加一行元数据——
> 耗时 / 引用来源数 / 拒答标记 / 会话与问题的**单向哈希**。
> **只记元数据、不记问题原文**（业务提问可能含敏感信息，与脱敏口径一致），
> 因此可统计「提问数 / 人数近似 / 延迟分布」，但无法还原提问内容。

## 数据脱敏

`clean_text.py` 内置一套映射表，把真实信息泛化为化名：

- **人名**：真实姓名 → `导师A~E` / `学员A~I`（保留「导师/学员」角色结构）
- **公司**：真实公司名 → `某外贸公司`（含内地/海外工厂）
- **客户**：客户名 → `客户A` / `客户B`
- **其他**：客户订单号 → `[订单号]`、URL → `[链接]`、内网 IP → `[内网地址]`

除词表替换外，还有一组**结构化规则**（不依赖词表，换公司/换客户无需改代码）：

| 规则 | 匹配形态 | 替换为 |
|---|---|---|
| 内部合同号 | `[A-Z]{2}\d[A-Z]{3}\d{2}-\d{4}` | `[内部合同号]` |
| 英文企业主体 | `词 + GmbH/Ltd./LLC/Inc./KGaA/B.V.` | `[企业主体]` |
| 客户订单号 | `客户名 + 6 位以上数字` | `[订单号]` |
| 银行账号 | `\d{3,4}-\d{6}-\d{2,5}` | `[银行账号]` |
| SWIFT/BIC | `SWIFT Code：` + 8/11 位编码 | `[银行代码]` |
| 内网 IP / 链接 | `192.168.x.x` / `http(s)://…` | `[内网地址]` / `[链接]` |

两条工程经验（踩过坑）：

- **英文词条忽略大小写 + 加词边界**：OCR 输出大小写不可控（截图里是全大写抬头，文档里是首字母大写形式），大小写敏感会漏；而英文词不加边界又会误伤（如 `ABC` 命中 `ABCDEF`）。
- **能锚定上下文就别用裸正则**：BIC 码字面格式（8/11 位大写串）会误伤 `SHIPPING`、`STANDARD`、`MATERIAL`、`PRODUCTS` 等正常词，所以改成必须跟在 `SWIFT Code：` 之后。

真实映射表存于本地 `scripts/maps_local.py`（已被 `.gitignore` 忽略、不入库），公开仓库不含任何真实敏感词。替换按**关键词长度降序**执行，避免简称先命中全名。脱敏统计报告会输出到控制台。

### 双重防护

脱敏靠规则吃饭，规则会漏。所以除了入库前的闸门，还有 `scripts/audit_leaks.py` 做**入库后巡检**：用真实词表 + 结构化正则反查 `data/clean/` 与 LLM-Wiki 项目目录（`raw/` 来源副本 + `wiki/` 编译页），发现残留即以退出码 1 失败，可直接挂 CI 卡点。

> 为什么必须在入库前脱敏：一旦编译完成，敏感词会扩散到 wiki 的 entities / concepts / sources 多类页面，清理要连带重建向量索引，成本远高于改一处规则。

## 评测方法

评测集共 **22 题**，覆盖概念（C）、对比（D）、流程（P）、操作（O）、报价配比（Q）、机制（M）六类，每题带标准答案要点与来源。`eval_chat.py` 逐题调用 LLM-Wiki 的 chat API，提取最终答案 + 来源引用，并按三档归类：

- **命中**：答案与标准要点一致
- **答偏**：方向对但关键信息缺失/错误
- **幻觉**：给出语料中不存在的信息

> P2（核料纸样 vs 制版口径）语料自相矛盾，标注为**不计分项**，待向业务确认后回填。

### 拒答准确性专项（13 题）

22 题主评测集的「幻觉 0%」有**结构性盲区**——那 21 道题的答案语料里全都有，模型只要正常检索就能答对，
**测不出「没有依据时会不会硬编」**。而企业场景最怕的恰恰是「答错还自信」。因此另设一组对照测试（`eval_refusal.py`）：

| 组别 | 题数 | 正确行为 | 实测 |
|---|---|---|---|
| **X 类 · 语料外**（含 2 道「相邻概念诱导」题） | 8 | 拒答 | **8/8 正确拒答、0 编造** |
| **N 类 · 语料内冷门对照**（主评测集未问过的模块） | 5 | 作答 | **5/5 正常作答、0 误拒** |

> 语料外问题均经 `check_refusal_bank.py` 验证：关键词在 119 份语料中命中为 **0**。
> **关键发现**：模型是**内容驱动而非检索驱动**——8 道语料外题里 7 道检索都返回了 5~15 条内容，
> 模型仍正确拒答，避开了 RAG 最常见的失败模式「检索恒有返回 → 强行凑答案」。

详细结论见 `eval/评测报告.md` 与 `eval/评测集.md`，拒答专项见 `eval/拒答评测报告.md`；
质量门槛与现状对标见 `docs/质量门槛对标.md`。

> 评测口径提醒：单一命中率不足以判断系统水平（受语料覆盖度影响极大）。企业场景下**忠实度（零幻觉）与引用可溯源**才是上线底线，详见 `docs/质量门槛对标.md`。

## 技术栈

- Python（标准库优先；PDF 用 `pymupdf`、飞书机器人用 `lark-oapi`）
- 智谱 GLM（国产大模型，OpenAI 兼容接口；**分模型**：编译/入库用 `glm-4.5-air` 求快，问答用 `glm-4.7` 求质量，截图 OCR 用 `glm-4v-flash`）
- LLM-Wiki（本地知识库引擎，编译式 Wiki + 向量检索 + 本地 HTTP API）
- 飞书开放平台（企业自建应用 + WebSocket 长连接）

## License

[MIT](./LICENSE)
