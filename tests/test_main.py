import threading

import pytest
from fastapi.testclient import TestClient

from backend import (apple_script, banweb, canvas_client, credentials, files_downloader,
                     llm_client, mail_client, main)

client = TestClient(main.app)


def test_test_connection_ok(monkeypatch):
    monkeypatch.setattr(canvas_client, "list_courses", lambda u, t: [{"id": 1, "name": "CS 101"}])
    r = client.post("/api/test_connection", json={"canvas_url": "https://x", "canvas_token": "t"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "courses": [{"id": 1, "name": "CS 101"}]}


def test_test_connection_error(monkeypatch):
    monkeypatch.setattr(canvas_client, "list_courses", lambda u, t: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.post("/api/test_connection", json={"canvas_url": "https://x", "canvas_token": "t"})
    assert r.json()["ok"] is False
    assert "boom" in r.json()["error"]


def test_calendars(monkeypatch):
    monkeypatch.setattr(apple_script, "list_calendars", lambda: ["Study", "Work"])
    r = client.post("/api/calendars", json={})
    assert r.json() == {"ok": True, "calendars": ["Study", "Work"]}


def test_reminder_lists(monkeypatch):
    monkeypatch.setattr(apple_script, "list_reminder_lists", lambda: ["提醒", "任务"])
    r = client.post("/api/reminder_lists", json={})
    assert r.json() == {"ok": True, "lists": ["提醒", "任务"]}


def test_sync_announcements_returns_raw_only(monkeypatch):
    """同步只返回原始公告，不调用 LLM。"""
    monkeypatch.setattr(canvas_client, "get_announcements",
                        lambda u, t, ids, a, b: {5: [{"id": 1, "title": "T", "message": "M", "posted_at": "2026-08-01"}]})
    monkeypatch.setattr(canvas_client, "list_courses", lambda u, t: [{"id": 5, "name": "CS 101"}])
    llm_called = []
    monkeypatch.setattr(llm_client, "extract_course_summary",
                        lambda *a, **k: llm_called.append(1) or {})
    body = {"canvas_url": "https://x", "canvas_token": "t", "course_ids": [5],
            "start_date": "2026-08-01", "end_date": "2026-08-31"}
    r = client.post("/api/sync_announcements", json=body)
    courses = r.json()["courses"]
    assert r.json()["ok"] is True
    assert llm_called == []                      # 不再生成总结
    assert courses[0]["course_id"] == 5
    assert courses[0]["course_name"] == "CS 101"
    assert courses[0]["announcements"][0]["title"] == "T"


def test_summarize_course(monkeypatch):
    captured = {}
    def fake(base, key, model, name, anns, language="zh"):
        captured.update(base=base, key=key, model=model, name=name, anns=anns, language=language)
        return {"course_name": name, "summaries": ["要点"], "calendar_events": [{"title": "E"}],
                "reminders": [], "warning": ""}
    monkeypatch.setattr(llm_client, "extract_course_summary", fake)
    body = {"canvas_url": "https://x", "canvas_token": "t", "llm_base_url": "https://llm/v1",
            "llm_api_key": "k", "llm_model": "m", "course_id": 5, "course_name": "CS 101",
            "announcements": [{"title": "T", "message": "M"}], "language": "en"}
    r = client.post("/api/summarize_course", json=body)
    data = r.json()
    assert data["ok"] is True
    assert data["summaries"] == ["要点"]
    assert data["course_id"] == 5
    assert captured["language"] == "en"
    assert captured["anns"] == [{"title": "T", "message": "M"}]


def test_summarize_course_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("llm down")
    monkeypatch.setattr(llm_client, "extract_course_summary", boom)
    r = client.post("/api/summarize_course", json={
        "canvas_url": "https://x", "canvas_token": "t", "llm_base_url": "https://llm/v1",
        "llm_api_key": "k", "llm_model": "m", "course_id": 5, "course_name": "CS 101",
        "announcements": []})
    assert r.json()["ok"] is False
    assert "llm down" in r.json()["error"]


def test_course_detail_ok(monkeypatch):
    monkeypatch.setattr(canvas_client, "get_course",
                        lambda u, t, cid: {"id": cid, "name": "CS 101",
                                           "syllabus_text": "s", "teachers": ["A"]})
    monkeypatch.setattr(canvas_client, "get_modules",
                        lambda u, t, cid: [{"id": 1, "name": "Week 1",
                                            "items": [{"id": 11, "title": "Lecture",
                                                       "type": "Page",
                                                       "url": "https://x/courses/5/pages/11"}]}])
    r = client.post("/api/course_detail",
                    json={"canvas_url": "https://x", "canvas_token": "t", "course_id": 5})
    assert r.json() == {"ok": True,
                        "course": {"id": 5, "name": "CS 101", "syllabus_text": "s",
                                   "teachers": ["A"]},
                        "modules": [{"id": 1, "name": "Week 1",
                                     "items": [{"id": 11, "title": "Lecture", "type": "Page",
                                                "url": "https://x/courses/5/pages/11"}]}],
                        "modules_error": None}


def test_course_detail_modules_failure_keeps_ok(monkeypatch):
    """modules 拉取失败只带 modules_error，不影响详情主数据。"""
    monkeypatch.setattr(canvas_client, "get_course",
                        lambda u, t, cid: {"id": cid, "name": "CS 101",
                                           "syllabus_text": "", "teachers": []})
    def _boom(u, t, cid):
        raise RuntimeError("modules down")
    monkeypatch.setattr(canvas_client, "get_modules", _boom)
    r = client.post("/api/course_detail",
                    json={"canvas_url": "https://x", "canvas_token": "t", "course_id": 5})
    body = r.json()
    assert body["ok"] is True
    assert body["course"]["name"] == "CS 101"
    assert body["modules"] is None
    assert "modules down" in body["modules_error"]


def test_course_detail_error(monkeypatch):
    def boom(u, t, cid):
        raise RuntimeError("boom")
    monkeypatch.setattr(canvas_client, "get_course", boom)
    r = client.post("/api/course_detail",
                    json={"canvas_url": "https://x", "canvas_token": "t", "course_id": 5})
    assert r.json()["ok"] is False
    assert "boom" in r.json()["error"]


def test_assignments_batch_per_course(monkeypatch):
    """单门失败只进 errors，不拖垮整批。"""
    real = {5: [{"id": 1, "name": "HW"}], 6: [{"id": 2, "name": "Proj"}]}
    def fake(u, t, cid):
        if cid == 7:
            raise canvas_client.CanvasError("403 无权限")
        return real.get(cid, [])
    monkeypatch.setattr(canvas_client, "get_assignments", fake)
    r = client.post("/api/assignments", json={"canvas_url": "https://x", "canvas_token": "t",
                                              "course_ids": [5, 7]})
    body = r.json()
    assert body["ok"] is True
    assert body["by_course"]["5"] == real[5]
    assert body["by_course"]["7"] == []
    assert "403" in body["errors"]["7"]


def test_summarize_syllabus_ok(monkeypatch):
    monkeypatch.setattr(canvas_client, "get_course",
                        lambda u, t, cid: {"id": cid, "name": "CS 101",
                                           "syllabus_text": "syllabus text"})
    captured = {}
    result = {"summary": "要点",
              "calendar_events": [{"title": "Midterm", "start": "2026-11-12T14:00:00",
                                   "end": "2026-11-12T17:00:00", "location": "", "notes": ""}],
              "reminders": [{"title": "HW1", "due_date": "2026-09-15T23:59:00", "notes": ""}]}
    def fake(base, key, model, name, text, language="zh"):
        captured.update(name=name, language=language)
        return result
    monkeypatch.setattr(llm_client, "summarize_syllabus", fake)
    r = client.post("/api/summarize_syllabus", json={
        "canvas_url": "https://x", "canvas_token": "t", "llm_base_url": "https://llm/v1",
        "llm_api_key": "k", "llm_model": "m", "course_id": 5, "language": "zh"})
    assert r.json() == {"ok": True, **result}
    assert captured["name"] == "CS 101"
    assert captured["language"] == "zh"




def test_add_calendar_event(monkeypatch):
    called = {}
    def add(calendar_name, title, start, end, location, notes, alert_minutes=None):
        called.update(locals())
    monkeypatch.setattr(apple_script, "add_calendar_event", add)
    r = client.post("/api/add_calendar_event", json={
        "calendar_name": "Study", "title": "Quiz", "start": "2026-08-31T14:00:00",
        "end": "2026-08-31T15:00:00", "location": "A101", "notes": ""})
    assert r.json() == {"ok": True}
    assert called["title"] == "Quiz"
    assert called["alert_minutes"] is None


def test_add_calendar_event_with_alert(monkeypatch):
    called = {}
    def add(calendar_name, title, start, end, location, notes, alert_minutes=None):
        called["alert_minutes"] = alert_minutes
    monkeypatch.setattr(apple_script, "add_calendar_event", add)
    r = client.post("/api/add_calendar_event", json={
        "calendar_name": "Study", "title": "Quiz", "start": "2026-08-31T14:00:00",
        "end": "2026-08-31T15:00:00", "location": "", "notes": "", "alert_minutes": 30})
    assert r.json() == {"ok": True}
    assert called["alert_minutes"] == 30


def test_list_files_and_download(monkeypatch):
    # get_course_files 返回的是已映射字段（content_type 下划线形式）
    files = [{"id": 9, "display_name": "a.pdf", "folder_id": None,
              "content_type": "application/pdf", "size": 1, "url": "http://x/f/9"}]
    folders = []
    monkeypatch.setattr(canvas_client, "list_courses", lambda u, t: [{"id": 5, "name": "CS 101"}])
    monkeypatch.setattr(canvas_client, "get_course_files", lambda u, t, cid: (files, folders))
    r = client.post("/api/list_files", json={"canvas_url": "https://x", "canvas_token": "t",
                                             "course_ids": [5], "download_dir": "/tmp/dl"})
    body = r.json()
    assert body["ok"] is True
    assert body["courses"][0]["files"][0]["display_name"] == "a.pdf"
    assert body["courses"][0]["files"][0]["dest_path"].endswith("a.pdf")

    monkeypatch.setattr(canvas_client, "get_file", lambda u, t, cid, fid: {"url": "http://x/f/9"})
    monkeypatch.setattr(canvas_client, "download_file", lambda u, t, url, dest: None)
    dest = body["courses"][0]["files"][0]["dest_path"]
    r = client.post("/api/download_files", json={"canvas_url": "https://x", "canvas_token": "t",
                                                 "download_dir": "/tmp/dl",
                                                 "items": [{"course_id": 5, "file_id": 9, "dest_path": dest}]})
    dl = r.json()
    assert dl["ok"] is True
    assert dl["downloaded"] == [dest]


def test_download_module_item_ok(tmp_path, monkeypatch):
    """模块文件下载：落到 下载目录/课程/模块/文件名，下载一次即成功。"""
    downloaded = []
    monkeypatch.setattr(canvas_client, "get_file",
                        lambda u, t, cid, fid: {"display_name": "syllabus.pdf", "url": "http://x/dl"})
    monkeypatch.setattr(canvas_client, "download_file",
                        lambda u, t, url, dest: downloaded.append(str(dest)))
    r = client.post("/api/download_module_item", json={
        "canvas_url": "https://x", "canvas_token": "t",
        "download_dir": str(tmp_path), "course_id": 5, "course_name": "CS 101",
        "module_name": "Week 1", "file_id": 9})
    body = r.json()
    assert body["ok"] is True
    assert body["saved"] is False
    assert body["dest_path"].endswith("CS 101/Week 1/syllabus.pdf")
    assert downloaded == [body["dest_path"]]


def test_download_module_item_skips_existing(tmp_path, monkeypatch):
    """目标已存在 → saved True 且不再调 download_file。"""
    dest = tmp_path / "CS 101" / "Week 1" / "syllabus.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_text("old")
    called = []
    monkeypatch.setattr(canvas_client, "get_file",
                        lambda u, t, cid, fid: {"display_name": "syllabus.pdf", "url": "http://x/dl"})
    monkeypatch.setattr(canvas_client, "download_file",
                        lambda u, t, url, d: called.append(d))
    r = client.post("/api/download_module_item", json={
        "canvas_url": "https://x", "canvas_token": "t",
        "download_dir": str(tmp_path), "course_id": 5, "course_name": "CS 101",
        "module_name": "Week 1", "file_id": 9})
    body = r.json()
    assert body["ok"] is True
    assert body["saved"] is True
    assert body["dest_path"] == str(dest)
    assert called == []


def test_download_module_item_get_file_fails(tmp_path, monkeypatch):
    """get_file 异常 → ok False + 错误；不触达下载。"""
    def boom(u, t, cid, fid):
        raise canvas_client.CanvasError("HTTP 403")
    monkeypatch.setattr(canvas_client, "get_file", boom)
    called = []
    monkeypatch.setattr(canvas_client, "download_file",
                        lambda u, t, url, d: called.append(d))
    r = client.post("/api/download_module_item", json={
        "canvas_url": "https://x", "canvas_token": "t",
        "download_dir": str(tmp_path), "course_id": 5, "course_name": "CS 101",
        "module_name": "Week 1", "file_id": 9})
    body = r.json()
    assert body["ok"] is False
    assert "403" in body["error"]
    assert called == []


def test_download_module_item_no_course_name_default(tmp_path, monkeypatch):
    """course_name 缺省 → 回退 Course {course_id}；模块名中的 / 净化成 _。"""
    monkeypatch.setattr(canvas_client, "get_file",
                        lambda u, t, cid, fid: {"filename": "a<b>.pdf", "url": "http://x/dl"})
    monkeypatch.setattr(canvas_client, "download_file", lambda u, t, url, dest: None)
    r = client.post("/api/download_module_item", json={
        "canvas_url": "https://x", "canvas_token": "t",
        "download_dir": str(tmp_path), "course_id": 7,
        "module_name": "A/B", "file_id": 3})
    dest_path = r.json()["dest_path"]
    assert str(tmp_path) in dest_path
    assert dest_path.endswith("Course 7/A_B/a<b>.pdf")   # 文件名只净化 / \ 与前导点
    assert "/A/B/" not in dest_path


def test_module_file_stream_ok(monkeypatch):
    """模块文件 inline 转发：字节 + 真实 content-type 原样回传，不落盘。"""
    got = []
    monkeypatch.setattr(canvas_client, "get_file",
                        lambda u, t, cid, fid: got.append((cid, fid)) or
                        {"url": "https://files.example/dl?sig=abc",
                         "display_name": "x.pdf", "content-type": "application/pdf"})
    monkeypatch.setattr(canvas_client, "stream_file",
                        lambda u, t, url: (chunk for chunk in (b"%PDF-1.4\n", b"data")))
    r = client.post("/api/module_file_stream", json={
        "canvas_url": "https://x", "canvas_token": "t", "course_id": 5, "file_id": 9})
    assert got == [(5, 9)]
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content == b"%PDF-1.4\ndata"


def test_module_file_stream_pdf_fallback_content_type(monkeypatch):
    """content-type 缺失但文件名 .pdf → 按 application/pdf 回传，保证浏览器内联渲染。"""
    monkeypatch.setattr(canvas_client, "get_file",
                        lambda u, t, cid, fid: {"url": "https://files.example/dl",
                                                "filename": "syllabus.pdf"})
    monkeypatch.setattr(canvas_client, "stream_file",
                        lambda u, t, url: (chunk for chunk in (b"pdf",)))
    r = client.post("/api/module_file_stream", json={
        "canvas_url": "https://x", "canvas_token": "t", "course_id": 5, "file_id": 9})
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content == b"pdf"


def test_module_file_stream_get_file_fails(monkeypatch):
    """get_file 抛错 → JSON {ok:false} 而非裸 500。"""
    monkeypatch.setattr(canvas_client, "get_file",
                        lambda u, t, cid, fid: (_ for _ in ()).throw(canvas_client.CanvasError("HTTP 403")))
    r = client.post("/api/module_file_stream", json={
        "canvas_url": "https://x", "canvas_token": "t", "course_id": 5, "file_id": 9})
    body = r.json()
    assert body["ok"] is False
    assert "403" in body["error"]


def test_module_file_stream_missing_url(monkeypatch):
    """get_file 返回无 url → JSON {ok:false}「缺少下载地址」。"""
    monkeypatch.setattr(canvas_client, "get_file",
                        lambda u, t, cid, fid: {"display_name": "x.pdf"})
    r = client.post("/api/module_file_stream", json={
        "canvas_url": "https://x", "canvas_token": "t", "course_id": 5, "file_id": 9})
    body = r.json()
    assert body["ok"] is False
    assert "缺少下载地址" in body["error"]


def test_module_file_stream_fetch_fails(monkeypatch):
    """流式取文件阶段失败（连不上/401）→ 响应头发出前转 JSON {ok:false}。"""
    monkeypatch.setattr(canvas_client, "get_file",
                        lambda u, t, cid, fid: {"url": "https://files.example/dl",
                                                "content-type": "application/pdf"})

    def _boom():
        raise canvas_client.CanvasError("HTTP 401 无权限")
        yield b""

    monkeypatch.setattr(canvas_client, "stream_file", lambda u, t, url: _boom())
    r = client.post("/api/module_file_stream", json={
        "canvas_url": "https://x", "canvas_token": "t", "course_id": 5, "file_id": 9})
    body = r.json()
    assert body["ok"] is False
    assert "401" in body["error"]


def test_banweb_status(monkeypatch):
    monkeypatch.setattr(banweb, "get_status", lambda: {"ok": True, "status": "logged_in"})
    r = client.post("/api/banweb/status", json={})
    assert r.json() == {"ok": True, "status": "logged_in"}


def test_banweb_open_login(monkeypatch):
    called = {}
    monkeypatch.setattr(banweb, "open_login", lambda: called.update(done=True))
    r = client.post("/api/banweb/open_login", json={})
    assert r.json() == {"ok": True}
    assert called.get("done") is True


def test_banweb_open_login_error(monkeypatch):
    def boom():
        raise banweb.BanwebError("无法打开浏览器")
    monkeypatch.setattr(banweb, "open_login", boom)
    r = client.post("/api/banweb/open_login", json={})
    assert r.json()["ok"] is False
    assert "无法打开浏览器" in r.json()["error"]


def test_banweb_terms(monkeypatch):
    monkeypatch.setattr(banweb, "list_terms",
                        lambda: [{"value": "202609", "label": "Semester A 2026/27"}])
    r = client.post("/api/banweb/terms", json={})
    assert r.json()["ok"] is True
    assert r.json()["terms"][0]["value"] == "202609"


def test_banweb_schedule(monkeypatch):
    monkeypatch.setattr(banweb, "get_schedule", lambda term: [{"code": "CS1315"}])
    r = client.post("/api/banweb/schedule", json={"term": "202609"})
    assert r.json() == {"ok": True, "term": "202609", "courses": [{"code": "CS1315"}]}


def test_banweb_schedule_error(monkeypatch):
    def boom(term):
        raise banweb.BanwebError("尚未登录 AIMS：请在新打开的浏览器窗口登录后再试")
    monkeypatch.setattr(banweb, "get_schedule", boom)
    r = client.post("/api/banweb/schedule", json={"term": "202609"})
    assert r.json()["ok"] is False
    assert "尚未登录" in r.json()["error"]


def _sched_course(code="CS1315", section="C01", course="Intro to Comp",
                  time="12:00 pm - 2:50 pm", days="F", room="MMW 2450",
                  range_="Aug 31, 2026 - Nov 28, 2026", instr="Kenneth LEE (P)"):
    return {"code": code, "section": section, "course": course,
            "meetings": [{"time": time, "days": days, "room": room,
                          "range": range_, "instr": instr}]}


def test_banweb_write_calendar_create(monkeypatch):
    """日历里没有旧事件 → 新建；写入参数正确传递。"""
    courses = [_sched_course()]
    specs = banweb.build_event_specs(courses, {"CS1315:C01"})
    monkeypatch.setattr(banweb, "build_event_specs", lambda cs, sel: specs)
    created, hidden = [], []
    monkeypatch.setattr(apple_script, "find_events", lambda cal, prefix: [])
    monkeypatch.setattr(apple_script, "hide_recurring_event",
                        lambda cal, summary: hidden.append(summary))
    monkeypatch.setattr(apple_script, "add_recurring_event",
                        lambda *a, **k: created.append((a, k)))
    r = client.post("/api/banweb/write_calendar", json={
        "calendar_name": "Study", "courses": courses,
        "selected": ["CS1315:C01"], "alert_minutes": 30})
    body = r.json()
    assert body["ok"] is True
    assert body["created"] == 1
    assert body["exists"] == 0
    assert body["updated"] == 0
    assert body["removed"] == 0
    assert body["errors"] == 0
    assert body["items"][0]["status"] == "created"
    assert hidden == []   # 没有旧事件 → 不隐藏
    args, _kwargs = created[0]
    assert args[4] == "2026-11-28"    # until（位置参数）
    assert args[7] == 30              # alert_minutes


def test_banweb_write_calendar_all_exists(monkeypatch):
    """日历里四元组全等 → 全部跳过，不重复写入、不删除。"""
    courses = [_sched_course()]
    specs = banweb.build_event_specs(courses, {"CS1315:C01"})
    monkeypatch.setattr(banweb, "build_event_specs", lambda cs, sel: specs)
    sp = specs[0]
    monkeypatch.setattr(apple_script, "find_events",
                        lambda cal, prefix: [{"summary": sp["title"],
                                              "start": sp["start"], "end": sp["end"]}])
    created, hidden = [], []
    monkeypatch.setattr(apple_script, "add_recurring_event",
                        lambda *a, **k: created.append(1))
    monkeypatch.setattr(apple_script, "hide_recurring_event",
                        lambda cal, summary: hidden.append(summary))
    r = client.post("/api/banweb/write_calendar", json={
        "calendar_name": "Study", "courses": courses,
        "selected": ["CS1315:C01"]})
    body = r.json()
    assert body["created"] == 0
    assert body["exists"] == 1
    assert created == []   # 已存在 → 不重复写入
    assert hidden == []    # 全匹配 → 不隐藏


def test_banweb_write_calendar_time_changed_updates(monkeypatch):
    """旧事件时间已改（标题不变）→ 原地编辑旧事件，状态 updated 并附旧时间。"""
    courses = [_sched_course()]
    specs = banweb.build_event_specs(courses, {"CS1315:C01"})
    monkeypatch.setattr(banweb, "build_event_specs", lambda cs, sel: specs)
    sp = specs[0]
    # 旧事件同标题但时间从 12:00-14:50 改到了 10:00-11:50
    old = {"summary": sp["title"], "start": "2026-09-04T10:00:00",
           "end": "2026-09-04T11:50:00"}
    monkeypatch.setattr(apple_script, "find_events", lambda cal, prefix: [old])
    edits, created, hidden = [], [], []
    monkeypatch.setattr(apple_script, "edit_recurring_event",
                        lambda *a: edits.append(a))
    monkeypatch.setattr(apple_script, "add_recurring_event",
                        lambda *a, **k: created.append(a))
    monkeypatch.setattr(apple_script, "hide_recurring_event",
                        lambda cal, summary: hidden.append(summary))
    r = client.post("/api/banweb/write_calendar", json={
        "calendar_name": "Study", "courses": courses,
        "selected": ["CS1315:C01"]})
    body = r.json()
    assert body["updated"] == 1
    assert body["removed"] == 0       # 同标题重建 → 不算纯删除
    assert body["items"][0]["status"] == "updated"
    assert body["items"][0]["old_time"] == "Fri 10:00-11:50"
    assert len(edits) == 1            # 原地编辑旧事件
    assert edits[0][0] == "Study"     # calendar_name
    assert edits[0][1] == sp["title"] # old_summary
    assert edits[0][2] == sp["title"] # new_summary
    assert edits[0][3] == sp["start"] # 新开始时间
    assert created == [] and hidden == []


def test_index_and_static(monkeypatch):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    for path in ["/static/app.css", "/static/app.js", "/static/i18n.js"]:
        assert client.get(path).status_code == 200


# ---------------- AIMS 自动登录凭据端点 ----------------

def test_banweb_save_credentials(monkeypatch):
    called = {}
    monkeypatch.setattr(credentials, "save_credentials",
                        lambda u, p: called.update(u=u, p=p))
    r = client.post("/api/banweb/credentials",
                    json={"username": "sc123456", "password": "s3cret"})
    assert r.json() == {"ok": True}
    assert called == {"u": "sc123456", "p": "s3cret"}


def test_banweb_save_credentials_strips_username(monkeypatch):
    called = {}
    monkeypatch.setattr(credentials, "save_credentials",
                        lambda u, p: called.update(u=u))
    client.post("/api/banweb/credentials",
                json={"username": "  sc123456  ", "password": "s3cret"})
    assert called["u"] == "sc123456"


def test_banweb_save_credentials_error(monkeypatch):
    def boom(u, p):
        raise credentials.CredentialsError("钥匙串不可用")
    monkeypatch.setattr(credentials, "save_credentials", boom)
    r = client.post("/api/banweb/credentials",
                    json={"username": "u", "password": "p"})
    assert r.json()["ok"] is False
    assert "钥匙串" in r.json()["error"]


def test_banweb_credentials_status(monkeypatch):
    monkeypatch.setattr(credentials, "get_username", lambda: "sc123456")
    r = client.get("/api/banweb/credentials/status")
    assert r.json() == {"ok": True, "has_credentials": True, "username": "sc123456"}


def test_banweb_credentials_status_empty(monkeypatch):
    monkeypatch.setattr(credentials, "get_username", lambda: "")
    r = client.get("/api/banweb/credentials/status")
    assert r.json() == {"ok": True, "has_credentials": False, "username": ""}


def test_banweb_delete_credentials(monkeypatch):
    monkeypatch.setattr(credentials, "delete_credentials", lambda: True)
    r = client.delete("/api/banweb/credentials")
    assert r.json() == {"ok": True}


def test_banweb_auto_login(monkeypatch):
    monkeypatch.setattr(banweb, "auto_login_from_stored", lambda: "logged_in")
    r = client.post("/api/banweb/auto_login", json={})
    assert r.json() == {"ok": True, "status": "logged_in"}


def test_banweb_auto_login_no_credentials(monkeypatch):
    def boom():
        raise banweb.BanwebError("尚未保存 AIMS 账号密码，请先在设置中填写")
    monkeypatch.setattr(banweb, "auto_login_from_stored", boom)
    r = client.post("/api/banweb/auto_login", json={})
    assert r.json()["ok"] is False
    assert "尚未保存" in r.json()["error"]


def test_pick_dir_ok(monkeypatch):
    monkeypatch.setattr(apple_script, "pick_folder", lambda: "/Users/me/Downloads")
    r = client.post("/api/pick_dir", json={})
    assert r.json() == {"ok": True, "path": "/Users/me/Downloads"}


def test_pick_dir_cancelled(monkeypatch):
    def cancel():
        raise apple_script.AppleScriptError("execution error: User canceled. (-128)")
    monkeypatch.setattr(apple_script, "pick_folder", cancel)
    r = client.post("/api/pick_dir", json={})
    assert r.json() == {"ok": False, "cancelled": True}


def test_pick_dir_error(monkeypatch):
    def boom():
        raise apple_script.AppleScriptError("not authorized")
    monkeypatch.setattr(apple_script, "pick_folder", boom)
    r = client.post("/api/pick_dir", json={})
    assert r.json()["ok"] is False
    assert r.json().get("cancelled") is not True
    assert "not authorized" in r.json()["error"]


def test_grades(monkeypatch):
    monkeypatch.setattr(canvas_client, "list_courses",
                        lambda u, t, include_scores=False: [
                            {"id": 5, "name": "CS101", "course_code": "CS101A",
                             "current_score": 88.5, "final_score": 85.0}])
    monkeypatch.setattr(canvas_client, "get_assignments_full",
                        lambda u, t, cid: [{"id": 1, "name": "HW1", "due_at": "",
                                            "points_possible": 10, "html_url": "", "score": 8.0, "submitted": True}])
    r = client.post("/api/grades", json={"canvas_url": "https://x", "canvas_token": "t", "course_ids": [5]})
    body = r.json()
    assert body["ok"] is True
    assert body["courses"][0]["current_score"] == 88.5
    assert body["courses"][0]["assignments"][0]["score"] == 8.0


def test_grades_course_failure_does_not_drag_batch(monkeypatch):
    monkeypatch.setattr(canvas_client, "list_courses", lambda u, t, include_scores=False: [{"id": 5, "name": "CS101"}])
    def _boom(u, t, cid):
        raise RuntimeError("boom")
    monkeypatch.setattr(canvas_client, "get_assignments_full", _boom)
    r = client.post("/api/grades", json={"canvas_url": "https://x", "canvas_token": "t", "course_ids": [5]})
    body = r.json()
    assert body["ok"] is True
    assert body["courses"][0]["assignments"] == []
    # 与既有 /api/assignments 约定一致：JSON 往返后 errors 的键是字符串
    assert body["errors"] == {"5": "boom"}


def test_todo(monkeypatch):
    monkeypatch.setattr(canvas_client, "get_todo", lambda u, t: [{"id": 1, "title": "HW1", "overdue": False}])
    r = client.post("/api/todo", json={"canvas_url": "https://x", "canvas_token": "t"})
    assert r.json() == {"ok": True, "items": [{"id": 1, "title": "HW1", "overdue": False}]}


def test_calendar_events(monkeypatch):
    monkeypatch.setattr(canvas_client, "get_calendar_events",
                        lambda u, t, ids, a, b: [{"id": 1, "title": "Talk", "start_at": "2026-09-10T14:00:00Z"}])
    r = client.post("/api/calendar_events", json={"canvas_url": "https://x", "canvas_token": "t",
                                                  "course_ids": [5], "start_date": "2026-09-01", "end_date": "2026-09-30"})
    assert r.json()["ok"] is True
    assert r.json()["events"][0]["title"] == "Talk"


def test_write_canvas_events_dedup(monkeypatch):
    # find_events 返回一条同标题同开始的既有事件 → 第二项应跳过（exists）
    monkeypatch.setattr(apple_script, "find_events",
                        lambda cal, prefix: [{"summary": "Talk", "start": "2026-09-10T14:00:00"}])
    added = []
    monkeypatch.setattr(apple_script, "add_calendar_event",
                        lambda *a: added.append(a))
    r = client.post("/api/write_canvas_events", json={
        "calendar_name": "Study",
        "items": [
            {"title": "Talk", "start": "2026-09-10T14:00:00", "end": "2026-09-10T15:00:00",
             "location": "LT-1", "notes": ""},
            {"title": "Other", "start": "2026-09-11T09:00:00", "end": "2026-09-11T10:00:00",
             "location": "", "notes": ""},
        ],
        "alert_minutes": None})
    body = r.json()
    assert body["ok"] is True
    assert body["created"] == 1
    assert body["exists"] == 1
    assert body["errors"] == 0
    assert len(added) == 1


def test_write_canvas_events_error(monkeypatch):
    monkeypatch.setattr(apple_script, "find_events", lambda cal, prefix: [])
    def _boom(cal, title, start, end, loc, notes, alert):
        raise RuntimeError("cal busy")
    monkeypatch.setattr(apple_script, "add_calendar_event", _boom)
    r = client.post("/api/write_canvas_events", json={
        "calendar_name": "Study",
        "items": [{"title": "X", "start": "2026-09-10T14:00:00", "end": "2026-09-10T15:00:00", "location": "", "notes": ""}],
        "alert_minutes": None})
    body = r.json()
    assert body["errors"] == 1
    assert body["items"][0]["status"] == "error"


# ---------------- AIMS 考试时间表端点 ----------------

def test_banweb_exams(monkeypatch):
    monkeypatch.setattr(banweb, "get_exams", lambda: ("Semester A 2026/27", [{"code": "CS2315", "date": "2026-12-15"}]))
    r = client.post("/api/banweb/exams", json={})
    assert r.json()["ok"] is True
    assert r.json()["term_label"] == "Semester A 2026/27"
    assert r.json()["exams"][0]["code"] == "CS2315"


def test_banweb_write_exams(monkeypatch):
    monkeypatch.setattr(apple_script, "find_events", lambda cal, prefix: [])
    added = []
    monkeypatch.setattr(apple_script, "add_calendar_event", lambda *a: added.append(a))
    r = client.post("/api/banweb/write_exams", json={
        "calendar_name": "Study",
        "exams": [{"course": "CS2315 程序设计", "code": "CS2315", "section": "C01",
                   "date": "2026-12-15", "start": "14:30", "end": "17:30",
                   "room": "LT-17", "seat": "23"}],
        "alert_minutes": None})
    body = r.json()
    assert body["ok"] is True
    assert body["created"] == 1
    assert body["items"][0]["title"] == "CS2315 考试"
    # 写入事件是考试日的起止时间
    assert added[0][2] == "2026-12-15T14:30:00"
    assert added[0][3] == "2026-12-15T17:30:00"


def test_write_one_off_utc_normalized_to_local(monkeypatch):
    """Z 后缀 UTC 开始时间 → 写入本地无时区；find_events 读回本地时间判 exists。"""
    import datetime as _dt
    src = "2026-09-10T06:00:00Z"
    expect = _dt.datetime(2026, 9, 10, 6, 0, tzinfo=_dt.timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:00")
    monkeypatch.setattr(apple_script, "find_events",
                        lambda cal, prefix: [{"summary": "Guest Talk", "start": expect}])
    added = []
    monkeypatch.setattr(apple_script, "add_calendar_event", lambda *a: added.append(a))
    res = main._write_one_off("Study", [{"title": "Guest Talk", "start": src, "end": None}], None)
    assert res["created"] == 0
    assert res["exists"] == 1
    assert added == []          # 命中已存在，不重复写入


def test_write_one_off_writes_local_naive_when_absent(monkeypatch):
    """不存在时按本地无时区写入（不含 Z）。"""
    import datetime as _dt
    src = "2026-09-10T06:00:00Z"
    expect_start = _dt.datetime(2026, 9, 10, 6, 0, tzinfo=_dt.timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:00")
    expect_end = _dt.datetime(2026, 9, 10, 7, 0, tzinfo=_dt.timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:00")
    monkeypatch.setattr(apple_script, "find_events", lambda cal, prefix: [])
    added = []
    monkeypatch.setattr(apple_script, "add_calendar_event", lambda *a: added.append(a))
    res = main._write_one_off("Study",
        [{"title": "Guest Talk", "start": src, "end": "2026-09-10T07:00:00Z"}], None)
    assert res["created"] == 1
    assert added[0][2] == expect_start     # 写入的开始=本地无时区
    assert added[0][3] == expect_end       # 写入的结束=本地无时区


# ===== 新端点：quizzes / discussions / pages / page_body / planner =====

def test_quizzes_batch_isolates_per_course(monkeypatch):
    """逐课程错误隔离：一门失败不拖垮整批。"""
    def fake(url, token, cid):
        if cid == 5:
            raise RuntimeError("boom")
        return [{"id": cid, "title": "Q"}]
    monkeypatch.setattr(canvas_client, "get_quizzes", fake)
    client = TestClient(main.app)
    r = client.post("/api/quizzes", json={
        "canvas_url": "https://x", "canvas_token": "t", "course_ids": [4, 5]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["by_course"] == {"4": [{"id": 4, "title": "Q"}], "5": []}
    # 与既有 /api/assignments 约定一致：JSON 往返后 errors 的键是字符串
    assert body["errors"] == {"5": "boom"}


def test_quizzes_tool_disabled_is_not_an_error(monkeypatch):
    """实测约一半课程未启用测验工具 → 404。这是常态，不进 errors、不显示错误行。"""
    def fake(url, token, cid):
        raise canvas_client.CanvasToolDisabled("课程未启用该工具 (HTTP 404)")
    monkeypatch.setattr(canvas_client, "get_quizzes", fake)
    client = TestClient(main.app)
    body = client.post("/api/quizzes", json={
        "canvas_url": "https://x", "canvas_token": "t", "course_ids": [1, 2]}).json()
    assert body["ok"] is True
    assert body["by_course"] == {"1": [], "2": []}
    assert body["errors"] == {}


def test_discussions_batch_isolates_per_course(monkeypatch):
    def fake(url, token, cid):
        if cid == 9:
            raise RuntimeError("nope")
        return [{"id": 1, "title": "T", "replies_count": 3}]
    monkeypatch.setattr(canvas_client, "get_discussion_topics", fake)
    client = TestClient(main.app)
    body = client.post("/api/discussions", json={
        "canvas_url": "https://x", "canvas_token": "t", "course_ids": [8, 9]}).json()
    assert body["ok"] is True
    assert body["by_course"]["8"][0]["replies_count"] == 3
    assert body["by_course"]["9"] == []
    assert body["errors"] == {"9": "nope"}


def test_discussions_tool_disabled_is_not_an_error(monkeypatch):
    def fake(url, token, cid):
        raise canvas_client.CanvasToolDisabled("课程未启用该工具 (HTTP 404)")
    monkeypatch.setattr(canvas_client, "get_discussion_topics", fake)
    client = TestClient(main.app)
    body = client.post("/api/discussions", json={
        "canvas_url": "https://x", "canvas_token": "t", "course_ids": [1]}).json()
    assert body["errors"] == {}
    assert body["by_course"] == {"1": []}


def test_pages_success(monkeypatch):
    monkeypatch.setattr(canvas_client, "get_pages",
                        lambda url, token, cid: [{"url": "home", "title": "Home"}])
    client = TestClient(main.app)
    body = client.post("/api/pages", json={
        "canvas_url": "https://x", "canvas_token": "t", "course_id": 5}).json()
    assert body == {"ok": True, "pages": [{"url": "home", "title": "Home"}]}


def test_pages_tool_disabled_returns_empty_not_error(monkeypatch):
    def fake(url, token, cid):
        raise canvas_client.CanvasToolDisabled("课程未启用该工具 (HTTP 404)")
    monkeypatch.setattr(canvas_client, "get_pages", fake)
    client = TestClient(main.app)
    body = client.post("/api/pages", json={
        "canvas_url": "https://x", "canvas_token": "t", "course_id": 5}).json()
    assert body["ok"] is True
    assert body["pages"] == []
    assert "error" not in body or not body["error"]


def test_pages_failure_is_flat_error(monkeypatch):
    monkeypatch.setattr(canvas_client, "get_pages",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    client = TestClient(main.app)
    body = client.post("/api/pages", json={
        "canvas_url": "https://x", "canvas_token": "t", "course_id": 5}).json()
    assert body["ok"] is False
    assert body["error"] == "boom"


def test_page_body_success(monkeypatch):
    monkeypatch.setattr(canvas_client, "get_page_body",
                        lambda url, token, cid, purl: {"url": purl, "title": "T",
                                                       "body_text": "纯文本"})
    client = TestClient(main.app)
    body = client.post("/api/page_body", json={
        "canvas_url": "https://x", "canvas_token": "t",
        "course_id": 5, "page_url": "home"}).json()
    assert body["ok"] is True
    assert body["page"]["body_text"] == "纯文本"


def test_page_body_failure(monkeypatch):
    monkeypatch.setattr(canvas_client, "get_page_body",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gone")))
    client = TestClient(main.app)
    body = client.post("/api/page_body", json={
        "canvas_url": "https://x", "canvas_token": "t",
        "course_id": 5, "page_url": "home"}).json()
    assert body["ok"] is False
    assert body["error"] == "gone"


def test_planner_success_and_passthrough(monkeypatch):
    got = {}

    def fake(url, token, start, end):
        got["range"] = (start, end)
        return [{"id": 1, "type": "assignment", "title": "作业",
                 "date": "2026-09-12T15:59:00Z", "submitted": False,
                 "html_url": "https://x/courses/5/assignments/1"}]

    monkeypatch.setattr(canvas_client, "get_planner_items", fake)
    client = TestClient(main.app)
    body = client.post("/api/planner", json={
        "canvas_url": "https://x", "canvas_token": "t",
        "start_date": "2026-09-10", "end_date": "2026-09-17"}).json()
    assert body["ok"] is True
    assert body["items"][0]["submitted"] is False
    assert got["range"] == ("2026-09-10", "2026-09-17")


def test_planner_failure_is_flat_error(monkeypatch):
    monkeypatch.setattr(canvas_client, "get_planner_items",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    client = TestClient(main.app)
    body = client.post("/api/planner", json={
        "canvas_url": "https://x", "canvas_token": "t",
        "start_date": "2026-09-10", "end_date": "2026-09-17"}).json()
    assert body["ok"] is False
    assert body["error"] == "boom"


def test_list_files_returns_folders_with_position(monkeypatch):
    """folders 必须回传给前端（建树用），并透传 position（Canvas 不一定给，取不到即 None）。"""
    files = [{"id": 9, "display_name": "a.pdf", "folder_id": 2,
              "content_type": "application/pdf", "size": 1, "url": "http://x/f/9"},
             # 第二个文件 folder_id 显式为 None → 响应里应是 None（前端按挂根层兜底）
             {"id": 10, "display_name": "b.pdf", "folder_id": None,
              "content_type": "application/pdf", "size": 2, "url": "http://x/f/10"},
             # 第三个文件**整个键都缺**（Canvas 真的不回这个字段）→ 响应仍须有键且为 None。
             # 只有这条能区分 .get 与 []：上一条的键是在的，两种写法结果一样。
             {"id": 11, "display_name": "c.pdf",
              "content_type": "application/pdf", "size": 3, "url": "http://x/f/11"}]
    folders = [
        {"id": 1, "name": "course files", "parent_folder_id": None, "position": 1},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1, "position": None},
    ]
    monkeypatch.setattr(canvas_client, "list_courses", lambda u, t: [{"id": 5, "name": "CS 101"}])
    monkeypatch.setattr(canvas_client, "get_course_files", lambda u, t, cid: (files, folders))
    r = client.post("/api/list_files", json={"canvas_url": "https://x", "canvas_token": "t",
                                             "course_ids": [5], "download_dir": "/tmp/dl"})
    body = r.json()
    assert body["ok"] is True
    got = body["courses"][0]
    assert [f["name"] for f in got["folders"]] == ["course files", "Week 3"]
    assert got["folders"][0]["parent_folder_id"] is None
    assert got["folders"][1]["position"] is None
    # 路径语义随之改变：不含课程根那一段
    assert got["files"][0]["path"] == "Week 3"
    # 落盘目标也不含那一段
    assert got["files"][0]["dest_path"].endswith("CS 101/Week 3/a.pdf")
    # 旧路径随 files 一并回传，供前端原样转交给下载端点
    assert got["files"][0]["legacy_path"].endswith("CS 101/course files/Week 3/a.pdf")
    # folder_id 必须回传 —— 前端建树靠它把文件挂到正确的文件夹节点上。
    # 少了它，nodes.get(undefined) 会让所有文件落到根层：树变平铺且不报错。
    assert got["files"][0]["folder_id"] == 2
    # 容缺：Canvas 不回 folder_id 时键仍在、值为 None（前端据此挂根层）
    assert got["files"][1]["folder_id"] is None
    assert "folder_id" in got["files"][1]
    # 输入里连键都没有时也要有：这条才真正钉住 .get（用 f["folder_id"] 会 KeyError）
    assert got["files"][2]["folder_id"] is None


def test_list_files_403_still_returns_folders_key(monkeypatch):
    """空文件夹区/未开放 → no_files，且 folders 键存在（形状统一，前端少一种分支）。"""
    def boom(u, t, cid):
        raise canvas_client.CanvasError("HTTP 403")
    monkeypatch.setattr(canvas_client, "list_courses", lambda u, t: [{"id": 5, "name": "CS 101"}])
    monkeypatch.setattr(canvas_client, "get_course_files", boom)
    r = client.post("/api/list_files", json={"canvas_url": "https://x", "canvas_token": "t",
                                             "course_ids": [5], "download_dir": "/tmp/dl"})
    got = r.json()["courses"][0]
    assert got["no_files"] is True
    assert got["folders"] == []


@pytest.mark.parametrize("exc", [
    canvas_client.CanvasError("HTTP 500"),
    RuntimeError("boom"),
])
def test_list_files_error_branches_still_return_folders_key(monkeypatch, exc):
    """非 403 的 CanvasError 与兜底 Exception 分支：形状同样必须统一。

    前端凭「folders 键恒在」少一种分支判断 —— 上面 no_files 那条只盖了三条里的
    403；这里补上另外两条（main.py 的 except CanvasError 的 else，以及 except Exception）。
    """
    def boom(u, t, cid):
        raise exc
    monkeypatch.setattr(canvas_client, "list_courses", lambda u, t: [{"id": 5, "name": "CS 101"}])
    monkeypatch.setattr(canvas_client, "get_course_files", boom)
    r = client.post("/api/list_files", json={"canvas_url": "https://x", "canvas_token": "t",
                                             "course_ids": [5], "download_dir": "/tmp/dl"})
    got = r.json()["courses"][0]
    assert got["folders"] == []
    assert got["files"] == []
    assert got["error"] == str(exc)
    assert "no_files" not in got


def test_download_files_skips_when_legacy_path_exists(tmp_path, monkeypatch):
    """客户端回传的 legacy_path 命中磁盘 → 跳过、不重下、也不搬到新路径。

    这是前端唯一依赖的那条接缝：/api/download_files 必须把 items 里的 legacy_path 透传到
    download_items。把那一行透传删掉，本用例会红 —— 而除此之外全套测试仍然是绿的，
    所以没有它就等于这条不变量完全没有回归保护。
    """
    legacy = tmp_path / "CS 101" / "course files" / "Week 3" / "a.pdf"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("old")
    dest = tmp_path / "CS 101" / "Week 3" / "a.pdf"
    called = []
    monkeypatch.setattr(canvas_client, "get_file",
                        lambda u, t, cid, fid: {"url": "http://x/f/9"})
    monkeypatch.setattr(canvas_client, "download_file",
                        lambda u, t, url, d: called.append(str(d)))
    r = client.post("/api/download_files", json={
        "canvas_url": "https://x", "canvas_token": "t", "download_dir": str(tmp_path),
        "items": [{"course_id": 5, "file_id": 9,
                   "dest_path": str(dest), "legacy_path": str(legacy)}]})
    body = r.json()
    assert body["ok"] is True
    assert body["downloaded"] == []
    assert body["skipped"] == [str(legacy)]
    assert called == []          # 旧布局命中 → 一个字节都不下载
    assert not dest.exists()     # 也不搬到新路径


# ---------- 图书馆端点 ----------

def _patch_library(monkeypatch, calls=None):
    """把三个新模块全部换成替身。calls 用来断言 LLM 有没有被调到。

    `_MIN_INTERVAL` 一并归零：端点现在每次查询都会进 `library_client.query_scope()`
    （跨查询节流 ≥1s），不归零的话每个图书馆端点用例都要真睡 1 秒。
    节流本身由 test_library_client 的专项用例在旁边钉住。
    """
    from backend import library_areas, library_client, library_query
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(library_areas, "load_areas",
                        lambda **k: {"available": True,
                                     "areas": [{"code": "comp", "name": "Computer Science"}]})
    monkeypatch.setattr(library_client, "list_domains", lambda **k: ["MEDLINE"])
    if calls is None:
        calls = []
    monkeypatch.setattr(library_query, "parse_natural",
                        lambda *a, **k: (calls.append("llm") or
                                         ({"keywords": ["ml"], "synonyms": [], "year_from": None,
                                           "year_to": None, "rtype": [], "language": None,
                                           "open_access_only": False, "peer_reviewed_only": False,
                                           "exclude_retracted": False, "areas": [], "domains": [],
                                           "explanation": ""}, [])))
    monkeypatch.setattr(library_client, "search",
                        lambda scope, q, **k: {"scope": scope,
                                               "items": [{"title": scope, "url": "u",
                                                          "record_id": "r"}],
                                               "count": 5, "page": k.get("page", 1),
                                               "has_more": False})
    return calls


def test_library_areas_endpoint(monkeypatch):
    _patch_library(monkeypatch)
    r = client.post("/api/library/areas", json={})
    assert r.json() == {"ok": True, "available": True,
                        "areas": [{"code": "comp", "name": "Computer Science"}]}


def test_library_areas_unavailable_is_not_an_error(monkeypatch):
    """拿不到不是错误 —— 前端据此不渲染选择器，不该看到红字。"""
    from backend import library_areas
    monkeypatch.setattr(library_areas, "load_areas", lambda **k: {"available": False, "areas": []})
    body = client.post("/api/library/areas", json={}).json()
    assert body["ok"] is True and body["available"] is False


def test_library_search_returns_two_blocks(monkeypatch):
    calls = _patch_library(monkeypatch)
    r = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "text": "ml papers"})
    body = r.json()
    assert body["ok"] is True
    assert body["local"]["items"][0]["title"] == "MyInstitution"
    assert body["external"]["items"][0]["title"] == "CentralIndex"
    assert calls == ["llm"]


def test_library_search_second_page_does_not_call_llm(monkeypatch):
    """spec §5.3 核心：翻页绝不重新调用 LLM ——
    否则 LLM 两次产出不同，第 2 页的检索式与第 1 页不同，结果集中途漂移。

    **text 与 parsed 一起传**是刻意的：只传 parsed 的话，一旦有人把 `parsed`
    分支删掉，端点会在「缺少 text 或 parsed」处提前返回，用例虽然也会红，
    但红在 `ok is True` 上 —— 真正该管事的 `calls == []` 那行根本没被走到，
    等于「不调 LLM」这条不变量没有任何一行断言替它把关。带上 text 之后，
    被删掉分支的实现会**真的走到 LLM 调用点**，只有 `calls == []` 红了才算数。
    """
    calls = _patch_library(monkeypatch)
    parsed = {"keywords": ["ml"], "synonyms": [], "year_from": None, "year_to": None,
              "rtype": [], "language": None, "open_access_only": False,
              "peer_reviewed_only": False, "exclude_retracted": False,
              "areas": [], "domains": [], "explanation": ""}
    r = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "text": "ml papers", "page": 2, "parsed": parsed})
    assert r.json()["ok"] is True
    # 页码必须**传到上游 search**：替身把收到的 page 原样回显，写成 page=1
    # 的实现会在这里露馅（否则「翻页」= 永远看第 1 页，而无一用例会红）。
    assert r.json()["local"]["page"] == 2 and r.json()["external"]["page"] == 2
    assert calls == []          # LLM 一次都没被调 —— parsed 在就绝不重新解析


def test_library_search_page_beyond_max_rejected(monkeypatch):
    """上限由后端挡 —— 前端不显示按钮拦不住改请求的人。"""
    _patch_library(monkeypatch)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "text": "x", "page": 6}).json()
    assert body["ok"] is False and "页码" in body["error"]


def test_library_search_requires_text_or_parsed(monkeypatch):
    _patch_library(monkeypatch)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m"}).json()
    assert body["ok"] is False


def test_library_search_page_below_one_rejected(monkeypatch):
    """F2：`page < 1` 的拒绝。

    变异 `A2_lower_guard`（把守卫的 `req.page < 1 or` 那一半去掉）原先**存活** ——
    `page > MAX_PAGES` 那一半有 `test_library_search_page_beyond_max_rejected` 守着，
    下界这一半没有任何用例。而 `page=0` 会算成 `offset = -20`，把畸形页码原样送给上游。
    错误信息里必须带上**页码本身**，否则用户看到「页码越界」却不知道该改哪个值。
    """
    _patch_library(monkeypatch)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "text": "x", "page": 0}).json()
    assert body["ok"] is False
    assert "页码" in body["error"]
    assert "0" in body["error"]           # 回显的是请求里那个具体的页码


def test_library_search_rejects_parsed_without_keywords(monkeypatch):
    """F3-a：**翻页路径**（带 `parsed`、不带 `text`）送来的空检索式必须被拒。

    变异 `W`（删掉那次 `not parsed["keywords"] and not parsed["synonyms"]` 判断）
    原先**存活**：`keywords` / `synonyms` 双空时 `build_q` 会拼出 `"any,contains,"`
    —— 一个语法合法、语义为「匹配一切」的检索式，于是用户拿到的是**全库结果**
    而不是一个明确错误。走翻页路径（而不是带 text）才钉得住：带 text 的实现会在
    「缺少 text 或 parsed」那里提前返回，红在别处。
    """
    _patch_library(monkeypatch)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "page": 2,
        "parsed": {"keywords": [], "synonyms": [], "rtype": [], "domains": [],
                   "year_from": None, "year_to": None}}).json()
    assert body["ok"] is False
    assert "keywords" in body["error"]


