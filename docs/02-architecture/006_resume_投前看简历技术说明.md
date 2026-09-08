# 投前看简历（resume）技术说明

- **版本**：v0.13
- **日期**：2026-09-08
- **对应命令**：`employ-guard resume`
- **产品**：[产品说明 §5](../01-product/001_prd_就业守护助手产品说明.md)

## 1. 锁定结论

| 项     | 口径                                                                                                           |
| ------ | -------------------------------------------------------------------------------------------------------------- |
| 输入   | **必须是 PDF**；参数可为单份 PDF，或**老师显式指定**的含 PDF 目录（批跑）                                      |
| 做什么 | 老师一条命令跑完；墙钟上 `(出图→查排版) ∥ (抽文本→规范化/抽取→基础信息→文字→技能→项目审阅→判断→题)`            |
| 批跑   | 目录下一层 `.pdf`（默认非递归）；逐份调用同一套 `run_resume`；写出本地 `batch-summary.md` / `.json`            |
| 抽文本 | 优先 PDF 文字层；**整份文字层为空**时 `read-resume` 内 OCR（tesseract）；失败退出码 `1`，不得写成「不能投」    |
| 正文源 | **parse 之后**各文本评价步一律优先 `{stem}.resume.norm.md`（有则用；无则回退抽出正文）；layout 仍看页图        |
| 跳过   | 对应结果已存在 **且** 输入 PDF 的 sha256 与记录一致时才跳过                                                    |
| 强制   | `--force` 忽略已有产物，各步重跑                                                                               |
| 排查   | `--triage`：关掉查文字、技能检、项目审阅、出练习题；**仍跑** parse / profile；写出短 `{stem}.brief.md`         |
| 进度   | 每步开始打印「正在…」；结束打印状态与耗时（毫秒）                                                              |
| 可关   | `--no-questions` 关掉按项目出练习题；`--job-desc` 可选                                                         |
| 不做   | 不合并内容与排版结论；不自动扫描未指定目录；不上门户、不在班级群点名；不把出图/读文本/parse 失败写成「不能投」 |

各工具仍可单独再跑。本命令只编排，不另立评价口径。教练摘要只做阅读入口，合格线仍以 layout / judge 报告为准。分项（profile / skills / projects）不替代整份 judge。

## 2. 现行调用顺序与产物

逻辑顺序如下；实现上**布局路径**与**文本路径**并行。任一路硬失败 → 退出码 `1`（不得写成「不能投」）。语义上可重叠执行的评价步仅为「查排版 ∥ 查文字表达」（各自输入就绪后）。

| 顺序 | 工具              | 路径 | 跳过条件（均须非 `--force`）                                      | 产物                                       |
| ---- | ----------------- | ---- | ----------------------------------------------------------------- | ------------------------------------------ |
| 1    | `pdf-to-images`   | 布局 | 已有页图，且 `pdf-to-images.json` 的 `sha256` 与当前 PDF 一致     | `pages/*.png`、`pdf-to-images.json`        |
| 2    | `check-layout`    | 布局 | 已有 `{stem}.layout.json`，且出图记录 PDF 哈希一致                | `{stem}.layout.md` / `.json`               |
| 3    | `read-resume`     | 文本 | 已有 `{stem}.resume.md`，且 `{stem}.resume.json` 的 `sha256` 一致 | `{stem}.resume.md` / `.json`（可含 OCR）   |
| 4    | `parse-resume`    | 文本 | 已有 `{stem}.parsed.json` 与 norm，且 `resume.json` 哈希一致      | `{stem}.resume.norm.md`、`{stem}.parsed.*` |
| 5    | `check-profile`   | 文本 | 已有 profile 报告且哈希一致                                       | `{stem}.profile.md` / `.json`              |
| 6    | `check-writing`   | 文本 | 已有 writing 报告且哈希一致；**排查模式下关闭**                   | `{stem}.writing.md` / `.json`              |
| 7    | `check-skills`    | 文本 | 已有 skills 报告且哈希一致；**排查模式下关闭**                    | `{stem}.skills.md` / `.json`               |
| 8    | `review-projects` | 文本 | 已有 projects 报告且哈希一致；**排查模式下关闭**                  | `{stem}.projects.md` / `.json`             |
| 9    | `judge-resume`    | 文本 | 已有 judge 报告，且 `resume.json` 的 PDF 哈希一致                 | `{stem}.judge.md` / `.json`                |
| 10   | `draft-questions` | 文本 | 已有 questions 报告且哈希一致，或已关闭 / **排查模式**            | `{stem}.questions.md` / `.json`（可关闭）  |

## 2.1 目录批跑

```bash
uv run employ-guard resume <含PDF的目录> [--triage] [--force]
```

- 只处理该目录**下一层**的 `.pdf`（非递归）；其他后缀跳过；目录内无 PDF 则退出码 `1` 并说明。
- 选项（`--triage` / `--force` / `--no-questions` / `--job-desc` / `--dpi`）透传给每一份。
- 总表写在镜像输出目录的 `batch-summary.md` 与 `batch-summary.json`。
- 批跑退出码：任一份 `1` → `1`；否则任一份 `2` → `2`；否则 `0`。

## 3. 结构化链式检查（已合入 R17～R23）

与 §2 现行顺序一致：

1. 出图 → 查排版（页图）
2. 抽文本 → **文本规范化**（不改字义；**不是**查排版）→ **字段抽取** `parsed.*`
3. **基础信息检测**（首页岗位类表述，对齐 C1）
4. 查文字表达（与查排版可并行，各自输入就绪后）
5. **技能结构 / 与意向 / 与项目覆盖 / 市场高频 / 排序**
6. **项目审阅**（时间倒序；G1-T；P1～P8；结构）
7. 判能不能投（汇总合格线）
8. 按项目出练习题

**并行**：布局路径 ∥ 文本路径；评价步语义上仅查排版 ∥ 查文字表达。

口径：

- **parse 之后**文本评价步（profile / writing / skills / projects / judge / questions）一律优先 `resume.norm.md`；公共函数 `prefer_normalized_body`；JSON 记 `used_normalized`。
- 首页基本信息须有岗位类表述（003 S1 / 004 C1）；不强制「应聘岗位」四字。
- 性别等只抽取、缺不硬伤；`parse_incomplete` 不得写成不能投。
- 近段含金量不得明显低于远段 → 存疑 / 辅导（005 G1-T）；P\* 同理，默认不自动等同不能投。

## 4. 修订记录

| 版本  | 日期       | 说明                                                             |
| ----- | ---------- | ---------------------------------------------------------------- |
| v0.8  | 2026-09-04 | 出题步按项目；不编排 review-projects                             |
| v0.9  | 2026-09-07 | 写入结构化链目标 §3；并行收窄为 layout ∥ writing；C1 / G1-T 指向 |
| v0.10 | 2026-09-08 | 注明 check-skills 可单独跑；挂入见 R23                           |
| v0.11 | 2026-09-08 | 完整模式挂入 review-projects；排查可关；步骤改为 7 步            |
| v0.12 | 2026-09-08 | R23：挂入 parse / profile / skills；现行顺序升格；步骤改为 10 步 |
| v0.13 | 2026-09-08 | R24：parse 后文本步一律优先 resume.norm.md                       |
