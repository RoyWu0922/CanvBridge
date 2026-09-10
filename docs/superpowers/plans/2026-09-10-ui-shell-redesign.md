# CanvBridge 前端壳层化重构 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 CanvBridge 前端从「顶栏 + 5 页签 + 设置弹窗」改造成「左侧 8 项导航 + 卡片式首页 + 设置独立页 + 课程页」，视觉换成深空玻璃暗色主题。

**Architecture:** 不引入构建步骤。把 1879 行的 `app.js` 按职责拆成三个**经典 `<script>`**（靠全局作用域共享，不用 ES 模块）：`util.js`（纯工具）→ `app.js`（五个板块的渲染与业务，函数体基本不动）→ `shell.js`（导航、首页、设置页、主题）。页面全部常驻 DOM，切页只切 `hidden` 属性，因此现有的顶层事件绑定无需改成懒初始化。

**Tech Stack:** 原生 JS（无框架、无 bundler）、原生 CSS（含 `backdrop-filter`）、FastAPI 静态托管、pywebview 桌面窗口、`node --check` / `node` v24 做开发期校验、pytest 做后端回归网。

**Spec:** `docs/superpowers/specs/2026-09-10-ui-shell-redesign-design.md`

## Global Constraints

- **只改 `frontend/`。** `backend/`、`tests/`、`run_app.py`、`CanvBridge.spec`、`build_app.sh` 一律不动。
- **不引入构建步骤**：不建 `package.json`、不装 npm 依赖、不用 bundler。保持「浏览器直接跑源码」。
- **不用 ES 模块**：三个 js 用普通 `<script>`，全部置于 `<body>` 末尾，顺序必须是 `i18n.js → util.js → app.js → shell.js`。
- **页面常驻 DOM**：8 个页面容器始终在 DOM 中，切页只切 `hidden`，不销毁重建。
- **现有 input 的 id 一律不变**：`canvasUrl`、`canvasToken`、`llmBaseUrl`、`llmApiKey`、`llmModel`、`downloadDir`、`aimsUsername`、`aimsPassword`、`selTheme`、`selCalendar`、`selList`、`ignoreCourses`、`inpStart`、`inpEnd`、`btnLoadCourses`、`btnLang` 等。这样 `loadSettings()` / `saveSettings()` / `settings()` 一个字都不用改。
- **配色 token 沿用现有名字，只换值**（spec §5.1），另加 4 个新 token：`--panel`、`--panel-border`、`--panel-blur`、`--panel-saturate`。
- **主题默认值从 `system` 改为 `dark`**，三态切换保留。
- 所有新增文案必须**同时补 `zh` 和 `en`** 两份（`i18n.js` 的 `I18N.zh` / `I18N.en` 必须键集一致）。
- 每个任务结束都要跑 `node tools/check_dom_ids.mjs` 且通过。

---

## 关键风险：删元素却留下顶层引用

`app.js` 里有 ~55 行**解析时立即执行**的语句（`$("x").onclick = …` 形式），它们依赖「脚本在 body 末尾 → DOM 已就绪」。**若删掉某个 DOM 元素却留下引用它的顶层语句，会 `null.onclick` 抛错，该行之后整个文件不再执行**——表现为「课表、待办、成绩全都不工作」而不是「那个按钮不见了」，极难排查。

本次会删除的元素，以及必须同步处理的引用点：

| 被删元素 | 断裂的顶层语句 | 处理 |
|---|---|---|
| `#rangePill` | `app.js:1866`（经 `refreshPill()`） | 删 `refreshPill()` 函数体 + 两处调用（`app.js:1861`、`app.js:1866`） |
| `#btnSettings` | `app.js:170` | 改为侧栏项驱动 `switchPage("settings")` |
| `#btnCloseSettings` | `app.js:171` | 设置页不需要关闭按钮，整行删除 |
| `#settingsModal` | `app.js:172`、`app.js:173` | 两行整行删除（改设置页后无弹窗、无 Esc 关闭） |
| `.tab` 按钮 | `app.js:546`（`$$(".tab").forEach`） | `$$` 返回空数组不抛错，但必须删掉这段死代码 |
| `#btnLang` | `app.js:1847` | **元素保留**，只是搬到设置页，id 不变 → 无需改动 |

Task 1 会建立自动校验来守住这一类问题。

---

## 文件结构

```
frontend/
  index.html    重写  壳层骨架：侧栏 + 8 个页面容器 + 沿用详情弹层/模块弹层/横幅/遮罩
  util.js       新增  纯工具：$ $$ KEY ALERTS esc escAttr fmt short api withBusy setStatus
                      loadSettings saveSettings settings downloadDir fillSelect fillAlert fillCourseFilter
  app.js        瘦身  五个板块渲染与业务（函数体基本不动），删掉已迁走的工具函数
  shell.js      新增  导航 switchPage、未读徽章、首页、设置页 wiring、主题、启动引导
  i18n.js       补键  新增 nav.* / home.* / settings.group.* / courses.*，zh+en 各一份
  app.css       改   换 :root 与 [data-theme=dark] 的值 + 新增壳层样式 + 删 .tabs/.tab
tools/
  check_dom_ids.mjs   新增  DOM id 交叉校验（开发期安全网，纯 node）
```

---

### Task 1: DOM id 交叉校验脚本（安全网）

先建安全网再动结构。这个脚本扫描 `frontend/*.js` 里所有 `$("someId")` 调用，检查对应 id 是否存在于 `frontend/index.html`。它能在 Task 3 删元素时立刻报出遗漏的引用点——正是上面那张表所列的风险。

**Files:**
- Create: `tools/check_dom_ids.mjs`

**Interfaces:**
- Consumes: 无（独立工具）
- Produces: 命令行退出码 —— `0` = 全部 id 都能在 HTML 里找到；`1` = 有缺失，并逐行打印 `文件:行号  缺失的id`

- [ ] **Step 1: 写脚本**

```js
#!/usr/bin/env node
/**
 * DOM id 交叉校验：扫 frontend/*.js 里的 $("id") 调用，
 * 检查该 id 是否在 frontend/index.html 中存在。
 *
 * 存在的意义：app.js 有大量解析时立即执行的 $("x").onclick = … 语句。
 * 删掉某个 DOM 元素却留下引用，会在浏览器里 null 抛错并静默中断该文件
 * 后续全部代码——症状是「好几个板块一起失灵」，很难定位。这个脚本让
 * 这类错误在改完就能被发现，而不是等到跑起来肉眼观察。
 *
 * 用法：node tools/check_dom_ids.mjs
 * 退出码：0 = 通过，1 = 有缺失
 */
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONTEND = join(ROOT, "frontend");

const html = readFileSync(join(FRONTEND, "index.html"), "utf8");
// 收集 HTML 里的所有 id
const htmlIds = new Set([...html.matchAll(/\bid\s*=\s*"([^"]+)"/g)].map(m => m[1]));

const missing = [];
for (const file of readdirSync(FRONTEND).filter(f => f.endsWith(".js"))) {
  const src = readFileSync(join(FRONTEND, file), "utf8");
  src.split("\n").forEach((line, i) => {
    // 只匹配 $("id") —— $$ 是选择器，不走 id 查找
    for (const m of line.matchAll(/(?<!\$)\$\("([A-Za-z_][\w-]*)"\)/g)) {
      const id = m[1];
      if (!htmlIds.has(id)) missing.push(`${file}:${i + 1}  缺失 id: ${id}`);
    }
  });
}

if (missing.length) {
  console.error("✗ 以下 $(\"id\") 引用在 frontend/index.html 里找不到对应元素：\n");
  missing.forEach(l => console.error("  " + l));
  console.error(`\n共 ${missing.length} 处。删元素时必须同步改掉引用它的语句，`);
  console.error("否则浏览器里会 null 抛错并中断该文件后续全部代码。");
  process.exit(1);
}
console.log(`✓ 所有 $("id") 引用都能在 index.html 中找到（HTML 共 ${htmlIds.size} 个 id）`);
```