def test_library_search_sends_the_compiled_q_to_the_client(monkeypatch):
    """F3-b：断言**真正发给客户端的那个 `q`**（不是返回体里的 `parsed`）。

    变异 `U`（`q = library_query.build_q(parsed)` 被改坏 / 换成常量）原先**存活** ——
    既有用例都只看返回体，没有任何一条看得见送上游的检索式。
    这里断言的是字面值，不是 `== library_query.build_q(parsed)`：后者是同义反复，
    两边一起变，`build_q` 被架空时照样绿。
    """
    _patch_library(monkeypatch)
    seen = {}
    from backend import library_client

    def spy(scope, q, **k):
        seen[scope] = q
        return {"scope": scope, "items": [], "count": 0, "page": 1, "has_more": False}

    monkeypatch.setattr(library_client, "search", spy)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "page": 2,
        "parsed": {"keywords": ["ml", "papers"], "synonyms": []}}).json()
    assert body["ok"] is True
    assert seen[library_client.SCOPE_LOCAL] == "any,contains,ml papers"
    assert seen[library_client.SCOPE_EXTERNAL] == "any,contains,ml papers"


def test_library_search_wraps_upstream_failure(monkeypatch):
    _patch_library(monkeypatch)
    from backend import library_client

    def boom(scope, q, **k):
        raise library_client.LibraryError("上游炸了")

    monkeypatch.setattr(library_client, "search", boom)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "text": "x"}).json()
    assert body["ok"] is False and "上游炸了" in body["error"]


