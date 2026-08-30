# UI 重构 + i18n + 课表周视图 + 课程筛选 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Canvas 课程助手前端重构为多文件、现代化极简专业风格（跟随系统深浅色、去 emoji），加中英切换（含 LLM 总结输出语言），课表改为周视图日历，公告/文件按课程筛选。

**Architecture:** 前端 `index.html` 拆为 `index.html` + `app.css` + `i18n.js` + `app.js` 四文件，`main.py` 挂 `StaticFiles` 提供 `/static`。后端 `llm_client` 按 `language` 生成 `summary`（替换 `summary_cn`），`banweb` 为 meeting 附解析字段供日历落位。前端 i18n 用字典 + `t()`，课表用周视图网格，筛选用单选下拉。

**Tech Stack:** FastAPI + 原生 HTML/CSS/JS（无构建工具）、`playwright`（banweb，只加纯解析字段）、pytest。

**Spec:** `docs/superpowers/specs/2026-08-31-ui-i18n-calendar-design.md`

## Global Constraints

- 语言切换默认 `zh`，存 `localStorage.sc_lang`；切换只影响 UI 文案与新同步的 LLM 总结，不重跑历史总结。
- `summary_cn` 字段全量改名为 `summary`。
- 课表 meeting 新增 `start_min` / `end_min` / `days_list`，原 `time` / `days` / `range` 字段保留不变。
- 深/浅色纯跟随 `prefers-color-scheme`，不做手动开关。
- 前端无构建工具；`/static` 不得遮挡 `/api/*`。
- 保持 `banwebSchedule` localStorage 结构不变（老预览仍可加载）。
- Python 测试跑法统一：`python -m pytest`（项目根目录）。

---

### Task 1: 前端拆分 + 静态目录挂载

**Files:**
- Create: `frontend/app.css`（现有 `<style>` 内容原样迁入）
- Create: `frontend/app.js`（现有 `<script>` 内容原样迁入）
- Create: `frontend/i18n.js`（临时 stub，Task 5 再填字典）
- Rewrite: `frontend/index.html`（仅保留 `<head>`/`<body>` 结构，引用 3 个静态文件）
- Modify: `backend/main.py`（挂 `StaticFiles`）

**Interfaces:**
- Produces: `/static/app.css`、`/static/app.js`、`/static/i18n.js` 可被 GET 访问；`/` 仍返回 `index.html`。
- Produces: 全局函数 `t(key)` 与 `applyLang()`（`i18n.js` stub，Task 5 覆盖），`app.js` 后续任务会调用。

- [ ] **Step 1: 拆分 CSS**

把 `frontend/index.html` 的 `<style>…</style>` 内容整体搬进 `frontend/app.css`（`<style>`/`</style>` 标签本身不搬，只搬内部 CSS）。

- [ ] **Step 2: 拆分 JS**

把 `frontend/index.html` 的 `<script>…</script>` 内容整体搬进 `frontend/app.js`（`<script>`/`</script>` 标签不搬，只搬内部 JS）。

- [ ] **Step 3: 写 i18n.js stub**

`frontend/i18n.js`：

```js
// 文案字典（Task 5 填 zh/en 内容）
const t = (key) => key;
function applyLang() {}
```

- [ ] **Step 4: 重写 index.html**

保留 `<head>` 里的 `<meta>` 与 `<title>`，删掉 `<style>` 与 `<script>`，改成引用静态文件；`<body>` 内的全部标记原样保留（含 `id` 与文本）。关键改动：

```html
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Canvas 课程助手</title>
<link rel="stylesheet" href="/static/app.css">
</head>
```

`<body>` 末尾（原 `<script>` 位置）替换为：

```html
<script src="/static/i18n.js"></script>
<script src="/static/app.js"></script>
```

- [ ] **Step 5: main.py 挂静态目录**

`backend/main.py` 顶部 import 增加 `from fastapi.staticfiles import StaticFiles`，在 `app = FastAPI(...)` 之后、`FRONTEND = ...` 附近加：

```python
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
```

注意 `FRONTEND` 仍指向 `FRONTEND_DIR / "index.html"`，`/` 路由不变。

- [ ] **Step 6: 加路由测试**

`tests/test_main.py` 追加：

```python
def test_index_and_static(monkeypatch):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    for path in ["/static/app.css", "/static/app.js", "/static/i18n.js"]:
        assert client.get(path).status_code == 200
```

- [ ] **Step 7: 运行测试验证**

Run: `python -m pytest tests/test_main.py -v`
Expected: 全部 PASS（含新增 `test_index_and_static`）。

- [ ] **Step 8: 手动冒烟**

Run: `uvicorn backend.main:app --host 127.0.0.1 --port 8000`，浏览器开 `http://127.0.0.1:8000`。
Expected: 页面样式与拆分前一致，Console 无 404（`app.js` 里暂无 `t()` 调用，stub 不破坏任何逻辑）。

- [ ] **Step 9: Commit**

```bash
git add frontend/ backend/main.py tests/test_main.py
git commit -m "refactor: 前端拆分为 4 文件并挂载静态目录"
```

---

### Task 2: llm_client 语言参数 + summary 改名

**Files:**
- Modify: `backend/llm_client.py`
- Modify: `tests/test_llm_client.py`

**Interfaces:**
- Produces: `extract_course_summary(base_url, api_key, model, course_name, announcements, language="zh")` 返回 dict，含 `summary` 字段（不再有 `summary_cn`）。
- Produces: `_build_prompt(course_name, announcements, language="zh")`；`_schema_instructions(language)`。

- [ ] **Step 1: 写失败测试**

`tests/test_llm_client.py` 现有断言 `summary_cn` 的地方全部改为 `summary`，并新增：

