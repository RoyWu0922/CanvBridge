# Canvas 课程助手 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建本地后端 + 网页前端，登录 Canvas 读取公告并总结、写 Apple 日历/提醒、下载课程 Files 到本地。

**Architecture:** Python 3 + FastAPI 本地后端（`127.0.0.1`），分 `canvas_client`（Canvas REST）、`llm_client`（OpenAI 兼容）、`apple_script`（osascript 写日历/提醒）、`files_downloader`（下载分类）四个模块；前端为单页 `index.html`（内联 CSS/JS，中文界面）。配置存浏览器 localStorage，随请求体传给后端，不落盘。

**Tech Stack:** Python 3.9+、FastAPI、uvicorn、requests、pytest、AppleScript(osascript)。

**Spec:** `docs/superpowers/specs/2026-08-29-canvas-calendar-design.md`

## Global Constraints

- 平台：仅 macOS（依赖 `osascript`）。
- 语言：界面中文；`calendar_events`/`reminders` 的 title/notes 英文；`summary_cn` 中文。
- 分流：有具体时间段+地点 → 日历事件；只有截止 DDL → 提醒事项。
- 文件分类：`下载目录/科目名/<Canvas 原文件夹路径>/<文件名>`（保留结构）。
- Canvas 认证：Bearer API Token，端点 `{canvas_url}/api/v1/...`。
- LLM：OpenAI 兼容 `POST {base_url}/chat/completions`，`Authorization: Bearer <key>`。
- 安全：服务仅监听 127.0.0.1；token/key 只存 localStorage 与内存，不写磁盘。
- 依赖：`fastapi`、`uvicorn[standard]`、`requests`、`httpx`（TestClient 用）、`pytest`。
- 测试命令：在项目根目录 `python -m pytest`；需 Python 3.9+。

---

### Task 1: 脚手架 + `canvas_client.list_courses` + 分页

**Files:**
- Create: `requirements.txt`
- Create: `backend/__init__.py`
- Create: `backend/canvas_client.py`（仅 `list_courses`、`_paginate`、`_headers`、`_next_link`、`CanvasError`）
- Create: `tests/__init__.py`
- Create: `tests/test_canvas_client.py`（仅分页 + list_courses 部分）

**Interfaces:**
- Consumes: 无（首任务）。
- Produces:
  - `canvas_client.CanvasError(RuntimeError)`
  - `canvas_client.list_courses(canvas_url: str, token: str) -> list[dict]`，返回 `[{"id": int, "name": str}]`
  - `canvas_client._paginate(session, url, params, token) -> list[dict]`
  - `canvas_client._next_link(link_header: str) -> str | None`
  - `canvas_client._headers(token: str) -> dict`

- [ ] **Step 1: 写失败测试**

`tests/test_canvas_client.py`:

```python
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
        status_code = 401

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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_canvas_client.py -v`
Expected: FAIL（`ModuleNotFoundError: backend.canvas_client`）

- [ ] **Step 3: 实现**

`requirements.txt`:

```
fastapi>=0.110
uvicorn[standard]>=0.29
requests>=2.31
httpx>=0.27
pytest>=8.0
```

`backend/__init__.py`:（空文件）

`backend/canvas_client.py`:

```python
"""Canvas REST API 客户端（本应用只用读操作）。"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests

USER_AGENT = "canvas-calendar-helper/1.0"


class CanvasError(RuntimeError):
    """Canvas 返回错误时抛给上层展示。"""


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}


def _next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        section = part.split(";")
        if len(section) < 2:
            continue
        url = section[0].strip().strip("<>")
        rel = ""
        for attr in section[1:]:
            if "rel=" in attr:
                rel = attr.split("=", 1)[1].strip().strip('"')
        if rel == "next":
            return url
    return None


def _paginate(session: requests.Session, url: str, params: dict[str, Any],
              token: str) -> list[dict]:
    """沿 Canvas 的 Link 头翻页，收集所有结果。"""
    results: list[dict] = []
    next_url: str | None = url
    current_params: dict[str, Any] = params
    while next_url:
        resp = session.get(next_url, params=current_params, headers=_headers(token))
        if resp.status_code == 401:
            raise CanvasError("Canvas token 无效或已过期 (HTTP 401)")
        if resp.status_code == 403:
            raise CanvasError("没有权限访问该资源 (HTTP 403)")
        resp.raise_for_status()
        results.extend(resp.json())
        next_url = _next_link(resp.headers.get("Link", ""))
        current_params = {}  # Link 头里的 next URL 已带全部 query 参数
    return results


def list_courses(canvas_url: str, token: str) -> list[dict]:
    """返回用户当前在修的课程 [{"id": int, "name": str}]。"""
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        data = _paginate(
            s, f"{base}/api/v1/courses",
            {"enrollment_state": "active", "per_page": 100}, token,
        )
    return [
        {"id": c["id"], "name": c.get("name", f"Course {c['id']}")}
        for c in data
    ]
```

`tests/__init__.py`:（空文件）

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_canvas_client.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add requirements.txt backend/__init__.py backend/canvas_client.py tests/__init__.py tests/test_canvas_client.py
git commit -m "feat: canvas client 基础（课程列表 + 分页）"
```

---

### Task 2: `canvas_client.strip_html` + `get_announcements`

**Files:**
- Modify: `backend/canvas_client.py`（追加 `strip_html`、`get_announcements`）
- Modify: `tests/test_canvas_client.py`（追加测试）

**Interfaces:**
- Consumes: `canvas_client._paginate`、`CanvasError`、`_headers`。
- Produces:
  - `canvas_client.strip_html(html: str) -> str`
  - `canvas_client.get_announcements(canvas_url: str, token: str, course_ids: list[int], start_date: str, end_date: str) -> dict[int, list[dict]]`，返回 `{course_id: [{"id", "title", "message", "posted_at"}]}`，`message` 为纯文本。

- [ ] **Step 1: 写失败测试**

`tests/test_canvas_client.py` 追加:

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_canvas_client.py -v`
Expected: FAIL（`AttributeError: module ... has no attribute 'strip_html'`）

- [ ] **Step 3: 实现**

`backend/canvas_client.py` 追加:

