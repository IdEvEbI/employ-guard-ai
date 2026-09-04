# employ-guard-ai · 就业守护

- **定位**：一组在本机运行的小工具，把投前看简历、听后做复盘变成可以核对的文件与报告
- **形态**：本机命令行；无账号、无门户。老师记住两条命令，维护者可以单独运行某一个工具
- **标准与方法上游**：私有业务文档仓（口径以那边为准；本仓只保留工程所需摘要）
- **关系**：与课评工程仓并列；全新实现，不复制平行仓代码，也不混用课评口径

> 本仓负责**可以运行的工具，以及老师用的两条命令**。简历是否达标、面试作答如何评价，口径在上游文档仓，不在这里另写一套。本仓为 public 仓库，文档不写维护者真名、所在机构或对外品牌名。

---

## 1. 目标（分期）

1. **投前看简历**：出图并查排版；读文本 → 判能不能投 → 出练习题（命令 `resume`，本期只收 PDF）。
2. **听后做复盘**：转写现场录音 → 还原问答 → 面试评价（命令 `interview`）。
3. **后续愿景**：知识库、模拟面试、真面试入库、企业查询（都是新工具，不作为本期必须交付）。

当前里程碑是 **投前看简历 · 辅导增强**：排查增强 R7～R12 / R3b 已可用；听后做复盘暂缓；**R13 本轮跳过**。当前推进 **R14**（按项目出基础题与追问 / [#53](https://github.com/IdEvEbI/employ-guard-ai/issues/53)）。顺序见 [开发看板](./docs/03-delivery/001_dev-board_开发看板.md) §5.1。

当前**不做**：全员自助门户、代写简历、用分数替代老师分批、替代按日就业台账、默认使用云端语音识别、把课评「好课标准」用到简历与面试上、为两个动作各建一个仓库。

---

## 2. 新会话请先读

1. [开发看板](./docs/03-delivery/001_dev-board_开发看板.md)：当前只做什么
2. [产品说明](./docs/01-product/001_prd_就业守护助手产品说明.md)（工具、老师用的命令、验收）
3. [上游标准引用](./docs/02-architecture/001_upstream-standards_上游标准引用.md)
4. [分支与合入](./docs/03-delivery/002_devops-workflow_分支与合入.md)
5. 本仓 Cursor Rule：`.cursor/rules/employ-guard-ai.mdc`
6. 新会话 Skill：`.cursor/skills/employ-guard-new-session/SKILL.md`（说「继续 / 开干」时优先按此阅读）

老师第一次跑通（半页）：[003 老师第一次跑通](./docs/03-delivery/003_onboard_老师第一次跑通.md)。先 `uv run employ-guard check`，再 `resume`。

老师用的命令：`employ-guard resume <简历.pdf>`（已可用）；`employ-guard interview <录音> [--resume <简历.pdf>]`（听后做复盘，**暂缓**）。各个工具见产品说明第 4 节。

**默认技术栈**：Python 3.12 命令行（uv）· 本机 mlx-whisper（转写）· DeepSeek API · Markdown 格式化（Prettier + prettier-plugin-zh + markdownlint）

---

## 3. Markdown 格式化

编辑器安装推荐扩展后，保存 Markdown 会自动格式化（含中英文空格）。

```bash
npm install
npm run format      # 格式化
npm run lint:md     # Markdownlint 检查
```

提交前请确保 `npm run format:check` 与 `npm run lint:md` 均通过。CI 工作流：`.github/workflows/docs-lint.yml`。

---

## 4. Python 命令行

```bash
uv sync
uv run employ-guard --help
uv run employ-guard check
uv run employ-guard pdf-to-images <简历.pdf>
uv run employ-guard check-layout <简历.pdf>
uv run employ-guard read-resume <简历.pdf>
uv run employ-guard check-writing <简历.pdf>
uv run employ-guard judge-resume <简历.pdf>
uv run employ-guard draft-questions <简历.pdf>
uv run employ-guard resume <简历.pdf>
uv run employ-guard resume <含PDF的目录>   # 批跑并写本地总表
```

`pdf-to-images` 把投递用 PDF 按页写成 `data/output/.../pages/`（本步不评价排版）。`check-layout` 只凭页图对照 [004 §3](./docs/04-standard/004_resume-bar_简历合格线.md) 查排版（须先出图；[002](./docs/02-architecture/002_check-layout_查排版技术说明.md)）。`read-resume` 抽出文本（文字层为空时 OCR；本步不判断能不能投）。`check-writing` 只凭文本对照 [003 §3.5](./docs/04-standard/003_resume-standard_简历书写标准.md) 查错别字、标点与用语（须先 read-resume；[004 技术说明](./docs/02-architecture/004_check-writing_查文字表达技术说明.md)）。

`judge-resume` 只凭文本对照 [004 §2](./docs/04-standard/004_resume-bar_简历合格线.md) 判能不能投（须先 read-resume；[003 技术说明](./docs/02-architecture/003_judge-resume_判能不能投技术说明.md)）。

`draft-questions` 按主项目出基础题（考察点 + 可能追问）与深挖题（[005 技术说明](./docs/02-architecture/005_draft-questions_出练习题技术说明.md)）；`resume` 完整模式会跑本步，`--triage` / `--no-questions` 可关。

`resume` 按产品说明 §5 调用上述工具（布局路径与文本路径并行）；参数可为单份 PDF 或含 PDF 的目录（批跑写 `batch-summary.md`）；已有结果且 PDF 哈希一致则跳过；`--force` 强制重跑；`--triage` 排查模式（关掉出题与查文字，写出 brief）；可用 `--no-questions` 关掉出练习题（[006 技术说明](./docs/02-architecture/006_resume_投前看简历技术说明.md)）。

真实简历放在 `data/input/`，不要提交。密钥放在 `.env`（从 `.env.example` 复制），不要提交。

---

## 5. 隐私

- 真实学员简历、面试录音、完整逐字稿或可识别的商业秘密默认不进 Git。
- API Key、模型密钥只放本地环境变量或私钥文件（已进 `.gitignore`）。
- 对外演示用脱敏或自制样例。
- public 仓库中不写维护者真名、所在机构或对外品牌名。
