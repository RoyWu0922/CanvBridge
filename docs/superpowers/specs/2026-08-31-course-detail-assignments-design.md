# 课程详情 + Syllabus AI 总结 + 作业 due 标注 + 教授筛选 — 设计文档

日期：2026-08-31
状态：待用户 review

## 目标

在既有 Canvas 课程助手（公告总结 / 文件 / 课表周视图）之上新增四项功能：

1. **课程详情弹层**：公告总结卡片与课表课程块均可点进一门课的详情，顶部展示课程名 + 教授，下方为 syllabus 与作业列表。
2. **Syllabus AI 总结**：详情弹层内对 syllabus 一键生成中/英总结（复用 OpenAI 兼容 LLM 客户端）。
3. **作业 due 标注**：课表周视图里用和课程块**明显不同的样式**标出未截止作业的 due 日期；点击作业在新标签页打开 Canvas 作业页提交。
4. **教授筛选**：课表页按下拉框按教授过滤课程块。

## 非目标（Out of scope）

- 不做应用内提交作业（代登录、代填写、代上传）。作业提交只提供「新标签页打开 Canvas 作业页」的跳转。
- 不把作业 due 写入苹果日历/提醒事项（用户已确认只标在课表周视图）。
- 不显示已截止作业（用户已确认只看未截止；无截止日期的作业在详情里列出但不上日历）。
- 不改 Banweb 抓取、AppleScript 写入、文件下载的既有逻辑（仅新增一个取主教授的 helper）。
- 不引入前端构建工具 / 框架（保持无构建、静态文件由 FastAPI 提供）。

## 架构

### 数据来源（Canvas API）

| 数据 | 接口 | 取用字段 |
|------|------|---------|
| 单课程（含 syllabus） | `GET /api/v1/courses/:id?include[]=syllabus_body` | `id`、`name`、`syllabus_body`(HTML) |
| 教授 | `GET /api/v1/courses/:id/users?enrollment_type[]=TeacherEnrollment&enrollment_type[]=TaEnrollment&per_page=100` | `name` |
| 作业 | `GET /api/v1/courses/:id/assignments?per_page=100` | `id`、`name`、`due_at`、`points_possible`、`html_url`、`course_id` |
| 课表上的教授 | Banweb 抓取已带的 `meetings[].instr` | 见 `primary_instructor` |

说明：

- `include[]=teachers` 在单课程端点上不可靠，教授改用 users 端点按 enrollment_type 过滤（Teacher + TA）。
- 作业 `html_url` 缺失时后端拼接兜底 `{canvas_url}/courses/{course_id}/assignments/{assignment_id}`。
- syllabus 一律在后端 `strip_html` 成纯文本再下发与送 LLM（前端渲染走 `esc()`，不渲染原始 HTML，保持 XSS 安全）。

## 后端改动

### `backend/canvas_client.py`

新增两个函数：

```python
def get_course(canvas_url, token, course_id) -> dict:
    # course = GET /api/v1/courses/{id}?include[]=syllabus_body
    # teachers = GET /api/v1/courses/{id}/users?enrollment_type[]=TeacherEnrollment&enrollment_type[]=TaEnrollment
    # 返回 {id, name, syllabus_text(str, 已 strip_html), teachers:[name,...]}

def get_assignments(canvas_url, token, course_id) -> list[dict]:
    # GET /api/v1/courses/{id}/assignments?per_page=100
    # 只保留未截止作业：
    #   due_at 为空（无截止日期）→ 保留（详情展示用，不上日历）
    #   due_at 在未来 → 保留
    #   due_at 已过 → 丢弃
    # 返回 [{id, name, due_at, points_possible, html_url}]
```

- 复用 `_paginate` 与既有错误处理（401/403 抛 `CanvasError`）。
- `get_course` 里 syllabus 字段可能缺失（空课程）→ 返回空串。
- `get_assignments` 的 `html_url` 缺失时拼兜底 URL。

### `backend/llm_client.py`

新增 `summarize_syllabus`：

```python
def summarize_syllabus(base_url, api_key, model, course_name,
                       syllabus_text, language="zh") -> str:
    # prompt：目标语言（中/英）输出 syllabus 要点总结，用 _call_chat
    # syllabus_text 过长时截断（约 20000 字符）防 token 超限
    # 失败重试一次；仍失败抛异常（由端点转 ok:false），不静默降级
```

复用 `_call_chat`；不走 JSON 结构化（直接返回总结文本）。

### `backend/banweb.py`

新增纯函数 `primary_instructor(course) -> str`：

- 遍历 `course["meetings"]`，取 `instr` 含 `(P)`（主讲师标记）的第一个；没有 `(P)` 则取第一个非空 `instr`。
- 返回值剥离尾部 `(P)` 标记、去空格；无则返回 `""`。

`get_schedule` 在 `enrich_meetings` 之后为每个课程块追加 `primary_instructor` 字段（与 `enrich_meetings` 追加定位字段同一模式，前端筛选下拉直接取用，不用重复解析）。

### `backend/main.py`

新增三个端点：

| 端点 | 入参 | 返回 |
|------|------|------|
| `POST /api/course_detail` | `{canvas_url, canvas_token, course_id}` | `{ok, course:{id, name, syllabus_text, teachers}}` |
| `POST /api/assignments` | `{canvas_url, canvas_token, course_ids:list[int]}` | `{ok, by_course:{course_id: [assignments]}}`，逐课程 try/except（单门失败不拖垮整批，返回 `{course_id:[], error}`） |
| `POST /api/summarize_syllabus` | `{canvas_url, canvas_token, llm_base_url, llm_api_key, llm_model, course_id, language}` | `{ok, summary}` |

`sync_announcements` 现有返回的每个课程结果补 `course_id` 字段（前端点开详情需要）：