```python
class _TextExtractor(HTMLParser):
    """抽取 HTML 文本，块级标签处换行。"""

    _BLOCK = {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html or "")
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def get_announcements(canvas_url: str, token: str, course_ids: list[int],
                      start_date: str, end_date: str) -> dict[int, list[dict]]:
    """按课程分组返回公告；无公告的课程不在结果中出现。

    message 已剥离 HTML。日期格式 YYYY-MM-DD。
    """
    base = canvas_url.rstrip("/")
    params = {
        "context_codes[]": [f"course_{cid}" for cid in course_ids],
        "start_date": start_date,
        "end_date": end_date,
        "per_page": 100,
    }
    with requests.Session() as s:
        data = _paginate(s, f"{base}/api/v1/announcements", params, token)
    grouped: dict[int, list[dict]] = {}
    for item in data:
        match = re.fullmatch(r"course_(\d+)", item.get("context_code", ""))
        if not match:
            continue
        cid = int(match.group(1))
        grouped.setdefault(cid, []).append({
            "id": item.get("id"),
            "title": item.get("title", "(untitled)"),
            "message": strip_html(item.get("message", "")),
            "posted_at": item.get("posted_at", ""),
        })
    return grouped
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_canvas_client.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/canvas_client.py tests/test_canvas_client.py
git commit -m "feat: canvas 公告拉取 + HTML 清洗"
```

---

### Task 3: `canvas_client` 文件相关（`get_course_files`/`get_file`/`download_file`）

**Files:**
- Modify: `backend/canvas_client.py`
- Modify: `tests/test_canvas_client.py`

**Interfaces:**
- Consumes: `_paginate`、`_headers`、`CanvasError`。
- Produces:
  - `canvas_client.get_course_files(canvas_url, token, course_id: int) -> tuple[list[dict], list[dict]]`；files 元素 `{"id","display_name","folder_id","content_type","size","url"}`；folders 元素 `{"id","name","parent_folder_id"}`。
  - `canvas_client.get_file(canvas_url, token, course_id: int, file_id: int) -> dict`（返回完整 File 对象，含 `url`）。
  - `canvas_client.download_file(canvas_url, token, file_url: str, dest_path: str) -> None`（写入 `dest_path`，自动建父目录）。

- [ ] **Step 1: 写失败测试**

`tests/test_canvas_client.py` 追加:

```python
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
```

`tests/test_canvas_client.py` 顶部需 `import requests`。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_canvas_client.py -v`
Expected: FAIL（`AttributeError: ... no attribute 'get_course_files'`）

- [ ] **Step 3: 实现**

`backend/canvas_client.py` 追加:

```python
def get_course_files(canvas_url: str, token: str, course_id: int) -> tuple[list[dict], list[dict]]:
    """返回 (files, folders) 供下载规划使用。"""
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        files_data = _paginate(
            s, f"{base}/api/v1/courses/{course_id}/files",
            {"per_page": 100}, token,
        )
        folders_data = _paginate(
            s, f"{base}/api/v1/courses/{course_id}/folders",
            {"per_page": 100}, token,
        )
    files = [{
        "id": f["id"],
        "display_name": f.get("display_name", f.get("filename", "file")),
        "folder_id": f.get("folder_id"),
        "content_type": f.get("content-type", ""),
        "size": f.get("size", 0),
        "url": f.get("url", ""),
    } for f in files_data]
    folders = [{
        "id": fo["id"],
        "name": fo.get("name", ""),
        "parent_folder_id": fo.get("parent_folder_id"),
    } for fo in folders_data]
    return files, folders


def get_file(canvas_url: str, token: str, course_id: int, file_id: int) -> dict:
    base = canvas_url.rstrip("/")
    resp = requests.get(
        f"{base}/api/v1/courses/{course_id}/files/{file_id}",
        headers=_headers(token), timeout=30,
    )
    if resp.status_code == 401:
        raise CanvasError("Canvas token 无效或已过期 (HTTP 401)")
    resp.raise_for_status()
    return resp.json()


def download_file(canvas_url: str, token: str, file_url: str, dest_path: str) -> None:
    """流式下载 file_url 到 dest_path（自动建父目录）。"""
    dest = Path(dest_path)
    resp = requests.get(file_url, headers=_headers(token), stream=True, timeout=60)
    if resp.status_code == 401:
        raise CanvasError("Canvas token 无效或已过期 (HTTP 401)")
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                fh.write(chunk)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_canvas_client.py -v`
Expected: PASS（9 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/canvas_client.py tests/test_canvas_client.py
git commit -m "feat: canvas 课程文件/文件夹/下载"
```

---

### Task 4: `llm_client.extract_course_summary`

**Files:**
- Create: `backend/llm_client.py`
- Create: `tests/test_llm_client.py`

**Interfaces:**
- Consumes: 无（requests）。
- Produces:
  - `llm_client.extract_course_summary(base_url: str, api_key: str, model: str, course_name: str, announcements: list[dict]) -> dict`，返回 `{"course_name", "summary_cn", "calendar_events", "reminders"}`，解析失败时附加 `"warning": str` 并回退原文。
  - `llm_client._parse_json(text: str) -> dict`
  - `llm_client._build_prompt(course_name: str, announcements: list[dict]) -> str`

- [ ] **Step 1: 写失败测试**

`tests/test_llm_client.py`:

```python
import json

from backend import llm_client


def test_parse_json_handles_code_fence():
    raw = "```json\n{\"a\": 1}\n```"
    assert llm_client._parse_json(raw) == {"a": 1}


def test_extract_success(monkeypatch):
    payload = json.dumps({
        "course_name": "CS 101",
        "summary_cn": "本周要点。",
        "calendar_events": [{"title": "Quiz", "start": "2026-08-31T14:00:00",
                             "end": "2026-08-31T15:00:00", "location": "A101", "notes": ""}],
        "reminders": [{"title": "HW3", "due_date": "2026-09-02T23:59:00", "notes": ""}],
    })
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: payload)
    result = llm_client.extract_course_summary("https://llm/v1", "key", "m", "CS 101", [{"title": "x", "message": "y", "posted_at": ""}])
    assert result["summary_cn"] == "本周要点。"
    assert result["calendar_events"][0]["location"] == "A101"
    assert result["reminders"][0]["due_date"] == "2026-09-02T23:59:00"


def test_extract_fallback_on_persistent_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr(llm_client, "_call_chat", boom)
    result = llm_client.extract_course_summary("https://llm/v1", "key", "m", "CS 101", [{"title": "T", "message": "M", "posted_at": ""}])
    assert result["warning"] == "总结失败，已展示公告原文"
    assert result["calendar_events"] == []
    assert "T" in result["summary_cn"]