- [ ] **Step 2: 对当前代码跑一次，确认基线通过**

Run: `node tools/check_dom_ids.mjs`
Expected: `✓ 所有 $("id") 引用都能在 index.html 中找到（HTML 共 N 个 id）`

若报缺失：说明脚本的正则误伤了（例如把模板字符串里的 `${...}` 或注释里的示例算进去了）。**修脚本，不要改产品代码**——当前 app 是能正常跑的，基线必须是绿的。

- [ ] **Step 3: 提交**

```bash
git add tools/check_dom_ids.mjs
git commit -m "test: 新增 DOM id 交叉校验脚本（防删元素留顶层引用的静默中断）"
```

---

### Task 2: 抽取 util.js

把纯工具函数从 `app.js` 搬到新文件，**函数体原样复制**，不改逻辑。这一步不改变任何行为，App 跑起来必须和之前完全一样。

**Files:**
- Create: `frontend/util.js`
- Modify: `frontend/app.js`（删除已迁走的函数定义）
- Modify: `frontend/index.html:233-234`（插入 `<script src="/static/util.js">`）

**Interfaces:**
- Consumes: `t()`（来自 `i18n.js`，需已加载）
- Produces: 全局符号 `$`、`$$`、`KEY`、`ALERTS`、`esc`、`escAttr`、`fmt`、`short`、`api`、`withBusy`、`setStatus`、`loadSettings`、`saveSettings`、`settings`、`downloadDir`、`fillSelect`、`fillAlert`、`fillCourseFilter`

- [ ] **Step 1: 建 util.js，把下列函数原样搬入**

从 `app.js` 剪切这些定义（**代码一字不改**），按以下顺序放进 `frontend/util.js`：

| 源位置 | 符号 |
|---|---|
| `app.js:2-3` | `$`、`$$` |
| `app.js:4-5` | `KEY`、`ALERTS` |
| `app.js:66-67` | `loadSettings`、`saveSettings` |
| `app.js:68-73` | `settings`、`downloadDir` |
| `app.js:98-103` | `setStatus` |
| `app.js:104-118` | `api`、`withBusy` |
| `app.js:119-140` | `fillSelect`、`fillAlert`、`fillCourseFilter` |
| `app.js:141-142` | `esc`、`escAttr` |
| `app.js:76-77` | `fmt`、`short` |

文件顶部加一行注释说明职责：

```js
/* 通用工具：DOM 查找、后端调用、状态提示、格式化。
   被 app.js（板块）与 shell.js（壳层）共用。
   依赖 i18n.js 的 t()，须在其之后加载。 */
```

- [ ] **Step 2: 从 app.js 删除这些定义**

确认 `app.js` 里不再有上述任何函数/常量的定义（`grep` 验证，见 Step 3）。**注意 `defaultRange()`（`app.js:78-83`）留在 `app.js`** —— 它操作 `#inpStart`/`#inpEnd`，属于课程页职责。

- [ ] **Step 3: 验证没有残留定义**

Run: `grep -nE '^(const|let) (\$|KEY|ALERTS)|^function (loadSettings|saveSettings|settings|downloadDir|setStatus|api|withBusy|fillSelect|fillAlert|fillCourseFilter|esc|escAttr)' frontend/app.js`
Expected: 无输出（全部已迁走）

Run: `node --check frontend/util.js && node --check frontend/app.js`
Expected: 两条都无输出（语法通过）

- [ ] **Step 4: 在 index.html 里插入 util.js**

`frontend/index.html` 末尾现在的两行（第 233-234 行附近）：

```html
<script src="/static/i18n.js"></script>
<script src="/static/app.js"></script>
```

改为：

```html
<script src="/static/i18n.js"></script>
<script src="/static/util.js"></script>
<script src="/static/app.js"></script>
```

- [ ] **Step 5: 跑校验 + 冒烟测试**

Run: `node tools/check_dom_ids.mjs`
Expected: `✓ …`

Run: `.venv/bin/pytest -q`
Expected: 全部通过（后端未动）

手动冒烟：`python run_app.py`，确认公告页能出内容、按钮能点、控制台无 `ReferenceError`。

- [ ] **Step 6: 提交**

```bash
git add frontend/util.js frontend/app.js frontend/index.html
git commit -m "refactor: 抽出 util.js（DOM/请求/提示/格式化工具），app.js 瘦身"
```

---

### Task 3: 壳层骨架 + 8 页导航

本任务产出：app 能跑，左侧 8 项能切，五个原有板块内容都在新壳里，首页/课程/设置为空占位。**样式仍是旧的**，视觉在 Task 7 统一处理。

**Files:**
- Rewrite: `frontend/index.html`
- Create: `frontend/shell.js`
- Modify: `frontend/app.js`（处理上面「关键风险」表里列的断裂点）

**Interfaces:**
- Consumes: `util.js` 的全部符号；`app.js` 的 `initScheduleTab()`、`initTodoTab()`、`initGradesTab()`、`markAnnounceSeen()`、`countNewAnnounce()`、`countNewTodo()`、`renderCourseCheckboxes()`、`syncAnnouncements()`
- Produces: 全局 `PAGES`（字符串数组）、`switchPage(name)`、`refreshBadges()`、`setNavBadge(page, n)`

- [ ] **Step 1: 重写 index.html 骨架**

页面容器 id 映射（**这是全任务的契约，后续任务都按它写**）：

| 原 id | 新 id | 侧栏 `data-page` |
|---|---|---|
| （新增） | `page-home` | `home` |
| `tabAnnounce` | `page-announce` | `announce` |
| `tabSchedule` | `page-schedule` | `schedule` |
| `tabTodo` | `page-todo` | `todo` |
| `tabFiles` | `page-files` | `files` |
| `tabGrades` | `page-grades` | `grades` |
| （新增） | `page-courses` | `courses` |
| （原 `settingsModal` 内容） | `page-settings` | `settings` |

`<body>` 结构：

