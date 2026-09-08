import json

import pytest

from backend import llm_client


def test_parse_json_handles_code_fence():
    raw = "```json\n{\"a\": 1}\n```"
    assert llm_client._parse_json(raw) == {"a": 1}


def test_extract_success(monkeypatch):
    payload = json.dumps({
        "course_name": "CS 101",
        "summaries": ["本周要点。"],
        "calendar_events": [{"title": "Quiz", "start": "2026-08-31T14:00:00",
                             "end": "2026-08-31T15:00:00", "location": "A101", "notes": ""}],
        "reminders": [{"title": "HW3", "due_date": "2026-09-02T23:59:00", "notes": ""}],
    })
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: payload)
    result = llm_client.extract_course_summary("https://llm/v1", "key", "m", "CS 101", [{"title": "x", "message": "y", "posted_at": ""}])
    assert result["summaries"] == ["本周要点。"]
    assert result["calendar_events"][0]["location"] == "A101"
    assert result["reminders"][0]["due_date"] == "2026-09-02T23:59:00"


def test_extract_returns_summaries_field(monkeypatch):
    payload = json.dumps({
        "course_name": "CS 101",
        "summaries": ["Weekly summary."],
        "calendar_events": [], "reminders": [],
    })
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: payload)
    result = llm_client.extract_course_summary(
        "https://llm/v1", "key", "m", "CS 101", [{"title": "x", "message": "y", "posted_at": ""}])
    assert result["summaries"] == ["Weekly summary."]
    assert "summary" not in result      # 不再有旧的单一 summary 键


def test_summaries_align_with_announcements(monkeypatch):
    """模型漏条时用原文摘录补齐，保证 summaries 与公告逐条对齐（顺序一致）。"""
    payload = json.dumps({
        "course_name": "CS 101",
        "summaries": ["第一条的总结"],
        "calendar_events": [], "reminders": [],
    })
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: payload)
    anns = [{"title": "A", "message": "msgA", "posted_at": ""},
            {"title": "B", "message": "msgB", "posted_at": ""}]
    result = llm_client.extract_course_summary("https://llm/v1", "key", "m", "CS 101", anns)
    assert result["summaries"][0] == "第一条的总结"
    assert "B" in result["summaries"][1]      # 缺失的第二条 → 原文摘录兜底
    assert len(result["summaries"]) == len(anns)


def test_extract_fallback_on_persistent_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr(llm_client, "_call_chat", boom)
    result = llm_client.extract_course_summary("https://llm/v1", "key", "m", "CS 101", [{"title": "T", "message": "M", "posted_at": ""}])
    assert result["warning"] == "总结失败，已展示公告原文"
    assert result["calendar_events"] == []
    assert "T" in result["summaries"][0]


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


def test_summarize_syllabus_returns_structured(monkeypatch):
    """json_mode 打开：返回解析后的 {summary, calendar_events, reminders}。"""
    captured = {}
    payload = json.dumps({
        "summary": "- Objective: learn Python\n- Grading: 40% exam",
        "calendar_events": [{"title": "Midterm", "start": "2026-11-12T14:00:00",
                             "end": "2026-11-12T17:00:00", "location": "LT-1", "notes": ""}],
        "reminders": [{"title": "HW1", "due_date": "2026-09-15T23:59:00", "notes": ""}],
    })
    def fake_call(base, key, model, prompt, json_mode=True):
        captured["json_mode"] = json_mode
        captured["prompt"] = prompt
        return payload
    monkeypatch.setattr(llm_client, "_call_chat", fake_call)
    out = llm_client.summarize_syllabus(
        "https://llm/v1", "key", "m", "CS 101", "<p>syllabus</p>", language="zh")
    assert out == {"summary": "- Objective: learn Python\n- Grading: 40% exam",
                   "calendar_events": [{"title": "Midterm", "start": "2026-11-12T14:00:00",
                                         "end": "2026-11-12T17:00:00",
                                         "location": "LT-1", "notes": ""}],
                   "reminders": [{"title": "HW1", "due_date": "2026-09-15T23:59:00",
                                  "notes": ""}]}
    assert captured["json_mode"] is True
    assert "CS 101" in captured["prompt"] and "Chinese" in captured["prompt"]


