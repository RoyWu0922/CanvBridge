# 课程详情 + Syllabus AI 总结 + 作业 due 标注 + 教授筛选 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有 Canvas 课程助手（公告总结 / 文件 / 课表周视图）上新增四项能力：可点击的课程详情弹层（含 syllabus、作业、教授）、syllabus AI 总结、周视图作业 due 琥珀色标注、按教授筛选课表。

**Architecture:** 后端在 `canvas_client.py` 新增 `get_course` / `get_assignments` 两个读函数、`llm_client.py` 新增 `summarize_syllabus`、`banweb.py` 新增 `primary_instructor` 纯函数（并在 `enrich_meetings` 里给每个课程块附加该字段）；`main.py` 新增 3 个端点并给 `sync_announcements` 结果补 `course_id`。前端保持无构建 vanilla JS：新增详情弹层 DOM + 周视图作业标注条 + 教授下拉，全部走既有 `api()` / `esc()` / `t()` 模式。

**Tech Stack:** Python 3.10+（FastAPI + requests + pytest）、vanilla JS（无构建工具，FastAPI 静态托管）、OpenAI 兼容 LLM 客户端（复用 `_call_chat`）。

**Spec:** [2026-08-31-course-detail-assignments-design.md](../specs/2026-08-31-course-detail-assignments-design.md)（本计划以其为准，实现时请一并阅读）

## Global Constraints

- 后端改动后必须**手动重启** uvicorn（无 `--reload`）：`uvicorn backend.main:app --host 127.0.0.1 --port 8000`。前端改动刷新浏览器即可。
- 测试命令：`pytest tests/<file>.py -v`（单文件）；全量 `pytest tests/ -q`。
- 前端无自动化测试，任务以「手动验证」步骤收尾：本地起服务 → 浏览器 localhost:8000 走通指定流程。
- **XSS 红线**：syllabus / 课程名 / 教授名 / 作业名一律 `esc()` 后再进 innerHTML；syllabus 在后端已 `strip_html` 成纯文本，前端绝不渲染原始 HTML。
- 所有新增文案进 `frontend/i18n.js` 的 `zh` / `en` 字典，用 `t()` 引用；能复用既有 key 就复用（如「截止」用 `announce.due`、「请先勾选课程」用 `status.need_course`）。
- 作业提交只做「新标签页打开 Canvas 作业页」跳转（`<a target="_blank">`），不做应用内代提交。
- 作业 due 只标周视图；已截止作业不进结果（后端 `get_assignments` 过滤）；无截止日期作业进详情列表但不上日历。
- 教授筛选只影响渲染（网格 + 无固定时间区），不改变 `banwebSchedule.selected` 与 localStorage。
- 不引入任何前端框架 / 构建工具；不改动 Banweb 抓取、AppleScript 写入、文件下载既有逻辑。
- LLM API key 留本机（沿用 `settings()` 从表单读）。

---

## 文件结构

| 文件 | 职责 | 改动 |
|------|------|------|
| `backend/canvas_client.py` | Canvas 读 API | +`get_course`、+`get_assignments`、+`datetime` 导入 |
| `backend/llm_client.py` | OpenAI 兼容 LLM | `_call_chat` 加 `json_mode` 参数、+`_SYSTEM_TEXT`、+`summarize_syllabus` |
| `backend/banweb.py` | Banweb 解析 | +`primary_instructor`、`enrich_meetings` 附加 `primary_instructor` |
| `backend/main.py` | FastAPI 端点 | +3 个 request model、+3 个端点、`sync_announcements` 补 `course_id` |
| `frontend/i18n.js` | 文案字典 | 各任务按需新增 zh/en key |
| `frontend/index.html` | 页面骨架 | +`#detailModal`、+课表 filter-bar 作业按钮与教授下拉 |
| `frontend/app.js` | 前端逻辑 | 详情弹层、AI 总结、作业标注、教授筛选 |
| `frontend/app.css` | 样式 | 详情弹层、琥珀标注、`cal-detail` 按钮 |
| `tests/test_canvas_client.py` | 后端单测 | +`get_course` / `get_assignments` |
| `tests/test_llm_client.py` | 后端单测 | +`summarize_syllabus` / `_call_chat` json_mode |
| `tests/test_banweb.py` | 后端单测 | +`primary_instructor` / `enrich_meetings` 附加字段 |
| `tests/test_main.py` | 端点测试 | +3 端点、+`sync_announcements` course_id |

**后端（任务 1–5）必须全部完成并重启后，前端（任务 7–9）才能走通手动验证**——前端任务依赖任务 5 的端点。

---

## Phase 1 — 后端数据层

### Task 1: `get_course` — 单课程详情（syllabus + 教授）

**Files:**
- Modify: `backend/canvas_client.py`
- Test: `tests/test_canvas_client.py`

**Interfaces:**
- Consumes: 既有 `_headers(token)`、`_paginate(session, url, params, token)`、`strip_html(html)`、`CanvasError`。
- Produces: `get_course(canvas_url: str, token: str, course_id: int) -> dict`，返回 `{"id": int, "name": str, "syllabus_text": str, "teachers": list[str]}`。任务 5 的 `/api/course_detail` 消费它。

- [ ] **Step 1: 写失败测试**

在 `tests/test_canvas_client.py` 末尾追加两个测试。`_Resp` 已在该文件顶部定义（`json()` 返回传入的 data）。

```python
def test_get_course_maps_fields(monkeypatch):
    """单课程 GET 返回 dict（非列表），须直接取 resp.json()，teachers 走 users 端点。"""
    course = {"id": 42, "name": "CS 101",
              "syllabus_body": "<p>Welcome to <b>CS 101</b></p>"}

    class _CtxSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None, headers=None):
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
        def get(self, url, params=None, headers=None):
            return _Resp({"id": 42, "name": "CS 101"})

    monkeypatch.setattr(requests, "Session", lambda: _CtxSession())
    monkeypatch.setattr(canvas_client, "_paginate", lambda s, u, p, t: [])
    result = canvas_client.get_course("https://x.instructure.com", "tok", 42)
    assert result["syllabus_text"] == ""
    assert result["teachers"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_canvas_client.py::test_get_course_maps_fields -v`
Expected: FAIL with `AttributeError: module 'backend.canvas_client' has no attribute 'get_course'`

- [ ] **Step 3: 实现 `get_course`**

在 `canvas_client.py` 顶部加导入，然后在 `list_courses` 之后新增函数：

```python
from datetime import datetime, timezone  # 放到文件顶部现有 import 区
```

