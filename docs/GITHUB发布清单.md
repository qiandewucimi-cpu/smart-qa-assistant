# GitHub 发布清单

本项目采用“一个本地开发源、一个独立公开仓库”的方式维护。两个仓库在文件夹中并列，但代码只在本地完整版中维护，再通过脚本同步到 GitHub 发布仓库。

## 版本边界

| 内容 | 本地完整版 | GitHub 发布版 |
|---|:---:|:---:|
| `scripts/` 正式代码与配置模板 | 是 | 是 |
| `docs/` 项目文档 | 是 | 是，发布前复核 |
| `eval/` 评测集、脱敏结果与报告 | 是 | 是，必须通过脱敏检查 |
| `wiki-patches/` 固化知识 | 是 | 是，仅限脱敏内容 |
| `工作内容/` 原始业务资料 | 是 | 否 |
| `data/` 清洗数据、缓存、状态与备份 | 是 | 否 |
| `projects/` 本地知识库 | 是 | 否 |
| `tools/`、`llm_wiki-src/` 第三方程序 | 是 | 否 |
| `local-only/` 个人笔记与演示准备 | 是 | 否 |
| `scripts/.env`、`scripts/maps_local.py` | 是 | 否 |

目录位置：

```text
智能问答助手/
├── smart-qa-local/       # 本地完整版，唯一开发源
├── smart-qa-assistant/   # 独立 GitHub 发布仓库
└── project-materials/    # 项目说明与迁移资料
```

`smart-qa-local/github-release/` 是同步过程使用的临时安全预览，已被本地仓库忽略；`smart-qa-assistant/` 才是可提交、可推送的 GitHub 版本。两处都不要单独维护代码。

## 发布前操作

在项目根目录运行：

```powershell
py scripts/check_public_release.py --include-untracked
py scripts/sanitize_eval_results.py --check
py scripts/build_github_release.py
py scripts/sync_github_repo.py
git status --short
git diff --check
```

同步后进入 `../smart-qa-assistant/`，检查 `git status` 和 `git diff`。同步脚本会保留发布仓库自己的 `.git/`，不会复制本地资料、密钥、知识库或 LLM Wiki。

检查全部通过后，再按需 `git add`、提交和推送；同步脚本本身不会提交或推送。

## 新电脑恢复

1. 克隆 GitHub 仓库。
2. 安装 `scripts/requirements.txt` 中的可选依赖。
3. 将 `scripts/.env.example` 复制为 `scripts/.env`，再填写本地凭据。
4. 单独放回 `工作内容/`、`data/`、`projects/` 等私有内容；这些内容不从 GitHub 恢复。

## 发布原则

- 不把 `.gitignore` 当作唯一保障；发布检查会再次审查 Git 跟踪文件。
- 不删除本地原始资料，只让它们处于 Git 发布边界之外。
- 评测结果必须先通过输出侧脱敏检查。
- 新增目录若不在发布白名单中，发布检查会失败，需先明确其归属。
