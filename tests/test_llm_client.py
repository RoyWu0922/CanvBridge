import json

import pytest

from backend import llm_client


def test_parse_json_handles_code_fence():
    raw = "```json\n{\"a\": 1}\n```"
    assert llm_client._parse_json(raw) == {"a": 1}


def test_extract_success(monkeypatch):
    payload = json.dumps({
        "course_name": "CS 101",
        "summary": "本周要点。",
        "calendar_events": [{"title": "Quiz", "start": "2026-08-31T14:00:00",
                             "end": "2026-08-31T15:00:00", "location": "A101", "notes": ""}],
        "reminders": [{"title": "HW3", "due_date": "2026-09-02T23:59:00", "notes": ""}],
    })
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: payload)
    result = llm_client.extract_course_summary("https://llm/v1", "key", "m", "CS 101", [{"title": "x", "message": "y", "posted_at": ""}])
    assert result["summary"] == "本周要点。"
    assert result["calendar_events"][0]["location"] == "A101"
    assert result["reminders"][0]["due_date"] == "2026-09-02T23:59:00"


def test_extract_returns_summary_field(monkeypatch):
    payload = json.dumps({
        "course_name": "CS 101",
        "summary": "Weekly summary.",
        "calendar_events": [], "reminders": [],
    })
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: payload)
    result = llm_client.extract_course_summary(
        "https://llm/v1", "key", "m", "CS 101", [{"title": "x", "message": "y", "posted_at": ""}])
    assert result["summary"] == "Weekly summary."
    assert "summary_cn" not in result


def test_extract_fallback_on_persistent_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr(llm_client, "_call_chat", boom)
    result = llm_client.extract_course_summary("https://llm/v1", "key", "m", "CS 101", [{"title": "T", "message": "M", "posted_at": ""}])
    assert result["warning"] == "总结失败，已展示公告原文"
    assert result["calendar_events"] == []
    assert "T" in result["summary"]


def test_build_prompt_includes_announcements():
    prompt = llm_client._build_prompt("CS 101", [{"title": "T", "message": "M", "posted_at": "2026-08-29"}])
    assert "CS 101" in prompt and "T" in prompt and "M" in prompt
    assert "calendar_events" in prompt


def test_language_affects_prompt():
    zh = llm_client._build_prompt("CS 101", [{"title": "T", "message": "M", "posted_at": ""}], language="zh")
    en = llm_client._build_prompt("CS 101", [{"title": "T", "message": "M", "posted_at": ""}], language="en")
    assert "Chinese summary" in zh and "write in Chinese" in zh
    assert "English summary" in en and "write in English" in en
    assert "Chinese" not in en


def test_extract_fallback_on_non_dict_json(monkeypatch):
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: "[1, 2]")
    result = llm_client.extract_course_summary("https://llm/v1", "key", "m", "CS 101", [{"title": "T", "message": "M", "posted_at": ""}])
    assert result["warning"] == "总结失败，已展示公告原文"
    assert result["calendar_events"] == []


def test_summarize_syllabus_returns_text(monkeypatch):
    """json_mode 关掉（plain text），strip 后返回总结。"""
    captured = {}
    def fake_call(base, key, model, prompt, json_mode=True):
        captured["json_mode"] = json_mode
        captured["prompt"] = prompt
        return "  - Objective: learn Python\n- Grading: 40% exam\n"
    monkeypatch.setattr(llm_client, "_call_chat", fake_call)
    out = llm_client.summarize_syllabus(
        "https://llm/v1", "key", "m", "CS 101", "<p>syllabus</p>", language="zh")
    assert out == "- Objective: learn Python\n- Grading: 40% exam"
    assert captured["json_mode"] is False
    assert "CS 101" in captured["prompt"] and "Chinese" in captured["prompt"]


def test_summarize_syllabus_truncates_long(monkeypatch):
    """超长 syllabus 截断到约 20000 字符，防 token 超限。"""
    captured = {}
    def fake_call(base, key, model, prompt, json_mode=True):
        captured["prompt"] = prompt
        return "ok"
    monkeypatch.setattr(llm_client, "_call_chat", fake_call)
    llm_client.summarize_syllabus("u", "k", "m", "C", "x" * 50000)
    assert len(captured["prompt"]) < 21000


def test_summarize_syllabus_raises_after_retry(monkeypatch):
    """连续失败重试一次后抛异常（不静默降级成原文）。"""
    def boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr(llm_client, "_call_chat", boom)
    with pytest.raises(RuntimeError):
        llm_client.summarize_syllabus("u", "k", "m", "C", "text")
