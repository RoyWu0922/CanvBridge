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

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None, headers=None, timeout=None):
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
        def get(self, url, params=None, headers=None, timeout=None):
            return _BadResp([])

    import pytest
    with pytest.raises(canvas_client.CanvasError):
        canvas_client._paginate(_BadSession(), "http://x/api/v1/courses", {}, "tok")


def test_paginate_sets_timeout():
    seen = {}

    class _T:
        def get(self, url, params=None, headers=None, timeout=None):
            seen["timeout"] = timeout
            return _Resp([])

    canvas_client._paginate(_T(), "http://x/api/v1/courses", {}, "tok")
    assert seen["timeout"] == 30


def test_list_courses_maps_fields(monkeypatch):
    captured = {}

    def _fake_paginate(*args):
        captured["url"] = args[1]
        captured["params"] = args[2]
        return [{"id": 42, "name": "CS 101", "course_code": "CS101A"}, {"id": 43}]

    monkeypatch.setattr(canvas_client, "_paginate", _fake_paginate)
    courses = canvas_client.list_courses("https://x.instructure.com", "tok")
    assert courses == [
        {"id": 42, "name": "CS 101", "course_code": "CS101A"},
        {"id": 43, "name": "Course 43", "course_code": ""},
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


class _StreamResp(_Resp):
    """流式响应：支持 with 上下文，逐块吐字节，可中途抛错模拟断网。"""

    def __init__(self, chunks, fail_after=None):
        super().__init__({})
        self._chunks = chunks
        self._fail_after = fail_after

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size):
        for i, chunk in enumerate(self._chunks):
            if self._fail_after is not None and i >= self._fail_after:
                raise RuntimeError("connection reset")
            yield chunk


def test_download_file_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _StreamResp([b"abc", b"def"]))
    dest = tmp_path / "out" / "a.pdf"
    canvas_client.download_file("https://x", "tok", "http://x/files/9/download", str(dest))
    assert dest.read_bytes() == b"abcdef"
    # 原子改名后不留 .part 临时文件
    assert not (tmp_path / "out" / "a.pdf.part").exists()


def test_download_file_atomic_no_partial_on_failure(tmp_path, monkeypatch):
    """中途断流：不落任何半截文件到目标路径，临时文件也被清理（M3）。"""
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: _StreamResp([b"abc", b"def"], fail_after=1))
    dest = tmp_path / "out" / "a.pdf"
    import pytest
    with pytest.raises(RuntimeError):
        canvas_client.download_file("https://x", "tok", "http://x/files/9/download", str(dest))
    assert not dest.exists()
    assert not (tmp_path / "out" / "a.pdf.part").exists()


def test_stream_file_yields_chunks(monkeypatch):
    """stream_file 逐块 yield 文件字节，可整体拼回原文。"""

    class _StreamResp(_Resp):
        def __init__(self):
            super().__init__({})
            self._chunks = [b"%PDF-", b"1.4\n", b"abc"]

        def __enter__(self): return self
        def __exit__(self, *a): return False

        def iter_content(self, chunk_size):
            return iter(self._chunks)

    monkeypatch.setattr(requests, "get", lambda *a, **k: _StreamResp())
    got = b"".join(canvas_client.stream_file("https://x", "tok", "http://x/files/9/download"))
    assert got == b"%PDF-1.4\nabc"


def test_stream_file_raises_on_401(monkeypatch):
    """Canvas 401 → stream_file 抛 CanvasError（而非 yield 空）。"""

    class _Denied(_Resp):
        def __init__(self):
            super().__init__({})
            self.status_code = 401

        def __enter__(self): return self
        def __exit__(self, *a): return False

        def iter_content(self, chunk_size):
            return iter(())

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Denied())
    try:
        list(canvas_client.stream_file("https://x", "tok", "http://x/files/9/download"))
    except canvas_client.CanvasError as exc:
        assert "401" in str(exc)
        return
    assert False, "应当抛 CanvasError"


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


