# CanvBridge 前端 UI 重构：壳层化 + 卡片式首页

**日期**：2026-09-10
**状态**：待评审
**范围**：仅 `frontend/`。后端、测试、打包脚本不动。

---

## 1. 背景

当前前端是一个「顶栏 + 两张卡 + 5 个页签 + 设置弹窗」的单页结构：

- `frontend/index.html`（236 行）——顶栏放标题 / 日期范围胶囊 / 语言 / 设置按钮；`<main>` 里第一张卡是「同步日期范围 + 加载课程 + 课程勾选」，第二张卡里塞了 5 个页签（公告 / 文件 / 课表 / 待办 / 成绩）。
- `frontend/app.js`（1879 行）——`switchTab()` 管页签切换，各板块渲染函数与业务逻辑混在一起。
- `frontend/app.css`（329 行）——浅色为默认、深色为 `[data-theme="dark"]` 覆盖。
- 设置是个弹窗，7 组配置挤在一列里。

问题：所有板块共用的「课程加载」卡片长期占据首屏最显眼的位置，但它是个**前置步骤**而不是**内容**；设置弹窗太挤；页签横排到 5 个已经接近上限，再加板块无处可放。

## 2. 目标

1. 引入**左侧导航**作为主导航，替代顶部页签。
2. 引入**卡片式首页**，打开 app 即看到概览，不必逐页点。
3. **设置从弹窗改为独立页面**，分组呈现。
4. 把「课程加载」从首屏挪进独立的**课程页**。
5. 视觉改为**清新暗色**：深色玻璃质感，默认深色。

## 3. 非目标

- 不改 `backend/` 的任何路由、模型或业务逻辑（41 个路由原样保留）。
- 不改 `tests/`、`run_app.py`、`CanvBridge.spec`、`build_app.sh`。
- 不引入构建步骤（无 bundler、无 npm）。保持「浏览器直接跑源码」的形态。
- 不引入前端框架（React/Vue 等）。
- 不做响应式移动端布局（这是 pywebview 桌面窗口应用，只保证窗口缩小时不破版）。

## 4. 信息架构

侧栏固定 8 项，顺序如下：

| # | 页面 | `data-page` | 对应现有功能 |
|---|---|---|---|
| 1 | 首页 | `home` | **新增** |
| 2 | 公告 | `announce` | 原 `tabAnnounce` |
| 3 | 课表 | `schedule` | 原 `tabSchedule` |
| 4 | 待办 | `todo` | 原 `tabTodo` |
| 5 | 文件 | `files` | 原 `tabFiles` |
| 6 | 成绩 | `grades` | 原 `tabGrades` |
| 7 | 课程 | `courses` | **新增**（承接原首页的日期范围 + 加载课程 + 课程勾选，另加课程详情入口） |
| 8 | 设置 | `settings` | 原设置弹窗 |

**顶栏整个取消，不保留任何全局顶栏。** 原顶栏四处内容的去向：

| 原顶栏元素 | 去向 |
|---|---|
| 标题 | 侧栏 logo |
| 设置按钮 `#btnSettings` | 侧栏第 8 项 |
| 语言按钮 `#btnLang` | 设置页「外观」组 |
| 日期范围胶囊 `#rangePill` | **直接删除**，不迁移。日期范围只在课程页以两个输入框呈现（§10） |

**未读徽章**：公告、待办两项在侧栏显示未读计数（复用现有 `countNewAnnounce()` / `countNewTodo()` 与 `refreshBadges()`，只是渲染目标从页签改为侧栏项）。徽章样式沿用现有 `.tab-badge` 的红底白字，改名 `.nav-badge`。

## 5. 视觉方向

**选定方案：A · 深空玻璃**（三选一，另两案为「B 极光玻璃」「C 薄荷石墨」，已否决）。

深色为默认主题；浅色主题保留为对等变体，两套色板同步重做。

### 5.1 深色色板（`html[data-theme="dark"]`）

**现有 token 名一律保留，只换值**，另加 4 个玻璃专用的新 token。这样 `app.css` 之外任何按名字引用颜色的地方都不会失配。