def test_build_prompt_includes_announcements():
    prompt = llm_client._build_prompt("CS 101", [{"title": "T", "message": "M", "posted_at": "2026-08-29"}])
    assert "CS 101" in prompt and "T" in prompt and "M" in prompt
    assert "calendar_events" in prompt
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_llm_client.py -v`
Expected: FAIL（`ModuleNotFoundError: backend.llm_client`）

- [ ] **Step 3: 实现**

`backend/llm_client.py`:

```python
"""OpenAI 兼容 chat completions 客户端，用于公告总结与日程提取。"""
from __future__ import annotations

import json
import re
from typing import Any

import requests

_SYSTEM = (
    "You extract structured schedule information from course announcements. "
    "Respond only with the requested JSON object, never with markdown."
)

_SCHEMA_INSTRUCTIONS = (
    'Produce a JSON object with EXACTLY this structure:\n'
    '{\n'
    '  "course_name": "<course_name>",\n'
    '  "summary_cn": "<Chinese summary>",\n'
    '  "calendar_events": [{"title": "...", "start": "YYYY-MM-DDTHH:MM:SS", '
    '"end": "YYYY-MM-DDTHH:MM:SS", "location": "...", "notes": "..."}],\n'
    '  "reminders": [{"title": "...", "due_date": "YYYY-MM-DDTHH:MM:SS", "notes": "..."}]\n'
    '}\n'
    'Rules:\n'
    '- summary_cn: write in Chinese, covering the key points in a few sentences.\n'
    '- calendar_events: ONLY items with a concrete date/time (a class, review session, '
    'exam, office hours). If only a date is given, use 23:59:00 as the end time. '
    'Location in English if mentioned, else "".\n'
    '- reminders: ONLY deadlines/due dates without a start/end period. due_date is the '
    'deadline; default to 23:59:00 if only a date is given.\n'
    '- Titles and notes in English, verbatim from the announcements where possible.\n'
    '- Return [] for calendar_events or reminders if there are none. Never invent events.\n'
    'Return ONLY the JSON object.'
)


def _build_prompt(course_name: str, announcements: list[dict]) -> str:
    lines = [
        f'You are an academic assistant. Below are announcements from the Canvas course named "{course_name}".',
        "",
    ]
    for i, a in enumerate(announcements, 1):
        lines.append(f"[{i}] {a.get('posted_at', '')} — {a.get('title', '')}")
        lines.append(a.get("message", ""))
        lines.append("")
    lines.append(_SCHEMA_INSTRUCTIONS)
    return "\n".join(lines)


def _call_chat(base_url: str, api_key: str, model: str, prompt: str) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(
        url, json=payload,
        headers={"Authorization": f"Bearer {api_key}"}, timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def extract_course_summary(base_url: str, api_key: str, model: str,
                           course_name: str, announcements: list[dict]) -> dict:
    """返回 {course_name, summary_cn, calendar_events, reminders}。

    输出无法解析时（重试一次后）回退到公告原文，并附 warning 标记。
    """
    fallback = {
        "course_name": course_name,
        "summary_cn": "\n".join(
            f"- {a.get('title', '')}: {a.get('message', '')[:300]}" for a in announcements
        ) or "(无公告)",
        "calendar_events": [],
        "reminders": [],
        "warning": "总结失败，已展示公告原文",
    }
    prompt = _build_prompt(course_name, announcements)
    for _attempt in range(2):
        try:
            content = _call_chat(base_url, api_key, model, prompt)
            parsed = _parse_json(content)
            parsed.setdefault("course_name", course_name)
            parsed.setdefault("summary_cn", "")
            parsed.setdefault("calendar_events", [])
            parsed.setdefault("reminders", [])
            return parsed
        except (requests.RequestException, ValueError, KeyError, TypeError):
            continue
    return fallback
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_llm_client.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/llm_client.py tests/test_llm_client.py
git commit -m "feat: LLM 公告总结与结构化提取"
```

---

### Task 5: `apple_script`（日历/提醒）

**Files:**
- Create: `backend/apple_script.py`
- Create: `tests/test_apple_script.py`

**Interfaces:**
- Consumes: 无（subprocess 调 `osascript`）。
- Produces:
  - `apple_script.list_calendars() -> list[str]`
  - `apple_script.list_reminder_lists() -> list[str]`
  - `apple_script.add_calendar_event(calendar_name, title, start_iso, end_iso, location, notes) -> None`
  - `apple_script.add_reminder(list_name, title, due_iso, notes) -> None`
  - `apple_script._quote(s: str) -> str`
  - `apple_script._build_date_script(iso: str, var: str) -> str`
  - `apple_script._run(script: str) -> str`（子进程封装）
  - `apple_script.AppleScriptError(RuntimeError)`
- 时间格式：ISO 8601 字符串，如 `2026-08-31T14:00:00`（`datetime.fromisoformat` 可解析）。

- [ ] **Step 1: 写失败测试**

`tests/test_apple_script.py`:

```python
import subprocess

import pytest

from backend import apple_script


def test_quote_escapes():
    assert apple_script._quote('say "hi"') == 'say \\"hi\\"'
    assert apple_script._quote("a\nb") == "a b"


def test_build_date_script_sets_components():
    script = apple_script._build_date_script("2026-08-31T14:00:00", "d")
    assert "set year of d to 2026" in script
    assert "set month of d to August" in script
    assert "set day of d to 31" in script
    assert "set hours of d to 14" in script
    assert "set minutes of d to 0" in script


def test_run_raises_on_nonzero(monkeypatch):
    class _P:
        returncode = 1
        stdout = ""
        stderr = "not allowed"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())
    with pytest.raises(apple_script.AppleScriptError):
        apple_script._run("tell application \"Calendar\"")


