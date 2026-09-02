# 成绩 / 待办 / 日历事件 / 考试时间表 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 CanvBridge 加 4 个功能：成绩总评+作业明细、待办/即将截止、Canvas 日历事件导入写 Apple 日历、AIMS 考试时间表叠加课表。

**Architecture:** 复用既有 FastAPI 后端模式（`{ok, ...}` 返回、`_paginate` Canvas 客户端、`_on_browser_thread`/`_retry_once` AIMS 抓取、AppleScript 日历写入）。canvas_client 加 4 个读函数，main.py 加 6 个端点 + 1 个共享一次性事件写入循环，banweb 加考试解析与抓取，前端加 2 个标签页 + 课表考试块叠加。测试用 pytest（纯函数直测 + monkeypatch 测端点）与 `node --check`。

**Tech Stack:** Python / FastAPI / requests / Playwright / AppleScript；vanilla JS / i18n 双语文案。

**Spec:** [docs/superpowers/specs/2026-09-02-canvas-grades-todo-exams-design.md](../specs/2026-09-02-canvas-grades-todo-exams-design.md)

## Global Constraints

- 所有新端点返回 `{ok: true, ...}`；异常返回 `{ok: false, error: str}`。
- Canvas token 由前端在请求体传入，后端不落盘、不返回（沿用现状）。
- 前端渲染一律 `esc()` / `escAttr()`（见 `frontend/app.js` 顶部的两个 helper）。
- 每个新增用户可见文案都走 `t(key)`，zh + en 两个语言块成对补（`frontend/i18n.js`）。
- 日期格式 ISO `YYYY-MM-DDTHH:MM:SS`；纯时间 `HH:MM`。
- `find_events`/`add_calendar_event` 是 AppleScript 子进程调用——端点测试一律 monkeypatch。
- 纯解析函数用 HTML/JSON fixture 直测；浏览器/AppleScript 路径 monkeypatch 测端点。
- 注释沿用既有中文风格；既有代码不动（`list_courses` 默认行为不变）。
- 验证：`pytest` 全绿 + `node --check frontend/app.js frontend/i18n.js` 通过。

---

### Task 1: canvas_client 加成绩/待办/日历事件读取

**Files:**
- Modify: `backend/canvas_client.py`（`list_courses` 加 `include_scores`；文件尾加 `get_assignments_full`、`get_todo`、`get_calendar_events`）
- Test: `tests/test_canvas_client.py`

**Interfaces:**
- Consumes: 既有 `_headers`、`_paginate`、`CanvasError`、`re`、`datetime`/`timezone`（文件已 import `re`、`datetime`；若 `timezone` 不在 import 行则补上）。
- Produces（Task 2 消费）:
  - `list_courses(canvas_url, token, include_scores=False)` → 默认返回不变 `[{id,name,course_code}]`；`include_scores=True` 时每课多 `current_score`/`final_score`（数字或 None）。
  - `get_assignments_full(canvas_url, token, course_id)` → `[{id, name, due_at, points_possible, html_url, score, submitted}]`。
  - `get_todo(canvas_url, token)` → `[{id, type, title, course_id, course_name, due_at, html_url, points_possible, overdue}]`。
  - `get_calendar_events(canvas_url, token, course_ids, start_date, end_date)` → `[{id, title, course_id, start_at, end_at, location_name, html_url}]`。

- [ ] **Step 1: 写失败的测试**（追加到 `tests/test_canvas_client.py`）

```python
def test_list_courses_include_scores():
    s = _Session([([{
        "id": 1, "name": "CS101", "course_code": "CS101A",
        "enrollments": [{"grades": {"current_score": 88.5, "final_score": 85.0}}],
    }], "")])
    out = canvas_client.list_courses("https://x", "tok", include_scores=True)
    assert out[0]["current_score"] == 88.5
    assert out[0]["final_score"] == 85.0
    # 请求带 include[] 数组参数
    assert s.calls[0][1]["include[]"] == ["enrollments", "total_scores"]


def test_list_courses_include_scores_missing():
    s = _Session([([{"id": 1, "name": "CS101", "course_code": "CS101A"}], "")])
    out = canvas_client.list_courses("https://x", "tok", include_scores=True)
    assert out[0]["current_score"] is None
    assert out[0]["final_score"] is None


def test_list_courses_default_no_include_scores():
    s = _Session([([{"id": 1, "name": "CS101", "course_code": "CS101A"}], "")])
    out = canvas_client.list_courses("https://x", "tok")
    assert "include[]" not in s.calls[0][1]
    assert "current_score" not in out[0]


def test_get_assignments_full():
    s = _Session([([{
        "id": 9, "name": "HW1", "due_at": "2026-09-10T23:59:59Z",
        "points_possible": 10, "html_url": "https://x/c/1/a/9",
        "submission": {"score": 8.0, "submitted_at": "2026-09-09T10:00:00Z"},
    }], "")])
    out = canvas_client.get_assignments_full("https://x", "tok", 1)
    assert out[0]["score"] == 8.0
    assert out[0]["submitted"] is True


def test_get_assignments_full_no_submission():
    s = _Session([([{"id": 9, "name": "HW1", "due_at": ""}], "")])
    out = canvas_client.get_assignments_full("https://x", "tok", 1)
    assert out[0]["score"] is None
    assert out[0]["submitted"] is False


def test_get_todo_normalizes_and_sorts():
    s = _Session([([
        {"type": "Assignment",
         "assignment": {"id": 1, "name": "Late", "due_at": "2026-08-20T23:59:59Z",
                        "html_url": "https://x/c/1/a/1", "points_possible": 5, "course_id": 1},
         "context_name": "CS101"},
        {"type": "Quiz",
         "assignment": {"id": 2, "name": "Soon", "due_at": "2026-09-15T10:00:00Z",
                        "html_url": "", "points_possible": None, "course_id": 2},
         "context_name": "MA200"},
    ], "")])
    out = canvas_client.get_todo("https://x", "tok")
    assert out[0]["overdue"] is True
    assert out[0]["type"] == "Assignment"
    assert out[1]["overdue"] is False
    assert out[1]["course_name"] == "MA200"
    # 按 due_at 升序：Late 在前
    assert out[0]["title"] == "Late"


def test_get_todo_undated_last():
    s = _Session([([
        {"type": "Assignment",
         "assignment": {"id": 1, "name": "NoDue", "due_at": None, "course_id": 1},
         "context_name": "CS101"},
        {"type": "Assignment",
         "assignment": {"id": 2, "name": "Dated", "due_at": "2026-09-20T23:59:59Z", "course_id": 1},
         "context_name": "CS101"},
    ], "")])
    out = canvas_client.get_todo("https://x", "tok")
    assert out[0]["title"] == "Dated"
    assert out[1]["title"] == "NoDue"


def test_get_calendar_events_filters_assignment():
    s = _Session([([
        {"id": 1, "type": "event", "title": "Guest Talk", "context_code": "course_5",
         "start_at": "2026-09-10T14:00:00Z", "end_at": "2026-09-10T15:00:00Z",
         "location_name": "LT-1", "html_url": "https://x/c/5/e/1"},
        {"id": 2, "type": "assignment", "title": "HW1", "context_code": "course_5",
         "start_at": "2026-09-10T23:59:59Z"},
    ], "")])
    out = canvas_client.get_calendar_events("https://x", "tok", [5], "2026-09-01", "2026-09-30")
    assert len(out) == 1
    assert out[0]["title"] == "Guest Talk"
    assert out[0]["course_id"] == 5
    assert s.calls[0][1]["context_codes[]"] == ["course_5"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_canvas_client.py -q`
