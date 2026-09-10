# Canvas 内容扩展 + 首页仪表盘设计稿

> 状态：待评审（字段表已按真实实例实测校准）
> 上一稿：`2026-09-10-ui-shell-redesign-design.md`（壳层化重构，已合并进 main）

## 1. 背景

壳层化重构（`060598a`）交付了左侧 8 项导航 + 卡片式首页 + 深空玻璃暗色主题。但 Canvas 侧
目前只消费 9 个接口，**三块学生日常最需要的内容完全看不见**：

- **测验 / 线上考**：Canvas 的 quiz **不在 `assignments` 里**，走独立端点。现在有截止时间也不知道。
- **课程 Pages（wiki）**：很多课的内容只放 Pages 不进 Modules，**整块是盲区**。
- **讨论区**：新帖、需回复的帖看不到。
- **Planner**：`/planner/items` 是 Canvas 给的聚合流（作业 + 测验 + 讨论 + 日历），比现在首页拼三处更准。

同时首页需要从「统计卡 + 两张列表」升级为**仪表盘网格**，玻璃质感进一步加强。

## 2. 目标

1. 打通 4 个新数据源（quizzes / pages / discussion_topics / planner），带测试。
2. 把 4 个数据源**按信息架构落位**，不靠堆菜单项。
3. 首页升级为仪表盘网格（统计卡 + 今日课程卡组 + 3 张速览卡）。
4. 玻璃质感增强（更透的底 + 顶部内高光 + 更强模糊与投影）。
5. 删除侧栏 8 个 emoji 图标，改纯文字。

## 3. 非目标

- 不做作业提交、不做回复讨论（只读）。
- 不做 `conversations`（站内信）—— 权限口径不明，本轮不碰。
- 不改 AIMS/Banweb 那一侧。
- 不引入构建步骤、不用 ES 模块（沿用上一稿约束）。

## 4. 信息架构落位（本稿最关键的决定）

四个数据源**都不新增顶级菜单项**，只有讨论区例外。理由：侧栏从 8 项涨到 12 项会让菜单失去
「一眼扫完」的价值。

| 数据源 | 落位 | 理由 |
|---|---|---|
| 课程 Pages | **课程详情弹层**的第三个板块（现有：Modules / 文件） | Pages 是课程内容，天然属于课程详情，不属于顶层导航 |
| Planner | **不新增界面**，只替换首页「本周 DDL」的数据源 | 它是更准的数据来源，不是新功能。**待办页不在本轮范围内**，沿用现有 `/api/todo` —— 它已能用，改它只增风险不增价值 |
| 测验 Quizzes | **课表页的考试区**，与考试红块并列 | quiz 有截止时间，天然属于「考试 / 截止」这一类 |
| 讨论区 | **新增「讨论」页**（侧栏 8 → 9 项） | 独立的互动流，无处可挂 |

新页面容器 id：`page-discuss`，`data-page="discuss"`，侧栏位置排在「公告」之后，未读徽章
沿用现有 `.nav-badge` 机制。

## 5. 后端设计

### 5.0 数据源（实测确认）

本稿的字段表**不是照 Canvas 文档写的，是拿真实凭据对着实例探过的**。探针于 2026-09-10 打
`https://canvas.cityu.edu.hk`（`GET /api/v1/users/self` 确认为在读学生账号，11 门课），
**全程只读**，覆盖 `70488 / 70699 / 70800 / 70830 / 71134 / 71247` 六门课。

**实测推翻的四处文档假设**（照文档写会静默出错）：

| # | 文档 / 直觉 | 实测 | 若照文档写的后果 |
|---|---|---|---|
| 1 | 讨论区回复数字段叫 `replies_count` | 真实字段是 **`discussion_subentry_count`** | 回复数恒为空（与上一轮 `renderHomeToday()` 的字段臆造同类） |
| 2 | planner 只有 assignment / quiz / discussion / calendar 四类 | 还大量存在 **`announcement`**（88 条里 20 条） | 首页「本周 DDL」混入 20 条公告 |
| 3 | planner 的 `html_url` 可直接打开 | 是**相对路径** `/courses/…` | `openExternal` 打不开 |
| 4 | `submissions` 键时有时无 | **键恒在**，值是 `false` 或对象 | 判空写错会把每一项都当「有提交状态」 |