def test_list_calendars_splits(monkeypatch):
    class _P:
        returncode = 0
        stdout = "Study, 家庭, Work"
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())
    assert apple_script.list_calendars() == ["Study", " 家庭", " Work"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_apple_script.py -v`
Expected: FAIL（`ModuleNotFoundError: backend.apple_script`）

- [ ] **Step 3: 实现**

`backend/apple_script.py`:

```python
"""通过 AppleScript 操作 macOS 日历与提醒事项。"""
from __future__ import annotations

import subprocess
from datetime import datetime

_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


class AppleScriptError(RuntimeError):
    """osascript 失败时抛出（多为系统权限未授权）。"""


def _run(script: str) -> str:
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AppleScriptError(proc.stderr.strip() or f"osascript failed ({proc.returncode})")
    return proc.stdout.strip()


def _quote(s: str) -> str:
    """把字符串安全嵌入 AppleScript 双引号字面量。"""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _build_date_script(iso: str, var: str) -> str:
    """生成把 var 设为指定时刻的 AppleScript（按组件赋值，避免本地化问题）。"""
    dt = datetime.fromisoformat(iso)
    return (
        f"set {var} to current date\n"
        f"set year of {var} to {dt.year}\n"
        f"set month of {var} to {_MONTHS[dt.month - 1]}\n"
        f"set day of {var} to {dt.day}\n"
        f"set hours of {var} to {dt.hour}\n"
        f"set minutes of {var} to {dt.minute}\n"
        f"set seconds of {var} to {dt.second}"
    )


def list_calendars() -> list[str]:
    out = _run('tell application "Calendar" to get name of every calendar')
    return out.split(",")


def list_reminder_lists() -> list[str]:
    out = _run('tell application "Reminders" to get name of every list')
    return out.split(",")


def add_calendar_event(calendar_name: str, title: str, start_iso: str, end_iso: str,
                       location: str, notes: str) -> None:
    props = '{summary:"' + _quote(title) + '", start date:startDate, end date:endDate'
    if location:
        props += ', location:"' + _quote(location) + '"'
    if notes:
        props += ', description:"' + _quote(notes) + '"'
    props += "}"
    script = (
        'tell application "Calendar"\n'
        + _build_date_script(start_iso, "startDate") + "\n"
        + _build_date_script(end_iso, "endDate") + "\n"
        'tell calendar "' + _quote(calendar_name) + '"\n'
        "make new event with properties " + props + "\n"
        "end tell\n"
        "end tell"
    )
    _run(script)


def add_reminder(list_name: str, title: str, due_iso: str, notes: str) -> None:
    props = '{name:"' + _quote(title) + '", due date:dueDate'
    if notes:
        props += ', body:"' + _quote(notes) + '"'
    props += "}"
    script = (
        'tell application "Reminders"\n'
        + _build_date_script(due_iso, "dueDate") + "\n"
        'tell list "' + _quote(list_name) + '"\n'
        "make new reminder with properties " + props + "\n"
        "end tell\n"
        "end tell"
    )
    _run(script)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_apple_script.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/apple_script.py tests/test_apple_script.py
git commit -m "feat: AppleScript 日历/提醒写入"
```

---

### Task 6: `files_downloader`

**Files:**
- Create: `backend/files_downloader.py`
- Create: `tests/test_files_downloader.py`

**Interfaces:**
- Consumes: `canvas_client.download_file`。
- Produces:
  - `files_downloader._safe_name(name: str) -> str`
  - `files_downloader.build_folder_path(folder_id, folders: list[dict]) -> str`
  - `files_downloader.plan_downloads(download_dir: str, course_name: str, files: list[dict], folders: list[dict]) -> list[dict]`，返回 `[{"file_id", "display_name", "dest_path"}]`，dest_path 唯一（同名加 `_2` 后缀）。
  - `files_downloader.download_items(canvas_url: str, token: str, files_by_id: dict[int, dict], planned: list[dict]) -> dict`，返回 `{"ok", "downloaded": [str], "failed": [{"file_id", "error"}]}`。

- [ ] **Step 1: 写失败测试**

`tests/test_files_downloader.py`:

```python
from backend import canvas_client, files_downloader


def test_safe_name_strips_path():
    assert files_downloader._safe_name("../a/b.pdf") == "a_b.pdf"
    assert files_downloader._safe_name(".hidden") == "hidden"
    assert files_downloader._safe_name("") == "_"


def test_build_folder_path():
    folders = [
        {"id": 1, "name": "Slides", "parent_folder_id": None},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1},
    ]
    assert files_downloader.build_folder_path(2, folders) == "Slides/Week 3"
    assert files_downloader.build_folder_path(1, folders) == "Slides"
    assert files_downloader.build_folder_path(999, folders) == ""


def test_plan_downloads_path_and_rename(tmp_path):
    files = [
        {"id": 1, "display_name": "a.pdf", "folder_id": 2},
        {"id": 2, "display_name": "a.pdf", "folder_id": 2},
        {"id": 3, "display_name": "b.pdf", "folder_id": None},
    ]
    folders = [
        {"id": 1, "name": "Slides", "parent_folder_id": None},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1},
    ]
    planned = files_downloader.plan_downloads(str(tmp_path), "CS 101", files, folders)
    assert len(planned) == 3
    assert planned[0]["dest_path"] == str(tmp_path / "CS 101" / "Slides" / "Week 3" / "a.pdf")
    assert planned[1]["dest_path"] == str(tmp_path / "CS 101" / "Slides" / "Week 3" / "a_2.pdf")
    assert planned[2]["dest_path"] == str(tmp_path / "CS 101" / "b.pdf")


def test_download_items_reports_failure(monkeypatch):
    def boom(canvas_url, token, url, dest):
        raise RuntimeError("network")
    monkeypatch.setattr(canvas_client, "download_file", boom)
    files_by_id = {1: {"url": "http://x/f/1", "display_name": "a.pdf"}}
    planned = [{"file_id": 1, "dest_path": "/tmp/a.pdf"}, {"file_id": 99, "dest_path": "/tmp/miss.pdf"}]
    result = files_downloader.download_items("https://x", "tok", files_by_id, planned)
    assert result["ok"] is False
    assert result["downloaded"] == []
    assert len(result["failed"]) == 2


def test_download_items_success(monkeypatch, tmp_path):
    calls = []
    def ok(canvas_url, token, url, dest):
        calls.append(dest)
    monkeypatch.setattr(canvas_client, "download_file", ok)
    files_by_id = {1: {"url": "http://x/f/1"}}
    planned = [{"file_id": 1, "dest_path": str(tmp_path / "a.pdf")}]
    result = files_downloader.download_items("https://x", "tok", files_by_id, planned)
    assert result["ok"] is True
    assert result["downloaded"] == [str(tmp_path / "a.pdf")]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_files_downloader.py -v`
Expected: FAIL（`ModuleNotFoundError: backend.files_downloader`）

- [ ] **Step 3: 实现**

`backend/files_downloader.py`:

```python
"""文件下载规划与执行（保留 Canvas 文件夹结构）。"""
from __future__ import annotations

from pathlib import Path

from . import canvas_client


def _safe_name(name: str) -> str:
    name = name.replace("/", "_").replace("\\", "_").lstrip(".")
    return name or "_"


