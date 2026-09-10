# LLM Wiki 运行手册（阶段 2）

> 版本 v0.1 ｜ 更新：2026-09-09 ｜ 目标：把本地 64 份清洗后语料编译成知识库

---

## 0. 结论先行

| 问题 | 结论 |
|---|---|
| 能不能跑？ | **能**。用官方 Windows 便携版，解压即用，无需装 Rust/protoc/编译 |
| 用什么模型？ | **智谱 GLM**（OpenAI 兼容，`https://open.bigmodel.cn/api/paas/v4`） |
| 要不要 clone 源码？ | **不需要**。源码编译要 Rust 1.88+ + protoc（本机没有），性价比低；便携版直接是编译好的可执行文件 |

---

## 1. 下载（你来做，网络卡点在你这边）

> 国内直连 GitHub 极慢（实测 ~2KB/s，42.9MB 要几小时）。**用浏览器或迅雷下载**，不要用命令行 curl。

**第一步：打开官方 Release 页**
```
https://github.com/nashsu/llm_wiki/releases
```

**第二步：找到最新版 v0.6.11（2026-08-25），下载 Windows 文件，三选一：**

| 文件 | 说明 | 推荐 |
|---|---|---|
| `LLM-Wiki-0.6.11-windows-x64-portable.zip` | 便携版，解压即用，免安装 | ⭐ 首选 |
| `...-setup.exe` | 安装版 | 次选 |
| `...-msi` | 安装包 | 备选 |

**如果 GitHub 打不开/太慢，用镜像加速（复制 release 下载链接，把 `github.com` 换成下面任一前缀）：**
```
https://ghfast.top/
https://gh-proxy.com/
https://ghproxy.net/
```
> 例：`https://ghfast.top/https://github.com/nashsu/llm_wiki/releases/download/v0.6.11/LLM-Wiki-0.6.11-windows-x64-portable.zip`
>
> 或直接用迅雷粘贴原始链接下载。

**第三步：解压**，得到 `LLM-Wiki.exe`（或类似名字），双击运行。

---

## 2. 配置智谱 API（关键一步）

LLM Wiki 本身不带模型，需要接一个大模型来「读文档、写词条」。智谱走 OpenAI 兼容通道，直接选「自定义 / Custom」即可。

**第一步：去智谱开放平台拿 API Key**
```
https://open.bigmodel.cn
```
控制台 →「API Keys」→ 新建一个 Key，复制保存（形如 `xxxxx.yyyyy`，只显示一次）。

**第二步：打开 LLM Wiki → 设置（Settings）→ 添加 LLM 提供商**，填：

| 配置项 | 填什么 |
|---|---|
| 提供商类型 | **Custom / OpenAI 兼容** |
| Base URL | `https://open.bigmodel.cn/api/paas/v4` |
| API Key | 你刚复制的智谱 Key |
| 模型 | `glm-4.6`（首选，见下方选型） |

**模型选型建议：**

| 模型 | 用途 | 备注 |
|---|---|---|
| `glm-4.6` | 默认主力，质量/成本均衡 | ⭐ 推荐 |
| `glm-4.7` | 智谱最新旗舰（若控制台已开放） | 质量更高、更慢更贵 |
| `glm-4-flash` | 大批量首次编译时省 token | 可先试，效果不够再换 4.6 |

> 提示：首次把 64 份文档全部编译成词条，会消耗一定 token（逐份读 + 生成）。可以先拿一小批（比如 5 份）跑通，确认效果和成本，再全量导入。

---

## 3. 导入数据并编译

1. 左侧「项目」→ **新建项目**（选模板，或空白项目）。
2. 进入 **资料源 / Sources → 导入文件夹**，选择：
   ```
   C:\Users\31114\WorkBuddy\智能问答助手\data\clean\
   ```
   （共 64 份已脱敏语料：40 txt/md + 24 docx 转出的 txt）
3. 观察右侧 **活动面板（Activity Panel）**：LLM 会逐份读取 → 生成 wiki 页面（实体页 / 概念页 / 来源摘要），并自动更新 `index.md`、`log.md`、`overview.md`。
4. 等全部编译完，在 **聊天** 里提问测试，比如：
   - 「DLS 系统的下单流程是什么？」
   - 「培训期间导师 A 强调了哪些单据重点？」

---

## 4. 验收标准（对应项目计划书阶段 2）

- [ ] 64 份语料全部成功导入并生成来源摘要页
- [ ] 能通过聊天提问返回相关片段，且带来源引用
- [ ] 分块/页面结构合理（实体、概念、来源三类页面能对上）
- [ ] 记录一次全量编译的耗时与 token 成本（写进复盘）

---

## 5. 常见问题

| 问题 | 解决 |
|---|---|
| 双击 exe 被 Windows 拦截 | 智能屏「仍要运行」/ 更多信息 → 仍要运行 |
| Base URL 报错 401 | 确认 Key 没复制错、没带多余空格 |
| 模型名报错 | 去智谱控制台看当前账号可用模型名，改填一致 |
| 编译很慢 | 正常（串行摄入）；可先换 `glm-4-flash` 测速 |
| 中文界面 | 应用内可切换中/英（react-i18next） |

---

## 6. 下一步

跑通编译后 → 进入 **阶段 3（Dify 检索与工作流）**，届时把 LLM Wiki 的 HTTP API（`127.0.0.1:19828`）接给 Dify，或直接导出 Wiki 的 Markdown 作为检索语料。

---

## 7. 实测记录（2026-09-09）

> 以下结论来自本机实际执行，非推测。

| 项 | 实测结果 |
|---|---|
| 下载通道 | 直连 GitHub ~1KB/s；`gh-proxy.com` 镜像 **可用**（已缓存，~170KB/s，3.7 分钟下完）；`ghfast.top` 失败 |
| 文件校验 | 42.9MB，SHA256 `d9e3df...f8e6` 与官方一致 ✅ |
| 解压位置 | `tools/llm-wiki/`（含 `LLM Wiki.exe` + `mcp-server/` + `pdfium/`） |
| 启动 | 双击 `tools/llm-wiki/LLM Wiki.exe` 即弹出窗口（Tauri WebView2 渲染） |
| 应用数据目录 | `C:\Users\31114\AppData\Local\com.llmwiki.app`（项目/设置存于此） |
| 本地 HTTP API | `http://127.0.0.1:19828`，**默认关闭**，需在设置里手动开启（阶段 3/5 用） |
| MCP server | 随包附带 `mcp-server/`（`llm-wiki-mcp`，v0.4.26，Node≥20），阶段 5 接入用 |