```python
def test_extract_returns_summary_field(monkeypatch):
    payload = json.dumps({
        "course_name": "CS 101",
        "summary": "Weekly summary.",
        "calendar_events": [], "reminders": [],
    })
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: payload)
    result = llm_client.extract_course_summary(
        "https://llm/v1", "key", "m", "CS 101", [{"title": "x", "message": "y", "posted_at": ""}])
    assert result["summary"] == "Weekly summary."
    assert "summary_cn" not in result


def test_language_affects_prompt():
    zh = llm_client._build_prompt("CS 101", [{"title": "T", "message": "M", "posted_at": ""}], language="zh")
    en = llm_client._build_prompt("CS 101", [{"title": "T", "message": "M", "posted_at": ""}], language="en")
    assert "Chinese" in zh and "English" not in zh
    assert "English" in en and "Chinese" not in en
```

同时把 `test_extract_fallback_on_persistent_failure` 与 `test_extract_fallback_on_non_dict_json` 里对 `summary_cn` 的断言改为 `summary`。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_llm_client.py -v`
Expected: 新增/改动断言失败（`summary_cn` 尚不存在 / 无 `language` 参数）。

- [ ] **Step 3: 实现**

`backend/llm_client.py` 把 `_SCHEMA_INSTRUCTIONS` 常量改为函数，并让 `_build_prompt`、`extract_course_summary` 携带 `language`：

```python
def _schema_instructions(language: str) -> str:
    lang = "Chinese" if language == "zh" else "English"
    return (
        'Produce a JSON object with EXACTLY this structure:\n'
        '{\n'
        '  "course_name": "<course_name>",\n'
        f'  "summary": "<{lang} summary>",\n'
        '  "calendar_events": [{"title": "...", "start": "YYYY-MM-DDTHH:MM:SS", '
        '"end": "YYYY-MM-DDTHH:MM:00", "location": "...", "notes": "..."}],\n'
        '  "reminders": [{"title": "...", "due_date": "YYYY-MM-DDTHH:MM:SS", "notes": "..."}]\n'
        '}\n'
        'Rules:\n'
        f'- summary: write in {lang}, covering the key points in a few sentences.\n'
        '- calendar_events: ONLY items with a concrete date/time (a class, review session, '
        'exam, office hours). If only a date is given, use 23:59:00 as the end time. '
        'Location in English if mentioned, else "".\n'
        '- reminders: ONLY deadlines/due dates without a start/end period. due_date is the '
        'deadline; default to 23:59:00 if only a date is given.\n'
        '- Titles and notes in English, verbatim from the announcements where possible.\n'
        '- Return [] for calendar_events or reminders if there are none. Never invent events.\n'
        'Return ONLY the JSON object.'
    )


def _build_prompt(course_name: str, announcements: list[dict], language: str = "zh") -> str:
    lines = [
        f'You are an academic assistant. Below are announcements from the Canvas course named "{course_name}".',
        "",
    ]
    for i, a in enumerate(announcements, 1):
        lines.append(f"[{i}] {a.get('posted_at', '')} — {a.get('title', '')}")
        lines.append(a.get("message", ""))
        lines.append("")
    lines.append(_schema_instructions(language))
    return "\n".join(lines)
```

`extract_course_summary` 签名加 `language: str = "zh"`，内部：

- fallback dict 的 `"summary_cn"` → `"summary"`。
- `prompt = _build_prompt(course_name, announcements, language)`。
- `parsed.setdefault("summary_cn", "")` → `parsed.setdefault("summary", "")`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_llm_client.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/llm_client.py tests/test_llm_client.py
git commit -m "feat: LLM 总结按 language 输出并改字段为 summary"
```

---

### Task 3: main.py language 透传

**Files:**
- Modify: `backend/main.py`
- Modify: `tests/test_main.py`

**Interfaces:**
- Consumes: `extract_course_summary(..., language="zh")`（Task 2）。
- Produces: `POST /api/sync_announcements` 请求体可带 `language`，透传给 `extract_course_summary`。

- [ ] **Step 1: 写失败测试**

`tests/test_main.py`：把 `test_sync_announcements` 的 mock 返回值 `"summary_cn"` 改为 `"summary"`，断言 `r.json()["courses"][0]["summary"] == "要点"`。新增：

```python
def test_sync_announcements_passes_language(monkeypatch):
    captured = {}
    monkeypatch.setattr(canvas_client, "get_announcements",
                        lambda u, t, ids, a, b: {5: [{"title": "T", "message": "M", "posted_at": ""}]})
    monkeypatch.setattr(canvas_client, "list_courses", lambda u, t: [{"id": 5, "name": "CS 101"}])
    def fake(base, key, model, name, anns, language="zh"):
        captured["language"] = language
        return {"course_name": name, "summary": "s", "calendar_events": [], "reminders": []}
    monkeypatch.setattr(llm_client, "extract_course_summary", fake)
    body = {"canvas_url": "https://x", "canvas_token": "t", "llm_base_url": "https://llm/v1",
            "llm_api_key": "k", "llm_model": "m", "course_ids": [5],
            "start_date": "2026-08-01", "end_date": "2026-08-31", "language": "en"}
    r = client.post("/api/sync_announcements", json=body)
    assert r.json()["ok"] is True
    assert captured["language"] == "en"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_main.py -v`
Expected: `test_sync_announcements_passes_language` FAIL（`language` 未透传，captured 为空 / mock 不匹配）。

- [ ] **Step 3: 实现**

`backend/main.py` 的 `SyncRequest` 增加字段，`sync` 端点透传：

```python
class SyncRequest(CanvasConfig, LLMConfig):
    course_ids: list[int]
    start_date: str
    end_date: str
    language: str = "zh"
```