Expected: 新测试 FAIL（函数不存在 / 无 include_scores 参数）。

- [ ] **Step 3: 实现**

`backend/canvas_client.py` 的 `list_courses` 改成：

```python
def list_courses(canvas_url: str, token: str, include_scores: bool = False) -> list[dict]:
    """返回用户当前在修的课程 [{"id", "name", "course_code"}]。

    course_code 是 Canvas/SIS 里的课程代码（如 "CS1315A"），前端用它把
    Banweb 课表与 Canvas 课程按「字母简称 + 4 位数字」对齐（忽略 a/c 后缀）。
    enrollment_state 用 current_and_invited 而不是 active：新加入的课程在
    学生接受邀请前 enrollment 状态是 invited/invitation_pending，只查 active
    会把这类课程静默漏掉（Canvas 页面上能看到 7 门、这里只返回 6 门）。
    current_and_invited = 当前学期 active + invited 的选课，不含已结业课程。
    include_scores=True 时请求带 include[]=enrollments&include[]=total_scores，
    每课附加 current_score / final_score（数字或 None，课程未给分时为 None）。
    """
    base = canvas_url.rstrip("/")
    params = {"enrollment_state": "current_and_invited", "per_page": 100}
    if include_scores:
        params["include[]"] = ["enrollments", "total_scores"]
    with requests.Session() as s:
        data = _paginate(s, f"{base}/api/v1/courses", params, token)
    out = []
    for c in data:
        item = {
            "id": c["id"],
            "name": c.get("name", f"Course {c['id']}"),
            "course_code": c.get("course_code", "") or "",
        }
        if include_scores:
            item["current_score"] = _course_score(c, "current")
            item["final_score"] = _course_score(c, "final")
        out.append(item)
    return out
```

`list_courses` 之后加私有 helper：

```python
def _course_score(course: dict, kind: str):
    """从 enrollments[0].grades 或 total_scores 取当前/期末分数，缺失返回 None。

    include[]=enrollments 返回 enrollments[].grades.{current,final}_score；
    include[]=total_scores 返回 total_scores（list）的 computed_{current,final}_score。
    两者都缺（课程未给分）→ None。
    """
    grades = (course.get("enrollments") or [None])[0]
    if grades:
        g = grades.get("grades") or {}
        v = g.get("current_score" if kind == "current" else "final_score")
        if v is not None:
            return v
    totals = course.get("total_scores") or []
    if isinstance(totals, dict):
        totals = [totals]
    for t in totals:
        v = t.get("computed_current_score" if kind == "current" else "computed_final_score")
        if v is not None:
            return v
    return None
```

文件尾（`download_file` 之后，即文件末尾）追加三个函数：

```python
def get_assignments_full(canvas_url: str, token: str, course_id: int) -> list[dict]:
    """返回全部作业（含已截止）与提交分数 [{id, name, due_at, points_possible, html_url, score, submitted}]。

    include[]=submission 让每作业带 submission 对象；score 取 submission.score
    （无提交 None），submitted = 有 submitted_at。成绩明细用，不筛未来。
    """
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        data = _paginate(
            s, f"{base}/api/v1/courses/{course_id}/assignments",
            {"per_page": 100, "include[]": "submission"}, token,
        )
    out = []
    for a in data:
        sub = a.get("submission") or {}
        out.append({
            "id": a.get("id"),
            "name": a.get("name", "(untitled)"),
            "due_at": a.get("due_at") or "",
            "points_possible": a.get("points_possible"),
            "html_url": a.get("html_url")
                or f"{base}/courses/{course_id}/assignments/{a.get('id')}",
            "score": sub.get("score"),
            "submitted": bool(sub.get("submitted_at")),
        })
    return out


def get_todo(canvas_url: str, token: str) -> list[dict]:
    """返回归一化待办 [{id, type, title, course_id, course_name, due_at, html_url, points_possible, overdue}]。

    调 /api/v1/users/self/todo（含已过期项，无需课程参数）。due_at 解析失败按无截止
    处理；overdue = 有截止且早于现在。按 due_at 升序，无截止排最后。
    """
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        data = _paginate(s, f"{base}/api/v1/users/self/todo", {"per_page": 100}, token)
    now = datetime.now(timezone.utc)
    out = []
    for item in data:
        asg = item.get("assignment") or {}
        if not asg:
            continue
        due_raw = asg.get("due_at")
        due_dt = None
        if due_raw:
            try:
                due_dt = datetime.fromisoformat(due_raw.replace("Z", "+00:00"))
            except ValueError:
                due_dt = None
        out.append({
            "id": asg.get("id"),
            "type": item.get("type") or "Assignment",
            "title": asg.get("name", "(untitled)"),
            "course_id": asg.get("course_id"),
            "course_name": item.get("context_name") or "",
            "due_at": due_raw or "",
            "html_url": asg.get("html_url") or "",
            "points_possible": asg.get("points_possible"),
            "overdue": bool(due_dt and due_dt < now),
        })
    out.sort(key=lambda x: (x["due_at"] == "", x["due_at"]))
    return out


def get_calendar_events(canvas_url: str, token: str, course_ids: list[int],
                        start_date: str, end_date: str) -> list[dict]:
    """按课程拉日历事件，只保留 type=="event" 的一次性事件。

    返回 [{id, title, course_id, start_at, end_at, location_name, html_url}]。
    type=="assignment" 的日历事件（作业截止在日历上的展示）与待办重复，排除。
    """
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        data = _paginate(
            s, f"{base}/api/v1/calendar_events",
            {"context_codes[]": [f"course_{cid}" for cid in course_ids],
             "start_date": start_date, "end_date": end_date, "per_page": 100}, token,
        )
    out = []
    for ev in data:
        if (ev.get("type") or "") != "event":
            continue
        match = re.fullmatch(r"course_(\d+)", ev.get("context_code") or "")
        out.append({
            "id": ev.get("id"),
            "title": ev.get("title", "(untitled)"),
            "course_id": int(match.group(1)) if match else None,
            "start_at": ev.get("start_at") or "",
            "end_at": ev.get("end_at") or ev.get("start_at") or "",
            "location_name": ev.get("location_name") or "",
            "html_url": ev.get("html_url") or "",
        })
    out.sort(key=lambda x: x["start_at"])
    return out
```