**实测确认文档是对的两处**：`pages` 列表响应确实不含正文；`plannable_date` 是每种类型都存在
的权威日期字段（88/88 命中）。

**探不到的**：`wiki_page` / `planner_note` 在本实例上**一条都没有**（无样本）。这两类保留
兜底分支，但不当作主路径，也不标「已验证」。

### 5.1 `backend/canvas_client.py` 新增 5 个函数

全部复用现有 `_paginate`（自带 `Link` 头翻页、401 抛错）。

```python
def get_quizzes(canvas_url, token, course_id) -> list[dict]
```
`GET /api/v1/courses/{course_id}/quizzes?per_page=100`
返回 `[{id, title, due_at, lock_at, points_possible, quiz_type, time_limit,
question_count, html_url, published}]`。

- **实测：未启用「测验」工具的课程返回 `404 {"message":"That page has been disabled for
  this course"}`** —— 6 门课里 3 门如此。**这是常态而非异常**，调用方必须当「该课无测验」
  处理，不能让一门课的 404 拖垮整批请求。
- 原始 `due_at` / `lock_at` / `time_limit` 无值时是 **`null`**（实测），归一化统一转空串
  （与 `get_todo` 同惯例）。
- 排序：`due_at` 升序，无截止排最后。
- 只保留 `published` 为真的项。
- 实测样本：`70488 / 70699 / 71247` → 404；`71134` → 200 且 0 条；`70800` → 1 条
  （`quiz_type="practice_quiz"`，`due_at=null`）；`70830` → 14 条（`quiz_type="assignment"`，
  有 `due_at` 与 `lock_at`）。

```python
def get_discussion_topics(canvas_url, token, course_id) -> list[dict]
```
`GET /api/v1/courses/{course_id}/discussion_topics?per_page=100&order_by=recent_activity`
返回 `[{id, title, posted_at, last_reply_at, author, replies_count, unread_count,
read_state, pinned, locked, require_initial_post, html_url}]`。

- **回复数字段是 `discussion_subentry_count`，不是 `replies_count`**（实测）。输出仍叫
  `replies_count`（前端契约），但取数必须取对。
- `author` 取 `topic.author.display_name`，**必须先按 `isinstance(topic.get("author"), dict)`
  守卫**：实测 `71134` 的 `author` 是**空数组 `[]`**，直接下标会 `TypeError` 打死整批。
  `display_name` 本身也可能是 `null`（实测）→ `or ""`。
- **`read_state` 与 `unread_count` 是两个独立维度，不是同一件事**（实测）：
  `read_state` 是**根帖**的已读状态，`unread_count` 是**未读回复数**。同一门课里既存在
  `read_state="read"` 且 `unread_count=8` 的（根帖读了、8 条回复没读），也存在
  `read_state="unread"` 且 `unread_count=0` 的（根帖没读、无人回复）。
  **两者都原样输出**，未读口径由前端决定（见 §6.2）。
- `read_state` 缺失按 `"read"` 处理；`unread_count` 缺失按 `0` 处理。
- **过滤 `is_announcement` 为真的项** —— 公告有独立页面与接口，不该混进讨论页（实测全为
  False，但接口确实给了这个字段，属于迟早会踩的坑）。
- 排序：`pinned` 优先；其次 `last_reply_at` 降序，为 `None` 时退到 `posted_at`；
  **两者都为 `None` 时排在最后**（实测 `71134` 有 9 条 `posted_at=None` 的已发布帖，
  不定义回退会让排序不稳定）。

```python
def get_pages(canvas_url, token, course_id) -> list[dict]
```
`GET /api/v1/courses/{course_id}/pages?per_page=100`
返回**列表**（**不含正文**）`[{url, title, updated_at, published, front_page, html_url}]`。

- 正文由下面的 `get_page_body` 单独取 —— 列表阶段拉全部正文是 O(N) 次额外请求，不可接受。
- **实测确认列表响应确实不含 `body`**；上述 6 个字段全部存在（另有 `page_id` / `created_at` /
  `editing_roles` / `hide_from_students` / `locked_for_user` / `publish_at` / `todo_date`
  本轮不用）。