```python
def get_course(canvas_url: str, token: str, course_id: int) -> dict:
    """返回单课程详情 {id, name, syllabus_text, teachers}。

    syllabus_body 用 strip_html 转纯文本（前端只渲染纯文本，不碰原始 HTML）。
    teachers 取 TeacherEnrollment + TaEnrollment 的 user 名字。
    syllabus 缺失（空课程）返回空串；认证错误抛 CanvasError。
    """
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        resp = s.get(
            f"{base}/api/v1/courses/{course_id}",
            params={"include[]": "syllabus_body"}, headers=_headers(token), timeout=30,
        )
        if resp.status_code == 401:
            raise CanvasError("Canvas token 无效或已过期 (HTTP 401)")
        if resp.status_code == 403:
            raise CanvasError("没有权限访问该资源 (HTTP 403)")
        resp.raise_for_status()
        course = resp.json()
        teachers = _paginate(
            s, f"{base}/api/v1/courses/{course_id}/users",
            {"enrollment_type[]": ["TeacherEnrollment", "TaEnrollment"], "per_page": 100},
            token,
        )
    return {
        "id": course.get("id", course_id),
        "name": course.get("name", f"Course {course_id}"),
        "syllabus_text": strip_html(course.get("syllabus_body", "")),
        "teachers": [t.get("name", "") for t in teachers if t.get("name")],
    }
```

注意：单课程端点是单个对象（dict）不是列表，**不能用 `_paginate`**（它 `extend(resp.json())`，dict 会炸），所以 course 用直接 GET、只有 users 端点走 `_paginate`。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_canvas_client.py -v`
Expected: 全部 PASS（含既有 `test_list_courses_includes_invited_enrollments` 等）

- [ ] **Step 5: 提交**

```bash
git add backend/canvas_client.py tests/test_canvas_client.py
git commit -m "feat: canvas 课程详情取 syllabus 与教授 (get_course)"
```

---

### Task 2: `get_assignments` — 未截止作业列表

**Files:**
- Modify: `backend/canvas_client.py`
- Test: `tests/test_canvas_client.py`

**Interfaces:**
- Consumes: `_paginate`、`CanvasError`、`datetime`/`timezone`（Task 1 已导入）。
- Produces: `get_assignments(canvas_url: str, token: str, course_id: int) -> list[dict]`，返回 `[{"id", "name", "due_at", "points_possible", "html_url"}]`，只含未截止作业。任务 5 的 `/api/assignments` 逐课程调用它。

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_canvas_client.py::test_get_assignments_filters_and_builds -v`
Expected: FAIL with `AttributeError: ... no attribute 'get_assignments'`

- [ ] **Step 3: 实现 `get_assignments`**

在 `get_course` 之后新增：

```python
def get_assignments(canvas_url: str, token: str, course_id: int) -> list[dict]:
    """返回未截止作业 [{id, name, due_at, points_possible, html_url}]。

    无截止日期（due_at 为空）保留（详情展示用，不上日历）；
    due_at 在未来保留；已过或无法解析的丢弃。html_url 缺失时拼兜底 URL。
    """
    base = canvas_url.rstrip("/")
    now = datetime.now(timezone.utc)
    with requests.Session() as s:
        data = _paginate(
            s, f"{base}/api/v1/courses/{course_id}/assignments",
            {"per_page": 100}, token,
        )
    out = []
    for a in data:
        due = a.get("due_at")
        if due:
            try:
                due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
            except ValueError:
                continue
            if due_dt <= now:
                continue
        out.append({
            "id": a.get("id"),
            "name": a.get("name", "(untitled)"),
            "due_at": due or "",
            "points_possible": a.get("points_possible"),
            "html_url": a.get("html_url")
                or f"{base}/courses/{course_id}/assignments/{a.get('id')}",
        })
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_canvas_client.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add backend/canvas_client.py tests/test_canvas_client.py
git commit -m "feat: canvas 拉取未截止作业 (get_assignments)"
```

---

### Task 3: `summarize_syllabus` — syllabus AI 总结

**Files:**
- Modify: `backend/llm_client.py`
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Consumes: 既有 `_call_chat(base_url, api_key, model, prompt)`（本任务给它加可选参数）。
- Produces:
  - `_call_chat(base_url, api_key, model, prompt, json_mode=True)` —— 加 `json_mode=False` 时**不**强制 JSON 输出、换用纯文本 system prompt；默认 `True` 行为不变，既有调用不受影响。
  - `summarize_syllabus(base_url, api_key, model, course_name, syllabus_text, language="zh") -> str` —— 返回总结纯文本；失败重试一次后抛异常。任务 5 的 `/api/summarize_syllabus` 消费它。

- [ ] **Step 1: 写失败测试**

在 `tests/test_llm_client.py` 顶部加 `import pytest`（文件目前没导入），再追加三个测试：

```python
import pytest
```

```python
def test_summarize_syllabus_returns_text(monkeypatch):
    """json_mode 关掉（plain text），strip 后返回总结。"""
    captured = {}
    def fake_call(base, key, model, prompt, json_mode=True):
        captured["json_mode"] = json_mode
        captured["prompt"] = prompt
        return "  - Objective: learn Python\n- Grading: 40% exam\n"
    monkeypatch.setattr(llm_client, "_call_chat", fake_call)
    out = llm_client.summarize_syllabus(
        "https://llm/v1", "key", "m", "CS 101", "<p>syllabus</p>", language="zh")
    assert out == "- Objective: learn Python\n- Grading: 40% exam"
    assert captured["json_mode"] is False
    assert "CS 101" in captured["prompt"] and "Chinese" in captured["prompt"]


def test_summarize_syllabus_truncates_long(monkeypatch):
    """超长 syllabus 截断到约 20000 字符，防 token 超限。"""
    captured = {}
    def fake_call(base, key, model, prompt, json_mode=True):
        captured["prompt"] = prompt
        return "ok"
    monkeypatch.setattr(llm_client, "_call_chat", fake_call)
    llm_client.summarize_syllabus("u", "k", "m", "C", "x" * 50000)
    assert len(captured["prompt"]) < 21000


def test_summarize_syllabus_raises_after_retry(monkeypatch):
    """连续失败重试一次后抛异常（不静默降级成原文）。"""
    def boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr(llm_client, "_call_chat", boom)
    with pytest.raises(RuntimeError):
        llm_client.summarize_syllabus("u", "k", "m", "C", "text")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_llm_client.py::test_summarize_syllabus_returns_text -v`
Expected: FAIL with `AttributeError: ... no attribute 'summarize_syllabus'`

- [ ] **Step 3: 实现**

在 `_SYSTEM` 常量后加纯文本版 system prompt；给 `_call_chat` 加 `json_mode` 参数；在 `extract_course_summary` 后新增 `summarize_syllabus`。

```python
_SYSTEM_TEXT = (
    "You are a helpful academic assistant. Respond with concise plain text, "
    "never markdown, never JSON."
)
```