文件头 import 区确认：`re`、`datetime` 已 import；若 `timezone` 不在 `from datetime import datetime, timezone` 里则补上。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_canvas_client.py -q`
Expected: 全部 PASS（既有测试不破）。

- [ ] **Step 5: Commit**

```bash
git add backend/canvas_client.py tests/test_canvas_client.py
git commit -m "feat: canvas_client 支持成绩/待办/日历事件读取"
```

---

### Task 2: main.py Canvas 端点 + 共享一次性事件写入

**Files:**
- Modify: `backend/main.py`（模型区 + 端点区）
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: Task 1 的 `list_courses(include_scores=True)`、`get_assignments_full`、`get_todo`、`get_calendar_events`；既有 `apple_script.find_events`、`apple_script.add_calendar_event`。
- Produces（Task 3/4/6 消费）:
  - `POST /api/grades {canvas_url, canvas_token, course_ids}` → `{ok, courses:[{course_id, course_name, current_score, final_score, assignments:[...]}], errors}`
  - `POST /api/todo {canvas_url, canvas_token}` → `{ok, items:[...]}`
  - `POST /api/calendar_events {canvas_url, canvas_token, course_ids, start_date, end_date}` → `{ok, events:[...]}`
  - `POST /api/write_canvas_events {calendar_name, items:[{title,start,end,location,notes}], alert_minutes}` → `{ok, items:[{title,status}], created, exists, errors}`
  - 私有 `_write_one_off(calendar_name, items, alert_minutes)`（Task 6 的写考试复用）

- [ ] **Step 1: 写失败的测试**（追加到 `tests/test_main.py`）

```python
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
    assert body["errors"] == {5: "boom"}


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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_main.py -q`
Expected: 新测试 FAIL（端点不存在）。

- [ ] **Step 3: 实现**

模型区（`AssignmentsRequest` 附近）加：

```python
class GradesRequest(CanvasConfig):
    course_ids: list[int]


class CalendarEventsRequest(CanvasConfig):
    course_ids: list[int]
    start_date: str
    end_date: str


class WriteCanvasEventsRequest(BaseModel):
    calendar_name: str
    items: list[dict]   # [{title, start, end, location, notes}]
    alert_minutes: int | None = None
```

端点区（`/api/assignments` 之后）加：

```python
@app.post("/api/grades")
def grades(req: GradesRequest):
    """课程总评 + 作业明细；单门作业失败进 errors（该门 assignments=[]），不拖垮整批。"""
    try:
        courses = canvas_client.list_courses(req.canvas_url, req.canvas_token, include_scores=True)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    by_id = {c["id"]: c for c in courses}
    results = []
    errors = {}
    for cid in req.course_ids:
        c = by_id.get(cid)
        if c is None:
            results.append({"course_id": cid, "course_name": f"Course {cid}",
                            "current_score": None, "final_score": None, "assignments": []})
            continue
        try:
            asg = canvas_client.get_assignments_full(req.canvas_url, req.canvas_token, cid)
        except Exception as exc:
            asg = []
            errors[cid] = str(exc)
        results.append({
            "course_id": cid, "course_name": c["name"],
            "current_score": c.get("current_score"), "final_score": c.get("final_score"),
            "assignments": asg,
        })
    return {"ok": True, "courses": results, "errors": errors}