```html
<body>
<div class="app-shell">

  <aside class="sidebar">
    <div class="sidebar-logo">
      <span class="logo-mark">🎓</span>
      <span class="logo-text" data-i18n="app.title"></span>
    </div>
    <nav class="nav" id="nav">
      <button class="nav-item" data-page="home"><span class="nav-ico">⌂</span><span data-i18n="nav.home"></span></button>
      <button class="nav-item" data-page="announce"><span class="nav-ico">📢</span><span data-i18n="nav.announce"></span></button>
      <button class="nav-item" data-page="schedule"><span class="nav-ico">📅</span><span data-i18n="nav.schedule"></span></button>
      <button class="nav-item" data-page="todo"><span class="nav-ico">⏰</span><span data-i18n="nav.todo"></span></button>
      <button class="nav-item" data-page="files"><span class="nav-ico">📁</span><span data-i18n="nav.files"></span></button>
      <button class="nav-item" data-page="grades"><span class="nav-ico">📊</span><span data-i18n="nav.grades"></span></button>
      <button class="nav-item" data-page="courses"><span class="nav-ico">📚</span><span data-i18n="nav.courses"></span></button>
      <div class="nav-gap"></div>
      <button class="nav-item" data-page="settings"><span class="nav-ico">⚙</span><span data-i18n="nav.settings"></span></button>
    </nav>
  </aside>

  <main class="content">
    <div id="page-home"     class="page" hidden></div>
    <div id="page-announce" class="page">  <!-- 原来的 #tabAnnounce 内容原样搬进来，去掉 class="tab-panel" --> </div>
    <div id="page-schedule" class="page" hidden><!-- 原 #tabSchedule --> </div>
    <div id="page-todo"     class="page" hidden><!-- 原 #tabTodo --> </div>
    <div id="page-files"    class="page" hidden><!-- 原 #tabFiles --> </div>
    <div id="page-grades"   class="page" hidden><!-- 原 #tabGrades --> </div>
    <div id="page-courses"  class="page" hidden><!-- 原首屏那张「同步范围 + 加载课程 + 课程勾选」卡片整块搬进来，Task 5 再重排 --> </div>
    <div id="page-settings" class="page" hidden><!-- 原 #settingsModal 内容整块搬进来，Task 4 再重排 --> </div>
  </main>

</div>

<!-- 以下四块原样保留，一字不改 -->
<div id="detailModal" class="modal" …>…</div>
<div id="modulePop" class="module-pop" …>…</div>
<div id="status" class="banner" role="status"></div>
<div id="overlay">…</div>

<script src="/static/i18n.js"></script>
<script src="/static/util.js"></script>
<script src="/static/app.js"></script>
<script src="/static/shell.js"></script>
</body>
```

**搬移时删掉的东西**：整个 `<header class="app-header">`（含 `#rangePill`、`#btnLang`、`#btnSettings`）；`<div class="tabs" role="tablist">` 那一块；`#settingsModal` 的外壳（`.modal` / `.modal-backdrop` / `.modal-card` / `.modal-head` / `.modal-close`）；各 `tab-panel` 的 `class="tab-panel"` 与 `role="tabpanel"`。

**两块内容必须原样搬入、不能丢**（丢了会在浏览器里静默中断 `app.js`，且 Step 7 的校验器会立刻报出来）：

| 原位置 | 搬到 | 为什么不能丢 |
|---|---|---|
| `<main>` 里第一张 `.card`（同步范围 + `#btnLoadCourses` + `#courseCheckboxes`） | `#page-courses` | `app.js:574` 绑 `#btnLoadCourses`、`:1864-1865` 绑 `#inpStart`/`#inpEnd`、`:611` 绑 `#courseCheckboxes` |
| `#settingsModal` 的**内部内容**（`.modal-card` 里的全部表单） | `#page-settings` | `app.js` 有十几处 `$("canvasUrl")` / `$("selTheme")` / `$("llmApiKey")` 等引用 |

**`#btnLang` 保留**：Task 4 会把它放进设置页「外观」组，本任务先随设置内容一起搬进 `#page-settings`，id 不变。

- [ ] **Step 2: 写 shell.js 的导航层**

```js
/* 壳层：左侧导航、未读徽章、首页、设置页、主题、启动引导。
   依赖 util.js（$ / api / setStatus …）与 app.js（各板块的 init* 函数）。
   必须最后加载。 */

const PAGES = ["home","announce","schedule","todo","files","grades","courses","settings"];

/* 各板块的首次进入初始化（app.js 提供，均为函数声明，运行时可见）。
   注意 settings 也在这里 —— 见 Step 4 第 2 条：openSettings() 不能自己调
   switchPage()，否则 switchPage("settings") → openSettings() → switchPage()
   会无限递归。让它只做「进入设置页后要同步的那几件事」。 */
const PAGE_INIT = {
  schedule: () => initScheduleTab(),
  todo:     () => initTodoTab(),
  grades:   () => initGradesTab(),
  home:     () => initHome(),
  settings: () => openSettings(),
};

let currentPage = "";

function switchPage(name){
  if(!PAGES.includes(name)) name = "home";
  const prev = currentPage;
  currentPage = name;

  PAGES.forEach(p => { const el = $("page-" + p); if (el) el.hidden = (p !== name); });
  $$("#nav .nav-item").forEach(b => b.classList.toggle("active", b.dataset.page === name));

  /* 「切走即已读」：离开公告页 = 已看过其上内容 → 记已读、徽章消失。
     启动落在首页，新公告徽章会一直亮到用户切走那一刻。 */
  if(prev === "announce" && name !== "announce") markAnnounceSeen();
  else if(name === "announce" && !countNewAnnounce()){
    $$("#summaries .item.is-new").forEach(el => el.classList.remove("is-new"));
    $$("#summaries .badge-new").forEach(el => el.remove());
  }

  refreshBadges();
  const init = PAGE_INIT[name];
  if (init) init();
}

$("nav").addEventListener("click", e => {
  const b = e.target.closest(".nav-item");
  if (b) switchPage(b.dataset.page);
});
```

- [ ] **Step 3: 写 shell.js 的未读徽章**

取代 `app.js` 原 `setTabBadge` / `refreshBadges`（`app.js:513-529`）。

**先把 `app.js` 里 `activeTabId`、`setTabBadge`、`refreshBadges`、`switchTab` 四个函数连同 `$$(".tab").forEach(...)` 那段监听（`app.js:513-552`）整块删除。** 然后：

```js
/* 在侧栏项右侧画/收未读徽标 */
function setNavBadge(page, n){
  const item = $(`#nav .nav-item[data-page="${page}"]`);
  if (!item) return;
  let b = item.querySelector(".nav-badge");
  if (n <= 0){ if (b) b.remove(); item.removeAttribute("title"); return; }
  if (!b){ b = document.createElement("span"); b.className = "nav-badge"; item.appendChild(b); }
  b.textContent = n > 99 ? "99+" : String(n);
  b.hidden = false;
  item.title = t(page === "todo" ? "unread.todo" : "unread.announce", { n });
}