- 同 quizzes，未启用 Pages 工具的课程返回 `404`（实测 3/6），按「该课无页面」处理。
- 只保留 `published` 为真的项。

```python
def get_page_body(canvas_url, token, course_id, page_url) -> dict
```
`GET /api/v1/courses/{course_id}/pages/{page_url}`
返回 `{url, title, body_text, updated_at, html_url}`。

- 正文原始字段是 `body`，**实测是 HTML 片段**（`<p>Adapted from <a href="…">…`）→ 必须过
  `strip_html()` 产出 `body_text`。两段处理各司其职：`strip_html()` 去标签，前端 `esc()`
  防注入，**都不能省**。
- 实测该端点的 `html_url` 是**绝对 URL**（与 planner 的相对路径不同，别记混）。

```python
def get_planner_items(canvas_url, token, start_date, end_date) -> list[dict]
```
`GET /api/v1/planner/items?start_date=&end_date=&per_page=100`
归一化为 `[{id, type, title, course_id, course_name, date, submitted, html_url}]`。

**归一化必须按 `plannable_type` 分别处理**（本稿最大的实现陷阱，且**实测已推翻文档**）：

| `plannable_type` | 标题来源 | 日期来源 | `submissions` 实测值 | 是否收入 |
|---|---|---|---|---|
| `assignment` | `plannable.title` | `plannable_date`，退 `plannable.due_at` | 对象 | ✅ |
| `quiz` | `plannable.title` | `plannable_date`，退 `plannable.due_at` | 对象 | ✅ |
| `discussion_topic` | `plannable.title` | `plannable_date`，退 `plannable.todo_date` | `false` | ✅ |
| `calendar_event` | `plannable.title` | `plannable_date`，退 `plannable.start_at` | `false` | ✅ |
| **`announcement`** | `plannable.title` | `plannable_date`（= **发布时间**） | `false` | ❌ **丢弃** |
| `wiki_page` | `plannable.title` | `plannable_date` | `false` | ✅（本实例无样本） |
| `planner_note` | `plannable.title` | `plannable_date`，退 `plannable.todo_date` | `false` | ✅（本实例无样本） |
| 其它未知类型 | `plannable.title` | `plannable_date` | 任意 | ✅ 走兜底 |

- **`announcement` 必须显式丢弃**（实测 88 条里 20 条）。它的 `plannable_date` 是**公告发布
  时间**，不是截止时间；混进「本周 DDL」就是纯噪音。公告归公告页，不归 DDL。
- **`plannable_date` 是每种类型都存在的权威日期字段**（实测 88/88）。兜底链按上表，
  全缺则**丢弃该项**（无日期的聚合项对「本周 DDL」无意义）。
- **`submissions` 键恒在**，值是 **`false` 或对象**（实测 35 : 39）。判定必须写
  `isinstance(item.get("submissions"), dict)`；写成 `if "submissions" in item` 会让
  **每一项**都被当成「有提交状态」。
- `submitted` 三态：`isinstance(submissions, dict)` 时取 `bool(submissions.get("submitted"))`，
  否则 `None`（「未交」与「不适用」必须可区分）。
- `type` 直接透传 `plannable_type`，前端按它决定措辞。
- `course_name` 取 `context_name`（实测存在，如 `"DSC1001_GE1356 Introduction to Data
  Science"`），缺失给空串；`course_id` 取顶层 `course_id`；`context_type` 实测恒为
  `"Course"`，本轮不透传。
- **`html_url` 是相对路径**（实测 `/courses/72355/discussion_topics/628563`）→ **必须用
  `canvas_url` 补成绝对 URL**，否则前端 `openExternal` 打不开。
- **`id` 取顶层 `plannable_id`**（不是 `plannable.id`；两者实测同值，但顶层才是契约）。
- **不按日期过滤**：Canvas 的 `start_date`/`end_date` 参数实测生效（传范围 74 条 vs 不传
  88 条），但结果里**仍含过去条目**（实测 23 条早于今天）。「本周」是前端展示口径，后端只做
  归一化，不过滤 —— 否则口径被焊死在 API 里，换个页面想复用它就得改后端。