def test_get_modules_maps_drops_and_prefixes(monkeypatch):
    """modules 映射：无链接 item 丢弃、空 module 丢弃、相对 html_url 补全 base。"""
    data = [
        {"id": 1, "name": "Week 1", "items": [
            {"id": 11, "title": "Lecture", "type": "Page",
             "html_url": "https://x.instructure.com/courses/42/pages/11", "external_url": ""},
            {"id": 12, "title": "Sub heading", "type": "SubHeader",
             "html_url": None, "external_url": None},
            {"id": 13, "title": "Tool", "type": "ExternalTool",
             "html_url": "/courses/42/modules/items/13", "external_url": "https://tool.example/x"},
        ]},
        {"id": 2, "name": "Empty", "items": []},
    ]
    monkeypatch.setattr(canvas_client, "_paginate", lambda s, u, p, t: data)
    out = canvas_client.get_modules("https://x.instructure.com", "tok", 42)
    assert len(out) == 1                                     # 空 module 丢弃
    assert out[0]["name"] == "Week 1"
    assert [i["title"] for i in out[0]["items"]] == ["Lecture", "Tool"]
    assert out[0]["items"][0]["url"] == "https://x.instructure.com/courses/42/pages/11"
    assert out[0]["items"][1]["url"] == "https://x.instructure.com/courses/42/modules/items/13"


def test_get_modules_none_have_linkable_items(monkeypatch):
    """只有 SubHeader/无链接项的 modules → 全丢弃返回 []。"""
    data = [{"id": 1, "name": "Week 1",
             "items": [{"id": 12, "title": "Sub heading", "type": "SubHeader",
                        "html_url": None, "external_url": None}]}]
    monkeypatch.setattr(canvas_client, "_paginate", lambda s, u, p, t: data)
    assert canvas_client.get_modules("https://x.instructure.com", "tok", 42) == []


def test_get_modules_file_items_carry_file_id(monkeypatch):
    """File item 从 content_id 取 file_id；无 URL 但有 content_id 仍保留；其余类型 file_id 为 None。"""
    data = [{"id": 1, "name": "Week 1", "items": [
        {"id": 11, "title": "syllabus.pdf", "type": "File",
         "html_url": "https://x.instructure.com/courses/42/modules/items/11",
         "content_id": 77},
        {"id": 12, "title": "no-page.pdf", "type": "File",
         "html_url": None, "content_id": 88},
        {"id": 13, "title": "Lecture", "type": "Page",
         "html_url": "https://x.instructure.com/courses/42/pages/13", "content_id": None},
    ]}]
    monkeypatch.setattr(canvas_client, "_paginate", lambda s, u, p, t: data)
    out = canvas_client.get_modules("https://x.instructure.com", "tok", 42)
    items = out[0]["items"]
    assert len(items) == 3                                   # 无 URL 的 File 也保留
    assert items[0] == {"id": 11, "title": "syllabus.pdf", "type": "File",
                        "url": "https://x.instructure.com/courses/42/modules/items/11",
                        "file_id": 77}
    assert items[1]["file_id"] == 88
    assert items[1]["url"] == ""
    assert items[2]["file_id"] is None                       # 非 File 不设 file_id


def test_get_modules_file_content_id_bad(monkeypatch):
    """content_id 缺失或非数字 → file_id 回退 None，File 项不崩。"""
    data = [{"id": 1, "name": "Week 1", "items": [
        {"id": 11, "title": "f1", "type": "File",
         "html_url": "https://x.instructure.com/courses/42/modules/items/11",
         "content_id": "not-a-number"},
        {"id": 12, "title": "f2", "type": "File",
         "html_url": "https://x.instructure.com/courses/42/modules/items/12",
         "content_id": None},
    ]}]
    monkeypatch.setattr(canvas_client, "_paginate", lambda s, u, p, t: data)
    out = canvas_client.get_modules("https://x.instructure.com", "tok", 42)
    assert [i["file_id"] for i in out[0]["items"]] == [None, None]