| Token | 新/改 | 值 | 用途 |
|---|---|---|---|
| `--bg` | 改 | `#0a0c12` → `#0d1017` 线性渐变 + 两处冷蓝径向光晕 | 应用底色 |
| `--ink` | 改 | `#e8eaf0` | 正文 |
| `--muted` | 改 | `#8b93a5` | 次要文字 |
| `--accent` | 改 | `#6ea8fe` | 主色（冷蓝） |
| `--accent-soft` | 改 | `rgba(110,168,254,.14)` | 主色浅底（选中态、AI 总结块） |
| `--err` / `--err-bg` / `--err-border` | 改 | `#f87171` / `rgba(51,25,27,.75)` / `rgba(248,113,113,.35)` | 未读「新」高亮、错误态 |
| `--surface` / `--surface-2` / `--border` | 改 | 玻璃化后的等价色 | 沿用原名，供未迁移的旧规则使用 |
| `--ok*` / `--warn*` / `--link` | 改 | 沿用原名，仅调值 | 同上 |
| `--panel` | **新增** | `rgba(255,255,255,.052)` | 玻璃面板底 |
| `--panel-border` | **新增** | `rgba(255,255,255,.095)` | 玻璃面板描边 |
| `--panel-blur` | **新增** | `18px` | `backdrop-filter` 模糊半径 |
| `--panel-saturate` | **新增** | `130%` | `backdrop-filter` 饱和度 |

玻璃面板统一配方：

```css
background: var(--panel);
border: 1px solid var(--panel-border);
border-radius: 13px;
backdrop-filter: blur(var(--panel-blur)) saturate(var(--panel-saturate));
-webkit-backdrop-filter: blur(var(--panel-blur)) saturate(var(--panel-saturate));
```

圆角：面板 `13px`，卡片 `13px`，按钮 `8px`，胶囊 `999px`。现有 `.card` 的 `12px` / `--radius-sm` 体系被取代。

### 5.2 浅色主题

保留三态切换（跟随系统 / 浅色 / 深色），**默认值从 `system` 改为 `dark`**。浅色色板按同一套 token 名重做（玻璃在浅色下改用 `rgba(255,255,255,.72)` + 更重的描边与阴影，避免「糊成一片白」）。`html` 头部内联的主题预解析脚本（`index.html:8-20`）保留，只把缺省值从 `system` 改成 `dark`。

### 5.3 课表色块

`scheduleColor()`（`app.js:1515`）的返回值由当前高饱和色改为**低饱和柔和色**，以适配深色底。考试块维持现有深红 `#7f1d1d` + `#ef4444` 描边不变。

## 6. 文件划分与加载顺序

```
frontend/
  index.html    重写  壳层骨架：侧栏 + 8 个页面容器 + 沿用现有三个弹层
  util.js       新增  $ / $$ / api() / withBusy() / setStatus() / esc() / escAttr() / 日期格式化
  app.js        瘦身  五个板块渲染 + 业务；函数体不动，只改挂载点
  shell.js      新增  导航、首页、设置页、主题
  i18n.js       保留  新增文案补键
  app.css       重写  色板 + 壳层 + 卡片
```

**加载顺序**：`i18n.js → util.js → app.js → shell.js`，全部置于 `<body>` 末尾。

这条顺序是硬约束：`app.js` 现有多处**解析时即执行**的语句（例如 `app.js:1236` 的 `$("btnLoadTodo").onclick = …`、`app.js:1864-1867` 的 `addEventListener` 与 `refreshPill()`），依赖「脚本在 body 末尾 → DOM 已就绪」。拆文件后该约定必须保持，否则这些语句会在 DOM 就绪前执行而报错。

**不用 ES 模块**（`<script type="module">`）。原因：`app.js` 内大量隐式跨函数引用，转模块需改动全部引用点，一次性机械改动面大而收益仅是工程整洁。多个经典 `<script>` 共享全局作用域，函数声明跨文件可见，成本为零。

`CanvBridge.spec:4` 为 `datas = [('frontend', 'frontend')]`，整目录打包，新增 JS 自动包含，**spec 无需修改**。

## 7. 导航模型

### 7.1 页面容器与切换

**所有 8 个页面始终留在 DOM 中，切页只切 `hidden` 属性**，不销毁重建。

这是本次重构最大的降险点：`app.js` 里那些顶层事件绑定（按钮 `onclick`、`addEventListener`）无需改成懒初始化，一行都不用动。

`switchPage(name)` 职责：

1. 切换 8 个页面容器的 `hidden`
2. 更新侧栏高亮（`.nav-item.active`）
3. 更新页面标题
4. 触发该页的首次进入初始化 —— 复用已有函数：`initScheduleTab()`（`app.js:1818`）、`initTodoTab()`（`app.js:1197`）、`initGradesTab()`（`app.js:1309`）