`sync` 内 `results.append(...)` 处把 `extract_course_summary(req.llm_base_url, req.llm_api_key, req.llm_model, name_by_id.get(...), anns)` 末尾加 `, language=req.language`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_main.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_main.py
git commit -m "feat: sync_announcements 透传 language"
```

---

### Task 4: banweb 课表 meeting 解析字段

**Files:**
- Modify: `backend/banweb.py`
- Modify: `tests/test_banweb.py`

**Interfaces:**
- Produces: `enrich_meetings(courses: list[dict]) -> list[dict]`，为每个 meeting 追加 `start_min:int` / `end_min:int` / `days_list:list[str]`；无法解析 time 的 meeting 不追加这三字段，原字段不变。

- [ ] **Step 1: 写失败测试**

`tests/test_banweb.py` 追加：

```python
def test_enrich_meetings_parses_time_and_days():
    courses = [{"code": "CS1315", "section": "C01",
                "meetings": [{"time": "12:00 pm - 2:50 pm", "days": "MWF",
                              "room": "MMW 2450", "range": "Aug 31, 2026 - Nov 28, 2026",
                              "instr": "Kenneth LEE"}]}]
    out = banweb.enrich_meetings(courses)
    m = out[0]["meetings"][0]
    assert m["start_min"] == 720
    assert m["end_min"] == 890
    assert m["days_list"] == ["M", "W", "F"]
    assert m["days"] == "MWF"          # 原字段保留


def test_enrich_meetings_skips_unparseable_time():
    courses = [{"code": "CS1315", "section": "C02",
                "meetings": [{"type": "Lecture", "time": "", "days": ""}]}]
    out = banweb.enrich_meetings(courses)
    assert "start_min" not in out[0]["meetings"][0]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_banweb.py -v`
Expected: FAIL（`enrich_meetings` 不存在）。

- [ ] **Step 3: 实现**

`backend/banweb.py` 在 `build_event_specs` 之后加：

```python
def enrich_meetings(courses: list[dict]) -> list[dict]:
    """为每个 meeting 追加 start_min/end_min/days_list 供前端日历落位。

    原 time/days/range 字段不变。无固定时间（time 解析失败）的 meeting 不追加定位字段。
    """
    out: list[dict] = []
    for c in courses:
        c2 = dict(c)
        meetings = []
        for m in c.get("meetings", []):
            m2 = dict(m)
            try:
                (sh, sm), (eh, em) = parse_time_range(m["time"])
                m2["start_min"] = sh * 60 + sm
                m2["end_min"] = eh * 60 + em
                m2["days_list"] = [d for d in m.get("days", "") if d in _WEEKDAY]
            except (BanwebError, KeyError):
                pass
            meetings.append(m2)
        c2["meetings"] = meetings
        out.append(c2)
    return out
```

在 `get_schedule` 的 `_run` 里，`return parse_schedule_html(page.content())` 改为 `return enrich_meetings(parse_schedule_html(page.content()))`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_banweb.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/banweb.py tests/test_banweb.py
git commit -m "feat: 课表 meeting 附 start_min/end_min/days_list 解析字段"
```

---

### Task 5: 前端 i18n 基础设施 + 语言切换

**Files:**
- Rewrite: `frontend/i18n.js`（完整字典 + `t()` + `applyLang()`）
- Modify: `frontend/index.html`（header 加语言切换按钮；静态文案改 `data-i18n` 或 `t()`）
- Modify: `frontend/app.js`（文案改 `t()`；`settings()` 返回 `language`；`sync` 请求带 `language`；`applyLang()` 实现重渲）

**Interfaces:**
- Consumes: `extract_course_summary` / `sync_announcements` 的 `language` 字段（Task 2/3）。
- Produces: 全局 `t(key)`、`applyLang()`、`LANG()`。

- [ ] **Step 1: 写 i18n.js 完整字典**

`frontend/i18n.js`（覆盖全部界面文案，含状态提示与周几/月份；emoji 一律不写进文案）：

