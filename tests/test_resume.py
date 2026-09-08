"""老师命令 resume：布局路径与文本路径并行，已有结果则跳过。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from employ_guard.cli import app
from employ_guard.resume import STEP_ORDER, ResumeError, run_resume

runner = CliRunner()


def _write_pdf(
    path: Path,
    text: str = "求职意向：大模型工程师\nAgent RAG project",
) -> None:
    """夹具 PDF 须含首页岗位类表述，否则 C1 规则层会把注入的 content_pass 打回。"""
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    # china-s：内置中文字体，避免默认拉丁字体丢汉字导致 C1 误伤编排单测
    page.insert_text((72, 72), text, fontsize=14, fontname="china-s")
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()


def _pass_visual(_pages: list[Path]) -> dict:
    return {
        "pass_line": [
            {"id": "P2", "pass": True, "note": "无溢出", "method": "vision"},
            {"id": "P3", "pass": True, "note": "可扫读", "method": "vision"},
            {"id": "P4", "pass": True, "note": "留白正常", "method": "vision"},
            {"id": "P5", "pass": True, "note": "无水印", "method": "vision"},
        ],
        "level_line": [],
        "defects": [
            {"code": "leading_punct", "found": False, "pages": [], "note": ""},
            {"code": "bullet_inconsistent", "found": False, "pages": [], "note": ""},
            {"code": "alignment", "found": False, "pages": [], "note": ""},
            {"code": "tight_spacing", "found": False, "pages": [], "note": ""},
            {"code": "font_inconsistent", "found": False, "pages": [], "note": ""},
        ],
    }


def _fail_visual(_pages: list[Path]) -> dict:
    data = _pass_visual(_pages)
    data["pass_line"][2] = {
        "id": "P4",
        "pass": False,
        "note": "文字墙",
        "method": "vision",
    }
    return data


def _fail_writing(_text: str) -> dict:
    return {
        "llm_findings": [
            {
                "id": "W1",
                "line": 1,
                "excerpt": "示例",
                "note": "错别字示例",
            }
        ]
    }


def _pass_writing(_text: str) -> dict:
    return {"llm_findings": []}


def _pass_content(_text: str, _job: str | None) -> dict:
    pass_line = [
        {"id": f"C{i}", "pass": True, "doubtful": False, "note": "ok", "method": "llm"}
        for i in range(1, 10)
    ]
    return {
        "scope": "测试范围",
        "pass_line": pass_line,
        "level_line": [],
        "main_blockers": [],
    }


def _fail_content(_text: str, _job: str | None) -> dict:
    data = _pass_content(_text, _job)
    # C9 不在 skills/projects 汇总覆盖范围内，保证编排单测能稳定得到 content_pass=false
    data["pass_line"][8] = {
        "id": "C9",
        "pass": False,
        "doubtful": False,
        "note": "工作履历无 AI 主业支撑",
        "method": "llm",
    }
    data["main_blockers"] = ["C9：工作履历无 AI 主业支撑"]
    return data


def _questions(_text: str, _job: str | None) -> dict:
    return {
        "scope": "通用技术面，不是某家公司的真题",
        "projects": [
            {
                "name": "主项目",
                "why_selected": "测试用。",
                "basics": [
                    {
                        "id": "P1-B1",
                        "question": "主项目怎么做检索？",
                        "focus": "讲清召回。",
                        "follow_ups": ["为何选混合检索？"],
                    }
                ],
                "deep_dives": [
                    {
                        "id": "P1-D1",
                        "question": "指标掉了怎么定位？",
                        "focus": "可观测。",
                        "why": "简历写了评测。",
                    }
                ],
            }
        ],
    }


def _pass_projects(_text: str, _job: str | None) -> dict:
    return {
        "scope": "测试范围",
        "summary": "主项目宜作练习重点。",
        "projects": [
            {
                "name": "主项目",
                "time_range": "2024.01-2025.01",
                "role": "primary",
                "why_selected": "测试",
                "value_tier": "high",
                "value_evidence": "有链路",
                "difficulty_tier": "mid",
                "difficulty_evidence": "有工程",
                "structure_gaps": [],
                "fixes": [],
            }
        ],
    }


def _pass_normalize(text: str) -> str:
    return text


def _pass_parse(_text: str) -> dict:
    return {
        "name": "测",
        "gender": None,
        "hometown": None,
        "age": None,
        "target_role": "大模型工程师",
        "phone": None,
        "email": None,
        "skills": ["RAG", "Agent"],
        "work_experience": [],
        "projects": [
            {
                "start": "2024.01",
                "end": "2025.01",
                "name": "主项目",
                "role": "开发",
                "content": "RAG",
            }
        ],
        "education": [],
        "self_evaluation": None,
        "parse_incomplete": False,
        "parse_notes": [],
    }


def _pass_profile(_text: str, _hint: dict | None) -> dict:
    return {
        "status": "pass",
        "target_role": "大模型工程师",
        "homepage_evidence": "求职意向：大模型工程师",
        "conflict_note": None,
        "fixes": [],
        "notes": [],
    }


def _pass_skills(_text: str, _hint: dict | None) -> dict:
    return {
        "status": "pass",
        "target_role": "大模型工程师",
        "grouping": {"status": "pass", "note": "已分组"},
        "role_alignment": {"status": "pass", "note": "同向"},
        "project_coverage": {"status": "pass", "note": "有支撑"},
        "market_alignment": {"status": "pass", "note": "可见高频"},
        "skill_order": {"status": "pass", "note": "相关靠前"},
        "verbosity_note": None,
        "fixes": [],
        "notes": [],
    }


def _inject(**kwargs):  # type: ignore[no-untyped-def]
    return {
        "visual_assessor": _pass_visual,
        "normalize_assessor": _pass_normalize,
        "parse_assessor": _pass_parse,
        "profile_assessor": _pass_profile,
        "writing_assessor": _pass_writing,
        "skills_assessor": _pass_skills,
        "projects_assessor": _pass_projects,
        "content_assessor": _pass_content,
        "questions_assessor": _questions,
        **kwargs,
    }


def test_help_lists_resume() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "resume" in result.stdout
    detail = runner.invoke(app, ["resume", "--help"])
    assert detail.exit_code == 0
    assert "跳过" in detail.stdout or "顺序" in detail.stdout


def test_rejects_non_pdf(tmp_path: Path) -> None:
    fake = tmp_path / "a.docx"
    fake.write_text("x", encoding="utf-8")
    with pytest.raises(ResumeError, match="不是 PDF"):
        run_resume(fake, root=tmp_path, **_inject())


def test_full_pass_exit_0(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "demo.pdf"
    _write_pdf(pdf)
    result = run_resume(pdf, root=tmp_path, **_inject())
    assert result.exit_code == 0
    assert result.layout_pass is True
    assert result.content_pass is True
    assert result.questions_count == 2
    assert [s.name for s in result.steps] == list(STEP_ORDER)
    assert all(s.status == "ran" for s in result.steps if s.name != "draft-questions")
    assert result.steps[-1].name == "draft-questions"
    assert result.steps[-1].status == "ran"
    assert (result.run_dir / "pages").is_dir()
    assert (result.run_dir / "demo.layout.json").is_file()
    assert (result.run_dir / "demo.resume.md").is_file()
    assert (result.run_dir / "demo.parsed.json").is_file()
    assert (result.run_dir / "demo.profile.json").is_file()
    assert (result.run_dir / "demo.writing.json").is_file()
    assert (result.run_dir / "demo.skills.json").is_file()
    assert (result.run_dir / "demo.projects.json").is_file()
    assert (result.run_dir / "demo.judge.json").is_file()
    assert (result.run_dir / "demo.questions.json").is_file()


def test_layout_and_text_paths_overlap_wall_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """布局路径与文本路径应重叠执行，墙钟短于两路串行之和。"""
    pdf = tmp_path / "data" / "input" / "parallel.pdf"
    _write_pdf(pdf)

    def _slow_visual(pages: list[Path]) -> dict:
        time.sleep(0.15)
        return _pass_visual(pages)

    def _slow_content(text: str, job_description: str | None = None) -> dict:
        time.sleep(0.15)
        return _pass_content(text, job_description)

    started = time.perf_counter()
    result = run_resume(
        pdf,
        root=tmp_path,
        **_inject(visual_assessor=_slow_visual, content_assessor=_slow_content),
    )
    wall_ms = (time.perf_counter() - started) * 1000
    assert result.exit_code == 0
    assert [s.name for s in result.steps] == list(STEP_ORDER)
    # 若串行，仅这两步就约 300ms；并行应明显更短
    assert wall_ms < 280, f"expected overlap, wall={wall_ms:.0f}ms"


def test_layout_fail_still_finishes_exit_2(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "weak.pdf"
    _write_pdf(pdf)
    result = run_resume(pdf, root=tmp_path, **_inject(visual_assessor=_fail_visual))
    assert result.exit_code == 2
    assert result.layout_pass is False
    assert result.content_pass is True
    assert result.questions_count == 2
    assert (result.run_dir / "weak.questions.json").is_file()


def test_content_fail_exit_2(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "content.pdf"
    _write_pdf(pdf)
    result = run_resume(pdf, root=tmp_path, **_inject(content_assessor=_fail_content))
    assert result.exit_code == 2
    assert result.layout_pass is True
    assert result.content_pass is False
    assert result.questions_count == 2


def test_writing_fail_exit_2(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "writing-fail.pdf"
    _write_pdf(pdf)
    result = run_resume(pdf, root=tmp_path, **_inject(writing_assessor=_fail_writing))
    assert result.exit_code == 2
    assert result.writing_pass is False
    assert result.content_pass is True
    assert result.layout_pass is True
    assert result.brief_path is not None
    assert "计入总出口" in result.brief_path.read_text(encoding="utf-8")
    judge = json.loads(
        (result.run_dir / "writing-fail.judge.json").read_text(encoding="utf-8")
    )
    assert judge.get("writing_pass") is False
    assert judge.get("overall_pass") is False


def test_skip_existing_artifacts(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "again.pdf"
    _write_pdf(pdf)
    first = run_resume(pdf, root=tmp_path, **_inject())
    assert first.exit_code == 0
    assert all(s.status == "ran" for s in first.steps)

    second = run_resume(pdf, root=tmp_path, **_inject())
    assert second.exit_code == 0
    assert all(s.status == "skipped" for s in second.steps)
    assert second.layout_pass is True
    assert second.content_pass is True
    assert second.questions_count == 2


def test_force_reruns_even_when_hash_matches(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "force.pdf"
    _write_pdf(pdf)
    first = run_resume(pdf, root=tmp_path, **_inject())
    assert all(s.status == "ran" for s in first.steps)

    second = run_resume(pdf, root=tmp_path, force=True, **_inject())
    assert second.exit_code == 0
    assert all(s.status == "ran" for s in second.steps)
    assert any("强制重跑" in (s.detail or "") for s in second.steps)


def test_pdf_change_invalidates_skip(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "changed.pdf"
    _write_pdf(pdf, "求职意向：大模型工程师\nversion-one")
    first = run_resume(pdf, root=tmp_path, **_inject())
    assert all(s.status == "ran" for s in first.steps)
    from employ_guard.resume import _sha256_file

    old_sha = _sha256_file(pdf)

    _write_pdf(pdf, "求职意向：大模型工程师\nversion-two-changed-content")
    new_sha = _sha256_file(pdf)
    assert old_sha != new_sha

    second = run_resume(pdf, root=tmp_path, **_inject())
    assert second.exit_code == 0
    assert all(s.status == "ran" for s in second.steps)
    assert any("PDF 已变更" in (s.detail or "") for s in second.steps)
    resume_json = json.loads(
        (second.run_dir / "changed.resume.json").read_text(encoding="utf-8")
    )
    assert resume_json["sha256"] == new_sha


def test_no_questions_flag(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "nq.pdf"
    _write_pdf(pdf)
    result = run_resume(pdf, root=tmp_path, skip_questions=True, **_inject())
    assert result.exit_code == 0
    assert result.questions_count is None
    q_step = next(s for s in result.steps if s.name == "draft-questions")
    assert q_step.status == "disabled"
    assert not (result.run_dir / "nq.questions.json").is_file()
    assert result.brief_path is not None
    assert result.brief_path.is_file()


def test_triage_skips_writing_and_questions_writes_brief(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "triage.pdf"
    _write_pdf(pdf)
    result = run_resume(pdf, root=tmp_path, triage=True, **_inject())
    assert result.exit_code == 0
    assert result.triage is True
    by_name = {s.name: s for s in result.steps}
    assert by_name["parse-resume"].status == "ran"
    assert by_name["check-profile"].status == "ran"
    assert by_name["check-writing"].status == "disabled"
    assert by_name["check-skills"].status == "disabled"
    assert by_name["review-projects"].status == "disabled"
    assert by_name["draft-questions"].status == "disabled"
    assert (result.run_dir / "triage.parsed.json").is_file()
    assert (result.run_dir / "triage.profile.json").is_file()
    assert not (result.run_dir / "triage.writing.json").is_file()
    assert not (result.run_dir / "triage.skills.json").is_file()
    assert not (result.run_dir / "triage.projects.json").is_file()
    assert not (result.run_dir / "triage.questions.json").is_file()
    assert result.writing_pass is None
    assert result.questions_count is None
    writing = next(s for s in result.steps if s.name == "check-writing")
    questions = next(s for s in result.steps if s.name == "draft-questions")
    assert writing.status == "disabled"
    assert questions.status == "disabled"
    assert result.brief_path is not None
    text = result.brief_path.read_text(encoding="utf-8")
    assert "教练摘要" in text
    assert "排查" in text
    assert "排版：达标" in text
    assert "内容：达标" in text
    assert not (result.run_dir / "triage.writing.json").is_file()
    assert not (result.run_dir / "triage.questions.json").is_file()


def test_triage_brief_lists_actions_when_layout_fails(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "triage-fail.pdf"
    _write_pdf(pdf)
    result = run_resume(
        pdf,
        root=tmp_path,
        triage=True,
        **_inject(visual_assessor=_fail_visual),
    )
    assert result.exit_code == 2
    assert result.layout_pass is False
    assert result.actions
    assert len(result.actions) <= 3
    assert result.brief_path is not None
    body = result.brief_path.read_text(encoding="utf-8")
    assert "建议先改" in body
    assert result.actions[0] in body


def test_full_pass_also_writes_brief(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "brief-full.pdf"
    _write_pdf(pdf)
    result = run_resume(pdf, root=tmp_path, **_inject())
    assert result.brief_path is not None
    assert "完整" in result.brief_path.read_text(encoding="utf-8")


def test_progress_emits_start_and_finish(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "progress.pdf"
    _write_pdf(pdf)
    messages: list[str] = []
    result = run_resume(pdf, root=tmp_path, progress=messages.append, **_inject())
    assert result.exit_code == 0
    assert any("正在 PDF 出图" in m for m in messages)
    assert any("正在 查排版" in m for m in messages)
    assert any("正在 判能不能投" in m for m in messages)
    assert any("[1/10]" in m for m in messages)
    assert any("[3/10]" in m for m in messages)
    assert any("· 已跑 ·" in m and "ms" in m for m in messages)
    assert all(s.elapsed_ms is not None for s in result.steps)
    # 10 steps × (start + finish)
    assert len(messages) == 20


def test_cli_resume_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    pdf = tmp_path / "data" / "input" / "cli.pdf"
    _write_pdf(pdf)

    def _fake(source: Path, **kwargs):  # type: ignore[no-untyped-def]
        return run_resume(source, root=tmp_path, **_inject(**kwargs))

    monkeypatch.setattr("employ_guard.cli.run_resume", _fake)
    result = runner.invoke(app, ["resume", str(pdf)])
    assert result.exit_code == 0, result.output
    assert "排版：达标" in result.stdout
    assert "内容：达标" in result.stdout
    assert "练习题" in result.stdout


def test_cli_resume_layout_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    pdf = tmp_path / "data" / "input" / "cli-fail.pdf"
    _write_pdf(pdf)

    def _fake(source: Path, **kwargs):  # type: ignore[no-untyped-def]
        return run_resume(
            source,
            root=tmp_path,
            **_inject(visual_assessor=_fail_visual, **kwargs),
        )

    monkeypatch.setattr("employ_guard.cli.run_resume", _fake)
    result = runner.invoke(app, ["resume", str(pdf)])
    assert result.exit_code == 2, result.output
    assert "排版：未达标" in result.output
    assert "未过合格线" in result.output


def test_cli_rejects_non_pdf(tmp_path: Path) -> None:
    fake = tmp_path / "x.docx"
    fake.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["resume", str(fake)])
    assert result.exit_code == 1
    assert "不是 PDF" in result.output
    assert "不能投" not in result.output