def build_folder_path(folder_id, folders: list[dict]) -> str:
    """返回 folder_id 的斜杠路径；课程根目录返回 ''。"""
    by_id = {f["id"]: f for f in folders}
    parts: list[str] = []
    cur = by_id.get(folder_id)
    while cur is not None:
        parts.append(_safe_name(cur.get("name", "")))
        cur = by_id.get(cur.get("parent_folder_id"))
    return "/".join(reversed(parts))


def plan_downloads(download_dir: str, course_name: str, files: list[dict],
                   folders: list[dict]) -> list[dict]:
    """规划 dest_path = 下载目录/科目/原文件夹路径/文件名；同名加 _N 后缀。"""
    root = Path(download_dir).expanduser() / _safe_name(course_name)
    planned: list[dict] = []
    used: set[str] = set()
    for f in files:
        folder_path = build_folder_path(f.get("folder_id"), folders)
        base = root / folder_path if folder_path else root
        display = _safe_name(f.get("display_name", "file"))
        dest = base / display
        counter = 2
        while str(dest) in used:
            dest = base / f"{dest.stem}_{counter}{dest.suffix}"
            counter += 1
        used.add(str(dest))
        planned.append({"file_id": f["id"], "display_name": display, "dest_path": str(dest)})
    return planned


def download_items(canvas_url: str, token: str, files_by_id: dict[int, dict],
                   planned: list[dict]) -> dict:
    """逐文件下载；单个失败不中断其余。"""
    downloaded: list[str] = []
    failed: list[dict] = []
    for item in planned:
        info = files_by_id.get(item["file_id"])
        if not info or not info.get("url"):
            failed.append({"file_id": item["file_id"], "error": "缺少下载地址"})
            continue
        try:
            canvas_client.download_file(canvas_url, token, info["url"], item["dest_path"])
            downloaded.append(item["dest_path"])
        except Exception as exc:  # 单文件失败不影响整体
            failed.append({"file_id": item["file_id"], "error": str(exc)})
    return {"ok": len(failed) == 0, "downloaded": downloaded, "failed": failed}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_files_downloader.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/files_downloader.py tests/test_files_downloader.py
git commit -m "feat: 文件下载规划与执行"
```

---

### Task 7: `main.py`（FastAPI 路由）

**Files:**
- Create: `backend/main.py`
- Create: `tests/test_main.py`

**Interfaces:**
- Consumes: `canvas_client`（`list_courses`/`get_announcements`/`get_course_files`/`get_file`）、`llm_client.extract_course_summary`、`apple_script`（`list_calendars`/`list_reminder_lists`/`add_calendar_event`/`add_reminder`）、`files_downloader`（`plan_downloads`/`download_items`/`build_folder_path`）。
- Produces: FastAPI app `main.app`，静态服务 `frontend/index.html` 于 `/`。
- 路由（均 `POST /api/...`）见 §6 of spec；关键响应：`/api/test_connection`、`/api/courses` 返回 `{"ok", "courses":[...]}`；`/api/calendars` 返回 `{"ok", "calendars":[...]}`；`/api/reminder_lists` 返回 `{"ok", "lists":[...]}`；`/api/sync_announcements` 返回 `{"ok", "courses":[summary,...]}`；`/api/list_files` 返回 `{"ok", "courses":[{course_id, name, files:[{file_id, display_name, path, content_type, size, dest_path}]}]}`；`/api/download_files` 返回 `{"ok", "downloaded", "failed"}`；`/api/add_calendar_event`、`/api/add_reminder` 返回 `{"ok"}`。

- [ ] **Step 1: 写失败测试**

`tests/test_main.py`:

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_main.py -v`
Expected: FAIL（`ModuleNotFoundError: backend.main`）

- [ ] **Step 3: 实现**

`backend/main.py`:

```python
"""FastAPI 应用：Canvas 课程助手后端。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import apple_script, canvas_client, files_downloader, llm_client

app = FastAPI(title="Canvas 课程助手")

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "index.html"


class CanvasConfig(BaseModel):
    canvas_url: str
    canvas_token: str


class LLMConfig(BaseModel):
    llm_base_url: str
    llm_api_key: str
    llm_model: str


class SyncRequest(CanvasConfig, LLMConfig):
    course_ids: list[int]
    start_date: str
    end_date: str


class AddEventRequest(BaseModel):
    calendar_name: str
    title: str
    start: str
    end: str
    location: str = ""
    notes: str = ""


class AddReminderRequest(BaseModel):
    list_name: str
    title: str
    due_date: str
    notes: str = ""


class ListFilesRequest(CanvasConfig):
    course_ids: list[int]
    download_dir: str


class DownloadRequest(CanvasConfig):
    items: list[dict]  # [{course_id, file_id, dest_path}]


@app.get("/")
def index():
    return FileResponse(FRONTEND)


@app.post("/api/test_connection")
def test_connection(req: CanvasConfig):
    try:
        return {"ok": True, "courses": canvas_client.list_courses(req.canvas_url, req.canvas_token)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/courses")
def courses(req: CanvasConfig):
    try:
        return {"ok": True, "courses": canvas_client.list_courses(req.canvas_url, req.canvas_token)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/calendars")
def calendars():
    try:
        return {"ok": True, "calendars": apple_script.list_calendars()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/reminder_lists")
def reminder_lists():
    try:
        return {"ok": True, "lists": apple_script.list_reminder_lists()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/sync_announcements")
def sync(req: SyncRequest):
    try:
        announcements = canvas_client.get_announcements(
            req.canvas_url, req.canvas_token, req.course_ids,
            req.start_date, req.end_date)
        courses = canvas_client.list_courses(req.canvas_url, req.canvas_token)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    name_by_id = {c["id"]: c["name"] for c in courses}
    results = []
    for cid in req.course_ids:
        anns = announcements.get(cid, [])
        results.append(llm_client.extract_course_summary(
            req.llm_base_url, req.llm_api_key, req.llm_model,
            name_by_id.get(cid, f"Course {cid}"), anns))
    return {"ok": True, "courses": results}


@app.post("/api/add_calendar_event")
def add_event(req: AddEventRequest):
    try:
        apple_script.add_calendar_event(
            req.calendar_name, req.title, req.start, req.end,
            req.location, req.notes)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/add_reminder")
def add_reminder(req: AddReminderRequest):
    try:
        apple_script.add_reminder(req.list_name, req.title, req.due_date, req.notes)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/list_files")
def list_files(req: ListFilesRequest):
    try:
        courses = canvas_client.list_courses(req.canvas_url, req.canvas_token)
    except Exception:
        courses = []
    name_by_id = {c["id"]: c["name"] for c in courses}
    results = []
    for cid in req.course_ids:
        name = name_by_id.get(cid, f"Course {cid}")
        try:
            files, folders = canvas_client.get_course_files(req.canvas_url, req.canvas_token, cid)
            planned = files_downloader.plan_downloads(req.download_dir, name, files, folders)
            dest_by_id = {p["file_id"]: p["dest_path"] for p in planned}
            results.append({
                "course_id": cid, "name": name,
                "files": [{
                    "file_id": f["id"], "display_name": f["display_name"],
                    "path": files_downloader.build_folder_path(f.get("folder_id"), folders),
                    "content_type": f["content_type"], "size": f["size"],
                    "dest_path": dest_by_id.get(f["id"], ""),
                } for f in files],
            })
        except Exception as exc:
            results.append({"course_id": cid, "name": name, "files": [], "error": str(exc)})
    return {"ok": True, "courses": results}


@app.post("/api/download_files")
def download_files(req: DownloadRequest):
    files_by_id: dict[int, dict] = {}
    for item in req.items:
        try:
            info = canvas_client.get_file(req.canvas_url, req.canvas_token,
                                          item["course_id"], item["file_id"])
            files_by_id[item["file_id"]] = info
        except Exception:
            continue
    planned = [{"file_id": i["file_id"], "dest_path": i["dest_path"]} for i in req.items]
    return files_downloader.download_items(req.canvas_url, req.canvas_token, files_by_id, planned)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_main.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/main.py tests/test_main.py
git commit -m "feat: FastAPI 路由"
```

