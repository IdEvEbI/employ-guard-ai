# 文档入口（employ-guard-ai）

业务愿景与就业评价标准在上游私有文档仓。本仓只保留工程所需摘要与约定，避免与上游长期各写一套。

## 1. 产品（做什么、怎么验收）

请先阅读 [01-product/README.md](./01-product/README.md)。

工具清单、老师用的两条命令与分期，以 [001 产品说明](./01-product/001_prd_就业守护助手产品说明.md) 为唯一产品源。

## 2. 架构与技术（怎么做、为什么）

请阅读 [02-architecture/README.md](./02-architecture/README.md)。

评价标准从哪里来，见 [上游标准引用](./02-architecture/001_upstream-standards_上游标准引用.md)。

## 3. 交付（Issue、DevOps）

`docs/03-delivery/` 将在后续 Issue 合入。当前只做什么，在落地前以 GitHub Issue 为准。

## 4. 新会话推荐阅读顺序

1. [001 产品说明](./01-product/001_prd_就业守护助手产品说明.md)：确认工具、老师用的命令、不做什么。
2. [上游标准引用](./02-architecture/001_upstream-standards_上游标准引用.md)：确认评价标准从哪里来。
3. 根目录 [README.md](../README.md)：如何安装与运行 `employ-guard check`。
4. 本仓 Cursor Rule：`.cursor/rules/employ-guard-ai.mdc`（后续 Issue 添加）。
