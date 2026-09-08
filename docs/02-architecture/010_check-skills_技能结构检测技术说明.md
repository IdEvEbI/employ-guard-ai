# 技能结构检测技术说明

- **版本**：v0.2
- **日期**：2026-09-08
- **对应工具**：`employ-guard check-skills`
- **产品**：[产品说明 §4.3c](../01-product/001_prd_就业守护助手产品说明.md)
- **书面口径**：[003 §3.1a](../04-standard/003_resume-standard_简历书写标准.md) / [004 C8](../04-standard/004_resume-bar_简历合格线.md)
- **市场依据摘要**：[001 大模型岗位 JD 扫描](../04-standard/001_jd-market-scan_大模型岗位JD市场扫描.md) §3～§4（提示词内嵌摘要，不整篇粘贴）
- **提示词位置**：`src/employ_guard/check_skills.py` 中 `SYSTEM_PROMPT` / `_MARKET_SKILLS_DIGEST`

## 1. 锁定结论

| 项     | 口径                                                                                                  |
| ------ | ----------------------------------------------------------------------------------------------------- |
| 输入   | PDF（须已有 `{stem}.resume.md`）或简历 Markdown / 文本；**优先**同目录 `resume.norm` + `parsed`       |
| 看什么 | SK1 分组；SK2 与意向；SK3 项目覆盖；SK4 用语提醒；**SK5 对齐 001 市场高频**；**SK6 岗位相关技能靠前** |
| 结论   | 总评 `pass` / `fail` / `doubtful`（由分项推导：任一 fail → fail；否则任一 doubtful → doubtful）       |
| 输出   | `{stem}.skills.md` / `{stem}.skills.json`                                                             |
| 终端   | 打印本项结论与岗位意向；**退出码 0**（写出即可）；失败说明原因退出码 `1`                              |
| 编排   | **可单独跑**；挂入 `resume` 见看板 R23；**不替代** `judge-resume`                                     |
| 不做   | 不评排版；不把本项写成整份不能投；不改 judge C8 提示词大面；不以 C/D 类训推 Infra 为应用岗硬门槛      |

## 2. 检查怎么实现

| 编号            | 实现方式 | 说明                                                            |
| --------------- | -------- | --------------------------------------------------------------- |
| **正文来源**    | **规则** | 有 `resume.norm.md` 则优先；否则 `resume.md` 抽出正文           |
| **parsed 提示** | **规则** | 若有 `parsed.json`，把 `skills` / `target_role` 等作参考        |
| **SK1～SK3**    | **LLM**  | 分组、与意向、与项目覆盖                                        |
| **SK4**         | **LLM**  | 写入 `verbosity_note`；不单独把总评打成 fail                    |
| **SK5**         | **LLM**  | 对照 `_MARKET_SKILLS_DIGEST`（001 摘要）；缺市场高频 → doubtful |
| **SK6**         | **LLM**  | 岗位相关靠前；Python / Docker / Coze 等基础项置顶 → doubtful    |
| **fixes**       | **LLM**  | ≤3 条可执行改法                                                 |

## 3. 失败约定

| 情况               | 行为                          |
| ------------------ | ----------------------------- |
| 无简历文本等       | 失败说明，退出码 `1`          |
| 写出（含未过本项） | 退出码 `0`；报告标明 `status` |

## 4. 测试

单元测试注入假的 skills assessor；真实密钥与真实简历不进 Git。改口径后可用已有 `*.resume.md` 实跑 `check-skills` 目视 SK5 / SK6 分项。
