"""文本规范化与字段抽取。"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from employ_guard.cli import app
from employ_guard.parse_resume import (
    ParseResumeError,
    infer_age_from_birth,
    llm_normalize_rejection_reason,
    normalize_parsed_fields,
    normalize_resume_body,
    normalize_resume_body_rules,
    parse_resume,
)
from employ_guard.read_resume import extract_resume_text

runner = CliRunner()


def _write_pdf(path: Path, text: str) -> None:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 72), text, fontsize=14)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()


def _fake_assessor(_text: str) -> dict:
    return {
        "name": "柯文",
        "gender": None,
        "hometown": "湖北",
        "age": "23",
        "target_role": None,
        "phone": "19173814213",
        "email": "19173814213@163.com",
        "skills": ["LangGraph", "RAG", "FastAPI"],
        "work_experience": [
            {
                "start": "2024.07",
                "end": "2026.08",
                "company": "厦门鲲鹭科技有限公司",
                "title": "全栈工程师",
                "duties": "金融大模型应用研发。",
            }
        ],
        "projects": [
            {
                "start": "2025.08",
                "end": "2026.08",
                "name": "ICR智库检索",
                "role": "全栈工程师",
                "content": "多 Agent 金融问答。",
            }
        ],
        "education": [
            {
                "start": "2021.09",
                "end": "2025.06",
                "school": "武夷学院",
                "major": None,
                "degree": "本科",
            }
        ],
        "self_evaluation": "熟悉大模型应用链路。",
        "parse_incomplete": False,
        "parse_notes": [],
    }


def test_help_lists_parse_resume() -> None:
    result = runner.invoke(app, ["parse-resume", "--help"])
    assert result.exit_code == 0
    assert "规范" in result.stdout or "字段" in result.stdout


def test_normalize_resume_body_spacing() -> None:
    raw = "姓名：柯文\n熟悉Python 、FastAPI\nMCP协议"
    out = normalize_resume_body_rules(raw)
    assert "Python、FastAPI" in out or "Python 、FastAPI" not in out
    assert "熟悉 Python" in out or "Python" in out
    assert "MCP 协议" in out or "MCP协议" in out


def test_normalize_keeps_date_dot_without_space() -> None:
    raw = "教育背景\n2021.09-2025.06 江西中医药大学 计算机科学与技术\n"
    out = normalize_resume_body_rules(raw)
    assert "2021.09-2025.06" in out
    assert "2021. 09" not in out


def test_normalize_markdown_headings_and_lists() -> None:
    raw = """项目四：智评处理平台 2024.09-2025.01 项目负责人
项目背景与痛点
为了解决电商评论痛点。
项目职责与技术栈
1 数据收集与预处理：收集 15 万条数据。
2 特征工程与基线模型搭建：引入 TF-IDF。
项目成果：
·BM25 高置信度命中率 90%以上。
·Milvus 1095 向量。
业务效率提升：响应时间缩短约 60%。
"""
    out = normalize_resume_body_rules(raw)
    assert "### 项目四：智评处理平台" in out
    assert "#### 项目背景与痛点" in out
    assert "#### 项目职责与技术栈" in out
    assert "#### 项目成果" in out
    assert "1. 数据收集与预处理" in out
    assert "2. 特征工程与基线模型搭建" in out
    assert "- BM25 高置信度" in out
    assert "- Milvus 1095" in out
    assert "- 业务效率提升" in out


def test_normalize_malihua_style_sections_and_titles() -> None:
    """麦丽华式：章节别名、英文句号收尾、编号短标题、无项目符号技能。"""
    raw = """1995/07 mlh163youxiang@163.com
麦丽华 18320647824 求职意向：大模型工程师
大模型工程师 广东茂名 大学英语四级

专业技能

熟练运用 Numpy、Pandas 做数据分析;
掌握 Milvus、BGE-M3，抑制模型
幻觉.

工作经验

广州袋鼠跨境电商有限公司 大模型工程师 2023/09 - 2026/07
负责数据治理；

项目经历

电商 AI 多智能体协同运营平台 项目负责人 2026/02 - 2026/04
1.多智能体架构设计与模块化拆分开发
采用 Orchestrator 中心化调度架构，模块高度解耦。
2.搭建全域电商 RAG 底层检索底座，全局抑制模型幻觉
基于 Chroma 向量数据库做持久化存储。

教育经历

