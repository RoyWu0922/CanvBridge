# Canvas 成绩 / 待办 / 日历事件 / AIMS 考试时间表 — Design Spec

> 用户 2026-09-02 在既有 CanvBridge 应用上选定的 4 个新功能（AskUserQuestion 确认：
> 成绩粒度=总评+作业明细；UI 位置=两个新标签页；日历导入=展示+写 Apple 日历）。
> 设计经用户在聊天中逐项确认，本条记录实现细节供计划引用。

## 目标

给 CanvBridge 加 4 个功能，全部复用既有后端模式（FastAPI 同步端点、`{ok, ...}`
返回、Canvas REST 客户端、AIMS Playwright 抓取、AppleScript 日历写入、前端
vanilla JS + i18n 双语文案）。

1. **成绩 Grades**：课程总评 + 每门作业明细（新「成绩」标签页）。
2. **待办 / 即将截止**：Canvas todo（作业/测验）按 已过期/今天/本周/以后 分组
   （新「待办」标签页）。
3. **Canvas 日历事件导入**：在「待办」标签页展示课程日历事件（一次性事件），
   勾选后写 Apple 日历（按 标题+开始时间 去重）。
4. **考试时间表**：AIMS 考试时间表抓取，作为考试块叠加进课表周视图，可写 Apple 日历。

## 数据源（实测确认）

### Canvas REST API（官方，token 从设置取）
- 课程总评：`GET /api/v1/courses?enrollment_state=current_and_invited&per_page=100&include[]=enrollments&include[]=total_scores`
  → 每课 `enrollments[0].grades.current_score` / `.final_score`（None 时课程未给分）。
- 作业明细：`GET /api/v1/courses/{id}/assignments?per_page=100&include[]=submission`
  → 每作业 `submission` = `{score, grade, submitted_at, workflow_state}`；无提交时 `submission` 为 null。
- 待办：`GET /api/v1/users/self/todo`
  → `[{type: "Assignment"|"Quiz"|..., assignment: {id,name,due_at,points_possible,html_url,course_id}, context_name, ...}]`。
  含已过期项。`context_name` 即课程名。
- 日历事件：`GET /api/v1/calendar_events?context_codes[]=course_{id}&...&per_page=100`
  → `[{id, title, start_at, end_at, location_name, html_url, description, context_code}]`。
  `context_code` 形如 `course_123`。**排除 assignment 类事件**（type="assignment" 的日历事件
  与待办重复；只导 type="event" 的一次性事件）。

### AIMS 考试时间表（Playwright，实测 2026-09-02）
- 入口 URL（Site Map「Examination Timetable」实测）：`https://banweb.cityu.edu.hk/pls/PROD/hwsrsett_cityu.P_DispSchd`。
- 页面标题 `Student Examination Timetable`；**只显示当前注册学期**（无学期选择器、
  无 term 查询参数——带 `?term_in=`/`?p_term=` 均 404）。
- 学期标签在标题行：`Student Examination Timetable (Semester A 2026/27)`。
- 当前学期无考试时页面含文本 `Student Examination Timetable is currently not available.`
- 考试行（有考试时）在信息块之后的 `<table style="margin:0">` 数据表里，列结构
  无法在 9 月初抓到（本季考试未排期），解析器**按表头关键词匹配列**（不依赖列序），
  表头关键词：Course/课程、Section、Date/日期、Time/时间、Venue/地点、Room/房间、Seat/座位。

## 后端接口

### canvas_client.py（新增/修改）
- `list_courses(canvas_url, token, include_scores=False)`：加 `include_scores` 参数；
  `True` 时请求带 `include[]=enrollments&include[]=total_scores`，返回每课附加
  `current_score` / `final_score`（数字或 None）。`False` 保持现状（不破坏既有调用）。
- `get_assignments_full(canvas_url, token, course_id)`：返回**全部**作业
  （含已截止，供成绩明细）`[{id, name, due_at, points_possible, html_url, score, submitted}]`；
  `score` 取 `submission.score`（无提交 None），`submitted` = 有 `submitted_at`。