@app.post("/api/todo")
def todo(req: CanvasConfig):
    try:
        return {"ok": True, "items": canvas_client.get_todo(req.canvas_url, req.canvas_token)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/calendar_events")
def calendar_events(req: CalendarEventsRequest):
    try:
        events = canvas_client.get_calendar_events(
            req.canvas_url, req.canvas_token, req.course_ids,
            req.start_date, req.end_date)
        return {"ok": True, "events": events}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _write_one_off(calendar_name: str, items: list[dict], alert_minutes: int | None) -> dict:
    """共享一次性事件写入：按 标题+开始时间 去重（Task 6 写考试复用）。

    items: [{title, start, end, location, notes}]。find_events 按标题前缀读回既有
    事件，比较 (summary==标题 且 start==开始时间) 判已存在→跳过。返回
    {items:[{title,status,error?}], created, exists, errors}。
    """
    created = exists = errors = 0
    out = []
    for item in items:
        title = (item.get("title") or "").strip()
        start = (item.get("start") or "").strip()
        if not title or not start:
            errors += 1
            out.append({"title": title or (item.get("title") or ""), "status": "error",
                        "error": "缺标题或开始时间"})
            continue
        try:
            existing = apple_script.find_events(calendar_name, title)
            if any(ev["summary"] == title and ev["start"] == start for ev in existing):
                exists += 1
                out.append({"title": title, "status": "exists"})
                continue
            apple_script.add_calendar_event(
                calendar_name, title, start, item.get("end") or start,
                item.get("location") or "", item.get("notes") or "", alert_minutes)
            created += 1
            out.append({"title": title, "status": "created"})
        except Exception as exc:
            errors += 1
            out.append({"title": title, "status": "error", "error": str(exc)})
    return {"items": out, "created": created, "exists": exists, "errors": errors}


@app.post("/api/write_canvas_events")
def write_canvas_events(req: WriteCanvasEventsRequest):
    try:
        res = _write_one_off(req.calendar_name, req.items, req.alert_minutes)
        return {"ok": True, **res}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_main.py -q`
Expected: 全部 PASS（既有测试不破）。

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_main.py
git commit -m "feat: grades/todo/calendar_events/write_canvas_events 端点 + 共享一次性事件写入"
```

---

### Task 3: 前端「待办」标签页

**Files:**
- Modify: `frontend/index.html`（tab 按钮 + `tabTodo` 面板）
- Modify: `frontend/app.js`（待办状态 + 渲染 + 写入）
- Modify: `frontend/i18n.js`（zh + en 各补待办键）
- Modify: `frontend/app.css`（如需待办分组样式——非必需，可复用既有 `.course-card`）

**Interfaces:**
- Consumes: Task 2 的 `/api/todo`、`/api/calendar_events`、`/api/write_canvas_events`；既有 `selectedCourses()`、`settings()`、`api()`、`withBusy()`、`esc()`/`escAttr()`、`fillSelect`、`fillAlert`、`switchTab`、`$`。
- Produces: 无（Task 4 独立）。

- [ ] **Step 1: 加标签页 HTML**（`frontend/index.html`）

`.tabs` 里 `tabSchedule` 之后加按钮：

```html
<button class="tab" data-target="tabTodo" role="tab" aria-selected="false" data-i18n="tab.todo"></button>
<button class="tab" data-target="tabGrades" role="tab" aria-selected="false" data-i18n="tab.grades"></button>
```

`tabSchedule` 面板之后加面板：

```html
<div id="tabTodo" class="tab-panel" role="tabpanel" hidden>
  <div class="filter-bar" id="todoStatus">
    <span class="filter-label" data-i18n="todo.groups_label"></span>
    <button id="btnLoadTodo" class="btn btn-primary" data-i18n="btn.load_todo"></button>
  </div>
  <div id="todoGroups"></div>
  <div class="sub-label" style="margin-top:18px" data-i18n="todo.events_heading"></div>
  <div id="todoEvents" class="muted" style="margin-top:4px"></div>
  <div class="write-bar">
    <label class="field"><span data-i18n="schedule.calendar"></span><select id="selTodoCalendar"></select></label>
    <label class="field"><span data-i18n="schedule.alert"></span><select id="selTodoAlert"></select></label>
    <button id="btnWriteTodoEvents" class="btn btn-accent" data-i18n="todo.write_events"></button>
  </div>
</div>
```

`switchTab` 的点击绑定里，`tabTodo` 首次进入要触发加载（见 Step 2；把下面这行加进既有 `$$(".tab").forEach(...)` 的 handler 里，`tabSchedule` 那行之后）：

```js
  if(target==="tabTodo") initTodoTab();
```

（`tabGrades` 的 `if(target==="tabGrades") initGradesTab();` 由 Task 4 加，避免在 `initGradesTab` 定义前引用它。）

- [ ] **Step 2: 加 i18n 键**（`frontend/i18n.js`，zh 与 en 两个块各补）

zh 块（`"tab.schedule"` 附近加 tab，`"schedule.prof_unspecified"` 后加 todo 组）：
```js
    "tab.todo": "待办",
    "tab.grades": "成绩",
    "todo.groups_label": "待办 / 即将截止",
    "btn.load_todo": "刷新待办",
    "todo.group_overdue": "已过期",
    "todo.group_today": "今天",
    "todo.group_week": "本周（7 天内）",
    "todo.group_later": "以后",
    "todo.no_todo": "暂无待办事项 🎉",
    "todo.events_heading": "Canvas 日历事件（一次性事件）",
    "todo.write_events": "写入选中的日历事件",
    "todo.no_events": "暂无课程日历事件。请先勾选课程再刷新。",
    "todo.need_course": "请先在上方勾选课程",
    "todo.loading": "正在加载待办与日历事件…",
    "todo.loaded": "已加载 {a} 项待办、{b} 条日历事件",
    "todo.fail": "加载待办失败：",
    "todo.overdue_badge": "已过期",
    "status.todo_write_fail": "写入失败：",
    "todo.events_done": "已写入 {a}/{b} 条日历事件",
```
en 块（对应）：
```js
    "tab.todo": "To-do",
    "tab.grades": "Grades",
    "todo.groups_label": "To-do / Upcoming",
    "btn.load_todo": "Refresh",
    "todo.group_overdue": "Overdue",
    "todo.group_today": "Today",
    "todo.group_week": "This week (7 days)",
    "todo.group_later": "Later",
    "todo.no_todo": "Nothing to do 🎉",
    "todo.events_heading": "Canvas calendar events (one-off)",
    "todo.write_events": "Write selected events",
    "todo.no_events": "No course calendar events. Select courses above, then refresh.",
    "todo.need_course": "Select courses above first",
    "todo.loading": "Loading to-do and calendar events…",
    "todo.loaded": "Loaded {a} to-do items, {b} calendar events",
    "todo.fail": "Failed to load to-do: ",
    "todo.overdue_badge": "Overdue",
    "status.todo_write_fail": "Write failed: ",
    "todo.events_done": "Wrote {a}/{b} calendar events",
```

- [ ] **Step 3: 加 app.js 逻辑**

在课表区块之前（`/* ===== 课表（AIMS / Banweb）===== */` 上方）插入：

```js
/* ===== 待办 + Canvas 日历事件 ===== */
let todoItems = [];       // 归一化待办（/api/todo）
let todoEvents = [];      // Canvas 一次性事件（/api/calendar_events）
let todoTabInit = false;
async function initTodoTab(){
  if(todoTabInit) return;
  todoTabInit = true;
  if(!$("selTodoCalendar").options.length){
    const r = await api("calendars");
    fillSelect("selTodoCalendar", r.calendars || []);
  }
  if(!$("selTodoAlert").options.length) fillAlert("selTodoAlert");
  await loadTodo();
}
async function loadTodo(){
  await withBusy(t("todo.loading"), $("btnLoadTodo"), async ()=>{
    const s = settings();
    const r = await api("todo", { canvas_url:s.canvas_url, canvas_token:s.canvas_token });
    if(r.ok !== true){ setStatus(t("todo.fail") + (r.error || ""), "err"); return; }
    todoItems = r.items || [];
    const ids = selectedCourses();
    if(!ids.length){
      todoEvents = [];
      renderTodo();
      $("todoEvents").innerHTML = `<div class="muted">${t("todo.need_course")}</div>`;
      setStatus(t("todo.loaded", {a: todoItems.length, b: 0}), "ok");
      return;
    }
    const now = new Date();
    const end = new Date(now); end.setDate(end.getDate() + 30);
    const er = await api("calendar_events", {
      canvas_url:s.canvas_url, canvas_token:s.canvas_token,
      course_ids:ids, start_date:fmt(now), end_date:fmt(end) });
    if(er.ok !== true){ setStatus(t("todo.fail") + (er.error || ""), "err"); return; }
    todoEvents = er.events || [];
    renderTodo();
    setStatus(t("todo.loaded", {a: todoItems.length, b: todoEvents.length}), "ok");
  });
}
$("btnLoadTodo").onclick = () => { todoTabInit = false; initTodoTab(); };
/* 分组键：已过期 / 今天 / 本周（7 天内）/ 以后 */
function todoGroupKey(item){
  if(item.overdue) return "overdue";
  if(!item.due_at) return "later";
  const due = new Date(item.due_at);
  if(isNaN(due)) return "later";
  const now = new Date(); now.setHours(0,0,0,0);
  const dueDay = new Date(due); dueDay.setHours(0,0,0,0);
  const diff = Math.round((dueDay - now) / 86400000);
  if(diff <= 0) return "overdue";
  if(diff === 0) return "today";
  if(diff < 7) return "week";
  return "later";
}
function renderTodo(){
  const groups = { overdue: [], today: [], week: [], later: [] };
  todoItems.forEach(it => { (groups[todoGroupKey(it)] || groups.later).push(it); });
  const order = ["overdue", "today", "week", "later"];
  const keys = { overdue: t("todo.group_overdue"), today: t("todo.group_today"),
                 week: t("todo.group_week"), later: t("todo.group_later") };
  const has = order.some(k => groups[k].length);
  if(!has){
    $("todoGroups").innerHTML = `<div class="empty">${t("todo.no_todo")}</div>`;
  } else {
    $("todoGroups").innerHTML = order.map(k => {
      if(!groups[k].length) return "";
      return `<div class="sub-label">${esc(keys[k])}（${groups[k].length}）</div>` +
        groups[k].map(it => `
        <div class="item">
          <div>
            <div class="item-title">${it.html_url
              ? `<a href="${escAttr(it.html_url)}" target="_blank" rel="noopener">${esc(it.title)}</a>`
              : esc(it.title)}
              ${it.overdue ? `<span class="sched-badge err">${esc(t("todo.overdue_badge"))}</span>` : ""}</div>
            <div class="file-path">${esc(it.course_name || "")}${it.due_at ? " · " + esc(t("announce.due")) + " " + esc(fmtDue(it.due_at)) : ""}${it.points_possible != null ? " · " + esc(String(it.points_possible)) + " pts" : ""}</div>
          </div>
        </div>`).join("");
    }).join("");
  }
  const evs = todoEvents;
  if(!evs.length){
    $("todoEvents").innerHTML = `<div class="muted">${t("todo.no_events")}</div>`;
    return;
  }
  $("todoEvents").innerHTML = evs.map((e, i) => `
    <div class="item"><input type="checkbox" class="cev" data-i="${i}">
      <div><div class="item-title">${e.html_url
        ? `<a href="${escAttr(e.html_url)}" target="_blank" rel="noopener">${esc(e.title)}</a>`
        : esc(e.title)}</div>
      <div class="file-path">${esc(fmtDue(e.start_at))} → ${e.end_at ? esc(fmtDue(e.end_at)) : ""}${e.location_name ? " · " + esc(e.location_name) : ""}</div></div></div>`).join("");
}
$("btnWriteTodoEvents").onclick = async () => {
  const cal = $("selTodoCalendar").value;
  if(!cal){ setStatus(t("status.need_calendar"), "err"); return; }
  const sel = [...document.querySelectorAll(".cev:checked")].map(i => todoEvents[Number(i.dataset.i)]);
  if(!sel.length){ setStatus(t("status.no_event"), "err"); return; }
  const amVal = $("selTodoAlert").value ? Number($("selTodoAlert").value) : null;
  await withBusy(t("status.writing_events", {n: sel.length}), $("btnWriteTodoEvents"), async ()=>{
    const r = await api("write_canvas_events", {
      calendar_name: cal,
      items: sel.map(e => ({ title:e.title, start:e.start_at, end:e.end_at,
                            location:e.location_name || "", notes:"" })),
      alert_minutes: amVal });
    if(r.ok !== true){ setStatus(t("status.write_fail") + (r.error || ""), "err"); return; }
    setStatus(t("todo.events_done", {a:r.created, b:r.created + r.exists}),
      r.errors === 0 ? "ok" : "err");
  });
};
```

`applyLang()`（`frontend/i18n.js` 里 `renderSummaries(); renderFiles(); renderSchedule();` 一行）后追加守卫式调用：
```js
  if (typeof renderTodo === "function") renderTodo();
  if (typeof renderGrades === "function") renderGrades();
```

- [ ] **Step 4: 验证**

Run: `python3 -m pytest -q`（后端不动，应全绿）+ `node --check frontend/app.js frontend/i18n.js`
Expected: 后端全 PASS；node --check 无输出（语法通过）。

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/app.js frontend/i18n.js
git commit -m "feat: 待办标签页（分组展示 + Canvas 日历事件写 Apple 日历）"
```

---

### Task 4: 前端「成绩」标签页

**Files:**
- Modify: `frontend/index.html`（`tabGrades` 面板）
- Modify: `frontend/app.js`（成绩状态 + 渲染）
- Modify: `frontend/i18n.js`（zh + en 各补成绩键）

**Interfaces:**
- Consumes: Task 2 的 `/api/grades`；既有 `selectedCourses()`、`settings()`、`api()`、`withBusy()`、`esc()`、`switchTab`、`fmtDue`。
- Produces: 无。

- [ ] **Step 1: 加面板 HTML**（`frontend/index.html`，`tabTodo` 面板之后）

```html
<div id="tabGrades" class="tab-panel" role="tabpanel" hidden>
  <div class="filter-bar" id="gradesStatus">
    <span class="filter-label" data-i18n="grades.label"></span>
    <button id="btnLoadGrades" class="btn btn-primary" data-i18n="btn.load_grades"></button>
  </div>
  <div id="gradesArea"></div>
</div>
```

- [ ] **Step 2: 加 i18n 键**

zh 块：
```js
    "grades.label": "成绩",
    "btn.load_grades": "刷新成绩",
    "grades.current": "当前总分",
    "grades.final": "期末总分",
    "grades.no_grade": "暂无成绩",
    "grades.assignments": "作业明细",
    "grades.submitted": "已提交",
    "grades.unsubmitted": "未提交",
    "grades.no_data": "暂无成绩数据。请先勾选课程再刷新。",
    "grades.loading": "正在加载成绩…",
    "grades.loaded": "已加载 {n} 门课程成绩",
    "grades.fail": "加载成绩失败：",
```
en 块：
```js
    "grades.label": "Grades",
    "btn.load_grades": "Refresh grades",
    "grades.current": "Current",
    "grades.final": "Final",
    "grades.no_grade": "No grades yet",
    "grades.assignments": "Assignment breakdown",
    "grades.submitted": "Submitted",
    "grades.unsubmitted": "Not submitted",
    "grades.no_data": "No grade data. Select courses above, then refresh.",
    "grades.loading": "Loading grades…",
    "grades.loaded": "Loaded grades for {n} courses",
    "grades.fail": "Failed to load grades: ",
```

- [ ] **Step 3: 加 app.js 逻辑**（待办区块之后）

在既有 `$$(".tab").forEach(...)` 的 handler 里（Task 3 加的 `if(target==="tabTodo") initTodoTab();` 那行之后）追加：

```js
  if(target==="tabGrades") initGradesTab();
```

再在待办区块之后插入：

```js
/* ===== 成绩 ===== */
let gradesData = [];
let gradesTabInit = false;
async function initGradesTab(){
  if(gradesTabInit) return;
  gradesTabInit = true;
  await loadGrades();
}
async function loadGrades(){
  const ids = selectedCourses();
  if(!ids.length){ setStatus(t("todo.need_course"), "err"); return; }
  await withBusy(t("grades.loading"), $("btnLoadGrades"), async ()=>{
    const s = settings();
    const r = await api("grades", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                    course_ids: ids });
    if(r.ok !== true){ setStatus(t("grades.fail") + (r.error || ""), "err"); return; }
    gradesData = r.courses || [];
    renderGrades();
    setStatus(t("grades.loaded", {n: gradesData.length}), "ok");
  });
}
$("btnLoadGrades").onclick = () => { gradesTabInit = false; initGradesTab(); };
function renderGrades(){
  if(!gradesData.length){
    $("gradesArea").innerHTML = `<div class="empty">${t("grades.no_data")}</div>`;
    return;
  }
  $("gradesArea").innerHTML = gradesData.map(g => {
    const scoreLine = (g.current_score != null || g.final_score != null)
      ? `<div class="file-path">${esc(t("grades.current"))}: <b>${g.current_score != null ? esc(String(g.current_score)) : "—"}</b> · ${esc(t("grades.final"))}: <b>${g.final_score != null ? esc(String(g.final_score)) : "—"}</b></div>`
      : `<div class="file-path">${esc(t("grades.no_grade"))}</div>`;
    const rows = (g.assignments || []).map(a => `
      <div class="item">
        <div>
          <div class="item-title">${a.html_url
            ? `<a href="${escAttr(a.html_url)}" target="_blank" rel="noopener">${esc(a.name)}</a>`
            : esc(a.name)}
            ${a.submitted ? `<span class="file-saved">${esc(t("grades.submitted"))}</span>` : `<span class="muted">${esc(t("grades.unsubmitted"))}</span>`}</div>
          <div class="file-path">${a.due_at ? esc(t("announce.due")) + " " + esc(fmtDue(a.due_at)) : ""}
            ${a.points_possible != null ? " · " + esc(String(a.points_possible)) + " pts" : ""}
            ${a.score != null ? " · <b>" + esc(String(a.score)) + "</b>" : ""}</div>
        </div>
      </div>`).join("");
    return `
    <div class="course-card">
      <div class="course-name">${esc(g.course_name)}</div>
      ${scoreLine}
      <div class="sub-label">${esc(t("grades.assignments"))}（${(g.assignments || []).length}）</div>
      ${rows || `<div class="muted" style="padding:4px 0">${esc(t("detail.no_assignments"))}</div>`}
    </div>`;
  }).join("");
}
```

- [ ] **Step 4: 验证**

Run: `python3 -m pytest -q` + `node --check frontend/app.js frontend/i18n.js`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/app.js frontend/i18n.js
git commit -m "feat: 成绩标签页（总评 + 作业明细）"
```