珠海城市职业技术学院 商务英语 2016/09 - 2019/06
"""
    out = normalize_resume_body_rules(raw)
    assert "mlh163youxiang@163.com" in out
    assert "求职意向：大模型工程师" in out
    assert "工程师大模型工程师" not in out.replace(" ", "")
    assert "幻觉.工作经验" not in out.replace(" ", "")
    assert "## 专业技能" in out
    assert "- 熟练运用 Numpy" in out
    assert "- 掌握 Milvus" in out
    assert "## 工作经验" in out
    assert "2023/09 - 2026/07" in out
    assert "2026/07负责" not in out.replace(" ", "")
    assert "#### 工作职责" in out
    assert "- 负责数据治理" in out
    assert out.index("### 广州袋鼠") < out.index("#### 工作职责")
    assert out.index("#### 工作职责") < out.index("- 负责数据治理")
    assert "## 项目经历" in out

    assert "### 电商 AI 多智能体协同运营平台" in out
    assert "1. 多智能体架构设计与模块化拆分开发" in out
    assert "采用 Orchestrator" in out
    # 短标题与正文仍分行，不粘成一行
    assert "拆分开发采用" not in out.replace(" ", "")
    assert "## 教育经历" in out


def test_normalize_work_duties_like_tuhaipeng() -> None:
    raw = """工作经历
2024 年9 月至2026 年7 月 中网智通（南昌）技术有限公司
工作职责：
1.负责算法设计与实现。
2.负责大模型微调。
"""
    out = normalize_resume_body_rules(raw)
    assert "## 工作经历" in out
    assert "### 2024 年 9 月至 2026 年 7 月 中网智通（南昌）技术有限公司" in out
    assert "#### 工作职责" in out
    assert "1. 负责算法设计与实现" in out


def test_normalize_merges_soft_wraps_and_infers_headers() -> None:
    raw = """姓 名：涂海鹏 出生年月：2001.06
民 族：汉 毕业院校：江西中医药大学

●熟练掌握Python，Linux 平台开发和MySQL 数据库,掌握SQL 语言可以在python、MySQL ，milvus，
redis 环境下对数据进行增删改查；
●熟练使用TF-IDF 等常见基线处理
方法；

工作经历