修改 `_call_chat` 签名与 payload（保持默认 `json_mode=True`，既有行为不变）：

```python
def _call_chat(base_url: str, api_key: str, model: str, prompt: str,
               json_mode: bool = True) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM if json_mode else _SYSTEM_TEXT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    resp = requests.post(
        url, json=payload,
        headers={"Authorization": f"Bearer {api_key}"}, timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("choices"):
        raise ValueError("LLM 响应无 choices")
    return data["choices"][0]["message"].get("content") or ""
```

新增函数（放在 `extract_course_summary` 之后）：

```python
_MAX_SYLLABUS = 20000


def summarize_syllabus(base_url: str, api_key: str, model: str,
                       course_name: str, syllabus_text: str,
                       language: str = "zh") -> str:
    """返回 syllabus 的中/英要点总结（纯文本）。

    syllabus 过长截断防 token 超限；失败重试一次，仍失败抛异常
    （由端点转 ok:false，不静默降级成原文）。
    """
    if len(syllabus_text) > _MAX_SYLLABUS:
        syllabus_text = syllabus_text[:_MAX_SYLLABUS] + "\n…(已截断)"
    lang = "Chinese" if language == "zh" else "English"
    prompt = (
        f'You are an academic assistant. Summarize the syllabus for "{course_name}" '
        f"as concise bullet points in {lang}. Cover: course objectives, grading "
        f"scheme, key deadlines and assessments, and anything a student must know.\n\n"
        f"Syllabus:\n{syllabus_text}"
    )
    last_err: Exception | None = None
    for _attempt in range(2):
        try:
            return _call_chat(base_url, api_key, model, prompt, json_mode=False).strip()
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"Syllabus 总结失败: {last_err}") from last_err
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_llm_client.py -v`
Expected: 全部 PASS（既有 `test_extract_*` 因 `_call_chat` 默认 `json_mode=True` 不受影响）

- [ ] **Step 5: 提交**

```bash
git add backend/llm_client.py tests/test_llm_client.py
git commit -m "feat: syllabus AI 总结 (summarize_syllabus)"
```

---

### Task 4: `primary_instructor` — 课表主教授提取

**Files:**
- Modify: `backend/banweb.py`
- Test: `tests/test_banweb.py`

**Interfaces:**
- Consumes: 纯读 `course["meetings"][].instr`。
- Produces: `primary_instructor(course: dict) -> str`（纯函数）；`enrich_meetings(courses)` 现在给每个课程块附加 `primary_instructor` 字段。`get_schedule` 已调用 `enrich_meetings`，无需改动。任务 9 前端下拉直接读该字段。

- [ ] **Step 1: 写失败测试**

在 `tests/test_banweb.py` 的 `enrich_meetings` 小节后追加：

```python
# ---------------- primary_instructor（课表主教授） ----------------

def test_primary_instructor_prefers_p_marker():
    """多个 meeting 里优先取带 (P) 主讲师标记的那个，并剥掉标记。"""
    course = {"meetings": [
        {"instr": "TA Bob"},
        {"instr": "Alice CHAN (P)"},
        {"instr": "TA Carol"},
    ]}
    assert banweb.primary_instructor(course) == "Alice CHAN"


def test_primary_instructor_falls_back_to_first_nonempty():
    """没有 (P) 标记 → 取第一个非空 instr（原样返回）。"""
    course = {"meetings": [{"instr": ""}, {"instr": "TA Bob"}, {"instr": "Dr. Eve"}]}
    assert banweb.primary_instructor(course) == "TA Bob"


def test_primary_instructor_empty_when_no_instructor():
    assert banweb.primary_instructor({"meetings": [{"instr": ""}, {"instr": ""}]}) == ""
    assert banweb.primary_instructor({"meetings": []}) == ""


def test_enrich_meetings_attaches_primary_instructor():
    """get_schedule 已调 enrich_meetings → 每个课程块带 primary_instructor，前端下拉直接取用。"""
    courses = [{"code": "CS1315", "section": "C01",
                "meetings": [{"time": "12:00 pm - 2:50 pm", "days": "F", "room": "MMW",
                              "range": "Aug 31, 2026 - Nov 28, 2026",
                              "instr": "Kenneth LEE (P)"}]}]
    out = banweb.enrich_meetings(courses)
    assert out[0]["primary_instructor"] == "Kenneth LEE"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_banweb.py::test_primary_instructor_prefers_p_marker -v`
Expected: FAIL with `AttributeError: ... no attribute 'primary_instructor'`

- [ ] **Step 3: 实现**

在 `enrich_meetings` 之前新增纯函数：

```python
def primary_instructor(course: dict) -> str:
    """取课程块的主讲师（显示用，前端筛选下拉直接取用）。

    遍历 meetings 的 instr：优先取含 (P) 主讲师标记的第一个并剥掉标记；
    无 (P) 则取第一个非空 instr。无则返回空串。
    """
    for m in course.get("meetings", []):
        instr = (m.get("instr") or "").strip()
        if instr and "(P)" in instr:
            return re.sub(r"\s*\(P\)\s*$", "", instr).strip()
    for m in course.get("meetings", []):
        instr = (m.get("instr") or "").strip()
        if instr:
            return instr
    return ""
```

在 `enrich_meetings` 的循环体末尾（`c2["meetings"] = meetings` 之后）追加一行，让 `get_schedule` 返回的每个课程块自带该字段：

```python
        c2["primary_instructor"] = primary_instructor(c)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_banweb.py -v`
Expected: 全部 PASS（既有 enrich_meetings 测试只断言特定字段，附加字段不影响）

- [ ] **Step 5: 提交**

```bash
git add backend/banweb.py tests/test_banweb.py
git commit -m "feat: 课表主教授提取 (primary_instructor)"
```

---

### Task 5: 三个后端端点 + `sync_announcements` 补 `course_id`

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `canvas_client.get_course`、`canvas_client.get_assignments`、`llm_client.summarize_syllabus`（Task 1–3）。
- Produces:
  - `POST /api/course_detail` body `{canvas_url, canvas_token, course_id}` → `{"ok", "course": {id, name, syllabus_text, teachers}}`
  - `POST /api/assignments` body `{canvas_url, canvas_token, course_ids: list[int]}` → `{"ok", "by_course": {course_id: [assignments]}, "errors": {course_id: error}}`（单门失败进 `errors`，`by_course` 里该门为 `[]`，不拖垮整批）
  - `POST /api/summarize_syllabus` body `{canvas_url, canvas_token, llm_base_url, llm_api_key, llm_model, course_id, language}` → `{"ok", "summary"}`
  - `POST /api/sync_announcements` 每个结果补 `course_id` 字段。
  - 前端任务 6–8 消费这些端点。

- [ ] **Step 1: 写失败测试**