### 5.2 `backend/main.py` 新增 5 个端点

严格沿用现有约定：**不抛 `HTTPException`**，一律 `try/except` 返回
`{"ok": False, "error": str(exc)}`；批量端点用 `by_course` + `errors` 做**逐课程错误隔离**。

| 端点 | 请求体 | 响应 |
|---|---|---|
| `POST /api/quizzes` | `{canvas_url, canvas_token, course_ids}` | `{ok, by_course: {cid: [quiz]}, errors: {cid: msg}}` |
| `POST /api/discussions` | `{canvas_url, canvas_token, course_ids}` | `{ok, by_course: {cid: [topic]}, errors: {cid: msg}}` |
| `POST /api/pages` | `{canvas_url, canvas_token, course_id}` | `{ok, pages: [...], error?}` |
| `POST /api/page_body` | `{canvas_url, canvas_token, course_id, page_url}` | `{ok, page: {...}, error?}` |
| `POST /api/planner` | `{canvas_url, canvas_token, start_date, end_date}` | `{ok, items: [...], error?}` |

- `quizzes` / `discussions` 是**多课程**（课表页 / 讨论页一次拉全部已选课）→ 用 `by_course`。
  **实测中约一半课程会返回 404**（工具未启用），所以「逐课程隔离」不是防万一，是常规路径。
- `pages` / `page_body` 是**单课程**（课程详情弹层一次只看一门）→ 用扁平 `error`，与
  `course_detail` 一致。
- `planner` 单次调用无课程维度 → 扁平 `error`。

Pydantic 模型复用 `CanvasConfig` 基类，新增 `CoursesRequest`（`CanvasConfig` + `course_ids`）、
`PageBodyRequest`（`CourseDetailRequest` + `page_url`）。

## 6. 前端设计

### 6.1 文件划分（沿用现有四个文件，不新增）

| 文件 | 本轮改动 |
|---|---|
| `frontend/index.html` | 加 `#page-discuss` 容器；首页改仪表盘网格；删侧栏 emoji；课程详情弹层加 Pages 挂载点；课表页加测验挂载点 |
| `frontend/app.js` | 加 5 个 `loadXxx` + 5 个 `renderXxx`；`renderDetail()` 加 Pages 板块；`initScheduleTab` 调 `loadQuizzes` |
| `frontend/shell.js` | `PAGES` 加 `"discuss"`；`PAGE_INIT` 加 `discuss`；首页仪表盘渲染函数 |
| `frontend/i18n.js` | 全部新增文案的 `zh` + `en` |
| `frontend/app.css` | 仪表盘网格 + 玻璃质感增强 + 删 `.nav-ico` 规则 |

**不新增 js 文件**：四个文件的加载顺序约束（`i18n.js → util.js → app.js → shell.js`，
普通 `<script>`，经典脚本共享全局词法环境）保持不变。

### 6.2 讨论页

- 容器：`#page-discuss`，内部 `#discussStatus` / `#discussGroups`（按课程分组）。
- 首次进入由 `PAGE_INIT.discuss` 触发 `initDiscussTab()` → `loadDiscussions()`。
- 每门课一组，每条显示：标题、作者、回复数、最后回复时间、未读标记。
- 未读徽章：`refreshBadges()` 增加第三项 `setNavBadge("discuss", countUnreadDiscussions())`。
  **`countUnreadDiscussions()` 的口径是「根帖未读 **或** 有未读回复」**，即
  `read_state === "unread" || unread_count > 0`。只数 `read_state` 会漏掉「根帖已读但回复
  没读」的帖子 —— 实测存在 `read_state="read"` 而 `unread_count=8` 的条目，那恰恰是最需要
  提醒用户去看的一类。**首次加载讨论之前不显示徽章**（本稿不为讨论新增启动期后台预取 ——
  公告与待办有 `bgFetch…`，讨论没有，也不加）。
- 列表里每条同时展示 `replies_count`（回复数）与 `unread_count`（未读回复数）；未读标记用
  上面同一个口径，与徽章保持一致。
