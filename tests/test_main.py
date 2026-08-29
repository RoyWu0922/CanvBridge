from fastapi.testclient import TestClient

from backend import apple_script, canvas_client, files_downloader, llm_client, main

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


def test_sync_announcements(monkeypatch):
    monkeypatch.setattr(canvas_client, "get_announcements", lambda u, t, ids, a, b: {5: [{"title": "T", "message": "M", "posted_at": ""}]})
    monkeypatch.setattr(canvas_client, "list_courses", lambda u, t: [{"id": 5, "name": "CS 101"}])
    monkeypatch.setattr(llm_client, "extract_course_summary",
                        lambda *a, **k: {"course_name": "CS 101", "summary_cn": "要点", "calendar_events": [], "reminders": []})
    body = {"canvas_url": "https://x", "canvas_token": "t", "llm_base_url": "https://llm/v1",
            "llm_api_key": "k", "llm_model": "m", "course_ids": [5],
            "start_date": "2026-08-01", "end_date": "2026-08-31"}
    r = client.post("/api/sync_announcements", json=body)
    assert r.json()["ok"] is True
    assert r.json()["courses"][0]["summary_cn"] == "要点"


def test_add_calendar_event(monkeypatch):
    called = {}
    def add(calendar_name, title, start, end, location, notes):
        called.update(locals())
    monkeypatch.setattr(apple_script, "add_calendar_event", add)
    r = client.post("/api/add_calendar_event", json={
        "calendar_name": "Study", "title": "Quiz", "start": "2026-08-31T14:00:00",
        "end": "2026-08-31T15:00:00", "location": "A101", "notes": ""})
    assert r.json() == {"ok": True}
    assert called["title"] == "Quiz"


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