- `get_todo(canvas_url, token)`：调 `users/self/todo`，归一化为
  `[{id, type, title, course_id, course_name, due_at, html_url, points_possible, overdue}]`；
  `overdue` = `due_at < now`。按 due_at 升序（无截止排最后）。
- `get_calendar_events(canvas_url, token, course_ids, start_date, end_date)`：
  逐 course 调 `calendar_events`（`context_codes[]=course_{id}`，`per_page=100`），
  只保留 `type=="event"` 的项，返回 `[{id, title, course_id, start_at, end_at, location_name, html_url}]`。

### main.py（新增 5 个端点 + 1 个共享写事件循环）
- `POST /api/grades {canvas_url, canvas_token, course_ids}` →
  `{ok, courses:[{course_id, course_name, current_score, final_score, assignments:[...]}]}`；
  单课失败进 `errors`（by_course 同款容错）。
- `POST /api/todo {canvas_url, canvas_token}` → `{ok, items:[...]}`。
- `POST /api/calendar_events {canvas_url, canvas_token, course_ids, start_date, end_date}` →
  `{ok, events:[...]}`。
- `POST /api/write_canvas_events {calendar_name, items:[{title,start,end,location,notes}], alert_minutes}`：
  共享一次性事件写入循环，按 标题+开始时间 去重（`find_events` 读回比较），
  `created/exists/errors` 计数 + `items:[{title,status}]`。
- `POST /api/banweb/exams` → `{ok, term_label, exams:[{course, code, section, date, start, end, room, seat}]}`。
- `POST /api/banweb/write_exams {calendar_name, exams:[...], alert_minutes}`：
  同样走共享一次性事件写入循环，标题 `{code} 考试`，日期为考试日、起止为考试时间。

### banweb.py（新增）
- `EXAM_PAGE = BANWEB + "/pls/PROD/hwsrsett_cityu.P_DispSchd"`
- `parse_exam_html(html)`：纯函数。含「not currently available」→ 返回 `[]`；
  否则找考试数据表，按表头关键词映射列，返回考试块列表。
- `get_exams()`：`_on_browser_thread(lambda: _retry_once(_run))`；导航 `EXAM_PAGE`、
  `_require_logged_in`、`page.content()` 交 `parse_exam_html`，并抽学期标签返回
  `(term_label, exams)`。

## 前端

### index.html（新增 2 个标签页）
- 标签行加 `tabTodo`（待办）、`tabGrades`（成绩），tabSchedule 之后。
- `tabTodo` 面板：`todoGroups`（分组列表）+ `todoEvents`（日历事件区）+ 写入条
  （`selTodoCalendar`、`selTodoAlert`、`btnWriteTodoEvents`）。
- `tabGrades` 面板：`gradesArea`。

### app.js
- `switchTab` 对 `tabTodo`/`tabGrades` 首次进入触发数据加载（同 `tabSchedule` 的
  `initScheduleTab` 模式）。
- 待办：`/api/todo` + `/api/calendar_events`（用 `selectedCourses()` 与默认 30 天范围）
  → 分组渲染；写事件按钮走 `/api/write_canvas_events`。
- 成绩：`/api/grades` → 课程卡片（总评 + 作业明细行）。
- 考试：课表 tab 初始化后拉 `/api/banweb/exams`；`renderSchedule` 中考试日期落在
  浏览周时叠加考试块（独立配色 + EXAM 徽标）；写入按钮走 `/api/banweb/write_exams`。
- 语言切换 `applyLang` 重渲所有动态区。

### i18n.js
- 两个语言块各补 tab/状态/徽标键（zh + en 成对）。

## 安全 / 既有约束
- Canvas token 由前端在请求体传入（既有模式），后端不落盘、不返回。
- 前端渲染一律 `esc()` / `escAttr()`。
- AIMS 凭据只在钥匙串，不返回前端（沿用现状）。
- 所有新端点返回 `{ok: true, ...}`；异常返回 `{ok: false, error: str}`。
- 测试：纯函数直测；浏览器/AppleScript 路径 monkeypatch 测端点。