在 `tests/test_main.py` 的 `test_sync_announcements_passes_language` 之后追加：

```python
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


def test_sync_announcements_includes_course_id(monkeypatch):
    monkeypatch.setattr(canvas_client, "get_announcements",
                        lambda u, t, ids, a, b: {5: [{"title": "T", "message": "M", "posted_at": ""}]})
    monkeypatch.setattr(canvas_client, "list_courses", lambda u, t: [{"id": 5, "name": "CS 101"}])
    monkeypatch.setattr(llm_client, "extract_course_summary",
                        lambda *a, **k: {"course_name": "CS 101", "summary": "s",
                                         "calendar_events": [], "reminders": []})
    body = {"canvas_url": "https://x", "canvas_token": "t", "llm_base_url": "https://llm/v1",
            "llm_api_key": "k", "llm_model": "m", "course_ids": [5],
            "start_date": "2026-08-01", "end_date": "2026-08-31"}
    r = client.post("/api/sync_announcements", json=body)
    assert r.json()["courses"][0]["course_id"] == 5
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_main.py::test_course_detail_ok -v`
Expected: FAIL with 404（端点不存在）

- [ ] **Step 3: 实现**

在 `BanwebWriteRequest` 后新增三个 request model：

```python
class CourseDetailRequest(BaseModel):
    canvas_url: str
    canvas_token: str
    course_id: int


class AssignmentsRequest(BaseModel):
    canvas_url: str
    canvas_token: str
    course_ids: list[int]


class SummarizeSyllabusRequest(BaseModel):
    canvas_url: str
    canvas_token: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    course_id: int
    language: str = "zh"
```

在 `sync` 端点之后（`/api/add_calendar_event` 之前）新增三个端点：

```python
@app.post("/api/course_detail")
def course_detail(req: CourseDetailRequest):
    try:
        course = canvas_client.get_course(req.canvas_url, req.canvas_token, req.course_id)
        return {"ok": True, "course": course}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/assignments")
def assignments(req: AssignmentsRequest):
    """逐课程拉未截止作业；单门失败进 errors（by_course 里该门为 []），不拖垮整批。"""
    by_course: dict[int, list] = {}
    errors: dict[int, str] = {}
    for cid in req.course_ids:
        try:
            by_course[cid] = canvas_client.get_assignments(
                req.canvas_url, req.canvas_token, cid)
        except Exception as exc:
            by_course[cid] = []
            errors[cid] = str(exc)
    return {"ok": True, "by_course": by_course, "errors": errors}


@app.post("/api/summarize_syllabus")
def summarize_syllabus(req: SummarizeSyllabusRequest):
    try:
        course = canvas_client.get_course(req.canvas_url, req.canvas_token, req.course_id)
        summary = llm_client.summarize_syllabus(
            req.llm_base_url, req.llm_api_key, req.llm_model,
            course["name"], course["syllabus_text"], language=req.language)
        return {"ok": True, "summary": summary}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
```

修改 `sync` 端点（`/api/sync_announcements`）的循环，给每个结果补 `course_id`：

```python
    results = []
    for cid in req.course_ids:
        anns = announcements.get(cid, [])
        r = llm_client.extract_course_summary(
            req.llm_base_url, req.llm_api_key, req.llm_model,
            name_by_id.get(cid, f"Course {cid}"), anns, language=req.language)
        r["course_id"] = cid
        results.append(r)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_main.py -v`
Expected: 全部 PASS（既有端点测试不受影响）

- [ ] **Step 5: 提交**

```bash
git add backend/main.py tests/test_main.py
git commit -m "feat: 课程详情/作业/syllabus 总结 三个后端端点 + sync 补 course_id"
```

- [ ] **Step 6: 重启后端供前端联调**

Run: 重启 uvicorn（无 `--reload`）：
```bash
# 先 Ctrl-C 停掉旧进程，再：
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

---

## Phase 2 — 前端：课程详情弹层 + Syllabus AI 总结

> 前端任务无自动化测试，每个任务以「手动验证」收尾。验证前先确认后端已重启（Task 5 Step 6），并已「加载课程」（`btnLoadCourses`）。

### Task 6: 课程详情弹层 DOM + 打开/渲染逻辑

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/i18n.js`
- Modify: `frontend/app.js`
- Modify: `frontend/app.css`

**Interfaces:**
- Consumes: 后端 `POST /api/course_detail`（body `{canvas_url, canvas_token, course_id}` → `{ok, course}`）、`POST /api/assignments`（Task 5）；`settings()`、`api()`、`esc()`、`t()`、`courseList`、`banwebSchedule`（既有）。
- Produces:
  - DOM：`#detailModal`（含 `#detailTitle`、`#btnCloseDetail`、`#detailBody`）。
  - app.js 模块级：`assignmentMarks = {}`（`{course_id: [assignments]}`）、`detailCourse = null`、`detailAssignments = []`。
  - 函数：`ensureAssignments(courseIds) -> Promise<response|null>`（缺失才拉取、合并进 `assignmentMarks`）、`matchCourseByCode(code) -> course|null`、`openCourseDetail(courseId)`、`renderDetail()`、`closeDetail()`。
  - 入口：公告总结卡片课程名可点（需 `course_id`，来自 Task 5 sync 结果新增字段）；课表课程块右上角 `ⓘ` 详情按钮（需代码匹配到 Canvas 课程才显示）。
  - `applyLang()` 里补调 `renderDetail()`。
  - Task 8 的「加载作业 due」与标注复用 `assignmentMarks` / `ensureAssignments`。

- [ ] **Step 1: 新增 i18n key（zh + en 都要）**

在 `frontend/i18n.js` 的 `zh` 字典末尾（`"status.banweb_need_login_manual"` 后）追加：

```js
    "detail.title": "课程详情",
    "detail.close": "关闭",
    "detail.teachers": "教授",
    "detail.syllabus": "Syllabus",
    "detail.no_syllabus": "该课程暂无 syllabus",
    "detail.assignments": "作业",
    "detail.no_assignments": "暂无未截止作业",
    "detail.no_due": "无截止日期",
    "detail.open": "查看详情",
    "detail.load_fail": "加载课程详情失败：",
```

在 `en` 字典末尾（`"status.banweb_need_login_manual"` 后）追加：

```js
    "detail.title": "Course details",
    "detail.close": "Close",
    "detail.teachers": "Instructors",
    "detail.syllabus": "Syllabus",
    "detail.no_syllabus": "No syllabus for this course",
    "detail.assignments": "Assignments",
    "detail.no_assignments": "No upcoming assignments",
    "detail.no_due": "No due date",
    "detail.open": "View details",
    "detail.load_fail": "Failed to load course details: ",
```

- [ ] **Step 2: 新增详情弹层 DOM**

