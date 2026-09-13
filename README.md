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
- **混合检索**：关键词 + 知识图谱两路融合跑在生产上；向量一路（`embedding-3`，764/817 页索引已建）
  **已配置但因 embedding 额度耗尽未生效**（`/search` 的 `vectorHits` 恒为 0），充值时一键恢复——详见下方「向量检索的真实状况」。
- **可复现**：清洗 → 分批 → 重导 → （可选）建向量索引 → 评测每一步都有独立脚本，参数化、幂等、可续跑。
- **量化评测**：22 题评测集（概念/对比/流程/操作/报价配比/机制）+ 13 题拒答专项 + 延迟对照基准，
  按「命中 / 答偏 / 幻觉」三档打分，形成可对比的质量报告（`eval/`）。

## 量化结果（实测，均可复算）

| 维度 | 结果 | 口径 / 来源 |
|---|---|---|
| 语料 | **121 份 / 105.2 万字符**（源 173 份 / 153MB） | `corpus_stats.py` |
| 脱敏 | 命中 **4,667 处**；输入侧（`data/clean`）残留 **0**；评测产物残留 **0** | `corpus_stats.py` / `audit_leaks.py` / `sanitize_eval_results.py` |
| 知识库 | **119/119** 编译完成，**819 页**结构化 Wiki | `wiki_status.py` |
| 向量索引 | 索引 **764/817 页（93.5%）已建**，但**当前未生效**（embedding 额度耗尽） | `vector_index.py status` / `vector_index.py probe` |
| 命中率 | **18~19 / 21（85.7%~90.5%）**——单轮最好 90.5%（19/21，95% Wilson 71.1~97.3%），但**同配置再跑两轮均为 18/21（85.7%，95% CI 65.4~95.0%）**，故报区间不报单点 | `eval/评测报告_v1.1_知识固化与稳定性.md` §5 |
| 幻觉 | **0/21**（95% 置信上界 **13.3%**）※ 口径限定见该报告 §4.1 | 同上 |
| 拒答 | 语料外 **8/8** 正确拒答、0 编造；语料内对照 **5/5** 无误拒 | `eval/拒答评测报告.md` |
| 引用可溯源 | **100%**（22/22） | `eval/评测结果_raw.json` |
| 延迟 | P50 **18.0s** / 均值 22.1s（**未达标**，但较上轮 61.5s **↓3.4×**） | 同上 |