```python
for cid, r in zip(req.course_ids, results):
    r["course_id"] = cid
```

## 前端改动

### 课程详情弹层

- 复用现有 `.modal` 结构新建 `#detailModal`，含 header（课程名 + 教授名 chips）、syllabus 区块、作业区块。
- 打开入口：
  - **公告总结卡片**：`.course-name` 变为可点击（需 `course_id`，来自 sync 结果新增字段）。点击 → `api("course_detail", {course_id})` 拉详情 → 打开弹层。
  - **课表课程块**：块内加一个小「详情」图标按钮（右上角）。点击打开详情；块本体点击仍维持既有「勾选/取消」逻辑。前提是能通过课程代码匹配到 Canvas 课程。
- Banweb→Canvas 匹配 helper（前端）：

  ```js
  // 课程代码（如 CS1315）在 Canvas 课程名里做归一化查找
  function matchCourseByCode(code){
    const norm = s => s.toUpperCase().replace(/\s+/g, "");
    return courseList.find(c => norm(c.name).includes(norm(code))) || null;
  }
  ```

  匹配不到 → 该课表块不显示详情按钮。`courseList` 来自「加载课程」（已在内存）。

- Syllabus 区块：显示 `syllabus_text`（纯文本）；「AI 总结」按钮 → `api("summarize_syllabus", {course_id, ...llm配置})`，请求中按钮 loading 禁用，返回后总结显示在 syllabus 下方；失败显示错误文案。
- 作业区块：未截止作业列表（名称 + due 时间 + 分值），整行是 `<a target="_blank">` 指向 `html_url`（新标签页打开，浏览器里登录 Canvas 提交）。

### 周视图作业 due 标注

- 课表 tab 的 filter-bar 加「加载作业 due」按钮；点击 → `api("assignments", {course_ids: 当前勾选的 Canvas 课程})`。
- 未加载 Canvas 课程时提示「请先加载课程」；结果存模块级 `assignmentMarks`（`{course_id: [assignments]}`）。
- `renderSchedule()` 时，每个未截止且 `due_at` 非空的作业按 `due_at` 的星期几落入对应列，在 day-body 顶部渲染**琥珀色小标记**（课程名 + 作业名 + 截止日期），与课程块（实心色块）样式明显区分（细横条 / 边框色 + 顶部竖线）。
- 标记整块是 `<a target="_blank">` → `html_url`（提交页）。
- 同一个星期列多个作业垂直堆叠；标记不参与勾选/写日历逻辑。

### 教授筛选

- 课表页 filter-bar 加「按教授筛选」`select`：选项 = 课表课程 `primary_instructor(course)` 去重（含「全部教授」），无讲师的课归入「未指定」。
- 选中某教授 → `renderSchedule()` 只渲染匹配课程的块（网格 + 无固定时间区）；不改变 `selected` 集合与 localStorage。
- 作业标注不受教授筛选影响（标注是 Canvas 侧数据，与 Banweb 教授不直接对应）。

### i18n

所有新增文案（按钮、标题、空态、错误、提示）进 `frontend/i18n.js` 的 `zh` / `en` 字典，用 `t()` 引用；`applyLang()` 时重渲详情弹层与课表（若已打开）。

## 后端改动小结

| 文件 | 改动 |
|------|------|
| `backend/canvas_client.py` | 新增 `get_course`、`get_assignments` |
| `backend/llm_client.py` | 新增 `summarize_syllabus` |
| `backend/banweb.py` | 新增 `primary_instructor` |
| `backend/main.py` | 新增 3 端点；`sync_announcements` 结果补 `course_id` |
| `frontend/index.html` | 详情弹层 DOM；课表 filter-bar 新增作业/教授控件 |
| `frontend/app.js` | 详情弹层逻辑；作业标注；教授筛选；代码→课程匹配 |
| `frontend/i18n.js` | 新增文案字典 |

## 测试

- `tests/test_canvas_client.py`：
  - `get_course`：syllabus_body 转纯文本 + teachers 收集（mock `_paginate`）；syllabus 缺失 → 空串。
  - `get_assignments`：只保留未截止（未来 due_at 保留、已过丢弃、空 due_at 保留）；`html_url` 缺失时拼兜底。
- `tests/test_llm_client.py`：`summarize_syllabus` mock `_call_chat` 返回文本；长 syllabus 截断不报错。
- `tests/test_banweb.py`：`primary_instructor` 优先 `(P)`；无 `(P)` 取第一个非空；无讲师 → `""`。
- `tests/test_main.py`：三个新端点成功/失败路径；`sync_announcements` 返回结果带 `course_id`。
- 前端（弹层 / 标注 / 筛选）无自动化测试，手动验证：本地起服务，走通「加载课程 → 同步 → 点课程名开详情 → AI 总结」与「获取课表 → 加载作业 due → 标记出现 → 点标记开 Canvas → 按教授筛选」。

## 风险与注意

- **未接受邀请的课程**：`get_course` / `get_assignments` 可能 403 或返回空。`/api/assignments` 逐课程 try/except 单门失败不拖垮整批；详情弹层失败展示错误而非空白。
- **syllabus 很长**：送 LLM 前截断（约 20000 字符），防 token 超限。
- **作业 `html_url`**：优先取接口返回，缺失时拼接兜底；跳转依赖用户浏览器已有的 Canvas 登录态。
- **教授匹配是显示层面**：不落库、不改 `banwebSchedule` localStorage 结构；老用户已存的预览仍能加载。
- **代码匹配是启发式**：Banweb 代码在 Canvas 课程名里找不到时详情入口隐藏，不报错。
- **作业标记与课表块的点击冲突**：标记是独立 `<a>`，不与课程块勾选逻辑抢事件。
