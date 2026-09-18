import json

import pytest
import requests

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


# --- summarize_mail ---

def _mail(i, subject="作业", body="正文"):
    return {"index": i, "subject": subject, "sender": "李老師",
            "received": "2026-09-17T08:31:00Z", "body": body}


def test_trim_bodies_preserves_count():
    """条数永远不变 —— 对齐靠 index，丢一条就整体错位。"""
    mails = [_mail(i, body="x" * 50000) for i in range(4)]
    out = llm_client.trim_bodies(mails, budget=2000)
    assert len(out) == 4
    assert [m["index"] for m in out] == [0, 1, 2, 3]
    assert all(len(m["body"]) <= 500 for m in out)


def test_trim_bodies_caps_long_body():
    out = llm_client.trim_bodies([_mail(0, body="y" * 99999)])
    assert len(out[0]["body"]) == llm_client._MAX_BODY


def test_trim_bodies_never_shrinks_below_floor():
    """邮件极多时也不能把每封压到 0 字 —— 有下限，宁可超预算。"""
    mails = [_mail(i, body="z" * 10000) for i in range(500)]
    out = llm_client.trim_bodies(mails, budget=20000)
    assert all(len(m["body"]) == llm_client._MIN_BODY for m in out)


def test_trim_bodies_does_not_mutate_input():
    m = _mail(0, body="q" * 99999)
    llm_client.trim_bodies([m])
    assert len(m["body"]) == 99999


def test_summarize_mail_happy_path(monkeypatch):
    payload = {"groups": [{"course": "CS1315", "types": [
        {"type": "作业", "items": [{"mail_index": 0, "summary": "交实验报告",
            "calendar_events": [], "reminders": [
                {"title": "交报告", "due_date": "2026-09-25T23:59:00", "notes": ""}]}]}]}]}
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: json.dumps(payload))
    r = llm_client.summarize_mail("u", "k", "m", [_mail(0)])
    assert r["warnings"] == []
    item = r["groups"][0]["types"][0]["items"][0]
    assert item["mail_index"] == 0
    assert item["reminders"][0]["title"] == "交报告"


def test_summarize_mail_drops_out_of_range_index(monkeypatch):
    """AI 编出的越界 index 必须丢 —— 绝不能把内容挂到不存在的邮件上。"""
    payload = {"groups": [{"course": "C", "types": [{"type": "T", "items": [
        {"mail_index": 99, "summary": "幽灵", "calendar_events": [], "reminders": []}]}]}]}
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: json.dumps(payload))
    r = llm_client.summarize_mail("u", "k", "m", [_mail(0)])
    assert r["groups"] == []
    assert r["warnings"]          # 丢了东西必须留痕，不静默


def test_summarize_mail_warns_when_ai_covers_nothing(monkeypatch):
    """模型给了结构合法但内容不可用的分组 → 必须留痕，绝不能和「邮箱是空的」混为一谈。"""
    payload = {"groups": [{"course": "CS1315", "types": [
        {"type": "作业", "items": ["交实验报告"]}]}]}
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: json.dumps(payload))
    r = llm_client.summarize_mail("u", "k", "m", [_mail(0)])
    assert r["groups"] == []
    assert r["warnings"]


def test_summarize_mail_drops_bool_index(monkeypatch):
    """JSON 的 true 在 Python 里是 int 子类 → 不显式挡掉就会被当成 index 1，
    把 AI 编的内容挂到第 2 封真邮件上。用两封邮件才能把这个变异钉红。"""
    payload = {"groups": [{"course": "C", "types": [{"type": "T", "items": [
        {"mail_index": True, "summary": "幽灵", "calendar_events": [], "reminders": []}]}]}]}
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: json.dumps(payload))
    r = llm_client.summarize_mail("u", "k", "m", [_mail(0), _mail(1)])
    assert r["groups"] == []
    assert r["warnings"]


def test_summarize_mail_falls_back_to_excerpt_for_bad_summary(monkeypatch):
    payload = {"groups": [{"course": "C", "types": [{"type": "T", "items": [
        {"mail_index": 0, "summary": 12345, "calendar_events": [], "reminders": []}]}]}]}
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: json.dumps(payload))
    r = llm_client.summarize_mail("u", "k", "m", [_mail(0, body="课程正文内容")])
    s = r["groups"][0]["types"][0]["items"][0]["summary"]
    assert isinstance(s, str) and "课程正文内容" in s


def test_summarize_mail_retries_once_then_warns(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_client, "_call_chat",
                        lambda *a, **k: calls.append(1) or "不是 JSON")
    r = llm_client.summarize_mail("u", "k", "m", [_mail(0)])
    assert len(calls) == 2        # 重试一次
    assert r["groups"] == []
    assert r["warnings"]


def test_summarize_mail_empty_input_skips_llm(monkeypatch):
    called = []
    monkeypatch.setattr(llm_client, "_call_chat",
                        lambda *a, **k: called.append(1) or "{}")
    assert llm_client.summarize_mail("u", "k", "m", []) == {"groups": [], "warnings": []}
    assert called == []


def test_summarize_mail_reports_request_error_not_parse_error(monkeypatch):
    """鉴权/网络失败不能被报成「返回内容无法解析为 JSON」。"""
    def boom(*a, **k):
        raise requests.HTTPError("401 Client Error")
    monkeypatch.setattr(llm_client, "_call_chat", boom)
    r = llm_client.summarize_mail("u", "k", "m", [_mail(0)])
    assert r["groups"] == []
    assert "JSON" not in r["warnings"][0]