/* 更新两个侧栏项的未读徽章（纯展示）。已读动作由 switchPage / loadTodo 触发 */
function refreshBadges(){
  setNavBadge("announce", countNewAnnounce());
  setNavBadge("todo", countNewTodo());
}
```

- [ ] **Step 4: 处理 app.js 的断裂点**

按「关键风险」表逐条改：

1. **删 `refreshPill()` 整个函数**（`app.js:84-90`），并删它的两处调用：`onTopRangeChange()`（`app.js:1860-1863`）内的第一行、以及顶层 `app.js:1866` 那一行 `refreshPill();`。
   `onTopRangeChange()` 改后只剩 `scheduleAutoSync();`：

```js
function onTopRangeChange(){
  scheduleAutoSync();
}
```

2. **删 `app.js:170-173` 这四行**（`btnSettings` / `btnCloseSettings` / `settingsModal` backdrop / Esc 关闭），并删 `closeSettings`（`app.js:169`）。

   把 `openSettings`（`app.js:168`）改成**进入设置页后的同步动作** —— 它**不能**再调 `switchPage("settings")`（会与 `PAGE_INIT.settings` 形成无限递归，见 Step 2 的注释）。触发者交给 `PAGE_INIT.settings`：

```js
/* 进入设置页时同步一次控件状态（页面切换由 switchPage 负责，这里不再切） */
function openSettings(){
  $("selTheme").value = themeMode;
  refreshAimsUi();
  fillIgnoreCourses();
}
```

`$("btnCloseSettings")` 一并删除。

3. **`$("selTheme")` 监听（`app.js:174`）保留** —— 元素 id 不变（Task 4 搬到设置页），本任务不动。

4. **改 `switchTab` 的两处调用点**：
   - `app.js:585`（`btnLoadCourses.onclick` 内）：`switchTab("tabAnnounce")` → `switchPage("announce")`
   - `app.js:1877`（`autoLoadOnOpen` 内）：`switchTab("tabAnnounce")` → `switchPage("announce")`

5. **`autoLoadOnOpen()` 整体从 `app.js` 迁到 `shell.js`**（连同 `app.js:1879` 的触发条件）。原因：它现在要落回首页而不是公告页，且放在最后加载的 shell.js 里才能确保调用 `switchPage`、`refreshBadges` 时它们已定义。迁移后删掉 `app.js:1869-1879` 全部内容。

- [ ] **Step 5: 写 shell.js 的启动引导与 `initHome` 占位**

```js
/* 启动引导：先落首页，再静默拉数据（不遮罩、失败不报错） */
async function autoLoadOnOpen(){
  const s = settings();
  if(!s.canvas_url || !s.canvas_token) return;   // 未配置 → 首页显示引导卡，不发请求
  const r = await api("courses", s);
  if(!r.ok) return;
  courseList = r.courses;
  renderCourseCheckboxes(courseList);
  await syncAnnouncements();                      // 内部会 refreshBadges()
  if (typeof initHome === "function") initHome();
}

/* 首页：Task 6 实现 */
function initHome(){}

switchPage("home");
if($("canvasUrl").value && $("canvasToken").value) autoLoadOnOpen();
```

- [ ] **Step 6: 补 i18n 键（zh + en 各一份）**

在 `frontend/i18n.js` 的 `I18N.zh` 与 `I18N.en` 里**各加一组**（键名一致，值按语言）：

```js
"nav.home": "首页",
"nav.announce": "公告",
"nav.schedule": "课表",
"nav.todo": "待办",
"nav.files": "文件",
"nav.grades": "成绩",
"nav.courses": "课程",
"nav.settings": "设置",
```

英文对应 `"Home"` / `"Announcements"` / `"Schedule"` / `"To-do"` / `"Files"` / `"Grades"` / `"Courses"` / `"Settings"`。

- [ ] **Step 7: 全量校验**

Run: `node --check frontend/shell.js && node --check frontend/app.js && node --check frontend/util.js`
Expected: 无输出

Run: `node tools/check_dom_ids.mjs`
Expected: `✓ …` —— **这一步是本次重构的关键闸门**。若报 `缺失 id: rangePill` / `btnSettings` / `settingsModal` / `btnCloseSettings`，说明 Step 4 有遗漏，回去补。

Run: `grep -nE 'switchTab|activeTabId|setTabBadge|refreshPill|tabAnnounce|tabFiles|tabSchedule|tabTodo|tabGrades' frontend/app.js frontend/shell.js`
Expected: 无输出（旧页签体系与 `refreshPill` 已彻底清除）

- [ ] **Step 8: 冒烟测试**

Run: `python run_app.py`

逐项确认：
- 打开落在首页（暂时空白，正常）
- 侧栏 8 项都在，点击能切页，高亮跟着走
- **公告 / 课表 / 待办 / 文件 / 成绩 五页内容与改前一致**
- 公告页「加载课程」按钮仍工作
- 浏览器控制台无任何 `TypeError` / `ReferenceError`

- [ ] **Step 9: 提交**

```bash
git add frontend/index.html frontend/shell.js frontend/app.js frontend/i18n.js
git commit -m "feat: 前端改壳层结构，左侧 8 项导航替代顶部页签"
```

---

### Task 4: 设置弹窗 → 设置页

**Files:**
- Modify: `frontend/index.html`（`#page-settings` 填内容）
- Modify: `frontend/app.js`（`openSettings` / `closeSettings`）
- Modify: `frontend/i18n.js`

**Interfaces:**
- Consumes: Task 3 的 `switchPage(name)`
- Produces: 无新全局符号

- [ ] **Step 1: 把设置弹窗的内容搬进 `#page-settings`**

原 `#settingsModal > .modal-card` 里的内容整体搬入，**去掉 `.modal-card` / `.modal-head` / `.modal-close` 外壳**（整页不需要关闭按钮）。按 7 组重排，每组一个 `.set-card`，外层 `.set-grid`：

| 组 | 标题 i18n 键 | 含哪些原控件 |
|---|---|---|
| 1 | `settings.group.appearance` | `#selTheme` + `#btnLang` |
| 2 | `settings.group.canvas` | `#canvasUrl`、`#canvasToken`、`#btnTest` |
| 3 | `settings.group.aims` | `#aimsUsername`、`#aimsPassword`、`#btnSaveAims`、`#btnClearAims`、`#aimsSavedHint` |
| 4 | `settings.group.download` | `#downloadDir`、`#btnBrowseDir` |
| 5 | `settings.group.calendar` | `#selCalendar`、`#selList`、`#btnLoadCalendars` |
| 6 | `settings.group.llm` | `#llmBaseUrl`、`#llmApiKey`、`#llmModel` |
| 7 | `settings.group.ignore` | `#ignoreCourses`、`#btnClearIgnore`、`settings.ignore_hint` |

第 7 组加 `class="set-card set-wide"`（跨两列）。

**所有 input / select / button 的 id 必须原样保留** —— `app.js` 里已有的 `onclick` / `addEventListener` 全部依赖它们，改了 id 就会集体失灵。

- [ ] **Step 2: 确认 openSettings 形态正确**

Task 3 Step 4 已把 `openSettings()` 改成「只做同步、不切页」，并由 `PAGE_INIT.settings` 触发。本步只做核对，正常情况**无需改动**：

```js
/* 进入设置页时同步一次控件状态（页面切换由 switchPage 负责，这里不再切） */
function openSettings(){
  $("selTheme").value = themeMode;
  refreshAimsUi();
  fillIgnoreCourses();
}
```

确认三点：`closeSettings()` 已删除；`app.js` 里没有 `switchPage("settings")`；`shell.js` 的 `PAGE_INIT` 里有 `settings: () => openSettings()`。

- [ ] **Step 3: 补 i18n 键**

`I18N.zh` 与 `I18N.en` 各加：

```js
"settings.group.appearance": "外观",
"settings.group.canvas": "Canvas",
"settings.group.aims": "AIMS 自动登录",
"settings.group.download": "下载",
"settings.group.calendar": "日历与提醒",
"settings.group.llm": "AI 总结",
"settings.group.ignore": "忽略的课程",
"settings.language": "语言",
```

英文：`"Appearance"` / `"Canvas"` / `"AIMS auto-login"` / `"Download"` / `"Calendar & reminders"` / `"AI summary"` / `"Ignored courses"` / `"Language"`。

- [ ] **Step 4: 校验**

Run: `node --check frontend/app.js && node tools/check_dom_ids.mjs`
Expected: 都通过

Run: `grep -n 'settingsModal\|closeSettings\|btnCloseSettings' frontend/*.js frontend/index.html`
Expected: 无输出

- [ ] **Step 5: 冒烟测试**

