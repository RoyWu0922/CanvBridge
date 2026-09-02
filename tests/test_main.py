from fastapi.testclient import TestClient

from backend import apple_script, banweb, canvas_client, credentials, files_downloader, llm_client, main

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
        return {"course_name": name, "summary": "要点", "calendar_events": [{"title": "E"}],
                "reminders": [], "warning": ""}
    monkeypatch.setattr(llm_client, "extract_course_summary", fake)
    body = {"canvas_url": "https://x", "canvas_token": "t", "llm_base_url": "https://llm/v1",
            "llm_api_key": "k", "llm_model": "m", "course_id": 5, "course_name": "CS 101",
            "announcements": [{"title": "T", "message": "M"}], "language": "en"}
    r = client.post("/api/summarize_course", json=body)
    data = r.json()
    assert data["ok"] is True
    assert data["summary"] == "要点"
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
    r = client.post("/api/course_detail",
                    json={"canvas_url": "https://x", "canvas_token": "t", "course_id": 5})
    assert r.json() == {"ok": True, "course": {"id": 5, "name": "CS 101",
                                               "syllabus_text": "s", "teachers": ["A"]}}


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
    def fake(base, key, model, name, text, language="zh"):
        captured.update(name=name, language=language)
        return "要点"
    monkeypatch.setattr(llm_client, "summarize_syllabus", fake)
    r = client.post("/api/summarize_syllabus", json={
        "canvas_url": "https://x", "canvas_token": "t", "llm_base_url": "https://llm/v1",
        "llm_api_key": "k", "llm_model": "m", "course_id": 5, "language": "zh"})
    assert r.json() == {"ok": True, "summary": "要点"}
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
