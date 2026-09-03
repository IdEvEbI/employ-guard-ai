# 投前看简历（resume）技术说明

- **版本**：v0.1
- **日期**：2026-09-03
- **对应命令**：`employ-guard resume`
- **产品**：[产品说明 §5](../01-product/001_prd_就业守护助手产品说明.md)

## 1. 锁定结论

| 项     | 口径                                                                                          |
| ------ | --------------------------------------------------------------------------------------------- |
| 输入   | **必须是 PDF**；非 PDF 直接失败并说明请先转成 PDF                                             |
| 做什么 | 按顺序调用各工具；老师一条命令跑完出图 → 查排版 → 读文本 → 查文字表达 → 判能不能投 → 出练习题 |
| 跳过   | 对应结果已存在则跳过该步（页图 / layout / resume / writing / judge / questions）              |
| 可关   | `--no-questions` 关掉出练习题；`--job-desc` 可选传给判能不能投与出练习题                      |
| 不做   | 不合并内容与排版结论；不自动扫描全班目录；不把出图/读文本失败写成「不能投」                   |

各工具仍可单独再跑。本命令只编排，不另立评价口径。

## 2. 调用顺序与产物

| 顺序 | 工具              | 跳过条件                         | 产物                                      |
| ---- | ----------------- | -------------------------------- | ----------------------------------------- |
| 1    | `pdf-to-images`   | `pages/` 下已有页图              | `pages/*.png`、`pdf-to-images.json`       |
| 2    | `check-layout`    | `{stem}.layout.json`             | `{stem}.layout.md` / `.json`              |
| 3    | `read-resume`     | `{stem}.resume.md`               | `{stem}.resume.md` / `.json`              |
| 4    | `check-writing`   | `{stem}.writing.json`            | `{stem}.writing.md` / `.json`             |
| 5    | `judge-resume`    | `{stem}.judge.json`              | `{stem}.judge.md` / `.json`               |
| 6    | `draft-questions` | `{stem}.questions.json` 或已关闭 | `{stem}.questions.md` / `.json`（可关闭） |

运行目录约定见 `paths.output_run_dir`。实现入口：`src/employ_guard/resume.py` 的 `run_resume`。

## 3. 退出码

| 情况                                         | 退出码 | 说明                                            |
| -------------------------------------------- | ------ | ----------------------------------------------- |
| 非 PDF、出图失败、读文本失败、某步工具硬错误 | `1`    | **不得**写成内容不能投或排版不合格              |
| 排版未达标或内容未达标（仍尽量跑完后续步）   | `2`    | 终端摘要分栏写清排版 / 文字表达 / 内容 / 练习题 |
| 排版与内容均达标                             | `0`    | 文字表达有待改进项**不**自动把退出码升为 `2`    |

## 4. 测试

单元测试注入假的 vision / writing / content / questions assessor；真实密钥与真实简历不进 Git。