`python run_app.py`，点侧栏「设置」：
- 7 组都在，内容完整
- 改主题立即生效；改 Canvas 地址/Token → 刷新后仍在（`localStorage` 即时保存）
- 「测试连接」能拉回课程数
- 「浏览」能弹系统文件夹选择框并回填
- 「忽略的课程」chips 正常，能清除
- 回到公告页，一切照旧

- [ ] **Step 6: 提交**

```bash
git add frontend/index.html frontend/app.js frontend/i18n.js
git commit -m "feat: 设置从弹窗改为独立页，分 7 组"
```

---

### Task 5: 课程页

把「日期范围 + 加载课程 + 课程勾选」从原首屏搬进独立课程页，并补上课程详情与忽略入口。

**Files:**
- Modify: `frontend/index.html`（`#page-courses` 填内容）
- Modify: `frontend/app.js`（`renderCourseCheckboxes` 增强）
- Modify: `frontend/i18n.js`

**Interfaces:**
- Consumes: `renderCourseCheckboxes(courses)`（`app.js:598`）、`openCourseDetail(canvasId, banwebCourse)`（`app.js:298`）、`savedCourseIgnored()` / `saveCourseIgnored(ids)`（`app.js:617-623`）、`courseList`
- Produces: 无新全局符号

- [ ] **Step 1: 把 `#page-courses` 重排成课程页**

Task 3 已把原首屏那张卡片整块搬进了 `#page-courses`（id 全在，只是排版还是旧的）。本步把它替换成下列结构 —— **替换的是外壳，四个 id 一个都不能变**：

```html
<div id="page-courses" class="page" hidden>
  <div class="page-head">
    <h2 data-i18n="nav.courses"></h2>
    <span class="sp"></span>
    <button id="btnLoadCourses" class="btn btn-primary" data-i18n="btn.load_courses"></button>
  </div>

  <div class="range-row">
    <label class="field"><span data-i18n="courses.range_label"></span></label>
    <input id="inpStart" type="date">
    <span class="range-dash">–</span>
    <input id="inpEnd" type="date">
    <span class="sp"></span>
    <span class="muted" data-i18n="courses.hint_selected"></span>
  </div>

  <!-- 原有课程勾选容器：id 不变，renderCourseCheckboxes() 直接往里写 -->
  <div id="courseCheckboxes" class="course-list"></div>
</div>
```

`#btnLoadCourses` / `#inpStart` / `#inpEnd` / `#courseCheckboxes` **四个 id 全部保留** —— `app.js:574` 的 `onclick`、`app.js:1864-1865` 的 `change` 监听、`app.js:587` 的 `selectedCourses()`、`app.js:611` 的 change 监听都依赖它们。

- [ ] **Step 2: 补 i18n 键**

```js
"courses.range_label": "同步范围",
"courses.hint_selected": "勾选 = 参与公告 / 待办 / 文件同步",
"courses.detail": "详情",
"courses.ignore": "忽略",
```

英文：`"Sync range"` / `"Checked = included in announcements / to-do / file sync"` / `"Details"` / `"Ignore"`。

- [ ] **Step 3: 给课程行加「详情」「忽略」按钮**

改 `renderCourseCheckboxes()`（`app.js:598-615`）。**在保留原有勾选框渲染逻辑的前提下**，为每个课程在行尾追加两个按钮：

```js
/* 行尾操作按钮：详情（复用 openCourseDetail）/ 忽略（复用忽略机制） */
function courseRowActionsHtml(c){
  return `<span class="cr-actions">
    <button type="button" class="btn btn-ghost btn-xs cr-detail"
            data-id="${c.id}" data-i18n="courses.detail"></button>
    <button type="button" class="btn btn-ghost btn-xs cr-ignore"
            data-id="${c.id}" data-i18n="courses.ignore"></button>
  </span>`;
}
```

在 `renderCourseCheckboxes()` 的渲染模板里插入 `${courseRowActionsHtml(c)}`（`c` 为该行课程对象）。

然后在 `app.js` **顶层**（即紧跟在 `renderCourseCheckboxes` 定义之后、不要放进函数体内）绑定一次委托：

```js
/* 只在顶层绑定一次；放进 renderCourseCheckboxes() 里会随每次渲染重复叠加监听 */
$("courseCheckboxes").addEventListener("click", async (e) => {
  const d = e.target.closest(".cr-detail");
  if (d){ e.preventDefault(); e.stopPropagation();
    openCourseDetail(Number(d.dataset.id)); return; }
  const ig = e.target.closest(".cr-ignore");
  if (ig){ e.preventDefault(); e.stopPropagation();
    const id = Number(ig.dataset.id);
    const cur = savedCourseIgnored();
    if (!cur.includes(id)) saveCourseIgnored([...cur, id]);
    fillIgnoreCourses();
    renderCourseCheckboxes(courseList);
    return; }
});
```

`stopPropagation()` 是必需的：这两个按钮落在勾选框的 `<label>` 内，不拦住会连带切换勾选状态。

- [ ] **Step 4: 校验**

Run: `node --check frontend/app.js && node tools/check_dom_ids.mjs`
Expected: 都通过

- [ ] **Step 5: 冒烟测试**

`python run_app.py`，点侧栏「课程」：
- 日期范围显示当前值；改动后触发自动同步（与改前顶部卡片行为一致）
- 「加载课程」能拉回课程列表
- 勾选/取消勾选，刷新页面后状态保持（`sc_courseChecked`）
- 点「详情」弹出课程详情弹层
- 点「忽略」后该课程从列表消失，去设置页「忽略的课程」能看到它
- **点「详情」/「忽略」不会误触发勾选框**

- [ ] **Step 6: 提交**

```bash
git add frontend/index.html frontend/app.js frontend/i18n.js
git commit -m "feat: 新增课程页，承接日期范围与课程加载，补详情/忽略入口"
```

---

### Task 6: 首页

**Files:**
- Modify: `frontend/index.html`（`#page-home` 填内容）
- Modify: `frontend/shell.js`（实现 `initHome`）
- Modify: `frontend/i18n.js`

**Interfaces:**
- Consumes: `countNewAnnounce()`、`countNewTodo()`、`banwebSchedule`、`summaryResults`、`selectedCourses()`、`syncAnnouncements()`、`fmt()`、`short()`、`api()`、`esc()`、`switchPage()`
- Produces: `initHome()`

- [ ] **Step 1: 建首页 HTML**

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
    <div class="list-head"><span data-i18n="home.recent_announce"></span>
      <button class="link-btn" data-goto="announce" data-i18n="home.more"></button></div>
    <div id="homeAnnounceList" class="muted" data-i18n="home.loading"></div>
  </div>

  <div class="glass-card list-card">
    <div class="list-head"><span data-i18n="home.today_schedule"></span>
      <button class="link-btn" data-goto="schedule" data-i18n="home.more"></button></div>
    <div id="homeTodayList" class="muted" data-i18n="home.loading"></div>
  </div>
</div>
```

- [ ] **Step 2: 在 shell.js 里实现 initHome（替换 Task 3 的空占位）**

```js
let homeLoaded = false;   // 本次会话是否已拉过首页数据

/* 点统计卡 / 「更多」跳到对应板块 */
$("page-home").addEventListener("click", e => {
  const go = e.target.closest("[data-goto]");
  if (go) switchPage(go.dataset.goto);
});