痛点是信息源多、维
度多、且投资建议受强监管。
采用LangGraph 作为Agent 编排框架，通过
5 个专职Agent。
"""
    out = normalize_resume_body_rules(raw)
    assert "## 基本信息" in out
    assert "## 专业技能" in out
    assert "milvus，redis" in out.replace(" ", "")
    assert "基线处理方法" in out.replace(" ", "")
    assert "信息源多、维度多" in out.replace(" ", "")
    assert "框架，通过5" in out.replace(" ", "") or "框架，通过 5" in out
    assert "5. 个专职" not in out


def test_normalize_default_path_uses_injected_assessor() -> None:
    raw = "姓名：测\n专业技能\n熟练 Python"
    out, method, note = normalize_resume_body(
        raw, assessor=lambda t: "## 基本信息\n\n测\n## 专业技能\n\n- 熟练 Python"
    )
    assert method == "injected"
    assert note is None
    assert "## 基本信息" in out
    assert "- 熟练 Python" in out


def _long_extracted_resume() -> str:
    skills = "熟悉 LangGraph、RAG、Milvus 与 FastAPI。" * 8
    project = (
        "项目经历\n智能客服 Agent 2025.01-至今\n"
        "项目背景：面向客服场景搭建多 Agent 工作流。\n"
        "个人职责：负责编排、检索与评测。\n"
        "项目成果：召回率提升到 90%。\n"
    ) * 4
    return (
        "姓名：测\n求职意向：大模型应用开发工程师\n"
        "专业技能\n"
        f"{skills}\n"
        "工作经历\n2024.01-至今 某公司 工程师\n- 做 RAG 与 Agent。\n"
        f"{project}"
    )


def test_reject_thinking_dump() -> None:
    reason = llm_normalize_rejection_reason(
        _long_extracted_resume(),
        "我们需要避免前言后语。\n现在检查可能缺失的文本：\n"
        "现在确定所有内容无遗漏：\n现在逐段输出设计：",
    )
    assert reason is not None
    assert "思考过程" in reason


def test_reject_placeholder_and_too_short() -> None:
    source = _long_extracted_resume()
    assert llm_normalize_rejection_reason(
        source, "## 基本信息\n... list\n## 项目经历\n### ...\n"
    )
    short = "## 基本信息\n\n测\n求职意向：大模型应用开发工程师\n电话：13800000000"
    reason = llm_normalize_rejection_reason(source, short)
    assert reason is not None
    assert "过短" in reason


def test_thinking_dump_falls_back_to_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _long_extracted_resume()

    def _dump(**_kwargs: object) -> str:
        return (
            "”。直接用Markdown。\n我们需要避免前言后语。\n"
            "现在检查可能缺失的文本：\n现在逐段输出设计："
        )

    monkeypatch.setattr("employ_guard.parse_resume.chat_completion", _dump)
    out, method, note = normalize_resume_body(source)
    assert method == "rules_fallback"
    assert note is not None
    assert "思考过程" in note or "开头不像" in note
    assert "智能客服" in out
    assert "我们需要避免前言后语" not in out


def test_too_short_normalize_falls_back_to_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _long_extracted_resume()

    def _short(**_kwargs: object) -> str:
        return "## 基本信息\n\n测\n求职意向：大模型应用开发工程师"

    monkeypatch.setattr("employ_guard.parse_resume.chat_completion", _short)
    out, method, note = normalize_resume_body(source)
    assert method == "rules_fallback"
    assert note is not None
    assert "过短" in note
    assert "智能客服" in out


def test_normalize_parsed_fields_marks_empty_incomplete() -> None:
    fields = normalize_parsed_fields({"name": None, "projects": [], "work_experience": []})
    assert fields["parse_incomplete"] is True


def test_infer_age_from_birth_year() -> None:
    assert (
        infer_age_from_birth(
            "1995/07 mlh@163.com\n麦丽华 求职意向：大模型工程师", today_year=2026
        )
        == "31"
    )
    assert (
        infer_age_from_birth("出生年月：2001.06\n姓名：涂海鹏", today_year=2026) == "25"
    )
    # 文首无出生年时，勿把教育入学年当出生年
    assert (
        infer_age_from_birth(
            "姓名：测\n\n教育经历\n珠海城市职业技术学院 2016/09 - 2019/06",
            today_year=2026,
        )
        is None
    )


def test_normalize_fills_age_from_birth_when_missing() -> None:
    text = "1995/07 mlh@163.com\n麦丽华 求职意向：大模型工程师\n广东茂名"
    fields = normalize_parsed_fields(
        {
            "name": "麦丽华",
            "age": None,
            "projects": [{"name": "x"}],
            "work_experience": [],
        },
        normalized_text=text,
        today_year=2026,
    )
    assert fields["age"] == "31"
    assert any("出生年" in n for n in fields["parse_notes"])


def test_parse_from_resume_md(tmp_path: Path) -> None:
    md = tmp_path / "demo.resume.md"
    md.write_text(
        "# 简历文本\n\n姓名：柯文\n手机：19173814213\n智能客服 RAG\n",
        encoding="utf-8",
    )
    result = parse_resume(
        md,
        root=tmp_path,
        normalize_assessor=normalize_resume_body_rules,
        parse_assessor=_fake_assessor,
    )
    assert result.norm_md.is_file()
    assert result.parsed_md.is_file()
    assert result.parsed_json.is_file()
    assert result.normalize_method == "injected"
    assert "不改字义" in result.norm_md.read_text(encoding="utf-8")
    report = result.parsed_md.read_text(encoding="utf-8")
    assert "柯文" in report
    assert "不判断能不能投" in report or "不能投" in report
    data = json.loads(result.parsed_json.read_text(encoding="utf-8"))
    assert data["tool"] == "parse-resume"
    assert data["judges_content"] is False
    assert data["method"]["normalize"] == "injected"
    assert data["fields"]["name"] == "柯文"
    assert data["fields"]["target_role"] is None
    assert result.parse_incomplete is False


def test_parse_from_pdf_after_read_resume(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "ok.pdf"
    _write_pdf(pdf, "姓名 测试 RAG Agent")
    extract_resume_text(pdf, root=tmp_path)
    result = parse_resume(
        pdf,
        root=tmp_path,
        normalize_assessor=normalize_resume_body_rules,
        parse_assessor=_fake_assessor,
    )
    assert result.parsed_json.is_file()


def test_empty_body_fails(tmp_path: Path) -> None:
    md = tmp_path / "empty.resume.md"
    md.write_text("   \n", encoding="utf-8")
    with pytest.raises(ParseResumeError, match="为空"):
        parse_resume(
            md,
            root=tmp_path,
            normalize_assessor=normalize_resume_body_rules,
            parse_assessor=_fake_assessor,
        )


def test_cli_parse_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    md = tmp_path / "cli.resume.md"
    md.write_text("姓名：柯文\n", encoding="utf-8")
    monkeypatch.setattr(
        "employ_guard.cli.parse_resume",
        lambda source: parse_resume(
            source,
            root=tmp_path,
            normalize_assessor=normalize_resume_body_rules,
            parse_assessor=_fake_assessor,
        ),
    )
    result = runner.invoke(app, ["parse-resume", str(md)])
    assert result.exit_code == 0
    assert "字段抽取" in result.stdout
    assert "柯文" in result.stdout