```js
const I18N = {
  zh: {
    "app.title": "Canvas 课程助手",
    "range.placeholder": "请选择起止日期",
    "range.invalid": "开始日期晚于结束日期",
    "range.days": "共 {n} 天",
    "btn.settings": "设置",
    "sync.eyebrow": "时间范围",
    "sync.heading": "同步公告并总结",
    "sync.start": "开始日期",
    "sync.end": "结束日期",
    "btn.load_courses": "加载课程",
    "btn.sync": "同步并总结",
    "tab.announce": "公告总结",
    "tab.files": "课程文件",
    "tab.schedule": "课表",
    "announce.filter.label": "事件筛选",
    "announce.filter.show_all": "显示全部公告内事件",
    "announce.filter.from": "从",
    "announce.filter.to": "到",
    "announce.filter.course": "课程筛选",
    "announce.filter.all": "全部课程",
    "btn.write_calendar": "写入选中的日历事件",
    "announce.alert": "提前提醒",
    "btn.write_reminders": "写入选中的提醒",
    "announce.empty": "还没有同步结果。先在上方选择时间范围、勾选课程，点击「同步并总结」。",
    "announce.calendar_events": "日历事件",
    "announce.reminders": "提醒",
    "announce.due": "截止",
    "btn.list_files": "加载文件列表",
    "files.type_filter": "类型过滤",
    "files.placeholder": "pdf, pptx…（留空=全部）",
    "files.filter.course": "课程筛选",
    "files.filter.all": "全部课程",
    "btn.select_all": "全选",
    "btn.download": "下载所选",
    "files.empty": "没有匹配的文件",
    "schedule.login_label": "AIMS 登录",
    "schedule.checking": "检测中…",
    "btn.relogin": "重新登录",
    "schedule.term": "学期",
    "btn.fetch": "抓取课表",
    "schedule.calendar": "写入日历",
    "schedule.alert": "提前提醒",
    "btn.sync_schedule": "同步课表",
    "btn.clear": "清除预览",
    "schedule.empty": "还没有课表。选好学期后点「抓取课表」。",
    "schedule.no_fixed": "无固定时间",
    "settings.title": "设置",
    "settings.close": "关闭设置",
    "settings.canvas_url": "Canvas 实例 URL",
    "settings.canvas_token": "Canvas API Token",
    "settings.llm_base": "LLM Base URL",
    "settings.llm_key": "LLM API Key",
    "settings.llm_model": "LLM 模型名",
    "settings.download_dir": "下载目录",
    "btn.test": "测试连接",
    "settings.calendar": "写入日历",
    "settings.list": "写入提醒列表",
    "btn.refresh_calendars": "刷新日历/列表",
    "badge.created": "已新建",
    "badge.updated": "已更新",
    "badge.exists": "已存在 · 跳过",
    "badge.error": "写入失败",
    "badge.no_time": "无固定上课时间",
    "lang.zh": "中",
    "lang.en": "EN",
    "wd.0": "周一", "wd.1": "周二", "wd.2": "周三", "wd.3": "周四",
    "wd.4": "周五", "wd.5": "周六", "wd.6": "周日",
    "status.connecting": "正在测试连接…",
    "status.connected": "连接成功，共 {n} 门课程",
    "status.connect_fail": "连接失败：",
    "status.need_canvas": "请填写 Canvas URL 和 Token",
    "status.reading_cal": "正在读取日历与提醒列表…",
    "status.cal_fail": "日历读取失败：",
    "status.list_fail": "提醒列表读取失败：",
    "status.refreshed": "已刷新：{c} 个日历、{l} 个提醒列表",
    "status.loading_courses": "正在加载课程…",
    "status.courses_fail": "加载课程失败：",
    "status.courses_loaded": "已加载 {n} 门课程",
    "status.need_select_course": "请先勾选要同步的课程",
    "status.syncing": "正在同步公告并生成总结…",
    "status.sync_fail": "同步失败：",
    "status.sync_done": "同步完成：{n} 门课程已总结",
    "status.need_date": "请先选择开始和结束日期",
    "status.date_invalid": "开始日期不能晚于结束日期",
    "status.need_calendar": "请先刷新并选择要写入的日历",
    "status.no_event": "没有选中要写入的日历事件",
    "status.writing_events": "正在写入 {n} 条日历事件…",
    "status.events_done": "已写入 {a}/{b} 条日历事件",
    "status.write_fail": "写入失败：",
    "status.need_list": "请先刷新并选择提醒列表",
    "status.no_reminder": "没有选中要写入的提醒",
    "status.writing_reminders": "正在写入 {n} 条提醒…",
    "status.reminders_done": "已写入 {a}/{b} 条提醒",
    "status.loading_files": "正在加载文件列表…",
    "status.files_fail": "加载文件失败：",
    "status.files_loaded": "已加载 {n} 门课程的文件",
    "status.downloading": "正在下载 {n} 个文件…",
    "status.download_fail": "下载失败：",
    "status.download_done": "下载完成：成功 {a}，失败 {b}",
    "status.need_term": "请先选择学期",
    "status.fetching": "正在抓取课表…",
    "status.fetch_fail": "抓取失败：",
    "status.fetched": "课表已抓取：{n} 个课程块",
    "status.need_sched_calendar": "请先刷新并选择要写入的日历",
    "status.no_sched": "没有选中要写入的课程",
    "status.syncing_sched": "正在同步 {n} 个课程块…",
    "status.sched_fail": "写入失败：",
    "status.sched_done": "同步完成：新建 {a} · 已存在 {b} · 更新 {c} · 删除 {d} · 失败 {e}",
    "status.sched_cleared": "课表预览已清除",
    "status.opening_login": "正在打开登录窗口…",
    "status.login_opened": "已打开 AIMS 登录窗口，请在新窗口中登录",
    "status.login_fail": "打开登录窗口失败：",
    "status.backend_fail": "无法连接后端，请确认服务已启动",
    "status.parse_fail": "后端响应无法解析（HTTP {s}）",
    "status.busy_default": "处理中…",
    "status.banweb_gw": "无法连接后端：网关检测失败",
    "status.banweb_ok": "已登录 AIMS",
    "status.banweb_need_login": "已退登：点「重新登录」打开登录窗口，登录后自动继续…",
    "status.banweb_opening": "正在打开 Chrome 登录窗口…",
    "status.banweb_unknown": "状态未知，稍后重试…",
    "status.banweb_terms_fail": "读取学期失败：{e}，正在自动重试…",
  },
  en: {
    "app.title": "Canvas Course Assistant",
    "range.placeholder": "Select a date range",
    "range.invalid": "Start date is after end date",
    "range.days": "{n} days",
    "btn.settings": "Settings",
    "sync.eyebrow": "Date range",
    "sync.heading": "Sync & summarize announcements",
    "sync.start": "Start",
    "sync.end": "End",
    "btn.load_courses": "Load courses",
    "btn.sync": "Sync & summarize",
    "tab.announce": "Announcements",
    "tab.files": "Files",
    "tab.schedule": "Schedule",
    "announce.filter.label": "Filter events",
    "announce.filter.show_all": "Show all announcement events",
    "announce.filter.from": "From",
    "announce.filter.to": "To",
    "announce.filter.course": "Course filter",
    "announce.filter.all": "All courses",
    "btn.write_calendar": "Write selected events",
    "announce.alert": "Alert",
    "btn.write_reminders": "Write selected reminders",
    "announce.empty": "No sync results yet. Pick a date range, select courses, then click \"Sync & summarize\".",
    "announce.calendar_events": "Calendar events",
    "announce.reminders": "Reminders",
    "announce.due": "Due",
    "btn.list_files": "Load file list",
    "files.type_filter": "Type filter",
    "files.placeholder": "pdf, pptx… (blank = all)",
    "files.filter.course": "Course filter",
    "files.filter.all": "All courses",
    "btn.select_all": "Select all",
    "btn.download": "Download selected",
    "files.empty": "No matching files",
    "schedule.login_label": "AIMS login",
    "schedule.checking": "Checking…",
    "btn.relogin": "Sign in again",
    "schedule.term": "Term",
    "btn.fetch": "Fetch schedule",
    "schedule.calendar": "Write to calendar",
    "schedule.alert": "Alert",
    "btn.sync_schedule": "Sync schedule",
    "btn.clear": "Clear preview",
    "schedule.empty": "No schedule yet. Pick a term, then click \"Fetch schedule\".",
    "schedule.no_fixed": "No fixed time",
    "settings.title": "Settings",
    "settings.close": "Close settings",
    "settings.canvas_url": "Canvas instance URL",
    "settings.canvas_token": "Canvas API token",
    "settings.llm_base": "LLM Base URL",
    "settings.llm_key": "LLM API key",
    "settings.llm_model": "LLM model name",
    "settings.download_dir": "Download directory",
    "btn.test": "Test connection",
    "settings.calendar": "Write calendar",
    "settings.list": "Write reminder list",
    "btn.refresh_calendars": "Refresh calendars/lists",
    "badge.created": "Created",
    "badge.updated": "Updated",
    "badge.exists": "Exists · skipped",
    "badge.error": "Write failed",
    "badge.no_time": "No fixed time",
    "lang.zh": "中",
    "lang.en": "EN",
    "wd.0": "Mon", "wd.1": "Tue", "wd.2": "Wed", "wd.3": "Thu",
    "wd.4": "Fri", "wd.5": "Sat", "wd.6": "Sun",
    "status.connecting": "Testing connection…",
    "status.connected": "Connected, {n} courses",
    "status.connect_fail": "Connection failed: ",
    "status.need_canvas": "Please fill in Canvas URL and token",
    "status.reading_cal": "Reading calendars and reminder lists…",
    "status.cal_fail": "Failed to read calendars: ",
    "status.list_fail": "Failed to read reminder lists: ",
    "status.refreshed": "Refreshed: {c} calendars, {l} reminder lists",
    "status.loading_courses": "Loading courses…",
    "status.courses_fail": "Failed to load courses: ",
    "status.courses_loaded": "Loaded {n} courses",
    "status.need_select_course": "Please select courses to sync",
    "status.syncing": "Syncing announcements and generating summaries…",
    "status.sync_fail": "Sync failed: ",
    "status.sync_done": "Sync done: {n} courses summarized",
    "status.need_date": "Please choose start and end dates",
    "status.date_invalid": "Start date cannot be after end date",
    "status.need_calendar": "Please refresh and choose a calendar first",
    "status.no_event": "No calendar events selected",
    "status.writing_events": "Writing {n} calendar events…",
    "status.events_done": "Wrote {a}/{b} calendar events",
    "status.write_fail": "Write failed: ",
    "status.need_list": "Please refresh and choose a reminder list first",
    "status.no_reminder": "No reminders selected",
    "status.writing_reminders": "Writing {n} reminders…",
    "status.reminders_done": "Wrote {a}/{b} reminders",
    "status.loading_files": "Loading file list…",
    "status.files_fail": "Failed to load files: ",
    "status.files_loaded": "Loaded files for {n} courses",
    "status.downloading": "Downloading {n} files…",
    "status.download_fail": "Download failed: ",
    "status.download_done": "Download done: {a} ok, {b} failed",
    "status.need_term": "Please choose a term",
    "status.fetching": "Fetching schedule…",
    "status.fetch_fail": "Fetch failed: ",
    "status.fetched": "Schedule fetched: {n} course blocks",
    "status.need_sched_calendar": "Please refresh and choose a calendar first",
    "status.no_sched": "No courses selected",
    "status.syncing_sched": "Syncing {n} course blocks…",
    "status.sched_fail": "Write failed: ",
    "status.sched_done": "Sync done: created {a} · exists {b} · updated {c} · removed {d} · failed {e}",
    "status.sched_cleared": "Schedule preview cleared",
    "status.opening_login": "Opening login window…",
    "status.login_opened": "AIMS login window opened — sign in there",
    "status.login_fail": "Failed to open login window: ",
    "status.backend_fail": "Cannot reach backend — is the server running?",
    "status.parse_fail": "Could not parse backend response (HTTP {s})",
    "status.busy_default": "Working…",
    "status.banweb_gw": "Cannot reach backend: gateway check failed",
    "status.banweb_ok": "Signed in to AIMS",
    "status.banweb_need_login": "Signed out: click \"Sign in again\" to open the login window, then it resumes automatically…",
    "status.banweb_opening": "Opening Chrome login window…",
    "status.banweb_unknown": "Unknown status, retrying…",
    "status.banweb_terms_fail": "Failed to read terms: {e}, retrying…",
  },
};

const LANG = () => localStorage.getItem("sc_lang") || "zh";
const t = (key, vars) => {
  let s = (I18N[LANG()] && I18N[LANG()][key]) ?? (I18N.zh[key] ?? key);
  if (vars) for (const k in vars) s = s.replace("{" + k + "}", vars[k]);
  return s;
};
function applyLang() {
  document.title = t("app.title");
  document.documentElement.lang = LANG() === "zh" ? "zh-CN" : "en";
  $$("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  $$("[data-i18n-ph]").forEach(el => { el.placeholder = t(el.dataset.i18nPh); });
  $$("[data-i18n-aria]").forEach(el => { el.setAttribute("aria-label", t(el.dataset.i18nAria)); });
  const langBtn = $("btnLang"); if (langBtn) langBtn.textContent = LANG() === "zh" ? "EN" : "中";
  renderSummaries(); renderFiles(); renderSchedule();
  refreshPill();
}
```

