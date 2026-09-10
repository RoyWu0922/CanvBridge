# Canvas 内容扩展 + 首页仪表盘 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通 Canvas 的 quizzes / pages / discussion_topics / planner 四个新数据源（后端 + 测试 + 前端落位），把首页升级为仪表盘网格，增强玻璃质感，并删掉侧栏 8 个 emoji 图标。

**Architecture:** 后端在 `canvas_client.py` 加 5 个纯读函数（字段表已按 2026-09-10 真实实例实测校准，见 spec §5.0），在 `main.py` 加 5 个端点，批量端点沿用现有 `by_course` + `errors` 逐课程隔离约定。前端不新增文件，四个经典脚本（`i18n.js → util.js → app.js → shell.js`）共享全局词法环境；新数据源按信息架构落位：Pages 进课程详情弹层、测验进课表页考试区、Planner 只做首页「本周 DDL」的数据源、讨论区是唯一新增页面。

**Tech Stack:** Python 3 / FastAPI / Pydantic / requests / pytest；原生 JS（无构建步骤、无 ES 模块）+ 纯 CSS token 体系。

**Spec:** `docs/superpowers/specs/2026-09-10-canvas-content-expansion-design.md`

## Global Constraints

以下约束适用于本计划的**每一个任务**，逐条抄自 spec，不是建议：

- **不引入构建步骤**（无 bundler、无 npm、无 ES 模块），保持「浏览器直接跑源码」形态（spec §3）。
- **不新增 js 文件。** 四个脚本的加载顺序 `i18n.js → util.js → app.js → shell.js` 是硬约束，全部置于 `<body>` 末尾（spec §6.1）；它们共享同一个全局词法环境，函数声明跨文件可见。
- **新增顶层名必须在四个 js 文件中唯一。** 重复的顶层 `const`/`let` 会以 `SyntaxError: Identifier 'x' has already been declared` 杀死整页，表现为「好几个不相关的功能全坏了」。
- **`$()` 只接裸 id，`$$()` 只接选择器。** `$` 的定义是 `document.getElementById`，`$("#foo")` 静默返回 `null`。上一轮的 Critical 就出在这里。
- **所有新增文案必须同时补 `zh` 与 `en`**，两块键集完全一致且无空值（spec §6.8）。
- **禁止在 JS 里硬编码面向用户的字符串**（含 `t()` 之外的英文常量）。动态插入的节点用内联 `t()`，不挂 `data-i18n`（spec §6.8）。
- **后端不抛 `HTTPException`**，一律 `try/except` 返回 `{"ok": False, "error": str(exc)}`；批量端点用 `by_course` + `errors`（spec §5.2）。
- **颜色沿用现有 token 名，只改值，不新增 token**（spec §6.6）。
- **浅色主题下玻璃面板不得变透明或隐形**（spec §6.6）。
- **只读**：不做作业提交、不做讨论回复（spec §3）。
- **`pytest` 全绿 + `node tools/check_dom_ids.mjs` 通过** 是每个任务的收尾门。

### 计划级裁定（与 spec 文字的偏差，已记录）

| # | 裁定 | 理由 | 若判错 |
|---|---|---|---|
| R1 | 「课程未启用该工具」的 404 由 `canvas_client` 抛**专用异常 `CanvasToolDisabled`**，端点捕获后**只置 `by_course[cid] = []`、不写进 `errors`**。 | spec §8.1 要求「404 抛错」（客户端确实抛了），§7 要求「静默跳过、不算错误行」。若照 §8.1 括号里「由端点层转成 `errors`」的字面去做，前端就必须对错误**字符串**做模式匹配才能区分「工具没开」和「真故障」——Canvas 换个语言或换个措辞就失效。把知识留在做实测的客户端里，前端零特判。 | 前端在「该课没开测验」时可能显示一行错误。改回来只需在端点里把 `except CanvasToolDisabled` 分支合并进 `except Exception`。 |
| R2 | `@media (max-width:900px)` 里的 `.sidebar{width:64px}` 与 `.nav-item span:not(.nav-ico){display:none}` **整条删除**，窄窗下侧栏保持 200px 文字形态。 | spec §6.7 只说要删图标。那条规则的**唯一作用**是「侧栏收成 64px 图标条时藏掉文字」；图标一旦删掉，窄窗下 8 个按钮就全空了。图标条这个概念随之失效。`run_app.py` 的 `min_size=(980,680)` 让桌面形态下这个断点不可达，但 `--browser` 模式可达。 | 窄窗下侧栏偏宽 200px，内容区仍可用；不会出现空白侧栏。 |
| R3 | `get_planner_items` 的 `html_url` **只取顶层 `item["html_url"]`** 并在以 `/` 开头时用 `canvas_url` 补成绝对 URL，不回落 `plannable.html_url`。 | spec §5.1 明确「`html_url` 是相对路径」且契约在顶层（与 `id` 取顶层 `plannable_id` 同理）。 | 个别条目链接为空，前端 `href=""` 不可点，不影响计数与展示。 |
| R4 | `PlannerRequest` 是**第三个新 Pydantic 模型**（`CanvasConfig` + `start_date` + `end_date`）。 | spec §5.2 只点名了 `CoursesRequest` / `PageBodyRequest` 两个，但 planner 的请求体按 §5.2 表格**不含 `course_ids`**，现有 `CalendarEventsRequest` 的 `course_ids` 是必填，不能复用。约束是「复用 `CanvasConfig` 基类」，本模型照做。 | 无——多一个三字段模型不影响任何既有端点。 |
| R5 | `get_pages` 增加一级排序：`front_page` 优先，其次 `title` 升序。 | spec §5.1 未规定 pages 的排序。首页（front page）是课程的入口页，置顶是唯一有明显价值的顺序；不定顺序则每次刷新排列可能变化。 | 列表顺序不合口味，改一行 `sort` 的 key 即可。 |

---

## 文件结构

| 文件 | 本轮职责 | 改动量 |
|---|---|---|
| `backend/canvas_client.py` | 5 个新数据源函数 + 1 个专用异常 + 1 个 404 容忍的翻页包装 | +约 150 行 |
| `backend/main.py` | 3 个 Pydantic 模型 + 5 个端点 | +约 60 行 |
| `frontend/index.html` | 侧栏去图标；加 `#page-discuss`；首页改仪表盘网格；课表页加测验挂载点 | 局部重写首页段 |
| `frontend/app.js` | 讨论页 / Pages 板块 / 测验分组三块业务；成绩与详情状态复用 | +约 200 行 |
| `frontend/shell.js` | `PAGES` / `PAGE_INIT` / `refreshBadges` 三处改动 + 首页仪表盘渲染 | +约 70 行 |
| `frontend/i18n.js` | 28 个新键 × 2 语言 | +56 行 |
| `frontend/app.css` | 删 `.nav-ico`、修媒体查询、仪表盘网格、测验配色、玻璃 token 改值 | +约 60 行 |
| `tests/test_canvas_client.py` | 5 个函数逐条覆盖实测边界 | +约 200 行 |
| `tests/test_main.py` | 5 个端点的成功/失败/逐课程隔离 | +约 120 行 |

**不新增 js 文件**（spec §6.1）。`CanvBridge.spec` 整目录打包 `frontend/`，无需修改。

---

## Task 1: `canvas_client.py` — 404 容忍包装 + 三个直读字段的函数

**Files:**
- Modify: `backend/canvas_client.py`（文件顶部 import 区、`strip_html` 之后）
- Test: `tests/test_canvas_client.py`

**Interfaces:**
- Consumes: `canvas_client._paginate(session, url, params, token)`（现有，401/403 抛 `CanvasError`，其余 `raise_for_status()`）；`canvas_client.strip_html(html) -> str`（现有）；`canvas_client._headers(token)`（现有）
- Produces:
  - `class CanvasToolDisabled(CanvasError)` — 课程未启用该工具（HTTP 404）
  - `_paginate_allow_disabled(session, url, params, token) -> list[dict]` — 内部用，404 转 `CanvasToolDisabled`
  - `get_quizzes(canvas_url: str, token: str, course_id: int) -> list[dict]`
  - `get_pages(canvas_url: str, token: str, course_id: int) -> list[dict]`
  - `get_page_body(canvas_url: str, token: str, course_id: int, page_url: str) -> dict`

- [ ] **Step 1: 在文件顶部 import 区加 `quote`**

`backend/canvas_client.py` 第 6 行的 import 区改为：

```python
import os
import re
from datetime import datetime, timezone  # 放到文件顶部现有 import 区
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
```

- [ ] **Step 2: 写失败测试**

追加到 `tests/test_canvas_client.py` 末尾：

```python
# ===== 新数据源：quizzes / pages / page_body =====

def test_get_quizzes_maps_and_sorts(monkeypatch):
    s = _Session([([[
        {"id": 2, "title": "小测二", "due_at": None, "lock_at": None,
         "points_possible": 10, "quiz_type": "practice_quiz",
         "time_limit": None, "question_count": 5,
         "html_url": "https://x/courses/1/quizzes/2", "published": True},
        {"id": 1, "title": "小测一", "due_at": "2026-09-20T15:59:00Z",
         "lock_at": "2026-09-21T15:59:00Z", "points_possible": 100,
         "quiz_type": "assignment", "time_limit": 60, "question_count": 20,
         "html_url": "https://x/courses/1/quizzes/1", "published": True},
    ]], "")])
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
    # 请求参数
    assert s.calls[0][0] == "https://x/api/v1/courses/1/quizzes"
    assert s.calls[0][1] == {"per_page": 100}


def test_get_quizzes_filters_unpublished(monkeypatch):
    s = _Session([([[
        {"id": 1, "title": "已发布", "published": True},
        {"id": 2, "title": "未发布", "published": False},
        {"id": 3, "title": "缺字段"},
    ]], "")])
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
    s = _Session([([[
        {"url": "syllabus", "title": "Syllabus", "updated_at": "2026-09-01T00:00:00Z",
         "published": True, "front_page": False, "html_url": "https://x/courses/1/pages/syllabus"},
        {"url": "home", "title": "Course Home", "updated_at": "2026-09-02T00:00:00Z",
         "published": True, "front_page": True, "html_url": "https://x/courses/1/pages/home"},
        {"url": "draft", "title": "Draft", "published": False, "front_page": False},
    ]], "")])
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
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python -m pytest tests/test_canvas_client.py -k "quizzes or pages or page_body or allow_disabled" -v`
Expected: FAIL — `AttributeError: module 'backend.canvas_client' has no attribute 'get_quizzes'`（及其余同类）