def test_library_search_passes_area_hint_to_llm(monkeypatch):
    """area 传给 LLM 当提示，且**不**被当成过滤参数（spec §4.1）。"""
    seen = {}
    from backend import library_query
    _patch_library(monkeypatch)

    def spy(*a, **k):
        seen.update(k)
        return ({"keywords": ["x"], "synonyms": [], "year_from": None, "year_to": None,
                 "rtype": [], "language": None, "open_access_only": False,
                 "peer_reviewed_only": False, "exclude_retracted": False,
                 "areas": ["comp"], "domains": [], "explanation": ""}, [])

    monkeypatch.setattr(library_query, "parse_natural", spy)
    client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "text": "x", "area": "comp"})
    assert seen.get("area") == "comp"
    # 领域词表同样要喂给 LLM：它只能从这份名单里挑 domains，否则 LLM 编出来的
    # 库名会在 parse_natural 里被整批丢掉（domains_dropped）。
    assert seen.get("domains") == ["MEDLINE"]


def test_library_search_does_not_backfill_the_user_area(monkeypatch):
    """端点这侧也不许把用户选的 area 补进 `parsed.areas`（spec §6 的观察面）。

    LLM 返回的 areas 是 `["x"]`、用户选的是 `"y"` ⇒ 响应里必须**恰好**是 `["x"]`，
    `"y"` 不得出现。这就是杀死原缺陷的用例：旧实现会补成 `["y","x"]`，于是
    「LLM 照做了」与「LLM 完全无视」在界面上显示得一模一样，规格 :237 的补偿归零。

    注意 spec §6 里 `area`（单数，用户选的）与 `parsed.areas`（复数，LLM 理解的）
    是两个不同层的东西：`area` 只是**提示词**（另有用例钉住它确实被喂给了 LLM），
    这里管的是回显的那份数据必须来自 LLM。

    变异：把端点里那次 `parsed["areas"].insert(0, req.area)` 加回来 ⇒ 本用例红。
    """
    from backend import library_query
    _patch_library(monkeypatch)
    monkeypatch.setattr(library_query, "parse_natural",
                        lambda *a, **k: ({"keywords": ["x"], "synonyms": [],
                                          "year_from": None, "year_to": None, "rtype": [],
                                          "language": None, "open_access_only": False,
                                          "peer_reviewed_only": False,
                                          "exclude_retracted": False, "areas": ["x"],
                                          "domains": [], "explanation": ""}, []))
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "text": "x", "area": "y"}).json()
    assert body["ok"] is True
    assert body["parsed"]["areas"] == ["x"]
    assert "y" not in body["parsed"]["areas"]