- [ ] **Step 2: index.html 加语言切换按钮 + data-i18n 标注**

header 的 `.header-right` 内、`range-pill` 之后加：

```html
<button id="btnLang" class="lang-btn" aria-label="切换语言">中</button>
```

静态文本节点改标注（示例，其余同理——把文本写进 `data-i18n`，元素内文本清空）：

```html
<div class="app-title" data-i18n="app.title"></div>
<span class="eyebrow" data-i18n="sync.eyebrow"></span>
<h2 data-i18n="sync.heading"></h2>
<button id="btnLoadCourses" class="btn btn-ghost" data-i18n="btn.load_courses"></button>
<button id="btnSync" class="btn btn-primary" data-i18n="btn.sync"></button>
```

所有按钮/标签/标题里带 emoji 的文本一并移除，只留 `data-i18n`。带 placeholder 的输入框用 `data-i18n-ph`；`title`/`aria-label` 用 `data-i18n-aria`。`#rangePill`、`#banwebStatusText`、`#overlayText` 等动态文本元素保持为空，由 JS `t()` 填充。

- [ ] **Step 3: app.js 接入 i18n**

- `refreshPill()` 里三处硬编码中文改为 `t(...)`：
  - `$("rangePill").textContent = t("range.placeholder")`
  - `⚠️ 开始日期...` → `t("range.invalid")`
  - `` `${short(st)} → ${short(en)} · 共 ${days} 天` `` → `` `${short(st)} → ${short(en)} · ${t("range.days", {n: days})} ``
- `range()`、`setStatus` 相关文案改 `t()`（状态提示见 Task 5 Step 1 已列 key）。
- `api()` 两条错误串改 `t("status.backend_fail")` / `t("status.parse_fail", {s: r.status})`。
- `settings()` 返回值加 `language: LANG()`。
- `$("btnSync").onclick` 的 `api("sync_announcements", { ...s, course_ids:ids, ...rng })` → 加 `language: LANG()`。
- 语言按钮绑定：

```js
$("btnLang").onclick = () => {
  localStorage.setItem("sc_lang", LANG() === "zh" ? "en" : "zh");
  applyLang();
};
```

- 启动处（`loadSettings()` 之前）调用 `applyLang()` 初始化文案与 `<title>`。

- [ ] **Step 4: 手动验证**

Run: `uvicorn backend.main:app --host 127.0.0.1 --port 8000`，开 `http://127.0.0.1:8000`。
Expected:
- 默认中文、无 emoji；点「EN」后全部界面文案与状态提示变英文，`<title>` 变英文；刷新后语言保持。
- 点「同步并总结」请求体带 `language`（Network 面板可见 `"language":"en"`）；返回的 `summary` 为英文。