原 `switchTab()`（`app.js:532`）及其页签逻辑退役。`refreshBadges()`（`app.js:526`）的渲染目标由页签改为侧栏项。

打开 app 固定落在**首页**，不记忆上次位置。

### 7.2 页面容器 id

原 `#tabAnnounce` / `#tabFiles` / `#tabSchedule` / `#tabTodo` / `#tabGrades` 改为 `#page-announce` / `#page-files` / `#page-schedule` / `#page-todo` / `#page-grades`；新增 `#page-home` / `#page-courses` / `#page-settings`。

`app.js` 中引用这些 id 的地方需同步改（主要是各板块渲染函数的挂载点与 `switchTab` 相关的显隐逻辑）。

## 8. 首页

### 8.1 布局

- **统计卡 ×4**（顶部一排）：未读公告 / 今日课程 / 本周 DDL / 本周考试。每张显示数字 + 标签，可点击跳到对应板块。
- **速览卡 ×2**：最新公告（前 3 条，带日期）/ 今日课表（时间 + 课程 + 地点）。右上角「更多 →」跳对应板块。

### 8.2 数据加载

**进入首页时并发拉取，本次会话只拉一次。**

- 首页骨架立即渲染，数字位置显示 `—`，数据到达后逐个填入。
- 用 `Promise.allSettled` 并发：**某张卡失败只让那张卡保持 `—`，不弹错误、不阻断其他卡**。
- 本次会话内再次切回首页直接用缓存，不重复请求。
- 复用现有静默后台拉取模式（`bgFetchTodoBadge()`，`app.js:1183`）——不遮罩、失败不报错。
- **未配置 Canvas Token 时**：首页显示引导卡（「去设置填 Canvas Token」），**不发任何请求**。

数据来源：

| 卡片 | 来源 |
|---|---|
| 未读公告 | `countNewAnnounce()`（`app.js:492`），数据来自 `syncAnnouncements()` 的结果 |
| 今日课程 | `banwebSchedule`（localStorage 缓存，`loadBanweb()` `app.js:1363`） |
| 本周 DDL | `/api/calendar_events`（复用 `loadTodo()` 的调用形态，`app.js:1225`） |
| 本周考试 | `/api/banweb/exams`（复用 `loadExams()`，`app.js:1787`） |

「今日课程」依赖 AIMS 登录态。未登录时该卡显示提示文案而非空白。

## 9. 各板块页

| 页面 | 改动 |
|---|---|
| 公告 | 挂载点改 `#page-announce`；`renderSummaries()`（`app.js:716`）、`wireAnnounceExpands()`、`summarizeAnnouncement()` 等**函数体不动**；底部写日历/提醒条保留 |
| 课表 | `renderSchedule()`（`app.js:1542`）不动；仅 `scheduleColor()` 换色；考试红块、周导航、AIMS 状态条保留 |
| 待办 | 原样挂载，零改动 |
| 文件 | 原样挂载，零改动 |
| 成绩 | 原样挂载，零改动 |
| 课程 | 见 §10 |

**`refreshPill()` 退役（`app.js:84-90`）。** 它写 `$("rangePill").textContent`，而 `#rangePill` 随顶栏删除；且它在 `app.js:1866` 是**顶层调用**——若只删元素不删函数，这一行会 `null.textContent` 抛错，**导致 `app.js` 该行之后的全部代码不执行**。因此必须同时移除 `refreshPill()` 的函数体与它的两处调用点（`app.js:1861` 的 `onTopRangeChange()` 内、`app.js:1866` 顶层）。

**详情弹层（`#detailModal`）、模块文件弹层（`#modulePop`）、状态横幅（`#status`）、忙碌遮罩（`#overlay`）全部原样保留**，只换样式。

## 10. 课程页（新增）

承接原首页顶部那张卡的全部职责，并补上详情入口：

- **日期范围**：原 `#inpStart` / `#inpEnd` 两个 `<input type="date">` 移入，id 不变（`onTopRangeChange()` `app.js:1860` 与 `refreshPill()` 依赖它们）。
- **加载课程按钮**：原 `#btnLoadCourses` 移入，id 不变。
- **课程列表**：每行显示 勾选框 + 课程编号 + 课程名 + 未读数徽章 + 「详情」「忽略」按钮。
  - 勾选框复用现有 `renderCourseCheckboxes()`（`app.js:598`）的语义：勾选 = 参与公告/待办/文件同步，状态存 `localStorage` 的 `sc_course_sel`。
  - 「详情」直接调已有 `openCourseDetail()`（`app.js:298`）。
  - 「忽略」复用现有忽略机制（`savedCourseIgnored()` / `saveCourseIgnored()` `app.js:617-623`）。