def test_library_search_keeps_the_area_llm_did_obey(monkeypatch):
    """反向对照（端点侧）：LLM 自己把用户选的 area 放进 areas ⇒ 原样保留、不重复。

    删回填没有把正常路径一起改坏：LLM 照做时回显的就是它给的那份（顺序也不许动）。
    """
    from backend import library_query
    _patch_library(monkeypatch)
    monkeypatch.setattr(library_query, "parse_natural",
                        lambda *a, **k: ({"keywords": ["x"], "synonyms": [],
                                          "year_from": None, "year_to": None, "rtype": [],
                                          "language": None, "open_access_only": False,
                                          "peer_reviewed_only": False,
                                          "exclude_retracted": False,
                                          "areas": ["y", "z"], "domains": [],
                                          "explanation": ""}, []))
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "text": "x", "area": "y"}).json()
    assert body["parsed"]["areas"] == ["y", "z"]


def test_library_search_degrades_without_inventing_an_area(monkeypatch):
    """LLM 降级时 `areas` 就是空的 —— **不要**为了界面好看把它补回去。

    没有 LLM 答案就是没有；降级本身由 `llm_degraded` 警告码经既有 warning 通道可见，
    不需要也不允许用「补一个 area」来假装解析成功。
    """
    from backend import library_query
    _patch_library(monkeypatch)
    # 降级路径的真实产物就是 fallback_parsed —— 直接用它，别再手搓
    # dict(EMPTY_PARSED, ...)：那是浅拷贝，list 字段与模块常量共用同一个对象。
    monkeypatch.setattr(library_query, "parse_natural",
                        lambda *a, **k: (library_query.fallback_parsed("x"),
                                         ["llm_degraded"]))
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "text": "x", "area": "comp"}).json()
    assert body["ok"] is True
    assert body["parsed"]["areas"] == []
    assert body["warnings"] == ["llm_degraded"]
    # 金丝雀：**整包级的「全局常量未被改脏」哨兵，且顺序相关** —— 如实描述它是什么，
    # 不再声称它守的是「有人把就地改写 reintroduce 回来」（那是假的，见下）。
    #
    # 复审实测的守护范围（task-8-review「R2-6」）：
    #   ① 对「reintroduce 就地改写」**没有判别力**：变异「端点加回
    #      `parsed["areas"].insert(0, area)`」时本用例确实红，但红的是**上一行**
    #      `assert body["parsed"]["areas"] == []`（`E assert ['comp'] == []`），
    #      这两行金丝雀在同场景下**是通过的**（`_blank_parsed()` 现在是 deepcopy，
    #      端点改写碰不到 `EMPTY_PARSED`）。
    #   ② 它唯一的活触发是「`_blank_parsed` 退化为浅拷贝」，而且**单跑不红**：
    #      要靠前面 `test_mutating_returned_parsed_does_not_pollute_empty_parsed`
    #      先就地把常量改脏，整包顺序跑时才看见 ⇒ 它是一条**顺序相关的整包级哨兵**，
    #      不是本用例自身行为的守卫。
    #   ③ 它是**冗余**的：浅拷贝退化时库层三条不变量用例
    #      （`test_blank_parsed_returns_independent_lists` /
    #      `..._validate_parsed_none_...` / `test_mutating_returned_parsed_...`）
    #      会同时变红并指向同一根因。
    # 保留断言本身（成本几行，作为整包级哨兵仍有价值）；处置由控制者裁定，本轮不动。
    assert library_query.EMPTY_PARSED["areas"] == [], (
        "整包级哨兵（**顺序相关**）：EMPTY_PARSED 是**模块级常量**，本断言只保证"
        "**整个测试包跑完之后**它的值没有被改动过 —— 它不是「防就地改写」的守卫，"
        "对 reintroduce 没有判别力。失败信息里的值**可能来自另一个测试文件**，"
        "别据此断定是本文件的哪一行干的；先查谁在别处就地改写了共享的 list。"
        "`validate_parsed` 返回的是 `dict(EMPTY_PARSED)` 的**浅拷贝**，list 字段与"
        "常量**共用同一个对象**，这个结构隐患仍在。")
    assert library_query.EMPTY_PARSED["domains"] == [], (
        "整包级哨兵（**顺序相关**）：同上一行。EMPTY_PARSED 是模块级常量，"
        "本断言只保证整包跑完时它没被改动过；失败值**可能来自另一个测试文件**，"
        "不要据此定位到本文件。浅拷贝共享 list 对象的结构隐患仍在。")