> 完整复盘（目标 vs 结果逐条对照 / 归因 / 踩坑 / 方法论沉淀 / 数字来源索引）见 [`docs/复盘报告.md`](docs/复盘报告.md)。
> 质量维度对标企业参考门槛见 [`docs/质量门槛对标.md`](docs/质量门槛对标.md)。
> 五轮评测演进：73.3% → 81.0% → 85.7% → **90.5%** → **85.7%**（v1.1 两轮均 18/21）；幻觉**始终为 0/21**（95% 置信上界 13.3%，口径限定见报告）。
>
> ⚠️ **关于命中率口径（v1.1 修正，务必先读）**：最后那个 90.5% 是**单轮最好成绩，不可复现**——
> v1.1 用同一套配置连跑两轮都是 18/21，且**两轮未命中的题目几乎不重合**（r1 = D1/D2/P1，r2 = D1/P3/O2）。
> 方差来自**应用框架的失败**（8 步工具迭代上限 + 「原文直吐」/「JSON 直吐」）：D1 实测作答 **7/15 ≈ 47%**
> （⚠️ **不是"退化"**——此前只测过它 1 次，没有基线；且"换模型导致"这个归因已因**无对照**被撤回）。
> **v1.3 已把这层方差定位清楚**：失败集中在 D1/D2/P3/P1 四道「需多次检索」的题，其余 18 题 90 次采样零失败。
> **本项目对外一律报「18~19/21（±1~2 题），随框架失败波动」，并附复跑次数与每题采样次数，不报单一数字。**
> 详见 [`eval/评测报告_v1.1_知识固化与稳定性.md`](eval/评测报告_v1.1_知识固化与稳定性.md) §5–§7、
> [`eval/评测报告_v1.3_扩展集与框架失败成因.md`](eval/评测报告_v1.3_扩展集与框架失败成因.md)。

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
  vector_index.py ──（可选）开启向量检索 + 批量建索引（764/817 页；当前因额度未生效）
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
| **评测产物擦除** | `scripts/sanitize_eval_results.py` | 把「编译期派生的真实专名」从 `eval/` 产物中擦掉（`--check` 可挂 CI）；**检索面一变宽就必需**，见下 |
| 重复检测 | `scripts/check_duplicates.py` | 8-gram 倒排 + 精确 Jaccard，找完全相同/近似重复（语料或 wiki） |
| 批次构建 | `scripts/build_batches.py` | `data/clean/` → `data/import_batches/` |
| 知识入库 | `scripts/reingest.py` | `data/import_batches/` → LLM-Wiki 项目（清空旧库 → 复制 → 重置状态 → 触发重扫） |
| 续跑导入 | `scripts/resume_ingest.py` | 中断后续跑：摘除未编译条目 + 清队列 → 重扫重建，已编译的不重做（`--plan`/`--apply`/`--rescan`） |
| 分模型 | `scripts/set_ingest_model.py` | 单独指定「入库/编译」模型（编译用轻量、聊天跟随当前预设），`--show`/`--clear` |
| **知识固化** | `scripts/apply_faq_patch.py` | 把 `wiki-patches/queries/` 里人工沉淀的 wiki 页幂等注入 `wiki/queries/`；`--check` 可挂 CI。用于修「用户说法与语料说法对不上」导致的长尾答偏（见 P1 案例） |
| **向量索引** | `scripts/vector_index.py` | 开启/关闭 `embeddingConfig` + 批量给 wiki 页建向量索引（`probe`/`status`/`enable`/`disable`/`embed-one`/`embed-all`/`restore`，可续跑）；**`probe` 是必跑自检——`enabled=true` 不等于生效** |
| 编译监控 | `scripts/monitor_compile.py` | 读 `ingest-cache`/`ingest-queue` 看进度，编完自动触发 22 题评测（幂等：同一轮只评一次，`--force` 可强制重跑）；`failed` 单独计为「已终结」，避免一份永久失败卡死自动评测 |
| 失败重排 | `scripts/requeue_failed.py` | 队列里 `status=failed`（重试耗尽）的源文件重新排入编译：`--plan` 只读，`--apply` 摘 snapshot 条目 + 删 failed 条目 + 重扫 |
| 状态自检 | `scripts/wiki_status.py` | 一次打印 API health + 编译进度 + 当前入库模型；`--samples N --interval S` 可做有界后台采样 |
| 语料统计 | `scripts/corpus_stats.py` | 只读复算语料规模与脱敏命中数（给报告/简历回填数字） |
| 覆盖度检查 | `scripts/check_coverage.py` | 源素材 vs 实际入库：按元数据「原文件名」反向追溯（脱敏会改名），单独统计图片 OCR / PDF / XLSX |
| 模型选型 | `scripts/bench_models.py` | 同一语料对比各模型耗时/吞吐（解释编译慢的根因） |
| **延迟基准** | `scripts/bench_chat_latency.py` | 端到端对照问答模型（glm-4.7 / glm-4.5-air / glm-4.7 关思考）：P50·覆盖度·拒答，写 `eval/延迟基准_<ts>.json`；`--restore` 还原配置 |
| 效果评测 | `scripts/eval_chat.py` | 22 题 → `eval/评测结果_raw.json`；`--only`/`--repeat`/`--topk`/`--tag`/`--set` 支持复测、稳定性检查与外部题目集；新增 `framework_failure()` 识别 **limit / 原文直吐 / JSON 直吐** 三种框架失败，`chat_resilient()` 可带 `sessionId` 续跑（`--no-continue` 作对照臂） |
| **扩展评测集** | `scripts/gen_eval_set.py` | 从 wiki 页生成 45 题稳定性扩展集（固定种子可复现）+ **标题脱敏护栏**（过滤「连续 ≥5 位数字」的页名） |
| **稳定性分析** | `scripts/judge_stability.py` | 每题命中率 + Wilson 区间 + **失败是否集中在少数题** + 多轮不一致率；`api_error` 单独计数且**排除出分母** |
| **判定规则** | `scripts/eval_common.py` | 框架失败判定 + Wilson 区间，**全项目唯一一份**（此前 `eval_chat.py` 与 `ab_analysis.py` 各写一份，有漂移风险） |
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
│   ├── sanitize_eval_results.py # 评测产物擦除（输出侧脱敏闸门，提交前必跑）
│   ├── corpus_stats.py # 语料/脱敏统计（只读复算）
│   ├── check_coverage.py # 覆盖度检查（源素材 vs 入库，追溯原文件名）
│   ├── build_batches.py# 按主题分批
│   ├── reingest.py     # 重导 LLM-Wiki
│   ├── resume_ingest.py # 中断后续跑（file-snapshot 手术法，不重做已编译）
│   ├── set_ingest_model.py # 分模型：入库模型独立设置
│   ├── monitor_compile.py # 编译进度监控（编完自动评测）
│   ├── requeue_failed.py  # 失败文件重排入队（file-snapshot 手术法）
│   ├── wiki_status.py  # 状态自检（health + 进度 + 入库模型）
│   ├── vector_index.py # 向量索引：开关 embedding 配置 + 批量建索引 + probe 自检（可续跑）
│   ├── apply_faq_patch.py # 知识固化：wiki-patches/queries → wiki/queries 幂等注入（+ --check）
│   ├── sanitize_eval_results.py # 评测产物擦除（第三道脱敏闸门，--check 可挂 CI）
│   ├── bench_models.py # 模型耗时对比（编译层）
│   ├── bench_chat_latency.py # 端到端问答延迟对照基准（glm-4.7 / glm-4.5-air）
│   ├── eval_chat.py    # 机器评测（22 题，支持 --only/--repeat/--topk/--tag）
│   ├── eval_refusal.py # 拒答评测（语料外 vs 语料内对照）
│   ├── usage_stats.py  # 用量统计（读机器人埋点，出真实用量）
│   └── feishu_bot.py   # 飞书机器人（需 pip install lark-oapi）
├── wiki-patches/       # 人工沉淀的 wiki 页原文（知识固化；由 apply_faq_patch.py 注入知识库）
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