def test_get_assignments_filters_and_builds(monkeypatch):
    """已截止丢弃、未来/无截止保留；html_url 缺失拼兜底。"""
    import datetime as _dt
    future = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=7)).isoformat()
    past = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=7)).isoformat()
    data = [
        {"id": 1, "name": "Future HW", "due_at": future,
         "points_possible": 10, "html_url": "http://x/a/1"},
        {"id": 2, "name": "Past HW", "due_at": past,
         "points_possible": 10, "html_url": "http://x/a/2"},
        {"id": 3, "name": "No Due", "due_at": None,
         "points_possible": 5, "html_url": "http://x/a/3"},
        {"id": 4, "name": "No Url", "due_at": future,
         "points_possible": 5, "html_url": None},
    ]
    monkeypatch.setattr(canvas_client, "_paginate", lambda s, u, p, t: data)
    result = canvas_client.get_assignments("https://x.instructure.com", "tok", 42)
    assert [a["id"] for a in result] == [1, 3, 4]      # 已截止的 2 被丢弃
    assert result[2]["html_url"] == "https://x.instructure.com/courses/42/assignments/4"
    assert result[2]["due_at"] == future


def test_get_assignments_drops_unparseable_due(monkeypatch):
    """due_at 无法解析 → 丢弃（宁可不上日历，不误展示）。"""
    monkeypatch.setattr(canvas_client, "_paginate",
                        lambda s, u, p, t: [{"id": 9, "name": "X",
                                             "due_at": "not-a-date", "html_url": "http://x/9"}])
    result = canvas_client.get_assignments("https://x", "tok", 42)
    assert result == []