---

### Task 5: banweb 考试时间表解析与抓取

**Files:**
- Modify: `backend/banweb.py`（`EXAM_PAGE` 常量 + 纯解析函数 + `get_exams`）
- Modify: `backend/main.py`（`WriteExamsRequest` + `/api/banweb/exams` + `/api/banweb/write_exams` + `_exam_to_event`）
- Test: `tests/test_banweb.py`、`tests/test_main.py`

**Interfaces:**
- Consumes: 既有 `_TableParser`、`_ensure_browser`、`_require_logged_in`、`_on_browser_thread`、`_retry_once`、`BanwebError`、`BANWEB`；Task 2 的 `_write_one_off`（main.py 内同模块直接调用）。
- Produces（Task 6 消费）:
  - `banweb.get_exams()` → `(term_label: str, exams: list[dict])`；exams 元素 `{course, code, section, date, start, end, room, seat}`（date=`YYYY-MM-DD`，start/end=`HH:MM`，无考试时 `[]`）。
  - `POST /api/banweb/exams` → `{ok, term_label, exams}`
  - `POST /api/banweb/write_exams {calendar_name, exams:[...], alert_minutes}` → `{ok, items:[{title,status}], created, exists, errors}`

**背景（实测）：** 考试页 `hwsrsett_cityu.P_DispSchd` 只显示当前注册学期；无考试时页面含 `Student Examination Timetable is currently not available.`。考试行所在数据表列结构在 9 月初不可见（本季未排期），解析器**按表头关键词匹配列**。表头关键词：course=course/subject/课程，section=section/sec/分班，date=date/日期，time=time/时间，venue=venue/building/地点，room=room/房间，seat=seat/no./座位。