- [ ] **Step 4: 写实现**

在 `backend/canvas_client.py` 的 `strip_html` 定义之后插入：

```python
class CanvasToolDisabled(CanvasError):
    """课程未启用该工具 → Canvas 返回 404 "That page has been disabled for this
    course"。实测 6 门课里 3 门如此，**这是常态而非异常**，调用方应处理成
    「该课没有这类内容」，不带错误提示。"""


def _paginate_allow_disabled(session: requests.Session, url: str,
                             params: dict[str, Any], token: str) -> list[dict]:
    """同 _paginate，但把「课程未启用该工具」的 404 单独抛成 CanvasToolDisabled。
    其余一切（401/403 的 CanvasError、其它状态的 HTTPError）原样向上抛。"""
    try:
        return _paginate(session, url, params, token)
    except requests.HTTPError as exc:
        resp = getattr(exc, "response", None)
        if resp is not None and resp.status_code == 404:
            raise CanvasToolDisabled(f"课程未启用该工具 (HTTP 404): {url}") from exc
        raise


def get_quizzes(canvas_url: str, token: str, course_id: int) -> list[dict]:
    """课程测验。未启用测验工具的课程会抛 CanvasToolDisabled。

    注意：Canvas 的 quiz **不在 assignments 端点里**，走独立端点。
    """
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        data = _paginate_allow_disabled(
            s, f"{base}/api/v1/courses/{course_id}/quizzes", {"per_page": 100}, token)
    out = []
    for q in data:
        if not q.get("published"):
            continue
        out.append({
            "id": q.get("id"),
            "title": q.get("title") or "",
            "due_at": q.get("due_at") or "",          # 实测无值时是 null
            "lock_at": q.get("lock_at") or "",
            "points_possible": q.get("points_possible"),
            "quiz_type": q.get("quiz_type") or "",
            "time_limit": q.get("time_limit"),
            "question_count": q.get("question_count"),
            "html_url": q.get("html_url") or "",
        })
    out.sort(key=lambda x: (x["due_at"] == "", x["due_at"]))   # 无截止排最后
    return out


def get_pages(canvas_url: str, token: str, course_id: int) -> list[dict]:
    """课程 Pages **列表**（不含正文）。

    正文由 get_page_body 按需单独取 —— 列表阶段拉全部正文是 O(N) 次额外请求。
    未启用 Pages 工具的课程会抛 CanvasToolDisabled。
    """
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        data = _paginate_allow_disabled(
            s, f"{base}/api/v1/courses/{course_id}/pages", {"per_page": 100}, token)
    out = []
    for p in data:
        if not p.get("published"):
            continue
        out.append({
            "url": p.get("url") or "",
            "title": p.get("title") or "",
            "updated_at": p.get("updated_at") or "",
            "published": True,
            "front_page": bool(p.get("front_page")),
            "html_url": p.get("html_url") or "",
        })
    out.sort(key=lambda x: (not x["front_page"], x["title"]))   # 课程首页置顶
    return out


def get_page_body(canvas_url: str, token: str, course_id: int,
                  page_url: str) -> dict:
    """单个 Page 的正文。

    原始 body **实测是 HTML 片段** → 必须过 strip_html 转纯文本；前端插入时再过
    esc() 防注入。两道处理各司其职，都不能省。
    """
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        resp = s.get(f"{base}/api/v1/courses/{course_id}/pages/{quote(page_url, safe='')}",
                     headers=_headers(token), timeout=30)
        if resp.status_code == 401:
            raise CanvasError("Canvas token 无效或已过期 (HTTP 401)")
        if resp.status_code == 403:
            raise CanvasError("没有权限访问该资源 (HTTP 403)")
        resp.raise_for_status()
        p = resp.json()
    return {
        "url": p.get("url") or page_url,
        "title": p.get("title") or "",
        "body_text": strip_html(p.get("body") or ""),
        "updated_at": p.get("updated_at") or "",
        "html_url": p.get("html_url") or "",      # 此端点实测是绝对 URL
    }
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_canvas_client.py -v`
Expected: PASS（新增 10 条 + 既有全部）

- [ ] **Step 6: 提交**

```bash
git add backend/canvas_client.py tests/test_canvas_client.py
git commit -m "feat(backend): canvas_client 加 quizzes/pages/page_body + 工具未启用 404 专用异常"
```

---

## Task 2: `canvas_client.py` — 讨论区与 Planner 归一化

**Files:**
- Modify: `backend/canvas_client.py`（接在 Task 1 的 `get_page_body` 之后）
- Test: `tests/test_canvas_client.py`

**Interfaces:**
- Consumes: `_paginate_allow_disabled`、`CanvasToolDisabled`、`_paginate`（Task 1 与既有）
- Produces:
  - `get_discussion_topics(canvas_url: str, token: str, course_id: int) -> list[dict]`
  - `get_planner_items(canvas_url: str, token: str, start_date: str, end_date: str) -> list[dict]`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_canvas_client.py` 末尾：

```python
# ===== 新数据源：discussion_topics / planner =====