- 某门课失败（含 404）只在该组显示一行提示，其余课程照常分组渲染。
- **只读**：点条目走 `html_url` 交系统浏览器打开，不做站内回复。

### 6.3 课程详情加 Pages 板块

`renderDetail()`（`app.js:279`）当前产出 Modules 与文件两块。新增第三块「页面」：

- 展开课程详情时拉 `/api/pages`，失败只让该板块显示错误行，不拖垮 Modules。
- 每条可展开；**展开时才**请求 `/api/page_body` 取正文（懒加载），结果缓存在内存
  （`pageBodyCache`），同一页只拉一次。
- `pageBodyCache` 的键为 `` `${course_id}:${page_url}` ``，**会话内不失效** —— 详情弹层反复
  开合同一课程不重拉；刷新页面即清空（内存态，不落 localStorage）。
- 正文以纯文本渲染（`body_text` 经 `esc()` 后插入），**不注入 Canvas 返回的原始 HTML**。

### 6.4 课表页加测验

- `initScheduleTab()`（`app.js:1732`）在 `loadExams()` 之后调 `loadQuizzes()`。
- 模块级 `let canvasQuizzes = null` 承接结果（**注意**：与 `banwebExams` 同层，须确认不与
  其它文件的顶层 `const/let` 撞名）。
- 渲染进考试区下方的独立分组，样式复用考试红块的低饱和变体（蓝/紫系，与 AIMS 考试的红块区分）。
- Banweb 未登录也要能看测验（测验来自 Canvas，与 AIMS 无关）。
- **未启用测验工具的课程会 404**（实测 6 门里 3 门），走 `by_course.errors` **静默跳过** ——
  不显示错误行，那只是该课没有测验，不是故障。全部课程都无测验时显示空态文案。

### 6.5 首页仪表盘

```
┌─ 统计卡 ×4 ────────────────────────────┐   未读公告 / 今日课程 / 本周 DDL / 本学期考试
├─ 今日课程（每节一张小卡，横向网格）────┤   无课 → 空态卡
├─ 速览卡 ×3 ────────────────────────────┤   最新公告 / 本周 DDL / 成绩
└────────────────────────────────────────┘
```

- 统计卡：大数字 + 标签 + 跳转（`data-goto`）。
- 今日课程：**每节课一张小卡**（起止时间 / 课程号 / 教室），横向 grid，超宽自动换行。
  数据源仍是本地 `banwebSchedule` 缓存（无需网络）。
- 速览卡 ×3：最新公告（现 `homeAnnounceList`）、本周 DDL（**改用 `/api/planner`**）、成绩。
  - **成绩卡的取数写死**：只读内存里已有的成绩结果（用户访问过成绩页后才有），否则显示 `—`。
    **首页启动阶段不得为它发起 `/api/grades` 请求** —— 该端点会逐课程拉全部作业，代价与首页
    其余卡不在一个量级，放上首页会显著拖慢首屏。
- **「本周 DDL」数据源从 `/api/calendar_events` 换成 `/api/planner`** —— 覆盖更全（含 quiz、
  讨论、wiki 页），且自带 `submitted` 状态可排除已交项。
  - 计入条件（三者同时满足）：`date` 落在「今天起 7 天内」、`submitted !== true`、
    `type !== "announcement"`。
  - `type !== "announcement"` 是**冗余保险** —— 后端已丢弃（§5.1），前端再挡一道，因为这类
    噪音一旦漏进来就是几十条，且肉眼很难立刻看出是公告。
  - `submitted === null`（不适用，如日历事件、讨论）**计入** —— 它没有「交」这个动作，
    不等于已完成。
- 会话内只拉一次的逻辑（`homeLoaded`）与「课程未加载则不拉」的门禁（`shell.js:106`）**保留**。

### 6.6 玻璃质感增强

统一改 `.glass-card` / `.stat-card` / `.set-card` 等玻璃选择器组：