def test_library_search_surfaces_a_non_degraded_warning(monkeypatch):
    """F-6：成功分支的 `warnings` 必须原样透出**非降级**警告码。

    缺口是这一层：删掉整个 `warnings` 键已经有人守了（上面那条降级用例的
    `assert body["warnings"] == ["llm_degraded"]`），但把端点改成**只放行
    `llm_degraded`、丢掉其余码**（`[w for w in warnings if w == "llm_degraded"]`）
    曾经是 **378 全绿** ⇒ `domains_dropped` 以及将来新增的任何码在端点层毫无绑定，
    可以被静默抹掉而用户永远看不见。成因：`domains_dropped` 只在**库层**被绑
    （tests/test_library_query.py 的那条），**没有任何端点用例产生过一条非降级警告**。

    这里走**真实的** `parse_natural`（只把 LLM 出口换掉），让 `domains_dropped`
    真的被产生出来，而不是把警告码手搓进替身 —— 那样连「端点有没有转发库层警告」
    这件事都测不到。
    """
    import json as _json
    from backend import library_query, llm_client
    real_parse_natural = library_query.parse_natural
    _patch_library(monkeypatch)
    monkeypatch.setattr(library_query, "parse_natural", real_parse_natural)
    # LLM 编了一个词表里没有的库名。词表由 `_patch_library` 钉成 ["MEDLINE"]，
    # 于是 validate_parsed 会把它整批丢掉 ⇒ 真实地产生 `domains_dropped`。
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: _json.dumps({
        "keywords": ["ml"], "synonyms": [], "domains": ["NotInTheCatalog"]}))

    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "text": "x"}).json()
    assert body["ok"] is True
    assert body["warnings"] == ["domains_dropped"]