# 7.5) （可选）向量检索：先 probe 自检，再 enable / 建索引
python vector_index.py probe        # ★ 必跑：接口通不通 + 检索真的用上向量没
python vector_index.py enable       # 写配置（model=embedding-3，复用 llmConfig.apiKey）
python vector_index.py embed-all    # 批量建索引（可续跑）

# 7.6) （可选）知识固化：把人工沉淀的术语对照页注入知识库
#       用于修「用户说法与语料说法对不上」导致的长尾答偏，详见 eval/评测报告_v1.1 §3
python apply_faq_patch.py            # wiki-patches/queries/*.md → wiki/queries/（幂等）
python apply_faq_patch.py --check    # 缺失/不一致则退出码 1（可挂 CI）

# 8) 跑 22 题评测（默认 topK=15；--only/--repeat/--tag 支持复测）
python eval_chat.py

# 8.5) （可选）端到端延迟对照基准：glm-4.7 / glm-4.5-air
python bench_chat_latency.py

# 9) （可选）复算语料规模与脱敏命中数
python corpus_stats.py

# 10) 提交前：擦除评测产物中「编译期派生」的真实专名（输出侧脱敏闸门）
python sanitize_eval_results.py --check        # 有残留则退出码 1
python sanitize_eval_results.py --apply       # 就地擦除
```

> ⚠️ **向量检索的真实状况（务必先读）**：本项目**配置了**向量检索、也**建好了** 764/817 页索引，
> 但查询期的 embedding 调用因**账户额度耗尽**（智谱 `code 1113 余额不足或无可用资源包`）而失败，
> 应用**静默降级** → `/search` 的 `vectorHits` **恒为 0**，评测里**一个向量召回事件都没有**。
> 也就是说 **v1.0 的 90.5% 完全是「关键词 + 图谱」两路跑出来的**。
> **教训：`enabled=true` 不等于生效**——`vector_index.py probe` 就是为此加的自检；
> 充值后 `enable` 一步即可恢复（索引无需重建）。

> **关于 topK**：本项目 chat 端默认只回 5 条候选，**提高检索深度（→15）**修好了两道零召回题
> （C3/O4），但同时**把 P1 从"答偏"稀释成"拒答"**——召回更多 ≠ 更好。
> `eval_chat.py` 与 `feishu_bot.py` 统一 `topK=15`，可用 `--topk N` 覆盖。
> 详见 `docs/复盘报告.md` §4.7 与 §5。

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

> **⚠️ 巡检结论里"零残留"要看清范围**：`audit_leaks.py` 有两个目标，结论**不同**——
> `--only clean`（输入侧，121 份）**命中 0**；`--only wiki`（编译产物）**命中 58 处 / 11 文件**。
> 后者是 LLM 编译期的**派生与推断**：真实订单号，以及凭银行地址里的**城市线索**、用世界知识
> **推断**出的"所在国家：某南亚国家"（输入里只有 `[地址]`）。**这类残留任何词表都拦不住**——
> 词表只能挡输入里存在的词。
> 好在 `projects/` 已被 `.gitignore` 隔离，**公开仓库不受影响**；但取用 wiki 内容写对外文档时需人工过一遍。

### 第三道闸门：评测产物擦除（检索面变宽后必需）

`audit_leaks.py` 扫的是**输入侧**（语料与 wiki）。但**输出侧**也会带真实信息：
LLM 编译时会从**截图 OCR** 里派生出**新的真实实体页**（真实订单号、配送中心、
第三方物流商名），这些页面平时只躺在 gitignored 的 wiki 目录里；
而**检索面一变宽**（提高检索深度、或 agent 多跑几轮），chat API 的 `references`
就会把它们也带回给评测脚本，于是写进了**入库的** `eval/评测结果_raw*.json`（实测 2026-09-12 复现）。

> 注意：这次**不是**「开向量」导致的——向量当时根本还没生效，
> 而是**检索面变宽**（引用数由 5~10 增至 20+）把派生页带了进来。
> **结论：任何改变"会召回哪些页"的改动，都要重跑输出侧闸门。**

因此加了第三道闸门：

```bash
python scripts/sanitize_eval_results.py --check   # 提交前检查（退出码 1 = 有残留，可挂 CI）
python scripts/sanitize_eval_results.py --apply   # 就地擦除
```

> 擦除词表放在 gitignored 的 `scripts/maps_local.py`（`EVAL_SCRUB`），公开仓库不留明文。
> **经验：改了检索策略，要重新评估"什么信息会出现在输出里"——脱敏闸门得跟着数据流向扩。**

## 评测方法

评测集共 **22 题**，覆盖概念（C）、对比（D）、流程（P）、操作（O）、报价配比（Q）、机制（M）六类，每题带标准答案要点与来源。`eval_chat.py` 逐题调用 LLM-Wiki 的 chat API，提取最终答案 + 来源引用，并按三档归类：

- **命中**：答案与标准要点一致
- **答偏**：方向对但关键信息缺失/错误
- **幻觉**：给出语料中不存在的信息

> P2（核料纸样 vs 制版口径）语料自相矛盾，标注为**不计分项**，待向业务确认后回填。

### 五轮评测演进（口径可比）

| 轮次 | 语料覆盖 | 检索 / 模型 | 命中率 | 幻觉率 | 关键动作 |
|---|---|---|---|---|---|
| 基线 | 12 份（19%） | 关键词+图谱 / glm-4.6 | 73.3% | 0% | 首版评测 |
| v0.2 | **119 份（100%）** | 关键词+图谱 / glm-4.7 | 81.0% | 0% | 语料扩容 10× |
| v0.3 | 119 份 | 关键词+图谱 / glm-4.7 | 85.7% | 0% | **修评测链路缺陷**（502 重试 + 项目 ID 解析） |
| **v1.0** | 119 份 | 关键词+图谱（**向量未生效**）/ glm-4.5-air | **90.5%** | **0%** | **提高检索深度 topK 5→15 + 换快模型** |
| **v1.1** | 119 份 + 1 页沉淀 | 同上 | **85.7%**（两轮均 18/21） | 0% | **P1 知识固化 + 稳定性采样** |

> v1.1 的两轮均未命中**非语料缺失**：r1 = D1/D2/P1，r2 = D1/P3/O2——
> **两轮数字一样（18/21），但未命中的题几乎不重合**。
> 方差来自**框架随机失败**（8 步迭代上限 + 新模式「原文直吐」），不是知识缺口。
>
> ⚠️ **因此主口径不再是「单轮 90.5%」**，而是「**18~19/21（±1~2 题），随框架随机失败波动**」，
> 并附复跑次数。v1.0 那句「不按复跑的 95.2% 报」仍成立；
> v1.1 更进一步：**也不按单轮 90.5% 报**，因为它同样不可复现。
> P1 已在 v1.1 修复（术语对照沉淀，单变量 A/B **0/5 → 4/5**，Fisher 单侧 p = 0.024）。
>
> ⚠️ v1.0 一栏**不写「混合检索」**：向量虽已配置、索引也已建，但查询期 embedding 额度耗尽，
> 实际仍走「关键词 + 图谱」两路（见上方「向量检索的真实状况」）。

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

详细结论见 `eval/评测报告_v1.0_检索深度与快模型.md`、`eval/评测集.md`，拒答专项见 `eval/拒答评测报告.md`；
质量门槛与现状对标见 `docs/质量门槛对标.md`。

### 扩展评测集：查清 8 步上限到底卡住了谁

v1.2 时我看到「同配置下 D1 跨时段测出 1/10 与 3/5，差 50pp」，一度得出「系统噪声大于一切」。
扩样本后（v1.3，**140 次同配置采样**）真相比这更精确、也更有用：

**不是整个系统噪声大，而是 4 道题处在 8 步上限边缘——它们的单次结果本身就是二项抽样。**

| 题 | 失败/采样 | 题型 |
|---|---|---|
| D1 | 8/15（**53%**） | 对比（加放系数 vs 短溢装） |
| D2 | 6/15（**40%**） | 对比（待核单 vs 已核单） |
| P3 | 1/5（20%） | 流程（核单合并条件） |
| P1 | 2/15（13%） | 流程（数据流转） |
| **其余 18 题** | **0/90（0%）** | 概念 / 操作 / 报价 / 机制 |

D1 的真实作答率是 **7/15 ≈ 47%**，所以 5 次采样测出 20% 或 60% 都属正常——**不是环境退化**。

成因也测出来了：按 `usage.toolEventCount` 分桶，≤24 的 126 次采样失败率 **0~13%**，
而 **24~28 的 14 次采样失败率 93%**。8 步 × ≈3.4 事件/步 ≈ 27，与内置硬上限吻合。

为此加了 45 题扩展集（`scripts/gen_eval_set.py`，固定种子可复现；判定用**「引用可溯源」**：
题目由某一页生成 → 看答案的 `references` 里有没有这一页，客观可自动判定可复算）。
**结果 40/45 = 88.9%、框架失败 0/45**——单词条定义题（2~3 步）从不撞上限。

> ⚠️ **局限必须连带说明**：扩展集题型单一（概念/实体定义题），**难度低于原 21 题**，
> 且判定口径是「有没有引用源页」而非「答案对不对」，**不能**用它报告「命中率」。
> 它的作用是**测量稳定性与框架失败率**。

> 评测口径提醒：单一命中率不足以判断系统水平（受语料覆盖度影响极大）。企业场景下**忠实度（零幻觉）与引用可溯源**才是上线底线，详见 `docs/质量门槛对标.md`。

## 技术栈

- Python（标准库优先；PDF 用 `pymupdf`、飞书机器人用 `lark-oapi`）
- 智谱 GLM（国产大模型，OpenAI 兼容接口；**分模型**：编译/入库与问答统一用 `glm-4.5-air`，截图 OCR 用 `glm-4v-flash`；向量嵌入 `embedding-3`——**已配置但当前未生效**）
- LLM-Wiki（本地知识库引擎，编译式 Wiki + 内置混合检索（关键词 / 向量 / 知识图谱；本项目实际跑前两路+图谱）+ 本地 HTTP API）
- 飞书开放平台（企业自建应用 + WebSocket 长连接）

> **模型选型的演进**：初期「入库用快模型、问答用旗舰 `glm-4.7`」；后经 `bench_chat_latency.py` 对照实测，
> 问答改用 `glm-4.5-air` 后**延迟 P50 从 61.5s 降到 18.0s（↓3.4×），命中率不降反升**（85.7% → 90.5%），
> 因此现在两层统一用 `glm-4.5-air`。
>
> ⚠️ **v1.1 补上的另一半账**：那句「命中率不降反升」只在**单轮**成立。
> 77 次采样显示，D1 在 10 次里只命中 1 次、D2 5/10，还出现了新模式「原文直吐」（答案直接吐检索片段）。
>
> ⚠️⚠️ **但别急着把账算到模型头上——我试过，站不住**（详见 `eval/评测报告_v1.1_知识固化与稳定性.md` §6）：
> - 「检索面变宽导致 agent 步数触顶」→ **排除**：D1 在各轮的引用数恒为 **10 条**，与 topK、与是否加 FAQ 页都无关；
> - 「换 `glm-4.5-air` 导致」→ **不能归因**：v1.0 的 D1 只有 1 次采样，**从未做过模型对照**。
>
> ✅ **v1.2 真正的解不在模型上**：应用 agent 有 **8 步工具迭代硬上限**（配置里确认无可配项），
> 但响应会返回 `sessionId`，失败文案也自带 `ask it to continue from the latest result`。
> 于是撞上限时**带同一 `sessionId` 补一句「请直接给出最终答案」**——实测 **22.8s 拿到完整正确答案**
> （对照组不带 `sessionId` 发同一句，答「没有收到具体的问题」）。
> 代价只落在撞上限的那部分题，**而不是换模型那样在全部题上多付 3.4× 延迟**。
> 已实现为 `eval_chat.py` 的 `chat_resilient()`（`--no-continue` 可作对照臂）。
>
> ⚠️ **但它没有通过显著性检验**（详见 `eval/评测报告_v1.2_框架失败续跑.md`）：
> A/B **12/15 vs 9/15，Fisher 单侧 p=0.213**。更要紧的是失败性质变了——
> **关：0 次错误答案 + 6 次无答案 → 开：2 次错误答案 + 1 次无答案**。
> 对一个以「0 幻觉」为卖点的系统，把「没答上来」换成「答错」未必划算，
> 因此默认提示语已改为防幻觉版本，**且不把它计入命中率提升**。
>
> ⚠️⚠️ **顺带发现的一条，v1.3 已给出准确版本**：对照臂这次 D1 是 **3/5**，v1.1 同配置测的是 **1/10**。
> 我当时写成「系统噪声大于一切」，**这个说法过头了**——扩样本后（140 次同配置采样）真相是：
> **D1 的真实作答率 ≈ 47%，5 次采样测出 20% 或 60% 都是二项分布的正常波动**；
> 而且失败**只集中在 4 道贴边的题上，其余 18 题 90 次采样零失败**。
> 所以正确记法不是「模型选型的代价」，也不只是「样本量不足」，而是
> **「这几道题天生要的步数贴近 8 步上限」**——下一步是**减少它们需要的步数**。

## License

[MIT](./LICENSE)