- [ ] **Step 1: 写失败的测试**（追加到 `tests/test_banweb.py`）

```python
def test_parse_exam_html_not_available():
    html = ("<html><body>Student Examination Timetable (Semester A 2026/27)"
            " ... currently not available.</body></html>")
    assert banweb.parse_exam_html(html) == []


EXAM_FIXTURE = """<table>
<tr><td>Course</td><td>Section</td><td>Date</td><td>Time</td><td>Venue</td><td>Room</td><td>Seat No.</td></tr>
<tr><td>CS2315</td><td>C01</td><td>2026-12-15</td><td>14:30 - 17:30</td><td>AC1</td><td>LT-17</td><td>23</td></tr>
<tr><td>MA2000</td><td>B01</td><td>2026-12-17</td><td>09:00 - 12:00</td><td>AC2</td><td>LT-4</td><td>11</td></tr>
</table>"""


def test_parse_exam_html_parses_table():
    out = banweb.parse_exam_html(EXAM_FIXTURE)
    assert len(out) == 2
    e = out[0]
    assert e["code"] == "CS2315"
    assert e["section"] == "C01"
    assert e["date"] == "2026-12-15"
    assert e["start"] == "14:30"
    assert e["end"] == "17:30"
    assert e["room"] == "LT-17"
    assert e["seat"] == "23"


def test_parse_exam_html_12h_time():
    html = EXAM_FIXTURE.replace("14:30 - 17:30", "2:30 pm - 5:30 pm")
    out = banweb.parse_exam_html(html)
    assert out[0]["start"] == "14:30"
    assert out[0]["end"] == "17:30"


def test_parse_exam_html_bad_date_kept():
    html = EXAM_FIXTURE.replace("2026-12-15", "15 Dec 2026")
    out = banweb.parse_exam_html(html)
    assert out[0]["date"] == "2026-12-15"


def test_parse_exam_html_no_recognized_headers():
    html = "<table><tr><td>Foo</td><td>Bar</td></tr><tr><td>a</td><td>b</td></tr></table>"
    assert banweb.parse_exam_html(html) == []


def test_split_exam_time_24h():
    assert banweb._split_exam_time("14:30 - 17:30") == ("14:30", "17:30")


def test_parse_exam_date_formats():
    assert banweb._parse_exam_date("2026-12-15") == "2026-12-15"
    assert banweb._parse_exam_date("15/12/2026") == "2026-12-15"
    assert banweb._parse_exam_date("15 Dec 2026") == "2026-12-15"
    assert banweb._parse_exam_date("") == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_banweb.py -q`
Expected: 新测试 FAIL（`parse_exam_html` 不存在）。

- [ ] **Step 3: 实现**（`backend/banweb.py`，`parse_date_range` 附近加常量与纯函数，文件尾加 `get_exams`）

常量（`WEEKLY_PAGE` 定义后加）：
```python
# 考试时间表（Site Map「Examination Timetable」实测）：只显示当前注册学期
EXAM_PAGE = BANWEB + "/pls/PROD/hwsrsett_cityu.P_DispSchd"
_EXAM_NOT_AVAILABLE = "currently not available"
_EXAM_COLUMN_KEYS = {
    "course": ("course", "subject", "课程"),
    "section": ("section", "sec", "分班"),
    "date": ("date", "日期"),
    "time": ("time", "时间"),
    "venue": ("venue", "building", "地点"),
    "room": ("room", "房间"),
    "seat": ("seat", "no.", "座位"),
}
```

纯解析函数（`parse_date_range` 之后）：