def test_library_search_normalizes_client_supplied_parsed(monkeypatch):
    """回传的 parsed 是**客户端输入**：未知 rtype / 词表外的 domain / 越界年份
    都必须在端点上被归一化掉，不能原样编译进上游查询（会静默 0 命中）。

    这些值都是第 2 页起前端原样回传的，中间可能被改过、也可能是旧版本存的。

    **输入里好坏值混着给**（`bogus_type` 与 `articles` 并存、`NotInTheCatalog` 与
    `MEDLINE` 并存）是刻意的：全是坏值时归一化后的过滤集本来就是空的，负向断言
    「不包含 X」在**什么都没产出**时照样成立 —— 变异 `E_filters_discarded`
    （`q_include` 被清空）当时就是在这组全负向断言下**假绿**的。
    混入能存活的值之后，下面那组正向断言才真的在管「产出了什么」。
    """
    _patch_library(monkeypatch)
    seen = {}
    from backend import library_client

    def spy(scope, q, **k):
        seen[scope] = k
        return {"scope": scope, "items": [], "count": 0, "page": 1, "has_more": False}

    monkeypatch.setattr(library_client, "search", spy)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "page": 2,
        "parsed": {"keywords": ["x"], "rtype": ["bogus_type", "articles"],
                   "domains": ["NotInTheCatalog", "MEDLINE"], "year_from": 1500,
                   "peer_reviewed_only": True}}).json()
    assert body["ok"] is True
    include = seen["CentralIndex"].get("q_include") or []
    # 正向：归一化后**确实产出了**这些过滤器（清空 q_include 的实现会在这里红）
    assert "facet_rtype,exact,articles" in include
    assert "domain,exact,MEDLINE" in include
    assert "facet_tlevel,exact,peer_reviewed" in include
    # 负向：坏值被剔
    assert not any("bogus_type" in f for f in include)          # 未知 rtype 被剔
    assert not any("NotInTheCatalog" in f for f in include)     # 词表外的库名被剔
    # ⚠️ 前缀必须是 `facet_creationdate`：写成 `creationdate` 时，本次加上 `facet_`
    # 前缀之后这条**恒真**（任何 f 都不再以 `creationdate` 开头），等于把
    # 「1800 年前的年份被剔」这条守则悄悄废掉。年份改成区间表达式后同理。
    assert not any(f.startswith("facet_creationdate") for f in include)


def test_library_search_warns_when_an_llm_facet_choice_is_narrowed(monkeypatch):
    """被窄化掉的第二个值必须出现在 `warnings` 里（**码**，不是句子）。

    这里刻意**不**打桩 `compile_filters` / `narrowing_warnings` —— 要钉的正是
    端点有没有把它们接上（`_patch_library` 只换掉 `parse_natural`/`search`/
    `list_domains`/`load_areas`，编译链是真题）。回显的 `parsed.rtype` 仍是
    两个值（`validate_parsed` 不动它），所以光看回显用户会以为两个都筛了
    —— `warnings` 是唯一的补偿通道，接不上就等于静默窄化。

    这里刻意选 `["books", "dissertations"]` —— 两个值**同时**在两份词表的交集里
    （交集只有 books / dissertations / conference_proceedings 三个），所以**两个
    scope 都会算出同一个码**，`assert == ["rtype_narrowed"]` 于是同时钉住了
    「接上了」和「**按码去重了**」。若换成 `["articles", "books"]`，本馆求交后只剩
    `books` 一个（不报），去重那条就测不到了 —— 变异验证里「端点警告不去重」
    当时正是**零杀伤**，就是因为用例选错了值。
    """
    _patch_library(monkeypatch)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "page": 2,
        "parsed": {"keywords": ["ml"], "rtype": ["books", "dissertations"]}}).json()
    assert body["ok"] is True
    assert body["warnings"] == ["rtype_narrowed"]              # 恰好一个 ⇒ 去过重
    assert body["parsed"]["rtype"] == ["books", "dissertations"]   # 回显照旧，不回填


def test_library_search_does_not_warn_for_a_single_llm_facet_choice(monkeypatch):
    """反向：单个值不该报 —— 否则每次检索都挂一条警告，警告就被训练成噪声。"""
    _patch_library(monkeypatch)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "page": 2,
        "parsed": {"keywords": ["ml"], "rtype": ["books"]}}).json()
    assert body["ok"] is True
    assert body["warnings"] == []


def test_library_search_compiles_two_different_param_sets(monkeypatch):
    """两个 scope 的过滤参数**不是同一套**（spec §6）。

    差异点是 **`domain` 与 `attribute`（PC 独有）**，**不是 `tlevel`** ——
    spec §6 把 tlevel 也算进「PC 独有」，实测为假（本馆有 tlevel 且过滤生效：
    34,105 → 45），所以 tlevel 现在两侧都发。这里补正向断言把它钉住，
    免得日后有人照规格把 tlevel 又挪回 `is_pc` 门控里。
    """
    _patch_library(monkeypatch)
    seen = {}
    from backend import library_client

    def spy(scope, q, **k):
        seen[scope] = k
        return {"scope": scope, "items": [], "count": 0, "page": 1, "has_more": False}

    monkeypatch.setattr(library_client, "search", spy)
    client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "text": "x", "parsed": {"keywords": ["x"], "peer_reviewed_only": True,
                                "exclude_retracted": True, "domains": ["MEDLINE"]}})
    pc_inc = seen["CentralIndex"].get("q_include") or []
    local_inc = seen["MyInstitution"].get("q_include") or []
    # 两侧**都**发 tlevel（实测推翻 spec §6 的「PC 独有」）
    assert "facet_tlevel,exact,peer_reviewed" in pc_inc
    assert "facet_tlevel,exact,peer_reviewed" in local_inc
    # 真正只属于 PC 的两个 facet
    assert "domain,exact,MEDLINE" in pc_inc
    assert "domain,exact,MEDLINE" not in local_inc
    assert "facet_attribute,exact,retracted_publication" in (
        seen["CentralIndex"].get("q_exclude") or [])
    assert (seen["MyInstitution"].get("q_exclude") or []) == []
    # 结论：两套参数确实不同
    assert pc_inc != local_inc


# ---------- F-3：两个 scope 并发 + 查询级节流 ----------

def test_library_search_runs_both_scopes_concurrently(monkeypatch):
    """spec §3.2「并发两次请求」/§5.2 ⑤/§9「只允许两个 scope 并发」。

    判据用**屏障**而不是计时：两个替身必须**同时在飞**才过得去；串行的实现会在
    屏障上等到超时（BrokenBarrierError）→ 异常冒到端点 → `ok is False`，确定性失败，
    与机器快慢无关（sleep 赌时序的写法会既脆又慢）。
    """
    import threading
    _patch_library(monkeypatch)
    from backend import library_client

    barrier = threading.Barrier(2, timeout=5)

    def both_at_once(scope, q, **k):
        barrier.wait()          # 只有两个 scope 并发才可能通过
        return {"scope": scope, "items": [{"title": scope, "url": "u", "record_id": "r"}],
                "count": 5, "page": k.get("page", 1), "has_more": False}

    monkeypatch.setattr(library_client, "search", both_at_once)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "text": "x"}).json()
    assert body["ok"] is True
    assert body["local"]["items"][0]["title"] == "MyInstitution"
    assert body["external"]["items"][0]["title"] == "CentralIndex"


def test_library_search_enters_the_query_gate(monkeypatch):
    """端点必须真的走 `query_scope`：库里备了门而没人用的话，
    spec §9 的「相邻请求 ≥1s」与「不同查询之间串行」在端点层等于不存在。"""
    import contextlib
    _patch_library(monkeypatch)
    from backend import library_client

    entered = []
    real_scope = library_client.query_scope

    @contextlib.contextmanager
    def spy_scope():
        entered.append(1)
        with real_scope():
            yield

    monkeypatch.setattr(library_client, "query_scope", spy_scope)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "text": "x"}).json()
    assert body["ok"] is True
    assert entered == [1]           # 一次查询恰好进一次门（并发的是门**内**的两栏）


