import requests

from backend import canvas_client


class _Resp:
    def __init__(self, data, link=""):
        self._data = data
        self.headers = {"Link": link}
        self.status_code = 200

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, pages):
        self.pages = pages  # [(data, link), ...]
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params))
        data, link = self.pages[len(self.calls) - 1]
        return _Resp(data, link)


def test_next_link():
    link = '<http://x/api/v1/courses?page=2>; rel="next", <http://x/api/v1/courses?page=1>; rel="prev"'
    assert canvas_client._next_link(link) == "http://x/api/v1/courses?page=2"
    assert canvas_client._next_link("") is None


def test_paginate_follows_next_links():
    s = _Session([
        ([{"id": 1}], '<http://x/api/v1/courses?page=2>; rel="next"'),
        ([{"id": 2}], ""),
    ])
    result = canvas_client._paginate(s, "http://x/api/v1/courses", {"per_page": 100}, "tok")
    assert result == [{"id": 1}, {"id": 2}]
    # 首次带 params，后续 next 链接自带 query、params 清空
    assert s.calls[0][1] == {"per_page": 100}
    assert s.calls[1][1] == {}


def test_paginate_raises_canvas_error_on_401():
    class _BadResp(_Resp):
        def __init__(self, data, link=""):
            super().__init__(data, link)
            self.status_code = 401

    class _BadSession:
        def get(self, url, params=None, headers=None):
            return _BadResp([])

    import pytest
    with pytest.raises(canvas_client.CanvasError):
        canvas_client._paginate(_BadSession(), "http://x/api/v1/courses", {}, "tok")


def test_list_courses_maps_fields(monkeypatch):
    captured = {}

    def _fake_paginate(*args):
        captured["url"] = args[1]
        captured["params"] = args[2]
        return [{"id": 42, "name": "CS 101"}, {"id": 43}]

    monkeypatch.setattr(canvas_client, "_paginate", _fake_paginate)
    courses = canvas_client.list_courses("https://x.instructure.com", "tok")
    assert courses == [
        {"id": 42, "name": "CS 101"},
        {"id": 43, "name": "Course 43"},
    ]
    assert captured["url"] == "https://x.instructure.com/api/v1/courses"
    assert captured["params"]["per_page"] == 100


def test_list_courses_includes_invited_enrollments(monkeypatch):
    """enrollment_state 须含 invited，否则未接受邀请的新课程会被 Canvas 静默漏掉。"""
    captured = {}

    def _fake_paginate(*args):
        captured.update(args[2])
        return []

    monkeypatch.setattr(canvas_client, "_paginate", _fake_paginate)
    canvas_client.list_courses("https://x.instructure.com", "tok")
    assert captured["enrollment_state"] == "current_and_invited"


def test_strip_html():
    html = "<h2>Quiz</h2><p>On <b>Monday</b>.</p><ul><li>Bring calc</li></ul>"
    text = canvas_client.strip_html(html)
    assert "Quiz" in text and "Monday" in text and "Bring calc" in text
    assert "<" not in text


def test_get_announcements_groups_and_strips(monkeypatch):
    fake = [
        {"id": 1, "context_code": "course_5", "title": "A",
         "message": "<p>Hello <b>world</b></p>", "posted_at": "2026-08-29T10:00:00Z"},
        {"id": 2, "context_code": "course_7", "title": "B",
         "message": "plain", "posted_at": "2026-08-28T10:00:00Z"},
        {"id": 3, "context_code": "group_1", "title": "ignored", "message": "x", "posted_at": ""},
    ]
    monkeypatch.setattr(canvas_client, "_paginate", lambda s, u, p, t: fake)
    result = canvas_client.get_announcements("https://x", "tok", [5, 7], "2026-08-01", "2026-08-31")
    assert set(result.keys()) == {5, 7}
    assert result[5][0]["message"] == "Hello world"
    assert result[5][0]["title"] == "A"
    assert result[7][0]["message"] == "plain"


def test_get_course_files_maps(monkeypatch):
    files_data = [{
        "id": 9, "display_name": "a.pdf", "folder_id": 1,
        "content-type": "application/pdf", "size": 10,
        "url": "http://x/courses/1/files/9/download",
    }]
    folders_data = [{"id": 1, "name": "Slides", "parent_folder_id": None}]
    monkeypatch.setattr(
        canvas_client, "_paginate",
        lambda s, u, p, t: files_data if "/files" in u else folders_data,
    )
    files, folders = canvas_client.get_course_files("https://x", "tok", 1)
    assert files[0]["id"] == 9
    assert files[0]["content_type"] == "application/pdf"
    assert folders == [{"id": 1, "name": "Slides", "parent_folder_id": None}]


def test_get_file_returns_url(monkeypatch):
    monkeypatch.setattr(
        requests, "get",
        lambda *a, **k: _Resp({"id": 9, "url": "http://x/files/9/download"}),
    )
    info = canvas_client.get_file("https://x", "tok", 1, 9)
    assert info["url"] == "http://x/files/9/download"


def test_download_file_writes(tmp_path, monkeypatch):
    class _StreamResp(_Resp):
        def __init__(self):
            super().__init__({})
            self._chunks = [b"abc", b"def"]

        def iter_content(self, chunk_size):
            return iter(self._chunks)

    monkeypatch.setattr(requests, "get", lambda *a, **k: _StreamResp())
    dest = tmp_path / "out" / "a.pdf"
    canvas_client.download_file("https://x", "tok", "http://x/files/9/download", str(dest))
    assert dest.read_bytes() == b"abcdef"


def test_get_course_maps_fields(monkeypatch):
    """单课程 GET 返回 dict（非列表），须直接取 resp.json()，teachers 走 users 端点。"""
    course = {"id": 42, "name": "CS 101",
              "syllabus_body": "<p>Welcome to <b>CS 101</b></p>"}

    class _CtxSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None, headers=None, **kwargs):
            return _Resp(course)

    monkeypatch.setattr(requests, "Session", lambda: _CtxSession())
    monkeypatch.setattr(canvas_client, "_paginate",
                        lambda s, u, p, t: [{"name": "Alice"}, {"name": "Bob"}])
    result = canvas_client.get_course("https://x.instructure.com", "tok", 42)
    assert result["id"] == 42
    assert result["name"] == "CS 101"
    assert result["syllabus_text"] == "Welcome to CS 101"   # strip_html 去标签
    assert result["teachers"] == ["Alice", "Bob"]


def test_get_course_no_syllabus(monkeypatch):
    """syllabus_body 缺失（空课程）→ syllabus_text 空串，teachers 空列表。"""
    class _CtxSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None, headers=None, **kwargs):
            return _Resp({"id": 42, "name": "CS 101"})

    monkeypatch.setattr(requests, "Session", lambda: _CtxSession())
    monkeypatch.setattr(canvas_client, "_paginate", lambda s, u, p, t: [])
    result = canvas_client.get_course("https://x.instructure.com", "tok", 42)
    assert result["syllabus_text"] == ""
    assert result["teachers"] == []