---

### Task 8: 前端 `frontend/index.html`

**Files:**
- Create: `frontend/index.html`

**Interfaces:**
- Consumes: 全部 `POST /api/...` 端点。
- Produces: 单页前端，服务于 `/`。

- [ ] **Step 1: 写文件（前端无单测，功能由 Task 9 手动验证）**

`frontend/index.html`（完整内容，内联 CSS/JS，中文界面）：

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Canvas 课程助手</title>
<style>
  :root { --bg:#f5f6fa; --card:#fff; --border:#e2e5ec; --text:#1f2430;
          --muted:#6b7280; --accent:#2563eb; --ok:#16a34a; --err:#dc2626; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         background:var(--bg); color:var(--text); }
  header { padding:16px 24px; background:var(--card); border-bottom:1px solid var(--border); font-size:20px; font-weight:600; }
  main { max-width:980px; margin:24px auto; padding:0 16px; display:flex; flex-direction:column; gap:20px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:18px; }
  .card h2 { margin:0 0 12px; font-size:16px; }
  details summary { cursor:pointer; font-size:16px; font-weight:600; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:10px; }
  label { display:flex; flex-direction:column; gap:4px; font-size:13px; color:var(--muted); }
  input,select,button { padding:8px 10px; border:1px solid var(--border); border-radius:8px; font-size:14px; background:#fff; }
  button { cursor:pointer; background:var(--accent); color:#fff; border:none; font-weight:500; }
  button.secondary { background:#e5e7eb; color:var(--text); }
  button:disabled { opacity:.5; cursor:not-allowed; }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .muted { color:var(--muted); font-size:13px; }
  .ok { color:var(--ok); } .err { color:var(--err); }
  .course-card { border:1px solid var(--border); border-radius:10px; padding:12px; margin-bottom:12px; }
  .summary { white-space:pre-wrap; font-size:14px; line-height:1.6; }
  .item { display:flex; gap:8px; align-items:flex-start; padding:6px 0; border-top:1px dashed var(--border); }
  .item input { margin-top:2px; }
  .file-path { color:var(--muted); font-size:12px; }
  #status { position:fixed; right:16px; bottom:16px; background:#1f2430; color:#fff;
            padding:10px 14px; border-radius:8px; display:none; max-width:420px; font-size:13px; }
</style>
</head>
<body>
<header>📚 Canvas 课程助手</header>
<main>
  <section class="card">
    <details open>
      <summary>⚙️ 设置</summary>
      <div class="grid" style="margin-top:12px">
        <label>Canvas 实例 URL<input id="canvasUrl" placeholder="https://xxx.instructure.com"></label>
        <label>Canvas API Token<input id="canvasToken" type="password" placeholder="…"></label>
        <label>LLM Base URL<input id="llmBaseUrl" placeholder="https://api.deepseek.com/v1"></label>
        <label>LLM API Key<input id="llmApiKey" type="password" placeholder="sk-…"></label>
        <label>LLM 模型名<input id="llmModel" placeholder="deepseek-chat"></label>
        <label>下载目录<input id="downloadDir" placeholder="~/Downloads/Canvas课程文件"></label>
      </div>
      <div class="row" style="margin-top:12px">
        <button id="btnTest">🔌 测试连接</button>
        <label>写入日历 <select id="selCalendar"></select></label>
        <label>写入提醒列表 <select id="selList"></select></label>
        <button id="btnLoadCalendars" class="secondary">刷新日历/列表</button>
      </div>
    </details>
  </section>

  <section class="card">
    <h2>🗓 同步公告并总结</h2>
    <div class="row">
      <label>时间段
        <select id="selRange">
          <option value="week">本周</option>
          <option value="month">本月</option>
          <option value="custom">自定义</option>
        </select>
      </label>
      <label id="lblStart" style="display:none">开始 <input id="inpStart" type="date"></label>
      <label id="lblEnd" style="display:none">结束 <input id="inpEnd" type="date"></label>
      <button id="btnLoadCourses" class="secondary">📂 加载课程</button>
      <button id="btnSync">✨ 同步并总结</button>
    </div>
    <div id="courseCheckboxes" class="muted" style="margin-top:10px"></div>
  </section>

  <section class="card" id="resultCard" style="display:none">
    <h2>📄 结果</h2>
    <div id="summaries"></div>
    <div class="row" style="margin-top:12px; border-top:1px solid var(--border); padding-top:12px">
      <button id="btnWriteCalendar">📅 写入选中的日历事件</button>
      <button id="btnWriteReminders">✅ 写入选中的提醒</button>
    </div>
  </section>

  <section class="card">
    <h2>📁 下载课程文件（Files）</h2>
    <div class="row">
      <button id="btnListFiles">🗂 加载文件列表</button>
      <label>类型过滤 <input id="inpTypeFilter" placeholder="pdf, pptx, docx…（留空=全部）"></label>
      <button id="btnSelectAllFiles" class="secondary">全选</button>
      <button id="btnDownloadFiles">⬇ 下载所选</button>
    </div>
    <div id="filesArea" class="muted" style="margin-top:10px"></div>
  </section>
</main>
<div id="status"></div>
<script>
const $ = (id) => document.getElementById(id);
const KEY = ["canvasUrl","canvasToken","llmBaseUrl","llmApiKey","llmModel","downloadDir"];
function loadSettings(){ KEY.forEach(k=>{ const v=localStorage.getItem("sc_"+k); if(v) $(k).value=v; }); }
function saveSettings(){ KEY.forEach(k=>localStorage.setItem("sc_"+k, $(k).value)); }
function settings(){
  saveSettings();
  return { canvas_url:$("canvasUrl").value.trim(), canvas_token:$("canvasToken").value.trim(),
           llm_base_url:$("llmBaseUrl").value.trim(), llm_api_key:$("llmApiKey").value.trim(),
           llm_model:$("llmModel").value.trim() };
}
function downloadDir(){ saveSettings(); return $("downloadDir").value.trim() || "~/Downloads/Canvas课程文件"; }
function setStatus(msg, ok){
  const s=$("status"); s.textContent=msg; s.style.display="block";
  s.style.background = ok===false ? "#dc2626" : "#1f2430";
  clearTimeout(s._t); s._t=setTimeout(()=>s.style.display="none", 4000);
}
async function api(path, body){
  const r = await fetch("/api/"+path, { method:"POST",
    headers:{"Content-Type":"application/json"}, body: JSON.stringify(body||{}) });
  return r.json();
}
function fillSelect(id, names){
  const sel=$(id); sel.innerHTML="";
  names.forEach(n=>{ const o=document.createElement("option"); o.value=o.textContent=n; sel.appendChild(o); });
}
function esc(t){ const d=document.createElement("div"); d.textContent = t==null?"":String(t); return d.innerHTML; }

$("btnTest").onclick = async () => {
  const s=settings();
  if(!s.canvas_url||!s.canvas_token){ setStatus("请填写 Canvas URL 和 Token", false); return; }
  const r=await api("test_connection", s);
  setStatus(r.ok ? `连接成功，共 ${r.courses.length} 门课程` : "连接失败："+(r.error||""), r.ok);
};
$("btnLoadCalendars").onclick = async () => {
  const [cal, list] = await Promise.all([api("calendars"), api("reminder_lists")]);
  fillSelect("selCalendar", cal.calendars||[]);
  fillSelect("selList", list.lists||[]);
  if(cal.ok===false) setStatus(cal.error, false); else setStatus("日历/提醒列表已刷新");
};

$("selRange").onchange = () => {
  const v=$("selRange").value;
  $("lblStart").style.display = $("lblEnd").style.display = v==="custom" ? "flex" : "none";
};
function range(){
  const now=new Date();
  const p=n=>String(n).padStart(2,"0");
  const fmt=d=>`${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;
  const v=$("selRange").value;
  if(v==="custom") return { start_date:$("inpStart").value, end_date:$("inpEnd").value };
  if(v==="week"){
    const day=(now.getDay()+6)%7;           // 周一起
    const start=new Date(now); start.setDate(now.getDate()-day);
    const end=new Date(start); end.setDate(start.getDate()+6);
    return { start_date:fmt(start), end_date:fmt(end) };
  }
  return { start_date:`${now.getFullYear()}-${p(now.getMonth()+1)}-01`, end_date:fmt(now) };
}

let courseList=[], summaryResults=[], fileCourses=[];
$("btnLoadCourses").onclick = async () => {
  const s=settings();
  if(!s.canvas_url||!s.canvas_token){ setStatus("请先填写 Canvas 配置", false); return; }
  const r=await api("courses", s);
  if(!r.ok){ setStatus("加载课程失败："+r.error, false); return; }
  courseList=r.courses;
  $("courseCheckboxes").innerHTML = courseList.map(c=>
    `<label style="flex-direction:row;align-items:center"><input type="checkbox" checked data-id="${c.id}"> ${esc(c.name)}</label>`).join("");
  setStatus(`已加载 ${courseList.length} 门课程`);
};
function selectedCourses(){ return [...document.querySelectorAll("#courseCheckboxes input:checked")].map(i=>Number(i.dataset.id)); }

$("btnSync").onclick = async () => {
  const s=settings(), ids=selectedCourses();
  if(!ids.length){ setStatus("请先勾选课程", false); return; }
  setStatus("同步并总结中，请稍候…");
  const r=await api("sync_announcements", { ...s, course_ids:ids, ...range() });
  if(!r.ok){ setStatus("同步失败："+r.error, false); return; }
  summaryResults=r.courses; renderSummaries(); setStatus("同步完成");
};
function renderSummaries(){
  $("resultCard").style.display="block";
  $("summaries").innerHTML = summaryResults.map((c,ci)=>`
    <div class="course-card">
      <div style="font-weight:600">${esc(c.course_name)}</div>
      ${c.warning?`<div class="err">${esc(c.warning)}</div>`:""}
      <div class="summary">${esc(c.summary_cn)}</div>
      <div style="margin-top:8px;font-size:13px;color:var(--muted)">📅 日历事件 (${(c.calendar_events||[]).length})</div>
      ${(c.calendar_events||[]).map((e,ei)=>`
        <div class="item"><input type="checkbox" class="ev" data-ci="${ci}" data-ei="${ei}">
          <div><div>${esc(e.title)}</div>
          <div class="file-path">${esc(e.start)} → ${esc(e.end)}${e.location?` · ${esc(e.location)}`:""}</div></div></div>`).join("")}
      <div style="margin-top:8px;font-size:13px;color:var(--muted)">✅ 提醒 (${(c.reminders||[]).length})</div>
      ${(c.reminders||[]).map((e,ei)=>`
        <div class="item"><input type="checkbox" class="rm" data-ci="${ci}" data-ei="${ei}">
          <div><div>${esc(e.title)}</div><div class="file-path">截止 ${esc(e.due_date)}</div></div></div>`).join("")}
    </div>`).join("");
}

$("btnWriteCalendar").onclick = async () => {
  const cal=$("selCalendar").value;
  if(!cal){ setStatus("请先刷新并选择日历", false); return; }
  const evs=[...document.querySelectorAll(".ev:checked")].map(i=>{
    const c=summaryResults[Number(i.dataset.ci)]; return c.calendar_events[Number(i.dataset.ei)]; });
  if(!evs.length){ setStatus("没有选中要写入的日历事件", false); return; }
  let n=0;
  for(const e of evs){
    const r=await api("add_calendar_event",{ calendar_name:cal, title:e.title, start:e.start,
      end:e.end, location:e.location||"", notes:e.notes||"" });
    if(r.ok) n++; else setStatus("写入失败："+r.error, false);
  }
  setStatus(`已写入 ${n}/${evs.length} 条日历事件`, n===evs.length);
};
$("btnWriteReminders").onclick = async () => {
  const list=$("selList").value;
  if(!list){ setStatus("请先刷新并选择提醒列表", false); return; }
  const rms=[...document.querySelectorAll(".rm:checked")].map(i=>{
    const c=summaryResults[Number(i.dataset.ci)]; return c.reminders[Number(i.dataset.ei)]; });
  if(!rms.length){ setStatus("没有选中要写入的提醒", false); return; }
  let n=0;
  for(const e of rms){
    const r=await api("add_reminder",{ list_name:list, title:e.title, due_date:e.due_date, notes:e.notes||"" });
    if(r.ok) n++; else setStatus("写入失败："+r.error, false);
  }
  setStatus(`已写入 ${n}/${rms.length} 条提醒`, n===rms.length);
};

$("btnListFiles").onclick = async () => {
  const s=settings(), ids=selectedCourses();
  if(!ids.length){ setStatus("请先勾选课程", false); return; }
  const r=await api("list_files",{ ...s, course_ids:ids, download_dir:downloadDir() });
  if(!r.ok){ setStatus("加载文件失败："+r.error, false); return; }
  fileCourses=r.courses; renderFiles(); setStatus("文件列表已加载");
};
function renderFiles(){
  const filter=$("inpTypeFilter").value.toLowerCase().trim().replace(/^\./,"");
  const shown=fileCourses.map(c=>({...c, files:(c.files||[]).filter(f=>{
    if(!filter) return true;
    return (f.content_type||"").toLowerCase().includes(filter)
        || (f.display_name||"").toLowerCase().endsWith("."+filter);
  })}));
  $("filesArea").innerHTML = shown.map((c,ci)=>`
    <div class="course-card">
      <div style="font-weight:600">${esc(c.name)} ${c.error?`<span class="err">(${esc(c.error)})</span>`:""}</div>
      ${(c.files||[]).map(f=>`
        <div class="item"><input type="checkbox" class="fl" data-ci="${ci}" data-fi="${f.file_id}">
          <div><div>${esc(f.display_name)} <span class="muted">(${esc(f.content_type)})</span></div>
          <div class="file-path">${esc(f.path||"/")}</div></div></div>`).join("")}
    </div>`).join("") || "<div class='muted'>没有匹配的文件</div>";
}
$("inpTypeFilter").oninput = renderFiles;
$("btnSelectAllFiles").onclick = ()=>document.querySelectorAll(".fl").forEach(i=>i.checked=true);
$("btnDownloadFiles").onclick = async () => {
  const s=settings();
  const items=[...document.querySelectorAll(".fl:checked")].map(i=>{
    const c=fileCourses[Number(i.dataset.ci)];
    const f=c.files.find(x=>x.file_id===Number(i.dataset.fi));
    return { course_id:c.course_id, file_id:f.file_id, dest_path:f.dest_path }; });
  if(!items.length){ setStatus("没有选中要下载的文件", false); return; }
  setStatus(`正在下载 ${items.length} 个文件…`);
  const r=await api("download_files",{ ...s, download_dir:downloadDir(), items });
  if(!r.ok){ setStatus("下载失败："+r.error, false); return; }
  const failed=(r.failed||[]).length;
  setStatus(`下载完成：成功 ${r.downloaded.length}，失败 ${failed}`, failed===0);
};

loadSettings();
fillSelect("selCalendar", []); fillSelect("selList", []);
</script>
</body>
</html>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/index.html
git commit -m "feat: 前端单页界面"
```

---

### Task 9: README + 端到端手动验证

**Files:**
- Create: `README.md`

- [ ] **Step 1: 写 README**

`README.md`:

```markdown
# Canvas 课程助手

从学校 Canvas 读取课程公告，用 LLM 总结，并把日程写入 Apple 日历、待办写入提醒事项，同时下载课程 Files 到本地。

## 功能

1. 连接 Canvas（API Token），按时间段拉取所选课程公告并生成中文总结
2. 从公告提取有具体时间的日程 → 写入 Apple 日历（可选日历）
3. 从公告提取截止 DDL → 写入 Apple 提醒事项（可选列表）
4. 下载课程 Files → 按「科目/原文件夹结构」分类存到本地
5. 总结/提取走 OpenAI 兼容 LLM（DeepSeek/Kimi/通义/智谱/Ollama）

## 快速开始

```bash
cd School_Calendar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>，在「设置」填写：

- Canvas 实例 URL（如 `https://xxx.instructure.com`）与 API Token（Canvas → Account → Settings → Approved Integrations → New Access Token）
- LLM Base URL / API Key / 模型名（OpenAI 兼容格式）

## 权限

首次写入日历/提醒时，macOS 会弹出授权。若失败，到「系统设置 → 隐私与安全性 → 日历 / 提醒事项」勾选你的终端或运行环境。

## 安全

- Token 与 API key 只保存在浏览器 localStorage 与后端内存，不写磁盘、不入 git
- 服务仅监听 127.0.0.1
- 注意：重复点击「写入」会创建重复的日历事件/提醒

## 测试

```bash
python -m pytest
```
```

- [ ] **Step 2: 运行全部测试**

Run: `python -m pytest -q`
Expected: 全部通过

- [ ] **Step 3: 启动服务做手动冒烟**

Run: `uvicorn backend.main:app --host 127.0.0.1 --port 8000`（后台），然后浏览器访问 `http://127.0.0.1:8000`：
- 页面显示「Canvas 课程助手」
- 设置里「刷新日历/列表」能列出本机日历
- （可选，需真实 token）走完整流程

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs: README 与使用说明"
```

---

## Self-Review 备注

- Spec §5（前端）、§6（API）、§7（LLM schema）、§8（错误处理）、§9（安全）均由对应任务覆盖。
- 范围外项（§11）未纳入任何任务，符合预期。
- 接口签名在各任务间保持一致（`list_courses`/`get_announcements`/`get_course_files`/`get_file`/`download_file`/`extract_course_summary`/`plan_downloads`/`download_items`/`add_calendar_event`/`add_reminder`）。
