# 文档入口（employ-guard-ai）

业务愿景与就业评价标准在上游私有文档仓。本仓只保留工程所需摘要与约定，避免与上游长期各写一套。

## 1. 产品（做什么、怎么验收）

请先阅读 [01-product/README.md](./01-product/README.md)。

工具清单、老师用的两条命令与分期，以 [001 产品说明](./01-product/001_prd_就业守护助手产品说明.md) 为唯一产品源。

## 2. 架构与技术（怎么做、为什么）

请阅读 [02-architecture/README.md](./02-architecture/README.md)。

评价标准从哪里来，见 [上游标准引用](./02-architecture/001_upstream-standards_上游标准引用.md)。就业投递向标准见 [04-standard](./04-standard/README.md)（含 JD 扫描、简历书写标准、简历合格线）。

## 3. 标准（合格线与 JD）

请阅读 [04-standard/README.md](./04-standard/README.md)。

## 4. 交付（Issue、DevOps）

请阅读 [03-delivery/README.md](./03-delivery/README.md)。

当前只做什么，以 [开发看板](./03-delivery/001_dev-board_开发看板.md) 为准。如何开 Issue、如何合入，见 [分支与合入](./03-delivery/002_devops-workflow_分支与合入.md)。

## 5. 新会话推荐阅读顺序

1. [开发看板](./03-delivery/001_dev-board_开发看板.md)：确认当前只做什么。
2. [001 产品说明](./01-product/001_prd_就业守护助手产品说明.md)：确认工具、老师用的命令、不做什么。
3. [上游标准引用](./02-architecture/001_upstream-standards_上游标准引用.md)：确认评价标准从哪里来。
4. [04-standard](./04-standard/README.md)：确认 JD 扫描、书写标准与简历合格线。
5. 根目录 [README.md](../README.md)：如何安装与运行 `employ-guard check`。
6. 本仓 Cursor Rule：`.cursor/rules/employ-guard-ai.mdc`。
7. 新会话 Skill：`.cursor/skills/employ-guard-new-session/SKILL.md`。