def test_list_courses_include_scores(monkeypatch):
    s = _Session([([{
        "id": 1, "name": "CS101", "course_code": "CS101A",
        "enrollments": [{"grades": {"current_score": 88.5, "final_score": 85.0}}],
    }], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.list_courses("https://x", "tok", include_scores=True)
    assert out[0]["current_score"] == 88.5
    assert out[0]["final_score"] == 85.0
    # 请求带 include[] 数组参数
    assert s.calls[0][1]["include[]"] == ["enrollments", "total_scores"]


def test_list_courses_include_scores_missing(monkeypatch):
    s = _Session([([{"id": 1, "name": "CS101", "course_code": "CS101A"}], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.list_courses("https://x", "tok", include_scores=True)
    assert out[0]["current_score"] is None
    assert out[0]["final_score"] is None


def test_list_courses_default_no_include_scores(monkeypatch):
    s = _Session([([{"id": 1, "name": "CS101", "course_code": "CS101A"}], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.list_courses("https://x", "tok")
    assert "include[]" not in s.calls[0][1]
    assert "current_score" not in out[0]


def test_get_assignments_full(monkeypatch):
    s = _Session([([{
        "id": 9, "name": "HW1", "due_at": "2026-09-10T23:59:59Z",
        "points_possible": 10, "html_url": "https://x/c/1/a/9",
        "submission": {"score": 8.0, "submitted_at": "2026-09-09T10:00:00Z"},
    }], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_assignments_full("https://x", "tok", 1)
    assert out[0]["score"] == 8.0
    assert out[0]["submitted"] is True


def test_get_assignments_full_no_submission(monkeypatch):
    s = _Session([([{"id": 9, "name": "HW1", "due_at": ""}], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_assignments_full("https://x", "tok", 1)
    assert out[0]["score"] is None
    assert out[0]["submitted"] is False


def test_get_todo_normalizes_and_sorts(monkeypatch):
    s = _Session([([
        {"type": "Assignment",
         "assignment": {"id": 1, "name": "Late", "due_at": "2026-08-20T23:59:59Z",
                        "html_url": "https://x/c/1/a/1", "points_possible": 5, "course_id": 1},
         "context_name": "CS101"},
        {"type": "Quiz",
         "assignment": {"id": 2, "name": "Soon", "due_at": "2099-01-01T10:00:00Z",
                        "html_url": "", "points_possible": None, "course_id": 2},
         "context_name": "MA200"},
    ], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_todo("https://x", "tok")
    assert out[0]["overdue"] is True
    assert out[0]["type"] == "Assignment"
    assert out[1]["overdue"] is False
    assert out[1]["course_name"] == "MA200"
    # 按 due_at 升序：Late 在前
    assert out[0]["title"] == "Late"


def test_get_todo_undated_last(monkeypatch):
    s = _Session([([
        {"type": "Assignment",
         "assignment": {"id": 1, "name": "NoDue", "due_at": None, "course_id": 1},
         "context_name": "CS101"},
        {"type": "Assignment",
         "assignment": {"id": 2, "name": "Dated", "due_at": "2026-09-20T23:59:59Z", "course_id": 1},
         "context_name": "CS101"},
    ], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_todo("https://x", "tok")
    assert out[0]["title"] == "Dated"
    assert out[1]["title"] == "NoDue"


def test_get_calendar_events_filters_assignment(monkeypatch):
    s = _Session([([
        {"id": 1, "type": "event", "title": "Guest Talk", "context_code": "course_5",
         "start_at": "2026-09-10T14:00:00Z", "end_at": "2026-09-10T15:00:00Z",
         "location_name": "LT-1", "html_url": "https://x/c/5/e/1"},
        {"id": 2, "type": "assignment", "title": "HW1", "context_code": "course_5",
         "start_at": "2026-09-10T23:59:59Z"},
    ], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_calendar_events("https://x", "tok", [5], "2026-09-01", "2026-09-30")
    assert len(out) == 1
    assert out[0]["title"] == "Guest Talk"
    assert out[0]["course_id"] == 5
    assert s.calls[0][1]["context_codes[]"] == ["course_5"]


# ===== 新数据源：quizzes / pages / page_body =====

def test_get_quizzes_maps_and_sorts(monkeypatch):
    s = _Session([([
        {"id": 2, "title": "小测二", "due_at": None, "lock_at": None,
         "points_possible": 10, "quiz_type": "practice_quiz",
         "time_limit": None, "question_count": 5,
         "html_url": "https://x/courses/1/quizzes/2", "published": True},
        {"id": 1, "title": "小测一", "due_at": "2026-09-20T15:59:00Z",
         "lock_at": "2026-09-21T15:59:00Z", "points_possible": 100,
         "quiz_type": "assignment", "time_limit": 60, "question_count": 20,
         "html_url": "https://x/courses/1/quizzes/1", "published": True},
    ], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_quizzes("https://x", "tok", 1)
    # 有截止的在前，无截止（due_at=None → ""）排最后
    assert [q["id"] for q in out] == [1, 2]
    assert out[1]["due_at"] == ""      # null → 空串，与 get_todo 同惯例
    assert out[1]["lock_at"] == ""
    assert out[0]["quiz_type"] == "assignment"
    assert out[0]["question_count"] == 20
    assert out[0]["time_limit"] == 60
    assert out[1]["time_limit"] is None
    assert out[0]["published"] is True
    # 请求参数
    assert s.calls[0][0] == "https://x/api/v1/courses/1/quizzes"
    assert s.calls[0][1] == {"per_page": 100}


def test_get_quizzes_filters_unpublished(monkeypatch):
    s = _Session([([
        {"id": 1, "title": "已发布", "published": True},
        {"id": 2, "title": "未发布", "published": False},
        {"id": 3, "title": "缺字段"},
    ], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_quizzes("https://x", "tok", 1)
    assert [q["id"] for q in out] == [1]


def test_get_quizzes_404_raises_tool_disabled(monkeypatch):
    """实测：6 门课里 3 门未启用测验工具 → 404。这是常态，必须与真故障可区分。"""
    import pytest

    def boom(session, url, params, token):
        resp = requests.Response()
        resp.status_code = 404
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(canvas_client, "_paginate", boom)
    with pytest.raises(canvas_client.CanvasToolDisabled):
        canvas_client.get_quizzes("https://x", "tok", 70488)


def test_paginate_allow_disabled_reraises_other_status(monkeypatch):
    """非 404 的错误不能被吞掉。"""
    import pytest

    def boom(session, url, params, token):
        resp = requests.Response()
        resp.status_code = 500
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(canvas_client, "_paginate", boom)
    with pytest.raises(requests.HTTPError):
        canvas_client._paginate_allow_disabled(None, "https://x/api/v1/a", {}, "tok")


def test_paginate_allow_disabled_reraises_canvas_error(monkeypatch):
    """401/403 走 CanvasError，不能被误判成「工具未启用」。"""
    import pytest

    def boom(session, url, params, token):
        raise canvas_client.CanvasError("Canvas token 无效或已过期 (HTTP 401)")

    monkeypatch.setattr(canvas_client, "_paginate", boom)
    with pytest.raises(canvas_client.CanvasError) as ei:
        canvas_client._paginate_allow_disabled(None, "https://x/api/v1/a", {}, "tok")
    assert not isinstance(ei.value, canvas_client.CanvasToolDisabled)


def test_get_pages_maps_filters_and_sorts(monkeypatch):
    s = _Session([([
        {"url": "syllabus", "title": "Syllabus", "updated_at": "2026-09-01T00:00:00Z",
         "published": True, "front_page": False, "html_url": "https://x/courses/1/pages/syllabus"},
        {"url": "home", "title": "Course Home", "updated_at": "2026-09-02T00:00:00Z",
         "published": True, "front_page": True, "html_url": "https://x/courses/1/pages/home"},
        {"url": "draft", "title": "Draft", "published": False, "front_page": False},
    ], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_pages("https://x", "tok", 1)
    assert [p["url"] for p in out] == ["home", "syllabus"]   # front_page 置顶，其余按标题
    assert out[0]["front_page"] is True
    assert out[1]["title"] == "Syllabus"
    assert "body" not in out[0]                              # 列表不带正文
    assert s.calls[0][0] == "https://x/api/v1/courses/1/pages"


def test_get_pages_404_raises_tool_disabled(monkeypatch):
    import pytest

    def boom(session, url, params, token):
        resp = requests.Response()
        resp.status_code = 404
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(canvas_client, "_paginate", boom)
    with pytest.raises(canvas_client.CanvasToolDisabled):
        canvas_client.get_pages("https://x", "tok", 70488)


def test_get_page_body_strips_html(monkeypatch):
    """实测：原始 body 是 HTML 片段，必须过 strip_html。"""
    s = _Session([({
        "url": "home", "title": "Course Home",
        "body": '<p>Adapted from <a href="https://x">the source</a></p><ul><li>要点一</li></ul>',
        "updated_at": "2026-09-02T00:00:00Z",
        "html_url": "https://x/courses/1/pages/home",
    }, "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_page_body("https://x", "tok", 1, "home")
    assert out["title"] == "Course Home"
    assert "<p>" not in out["body_text"]
    assert "<a href" not in out["body_text"]
    assert "Adapted from" in out["body_text"]
    assert "要点一" in out["body_text"]
    assert out["html_url"] == "https://x/courses/1/pages/home"   # 此端点实测是绝对 URL
    assert s.calls[0][0] == "https://x/api/v1/courses/1/pages/home"


def test_get_page_body_quotes_page_url(monkeypatch):
    """page_url 来自 Canvas 的 slug，进 URL 前必须转义。"""
    s = _Session([({"url": "a b", "title": "T", "body": ""}, "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    canvas_client.get_page_body("https://x", "tok", 1, "a b")
    assert s.calls[0][0] == "https://x/api/v1/courses/1/pages/a%20b"


def test_get_page_body_404_is_plain_http_error(monkeypatch):
    """单页正文没有「工具未启用」语义，404 就是真失败，走端点层 errors。"""
    import pytest

    class _NotFound(_Resp):
        def __init__(self, data=None, link=""):
            super().__init__(data, link)
            self.status_code = 404

        def raise_for_status(self):
            resp = requests.Response()
            resp.status_code = 404
            raise requests.HTTPError(response=resp)

    class _S:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None, timeout=None):
            return _NotFound()

    monkeypatch.setattr(requests, "Session", lambda: _S())
    with pytest.raises(requests.HTTPError):
        canvas_client.get_page_body("https://x", "tok", 1, "home")


# ===== 新数据源：discussion_topics / planner =====

def test_get_discussions_maps_subentry_count(monkeypatch):
    """实测：回复数字段是 discussion_subentry_count，不是 replies_count。"""
    s = _Session([([
        {"id": 7, "title": "第一次讨论", "posted_at": "2026-09-05T00:00:00Z",
         "last_reply_at": "2026-09-06T00:00:00Z",
         "author": {"display_name": "张三"},
         "discussion_subentry_count": 12, "unread_count": 0,
         "read_state": "read", "pinned": False, "locked": False,
         "require_initial_post": True, "html_url": "https://x/t/7",
         "is_announcement": False},
    ], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_discussion_topics("https://x", "tok", 1)
    assert out[0]["replies_count"] == 12
    assert out[0]["author"] == "张三"
    assert out[0]["require_initial_post"] is True
    assert s.calls[0][1] == {"per_page": 100, "order_by": "recent_activity"}


def test_get_discussions_author_empty_array(monkeypatch):
    """实测 71134 的 author 是空数组 []。直接下标会 TypeError 打死整批。"""
    s = _Session([([
        {"id": 1, "title": "无作者", "author": [], "discussion_subentry_count": 0},
    ], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_discussion_topics("https://x", "tok", 1)
    assert out[0]["author"] == ""


def test_get_discussions_author_display_name_none(monkeypatch):
    """实测 display_name 可能是 null → or ""。"""
    s = _Session([([
        {"id": 1, "title": "空名", "author": {"display_name": None},
         "discussion_subentry_count": 3},
    ], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_discussion_topics("https://x", "tok", 1)
    assert out[0]["author"] == ""


def test_get_discussions_read_state_and_unread_count_are_independent(monkeypatch):
    """实测：read_state 是根帖已读状态，unread_count 是未读回复数，两者独立。
    同一门课里既有 read+8 也有 unread+0。两个都要原样透传。"""
    s = _Session([([
        {"id": 1, "title": "根帖已读但 8 条回复没读",
         "read_state": "read", "unread_count": 8,
         "discussion_subentry_count": 8, "last_reply_at": "2026-09-08T00:00:00Z"},
        {"id": 2, "title": "根帖没读但无人回复",
         "read_state": "unread", "unread_count": 0,
         "discussion_subentry_count": 0, "last_reply_at": "2026-09-07T00:00:00Z"},
    ], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = {t["id"]: t for t in canvas_client.get_discussion_topics("https://x", "tok", 1)}
    assert (out[1]["read_state"], out[1]["unread_count"]) == ("read", 8)
    assert (out[2]["read_state"], out[2]["unread_count"]) == ("unread", 0)


def test_get_discussions_missing_fields_default(monkeypatch):
    s = _Session([([
        {"id": 1, "title": "缺字段", "discussion_subentry_count": 0},
    ], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_discussion_topics("https://x", "tok", 1)
    assert out[0]["read_state"] == "read"     # 缺失按已读
    assert out[0]["unread_count"] == 0
    assert out[0]["posted_at"] == ""
    assert out[0]["last_reply_at"] == ""


def test_get_discussions_filters_announcements(monkeypatch):
    s = _Session([([
        {"id": 1, "title": "真讨论", "is_announcement": False,
         "discussion_subentry_count": 1},
        {"id": 2, "title": "其实是公告", "is_announcement": True,
         "discussion_subentry_count": 1},
    ], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_discussion_topics("https://x", "tok", 1)
    assert [t["id"] for t in out] == [1]


def test_get_discussions_sort_pinned_then_activity(monkeypatch):
    """pinned 优先；其次 last_reply_at 降序，为 None 退到 posted_at；两者都 None 排最后。"""
    s = _Session([([
        {"id": 1, "title": "老帖", "last_reply_at": "2026-09-01T00:00:00Z",
         "posted_at": "2026-08-01T00:00:00Z", "discussion_subentry_count": 0},
        {"id": 2, "title": "置顶的老帖", "pinned": True,
         "last_reply_at": "2026-08-15T00:00:00Z", "posted_at": "2026-08-01T00:00:00Z",
         "discussion_subentry_count": 0},
        {"id": 3, "title": "新帖", "last_reply_at": "2026-09-09T00:00:00Z",
         "posted_at": "2026-09-09T00:00:00Z", "discussion_subentry_count": 0},
        {"id": 4, "title": "无回复，退 posted_at", "last_reply_at": None,
         "posted_at": "2026-09-05T00:00:00Z", "discussion_subentry_count": 0},
        {"id": 5, "title": "两个都 None", "last_reply_at": None,
         "posted_at": None, "discussion_subentry_count": 0},
    ], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_discussion_topics("https://x", "tok", 1)
    assert [t["id"] for t in out] == [2, 3, 4, 1, 5]


def test_get_discussions_404_raises_tool_disabled(monkeypatch):
    import pytest

    def boom(session, url, params, token):
        resp = requests.Response()
        resp.status_code = 404
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(canvas_client, "_paginate", boom)
    with pytest.raises(canvas_client.CanvasToolDisabled):
        canvas_client.get_discussion_topics("https://x", "tok", 1)


def _planner_payload():
    return [
        {"plannable_id": 101, "plannable_type": "assignment",
         "plannable_date": "2026-09-12T15:59:00Z", "course_id": 5,
         "context_name": "DSC1001 Introduction to Data Science",
         "html_url": "/courses/5/assignments/101",
         "plannable": {"title": "作业一", "due_at": "2026-09-12T15:59:00Z"},
         "submissions": {"submitted": False}},
        {"plannable_id": 102, "plannable_type": "quiz",
         "plannable_date": "2026-09-13T15:59:00Z", "course_id": 5,
         "context_name": "DSC1001", "html_url": "/courses/5/quizzes/102",
         "plannable": {"title": "小测一"}, "submissions": {"submitted": True}},
        {"plannable_id": 103, "plannable_type": "discussion_topic",
         "plannable_date": "2026-09-14T15:59:00Z", "course_id": 5,
         "context_name": "DSC1001", "html_url": "/courses/5/discussion_topics/103",
         "plannable": {"title": "讨论一"}, "submissions": False},
        {"plannable_id": 104, "plannable_type": "calendar_event",
         "plannable_date": "2026-09-15T02:00:00Z", "course_id": 5,
         "context_name": "DSC1001", "html_url": "/calendar_events/104",
         "plannable": {"title": "讲座"}, "submissions": False},
        {"plannable_id": 105, "plannable_type": "wiki_page",
         "plannable_date": "2026-09-16T00:00:00Z", "course_id": 5,
         "context_name": "DSC1001", "html_url": "/courses/5/pages/105",
         "plannable": {"title": "阅读材料"}, "submissions": False},
        {"plannable_id": 106, "plannable_type": "planner_note",
         "plannable_date": "2026-09-17T00:00:00Z", "course_id": None,
         "context_name": None, "html_url": "/planner/notes/106",
         "plannable": {"title": "自己记的"}, "submissions": False},
        {"plannable_id": 107, "plannable_type": "unknown_future_type",
         "plannable_date": "2026-09-18T00:00:00Z", "course_id": 5,
         "context_name": "DSC1001", "html_url": "/x/107",
         "plannable": {"title": "未知类型"}, "submissions": False},
        {"plannable_id": 108, "plannable_type": "announcement",
         "plannable_date": "2026-09-10T09:00:00Z", "course_id": 5,
         "context_name": "DSC1001", "html_url": "/courses/5/announcements/108",
         "plannable": {"title": "一条公告"}, "submissions": False},
    ]


def test_get_planner_keeps_every_known_type(monkeypatch):
    s = _Session([(_planner_payload(), "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_planner_items("https://x", "tok", "2026-09-10", "2026-09-17")
    assert [i["id"] for i in out] == [101, 102, 103, 104, 105, 106, 107]
    types = {i["id"]: i["type"] for i in out}
    assert types[107] == "unknown_future_type"      # 未知类型走兜底，不丢弃
    assert out[0]["course_name"] == "DSC1001 Introduction to Data Science"
    assert s.calls[0][1] == {"start_date": "2026-09-10", "end_date": "2026-09-17",
                             "per_page": 100}


def test_get_planner_drops_announcement(monkeypatch):
    """实测 88 条里 20 条是 announcement，其 plannable_date 是发布时间，是纯噪音。"""
    s = _Session([(_planner_payload(), "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_planner_items("https://x", "tok", "2026-09-10", "2026-09-17")
    assert 108 not in [i["id"] for i in out]
    assert all(i["type"] != "announcement" for i in out)


def test_get_planner_submitted_is_tri_state(monkeypatch):
    """submissions 键恒在，值是 false 或对象 → 必须 isinstance 判定。
    写成 `if "submissions" in item` 会把每一项都当成「有提交状态」。"""
    s = _Session([(_planner_payload(), "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = {i["id"]: i["submitted"] for i in
           canvas_client.get_planner_items("https://x", "tok", "2026-09-10", "2026-09-17")}
    assert out[101] is False      # 对象且 submitted=False
    assert out[102] is True       # 对象且 submitted=True
    assert out[103] is None       # submissions 是布尔 false → 不适用
    assert out[104] is None


def test_get_planner_absolutizes_html_url(monkeypatch):
    """实测 html_url 是相对路径 /courses/… → 必须补成绝对，否则 openExternal 打不开。"""
    s = _Session([(_planner_payload(), "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_planner_items("https://canvas.cityu.edu.hk/", "tok",
                                          "2026-09-10", "2026-09-17")
    assert out[0]["html_url"] == "https://canvas.cityu.edu.hk/courses/5/assignments/101"
    assert all(i["html_url"].startswith("https://") for i in out)


def test_get_planner_date_fallback_chain(monkeypatch):
    """plannable_date 缺失时按类型回落；全缺则丢弃该项。"""
    s = _Session([([
        {"plannable_id": 1, "plannable_type": "assignment", "course_id": 5,
         "plannable": {"title": "退 due_at", "due_at": "2026-09-19T00:00:00Z"},
         "html_url": "/a/1", "submissions": {"submitted": False}},
        {"plannable_id": 2, "plannable_type": "discussion_topic", "course_id": 5,
         "plannable": {"title": "退 todo_date", "todo_date": "2026-09-20T00:00:00Z"},
         "html_url": "/a/2", "submissions": False},
        {"plannable_id": 3, "plannable_type": "calendar_event", "course_id": 5,
         "plannable": {"title": "退 start_at", "start_at": "2026-09-21T00:00:00Z"},
         "html_url": "/a/3", "submissions": False},
        {"plannable_id": 4, "plannable_type": "assignment", "course_id": 5,
         "plannable": {"title": "全缺"}, "html_url": "/a/4",
         "submissions": {"submitted": False}},
    ], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_planner_items("https://x", "tok", "2026-09-10", "2026-09-30")
    assert [(i["id"], i["date"]) for i in out] == [
        (1, "2026-09-19T00:00:00Z"),
        (2, "2026-09-20T00:00:00Z"),
        (3, "2026-09-21T00:00:00Z"),
    ]      # id=4 无任何日期 → 丢弃


def test_get_planner_course_name_missing_is_empty(monkeypatch):
    s = _Session([(_planner_payload(), "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_planner_items("https://x", "tok", "2026-09-10", "2026-09-17")
    note = [i for i in out if i["id"] == 106][0]
    assert note["course_name"] == ""
    assert note["course_id"] is None