def test_get_discussions_maps_subentry_count(monkeypatch):
    """实测：回复数字段是 discussion_subentry_count，不是 replies_count。"""
    s = _Session([([[
        {"id": 7, "title": "第一次讨论", "posted_at": "2026-09-05T00:00:00Z",
         "last_reply_at": "2026-09-06T00:00:00Z",
         "author": {"display_name": "张三"},
         "discussion_subentry_count": 12, "unread_count": 0,
         "read_state": "read", "pinned": False, "locked": False,
         "require_initial_post": True, "html_url": "https://x/t/7",
         "is_announcement": False},
    ]], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_discussion_topics("https://x", "tok", 1)
    assert out[0]["replies_count"] == 12
    assert out[0]["author"] == "张三"
    assert out[0]["require_initial_post"] is True
    assert s.calls[0][1] == {"per_page": 100, "order_by": "recent_activity"}


def test_get_discussions_author_empty_array(monkeypatch):
    """实测 71134 的 author 是空数组 []。直接下标会 TypeError 打死整批。"""
    s = _Session([([[
        {"id": 1, "title": "无作者", "author": [], "discussion_subentry_count": 0},
    ]], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_discussion_topics("https://x", "tok", 1)
    assert out[0]["author"] == ""


def test_get_discussions_author_display_name_none(monkeypatch):
    """实测 display_name 可能是 null → or ""。"""
    s = _Session([([[
        {"id": 1, "title": "空名", "author": {"display_name": None},
         "discussion_subentry_count": 3},
    ]], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_discussion_topics("https://x", "tok", 1)
    assert out[0]["author"] == ""


def test_get_discussions_read_state_and_unread_count_are_independent(monkeypatch):
    """实测：read_state 是根帖已读状态，unread_count 是未读回复数，两者独立。
    同一门课里既有 read+8 也有 unread+0。两个都要原样透传。"""
    s = _Session([([[
        {"id": 1, "title": "根帖已读但 8 条回复没读",
         "read_state": "read", "unread_count": 8,
         "discussion_subentry_count": 8, "last_reply_at": "2026-09-08T00:00:00Z"},
        {"id": 2, "title": "根帖没读但无人回复",
         "read_state": "unread", "unread_count": 0,
         "discussion_subentry_count": 0, "last_reply_at": "2026-09-07T00:00:00Z"},
    ]], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = {t["id"]: t for t in canvas_client.get_discussion_topics("https://x", "tok", 1)}
    assert (out[1]["read_state"], out[1]["unread_count"]) == ("read", 8)
    assert (out[2]["read_state"], out[2]["unread_count"]) == ("unread", 0)


def test_get_discussions_missing_fields_default(monkeypatch):
    s = _Session([([[
        {"id": 1, "title": "缺字段", "discussion_subentry_count": 0},
    ]], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_discussion_topics("https://x", "tok", 1)
    assert out[0]["read_state"] == "read"     # 缺失按已读
    assert out[0]["unread_count"] == 0
    assert out[0]["posted_at"] == ""
    assert out[0]["last_reply_at"] == ""


def test_get_discussions_filters_announcements(monkeypatch):
    s = _Session([([[
        {"id": 1, "title": "真讨论", "is_announcement": False,
         "discussion_subentry_count": 1},
        {"id": 2, "title": "其实是公告", "is_announcement": True,
         "discussion_subentry_count": 1},
    ]], "")])
    monkeypatch.setattr(requests, "Session", lambda: s)
    out = canvas_client.get_discussion_topics("https://x", "tok", 1)
    assert [t["id"] for t in out] == [1]


def test_get_discussions_sort_pinned_then_activity(monkeypatch):
    """pinned 优先；其次 last_reply_at 降序，为 None 退到 posted_at；两者都 None 排最后。"""
    s = _Session([([[
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
    ]], "")])
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
    ]], "")])
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_canvas_client.py -k "discussions or planner" -v`
Expected: FAIL — `AttributeError: module 'backend.canvas_client' has no attribute 'get_discussion_topics'`

- [ ] **Step 3: 写实现**

接在 Task 1 的 `get_page_body` 之后插入：

```python
def get_discussion_topics(canvas_url: str, token: str, course_id: int) -> list[dict]:
    """课程讨论区帖子。未启用讨论工具的课程会抛 CanvasToolDisabled。"""
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        data = _paginate_allow_disabled(
            s, f"{base}/api/v1/courses/{course_id}/discussion_topics",
            {"per_page": 100, "order_by": "recent_activity"}, token)
    out = []
    for tp in data:
        if tp.get("is_announcement"):
            continue                       # 公告有独立页面与接口，不混进讨论页
        author = tp.get("author")
        # 实测 author 可能是空数组 []，直接下标会 TypeError 打死整批
        name = author.get("display_name") if isinstance(author, dict) else None
        out.append({
            "id": tp.get("id"),
            "title": tp.get("title") or "",
            "posted_at": tp.get("posted_at") or "",
            "last_reply_at": tp.get("last_reply_at") or "",
            "author": name or "",          # 实测 display_name 可能是 null
            # 实测字段名是 discussion_subentry_count，输出仍叫 replies_count
            "replies_count": tp.get("discussion_subentry_count") or 0,
            # read_state（根帖）与 unread_count（未读回复）是两个独立维度，
            # 两个都原样透传，未读口径由前端决定。
            "unread_count": tp.get("unread_count") or 0,
            "read_state": tp.get("read_state") or "read",
            "pinned": bool(tp.get("pinned")),
            "locked": bool(tp.get("locked")),
            "require_initial_post": bool(tp.get("require_initial_post")),
            "html_url": tp.get("html_url") or "",
        })
    # 先按「最后活动时间」降序（缺失的空串在 reverse=True 下自动排最后），
    # 再按 pinned 做稳定排序 → 置顶组内部保持时间序。
    out.sort(key=lambda x: x["last_reply_at"] or x["posted_at"] or "", reverse=True)
    out.sort(key=lambda x: not x["pinned"])
    return out


# planner 的日期回落链：plannable_date 是每种类型都存在的权威字段（实测 88/88），
# 缺失时按 plannable_type 回落到该类型的原生日期字段。
_PLANNER_DATE_FALLBACK = {
    "assignment": "due_at",
    "quiz": "due_at",
    "discussion_topic": "todo_date",
    "calendar_event": "start_at",
    "planner_note": "todo_date",
}


def get_planner_items(canvas_url: str, token: str, start_date: str,
                      end_date: str) -> list[dict]:
    """Canvas Planner 聚合流（作业 + 测验 + 讨论 + 日历 + 自记事项）。

    **不按日期过滤**：Canvas 的 start_date/end_date 参数实测生效（传范围 74 条 vs
    不传 88 条），但结果里仍含过去条目（实测 23 条早于今天）。「本周」是前端展示
    口径，后端只做归一化 —— 否则口径被焊死在 API 里，换个页面复用就得改后端。
    """
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        data = _paginate(s, f"{base}/api/v1/planner/items",
                         {"start_date": start_date, "end_date": end_date,
                          "per_page": 100}, token)
    out = []
    for item in data:
        ptype = item.get("plannable_type") or ""
        if ptype == "announcement":
            continue          # plannable_date 是发布时间不是截止时间，实测 20/88 条
        pl = item.get("plannable") or {}
        date = item.get("plannable_date") or ""
        if not date:
            fb = _PLANNER_DATE_FALLBACK.get(ptype)
            date = (pl.get(fb) or "") if fb else ""
        if not date:
            continue          # 无日期的聚合项对「本周 DDL」无意义
        subs = item.get("submissions")
        # submissions 键**恒在**，值是 false 或对象（实测 35:39）→ 必须判类型
        submitted = bool(subs.get("submitted")) if isinstance(subs, dict) else None
        url = item.get("html_url") or ""
        if url.startswith("/"):
            url = base + url          # 实测是相对路径，不补全就 openExternal 打不开
        out.append({
            "id": item.get("plannable_id"),
            "type": ptype,
            "title": pl.get("title") or "",
            "course_id": item.get("course_id"),
            "course_name": item.get("context_name") or "",
            "date": date,
            "submitted": submitted,
            "html_url": url,
        })
    out.sort(key=lambda x: (x["date"] == "", x["date"]))
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_canvas_client.py -v`
Expected: PASS（新增 13 条 + 既有全部）

- [ ] **Step 5: 提交**

```bash
git add backend/canvas_client.py tests/test_canvas_client.py
git commit -m "feat(backend): canvas_client 加 discussion_topics/planner 归一化（含实测边界）"
```

---

## Task 3: `main.py` — 3 个模型 + 5 个端点

**Files:**
- Modify: `backend/main.py`（模型区在 37-161，端点在现有 `course_detail` 附近）
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `canvas_client.get_quizzes / get_pages / get_page_body / get_discussion_topics / get_planner_items`（Task 1、2）；`canvas_client.CanvasToolDisabled`（Task 1）
- Produces（前端按这些形状取数）：
  - `POST /api/quizzes` ← `{canvas_url, canvas_token, course_ids}` → `{ok, by_course: {cid: [quiz]}, errors: {cid: msg}}`
  - `POST /api/discussions` ← 同上 → `{ok, by_course: {cid: [topic]}, errors: {cid: msg}}`
  - `POST /api/pages` ← `{canvas_url, canvas_token, course_id}` → `{ok, pages: [...], error?}`
  - `POST /api/page_body` ← `{canvas_url, canvas_token, course_id, page_url}` → `{ok, page: {...}, error?}`
  - `POST /api/planner` ← `{canvas_url, canvas_token, start_date, end_date}` → `{ok, items: [...], error?}`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_main.py` 末尾：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_main.py -k "quizzes or discussions or pages or page_body or planner" -v`
Expected: FAIL — `assert 404 == 200`（端点还不存在）

- [ ] **Step 3: 加模型**

在 `backend/main.py` 的 `CalendarEventsRequest`（第 135 行）之后插入：

```python
class CoursesRequest(CanvasConfig):
    """批量端点：一次拉多门课的同一类内容。"""
    course_ids: list[int]


class PageBodyRequest(CourseDetailRequest):
    """单个 Page 的正文。page_url 是 Canvas 的页面 slug（如 "course-home"）。"""
    page_url: str


class PlannerRequest(CanvasConfig):
    """Planner 聚合流。无课程维度 —— Canvas 按当前用户的全部选课聚合。"""
    start_date: str
    end_date: str
```

- [ ] **Step 4: 加端点**

在 `backend/main.py` 里、现有 `course_detail` 端点之后插入：

```python
@app.post("/api/quizzes")
def quizzes(req: CoursesRequest):
    """逐课程拉测验。未启用测验工具的课程返回 404（实测约一半课程如此），
    按「该课无测验」静默处理，不进 errors —— 那是常态，不是故障。"""
    by_course: dict[int, list] = {}
    errors: dict[int, str] = {}
    for cid in req.course_ids:
        try:
            by_course[cid] = canvas_client.get_quizzes(req.canvas_url,
                                                       req.canvas_token, cid)
        except canvas_client.CanvasToolDisabled:
            by_course[cid] = []
        except Exception as exc:
            by_course[cid] = []
            errors[cid] = str(exc)
    return {"ok": True, "by_course": by_course, "errors": errors}


@app.post("/api/discussions")
def discussions(req: CoursesRequest):
    """逐课程拉讨论区帖子。错误隔离口径与 /api/quizzes 一致。"""
    by_course: dict[int, list] = {}
    errors: dict[int, str] = {}
    for cid in req.course_ids:
        try:
            by_course[cid] = canvas_client.get_discussion_topics(
                req.canvas_url, req.canvas_token, cid)
        except canvas_client.CanvasToolDisabled:
            by_course[cid] = []
        except Exception as exc:
            by_course[cid] = []
            errors[cid] = str(exc)
    return {"ok": True, "by_course": by_course, "errors": errors}


@app.post("/api/pages")
def pages(req: CourseDetailRequest):
    """单课程 Pages 列表（不含正文）。未启用 Pages 工具 → 空列表，不算错误。"""
    try:
        return {"ok": True, "pages": canvas_client.get_pages(
            req.canvas_url, req.canvas_token, req.course_id)}
    except canvas_client.CanvasToolDisabled:
        return {"ok": True, "pages": []}
    except Exception as exc:
        return {"ok": False, "pages": [], "error": str(exc)}


@app.post("/api/page_body")
def page_body(req: PageBodyRequest):
    """单个 Page 正文。按需拉取 —— 列表阶段不带头文，避免 N+1。"""
    try:
        return {"ok": True, "page": canvas_client.get_page_body(
            req.canvas_url, req.canvas_token, req.course_id, req.page_url)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/planner")
def planner(req: PlannerRequest):
    """Canvas Planner 聚合流。不做日期过滤（见 canvas_client.get_planner_items）。"""
    try:
        return {"ok": True, "items": canvas_client.get_planner_items(
            req.canvas_url, req.canvas_token, req.start_date, req.end_date)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_main.py -v`
Expected: PASS（新增 11 条 + 既有全部）

- [ ] **Step 6: 全量回归 + 提交**

```bash
python -m pytest -q
```
Expected: 全绿（基线 193 passed + 本轮新增）

```bash
git add backend/main.py tests/test_main.py
git commit -m "feat(backend): 加 quizzes/discussions/pages/page_body/planner 五个端点"
```

---

## Task 4: 侧栏去图标 + 媒体查询修复

**Files:**
- Modify: `frontend/index.html:31-39`
- Modify: `frontend/app.css:68-77`（`.nav-item` / `.nav-ico`）、`frontend/app.css:376-382`（媒体查询）

**Interfaces:**
- Consumes: 无
- Produces: 纯文字侧栏；`@media (max-width:900px)` 不再有 64px 图标条分支

- [ ] **Step 1: 删 index.html 的 8 个图标 span**

`frontend/index.html` 第 31-39 行整体替换为：

```html
      <button class="nav-item" data-page="home"><span data-i18n="nav.home"></span></button>
      <button class="nav-item" data-page="announce"><span data-i18n="nav.announce"></span></button>
      <button class="nav-item" data-page="schedule"><span data-i18n="nav.schedule"></span></button>
      <button class="nav-item" data-page="todo"><span data-i18n="nav.todo"></span></button>
      <button class="nav-item" data-page="files"><span data-i18n="nav.files"></span></button>
      <button class="nav-item" data-page="grades"><span data-i18n="nav.grades"></span></button>
      <button class="nav-item" data-page="courses"><span data-i18n="nav.courses"></span></button>
      <div class="nav-gap"></div>
      <button class="nav-item" data-page="settings"><span data-i18n="nav.settings"></span></button>
```

`.sidebar-logo` 里的 `<span class="logo-mark">🎓</span>`（第 27 行）**保留** —— spec §6.7 只说删 `.nav-item` 内的图标。

- [ ] **Step 2: 删 `.nav-ico` 规则、调 `.nav-item` 内边距**

`frontend/app.css` 第 68-75 行改为：

```css
.nav-item { display:flex; align-items:center; padding:9px 14px; border:none;
  border-radius:var(--radius-sm); background:transparent; color:var(--muted);
  font:inherit; font-size:13.5px; font-weight:500; cursor:pointer; text-align:left;
  transition:background .15s, color .15s; }
.nav-item:hover { background:var(--surface-2); color:var(--ink); }
.nav-item.active { background:var(--accent-soft); color:var(--accent); font-weight:600;
  box-shadow:inset 0 0 0 1px var(--accent-brd); }
```

（`gap:10px` 去掉 —— 现在每个按钮只有一个子元素；`.nav-ico` 规则整条删除。）

`.nav-badge` 保持不动 —— 它是功能不是装饰，且 `margin-left:auto` 仍然生效。

- [ ] **Step 3: 修媒体查询（裁定 R2）**

`frontend/app.css` 第 376-382 行改为：

```css
@media (max-width:900px){
  /* 侧栏不再收成 64px 图标条：图标已删（见 .nav-item），图标条会变成空白。
     保持文字形态，只收紧内容区。 */
  .content { padding:18px 16px 96px; }
  .stat-row { grid-template-columns:repeat(2,1fr); }
  .set-grid { grid-template-columns:1fr; }
}
```

- [ ] **Step 4: 静态门**

```bash
node tools/check_dom_ids.mjs && node --check frontend/app.js && node --check frontend/shell.js && node --check frontend/i18n.js && node --check frontend/util.js
```
Expected: `✓ 所有 $("id") 引用都能在 index.html 中找到`（无输出即 JS 语法通过）

```bash
grep -c 'nav-ico' frontend/index.html frontend/app.css
```
Expected: 两个文件都输出 `0`

- [ ] **Step 5: 提交**

```bash
git add frontend/index.html frontend/app.css
git commit -m "feat(ui): 侧栏改纯文字去 emoji 图标；窄窗不再收成空白图标条"
```

---

## Task 5: 讨论页

**Files:**
- Modify: `frontend/index.html`（侧栏加一项 + 加 `#page-discuss`）
- Modify: `frontend/app.js`（新增讨论区代码块）
- Modify: `frontend/shell.js`（`PAGES` / `PAGE_INIT` / `setNavBadge` / `refreshBadges`）
- Modify: `frontend/i18n.js`（`zh` 与 `en` 各加 16 个键）

**Interfaces:**
- Consumes: `POST /api/discussions`（Task 3）；`$`/`esc`/`escAttr`/`short`（util.js）；`t`（i18n.js）；`api`/`settings`/`selectedCourses`/`courseList`/`setStatus`（app.js）
- Produces（Task 8 与 shell.js 依赖）：
  - `let discussData` — `{by_course: {cid: [topic]}, errors: {cid: msg}} | null`
  - `function initDiscussTab()` / `loadDiscussions()` / `renderDiscussions()`
  - `function countUnreadDiscussions() -> number` — 未加载时恒返回 `0`

- [ ] **Step 1: 加侧栏项与页面容器**

`frontend/index.html`：在 `data-page="announce"` 那一行之后插入：

```html
      <button class="nav-item" data-page="discuss"><span data-i18n="nav.discuss"></span></button>
```

在 `#page-announce` 那个 `</div>`（第 86 行）之后插入：

```html
    <div id="page-discuss" class="page" hidden>
      <div class="page-head"><h2 data-i18n="nav.discuss"></h2></div>
      <div class="filter-bar">
        <span class="filter-label" data-i18n="discuss.filter_label"></span>
        <button id="btnReloadDiscuss" class="btn btn-ghost" data-i18n="discuss.reload"></button>
        <span id="discussStatus" class="muted"></span>
      </div>
      <div id="discussGroups"></div>
    </div>
```

- [ ] **Step 2: 加 app.js 的讨论区代码块**

在 `frontend/app.js` 里 `async function initTodoTab(){`（第 1110 行）**之前**插入整个块：

```js
/* ===== 讨论区（只读：点条目交系统浏览器打开）===== */
let discussData = null;        // {by_course:{cid:[topic]}, errors:{cid:msg}} | null = 尚未加载
let discussTabInit = false;

function initDiscussTab(){
  if(discussTabInit) return;
  discussTabInit = true;
  loadDiscussions();
}

async function loadDiscussions(){
  const s = settings();
  if(!s.canvas_url || !s.canvas_token){ setStatus(t("discuss.need_canvas"), "err"); return; }
  const ids = selectedCourses();
  if(!ids.length){ setStatus(t("discuss.need_course"), "err"); return; }
  const el = $("discussStatus");
  el.textContent = t("discuss.loading");
  const r = await api("discussions", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                       course_ids: ids });
  if(r.ok !== true){ el.textContent = t("discuss.fail") + (r.error || ""); return; }
  discussData = { by_course: r.by_course || {}, errors: r.errors || {} };
  renderDiscussions();
  refreshBadges();
}

/* 未读口径：根帖未读 **或** 有未读回复。两个维度独立（实测 read_state="read" 而
   unread_count=8 确实存在），只数 read_state 会漏掉最该看的那一类。 */
function topicUnread(tp){ return tp.read_state === "unread" || Number(tp.unread_count) > 0; }

function topicRowHtml(tp){
  const unread = topicUnread(tp);
  const when = String(tp.last_reply_at || tp.posted_at || "");
  const bits = [tp.author || "", t("discuss.replies", { n: tp.replies_count })];
  if(tp.unread_count) bits.push(t("discuss.unread", { n: tp.unread_count }));
  if(when) bits.push(short(when.slice(0, 10)));
  return `<a class="discuss-row${unread ? " is-unread" : ""}" href="${escAttr(tp.html_url || "")}" target="_blank" rel="noopener">
    <div class="item-title">${unread ? `<span class="badge-new">${t("discuss.new")}</span>` : ""}${esc(tp.title || "")}</div>
    <div class="file-path">${esc(bits.filter(Boolean).join(" · "))}</div>
  </a>`;
}

function renderDiscussions(){
  const box = $("discussGroups");
  if(!discussData){ box.innerHTML = `<div class="muted">${t("discuss.not_loaded")}</div>`; return; }
  const by = discussData.by_course, errs = discussData.errors;
  const names = {};
  (courseList || []).forEach(c => { names[c.id] = c.name; });
  const keys = Object.keys(by).concat(Object.keys(errs).filter(k => !(k in by)));
  if(!keys.length){ box.innerHTML = `<div class="muted">${t("discuss.empty")}</div>`; return; }
  box.innerHTML = keys.map(k => {
    const list = by[k] || [], err = errs[k] || "";
    const body = err
      ? `<div class="muted">${t("discuss.course_fail")}${esc(err)}</div>`
      : !list.length
        ? `<div class="muted">${t("discuss.course_empty")}</div>`
        : list.map(topicRowHtml).join("");
    return `<div class="glass-card ann-grp"><div class="sub-label">${esc(names[k] || ("#" + k))}</div>${body}</div>`;
  }).join("");
  $("discussStatus").textContent = t("discuss.loaded", { n: keys.length });
}

/* 侧栏徽章用。首次加载讨论之前恒为 0 —— 本轮不为讨论加启动期后台预取。 */
function countUnreadDiscussions(){
  if(!discussData) return 0;
  return Object.keys(discussData.by_course || {}).reduce((n, k) =>
    n + (discussData.by_course[k] || []).filter(topicUnread).length, 0);
}
```

并在该块之后的顶层位置加按钮接线：

```js
$("btnReloadDiscuss").onclick = () => loadDiscussions();
```

- [ ] **Step 3: 接上线（shell.js 三处）**

`frontend/shell.js` 第 5 行 `PAGES` 改为（`discuss` 排在 `announce` 之后）：

```js
const PAGES = ["home","announce","discuss","schedule","todo","files","grades","courses","settings"];
```

第 11-17 行 `PAGE_INIT` 加一行：

```js
const PAGE_INIT = {
  schedule: () => initScheduleTab(),
  todo:     () => initTodoTab(),
  grades:   () => initGradesTab(),
  discuss:  () => initDiscussTab(),
  home:     () => initHome(),
  settings: () => openSettings(),
};
```

第 56 行 `setNavBadge` 里的 title 改成按页选键（原先是二选一的三元表达式）：

```js
  const KEY = { announce: "unread.announce", todo: "unread.todo", discuss: "unread.discuss" };
  item.title = t(KEY[page] || "unread.announce", { n });
```

第 60-63 行 `refreshBadges` 加第三项：

```js
function refreshBadges(){
  setNavBadge("announce", countNewAnnounce());
  setNavBadge("todo", countNewTodo());
  setNavBadge("discuss", countUnreadDiscussions());
}
```

- [ ] **Step 4: 加 i18n 键**

在 `frontend/i18n.js` 的 **`zh` 块**里，第 11 行 `"nav.settings": "设置",` 之后插入：

```js
    "nav.discuss": "讨论",
```

在 `zh` 块的 `"unread.todo"` 那一行之后插入：

```js
    "unread.discuss": "有未读讨论",
```

在 `zh` 块第 302 行 `"home.no_class": "今日无课",` 之后插入：

```js
    "discuss.filter_label": "讨论区",
    "discuss.reload": "刷新讨论",
    "discuss.loading": "正在加载讨论…",
    "discuss.loaded": "已加载 {n} 门课程的讨论",
    "discuss.fail": "加载讨论失败：",
    "discuss.empty": "暂无讨论",
    "discuss.not_loaded": "点「刷新讨论」加载",
    "discuss.course_empty": "该课程暂无讨论",
    "discuss.course_fail": "该课程加载失败：",
    "discuss.new": "新",
    "discuss.replies": "{n} 回复",
    "discuss.unread": "{n} 未读",
    "discuss.need_canvas": "还没配置 Canvas，去设置里填上地址和 Token。",
    "discuss.need_course": "请先在课程页勾选要同步的课程。",
```

在 **`en` 块**里，第 313 行 `"nav.settings": "Settings",` 之后插入：

```js
    "nav.discuss": "Discussions",
```

在 `en` 块的 `"unread.todo"` 那一行之后插入：

```js
    "unread.discuss": "Unread discussions",
```

在 `en` 块第 604 行 `"home.no_class": "No classes today",` 之后插入：

```js
    "discuss.filter_label": "Discussions",
    "discuss.reload": "Refresh discussions",
    "discuss.loading": "Loading discussions…",
    "discuss.loaded": "Loaded discussions for {n} course(s)",
    "discuss.fail": "Failed to load discussions: ",
    "discuss.empty": "No discussions yet",
    "discuss.not_loaded": "Click \"Refresh discussions\" to load",
    "discuss.course_empty": "No discussions in this course",
    "discuss.course_fail": "Failed to load this course: ",
    "discuss.new": "NEW",
    "discuss.replies": "{n} replies",
    "discuss.unread": "{n} unread",
    "discuss.need_canvas": "Canvas isn't configured yet — add the URL and token in Settings.",
    "discuss.need_course": "Select the courses to sync on the Courses page first.",
```

- [ ] **Step 5: 加 CSS**

`frontend/app.css` 追加：

```css
/* 讨论页：每条一行的可点链接 */
.discuss-row { display:block; padding:9px 12px; margin-bottom:6px; border-radius:var(--radius-sm);
  text-decoration:none; color:var(--ink); border:1px solid transparent; }
.discuss-row:hover { background:var(--surface-2); }
.discuss-row.is-unread { border-color:var(--err-border); background:var(--err-bg); }
```

- [ ] **Step 6: 静态门 + i18n 校验**

```bash
node tools/check_dom_ids.mjs
node --check frontend/app.js && node --check frontend/shell.js && node --check frontend/i18n.js
```
Expected: 无报错

```bash
node -e '
const s=require("fs").readFileSync("frontend/i18n.js","utf8");
const I=eval(s.replace(/^const I18N/,"var I18N")+";I18N");
const z=Object.keys(I.zh), e=Object.keys(I.en);
const miss=z.filter(k=>!(k in I.en)).concat(e.filter(k=>!(k in I.zh)));
const empty=[];
for(const [lang,d] of [["zh",I.zh],["en",I.en]])
  for(const k of Object.keys(d)) if(typeof d[k]==="string" && !d[k].trim()) empty.push(lang+":"+k);
const bad=[...new Set(miss.concat(empty))];
console.log(bad.length ? "✗ 键不一致或空值: "+bad.join(", ")
                      : `✓ zh/en 键集一致且无空值（各 ${z.length} 个）`);
process.exit(bad.length?1:0);'
```
Expected: `✓ zh/en 键集一致且无空值`

- [ ] **Step 7: 提交**

```bash
git add frontend/index.html frontend/app.js frontend/shell.js frontend/i18n.js frontend/app.css
git commit -m "feat(ui): 新增讨论页（按课程分组、未读双维度口径、侧栏徽章）"
```

---

## Task 6: 课程详情弹层加 Pages 板块

**Files:**
- Modify: `frontend/app.js`（`openCourseDetail` 第 228-253 行、`renderDetail` 第 279-397 行、新增顶层状态与函数）
- Modify: `frontend/i18n.js`（6 个键 × 2 语言）
- Modify: `frontend/app.css`

**Interfaces:**
- Consumes: `POST /api/pages`、`POST /api/page_body`（Task 3）
- Produces:
  - `let detailPages` — `list | null`
  - `let detailPagesError` — `str`
  - `const pageBodyCache` — `` {`${course_id}:${page_url}`: {page} | {error}} ``，会话内不失效
  - `async function fetchDetailPages(canvasId)` / `loadPageBody(courseId, pageUrl, hostEl)`

- [ ] **Step 1: 加 i18n 键**

`frontend/i18n.js` 的 `zh` 块第 24 行 `"courses.ignore": "忽略",` 之后插入：

```js
    "courses.pages": "页面",
    "courses.pages_empty": "该课程暂无页面",
    "courses.pages_fail": "页面加载失败：",
    "courses.page_front": "首页",
    "courses.page_loading": "正在加载正文…",
    "courses.page_fail": "正文加载失败：",
```

`en` 块第 326 行 `"courses.ignore": "Ignore",` 之后插入：

```js
    "courses.pages": "Pages",
    "courses.pages_empty": "No pages in this course",
    "courses.pages_fail": "Failed to load pages: ",
    "courses.page_front": "Front page",
    "courses.page_loading": "Loading content…",
    "courses.page_fail": "Failed to load content: ",
```

- [ ] **Step 2: 加状态与两个函数**

在 `frontend/app.js` 第 184 行 `let detailBanweb` 之后插入：

```js
let detailPages = null;        // 该课程的 Pages 列表 | null = 尚未取
let detailPagesError = "";
/* Page 正文本地缓存：键 `${course_id}:${page_url}`，会话内不失效。
   内存态，不落 localStorage —— 刷新页面即清空。 */
const pageBodyCache = {};
```

在 `openCourseDetail` 之前插入两个函数：

```js
/* 拉课程 Pages 列表（不含正文）。失败只影响该板块，不拖垮 Modules / 文件。 */
async function fetchDetailPages(canvasId){
  try {
    const s = settings();
    const r = await api("pages", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                   course_id: canvasId });
    detailPages = Array.isArray(r.pages) ? r.pages : [];
    detailPagesError = r.ok === true ? "" : (r.error || "");
  } catch (e) {
    detailPages = [];
    detailPagesError = String((e && e.message) || e);
  }
}

/* 展开单条 Page 时才拉正文；pageBodyCache 命中则直接渲染，不发请求。 */
async function loadPageBody(courseId, pageUrl, hostEl){
  const key = `${courseId}:${pageUrl}`;
  if(!(key in pageBodyCache)){
    hostEl.innerHTML = `<div class="muted">${t("courses.page_loading")}</div>`;
    const s = settings();
    try {
      const r = await api("page_body", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                         course_id:courseId, page_url:pageUrl });
      pageBodyCache[key] = r.ok === true ? { page: r.page } : { error: r.error || "" };
    } catch (e) {
      pageBodyCache[key] = { error: String((e && e.message) || e) };
    }
  }
  const hit = pageBodyCache[key];
  hostEl.innerHTML = hit.error
    ? `<div class="muted">${t("courses.page_fail")}${esc(hit.error)}</div>`
    // 纯文本插入：不注入 Canvas 返回的原始 HTML
    : `<div class="detail-syllabus">${esc((hit.page && hit.page.body_text) || "")}</div>`;
}
```

- [ ] **Step 3: 在 `openCourseDetail` 里复位并取数**

`frontend/app.js` 第 232-235 行之间（`detailModulesError = "";` 之后）插入：

```js
  detailPages = null;
  detailPagesError = "";
```

第 247 行的 `try { await ensureAssignments([canvasId]); }` 整段替换为：

```js
    // 两个请求并发，避免详情弹层打开被串行拖慢
    try { await Promise.all([ensureAssignments([canvasId]), fetchDetailPages(canvasId)]); }
    catch (e) { /* 详情仍展示，作业区 / 页面区留空 */ }
```

（`fetchDetailPages` 自己吞异常，不会让 `Promise.all` 失败。）

- [ ] **Step 4: 在 `renderDetail` 里加第三块**

在 `frontend/app.js` 的 `const profs = ...`（第 322 行）**之前**插入：

```js
  // ⑦ Canvas Pages：列表来自 /api/pages；正文展开时才拉（懒加载 + 内存缓存）
  const pagesHtml = (c && (detailPages || detailPagesError))
    ? `<div class="detail-section"><div class="sub-label">${t("courses.pages")}</div>` +
      (detailPagesError
        ? `<div class="muted">${t("courses.pages_fail")}${esc(detailPagesError)}</div>`
        : !detailPages.length
          ? `<div class="muted">${t("courses.pages_empty")}</div>`
          : detailPages.map(p =>
              `<div class="detail-page-row" data-cid="${c.id}" data-purl="${escAttr(p.url)}">
                 <div class="detail-page-head"><span class="detail-page-caret">▸</span>${esc(p.title || p.url)}${p.front_page ? ` <span class="chip">${t("courses.page_front")}</span>` : ""}</div>
                 <div class="detail-page-body" hidden></div>
               </div>`).join("")) +
      `</div>`
    : "";
```

在 `$("detailBody").innerHTML = \`...\`` 模板里，把 `${modulesHtml}` 那一行改成两行：

```js
    ${modulesHtml}
    ${pagesHtml}
```

- [ ] **Step 5: 加展开的点击委托**

`frontend/app.js` 第 911 行那个 `$("detailBody").addEventListener("click", ...)` 之后，再追加**第三个**委托监听（该元素已有两个，这是本文件既有的模式）：

```js
/* Pages 条目展开：展开时才拉正文；缓存命中则直接渲染（二次展开不重复请求） */
$("detailBody").addEventListener("click", (e) => {
  const head = e.target.closest(".detail-page-head");
  if(!head) return;
  const row = head.parentElement;
  const body = row && row.querySelector(".detail-page-body");
  if(!body) return;
  const opening = body.hidden;
  body.hidden = !opening;
  const caret = head.querySelector(".detail-page-caret");
  if(caret) caret.textContent = opening ? "▾" : "▸";
  if(opening) loadPageBody(Number(row.dataset.cid), row.dataset.purl, body);
});
```

- [ ] **Step 6: 加 CSS**

`frontend/app.css` 追加：

```css
/* 课程详情 · Pages 板块 */
.detail-page-row { border-top:1px dashed var(--panel-border); }
.detail-page-row:first-of-type { border-top:none; }
.detail-page-head { padding:7px 0; font-size:13.5px; cursor:pointer; display:flex;
  align-items:center; gap:6px; }
.detail-page-head:hover { color:var(--accent); }
.detail-page-caret { color:var(--muted); flex:none; font-size:11px; }
.detail-page-body { padding:2px 0 10px; }
```

- [ ] **Step 7: 静态门 + i18n 校验**

```bash
node tools/check_dom_ids.mjs && node --check frontend/app.js && node --check frontend/i18n.js
```
Expected: 无报错

再跑 Task 5 Step 6 里那段 i18n 键集 + 空值校验命令。
Expected: `✓ zh/en 键集一致且无空值`

- [ ] **Step 8: 提交**

```bash
git add frontend/app.js frontend/i18n.js frontend/app.css
git commit -m "feat(ui): 课程详情弹层加 Pages 板块（列表 + 懒加载正文 + 会话内缓存）"
```

---

## Task 7: 课表页加测验分组

**Files:**
- Modify: `frontend/index.html`（考试区之后加挂载点）
- Modify: `frontend/app.js`（`initScheduleTab` 第 1748 行、新增状态与两个函数、`applyLang` 第 629 行）
- Modify: `frontend/app.css`（测验配色）
- Modify: `frontend/i18n.js`（6 个键 × 2 语言）

**Interfaces:**
- Consumes: `POST /api/quizzes`（Task 3）
- Produces: `let canvasQuizzes` — `{by_course, errors} | null`；`loadQuizzes()` / `renderQuizzes()`

- [ ] **Step 1: 加挂载点**

`frontend/index.html` 第 117 行 `</div>`（`#examBar` 结束）之后、第 118 行 `.write-bar` 之前插入：

```html
      <div id="quizGroup" hidden></div>
```

- [ ] **Step 2: 加 i18n 键**

`frontend/i18n.js` 的 `zh` 块第 290 行 `"schedule.exam_done"` 之后插入：

```js
    "schedule.quiz_label": "课程测验",
    "schedule.quiz_loaded": "已加载 {n} 项测验",
    "schedule.quiz_none": "暂无测验",
    "schedule.quiz_due": "截止",
    "schedule.quiz_questions": "{n} 题",
    "schedule.quiz_limit": "限时 {n} 分钟",
```

`en` 块第 592 行 `"schedule.exam_done"` 之后插入：

```js
    "schedule.quiz_label": "Course quizzes",
    "schedule.quiz_loaded": "{n} quiz(es) loaded",
    "schedule.quiz_none": "No quizzes",
    "schedule.quiz_due": "Due",
    "schedule.quiz_questions": "{n} questions",
    "schedule.quiz_limit": "{n} min limit",
```

- [ ] **Step 3: 加状态与两个函数**

`frontend/app.js` 第 1696 行 `let banwebExams = null;` 之后插入：

```js
let canvasQuizzes = null;      // {by_course:{cid:[quiz]}, errors:{cid:msg}} | null = 未加载
```

`loadExams` 函数之后（第 1716 行之后）插入：

```js
/* 测验来自 Canvas，与 AIMS 登录无关 —— 未登录也要能看。整体失败就静默不显示分组。 */
async function loadQuizzes(){
  const s = settings();
  if(!s.canvas_url || !s.canvas_token) return;
  const ids = selectedCourses();
  if(!ids.length) return;
  try {
    const r = await api("quizzes", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                     course_ids: ids });
    canvasQuizzes = r.ok === true
      ? { by_course: r.by_course || {}, errors: r.errors || {} }
      : null;
  } catch (e) {
    canvasQuizzes = null;
  }
  renderQuizzes();
}

/* 未启用测验工具的课程在端点层就没进 errors（实测约一半课程如此），
   所以这里没有「404 错误行」要处理；真正失败的那些课程也只是不贡献条目。 */
function renderQuizzes(){
  const box = $("quizGroup");
  if(!box) return;
  if(!canvasQuizzes){ box.hidden = true; box.innerHTML = ""; return; }
  const names = {};
  (courseList || []).forEach(c => { names[c.id] = c.name; });
  const rows = [];
  Object.keys(canvasQuizzes.by_course).forEach(k => {
    (canvasQuizzes.by_course[k] || []).forEach(q =>
      rows.push({ ...q, course: names[k] || ("#" + k) }));
  });
  box.hidden = false;
  if(!rows.length){
    box.innerHTML = `<div class="filter-bar" style="margin-top:14px">
      <span class="filter-label">${t("schedule.quiz_label")}</span>
      <span class="muted">${t("schedule.quiz_none")}</span></div>`;
    return;
  }
  rows.sort((a, b) => String(a.due_at || "").localeCompare(String(b.due_at || "")));
  box.innerHTML =
    `<div class="filter-bar" style="margin-top:14px">
       <span class="filter-label">${t("schedule.quiz_label")}</span>
       <span class="muted">${t("schedule.quiz_loaded", { n: rows.length })}</span>
     </div>
     <div class="quiz-list">` +
    rows.map(q => {
      const bits = [];
      if(q.due_at) bits.push(t("schedule.quiz_due") + " " + fmtDue(q.due_at));
      if(q.question_count != null) bits.push(t("schedule.quiz_questions", { n: q.question_count }));
      if(q.time_limit != null) bits.push(t("schedule.quiz_limit", { n: q.time_limit }));
      if(q.points_possible != null) bits.push(String(q.points_possible) + " pts");
      return `<a class="quiz-row" href="${escAttr(q.html_url || "")}" target="_blank" rel="noopener">
        <div class="item-title">${esc(q.title || "")}</div>
        <div class="file-path">${esc(q.course + (bits.length ? " · " + bits.join(" · ") : ""))}</div>
      </a>`;
    }).join("") + `</div>`;
}
```

**已核对**：`fmtDue(iso)` 定义在 `frontend/app.js:186`，直接用它，无需另写格式化。

- [ ] **Step 4: 挂钩**

`frontend/app.js` 第 1748 行：

```js
  loadExams();   // 静默拉考试（登录态复用课表会话；失败仅提示不阻塞）
```

改为：

```js
  loadExams();     // 静默拉考试（登录态复用课表会话；失败仅提示不阻塞）
  loadQuizzes();   // 静默拉 Canvas 测验（与 AIMS 无关，不登录也要能看）
```

`frontend/app.js` 第 629 行 `renderSummaries(); renderFiles(); renderSchedule();` 之后插入：

```js
  if (typeof renderQuizzes === "function") renderQuizzes();   // 语言切换后重渲测验分组
```

- [ ] **Step 5: 加 CSS**

`frontend/app.css` 追加（低饱和蓝紫，与 AIMS 考试块的红 `#7f1d1d` / `#ef4444` 明确区分）：

```css
/* 课表页 · Canvas 测验分组（与考试红块并列但一眼可区分） */
.quiz-list { display:flex; flex-direction:column; gap:6px; margin-top:10px; }
.quiz-row { display:block; padding:9px 12px; border-radius:var(--radius-sm);
  background:rgba(129,140,248,.10); border:1px solid rgba(129,140,248,.30);
  text-decoration:none; color:var(--ink); }
.quiz-row:hover { background:rgba(129,140,248,.18); }
```

- [ ] **Step 6: 静态门 + i18n 校验**

```bash
node tools/check_dom_ids.mjs && node --check frontend/app.js && node --check frontend/i18n.js
```
Expected: 无报错

再跑 Task 5 Step 6 里那段 i18n 键集 + 空值校验命令。
Expected: `✓ zh/en 键集一致且无空值`

- [ ] **Step 7: 提交**

```bash
git add frontend/index.html frontend/app.js frontend/app.css frontend/i18n.js
git commit -m "feat(ui): 课表页加 Canvas 测验分组（独立于 AIMS 登录态）"
```

---

## Task 8: 首页仪表盘

**Files:**
- Modify: `frontend/index.html:44-74`（首页整段重写）
- Modify: `frontend/shell.js`（`initHome` / `renderHomeToday` / `renderHomeFromCache` / `renderHomeDdlList` / `renderHomeGradeCard` + 1 个新状态）
- Modify: `frontend/app.css`（仪表盘网格）
- Modify: `frontend/i18n.js`（4 个键 × 2 语言）

**Interfaces:**
- Consumes: `POST /api/planner`（Task 3）；`gradesData`（app.js:1220）；`banwebSchedule`（app.js:1274）；现有 `countNewAnnounce()` / `summaryResults` / `fmt(now)`
- Produces: `let homeDdlItems`；`renderHomeDdlList()` / `renderHomeGradeCard()`

- [ ] **Step 1: 重写首页 HTML**

`frontend/index.html` 第 44-74 行（`<div id="page-home"...>` 到它的 `</div>`）整体替换为：

```html
    <div id="page-home" class="page" hidden>
      <div class="page-head"><h2 data-i18n="nav.home"></h2></div>

      <div id="homeNeedCanvas" class="glass-card home-guide" hidden>
        <p data-i18n="home.need_canvas"></p>
        <button class="btn btn-primary" data-goto="settings" data-i18n="nav.settings"></button>
      </div>

      <div id="homeStats" class="stat-row">
        <div class="glass-card stat-card" data-goto="announce">
          <b id="statAnnounce">—</b><span data-i18n="home.stat.announce"></span></div>
        <div class="glass-card stat-card" data-goto="schedule">
          <b id="statToday">—</b><span data-i18n="home.stat.today"></span></div>
        <div class="glass-card stat-card" data-goto="todo">
          <b id="statDdl">—</b><span data-i18n="home.stat.ddl"></span></div>
        <div class="glass-card stat-card" data-goto="schedule">
          <b id="statExam">—</b><span data-i18n="home.stat.exam"></span></div>
      </div>

      <div class="glass-card list-card">
        <div class="list-head"><span data-i18n="home.today_schedule"></span>
          <button class="link-btn" data-goto="schedule" data-i18n="home.more"></button></div>
        <div id="homeTodayList" class="today-grid"></div>
      </div>

      <div class="home-cards">
        <div class="glass-card list-card">
          <div class="list-head"><span data-i18n="home.recent_announce"></span>
            <button class="link-btn" data-goto="announce" data-i18n="home.more"></button></div>
          <div id="homeAnnounceList" class="muted" data-i18n="home.loading"></div>
        </div>

        <div class="glass-card list-card">
          <div class="list-head"><span data-i18n="home.ddl_card"></span>
            <button class="link-btn" data-goto="todo" data-i18n="home.more"></button></div>
          <div id="homeDdlList" class="muted" data-i18n="home.loading"></div>
        </div>

        <div class="glass-card list-card">
          <div class="list-head"><span data-i18n="home.grade_card"></span>
            <button class="link-btn" data-goto="grades" data-i18n="home.more"></button></div>
          <div id="homeGradeList" class="muted" data-i18n="home.loading"></div>
        </div>
      </div>
    </div>
```

注意：三张速览卡与今日课程卡**都保留 `list-card` 类** —— `setHomeListCards()`（shell.js:89-91）靠 `$$("#page-home .list-card")` 在未配置 Canvas 时整组隐藏，不需要改。

**丢弃的旧 id**：`#homeTodayList` 保留（见上），但注意旧首页里 `#homeTodayList` 带 `class="muted"` 与 `data-i18n="home.loading"`，新版本改为 `class="today-grid"` 且不带 `data-i18n` —— 因为它现在装的是卡片而不是一句提示。空态由 `renderHomeToday()` 自己写 `<div class="muted">…</div>`。**这一步会删除 `data-i18n="home.loading"` 的一个使用点**，该键在别处仍被 `#homeAnnounceList` / `#homeDdlList` / `#homeGradeList` 使用，键本身保留。

- [ ] **Step 2: 加 i18n 键**

`frontend/i18n.js` 的 `zh` 块 `"home.no_class": "今日无课",` 之后插入：

```js
    "home.ddl_card": "本周 DDL",
    "home.grade_card": "成绩速览",
    "home.ddl_empty": "本周没有要交的",
    "home.grade_empty": "还没加载成绩，去成绩页看看",
```

`en` 块 `"home.no_class": "No classes today",` 之后插入：

```js
    "home.ddl_card": "Due this week",
    "home.grade_card": "Grades at a glance",
    "home.ddl_empty": "Nothing due this week",
    "home.grade_empty": "No grades loaded yet — open the Grades page",
```

- [ ] **Step 3: shell.js 加状态与两个渲染函数**

`frontend/shell.js` 第 80 行 `let homeExamCount = null;` 之后插入：

```js
let homeDdlItems = null;     // 本周 DDL 条目（来自 /api/planner），null = 尚未拉取
```

`frontend/shell.js` 第 188 行 `renderHomeAnnounceList` 函数之后插入：

```js
/* 本周 DDL 列表：数据来自 /api/planner（后端已丢弃 announcement，这里不再过滤） */
function renderHomeDdlList(){
  const box = $("homeDdlList");
  const items = Array.isArray(homeDdlItems) ? homeDdlItems : [];
  if(!items.length){ box.innerHTML = `<div class="muted">${t("home.ddl_empty")}</div>`; return; }
  box.innerHTML = items.slice(0, 5).map(it =>
    `<div class="home-row"><span class="hr-name">${esc(it.title || "")}</span>
       <em>${esc(short(String(it.date || "").slice(0, 10)))}</em></div>`).join("");
}

/* 成绩速览：**只读内存**里 gradesData（app.js:1220），用户访问过成绩页后才有内容。
   首页启动阶段不得为它发起 /api/grades —— 那会逐课程拉全部作业，代价与其余卡不在
   一个量级，放上首页会显著拖慢首屏。 */
function renderHomeGradeCard(){
  const box = $("homeGradeList");
  const rows = Array.isArray(gradesData) ? gradesData : [];
  if(!rows.length){ box.innerHTML = `<div class="muted">${t("home.grade_empty")}</div>`; return; }
  box.innerHTML = rows.slice(0, 5).map(g =>
    `<div class="home-row"><span class="hr-name">${esc(g.course_name || "")}</span>
       <em>${g.current_score != null ? esc(String(g.current_score)) : "—"}</em></div>`).join("");
}
```

- [ ] **Step 4: 今日课程改卡片组**

`frontend/shell.js` 第 162-164 行的 `$("homeTodayList").innerHTML = slots.map(...)` 那段替换为：

```js
  $("homeTodayList").innerHTML = slots.map(s =>
    `<div class="today-card"><b>${esc(fmtTime(s.start))}–${esc(fmtTime(s.end))}</b>
       <span>${esc(s.label)}</span><em>${esc(s.room || "")}</em></div>`).join("");
```

`renderHomeToday` 的其余部分（空态、`statToday` 赋值、slot 收集逻辑）**不动**。

- [ ] **Step 5: 「本周 DDL」换数据源**

`frontend/shell.js` 第 117-128 行那段（`(async () => { // 本周 DDL ... })()`）整体替换为：

```js
    (async () => {                                   // 本周 DDL（Canvas Planner 聚合流）
      const now = new Date();
      const end = new Date(now); end.setDate(end.getDate() + 7);
      const r = await api("planner", {
        canvas_url: s.canvas_url, canvas_token: s.canvas_token,
        start_date: fmt(now), end_date: fmt(end) });
      if(r.ok === true && Array.isArray(r.items)){
        const weekEnd = new Date(now); weekEnd.setDate(now.getDate() + 7);
        const week = r.items.filter(it => {
          if (it.type === "announcement") return false;   // 冗余保险：后端已丢弃
          if (it.submitted === true) return false;        // 已交不计入
          const d = new Date(it.date);                    // submitted === null 计入
          return !isNaN(d) && d >= now && d <= weekEnd;
        });
        homeDdlItems = week;
        homeDdlCount = week.length;
        $("statDdl").textContent = String(homeDdlCount);
      }
      renderHomeDdlList();
    })(),
```

- [ ] **Step 6: 缓存回填时也重渲两张新卡**

`frontend/shell.js` 第 168-174 行 `renderHomeFromCache()` 里，`renderHomeAnnounceList();` 之后插入：

```js
  renderHomeDdlList();
  renderHomeGradeCard();
```

- [ ] **Step 7: 加 CSS**

`frontend/app.css` 追加：

```css
/* 首页仪表盘：今日课程小卡组 + 三张速览卡 */
.today-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px; }
.today-card { padding:10px 12px; border-radius:var(--radius-sm);
  background:var(--surface-2); border:1px solid var(--panel-border); }
.today-card b { display:block; font-size:12.5px; font-variant-numeric:tabular-nums;
  color:var(--accent); }
.today-card span { display:block; font-size:12.5px; font-weight:600; }
.today-card em { font-style:normal; font-size:11.5px; color:var(--muted); }
.home-cards { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }
```

并在 `@media (max-width:900px)` 块里（Task 4 改好的那块）加一行：

```css
  .home-cards { grid-template-columns:1fr; }
```

- [ ] **Step 8: 静态门 + i18n 校验**

```bash
node tools/check_dom_ids.mjs && node --check frontend/shell.js && node --check frontend/i18n.js
```
Expected: 无报错

再跑 Task 5 Step 6 里那段 i18n 键集 + 空值校验命令。
Expected: `✓ zh/en 键集一致且无空值`

- [ ] **Step 9: 跑后端测试确认没被牵连 + 提交**

```bash
python -m pytest -q
```
Expected: 全绿

```bash
git add frontend/index.html frontend/shell.js frontend/app.css frontend/i18n.js
git commit -m "feat(ui): 首页改仪表盘网格，本周 DDL 换用 Canvas Planner"
```

---

## Task 9: 玻璃质感增强

**Files:**
- Modify: `frontend/app.css`（浅色 `:root` token 值、深色 `:root[data-theme="dark"]` token 值、玻璃配方选择器组）

**Interfaces:**
- Consumes: 现有 token 名（`--panel` / `--panel-border` / `--panel-blur` / `--panel-saturate` / `--shadow-sm` / `--shadow-md`）
- Produces: 无新符号。**不新增 token、不改 token 名**（spec §6.6）

- [ ] **Step 1: 改浅色 token（关键约束：不得变透明）**

`frontend/app.css` **第 14-17 行**（现为 `--panel:rgba(255,255,255,.72)` / `--panel-border:rgba(16,24,40,.10)` / `--panel-blur:14px` / `--panel-saturate:115%`）四行整体替换为：

```css
  --panel:rgba(255,255,255,.62);
  --panel-border:rgba(16,24,40,.14);
  --panel-blur:18px;
  --panel-saturate:125%;
```

浅色下 `--panel` 从 `.72` 降到 `.62`，但描边从 `.10` 加到 `.14` —— 面板仍然实心可读，只是更透一点。`.62` 是下限，**不要再低**。

同块 **第 11-12 行**（现为 `--shadow-sm:0 1px 2px rgba(16,24,40,.06)` / `--shadow-md:0 12px 32px rgba(16,24,40,.14)`）替换为：

```css
  --shadow-sm:0 2px 6px rgba(16,24,40,.07);
  --shadow-md:0 18px 44px rgba(16,24,40,.18);
```

- [ ] **Step 2: 改深色 token**

`frontend/app.css` **第 37-40 行**（现为 `--panel:rgba(255,255,255,.052)` / `--panel-border:rgba(255,255,255,.095)` / `--panel-blur:18px` / `--panel-saturate:130%`）四行替换为：

```css
  --panel:rgba(255,255,255,.038);
  --panel-border:rgba(255,255,255,.085);
  --panel-blur:26px;
  --panel-saturate:150%;
```

同块 **第 34-35 行**（现为 `--shadow-sm:0 1px 2px rgba(0,0,0,.4)` / `--shadow-md:0 24px 60px rgba(0,0,0,.55)`）替换为：

```css
  --shadow-sm:0 1px 2px rgba(0,0,0,.45);
  --shadow-md:0 28px 68px rgba(0,0,0,.62);
```

- [ ] **Step 3: 改玻璃配方，加顶部内高光**

`frontend/app.css` **第 86-90 行**（现为无 `box-shadow` 的五属性块）整体替换为：

```css
/* 玻璃卡片：更透的底 + 顶部内高光 + 更强的模糊/饱和度。
   内高光用 color-mix 会拖低 WKWebView 性能，这里给浅深两套各写一条固定值。 */
.glass-card, .set-card, .filter-bar, .course-card, .schedule-grid, .ann-grp,
.list-card, .stat-card {
  background:var(--panel); border:1px solid var(--panel-border);
  border-radius:var(--radius);
  backdrop-filter:blur(var(--panel-blur)) saturate(var(--panel-saturate));
  -webkit-backdrop-filter:blur(var(--panel-blur)) saturate(var(--panel-saturate));
  box-shadow:var(--shadow-sm), inset 0 1px 0 rgba(255,255,255,.55); }
:root[data-theme="dark"] .glass-card, :root[data-theme="dark"] .set-card,
:root[data-theme="dark"] .filter-bar, :root[data-theme="dark"] .course-card,
:root[data-theme="dark"] .schedule-grid, :root[data-theme="dark"] .ann-grp,
:root[data-theme="dark"] .list-card, :root[data-theme="dark"] .stat-card {
  box-shadow:var(--shadow-sm), inset 0 1px 0 rgba(255,255,255,.08); }
```

原块**没有 `box-shadow`**（已实读确认），所以这一步骤是纯新增一行 + 一个深色覆盖块，不存在覆盖既有取值的问题。`border-radius:var(--radius)` 与原块一致，原样保留。

注意 `.list-card` / `.stat-card` 被加进了这个选择器组 —— 它们在 index.html 里与 `.glass-card` 同时出现，重复声明无害，但能让只带 `list-card` 的用法也拿到玻璃底。

- [ ] **Step 4: 静态门 + 人工看色**

```bash
node tools/check_dom_ids.mjs
```
Expected: 无报错

```bash
grep -n 'panel:\|panel-blur:\|panel-saturate:\|panel-border:' frontend/app.css
```
Expected: 恰好 8 行（浅深两套各 4 个 token），确认没有漏改深色块

**人工（此时就跑，别等到最后）**：`python run_app.py --browser`，用设置页的主题三态切换，确认浅色下面板**仍是实心白**、能看清文字；深色下卡片有可见的顶部高光。

- [ ] **Step 5: 提交**

```bash
git add frontend/app.css
git commit -m "style(ui): 玻璃质感增强（更透的底 + 顶部内高光 + 更强模糊与投影）"
```

---

## 收尾：全量验收

- [ ] **Step 1: 后端全量回归**

```bash
python -m pytest -q
```
Expected: 全绿

- [ ] **Step 2: 静态门**

```bash
node tools/check_dom_ids.mjs && for f in frontend/*.js; do node --check "$f" || exit 1; done
```
Expected: 全部通过

- [ ] **Step 3: 顶层重名终检**

```bash
for n in discussData discussTabInit topicUnread topicRowHtml pageBodyCache detailPages detailPagesError fetchDetailPages loadPageBody canvasQuizzes homeDdlItems renderHomeDdlList renderHomeGradeCard initDiscussTab loadDiscussions renderDiscussions countUnreadDiscussions loadQuizzes renderQuizzes; do
  c=$(grep -h "^\(const\|let\|var\|function\) $n\b" frontend/*.js | wc -l | tr -d ' ')
  [ "$c" != "1" ] && echo "✗ $n 声明了 $c 次"
done; echo "重名终检完成"
```
Expected: `重名终检完成`，无 `✗` 行

- [ ] **Step 4: 按 spec §8.3 逐条人工验收**

```bash
python run_app.py --browser
```

（先确认 8331 端口没有旧实例：`lsof -ti:8331 | xargs kill 2>/dev/null`）

1. 侧栏 9 项都能切，无图标、纯文字、未读徽章正常
2. 讨论页：按课程分组、未读标记、点条目开系统浏览器
3. 课程详情：Modules / 文件 / 页面三块都在；页面展开才拉正文，二次展开不重复请求
4. 课表页：测验分组出现在考试区，AIMS 未登录也能看到；无测验的课程不显示错误行
5. 首页仪表盘：统计卡 ×4 + 今日课程卡组 + 速览卡 ×3
6. 首页「本周 DDL」来自 planner：**已交项不计入、公告不计入**
7. 玻璃质感：卡片更透、有顶部高光；**浅色主题下面板不透明**
8. 窗口缩小时仪表盘网格正确换行、不破版；**窄窗下侧栏不空**（裁定 R2）

---

## 自查记录（Self-Review）

**1. Spec 覆盖**

| Spec 章节 | 对应任务 |
|---|---|
| §5.0 实测确认 | Task 1 / 2 的字段表与用例逐条对应（`discussion_subentry_count`、`author=[]`、`read_state`×`unread_count`、`announcement` 丢弃、相对 `html_url`、`submissions` 恒在） |
| §5.1 五个函数 | Task 1（quizzes / pages / page_body）、Task 2（discussion_topics / planner） |
| §5.2 五个端点 + 模型 | Task 3（多出 `PlannerRequest`，见裁定 R4） |
| §6.1 文件划分 | 全部任务均不新增 js 文件；Global Constraints 已锁 |
| §6.2 讨论页 | Task 5 |
| §6.3 课程详情 Pages | Task 6 |
| §6.4 课表页测验 | Task 7 |
| §6.5 首页仪表盘 | Task 8（含「成绩卡只读内存、启动不拉 /api/grades」） |
| §6.6 玻璃质感 | Task 9（含「只改值不新增 token」与浅色不透约束） |
| §6.7 侧栏去图标 | Task 4（含 spec 漏掉的媒体查询缺陷，裁定 R2） |
| §6.8 i18n | Task 5/6/7/8 各自补键 + 每步跑键集与空值校验；共 16+6+6+4 = 32 键 × 2 |
| §7 错误处理 | Task 1 的 `CanvasToolDisabled`（裁定 R1）+ Task 3 的分支 + Task 6/7/8 的降级 |
| §8.1 自动化测试 | Task 1 / 2 / 3 |
| §8.2 静态门 | Task 4-9 每步 + 收尾 Step 2/3 |
| §8.3 人工验收 | 收尾 Step 4，8 条逐条列出 |

**2. 占位符扫描**：无 TBD / TODO / 「类似 Task N」/「加适当的错误处理」。每个 code step 都给了可直接粘贴的完整代码。

初稿里有两处「先 grep 确认 / 先读一遍再改」的条件分支（Task 7 的 `fmtDue`、Task 9 的玻璃块属性边界）。两处都已在写完后实读源码收敛为确定写法：`fmtDue` 确认存在于 app.js:186，玻璃块确认**无** `box-shadow` —— 计划里不再留任何「视情况而定」。

**3. 类型一致性**：`by_course` / `errors` 在 Task 1→2→3→5→7 全程同名同形；`discussData.by_course[cid]` 的元素字段与 Task 2 的输出键一一对应；`canvasQuizzes.by_course[cid]` 元素字段（`due_at` / `question_count` / `time_limit` / `points_possible` / `html_url`）全部来自 Task 1 的 `get_quizzes` 输出键，无臆造；`homeDdlItems` 元素字段（`title` / `date` / `submitted` / `type`）全部来自 Task 2 的 `get_planner_items` 输出键。`gradesData[i]` 的 `course_name` / `current_score` 已对着 `renderGrades()`（app.js:1241）核对，非臆造。

**4. 已核对的外部事实**（改代码前实测/实读，非推断）：
- `_Resp` / `_Session` 假类与 `monkeypatch.setattr(requests, "Session", lambda: s)` 是本仓库既有测试写法（`tests/test_canvas_client.py:6-33`）
- `errors` 经 JSON 往返后键是字符串，既有 `/api/assignments` 端点已确立该约定
- `#detailBody` 上已有两个 `addEventListener("click", ...)`（app.js:733、911），Task 6 加第三个是同模式而非新范式
- 计划引用的 19 个新顶层名与 5 个新 DOM id（`discussStatus` / `discussGroups` / `homeDdlList` / `homeGradeList` / `quizGroup`）已 grep 全仓库，**均无冲突**（实测：5 个 id 在 index.html 中各出现 0 次）
- `renderDetail` 里 `const c = detailCourse` 在 **app.js:281**，Task 6 把 `pagesHtml` 插在 **322 行**（`const profs`）之前 —— 在 `c` 绑定之后，无 TDZ 风险
- `fmtDue(iso)` 定义在 **app.js:186**，Task 7 直接复用（原稿曾留条件分支，已按实测收敛为确定写法）
- Task 5 的 CSS 用到的 `--err-bg` / `--err-border` 在浅色（app.css:9）与深色（app.css:32）两套里都已定义；`--surface-2` / `--accent-soft` / `--radius-sm` 同样两套齐全
- `#homeTodayList` 的 id 在 Task 8 中保留（`setHomeListCards` 依赖 `$$("#page-home .list-card")`，与新卡片共存）
- 计划复用的 i18n 键 `unread.todo` / `unread.announce` / `status.need_select_course` / `home.loading` / `home.more` 等均已存在于 zh 与 en 两块
- 计划复用的 CSS 类 `.chip` / `.muted` / `.file-path` / `.badge-new` / `.ann-grp` / `.detail-syllabus` / `.home-row` / `.hr-name` / `.sub-label` / `.item-title` 均已定义
