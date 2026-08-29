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
    monkeypatch.setattr(
        canvas_client, "_paginate",
        lambda s, url, params, token: [{"id": 42, "name": "CS 101"}, {"id": 43}],
    )
    courses = canvas_client.list_courses("https://x.instructure.com", "tok")
    assert courses == [
        {"id": 42, "name": "CS 101"},
        {"id": 43, "name": "Course 43"},
    ]


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
