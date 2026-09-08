# Word 转 PDF · 技术说明

- **版本**：v0.1
- **日期**：2026-09-08
- **对应**：产品说明 §4.0；看板 R13 / [#80](https://github.com/IdEvEbI/employ-guard-ai/issues/80)
- **命令**：`employ-guard word-to-pdf`

## 1. 职责

把 Word 原稿（`.docx` / `.doc`）转成投递用 PDF，供后续 `pdf-to-images` / `resume` 使用。本步**不**评价排版或内容；转换失败**不得**写成「不能投」。

`resume` **不**直接接受 `.docx`。

## 2. 转换器优先级

1. **LibreOffice**（`soffice` / `libreoffice`，含 macOS 应用包内路径）：无界面批转，推荐。
2. **Microsoft Word（仅 macOS）**：经 `osascript` 打开并另存为 PDF。

两者都没有时，退出码 1，并提示安装方式。`employ-guard check` 将本项标为可选（已有 PDF 时不需要）。

## 3. 输入与输出

| 项   | 约定                                                                  |
| ---- | --------------------------------------------------------------------- |
| 输入 | 已存在的 `.docx` / `.doc`（相对路径可落在 `data/input/`）             |
| PDF  | 默认与 Word **同目录** `{stem}.pdf`；可用 `--out-dir` 改目录          |
| 记录 | `data/output/.../{stem}/word-to-pdf.json`（含转换器名与哈希；不评价） |

真实学员 Word / PDF **不进 Git**。

## 4. 失败口径

- 找不到文件、后缀不对、无转换器、转换超时、未写出有效 PDF → `WordToPdfError` → CLI 退出码 **1**。
- 文案须说明「转换失败 / 尚未评价内容」，**禁止**写成合格线未过或不能投。

## 5. 不做

- 不挂进 `resume` 自动链（老师先转再检查）。
- 不引入云端转码服务。
- 不在本步做排版或内容判断。