async function initHome(){
  const s = settings();
  if(!s.canvas_url || !s.canvas_token){
    $("homeNeedCanvas").hidden = false;
    $("homeStats").hidden = true;
    return;
  }
  $("homeNeedCanvas").hidden = true;
  $("homeStats").hidden = false;

  renderHomeFromCache();          // 先用内存里已有的数据填一遍
  if (homeLoaded) return;         // 本次会话已拉过 → 不重复请求
  homeLoaded = true;

  /* 并发拉取；某张卡失败只让那张卡保持「—」，不弹错、不阻断其他卡 */
  await Promise.allSettled([
    (async () => {                                   // 未读公告 + 最新公告列表
      await syncAnnouncements();                     // 内部会 refreshBadges()
      $("statAnnounce").textContent = String(countNewAnnounce());
      renderHomeAnnounceList();
    })(),
    (async () => {                                   // 本周 DDL
      const now = new Date();
      const end = new Date(now); end.setDate(end.getDate() + 30);
      const r = await api("calendar_events", {
        canvas_url: s.canvas_url, canvas_token: s.canvas_token,
        course_ids: selectedCourses(), start_date: fmt(now), end_date: fmt(end) });
      if(r.ok === true && Array.isArray(r.events)){
        const weekEnd = new Date(now); weekEnd.setDate(now.getDate() + 7);
        $("statDdl").textContent = String(
          r.events.filter(ev => { const d = new Date(ev.start || ev.due); return d >= now && d <= weekEnd; }).length);
      }
    })(),
    (async () => {                                   // 本周考试
      const r = await api("banweb/exams", {});
      if(r.ok === true && Array.isArray(r.exams)) $("statExam").textContent = String(r.exams.length);
    })(),
  ]);

  renderHomeToday();              // 今日课表（本地缓存，无需网络）
}
```

三个纯渲染函数：

```js
/* 今日课程：来自 banwebSchedule 本地缓存（无需网络）。
   未登录 AIMS 时缓存为空 → 显示提示而不是空白 */
function renderHomeToday(){
  const today = new Date();
  const letter = ["U","M","T","W","R","F","S"][today.getDay()];   // 0 = 周日
  const courses = (banwebSchedule && banwebSchedule.courses) || [];
  const todayCourses = courses.filter(c => (c.days || "").includes(letter));
  const box = $("homeTodayList");
  if (!todayCourses.length){
    box.innerHTML = `<div class="muted">${t("home.need_aims")}</div>`;
    $("statToday").textContent = "0";
    return;
  }
  $("statToday").textContent = String(todayCourses.length);
  box.innerHTML = todayCourses.map(c =>
    `<div class="home-row"><span>${esc(c.time || "")}</span>
       <span class="hr-name">${esc(c.code || "")} ${esc(c.title || "")}</span>
       <em>${esc(c.room || "")}</em></div>`).join("");
}

/* 用内存里已有的结果填（切回首页时立即有内容，不等网络） */
function renderHomeFromCache(){
  $("statAnnounce").textContent = String(countNewAnnounce());
  renderHomeAnnounceList();
  renderHomeToday();
}