- **不设只读范围胶囊**。范围就是这两个输入框本身，没有第二处需要同步的显示（原 `#rangePill` 及 `refreshPill()` 已退役，见 §9）。

## 11. 设置页

弹窗改整页，7 组卡片：

1. **外观** —— 主题（三态）+ 语言（原顶栏 `btnLang` 迁入）
2. **Canvas** —— 地址 / Token / 测试连接
3. **AIMS 自动登录** —— EID / 密码 / 保存 / 清除
4. **下载** —— 目录 + 浏览按钮
5. **日历与提醒** —— 默认日历 / 默认提醒 / 刷新日历列表
6. **AI 总结** —— 接口地址 / API Key / 模型
7. **忽略的课程** —— chips 列表 + 全部恢复（跨两列）

**关键约束：复用现有 input 的 id** —— `canvasUrl`、`canvasToken`、`llmBaseUrl`、`llmApiKey`、`llmModel`、`downloadDir`、`aimsUsername`、`aimsPassword`、`selTheme`、`selCalendar`、`selList`、`ignoreCourses` 等全部保持不变。这样 `loadSettings()` / `saveSettings()` / `settings()`（`app.js:66-73`）及 `KEY` 数组（`app.js:4`）**一个字都不用改**。

`openSettings()` / `closeSettings()`（`app.js:168-169`）改为 `switchPage("settings")`。原「改动即时保存」语义保留（`saveSettings()` 由 `change` 事件驱动）。

## 12. 错误处理

不新造机制，沿用现有：

- `setStatus(msg, kind, ms)`（`app.js:98`）——右下角横幅，`ok` / `err` / `info` 三态。
- `withBusy(text, button, fn)`（`app.js:112`）——忙碌遮罩。

首页并发拉取是唯一的新错误路径，规则见 §8.2：单卡失败静默降级为 `—`。

## 13. 测试与验收

**后端零改动 → 跑现有 `pytest` 确认全绿**（`tests/` 下 7 个 `test_*.py`）。这是本次重构的安全网：只要后端测试全过，说明改动确实只落在前端。

前端无自动化测试，靠实跑 `python run_app.py` 手动验收：

- [ ] 8 个页面都能切换，侧栏高亮正确
- [ ] 首页骨架先出，数字随后填入；切走再切回不重新请求
- [ ] 未配置 Canvas Token 时首页显示引导卡且不发请求
- [ ] 公告页未读「新」红框高亮仍在
- [ ] 公告 AI 逐条总结仍在，且与原文字样式有区分
- [ ] 课表周视图、考试红块、周导航正常
- [ ] 课程页勾选状态能存能读（刷新后保持）
- [ ] 文件列表 / 下载 / 进度条正常
- [ ] 设置页所有项能存能读，主题三态切换正常
- [ ] 深色 / 浅色两套主题下都正常可读
- [ ] 详情弹层、模块文件弹层、右下角横幅、忙碌遮罩正常
- [ ] 窗口缩小时不破版

## 14. 风险

| 风险 | 缓解 |
|---|---|
| `app.js` 顶层立即执行语句因加载顺序变化而报错 | 严格保持 `i18n → util → app → shell` 且全部在 body 末尾（§6） |
| 删掉 DOM 元素却留下引用它的顶层语句 → `null` 抛错，**该行之后的 `app.js` 全部不执行** | 已识别具体实例：`refreshPill()` 与 `#rangePill`（§9）。改前先 grep 每个被删元素的 id，确认无顶层引用；同理适用于 `#btnSettings`、`#btnLang`、`.tabs` 相关元素 |
| 页面容器 id 改名后遗漏引用点 | 全量 grep `tabAnnounce\|tabFiles\|tabSchedule\|tabTodo\|tabGrades` 逐个改 |
| 玻璃效果（`backdrop-filter`）在 WKWebView 性能不佳 | 模糊半径限制在 18px；如遇卡顿降级为纯色面板（token 换值即可，不改结构） |
| 拆文件后全局符号冲突或丢失 | 拆分时逐个函数确认归属；用 `node --check` 做语法校验 |
| 改动面大导致回归 | 分批验证：先壳层能跑通，再逐板块接上；后端测试作安全网 |