def test_summarize_syllabus_truncates_long(monkeypatch):
    """超长 syllabus 截断到约 20000 字符，防 token 超限（总 prompt 含 schema 指令开销）。"""
    captured = {}
    def fake_call(base, key, model, prompt, json_mode=True):
        captured["prompt"] = prompt
        return json.dumps({"summary": "ok", "calendar_events": [], "reminders": []})
    monkeypatch.setattr(llm_client, "_call_chat", fake_call)
    llm_client.summarize_syllabus("u", "k", "m", "C", "x" * 50000)
    assert "x" * 20000 in captured["prompt"]                 # 正文被压到 _MAX_SYLLABUS
    assert "x" * 20001 not in captured["prompt"]
    assert "…(已截断)" in captured["prompt"]
    assert len(captured["prompt"]) < 22000                   # 20000 + 指令开销富余


def test_summarize_syllabus_raises_after_retry(monkeypatch):
    """连续失败重试一次后抛异常（不静默降级成原文）。"""
    def boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr(llm_client, "_call_chat", boom)
    with pytest.raises(RuntimeError):
        llm_client.summarize_syllabus("u", "k", "m", "C", "text")


def test_summarize_syllabus_non_json_raises(monkeypatch):
    """非 JSON 输出重试一次后仍失败 → 抛异常（不静默降级成原文）。"""
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: "not json at all")
    with pytest.raises(RuntimeError):
        llm_client.summarize_syllabus("u", "k", "m", "C", "text")


# ---- DeepSeek 第一方 API：旧模型名 2026-07-24 下线迁移 + V4 思考模式显式关闭 ----

class _FakeResp:
    ok = True
    text = ""
    status_code = 200
    reason = "OK"
    def raise_for_status(self):
        pass
    def json(self):
        return {"choices": [{"message": {"content": json.dumps(
            {"summary": "ok", "calendar_events": [], "reminders": []})}}]}


def test_deepseek_alias_migrated_and_thinking_off(monkeypatch):
    """第一方 deepseek 上旧名 deepseek-chat → 请求发 deepseek-v4-flash 并显式关思考。"""
    captured = {}
    def fake_post(url, json, headers, timeout):
        captured["payload"] = json
        return _FakeResp()
    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    llm_client.summarize_syllabus(
        "https://api.deepseek.com/v1", "key", "deepseek-chat", "CS 101", "syllabus")
    p = captured["payload"]
    assert p["model"] == "deepseek-v4-flash"
    assert p["thinking"] == {"type": "disabled"}
    assert p["response_format"] == {"type": "json_object"}


def test_deepseek_reasoner_alias_migrated(monkeypatch):
    captured = {}
    def fake_post(url, json, headers, timeout):
        captured["payload"] = json
        return _FakeResp()
    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    llm_client.extract_course_summary(
        "https://api.deepseek.com", "key", "deepseek-reasoner", "CS 101",
        [{"title": "T", "message": "M", "posted_at": ""}])
    assert captured["payload"]["model"] == "deepseek-v4-flash"
    assert captured["payload"]["thinking"] == {"type": "disabled"}


def test_non_deepseek_host_untouched(monkeypatch):
    """非 deepseek 第一方主机：模型名原样、不加 thinking 字段（第三方网关语义保留）。"""
    captured = {}
    def fake_post(url, json, headers, timeout):
        captured["payload"] = json
        return _FakeResp()
    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    llm_client.extract_course_summary(
        "https://llm.example/v1", "key", "deepseek-chat", "CS 101",
        [{"title": "T", "message": "M", "posted_at": ""}])
    p = captured["payload"]
    assert p["model"] == "deepseek-chat"
    assert "thinking" not in p