def test_library_search_marks_its_scope_calls_as_inside_the_query(monkeypatch):
    """门内的两个提交点必须**显式**声明「本调用在查询内部」（`inside_query=True`）。

    这是库侧唯一的放行判据（`library_client._get` 不再读 `_query_lock.locked()`）。
    漏传的后果是**永久死锁**（worker 卡在门内的 `_query_lock` 上、端点在 `fut.result()`
    上等它），所以库层已把 `inside_query` 定义成**必传参数**，漏传会在 worker 里立刻
    抛 `TypeError`。本用例仍在这里钉「两个提交点都显式传了 True」这个事实 ——
    换回「读锁状态」的话这条路照样能过，只有这条断言看得见差别。
    （更正：本行原先写「忘了传的后果是每次检索白等 `_MIN_INTERVAL`，不是死锁」，
    与实测不符，见 `task-8-report`「八、修复轮 3」。）
    """
    seen = []
    _patch_library(monkeypatch)
    from backend import library_client

    def spy(scope, q, **k):
        seen.append(k)
        return {"scope": scope, "items": [], "count": 0, "page": k.get("page", 1),
                "has_more": False}

    monkeypatch.setattr(library_client, "search", spy)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "text": "x"}).json()
    assert body["ok"] is True
    assert len(seen) == 2                       # 两个 scope 各一次
    assert all(k.get("inside_query") is True for k in seen)


# ---------- 修复轮 3：两道防线 + area 抓取进门（R2-2 / R2-3） ----------

# `parse_natural` 的替身返回值（只需要形状对：build_q / compile_filters 能跑）。
_LIB_PARSED_STUB = {"keywords": ["ml"], "synonyms": [], "year_from": None, "year_to": None,
                    "rtype": [], "language": None, "open_access_only": False,
                    "peer_reviewed_only": False, "exclude_retracted": False,
                    "areas": [], "domains": [], "explanation": ""}

_LIB_SEARCH_BODY = {"llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "text": "x"}


def _patch_library_boundaries(monkeypatch):
    """只打「外围边界」的桩，**不碰 `library_client.search` / `_get`**。

    R2-2 / R2-3 的根因都是**桩的覆盖面**：`_patch_library` 把 `library_client.search`
    整个换成 `lambda scope, q, **k`，于是「worker 走真实加锁路径」这件事在套件里
    结构上不存在 —— 漏传标志只会红断言、不会挂；`load_areas` 被换掉则让那次门外抓取
    在实机验证里消失。需要看见那条路径的用例（漏传标志、`fut.result` 兜底超时、
    area 抓取与门内两栏不重叠）用本函数，只把最底层 HTTP 出口与 LLM 换掉。
    """
    from backend import library_client, library_query
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(library_query, "parse_natural",
                        lambda *a, **k: (dict(_LIB_PARSED_STUB), []))
    monkeypatch.setattr(library_client, "list_domains", lambda **k: [])


def test_library_search_reports_an_error_when_a_submit_point_forgets_the_flag(monkeypatch):
    """提交点漏传 `inside_query=True` ⇒ 端点**快速返回 200 + JSON 错误**，不是永久挂死。

    这是 R2-2 的回归用例，也是**结构上唯一看得见这条路径的形态**：走**真实的**
    `library_client.search` / `_get`（只把最底层的 `_http_get` 换掉），
    不像 `_patch_library` 那样把 `search` 整个换掉 —— 替身 `lambda scope, q, **k`
    会把漏传照单全收，那条路径上永远看不见这个问题。

    改动前：漏传 ⇒ worker 卡在 `_query_lock` 上、端点卡在**无 timeout** 的
    `fut.result()` 上 ⇒ 本用例的 `join(5)` 先红（复审用两组独立进程实测：6 秒不返回）。
    改动后：`inside_query` 是必传参数 ⇒ worker 里立刻 `TypeError` ⇒ 由 `fut.result()`
    带出来 ⇒ 端点的 `except Exception` 转成 200 + JSON。
    """
    from backend import library_areas, library_client
    _patch_library_boundaries(monkeypatch)
    monkeypatch.setattr(library_areas, "load_areas", lambda **k: {"available": True, "areas": []})
    monkeypatch.setattr(library_client, "_http_get",
                        lambda path, params, session: {"docs": [], "info": {"total": 0},
                                                       "facets": []})

    real_search = library_client.search

    def forgetful_search(*a, **k):
        k.pop("inside_query", None)                 # ← 模拟「提交点漏传了标志」
        return real_search(*a, **k)

    monkeypatch.setattr(library_client, "search", forgetful_search)

    out = []
    t = threading.Thread(target=lambda: out.append(
        client.post("/api/library/search", json=_LIB_SEARCH_BODY).json()))
    t.start()
    t.join(5)
    assert t.is_alive() is False                    # 关键：5 秒内必须返回
    assert out[0]["ok"] is False
    assert "inside_query" in out[0]["error"]        # 就是漏传那个参数的 TypeError


def test_library_search_returns_an_error_instead_of_hanging_when_a_worker_blocks(monkeypatch):
    """`fut.result(timeout=)` 是**兜底**：worker 被意料外地卡住 ⇒ 200 + JSON，不是挂死。

    本条考的是端点的兜底本身（库层的加锁路径由上面那条与
    `test_inside_query_is_a_required_keyword_argument` 管），所以这里用一个**会卡住
    的 `search` 替身**把「意料外的永久阻塞」做出来。
    `_RESULT_TIMEOUT` 拨到 0.2s 只是为了让用例别等 45 秒，机制不变。
    """
    from backend import library_client, main as backend_main
    _patch_library(monkeypatch)                     # 含 load_areas / list_domains / parse_natural

    release = threading.Event()

    def blocked_search(scope, q, **k):
        release.wait(10)                            # 「永久」阻塞
        return {"scope": scope, "items": [], "count": 0, "page": 1, "has_more": False}

    monkeypatch.setattr(library_client, "search", blocked_search)
    monkeypatch.setattr(backend_main, "_RESULT_TIMEOUT", 0.2)

    out = []
    t = threading.Thread(target=lambda: out.append(
        client.post("/api/library/search", json=_LIB_SEARCH_BODY).json()))
    t.start()
    try:
        t.join(5)
        assert t.is_alive() is False                # 没有挂死
        assert out[0]["ok"] is False
        assert "超时" in out[0]["error"]
    finally:
        release.set()                               # 放游离的 worker 收工，别拖住进程退出
        t.join(5)


def test_an_area_fetch_never_overlaps_a_query(monkeypatch, tmp_path):
    """**冷 area 缓存**的抓取也必须进门：不能与门内两栏并发（spec §9 只允许两栏并发）。

    复审 R2-3 的实测反例：`library_areas.load_areas()` 走的是它自己那条 `requests`
    路径，**不经 `_get`、不受节流、不持锁** ⇒ 冷缓存 + 两请求并发时上游在飞峰值 **3**
    （复审三臂隔离因果：2 / 3 / 2）。

    这条用例**不把 `load_areas` 换成替身** —— 那正是上一轮看不见它半边的原因。
    用的是真 `load_areas`（真解析路径）+ 真 `library_client.search` / `_get`，
    只把两条最底层的 HTTP 出口换成计数替身。

    确定性（照 M3 的事件驱动写法，不赌时序）：查询 A 的两栏卡在 `release_query` 上，
    「两栏在飞」是被**观察到**的状态；B 若真把 area 抓取打进了网络，`areas_entered`
    会在 0.5s 内亮 —— 那一行先红，`peak == 2` 再红一次。
    """
    from backend import library_areas, library_client
    _patch_library_boundaries(monkeypatch)
    monkeypatch.setattr(library_client, "_last_query_at", 0.0)
    monkeypatch.setattr(library_client, "_last_call", 0.0)
    # 冷缓存：指向不存在的文件。抓取走的仍是 library_areas 的真实解析路径。
    monkeypatch.setattr(library_areas, "CACHE_FILE", tmp_path / "areas.json")

    state = {"in_flight": 0, "peak": 0, "areas_calls": 0}
    lock = threading.Lock()
    two_in_flight = threading.Event()
    release_query = threading.Event()
    areas_entered = threading.Event()

    def _enter():
        with lock:
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])

    def _leave():
        with lock:
            state["in_flight"] -= 1

    def fake_http_get(path, params, session):
        """门内两栏的上游出口：计数 + 卡住（「在飞」的度量点）。"""
        _enter()
        if state["in_flight"] >= 2:
            two_in_flight.set()
        try:
            release_query.wait(5)
        finally:
            _leave()
        return {"docs": [], "info": {"total": 0}, "facets": []}

    class _AreasResp:
        text, status_code = "<html></html>", 200

        def raise_for_status(self):
            pass

    class _AreasSession:
        """area 抓取的上游出口 —— 进了 `get` 就记账（进不来才是对的）。"""

        def get(self, *a, **k):
            _enter()
            with lock:
                state["areas_calls"] += 1
            areas_entered.set()
            try:
                return _AreasResp()
            finally:
                _leave()

        def close(self):
            pass

    class _AreasRequests:                    # 只换 library_areas 眼里的 requests
        Session = _AreasSession

    monkeypatch.setattr(library_client, "_http_get", fake_http_get)
    monkeypatch.setattr(library_areas, "requests", _AreasRequests)

    results = []

    def run():
        results.append(client.post("/api/library/search", json=_LIB_SEARCH_BODY).json())

    t_a = threading.Thread(target=run)
    t_a.start()
    assert two_in_flight.wait(5)             # A 的两栏确实同时在飞（观察到的事实）

    # A 自己那次冷缓存抓取（在它的门之前）已经发生过 —— 拿它当基线，
    # 之后多出来的任何一次都只能来自 B。事件也要清掉：它已经被 A 点过一次了。
    with lock:
        baseline = state["areas_calls"]
    areas_entered.clear()

    t_b = threading.Thread(target=run)
    t_b.start()
    # 正确实现：B 的 area 抓取被门外纪律挡住，进不了网络（这 0.5s 是给线程被调度
    # 起来的余量）；变异实现（去掉 main.py 里那圈 out_of_query）：抓取立刻打进网络。
    assert areas_entered.wait(0.5) is False
    with lock:
        assert state["areas_calls"] == baseline

    release_query.set()
    t_a.join(10)
    t_b.join(10)
    assert areas_entered.is_set()            # 门一放开照常完成（不是被永久挡住）
    with lock:
        assert state["areas_calls"] == baseline + 1
    assert [r["ok"] for r in results] == [True, True]
    assert state["peak"] == 2                # 全程峰值 2，不是 3


# ---------- F-2：端点的两处取词表调用必须在 try 之内 ----------

def test_library_search_returns_json_when_a_malformed_area_cache_slips_through(monkeypatch):
    """F-2：畸形 area 缓存**绕过了 R13-a** 时，端点也不得返 500。

    终审实测的原始形态：cache 写成合法 JSON 但形状不对（`{"areas": [1,2,3]}`）⇒
    `library_areas.load_areas()` 返回坏条目 ⇒ 端点的
    `{a["code"]: a["name"] for a in area_catalog["areas"]}` 抛
    `TypeError: 'int' object is not subscriptable` ⇒ **HTTP 500 裸栈**，违反
    Global Constraints「后端错误一律返回 200 + JSON」。根因是这两句**不在任何 try 里**。

    R13-a 已从**源头**堵住这条链（形状校验），所以本用例**不能**只把缓存写成畸形 ——
    那样 R13-a 会兜住，F-2 有没有改都绿，就又造出一条假绑定（本批要消灭的正是它）。
    这里显式模拟「缓存被未来某次改动放行」：把 `_read_cache` 换成会原样交出坏形状的
    版本，让坏形状**真的流进**端点那次字典推导 —— 这才是 F-2 那道 try 的判据。

    变异：把这两次调用移回 `try` 之外 ⇒ 本用例红（TestClient 会把异常抛出来）。
    """
    from backend import library_areas
    real_load_areas = library_areas.load_areas          # 必须在 _patch_library 之前抓
    _patch_library(monkeypatch)
    monkeypatch.setattr(library_areas, "load_areas", real_load_areas)   # 撤掉替身，走真路径
    monkeypatch.setattr(library_areas, "_read_cache", lambda: [1, 2, 3])  # 模拟 R13-a 被放行

    r = client.post("/api/library/search", json=dict(_LIB_SEARCH_BODY))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    # area 拿不到只该退化成「没有 area 名」，**不拖垮检索**（spec §4.3）。
    assert body["ok"] is True
    assert body["local"]["items"][0]["title"] == "MyInstitution"