```python
def _split_exam_time(s: str) -> tuple[str, str]:
    """"14:30 - 17:30" / "2:30 pm - 5:30 pm" → ("14:30","17:30")；解析失败返回 (s,"")。"""
    s = s.strip()
    if not s:
        return "", ""
    m = re.match(r"(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})$", s)
    if m:
        return (f"{int(m.group(1)):02d}:{m.group(2)}",
                f"{int(m.group(3)):02d}:{m.group(4)}")
    try:
        (sh, sm), (eh, em) = parse_time_range(s)   # 12h "2:30 pm - 5:30 pm"
        return f"{sh:02d}:{sm:02d}", f"{eh:02d}:{em:02d}"
    except BanwebError:
        return s, ""


def _parse_exam_date(s: str) -> str:
    """"2026-12-15" / "15/12/2026" / "15 Dec 2026" → "YYYY-MM-DD"；失败返回 "". """
    s = s.strip()
    if not s:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    m = re.fullmatch(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})", s)
    if m:
        month = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                 "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
        mon = month.get(m.group(2).capitalize()[:3])
        if mon:
            return f"{m.group(3)}-{mon:02d}-{int(m.group(1)):02d}"
    return ""


def _map_exam_columns(rows: list[list[str]]) -> dict | None:
    """按表头关键词把列名映射到列下标；无 date 也无 time 列 → None。"""
    if not rows:
        return None
    header = [h.lower() for h in rows[0]]
    mapping: dict[str, int] = {}
    for field, keys in _EXAM_COLUMN_KEYS.items():
        for i, h in enumerate(header):
            if any(k in h for k in keys):
                mapping[field] = i
                break
    if "date" not in mapping and "time" not in mapping:
        return None
    return mapping


def _row_to_exam(row: list[str], cols: dict) -> dict | None:
    def _cell(field: str) -> str:
        i = cols.get(field)
        return row[i].strip() if i is not None and i < len(row) else ""

    course = _cell("course")
    if not course and not _cell("date") and not _cell("time"):
        return None
    m = re.search(r"[A-Z]+\d{4}", course.upper())
    code = m.group(0) if m else re.sub(r"\s", "", course)
    start, end = _split_exam_time(_cell("time"))
    return {
        "course": course,
        "code": code,
        "section": _cell("section"),
        "date": _parse_exam_date(_cell("date")),
        "start": start,
        "end": end,
        "room": _cell("room") or _cell("venue"),
        "seat": _cell("seat"),
    }


def parse_exam_html(html: str) -> list[dict]:
    """解析考试时间表页面，返回考试块列表。

    含 "currently not available" → []。否则遍历 _TableParser 的表，找表头含
    date/time 关键词的数据表，按列映射解析每行（不依赖列序）。
    """
    if _EXAM_NOT_AVAILABLE in html:
        return []
    parser = _TableParser()
    parser.feed(html)
    for t in parser.tables:
        cols = _map_exam_columns(t["rows"])
        if not cols:
            continue
        out = []
        for row in t["rows"][1:]:
            rec = _row_to_exam(row, cols)
            if rec:
                out.append(rec)
        return out
    return []
```

文件尾（`get_schedule` 之后）加：

```python
def get_exams() -> tuple[str, list[dict]]:
    """抓取当前注册学期的考试时间表。返回 (term_label, exams)。

    term_label 取页面标题行 "Student Examination Timetable (Semester A 2026/27)"
    括号内的学期名；无考试时 exams=[]。失败抛 BanwebError。
    """
    def _run() -> tuple[str, list[dict]]:
        with _lock:
            page = _ensure_browser()
            try:
                page.goto(EXAM_PAGE, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_load_state("load", timeout=30000)
            except Exception as exc:
                if _target_closed(exc):
                    raise
                raise BanwebError("抓取考试时间表失败（可能登录已失效）：" + str(exc)) from exc
            _require_logged_in(page)
            content = page.content()
            m = re.search(r"Student Examination Timetable\s*\(([^)]+)\)", content)
            return (m.group(1) if m else ""), parse_exam_html(content)
    return _on_browser_thread(lambda: _retry_once(_run))
```

- [ ] **Step 4: 加端点测试**（追加到 `tests/test_main.py`）

```python
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
```

- [ ] **Step 5: 加端点实现**（`backend/main.py`）

模型区（`WriteCanvasEventsRequest` 之后）加：
```python
class WriteExamsRequest(BaseModel):
    calendar_name: str
    exams: list[dict]   # [{course, code, section, date, start, end, room, seat}]
    alert_minutes: int | None = None
```

端点区（`write_canvas_events` 之后）加：
```python
def _exam_to_event(exam: dict) -> dict:
    """考试块 → 一次性事件规格 {title, start, end, location, notes}。"""
    code = (exam.get("code") or exam.get("course") or "Exam").strip()
    title = f"{code} 考试"
    date = (exam.get("date") or "").strip()
    start = (exam.get("start") or "").strip()
    end = (exam.get("end") or "").strip()
    start_iso = f"{date}T{start}:00" if date and start else ""
    end_iso = f"{date}T{end}:00" if date and end else start_iso
    loc = " ".join(x for x in (exam.get("room"), exam.get("seat")) if x).strip()
    return {"title": title, "start": start_iso, "end": end_iso,
            "location": loc, "notes": exam.get("course") or ""}


@app.post("/api/banweb/exams")
def banweb_exams():
    try:
        term_label, exams = banweb.get_exams()
        return {"ok": True, "term_label": term_label, "exams": exams}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/banweb/write_exams")
def banweb_write_exams(req: WriteExamsRequest):
    try:
        items = [_exam_to_event(exam) for exam in req.exams]
        res = _write_one_off(req.calendar_name, items, req.alert_minutes)
        return {"ok": True, **res}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python3 -m pytest tests/test_banweb.py tests/test_main.py -q`
Expected: 全部 PASS。

- [ ] **Step 7: Commit**

```bash
git add backend/banweb.py backend/main.py tests/test_banweb.py tests/test_main.py
git commit -m "feat: AIMS 考试时间表抓取与写 Apple 日历"
```

---

### Task 6: 前端课表考试块叠加

**Files:**
- Modify: `frontend/index.html`（`tabSchedule` 面板内加考试条）
- Modify: `frontend/app.js`（考试状态 + renderSchedule 叠加 + 写入）
- Modify: `frontend/app.css`（`.exam-block` 样式）
- Modify: `frontend/i18n.js`（zh + en 各补考试键）

**Interfaces:**
- Consumes: Task 5 的 `/api/banweb/exams`、`/api/banweb/write_exams`；既有 `renderSchedule`、`schedWeekStart`、`gridMinutes`、`fmtTime`、`DAY_INDEX`、`banwebSchedule`、`api()`、`esc()`。
- Produces: 无。

- [ ] **Step 1: 加考试条 HTML**（`frontend/index.html`，`scheduleNoFixed` 之后、`write-bar` 之前）

```html
<div class="filter-bar" id="examBar" style="margin-top:14px">
  <span class="filter-label" data-i18n="schedule.exam_label"></span>
  <button id="btnReloadExams" class="btn btn-ghost" data-i18n="schedule.exam_reload"></button>
  <span id="examStatus" class="muted"></span>
  <span style="flex:1"></span>
  <label class="field"><span data-i18n="schedule.calendar"></span><select id="selExamCalendar"></select></label>
  <button id="btnWriteExams" class="btn btn-accent" data-i18n="schedule.write_exams"></button>
</div>
```