- [ ] **Step 5: Commit**

```bash
git add frontend/
git commit -m "feat: 前端 i18n 中英切换 + 语言透传"
```

---

### Task 6: 主题重设计 + 深色模式

**Files:**
- Rewrite: `frontend/app.css`

**Interfaces:**
- Consumes: Task 5 新增的 `.lang-btn`、`.schedule-grid`（Task 7 用到）等 class 名需在本任务预留样式。

- [ ] **Step 1: 重写 app.css**

整体替换为极简专业风：`:root` 中性 token，`@media (prefers-color-scheme: dark)` 覆盖。保留现有 class 名（`.card`/`.course-card`/`.btn*`/`.chip`/`.tabs`/`.tab`/`.filter-bar`/`.item`/`.sched-badge`/`.banner`/`.modal`/`.overlay` 等），仅调色与去渐变。核心 token 与新增组件样式如下（其余 class 沿用原结构、只改颜色变量引用）：

```css
:root {
  --bg:#f7f7f8; --surface:#ffffff; --surface-2:#f1f2f4; --border:#e5e6ea;
  --ink:#1a1d23; --muted:#6b7280;
  --accent:#2563eb; --accent-strong:#1d4ed8; --accent-soft:#eef2ff;
  --ok:#15803d; --ok-bg:#eefbf1; --ok-border:#bbe3c8;
  --err:#b91c1c; --err-bg:#fdf0f0; --err-border:#f2c6c6;
  --warn:#b45309; --warn-bg:#fdf6ec; --warn-border:#f0d8b0;
  --shadow-sm:0 1px 2px rgba(16,24,40,.06);
  --shadow-md:0 12px 32px rgba(16,24,40,.14);
  --radius:12px; --radius-sm:8px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#121317; --surface:#1b1d22; --surface-2:#23252b; --border:#2e3138;
    --ink:#e6e8ec; --muted:#9aa1ad;
    --accent:#5b8def; --accent-strong:#7aa2f5; --accent-soft:#1e2b45;
    --ok:#4ade80; --ok-bg:#12301e; --ok-border:#1f5c36;
    --err:#f87171; --err-bg:#33191b; --err-border:#6b2b2d;
    --warn:#fbbf24; --warn-bg:#32270f; --warn-border:#6b551c;
    --shadow-sm:0 1px 2px rgba(0,0,0,.4);
    --shadow-md:0 12px 32px rgba(0,0,0,.5);
  }
}
body { background:var(--bg); color:var(--ink); }
.card { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); box-shadow:var(--shadow-sm); }
.btn-primary { background:var(--accent); color:#fff; }
.btn-primary:hover:not(:disabled) { background:var(--accent-strong); }
.lang-btn { padding:6px 12px; border:1px solid var(--border); background:var(--surface);
  border-radius:8px; font-weight:600; font-size:12.5px; color:var(--ink); cursor:pointer; }
.lang-btn:hover { background:var(--surface-2); }

/* 周视图日历（Task 7 用）：左时间轴 + 7 个相对定位列容器 */
.schedule-grid { display:flex; border:1px solid var(--border); border-radius:var(--radius);
  overflow:hidden; background:var(--surface); }
.time-axis { width:56px; flex:none; border-right:1px solid var(--border); }
.time-axis .corner { height:34px; border-bottom:1px solid var(--border); }
.time-axis .time-label { height:56px; padding:2px 8px 0 0; font-size:10.5px; color:var(--muted);
  text-align:right; font-variant-numeric:tabular-nums; }
.day-col { flex:1; border-left:1px solid var(--border); }
.day-col:first-of-type { border-left:none; }
.day-head { height:34px; line-height:34px; text-align:center; font-size:11px; font-weight:600;
  color:var(--muted); border-bottom:1px solid var(--border); }
.day-body { position:relative; }
.cal-block { position:absolute; left:2px; right:2px; border-radius:6px; padding:4px 6px;
  font-size:11px; line-height:1.3; overflow:hidden; cursor:pointer; border:1px solid transparent; }
.cal-block.sel { border-color:var(--ink); box-shadow:0 0 0 1px var(--ink); }
.cal-block.muted { opacity:.5; cursor:default; }
```