| 属性 | 现在 | 改为 |
|---|---|---|
| `background` | `var(--panel)` | 更透的一层（token 改值，不新增） |
| `border` | 1px 四面 `var(--panel-border)` | 顶部内高光 `inset 0 1px 0 <亮色>` + 四面细边 |
| `backdrop-filter` | blur + saturate（现有 token） | blur 加大、saturate 提高（token 改值） |
| `box-shadow` | `var(--shadow-sm)` | 更深的外投影，悬浮态再深一档 |

**约束**：沿用现有 token 名只改值（与上一稿同规矩）；深浅两套主题都要可读 —— 浅色主题下面板
**不得变透明或隐形**（这是上一轮的头号视觉风险，见上一稿 §5.1）。

### 6.7 侧栏去图标

- 删除 `index.html` 8 个 `.nav-item` 内的 `<span class="nav-ico">…</span>`。
- 删除 `app.css` 的 `.nav-ico` 规则；调整 `.nav-item` 左内边距使文字不贴边。
- **保留** `.nav-badge` 未读徽章（功能，非装饰）。

### 6.8 i18n

所有新增文案**同时补 `zh` 与 `en`**，键集必须完全一致且无空值（校验命令见上一稿 Task 8
Step 3，含空串检测）。新增文案必须落在下列命名空间内，**完整键名清单由实施计划钉死**，
本稿只定命名空间与含义：

| 命名空间 | 覆盖 |
|---|---|
| `nav.discuss` | 侧栏第九项 |
| `discuss.*` | 页标题 / 空态 / 未加载 / 加载失败 / 单门失败 / 回复数 / 作者 / 未读 |
| `courses.pages*` | 板块名 / 空态 / 展开 / 加载中 / 正文失败 |
| `schedule.quiz*` | 分组名 / 空 / 截止 / 题数 / 限时 |
| `home.*` | 速览卡标题 / 分数 / 今日无课 / 课程卡标签 |

**禁止**在 JS 里硬编码任何面向用户的字符串（含 `t()` 之外的英文常量）—— 上一稿已确立
「动态文案一律内联 `t()`，不挂 `data-i18n`」的规矩，本轮沿用。

## 7. 错误处理

沿用现有机制，不新增全局范式：

| 场景 | 行为 |
|---|---|
| 某门课的 quizzes / discussions **404（工具未启用）** | **静默跳过**，不算错误行（实测约一半课程如此） |
| 某门课的 quizzes / discussions 其它失败 | 进 `errors`，该门显示为空，其余课程不受影响 |
| Pages 拉取失败（含 404） | 详情弹层里该板块显示空态或错误行，Modules / 文件照常 |
| Page 正文拉取失败 | 该条展开处显示错误，不影响列表 |
| Planner 失败 | 首页「本周 DDL」卡保持 `—`，不弹错、不阻断其他卡 |
| Canvas 未配置 | 首页仍走引导卡分支，一个请求都不发 |

## 8. 测试与验收

### 8.1 自动化（本轮必须写）

`tests/test_canvas_client.py` 加：5 个新函数的字段映射与排序测试。**用例必须覆盖实测发现的
每一个边界** —— 这些不是假想的边界，是真实响应里出现过的：

- `get_quizzes`：正常映射；`due_at=null` → `""`；排序含无截止项；`published=false` 被过滤；
  **404 抛错**（由端点层转成 `errors`）。
- `get_discussion_topics`：`discussion_subentry_count` → `replies_count`；
  **`author` 为 `[]` 时不崩**；**`author.display_name` 为 `None` → `""`**；
  **`read_state` 与 `unread_count` 各自独立透传**（含 `read_state="read"` +
  `unread_count=8` 这组实测值）；`is_announcement=true` 被过滤；`pinned` 优先；
  `last_reply_at=None` 退到 `posted_at`；**两者都 `None` 排最后**。
- `get_pages`：字段映射；`published=false` 被过滤。
- `get_page_body`：HTML → `body_text`（`strip_html` 生效）。
- `get_planner_items`：**每种 `plannable_type` 各一例**（`assignment` / `quiz` /
  `discussion_topic` / `calendar_event` / `wiki_page` / `planner_note` / 未知类型）；
  **`announcement` 被丢弃**（喂一条进去，断言结果为空）；
  **`submissions` 为布尔 `false` 时 `submitted=None`**，为对象且 `submitted` 为
  `true`/`false` 时分别为 `True`/`False`（三态）；`plannable_date` 缺失时按兜底链取值；
  **全缺则丢弃该项**；**`html_url` 相对路径被补成绝对 URL**。