- [ ] **Step 2: 加 i18n 键**

zh 块：
```js
    "schedule.exam_label": "考试时间表",
    "schedule.exam_reload": "刷新考试",
    "schedule.write_exams": "写入考试",
    "schedule.exam_none": "暂无考试时间表",
    "schedule.exam_loading": "正在加载考试…",
    "schedule.exam_loaded": "考试时间表 {term}",
    "schedule.exam_fail": "加载考试时间表失败：",
    "schedule.exam_badge": "考试",
    "schedule.exam_done": "已写入考试：新建 {a} · 已存在 {b} · 失败 {e}",
```
en 块：
```js
    "schedule.exam_label": "Exam timetable",
    "schedule.exam_reload": "Refresh exams",
    "schedule.write_exams": "Write exams",
    "schedule.exam_none": "No exams scheduled",
    "schedule.exam_loading": "Loading exams…",
    "schedule.exam_loaded": "Exam timetable {term}",
    "schedule.exam_fail": "Failed to load exams: ",
    "schedule.exam_badge": "EXAM",
    "schedule.exam_done": "Exams written: created {a} · exists {b} · failed {e}",
```

- [ ] **Step 3: 加 app.js**（`initScheduleTab` 前的课表区块内加考试状态与函数）

```js
/* ===== 考试时间表叠加 ===== */
let banwebExams = null;   // {term_label, exams} | null
function examMinutes(hhmm){
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm || "");
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}
async function loadExams(){
  const el = $("examStatus");
  if(el) el.textContent = t("schedule.exam_loading");
  const r = await api("banweb/exams");
  if(r.ok !== true){
    banwebExams = null;
    if(el) el.textContent = t("schedule.exam_fail") + (r.error || "");
    renderSchedule();
    return;
  }
  banwebExams = { term_label: r.term_label || "", exams: r.exams || [] };
  if(el) el.textContent = banwebExams.exams.length
    ? t("schedule.exam_loaded", {term: banwebExams.term_label})
    : t("schedule.exam_none");
  renderSchedule();
}
$("btnReloadExams").onclick = loadExams;
$("btnWriteExams").onclick = async () => {
  const cal = $("selExamCalendar").value;
  if(!cal){ setStatus(t("status.need_sched_calendar"), "err"); return; }
  const exams = (banwebExams && banwebExams.exams) || [];
  if(!exams.length){ setStatus(t("schedule.exam_none"), "err"); return; }
  const amVal = $("selSchedAlert").value ? Number($("selSchedAlert").value) : null;
  await withBusy(t("status.writing_events", {n: exams.length}), $("btnWriteExams"), async ()=>{
    const r = await api("banweb/write_exams", { calendar_name: cal, exams, alert_minutes: amVal });
    if(r.ok !== true){ setStatus(t("status.write_fail") + (r.error || ""), "err"); return; }
    setStatus(t("schedule.exam_done", {a:r.created, b:r.exists, e:r.errors}),
      r.errors === 0 ? "ok" : "err");
  });
};
```

`renderSchedule` 内、课程块 `colBlocks` 渲染循环之后（在 `gridEl.innerHTML = ...` 之前）插考试块叠加（叠在课程块之上）：

```js
  // 考试块叠加：日期落在浏览周 → 加到对应日列（叠在课程块之上）
  if (banwebExams && banwebExams.exams.length) {
    banwebExams.exams.forEach(ex => {
      if (!ex.date) return;
      const d = new Date(ex.date + "T00:00:00");
      if (isNaN(d)) return;
      const t = d.getTime();
      if (t < wkStart || t >= wkEnd) return;
      const idx = (d.getDay() + 6) % 7;
      const sm = examMinutes(ex.start);
      if (sm == null) return;
      const em = examMinutes(ex.end);
      const top = (sm - lo) * PX_PER_MIN;
      const hgt = em != null ? Math.max(20, (em - sm) * PX_PER_MIN) : 90;
      const title = (ex.code || ex.course || "Exam") + " " + (ex.section || "");
      colBlocks[idx] += `<div class="cal-block exam-block" style="top:${top}px;height:${hgt}px">
        <span class="exam-tag">${esc(t("schedule.exam_badge"))}</span>
        <div style="font-weight:600;color:#fff">${esc(title)}</div>
        <div style="color:rgba(255,255,255,.9)">${fmtTime(sm)}–${em != null ? fmtTime(em) : "?"}</div>
        ${(ex.room || ex.seat) ? `<div style="color:rgba(255,255,255,.8)">${esc([ex.room, ex.seat].filter(Boolean).join(" · "))}</div>` : ""}
      </div>`;
    });
  }
```

`initScheduleTab` 内（`renderSchedule();` 之后）加：
```js
  if(!$("selExamCalendar").options.length){
    const r = await api("calendars");
    fillSelect("selExamCalendar", r.calendars || []);
  }
  loadExams();   // 静默拉考试（登录态复用课表会话；失败仅提示不阻塞）
```

- [ ] **Step 4: 加 CSS**（`frontend/app.css`，`.cal-block` 附近）

```css
/* 考试块：独立深色，叠在课程块之上 */
.cal-block.exam-block{ background:#7f1d1d; border:2px solid #ef4444; box-shadow:0 2px 8px rgba(0,0,0,.35); }
.exam-tag{ display:inline-block; font-size:10px; font-weight:700; letter-spacing:.5px;
  background:#ef4444; color:#fff; border-radius:3px; padding:1px 5px; margin-bottom:3px; }
```

- [ ] **Step 5: 验证**

Run: `python3 -m pytest -q` + `node --check frontend/app.js frontend/i18n.js`
Expected: 全绿。

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/app.js frontend/app.css frontend/i18n.js
git commit -m "feat: 课表周视图叠加考试时间表块 + 写 Apple 日历"
```

---

## Self-Review Notes

- **覆盖核对**：成绩（Task 1 分数读取 + Task 2 /api/grades + Task 4 tab）、待办（Task 1 get_todo + Task 2 /api/todo + Task 3 tab）、日历事件（Task 1 get_calendar_events + Task 2 端点与写循环 + Task 3 写按钮）、考试（Task 5 抓取+端点 + Task 6 叠加）。spec 的「安全/约束」全部落进 Global Constraints。
- **类型一致**：`_write_one_off` 返回 `{items, created, exists, errors}`；Task 2 的 `/api/write_canvas_events` 与 Task 5 的 `/api/banweb/write_exams` 共用同一签名（`{ok, **res}`），Task 3/6 消费同一结构。`get_exams()` 返回 `(term_label, exams)`，Task 5 端点包装为 `{ok, term_label, exams}`，Task 6 消费。
- **考试时间 12h 分支**：`_split_exam_time` 复用 `parse_time_range`（已存在、处理 12h），无重复逻辑；24h 分支独立 regex。实现时以 Task 5 Step 3 给出的完整版为准。
