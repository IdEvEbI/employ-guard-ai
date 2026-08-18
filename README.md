# employ-guard-ai · 就业守护 AI 助手

- **定位**：把简历和面试录音变成可核对的检查报告（简历体检 + 面试复盘）的工程仓
- **形态**：本地 CLI；无账号、无门户
- **标准与方法上游**：私有业务文档仓（口径以那边为准；本仓只保留工程所需摘要）
- **关系**：与课评工程仓并列的就业辅导产品；全新实现，不复制平行仓代码，也不混用课评口径

> 本仓负责**可运行流水线**。就业达标尺子与校区打法在上游文档仓，不在这里另起一套。本仓为 public 仓库，文档不写维护者真名、所在机构或对外品牌名。

---

## 1. 目标（分期）

1. **简历体检**：输入简历（可选目标岗位说明）；对照学科口径判断是否达标；给出分项意见与改法；列出可能被问的练习题。
2. **面试复盘**：输入录音（或已有转写稿）；还原问答；给出作答评价、整体评价与不超过 3 条改法。
3. **后续愿景**：知识库、模拟面试、真面试入库、企业查询（不作为当期必做）。

当前里程碑是 **M0（仓基建与文档冻结）**，尚未实现体检或复盘流水线。产品范围见后续 PR 合入的 `docs/01-product/`。

当前**不做**：全员自助门户、代写简历、用分数替代老师分批、替代按日就业台账、云端 ASR、把课评「好课标准」套到简历与面试上。

---

## 2. 新会话请先读

文档将随 M0 后续 Issue 补齐。在文档落地前，以本 README 与 GitHub Issue 为准。

计划阅读顺序：

1. 产品 PRD（范围、用户、验收）
2. 上游标准引用
3. 开发看板与分支合入
4. 本仓 Cursor Rule：`.cursor/rules/employ-guard-ai.mdc`（后续 Issue 添加）

CLI 产品命令（后续里程碑）：`employ-guard resume <简历>` 体检一份简历；`employ-guard interview <录音或转写稿>` 复盘一场面试。

**默认技术栈**：Python 3.12 CLI（uv）· 本地 mlx-whisper（面试复盘）· DeepSeek API · Markdown 工具链（Prettier + prettier-plugin-zh + markdownlint）

---

## 3. Markdown 工具链

编辑器安装推荐扩展后，保存 Markdown 会自动格式化（含中英文空格）。

```bash
npm install
npm run format      # 格式化
npm run lint:md     # Markdownlint 检查
```

提交前请确保 `npm run format:check` 与 `npm run lint:md` 均通过。CI 工作流：`.github/workflows/docs-lint.yml`。

---

## 4. Python CLI（最小入口）

```bash
uv sync
uv run employ-guard --help
uv run employ-guard check
```

流水线命令（简历体检、面试复盘）将在后续里程碑加入，本 Issue 只保证入口能启动。密钥放在 `.env`（从 `.env.example` 复制），不要提交。

---

## 5. 隐私

- 真实学员简历、面试录音、完整逐字稿或可识别的商业秘密默认不进 Git。
- API Key、模型密钥只放本地环境变量或私钥文件（已进 `.gitignore`）。
- 对外演示用脱敏或自制样例。
- public 仓库中不写维护者真名、所在机构或对外品牌名。