/* 最新 3 条公告：取自 app.js 的 summaryResults（syncAnnouncements 的产物） */
function renderHomeAnnounceList(){
  const box = $("homeAnnounceList");
  const groups = Array.isArray(summaryResults) ? summaryResults : [];
  const rows = groups
    .flatMap(g => (g.announcements || []).map(a => ({ ...a, course: g.course_name })))
    .sort((a, b) => String(b.posted_at || "").localeCompare(String(a.posted_at || "")))
    .slice(0, 3);
  if (!rows.length){ box.innerHTML = `<div class="muted">${t("home.empty")}</div>`; return; }
  box.innerHTML = rows.map(a =>
    `<div class="home-row"><span class="hr-name">${esc(a.title || "")}</span>
       <em>${esc(short(String(a.posted_at || "").slice(0, 10)))}</em></div>`).join("");
}
```

- [ ] **Step 3: 补 i18n 键**

```js
"home.stat.announce": "未读公告",
"home.stat.today": "今日课程",
"home.stat.ddl": "本周 DDL",
"home.stat.exam": "本周考试",
"home.recent_announce": "最新公告",
"home.today_schedule": "今日课表",
"home.more": "更多 →",
"home.loading": "加载中…",
"home.empty": "暂无",
"home.need_canvas": "还没配置 Canvas，去设置里填上地址和 Token。",
"home.need_aims": "尚未登录 AIMS，去课表页登录后即可看到今日课程。",
```

英文：`"Unread announcements"` / `"Classes today"` / `"Due this week"` / `"Exams this week"` / `"Recent announcements"` / `"Today's classes"` / `"More →"` / `"Loading…"` / `"Nothing yet"` / `"Canvas isn't configured yet — add the URL and token in Settings."` / `"Not signed in to AIMS — sign in from the Schedule page to see today's classes."`

- [ ] **Step 4: 校验**

Run: `node --check frontend/shell.js && node tools/check_dom_ids.mjs`
Expected: 都通过

**这里不需要 `typeof` 守卫**：`summaryResults`（`app.js:573`）与 `banwebSchedule`（`app.js:1361`）是 `let` 顶层声明，而 `shell.js` 是**在 `app.js` 完整执行完之后**才解析的独立脚本，读取时它们必定已初始化。顺带一提，若真处在 TDZ 中，`typeof x` 对 `let` 同样会抛 `ReferenceError`（它只对**未声明**的变量安全），加守卫并不能防住那种情况 —— 所以别写。

- [ ] **Step 5: 冒烟测试**

`python run_app.py`：
- 打开落在首页，骨架立即出现，数字先显示 `—` 随后填入
- 切去公告页再切回首页 → **数字立刻就有，且不产生新请求**（DevTools Network 确认）
- 点统计卡 / 「更多」能跳到对应板块
- 清掉 `localStorage` 的 `sc_canvasUrl` 后重开 → 显示引导卡，Network 面板**无任何请求**
- 未登录 AIMS 时「今日课表」显示提示文案而非空白

- [ ] **Step 6: 提交**

```bash
git add frontend/index.html frontend/shell.js frontend/i18n.js
git commit -m "feat: 新增卡片式首页（统计卡 + 速览卡），并发拉取且会话内只拉一次"
```

---

### Task 7: 深空玻璃主题

**Files:**
- Modify: `frontend/app.css`
- Modify: `frontend/app.js:1513`（`PALETTE`）
- Modify: `frontend/index.html:8-20`（主题预解析脚本缺省值）

**Interfaces:**
- Consumes: 全部既有 class
- Produces: 4 个新 CSS token：`--panel`、`--panel-border`、`--panel-blur`、`--panel-saturate`

- [ ] **Step 1: 换 `:root`（浅色）与 `[data-theme="dark"]` 的值**

**token 名全部沿用现有的，只换值**，避免任何按名引用失配。按 spec §5.1 表格替换 `app.css:2-28` 两个块的取值。深色块：

```css
:root[data-theme="dark"] {
  color-scheme:dark;
  --bg:#0a0c12; --surface:#12151c; --surface-2:#171b23; --border:rgba(255,255,255,.095);
  --ink:#e8eaf0; --muted:#8b93a5;
  --accent:#6ea8fe; --accent-strong:#8fbaff; --accent-soft:rgba(110,168,254,.14);
  --link:#e8eaf0;
  --ok:#4ade80; --ok-bg:rgba(18,48,30,.75); --ok-border:rgba(74,222,128,.30);
  --err:#f87171; --err-bg:rgba(51,25,27,.75); --err-border:rgba(248,113,113,.35);
  --warn:#fbbf24; --warn-bg:rgba(50,39,15,.75); --warn-border:rgba(251,191,36,.30);
  --shadow-sm:0 1px 2px rgba(0,0,0,.4);
  --shadow-md:0 24px 60px rgba(0,0,0,.55);
  --radius:13px; --radius-sm:8px;
  /* 玻璃配方（新增） */
  --panel:rgba(255,255,255,.052);
  --panel-border:rgba(255,255,255,.095);
  --panel-blur:18px;
  --panel-saturate:130%;
}
```

浅色块保持现有**结构**，按同一套色相把 `--accent` 等调到与深色同族，并加同名 4 个 token：

```css
:root {
  color-scheme:light;
  /* …现有浅色取值保留，仅把 --accent 调成 #3b82f6 系、--radius 改 13px… */
  --panel:rgba(255,255,255,.72);
  --panel-border:rgba(16,24,40,.10);
  --panel-blur:14px;
  --panel-saturate:115%;
}
```

同时把 `body` 背景改为渐变光晕底（改 `app.css:31-32` 的 `background:var(--bg)` 那一处，其余属性不动）：

```css
body { /* …font-family / color / font-size 等原有属性保持不变… */
  background: radial-gradient(760px 420px at 12% -12%, rgba(110,168,254,.20), transparent 62%),
              radial-gradient(620px 420px at 96% 106%, rgba(110,168,254,.09), transparent 62%),
              linear-gradient(162deg,#0a0c12,#0d1017);
  background-attachment: fixed; }
:root[data-theme="light"] body { background: var(--bg); }
```

- [ ] **Step 2: 把主题缺省值改成 dark（两处都要改）**

`"system"` 作为缺省值出现在**两个地方**，漏掉任何一个都会出现「首绘按一个值、逻辑按另一个值」的错位：

1. **`frontend/index.html:12`**（内联预解析脚本）：
   ```js
   var mode = localStorage.getItem("sc_theme") || "dark";
   ```
   同一段脚本 `catch` 分支里的兜底值 `document.documentElement.dataset.theme = "light"` 也要改成 `"dark"`（`localStorage` 被禁用时不该退回浅色）。
2. **`frontend/app.js:49`**：
   ```js
   let themeMode = "dark";
   ```
   （`app.css` 无需改 —— `--bg` 的深色值本来就在 `[data-theme="dark"]` 块里。）

改完清一次 `localStorage` 的 `sc_theme`，重启确认默认落在暗色，且首绘没有闪一下浅色。

- [ ] **Step 3: 新增壳层样式**

```css
/* 壳层：固定侧栏 + 可滚内容 */
.app-shell { display:flex; min-height:100vh; }
.sidebar { width:200px; flex:none; display:flex; flex-direction:column; gap:3px;
  padding:16px 10px; position:sticky; top:0; height:100vh; box-sizing:border-box;
  background:var(--panel); border-right:1px solid var(--panel-border);
  backdrop-filter:blur(var(--panel-blur)) saturate(var(--panel-saturate));
  -webkit-backdrop-filter:blur(var(--panel-blur)) saturate(var(--panel-saturate)); }
.sidebar-logo { display:flex; align-items:center; gap:8px; padding:2px 8px 14px;
  font-weight:700; font-size:14px; letter-spacing:-.01em; }
.nav { display:flex; flex-direction:column; gap:3px; flex:1; }
.nav-gap { flex:1; }
.nav-item { display:flex; align-items:center; gap:10px; padding:9px 11px; border:none;
  border-radius:var(--radius-sm); background:transparent; color:var(--muted);
  font:inherit; font-size:13.5px; font-weight:500; cursor:pointer; text-align:left;
  transition:background .15s, color .15s; }
.nav-item:hover { background:var(--surface-2); color:var(--ink); }
.nav-item.active { background:var(--accent-soft); color:var(--accent); font-weight:600;
  box-shadow:inset 0 0 0 1px var(--accent-soft); }
.nav-ico { font-style:normal; width:16px; text-align:center; flex:none; }
.nav-badge { margin-left:auto; background:#e11d48; color:#fff; font-size:10.5px; font-weight:700;
  min-width:16px; height:16px; line-height:16px; border-radius:999px; text-align:center; padding:0 5px; }

.content { flex:1; min-width:0; padding:26px 30px 60px; }
.page[hidden] { display:none; }
.page-head { display:flex; align-items:center; gap:12px; margin-bottom:18px; }
.page-head h2 { margin:0; font-size:19px; font-weight:700; letter-spacing:-.02em; }
.page-head .sp { flex:1; }
```

- [ ] **Step 4: 新增玻璃卡片样式**

```css
.glass-card, .card, .filter-bar, .course-card, .schedule-grid, .ann-grp {
  background:var(--panel); border:1px solid var(--panel-border);
  border-radius:var(--radius);
  backdrop-filter:blur(var(--panel-blur)) saturate(var(--panel-saturate));
  -webkit-backdrop-filter:blur(var(--panel-blur)) saturate(var(--panel-saturate)); }

.stat-row { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:16px; }
.stat-card { padding:16px 18px; cursor:pointer; transition:border-color .15s, transform .1s; }
.stat-card:hover { border-color:var(--accent); }
.stat-card b { display:block; font-size:26px; font-weight:700; color:var(--accent);
  font-variant-numeric:tabular-nums; line-height:1.15; }
.stat-card span { font-size:12px; color:var(--muted); }
.list-card { padding:14px 18px; margin-bottom:14px; }
.list-head { display:flex; align-items:baseline; justify-content:space-between;
  margin-bottom:8px; font-size:13.5px; font-weight:700; }
.link-btn { border:none; background:none; color:var(--accent); font:inherit; font-size:12px;
  cursor:pointer; padding:0; }
.link-btn:hover { text-decoration:underline; }
.home-row { display:flex; align-items:baseline; gap:10px; padding:6px 0;
  border-top:1px dashed var(--panel-border); font-size:13px; }
.home-row:first-child { border-top:none; }
.home-row .hr-name { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.home-row em { font-style:normal; color:var(--muted); font-size:12px;
  font-variant-numeric:tabular-nums; flex:none; }
.home-guide { padding:20px 22px; margin-bottom:16px; }

/* 课程页：日期范围行 + 课程行尾操作按钮 */
.range-row { display:flex; align-items:center; gap:10px; margin-bottom:14px; flex-wrap:wrap; }
.range-dash { color:var(--muted); }
.cr-actions { display:flex; gap:6px; margin-left:auto; }
.btn-xs { padding:2px 8px; font-size:11.5px; }
```

- [ ] **Step 5: 删掉页签样式，调整旧规则**

删除 `app.css` 里已失效的规则：

- `.tabs`、`.tab`、`.tab.active`、`.tab.active::after`、`.tab-badge`、`.tab-panel[hidden]`（约 `app.css:86-98`）
- `.range-pill` 与其 `::before`（`app.css:43-46`）—— `#rangePill` 已随顶栏删除
- `.app-header` 及其子规则 —— 顶栏整体取消
- `main` 规则（`app.css:52`）—— 已被 `.content` 取代

保留 `.week-nav`。

窄窗规则改为：

```css
@media (max-width:900px){
  .sidebar { width:64px; padding:16px 8px; }
  .sidebar-logo .logo-text, .nav-item span:not(.nav-ico), .nav-badge { display:none; }
  .content { padding:18px 16px 60px; }
  .stat-row { grid-template-columns:repeat(2,1fr); }
}
```

- [ ] **Step 6: 课表色块换低饱和色**

改 `app.js:1513` 的 `PALETTE`：

```js
/* 低饱和柔和色板：配深色玻璃底不刺眼，相邻课程仍可区分 */
const PALETTE = ["#7aa7d8","#6fb8c4","#9d8fd0","#c98fae","#d9a273",
                 "#8fbf9a","#a8a06f","#7f9fc9"];
```

`PALETTE` 的唯一用例是 `scheduleColor()`（`app.js:1515-1517`），确认它按 `code` 取模索引 —— 色板长度变化不影响逻辑。考试块的深红 `#7f1d1d` + `#ef4444` 描边**保持不变**。

- [ ] **Step 7: 校验 + 冒烟**

Run: `node --check frontend/app.js && node tools/check_dom_ids.mjs`
Expected: 都通过

`python run_app.py`：
- 8 个页面在深色下都正常可读，玻璃与光晕底可见
- 切「设置 → 主题 → 浅色」，浅色下也可读不糊
- 切「跟随系统」，跟随系统外观变化
- 课表周视图色块柔和可分辨，考试红块仍醒目
- 窗口拉窄到 ~640px 侧栏收成图标条，不破版
- 滚动页面时侧栏保持固定

- [ ] **Step 8: 提交**

```bash
git add frontend/app.css frontend/app.js frontend/index.html
git commit -m "style: 深空玻璃暗色主题（侧栏 + 玻璃卡片 + 光晕底），课表色块转低饱和"
```

---

### Task 8: 全量验收

**Files:** 无（只跑验证；发现问题回到对应任务修）

- [ ] **Step 1: 后端回归网**

Run: `.venv/bin/pytest -q`
Expected: 全部通过。**若不通过，说明改动越界碰到了后端**——立刻查 `git diff --stat` 确认改动的文件范围。

- [ ] **Step 2: 静态校验**

Run: `node tools/check_dom_ids.mjs`
Expected: `✓ …`

Run: `for f in frontend/util.js frontend/app.js frontend/shell.js frontend/i18n.js; do node --check $f; done`
Expected: 无输出

- [ ] **Step 3: i18n 键集一致性**

`I18N.zh` 与 `I18N.en` 必须键集完全一致，否则切到英文会出现空白标签。

Run:
```bash
node -e '
const s=require("fs").readFileSync("frontend/i18n.js","utf8");
const I=eval(s.replace(/^const I18N/,"var I18N")+";I18N");
const z=Object.keys(I.zh).sort(), e=Object.keys(I.en).sort();
const only=z.filter(k=>!I.en[k]).concat(e.filter(k=>!I.zh[k]));
console.log(only.length? "✗ 键不一致: "+only.join(", ") : `✓ zh/en 键集一致（各 ${z.length} 个）`);
process.exit(only.length?1:0);'
```
Expected: `✓ zh/en 键集一致`

- [ ] **Step 4: 按 spec §13 清单逐项手动验收**

`python run_app.py`，逐条过 spec §13 的 12 项验收清单（8 页能切 / 首页数字填入 / 未配置时引导卡 / 公告未读红框 / AI 总结样式区分 / 课表周视图与考试红块 / 课程页勾选保持 / 文件下载 / 设置存读 / 深浅色 / 弹层与横幅 / 窄窗不破版）。

- [ ] **Step 5: 确认改动范围没有越界**

Run: `git diff --stat 276c173..HEAD`
Expected: 只出现 `frontend/` 下的文件，加上 `tools/check_dom_ids.mjs` 与 `docs/`。**不应出现任何 `backend/`、`tests/`、`*.spec`、`run_app.py`。**

- [ ] **Step 6: 提交验收记录**

```bash
git add -A
git commit -m "chore: 前端壳层化重构完成，全量验收通过"
```

---

## 自检记录

**Spec 覆盖**：§4 信息架构 → Task 3/4/5/6；§5 视觉方向 → Task 7；§6 文件划分与加载顺序 → Task 2/3；§7 导航模型 → Task 3；§8 首页 → Task 6；§9 各板块页 → Task 3（含 `refreshPill` 退役与断裂点清理）；§10 课程页 → Task 5；§11 设置页 → Task 4；§12 错误处理 → Task 3/6（沿用既有机制 + 首页 `Promise.allSettled`）；§13 测试与验收 → Task 8；§14 风险 → Task 1（自动校验）+ 各任务校验步骤。

**与 spec 的两处偏差（有意为之，实现时同步回写 spec）**：
1. 计划新增了 `tools/check_dom_ids.mjs`（安全网），spec 未提及 —— 它把 spec §14「删元素留引用」这条风险从「靠自觉 grep」变成「自动拦截」。
2. `autoLoadOnOpen()` 从 `app.js` 迁到 `shell.js`（Task 3 Step 4）—— spec §6 只说了拆三个文件，没说它的归属；放在最后加载的 `shell.js` 才能确保调用 `switchPage` / `refreshBadges` 时它们已定义。

**命名一致性**：`switchPage(name)` / `PAGES` / `refreshBadges()` / `setNavBadge(page, n)` / `initHome()` / `homeLoaded` / `renderHomeFromCache()` / `renderHomeToday()` / `renderHomeAnnounceList()` / `courseRowActionsHtml(c)` 在全文定义与引用一致。页面容器 id 统一为 `page-<name>`，`data-page` 值统一为 `<name>`。

**自检中查出并已修正的四处缺陷**（都是会让实现者写错的具体问题，不是措辞）：

1. **`openSettings()` 成了孤儿**。删掉 `#btnSettings` 后，侧栏点击走 `switchPage("settings")`，`openSettings()` 再无调用者 —— 那么「同步 `selTheme` 值 / 刷新 AIMS 状态 / 填充忽略课程 chips」三件事永远不执行。修法：把 `openSettings()` 改为「只做同步、不切页」，挂进 `PAGE_INIT.settings`。**不能**让它内部再调 `switchPage("settings")`，否则 `switchPage → openSettings → switchPage` 无限递归。
2. **原首屏那张卡片在 Task 3 里没有归属**。骨架图里 `#page-courses` 是空的，而「搬移时删掉的东西」只列了顶栏/页签/设置弹窗 —— 那张卡片会被无声丢掉，连带 `#btnLoadCourses`、`#inpStart`、`#inpEnd`、`#courseCheckboxes` 四处 id 消失。已补一张表明确两块内容（该卡片 → `#page-courses`；设置弹窗内容 → `#page-settings`）必须原样搬入。
3. **主题缺省值有两处**，原先只写了一处。`"system"` 同时在 `app.js:49` 和 `index.html:12`，且内联脚本的 `catch` 兜底是 `"light"`。漏改任何一个都会造成「首绘按一个值、逻辑按另一个值」。已改为明确列出两处（含兜底值）。
4. **`typeof` 守卫的注释是错的**。原写「用 `typeof x !== "undefined"` 防止 `let` 未初始化」—— 但 `typeof` 对 TDZ 中的 `let` **同样抛 `ReferenceError`**（它只对**未声明**变量安全），守卫防不住它声称要防的情况。实际也没有 TDZ 风险（`shell.js` 在 `app.js` 完整执行后才解析）。已删掉守卫与那段注释，改为直读并说明理由。