`tests/test_main.py` 加：5 个新端点的成功路径 + 失败路径（`ok: False`）+ 批量端点的逐课程
错误隔离。**至少一条用例专门断言「某门课 404 时其余课程照常返回」** —— 实测中一半课程会
404，这是常态路径而不是异常路径。沿用现有 `monkeypatch` 风格。

### 8.2 静态门（沿用）

- `node tools/check_dom_ids.mjs` 必须通过（新增 id 后总数会变，脚本会报新数）。
- 四个 js 文件 `node --check` 全过。
- **顶层重名检查**：新增的 `canvasQuizzes` / `pageBodyCache` / `initDiscussTab` /
  `loadDiscussions` / `renderDiscussions` / `countUnreadDiscussions` / `loadQuizzes` /
  `loadPages` / `renderPages` / `loadPlanner` 等顶层名，必须逐个确认在四个文件里唯一
  （经典脚本共享全局词法环境，重名会以 `SyntaxError` 杀死整页）。
- `$()` 只接裸 id，`$$()` 只接选择器（上一轮的 Critical 就出在这里）。

### 8.3 人工验收（无法自动化，需人工执行）

`python run_app.py --browser`，逐条：

1. 侧栏 9 项都能切，无图标、纯文字、未读徽章正常
2. 讨论页：按课程分组、未读标记、点条目开系统浏览器
3. 课程详情：Modules / 文件 / 页面三块都在；页面展开才拉正文，二次展开不重复请求
4. 课表页：测验分组出现在考试区，AIMS 未登录也能看到；无测验的课程不显示错误行
5. 首页仪表盘：统计卡 ×4 + 今日课程卡组 + 速览卡 ×3
6. 首页「本周 DDL」来自 planner：**已交项不计入、公告不计入**
7. 玻璃质感：卡片更透、有顶部高光；**浅色主题下面板不透明**
8. 窗口缩小时仪表盘网格正确换行、不破版

## 9. 风险

| 风险 | 缓解 |
|---|---|
| **`planner/items` 字段随类型而异**，归一化漏一个类型就静默丢数据 | §5.0 实测校准 + §5.1 分支表写死 + 每种类型一条测试；未知类型走兜底分支而非丢弃 |
| **`announcement` 混进「本周 DDL」**（实测 20/88 条） | 后端显式丢弃 + 前端 `type` 再挡一道 + 专门测试用例 |
| **planner 的 `html_url` 是相对路径**，`openExternal` 打不开 | 归一化时用 `canvas_url` 补全 + 测试断言绝对 URL |
| **`submissions` 恒在**，判空写错会把每项当「未交」 | `isinstance(..., dict)` 判定 + 三态测试 |
| **`author` 可能是空数组**，直接下标 `TypeError` 打死整批讨论 | `isinstance` 守卫 + 专门测试用例 |
| **顶层重名杀死整页**（经典脚本共享词法环境） | 派单时逐个核对新顶层名在四文件中唯一 |
| **`$()` / `$$()` 误用**（上轮 Critical 同类） | `check_dom_ids.mjs` + 明确约定 |
| Pages 懒加载的 N+1 | 列表不拉正文；正文按需且内存缓存 |
| 玻璃增强导致**浅色主题面板隐形** | 新 token 值必须同时写进 `:root` 与 dark 块；验收第 7 条专项 |
| 新增端点触及 `backend/`，上一轮的安全网（后端零改动）失效 | 本轮 `tests/` 必须同步新增；`pytest` 全绿是新的安全网 |
| **quizzes / pages 对未启用工具的课程 404**（实测约一半） | 按「该课无此内容」静默处理，`by_course.errors` 隔离；不当作故障 |
| token 权限不足 | **已实测：该 token 能读全部 5 类数据**（含 404 与空表在内的真实分布已记录于 §5.0）；残余风险是教师关闭工具，已按上一条处理 |