在 `frontend/index.html` 的 `#settingsModal` 之后、`#status` 之前插入：

```html
<div id="detailModal" class="modal" role="dialog" aria-modal="true" aria-labelledby="detailTitle" hidden>
  <div class="modal-backdrop"></div>
  <div class="modal-card modal-lg">
    <div class="modal-head">
      <h3 id="detailTitle" data-i18n="detail.title"></h3>
      <button id="btnCloseDetail" class="modal-close" data-i18n-aria="detail.close">✕</button>
    </div>
    <div id="detailBody"></div>
  </div>
</div>
```

- [ ] **Step 3: app.js 基础函数 + 弹层开关**

在 `frontend/app.js` 的「设置弹窗」区块后新增（放在 `const PALETTE` 之前即可）：

```js
/* 课程详情弹层 */
let assignmentMarks = {};        // {course_id: [未截止作业]}，周视图标注与详情共用
let detailCourse = null;         // {id, name, syllabus_text, teachers}
let detailAssignments = [];      // 当前打开课程的作业列表

function fmtDue(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return `${String(d.getMonth()+1).padStart(2,"0")}/${String(d.getDate()).padStart(2,"0")} ` +
         `${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`;
}
async function ensureAssignments(courseIds){
  const missing = courseIds.filter(id => !(id in assignmentMarks));
  if (!missing.length) return null;                       // 已加载 → 不动
  const s = settings();
  const r = await api("assignments", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                       course_ids:missing });
  if (r.ok !== true) throw new Error(r.error || t("status.assignments_fail"));
  Object.keys(r.by_course || {}).forEach(k => { assignmentMarks[Number(k)] = r.by_course[k] || []; });
  return r;                                               // 含 errors，供调用方提示
}
function matchCourseByCode(code){
  const norm = s => String(s).toUpperCase().replace(/\s+/g, "");
  const target = norm(code);
  if (!target) return null;
  return courseList.find(c => norm(c.name).includes(target)) || null;
}
function closeDetail(){ $("detailModal").hidden = true; }
$("btnCloseDetail").onclick = closeDetail;
$("detailModal").querySelector(".modal-backdrop").addEventListener("click", closeDetail);
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("detailModal").hidden) closeDetail(); });
```

- [ ] **Step 4: `openCourseDetail` + `renderDetail`**

继续在弹层区块后追加：

```js
async function openCourseDetail(courseId){
  const s = settings();
  const r = await api("course_detail", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                         course_id:courseId });
  if (r.ok !== true){ setStatus(t("detail.load_fail") + (r.error || ""), "err"); return; }
  detailCourse = r.course;
  try { await ensureAssignments([courseId]); }            // 详情作业区数据
  catch (e) { /* 详情仍展示，作业区留空 */ }
  detailAssignments = Array.isArray(assignmentMarks[courseId]) ? assignmentMarks[courseId] : [];
  renderDetail();
  $("detailModal").hidden = false;
}
function renderDetail(){
  if (!detailCourse) return;
  const c = detailCourse;
  const profs = (c.teachers || []).map(x => `<span class="chip">${esc(x)}</span>`).join("");
  const profLine = profs ? `<div class="detail-prof">${t("detail.teachers")}: ${profs}</div>` : "";
  const syl = c.syllabus_text
    ? `<div class="detail-section"><div class="sub-label">${t("detail.syllabus")}</div>
         <div class="detail-syllabus">${esc(c.syllabus_text)}</div>
         <button id="btnSummarize" class="btn btn-ghost">${t("detail.summarize")}</button></div>`
    : `<div class="detail-section"><div class="sub-label">${t("detail.syllabus")}</div>
         <div class="muted">${t("detail.no_syllabus")}</div></div>`;
  const asg = detailAssignments.length
    ? detailAssignments.map(a => `
        <a class="assignment-row" href="${esc(a.html_url || "")}" target="_blank" rel="noopener">
          <div class="item-title">${esc(a.name)}</div>
          <div class="file-path">${a.due_at
              ? t("announce.due") + " " + esc(fmtDue(a.due_at))
              : t("detail.no_due")}${a.points_possible != null ? ` · ${esc(String(a.points_possible))} pts` : ""}</div>
        </a>`).join("")
    : `<div class="muted">${t("detail.no_assignments")}</div>`;
  $("detailBody").innerHTML = `
    <div class="detail-head">${esc(c.name)}</div>
    ${profLine}
    ${syl}
    <div class="detail-section"><div class="sub-label">${t("detail.assignments")}</div>${asg}</div>`;
}
```

注：Task 7 会给 `renderDetail` 的 syllabus 区块追加「AI 总结」结果展示，本任务先只放按钮。

- [ ] **Step 5: 公告总结卡片课程名可点击**

修改 `renderSummaries()` 里的课程名渲染（把 `course-name` 那行改成可点链接，需 `course_id`）：

```js
      <div class="course-name">${c.course_id
          ? `<a href="#" class="course-detail-link" data-cid="${c.course_id}">${esc(c.course_name)}</a>`
          : esc(c.course_name)}</div>
```

并在 `$("btnWriteCalendar").onclick` 之前加一个委托点击：

```js
$("summaries").addEventListener("click", (e) => {
  const link = e.target.closest(".course-detail-link");
  if (!link) return;
  e.preventDefault();
  openCourseDetail(Number(link.dataset.cid));
});
```

- [ ] **Step 6: 课表课程块详情按钮**

修改 `renderSchedule()` 里生成课程块的循环：算出该块对应的 Canvas 课程，匹配到才加 `ⓘ` 按钮。在 `const res = banwebSchedule.results[key];` 之后加：

```js
    const canvas = matchCourseByCode(c.code);
    const detailBtn = canvas
      ? `<button class="cal-detail" data-cid="${canvas.id}"
           aria-label="${esc(t("detail.open"))}" title="${esc(t("detail.open"))}">ⓘ</button>`
      : "";
```

在块 HTML 的 `<div class="cal-block...">` 开头（第一个子 div 之前）插入 `${detailBtn}`：

```js
        colBlocks[idx] += `<div class="cal-block${selected ? " sel" : ""}" data-key="${esc(key)}"
          style="top:${top}px;height:${hgt}px;background:${color}">
          ${detailBtn}
          <div style="font-weight:600;color:#fff">${esc(c.code)} ${esc(c.section)}</div>
```

修改 `$("schedulePreview")` 的既有 click 委托（在 `const blk = e.target.closest(".cal-block");` 之前拦掉详情按钮，避免它触发勾选）：

```js
$("schedulePreview").addEventListener("click", (e) => {
  const detailBtn = e.target.closest(".cal-detail");
  if (detailBtn) {
    e.preventDefault(); e.stopPropagation();
    openCourseDetail(Number(detailBtn.dataset.cid));
    return;
  }
  const blk = e.target.closest(".cal-block");
  ...
```

- [ ] **Step 7: `applyLang()` 补渲详情弹层**

在 `frontend/i18n.js` 的 `applyLang()` 末尾（`refreshPill();` 之前）加一行：

```js
  if (typeof renderDetail === "function") renderDetail();   // 详情弹层语言切换后重渲
```

- [ ] **Step 8: CSS**

在 `frontend/app.css` 的「设置弹窗」区块后追加：

```css
/* 课程详情弹层 */
.modal-card.modal-lg { width:min(860px, calc(100vw - 32px)); }
.detail-head { font-size:16px; font-weight:700; margin-bottom:6px; }
.detail-prof { display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:12px;
  font-size:12.5px; color:var(--muted); }
.detail-prof .chip { cursor:default; }
.detail-section { margin-top:14px; }
.detail-syllabus { white-space:pre-wrap; font-size:13px; line-height:1.7; color:var(--ink);
  background:var(--surface-2); border:1px solid var(--border); border-radius:var(--radius-sm);
  padding:10px 12px; max-height:260px; overflow-y:auto; margin-bottom:10px; }
.assignment-row { display:block; padding:8px 10px; margin:4px 0; text-decoration:none; color:var(--ink);
  border:1px solid var(--border); border-left:4px solid var(--warn); border-radius:8px; }
.assignment-row:hover { background:var(--surface-2); }
.course-detail-link { color:var(--accent); text-decoration:none; }
.course-detail-link:hover { text-decoration:underline; }

/* 课表课程块内的详情按钮（右上角 ⓘ，不干扰块勾选） */
.cal-detail { position:absolute; top:2px; right:2px; z-index:2; width:18px; height:18px;
  padding:0; border:none; border-radius:50%; background:rgba(255,255,255,.28); color:#fff;
  font-size:11px; line-height:18px; text-align:center; cursor:pointer; }
.cal-detail:hover { background:rgba(255,255,255,.55); }
```

- [ ] **Step 9: 手动验证**

重启后端（Task 5 Step 6）后浏览器打开 `localhost:8000`，走通：
1. 设置里填 Canvas 配置 → 「测试连接」成功 → 「加载课程」→ 勾选课程。
2. 「同步并总结」→ 公告总结卡片出现；**点课程名** → 详情弹层打开，显示课程名、教授 chips、syllabus 纯文本、作业列表（无截止作业时显示「暂无未截止作业」）。
3. 课表 tab → 「抓取课表」→ 有固定时间的课程块右上角出现 `ⓘ`；**点 ⓘ** → 详情弹层打开；**点块本体**仍能勾选/取消（不冲突）。
4. 语言切 EN → 弹层重渲为英文。

Expected: 3 条路径全部可用，勾选逻辑不被破坏。

- [ ] **Step 10: 提交**

```bash
git add frontend/index.html frontend/i18n.js frontend/app.js frontend/app.css
git commit -m "feat: 课程详情弹层（syllabus/教授/作业）"
```

---

### Task 7: Syllabus AI 总结按钮

**Files:**
- Modify: `frontend/i18n.js`
- Modify: `frontend/app.js`

**Interfaces:**
- Consumes: `detailCourse`、`renderDetail()`（Task 6）；后端 `POST /api/summarize_syllabus`（body `{canvas_url, canvas_token, llm_base_url, llm_api_key, llm_model, course_id, language}` → `{ok, summary}`，Task 5）。
- Produces: `detailSummary`（模块级 string，`applyLang()` 重渲后仍保留）；`#btnSummarize` 点击行为：请求中按钮 loading 禁用，成功在 syllabus 下显示总结，失败显示错误。

- [ ] **Step 1: 新增 i18n key**

`zh` 字典追加：

```js
    "detail.summarize": "AI 总结",
    "detail.summarizing": "总结中…",
    "detail.summary_label": "AI 总结",
    "detail.summarize_fail": "总结失败：",
```

`en` 字典追加：

```js
    "detail.summarize": "AI summary",
    "detail.summarizing": "Summarizing…",
    "detail.summary_label": "AI summary",
    "detail.summarize_fail": "Summarize failed: ",
```

- [ ] **Step 2: app.js 状态与渲染**

在弹层区块顶部（`let detailAssignments = [];` 后）加：

```js
let detailSummary = "";          // 已生成的 AI 总结（切语言后仍显示）
```

修改 `renderDetail()` 的 syllabus 区块，让总结区从 `detailSummary` 渲染、并始终保留按钮：

```js
  const summaryHtml = detailSummary
    ? `<div class="detail-summary"><div class="sub-label">${t("detail.summary_label")}</div>
         ${esc(detailSummary)}</div>`
    : "";
  const syl = c.syllabus_text
    ? `<div class="detail-section"><div class="sub-label">${t("detail.syllabus")}</div>
         <div class="detail-syllabus">${esc(c.syllabus_text)}</div>
         <button id="btnSummarize" class="btn btn-ghost">${t("detail.summarize")}</button>
         ${summaryHtml}</div>`
    : `<div class="detail-section"><div class="sub-label">${t("detail.syllabus")}</div>
         <div class="muted">${t("detail.no_syllabus")}</div></div>`;
```

修改 `openCourseDetail()` 开头，打开新课程时清掉旧总结：

```js
  detailCourse = r.course;
  detailSummary = "";
```

- [ ] **Step 3: 按钮点击委托**

在 `$("summaries").addEventListener(...)`（Task 6 Step 5）之后追加：

```js
$("detailBody").addEventListener("click", async (e) => {
  const btn = e.target.closest("#btnSummarize");
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = t("detail.summarizing");
  const s = settings();
  try {
    const r = await api("summarize_syllabus", {
      canvas_url:s.canvas_url, canvas_token:s.canvas_token,
      llm_base_url:s.llm_base_url, llm_api_key:s.llm_api_key, llm_model:s.llm_model,
      course_id:detailCourse.id, language:LANG() });
    if (r.ok !== true) setStatus(t("detail.summarize_fail") + (r.error || ""), "err");
    else { detailSummary = r.summary; renderDetail(); }
  } catch (err) {
    setStatus(t("detail.summarize_fail") + (err.message || ""), "err");
  } finally {
    const b2 = $("btnSummarize");                       // 成功后已重渲，按钮是新元素
    if (b2) { b2.disabled = false; b2.textContent = t("detail.summarize"); }
  }
});
```

- [ ] **Step 4: 手动验证**

1. 打开任一课程详情 → 有 syllabus 的课程出现「AI 总结」按钮。
2. 点「AI 总结」→ 按钮变「总结中…」并禁用 → 完成后 syllabus 下方出现绿色总结块。
3. 再点一次（换语言后）→ 总结按当前界面语言输出（`language:LANG()` 透传）。
4. 把 LLM Base URL 填错 → 点总结 → 顶部出现「总结失败：…」，按钮恢复可用。

Expected: 成功/失败两条路径都正确，弹层不崩。

- [ ] **Step 5: 提交**

```bash
git add frontend/i18n.js frontend/app.js
git commit -m "feat: syllabus AI 总结按钮"
```

---

## Phase 3 — 前端：周视图作业 due 琥珀标注

### Task 8: 作业 due 标注

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/i18n.js`
- Modify: `frontend/app.js`
- Modify: `frontend/app.css`

**Interfaces:**
- Consumes: `assignmentMarks`、`ensureAssignments(courseIds)`、`selectedCourses()`、`fmtDue()`（Task 6）；后端 `POST /api/assignments`（Task 5）；`banwebSchedule.courses`。
- Produces: 课表 tab filter-bar 里 `#btnLoadAssignments`（加载作业 due）；`renderSchedule()` 现在对每个未截止且 `due_at` 非空的作业在对应星期列顶部渲染琥珀色 `assignment-mark` 链接（`target="_blank"` → Canvas 提交页）；`.assign-strip` 容器。标注不参与勾选/写日历逻辑。

- [ ] **Step 1: 新增 i18n key**

`zh`：

```js
    "btn.load_assignments": "加载作业 due",
    "status.loading_assignments": "正在加载作业 due…",
    "status.assignments_fail": "加载作业失败：",
    "status.assignments_loaded": "已加载作业 due：{n} 门课程",
```

`en`：

```js
    "btn.load_assignments": "Load due dates",
    "status.loading_assignments": "Loading due dates…",
    "status.assignments_fail": "Failed to load assignments: ",
    "status.assignments_loaded": "Loaded due dates for {n} courses",
```

- [ ] **Step 2: index.html 加 filter-bar**

在 `#tabSchedule` 的抓取行（`btnFetchSchedule` 那行 `</div>`）之后、`#schedulePreview` 之前插入：

```html
      <div class="filter-bar" id="scheduleFilters" style="margin-top:-4px">
        <button id="btnLoadAssignments" class="btn btn-ghost" data-i18n="btn.load_assignments"></button>
      </div>
```

- [ ] **Step 3: app.js 加载按钮**

在 `$("btnFetchSchedule").onclick` 之后追加：

```js
$("btnLoadAssignments").onclick = async () => {
  const ids = selectedCourses();
  if (!ids.length){ setStatus(t("status.need_course"), "err"); return; }
  await withBusy(t("status.loading_assignments"), $("btnLoadAssignments"), async ()=>{
    let r;
    try { r = await ensureAssignments(ids); }
    catch (err){ setStatus(t("status.assignments_fail") + (err.message || ""), "err"); return; }
    renderSchedule();
    const errCount = Object.keys((r && r.errors) || {}).length;
    if (errCount)
      setStatus(t("status.assignments_loaded", {n: ids.length - errCount}) +
                " · " + t("status.assignments_fail") + errCount, "err");
    else
      setStatus(t("status.assignments_loaded", {n: ids.length}), "ok");
  });
};
```

- [ ] **Step 4: `renderSchedule()` 渲染琥珀标注**

修改 `renderSchedule()`：

1. 在 `const colBlocks = Array.from({length:7}, () => "");` 后加 `const assignBlocks = Array.from({length:7}, () => "");`，并建 `courseNameById` 映射。
2. 在课程块循环**之后**、时间轴拼接**之前**，把 `assignmentMarks` 里每个有 `due_at` 的作业落到对应星期列：

```js
  // 作业 due 标注：按 due_at 的星期几落列，整块是 <a target="_blank"> 指向 Canvas 提交页
  const courseNameById = {};
  courseList.forEach(c => { courseNameById[c.id] = c.name; });
  Object.entries(assignmentMarks).forEach(([cidStr, list]) => {
    const cid = Number(cidStr);
    if (!Array.isArray(list)) return;
    const cname = courseNameById[cid] || `Course ${cid}`;
    list.forEach(a => {
      if (!a.due_at) return;                 // 无截止日期 → 不上日历
      const due = new Date(a.due_at);
      if (isNaN(due)) return;
      const idx = (due.getDay() + 6) % 7;    // JS 周日=0 → 转 周一=0
      assignBlocks[idx] += `<a class="assignment-mark" href="${esc(a.html_url || "")}"
        target="_blank" rel="noopener"
        title="${esc(a.name)} — ${t("announce.due")} ${fmtDue(a.due_at)}">
        <span class="mark-course">${esc(cname)}</span> · <span class="mark-name">${esc(a.name)}</span>
        <span class="mark-due">${esc(fmtDue(a.due_at))}</span></a>`;
    });
  });
```

3. 修改列容器拼接，把 `assignBlocks[d]` 插到 `day-body` 之前（`day-head` 与 `day-body` 之间），避免与时间定位的课程块重叠：

```js
  for (let d = 0; d < 7; d++) {
    cols += `<div class="day-col"><div class="day-head">${t("wd."+d)}</div>
      <div class="assign-strip">${assignBlocks[d]}</div>
      <div class="day-body" style="height:${rows * HOUR_PX}px">${colBlocks[d]}</div></div>`;
  }
```

注：规格里「在 day-body 顶部」按列顶条（`day-head` 与 `day-body` 之间）实现，避免琥珀条盖住 8:00 的课程块。

- [ ] **Step 5: CSS**

在 `app.css` 的「课表课程块内的详情按钮」之后追加：

```css
/* 周视图作业 due 琥珀标注条 */
.assign-strip { display:flex; flex-direction:column; gap:3px; padding:4px;
  background:var(--warn-bg); border-bottom:1px solid var(--warn-border); }
.assign-strip:empty { display:none; }
.assignment-mark { display:block; padding:2px 6px; border-left:3px solid var(--warn);
  background:var(--surface); border-radius:4px; font-size:10.5px; line-height:1.35;
  color:var(--warn); text-decoration:none; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; }
.assignment-mark:hover { background:var(--warn-bg); }
.assignment-mark .mark-course { font-weight:700; }
.assignment-mark .mark-due { opacity:.8; }
```

- [ ] **Step 6: 手动验证**

1. 加载课程（勾选若干）→ 课表 tab 抓取课表 → 点「加载作业 due」→ 状态提示已加载。
2. 有 due 的星期列顶部出现**琥珀色横条**（课程名 · 作业名 · 截止时间），与实心课程块样式明显不同；无作业的列该条隐藏。
3. 点琥珀条 → 新标签页打开 Canvas 作业页（浏览器里登录提交）。
4. 点课程块本体 → 仍是勾选（琥珀条不与勾选冲突）。
5. 未加载课程时点「加载作业 due」→ 提示「请先勾选课程」。

Expected: 标注出现、样式区分、跳转可用、勾选不被破坏。

- [ ] **Step 7: 提交**

```bash
git add frontend/index.html frontend/i18n.js frontend/app.js frontend/app.css
git commit -m "feat: 周视图作业 due 琥珀色标注"
```

---

## Phase 4 — 前端：按教授筛选课表

### Task 9: 教授筛选下拉

**Files:**
- Modify: `frontend/i18n.js`
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/app.css`

**Interfaces:**
- Consumes: `banwebSchedule.courses[].primary_instructor`（后端 Task 4 附加）；`renderSchedule()`、`banwebSchedule`（既有）。
- Produces: `#selProfessor` 下拉 + `fillProfessorFilter()`；`renderSchedule()` 按所选教授过滤可见课程块（网格 + 无固定时间区），不改变 `selected` 与 localStorage；作业标注不受教授筛选影响（标注是 Canvas 侧数据）。

- [ ] **Step 1: 新增 i18n key**

`zh`：

```js
    "schedule.professor": "按教授筛选",
    "schedule.prof_all": "全部教授",
    "schedule.prof_unspecified": "未指定",
```

`en`：

```js
    "schedule.professor": "Filter by instructor",
    "schedule.prof_all": "All instructors",
    "schedule.prof_unspecified": "Unspecified",
```

- [ ] **Step 2: index.html 加下拉**

把 Task 8 加的 `#scheduleFilters` 行扩展（在 `btnLoadAssignments` 后加一个 select）：

```html
      <div class="filter-bar" id="scheduleFilters" style="margin-top:-4px">
        <button id="btnLoadAssignments" class="btn btn-ghost" data-i18n="btn.load_assignments"></button>
        <label class="field"><span data-i18n="schedule.professor"></span><select id="selProfessor"></select></label>
      </div>
```

- [ ] **Step 3: app.js 填充下拉 + 过滤渲染**

在 `fillCourseFilter` 附近（`esc` 定义之后）新增：

```js
function fillProfessorFilter(){
  const sel = $("selProfessor");
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML = "";
  const all = document.createElement("option");
  all.value = ""; all.textContent = t("schedule.prof_all"); sel.appendChild(all);
  const profs = [...new Set((banwebSchedule.courses || [])
    .map(c => (c.primary_instructor || "").trim()).filter(Boolean))];
  profs.forEach(p => { const o = document.createElement("option"); o.value = o.textContent = p; sel.appendChild(o); });
  const un = document.createElement("option");
  un.value = "__none__"; un.textContent = t("schedule.prof_unspecified"); sel.appendChild(un);
  sel.value = prev && [...sel.options].some(o => o.value === prev) ? prev : "";
}
```

修改 `renderSchedule()`：
1. 在 `const { lo, hi } = gridMinutes();` 之后、时间轴/列拼接之前，加过滤 + 刷新下拉：

```js
  fillProfessorFilter();
  const profFilter = $("selProfessor") ? $("selProfessor").value : "";
  const visible = profFilter
    ? data.courses.filter(c => {
        const p = (c.primary_instructor || "").trim();
        return profFilter === "__none__" ? p === "" : p === profFilter;
      })
    : data.courses;
```

2. 把 `for (const c of data.courses) {` 改为 `for (const c of visible) {`（`noFixed` 也自然从过滤后的集合来）。

- [ ] **Step 4: CSS**

`#scheduleFilters` 已是 `.filter-bar` 样式，无需新增；如需下拉与按钮对齐，可在 `app.css` 追加一行（可选）：

```css
#scheduleFilters .field { min-width:150px; }
```

- [ ] **Step 5: 手动验证**

1. 抓取课表后，filter-bar 出现「按教授筛选」下拉，选项 = 课表里 `primary_instructor` 去重 + 「全部教授」+「未指定」。
2. 选某教授 → 只渲染该教授的课程块（网格 + 无固定时间区）；选「未指定」→ 只显示无讲师的课；选「全部教授」→ 全部恢复。
3. 勾选状态（`.sel` 边框）与 localStorage 里的 `selected` 不被筛选改变；写日历按钮仍按原 `selected` 工作。
4. 琥珀作业标注不受教授筛选影响（还在原位）。
5. 语言切 EN → 下拉选项文案变英文，选中值保留。

Expected: 过滤只影响显示，不影响勾选与作业标注。

- [ ] **Step 6: 提交**

```bash
git add frontend/index.html frontend/i18n.js frontend/app.js frontend/app.css
git commit -m "feat: 课表按教授筛选"
```

---

## 收尾

- [ ] **Step 1: 全量回归**

Run: `pytest tests/ -q`
Expected: 全部 PASS。

- [ ] **Step 2: 手动全流程回归**

重启后端，浏览器走通：
1. 公告总结 → 点课程名开详情 → AI 总结。
2. 课表 → 抓取 → 点 ⓘ 开详情 → 按教授筛选 → 加载作业 due → 琥珀条出现 → 点条开 Canvas 提交页。
3. 切中/EN 全程文案正确、已打开的弹层与课表重渲。

- [ ] **Step 3: 提交收尾（如有遗漏改动）**

```bash
git status --short   # 确认无遗留
git add -A && git commit -m "chore: 课程详情/作业/教授筛选 收尾"
```

---

## Self-Review（对照规格）

1. **规格覆盖**：
   - 详情弹层（公告卡片 + 课表块入口）→ Task 6 ✓
   - syllabus AI 总结（中/英、按钮 loading、失败提示）→ Task 7 ✓
   - 作业 due 琥珀标注（明显区分、可点击跳 Canvas 提交）→ Task 8 ✓
   - 教授筛选下拉 → Task 9 ✓
   - 后端 `get_course`/`get_assignments`/`summarize_syllabus`/`primary_instructor` + 3 端点 + sync `course_id` → Task 1–5 ✓
   - i18n 全部进字典、`applyLang` 重渲弹层与课表 → Task 6–9 ✓
   - 测试：后端 4 文件单测 + 端点测试 → Task 1–5 ✓；前端手动验证 → Task 6–9 收尾步骤 ✓
2. **占位符扫描**：所有代码步骤都有完整实现，无 TBD/TODO。
3. **类型一致性**：`get_course` → `{id,name,syllabus_text,teachers}`；`get_assignments` → `[{id,name,due_at,points_possible,html_url}]`；`summarize_syllabus(base,key,model,course_name,syllabus_text,language)` → `str`；`primary_instructor(course)` → `str`；前端 `ensureAssignments`/`assignmentMarks`/`matchCourseByCode`/`openCourseDetail`/`renderDetail`/`fillProfessorFilter`/`fmtDue` 前后引用一致。