其余颜色引用（`.chip:hover`、`input:focus` 边框、`.tab.active`、`.banner-*`、`.modal-backdrop` 等）统一从旧的紫蓝硬编码改为 `var(--accent)`/`var(--accent-soft)`/`var(--muted)` 等变量。

- [ ] **Step 2: 手动验证深浅色**

Run 后开页面，切换 macOS 系统「外观」浅/深色（或 DevTools 模拟 `prefers-color-scheme: dark`）。
Expected: 页面无紫色渐层、无 emoji，浅色清爽、深色下背景/卡片/文字对比正确，无刺眼白块。

- [ ] **Step 3: Commit**

```bash
git add frontend/app.css
git commit -m "style: 极简专业主题 + 跟随系统深浅色"
```

---

### Task 7: 课表周视图日历

**Files:**
- Modify: `frontend/app.js`（`renderSchedule` 重写为网格；新增 `scheduleColor`/`gridMinutes`/`fmtTime`；`updateSelected` 适配块点击）
- Modify: `frontend/index.html`（`#schedulePreview` 区域改为容器，加「无固定时间」容器）

**Interfaces:**
- Consumes: meeting 的 `start_min` / `end_min` / `days_list`（Task 4）。
- Produces: 周视图网格，块点击切换 `banwebSchedule.selected`（`code:section` 键，写入语义不变）。

- [ ] **Step 1: index.html 调整 schedulePreview 结构**

`#tabSchedule` 内 `#schedulePreview` 保持为一个容器 div；其下新增：

```html
<div id="schedulePreview"></div>
<div id="scheduleNoFixed"></div>
```

（`#scheduleNoFixed` 放网格下方，无固定时间的课归入此处。）

- [ ] **Step 2: 实现日历渲染**

`frontend/app.js` 里 `renderSchedule` 与 `renderCourseBlock` 整体替换为：

```js
const PALETTE = ["#2563eb","#0891b2","#7c3aed","#db2777","#ea580c",
                 "#16a34a","#ca8a04","#dc2626","#4f46e5","#0d9488"];
function scheduleColor(code){
  let h = 0; for (const ch of String(code)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}
function gridMinutes(){
  let lo = 480, hi = 1320;   // 默认 8:00–22:00
  for (const c of banwebSchedule.courses)
    for (const m of (c.meetings || []))
      if (m.start_min != null && m.end_min != null) {
        lo = Math.min(lo, m.start_min); hi = Math.max(hi, m.end_min);
      }
  lo = Math.max(0, Math.floor((lo - 30) / 60) * 60);
  hi = Math.min(1440, Math.ceil((hi + 30) / 60) * 60);
  return { lo, hi };
}
function fmtTime(min){ const h = Math.floor(min/60), m = min%60;
  return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}`; }
const DAY_INDEX = { M:0, T:1, W:2, R:3, F:4, S:5, U:6 };
function renderSchedule(){
  const gridEl = $("schedulePreview"), noFixedEl = $("scheduleNoFixed");
  const data = banwebSchedule;
  if (!data.courses.length) {
    gridEl.innerHTML = `<div class="empty">${esc(t("schedule.empty"))}</div>`;
    noFixedEl.innerHTML = "";
    $("btnWriteSchedule").disabled = true;
    return;
  }
  $("btnWriteSchedule").disabled = false;
  const { lo, hi } = gridMinutes();
  const HOUR_PX = 56, PX_PER_MIN = HOUR_PX / 60;
  const rows = Math.round((hi - lo) / 60);
  // 每列的块 HTML
  const colBlocks = Array.from({length:7}, () => "");
  const noFixed = [];
  for (const c of data.courses) {
    const key = c.code + ":" + c.section;
    const color = scheduleColor(c.code);
    const selected = banwebSchedule.selected.includes(key);
    const res = banwebSchedule.results[key];
    let placed = false;
    for (const m of (c.meetings || [])) {
      if (m.start_min == null || m.end_min == null) continue;
      placed = true;
      for (const d of (m.days_list || [])) {
        const idx = DAY_INDEX[d];
        if (idx == null) continue;
        const top = (m.start_min - lo) * PX_PER_MIN;
        const hgt = Math.max(20, (m.end_min - m.start_min) * PX_PER_MIN);
        const badge = res ? schedBadge(res) : "";
        colBlocks[idx] += `<div class="cal-block${selected ? " sel" : ""}" data-key="${esc(key)}"
          style="top:${top}px;height:${hgt}px;background:${color}">
          <div style="font-weight:600;color:#fff">${esc(c.code)} ${esc(c.section)}</div>
          <div style="color:rgba(255,255,255,.9)">${fmtTime(m.start_min)}–${fmtTime(m.end_min)}</div>
          ${m.room ? `<div style="color:rgba(255,255,255,.8)">${esc(m.room)}</div>` : ""}
          ${badge}</div>`;
      }
    }
    if (!placed) noFixed.push(c);
  }
  // 时间轴
  let axis = `<div class="time-axis"><div class="corner"></div>`;
  for (let r = 0; r < rows; r++) axis += `<div class="time-label">${fmtTime(lo + r * 60)}</div>`;
  axis += `</div>`;
  // 7 个列容器（day-body 相对定位，块绝对定位叠在其上）
  let cols = "";
  for (let d = 0; d < 7; d++) {
    cols += `<div class="day-col"><div class="day-head">${t("wd."+d)}</div>
      <div class="day-body" style="height:${rows * HOUR_PX}px">${colBlocks[d]}</div></div>`;
  }
  gridEl.innerHTML = `<div class="schedule-grid">${axis}${cols}</div>`;
  noFixedEl.innerHTML = noFixed.length
    ? `<div class="sub-label">${t("schedule.no_fixed")}</div>` +
      noFixed.map(c => {
        const key = c.code + ":" + c.section;
        return `<div class="course-card" style="opacity:.6">
          <label class="check" style="border:none;padding:0;background:transparent">
            <input type="checkbox" data-key="${esc(key)}" disabled> ${esc(c.code)} ${esc(c.section)} · ${esc(c.course)}
          </label>
          <span class="sched-badge muted">${t("badge.no_time")}</span></div>`;
      }).join("")
    : "";
}
```

- [ ] **Step 3: 选择交互**

块点击切换选中（`banwebSchedule.selected` 成为唯一选择来源）：

```js
$("schedulePreview").addEventListener("click", (e) => {
  const blk = e.target.closest(".cal-block");
  if (!blk) return;
  const key = blk.dataset.key;
  const sel = new Set(banwebSchedule.selected);
  if (sel.has(key)) sel.delete(key); else sel.add(key);
  banwebSchedule.selected = [...sel];
  saveBanweb(); renderSchedule();
});
```

删除原 `updateSelected`（不再从 DOM checkbox 读选中）。`$("btnWriteSchedule").onclick` 里读选中的那段：

```js
const selected = [...document.querySelectorAll("#schedulePreview .check input:checked")].map(i => i.dataset.key);
```

改为：

```js
const selected = banwebSchedule.selected;
```

（`#scheduleNoFixed` 的 checkbox 为 `disabled` 且仅供展示，不参与选择。）原 `$("#schedulePreview").addEventListener("change", ...)` 委托删除，改为上面的 `click` 委托。