def test_library_search_returns_json_when_the_domain_catalog_raises(monkeypatch):
    """F-2 的另一处：`list_domains()` 抛错时同样必须是 200 + JSON。

    选库是增强不是主干（spec §4.2）—— 词表炸了不该把一次检索升级成 500。
    """
    from backend import library_client
    _patch_library(monkeypatch)

    def boom(**k):
        raise RuntimeError("词表炸了")

    monkeypatch.setattr(library_client, "list_domains", boom)
    r = client.post("/api/library/search", json=dict(_LIB_SEARCH_BODY))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["ok"] is True


# --- 学校邮箱 ---

def test_mail_list_returns_mails(monkeypatch):
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [
        {"id": "A", "subject": "作业", "sender": "李老師",
         "received": "2026-09-17T08:31:00Z", "is_read": False}])
    body = TestClient(main.app).post("/api/mail/list", json={"days": 7}).json()
    assert body["ok"] is True
    assert body["count"] == 1
    assert body["mails"][0]["subject"] == "作业"
    # 响应键白名单。原先这里是 `assert "token" not in json.dumps(body)` ——**真空**：
    # mail dict 由 backend/mail_client.py:126-130 构造，只有
    # id/subject/sender/received/is_read 五个键（字段级钉子见
    # tests/test_mail_client.py:63 的 test_parse_find_items_maps_fields，它用整字典
    # 相等钉死这五个键）；bearer token 是 _capture_token 的局部值，经入参传给
    # collect_items/_post_json，从不落进任何 dict。所以那句「响应里没有 token」
    # 恒真 —— 它看着像在守 spec §9「token 绝不回传前端」，实际守的是个永不出现
    # 的子串，而往响应里加个名字不带 "token" 的凭证字段（如 llm_api_key）仍能全绿。
    # 真正的防护是这里钉死响应键集合，加上 test_mail_client.py 的字段级用例。
    assert set(body) == {"ok", "count", "mails"}


def test_mail_list_defaults_to_seven_days(monkeypatch):
    seen = {}
    # 形状必须是 list。旧写法 `seen.setdefault("d", days) or []` 里 setdefault 返回的是
    # 真值 7，`7 or []` 求值为 7，于是端点 len(7) 抛 TypeError 被 except 吞成
    # {ok: false} —— 用例仍绿，但走的是错误分支，对成功路径零覆盖。
    monkeypatch.setattr(mail_client, "list_mail",
                        lambda days: (seen.setdefault("d", days), [])[1])
    r = TestClient(main.app).post("/api/mail/list", json={})
    assert seen["d"] == 7
    assert r.json()["ok"] is True        # 不能靠异常分支蒙混过关


def test_mail_list_tolerates_bodyless_request(monkeypatch):
    """无 body 的 POST 也要走通 —— 前端 api() 在 body 为 undefined 时不发 body。"""
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [
        {"id": "A", "subject": "作业", "sender": "李老師",
         "received": "2026-09-17T08:31:00Z", "is_read": False}])
    r = TestClient(main.app).post("/api/mail/list")   # 完全不带 body
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 1
    assert body["mails"][0]["subject"] == "作业"
    assert "detail" not in body         # 不能是 FastAPI 的 422 错误体


def test_mail_list_failure_is_200_json(monkeypatch):
    """取信失败不能裸 500 —— 前端 api() 靠 ok 字段走兜底。"""
    def boom(days):
        raise mail_client.MailError("邮箱会话已失效")
    monkeypatch.setattr(mail_client, "list_mail", boom)
    r = TestClient(main.app).post("/api/mail/list", json={})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "邮箱会话已失效"}


def test_mail_summarize_joins_bodies_and_groups(monkeypatch):
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [
        {"id": "A", "subject": "作业", "sender": "李老師",
         "received": "2026-09-17T08:31:00Z", "is_read": False}])
    monkeypatch.setattr(mail_client, "fetch_bodies", lambda items: {"A": "交报告"})
    seen = {}

    def fake_summarize(base, key, model, mails, language="zh"):
        seen["mails"] = mails
        seen["language"] = language
        return {"groups": [], "warnings": ["w"]}

    monkeypatch.setattr(llm_client, "summarize_mail", fake_summarize)
    body = TestClient(main.app).post("/api/mail/summarize", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "days": 7, "language": "zh"}).json()
    assert body["ok"] is True
    assert body["warnings"] == ["w"]
    # index 是回指锚，必须从 0 连续编号
    assert [m["index"] for m in seen["mails"]] == [0]
    assert seen["mails"][0]["body"] == "交报告"
    # 响应键白名单。这里刻意**不**断言「token 不在响应里」：邮箱 bearer token
    # 只活在 mail_client 的 HTTP 层（header），从来不是 mail dict 的字段，所以
    # 那句在 /api/mail/summarize 上恒真 —— 它看着像在守「凭证不回传前端」这条
    # 硬约束，实际守的是个永远不会出现的串，而把 req 的字段（如 llm_api_key）
    # 摊进响应仍能全绿。真正要钉的是「响应只有这四个键」，凭证侧的字段级防护
    # 在 tests/test_mail_client.py。
    assert set(body) == {"ok", "mails", "groups", "warnings"}
    # 逐条邮件的键集合也要钉住：顶层白名单管不到元素，而 `mails` 正是前端的回指表。
    # 用 `<=` 而不是 `==`：端点允许往后加字段（本轮的失败路径就加过 `reason`），
    # 这里守的是「不许把 req 的字段（llm_api_key 等）摊进元素里」。
    assert all(set(m) <= {"id", "subject", "sender", "received", "is_read", "index", "body"}
               for m in body["mails"])


def test_mail_summarize_indexes_are_contiguous(monkeypatch):
    """两封起验证：index 必须是 0,1 而不是固定值 —— 前端靠它回指原文。

    断言只认**调用当刻**的快照。假替身若存 mails 的引用，端点在这之后仍会往
    同一批 dict 里补写 index/body，事后读 seen 看到的是已被改写过的状态，
    于是「把赋值循环挪到 summarize_mail 之后」——线上会让每个非空邮箱都返回
    {ok: false, error: "'index'"}（llm_client 在重试之外硬取 m["index"]）——
    这种改动在用例里照样绿。快照必须在 summarize_mail 内取，那是端点写完
    index/body 之后、返回之前的唯一观察点。
    """
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [
        {"id": "A", "subject": "甲", "sender": "", "received": "", "is_read": False},
        {"id": "B", "subject": "乙", "sender": "", "received": "", "is_read": True}])
    monkeypatch.setattr(mail_client, "fetch_bodies", lambda items: {"A": "a", "B": "b"})
    seen = {}

    def fake_summarize(base, key, model, mails, language="zh"):
        seen["at_call"] = [(m.get("index"), m.get("body")) for m in mails]
        return {"groups": [], "warnings": []}

    monkeypatch.setattr(llm_client, "summarize_mail", fake_summarize)
    body = TestClient(main.app).post("/api/mail/summarize", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m"}).json()
    assert body["ok"] is True
    # 承重断言：传给 LLM 时 index/body 就已写好
    assert seen["at_call"] == [(0, "a"), (1, "b")]
    # 顺带钉住返回给前端的形态
    assert [m["index"] for m in body["mails"]] == [0, 1]
    assert [m["body"] for m in body["mails"]] == ["a", "b"]


def test_mail_summarize_returns_mails_too(monkeypatch):
    """前端要把 AI 结果的 mail_index 对回原邮件 → 响应必须同时带上 mails。"""
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [
        {"id": "A", "subject": "作业", "sender": "", "received": "", "is_read": False}])
    monkeypatch.setattr(mail_client, "fetch_bodies", lambda items: {"A": "b"})
    monkeypatch.setattr(llm_client, "summarize_mail",
                        lambda *a, **k: {"groups": [], "warnings": []})
    body = TestClient(main.app).post("/api/mail/summarize", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m"}).json()
    assert body["mails"][0]["id"] == "A"
    assert body["mails"][0]["body"] == "b"


def test_mail_summarize_failure_is_200_json(monkeypatch):
    def boom(days):
        raise mail_client.MailError("邮箱会话已失效")
    monkeypatch.setattr(mail_client, "list_mail", boom)
    r = TestClient(main.app).post("/api/mail/summarize", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m"})
    assert r.status_code == 200
    # 失败路径的形状 = 原来的两键 + `reason`（本轮新增，前端据此分流登录问询）。
    # 这里刻意写成整字典相等：失败响应的键集合是前端契约的一部分，多一个少一个都该红。
    assert r.json() == {"ok": False, "reason": "mail", "error": "邮箱会话已失效"}


def test_mail_summarize_fetch_bodies_failure_reports_mail_phase(monkeypatch):
    """取信阶段（list_mail / fetch_bodies）炸了 → reason == "mail"。

    前端据此才敢发手动登录问询：这一段失败多半是 Okta 会话失效。若把 phase 写死在
    "llm" 上，用户会被指去查 API key —— 方向是错的。
    """
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [
        {"id": "A", "subject": "作业", "sender": "", "received": "", "is_read": False}])

    def boom(items):
        raise mail_client.MailError("邮箱会话已失效")
    monkeypatch.setattr(mail_client, "fetch_bodies", boom)
    body = TestClient(main.app).post("/api/mail/summarize", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m"}).json()
    assert body["ok"] is False
    assert body["reason"] == "mail"


def test_mail_summarize_llm_failure_reports_llm_phase(monkeypatch):
    """AI 阶段炸了 → reason == "llm"：**不许**发手动登录问询（那是 API key 的问题）。"""
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [
        {"id": "A", "subject": "作业", "sender": "", "received": "", "is_read": False}])
    monkeypatch.setattr(mail_client, "fetch_bodies", lambda items: {"A": "b"})

    def boom(*a, **k):
        raise RuntimeError("模型服务 500")
    monkeypatch.setattr(llm_client, "summarize_mail", boom)
    body = TestClient(main.app).post("/api/mail/summarize", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m"}).json()
    assert body["ok"] is False
    assert body["reason"] == "llm"


def test_mail_summarize_no_mail_skips_llm(monkeypatch):
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [])
    monkeypatch.setattr(mail_client, "fetch_bodies", lambda items: {})
    called = []
    monkeypatch.setattr(llm_client, "summarize_mail",
                        lambda *a, **k: called.append(1) or {"groups": [], "warnings": []})
    body = TestClient(main.app).post("/api/mail/summarize", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m"}).json()
    assert body["ok"] is True
    assert body["mails"] == []
    assert called == []          # 没邮件就别浪费一次 LLM 调用


def test_mail_summarize_missing_body_falls_back_to_empty(monkeypatch):
    """取不到正文的那封退化成空串，不能把整次请求打成 {ok: false}。

    取正文是尽力而为的：`bodies.get(m["id"], "")` 若被写成 `bodies[m["id"]]`，
    一封取不到正文的邮件会让整个响应变成 {ok: false, error: "'B'"}（KeyError
    被端点的宽 except 吞掉），其余邮件的摘要也随之全丢。
    """
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [
        {"id": "A", "subject": "甲", "sender": "", "received": "", "is_read": False},
        {"id": "B", "subject": "乙", "sender": "", "received": "", "is_read": True}])
    monkeypatch.setattr(mail_client, "fetch_bodies",
                        lambda items: {"A": "a"})      # B 没取到正文
    monkeypatch.setattr(llm_client, "summarize_mail",
                        lambda *a, **k: {"groups": [], "warnings": []})
    body = TestClient(main.app).post("/api/mail/summarize", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m"}).json()
    assert body["ok"] is True
    assert [m["id"] for m in body["mails"]] == ["A", "B"]
    assert [m["body"] for m in body["mails"]] == ["a", ""]
