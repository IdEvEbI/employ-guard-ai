# 老师第一次跑通（半页）

- **对应**：排查增强 R11
- **目标**：本机命令行跑通 `resume`；无账号、无门户

## 1. 准备（约 5 分钟）

1. 安装 [uv](https://docs.astral.sh/uv/) 与 Python 3.12+。
2. 克隆本仓，在仓库根执行 `uv sync`，再 `cp .env.example .env`。
3. 编辑 `.env`，填写 `LLM_API_KEY`（默认 DeepSeek；查排版还需支持看图的 `LLM_VISION_MODEL`，见示例文件注释）。
4. 自检：`uv run employ-guard check`。缺密钥或依赖时，按终端提示补齐即可。

## 2. 看一份简历

把投递用 **PDF** 放到 `data/input/`（真实简历不要提交 Git），然后：

```bash
uv run employ-guard resume data/input/某份简历.pdf
# 排查一摞（少跑贵步骤）：
uv run employ-guard resume data/input/某目录 --triage
```

报告在 `data/output/`。排版结论与内容结论分开看；`brief` / `batch-summary` 只做本机阅读入口。

**`--triage`（排查模式）少跑哪些：**

| 仍会跑                           | 关掉（本趟不跑） |
| -------------------------------- | ---------------- |
| PDF 出图、查排版                 | 查文字表达       |
| 读文本、判能不能投               | 出练习题         |
| 写出短 `brief`（建议先改 ≤3 条） | —                |

适合快速扫一摞「能不能投 + 排版过不过」。若还要文字表达或练习题，去掉 `--triage` 再跑完整模式（或单独跑对应工具）。

## 3. 注意

- 本期输入必须是 PDF；不是 PDF 会直接失败并说明。
- **扫描件 / 纯图 PDF**：`read-resume` 在文字层为空时用本机 tesseract OCR；未安装时该步失败并说明（不等于「不能投」）。数字 PDF 不需要。安装：`brew install tesseract tesseract-lang`。
- 听后做复盘（`interview`）暂缓；缺 ffmpeg 不影响简历侧。
- 更多命令与口径见仓库根 [README](../../README.md) 与 [产品说明](../01-product/001_prd_就业守护助手产品说明.md)。
