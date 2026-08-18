---
name: employ-guard-new-session
description: >-
  Starts an employ-guard-ai session by reading the board, product brief, and
  merge conventions in order. Use at the beginning of work in this repository,
  when the user says 继续, 开干, or asks what to do next.
---

# 本仓新会话阅读顺序

开始改本仓之前，按顺序打开下列文档，不要凭记忆扩大范围。

1. [开发看板](../../../docs/03-delivery/001_dev-board_开发看板.md)：当前只做什么。同时只推进一件事。
2. [产品说明](../../../docs/01-product/001_prd_就业守护助手产品说明.md)：工具、老师用的命令、不做什么。
3. [上游标准引用](../../../docs/02-architecture/001_upstream-standards_上游标准引用.md)：改评价口径时打开上游原文。
4. [分支与合入](../../../docs/03-delivery/002_devops-workflow_分支与合入.md)：从 `main` 拉分支，PR 打回 `main`，一 Issue 一 PR。
5. 本仓规则：`.cursor/rules/employ-guard-ai.mdc`。

## 做完阅读后

- 对照看板确认当前 Issue；没有分支则从 `main` 拉 `feat/#N-短名`（或 `docs` / `chore` / `fix`）。
- 不实现看板标明「现在不做」的能力；没有书面合格线，不开「判能不能投」或「查排版」。
- 本期简历只检查 PDF。
- 改完后停住，等维护者说「提交」再 commit。