- [ ] **Step 4: 手动验证**

Run 后进「课表」tab，抓取课表。
Expected: 课程按周几+时间落成彩色块，同一课程同色；点块可选中/取消（高亮描边）；无固定时间课出现在下方灰区且不可选；写入仍按 `code:section` 生效。

- [ ] **Step 5: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat: 课表周视图日历"
```

---

### Task 8: 课程筛选

**Files:**
- Modify: `frontend/index.html`（公告/文件 tab 各加一个课程筛选下拉）
- Modify: `frontend/app.js`（`renderSummaries` / `renderFiles` 读取筛选值；下拉填充）

**Interfaces:**
- Consumes: `summaryResults`（字段 `course_name`）、`fileCourses`（字段 `name`）。
- Produces: 两个 `select`（id `selAnnounceCourse` / `selFileCourse`），默认「全部课程」，切换后仅重渲对应区域。

- [ ] **Step 1: index.html 加下拉**

公告 tab 的 `.filter-bar` 内、文件 tab 的 `.row` 内各加：

```html
<label class="field" data-i18n="announce.filter.course"><select id="selAnnounceCourse"></select></label>
```

```html
<label class="field" data-i18n="files.filter.course"><select id="selFileCourse"></select></label>
```

（`label` 的文本节点由 `data-i18n` 填充；`select` 内选项由 JS 动态生成。）

- [ ] **Step 2: app.js 填充下拉**

```js
function fillCourseFilter(selId, names){
  const sel = $(selId); const prev = sel.value;
  sel.innerHTML = "";
  const all = document.createElement("option");
  all.value = ""; all.textContent = t(selId === "selAnnounceCourse" ? "announce.filter.all" : "files.filter.all");
  sel.appendChild(all);
  [...new Set(names.filter(Boolean))].forEach(n => {
    const o = document.createElement("option"); o.value = o.textContent = n; sel.appendChild(o);
  });
  sel.value = prev && [...sel.options].some(o => o.value === prev) ? prev : "";
}
```

- [ ] **Step 3: renderSummaries / renderFiles 接筛选**

`renderSummaries` 开头加：

```js
const courseSel = $("selAnnounceCourse").value;
fillCourseFilter("selAnnounceCourse", summaryResults.map(c => c.course_name));
let src = summaryResults;
if (courseSel) src = summaryResults.filter(c => c.course_name === courseSel);
```

并把 `displayResults = summaryResults.map(...)` 改为基于 `src`：`displayResults = src.map((c) => { const orig = summaryResults.indexOf(c); return { ...c, _orig: orig, calendar_events: (c.calendar_events||[]).filter(e=>dayWithin(e.start,f)), reminders: (c.reminders||[]).filter(e=>dayWithin(e.due_date,f)) }; })`；渲染处用 `c._orig` 替代原来的 `ci`。

`renderFiles` 开头加：

```js
const courseSel = $("selFileCourse").value;
fillCourseFilter("selFileCourse", fileCourses.map(c => c.name));
const shown = fileCourses
  .filter(c => !courseSel || c.name === courseSel)
  .map(c => ({...c, files:(c.files||[]).filter(f => { ...原有类型过滤... })}));
```

（原有类型过滤逻辑保留在 `.map` 内。）

- [ ] **Step 4: 绑定事件**

```js
$("selAnnounceCourse").addEventListener("change", renderSummaries);
$("selFileCourse").addEventListener("change", renderFiles);
```

- [ ] **Step 5: 手动验证**

Run 后：公告同步出多门课后，切「课程筛选」到某门课 → 只显示该课卡片；文件 tab 同理；切回「全部课程」恢复。

- [ ] **Step 6: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat: 公告/文件按课程筛选"
```

---

## 验收清单（全量跑一遍）

```bash
python -m pytest
```

预期全部 PASS。然后 `uvicorn backend.main:app --host 127.0.0.1 --port 8000`，浏览器逐项确认：

1. 无 emoji，界面极简专业，跟随系统深浅色。
2. 中英切换即时生效（含标题、状态提示、周几），刷新保持；新同步的 `summary` 用对应语言。
3. 课表为周视图日历，块点击选中、无固定时间课独立灰区，写入正常。
4. 公告/文件可按课程筛选。
