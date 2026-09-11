# 课程中心 + 课程配置归位 设计稿

日期：2026-09-11
分支：`main`（上一轮 `feat/canvas-discussions-quizzes-pages` 已快进合并，提交 `1d996c0`）

## 1. 背景

上一轮（`2026-09-10-canvas-content-expansion-design.md`）打通了 quizzes / pages /
discussion_topics / planner 四个数据源并做了首页仪表盘。用户人工验收后提出六项改动，
其中 ⑤（讨论区文字溢出）与 ⑥（syllabus 富内容）已修复并合并进 `main`。

本稿处理其余四项，均是验收时暴露的**信息架构问题**，不是新数据源：

| # | 用户原话 | 实质 |
|---|---|---|
| ① | 「忽略课程和加载课程放在一起就行, 全都放设置里」 | 课程配置分散在两页，该收敛到一处 |
| ② | 「文件列表的文件可以直接点名字(超链接)然后打开源文件(而不是canvas网页)」 | 文件行不可点，且没有"打开本体"的路径 |
| ③ | 「主页的今日课表可以点击, 进到课程的页面」 | 首页课表卡片是纯展示，没有出口 |
| ④ | 「点进课程后里面也可以有几个子菜单; 这个课程的文件, 公告, 待办, discussion」 | 课程内容没有按课程聚合的视图 |

**本轮的独特性：不动后端。** ② 需要的字节转发端点已存在（`/api/module_file_stream`），
④ 的四个标签全部复用既有端点。因此这是一个**纯前端轮次**，测试与验收策略相应偏重静态门
与人工目视，而非 pytest 新增用例。

## 2. 目标

1. 课程配置（加载 / 日期范围 / 勾选 / 忽略）合并进设置页一张卡。
2. 侧栏「课程」由"课程配置页"改为"课程中心页"：列出课程 → 点进某一门 → 四个子标签
   （文件 / 公告 / 待办 / 讨论）。
3. 文件在**两个位置**（侧栏文件页、课程 Modules 列表）都可点文件名直接打开源文件。
4. 首页今日课表卡片可点击，直达该课程的课程中心。
5. 后端零改动；不新增数据源。

## 3. 非目标

- 不改后端（`backend/**` 一行不动）。若实施中发现必须改，说明本稿有缺陷，应回来改设计。
- 不动课程详情弹层 `#detailModal` 的既有板块 —— 用户明确要求弹层与课程中心**并存**。
  例外：弹层内的 Modules 弹窗 `#modulePop` 的**打开行为**属于 ② 的范围（见 §5.2(b)）——
  改的只是"点标题也能打开文件"，不增删任何板块、不改布局。
- 不做「课程中心 → 弹层」的互相跳转优化，两者各自独立可用即可。
- 不改 AIMS/Banweb 侧。
- 不引入构建步骤、不用 ES 模块（沿用历次约束）。
- 不加"打开本地文件"的壳层能力（见 §5.2 的形态差异说明）。

## 4. 信息架构落位（本稿最关键的决定）

### 4.1 课程配置 → 设置页

现状是**一件事劈成两页**：

| 现在在哪 | 装了什么 | DOM id |
|---|---|---|
| `#page-courses`（侧栏「课程」） | 加载按钮 / 日期范围 / 课程勾选列表 | `btnLoadCourses` / `inpStart`,`inpEnd` / `courseCheckboxes` |
| `#page-settings` 第 7 张卡 | 已忽略课程 + 清空 | `ignoreCourses` / `btnClearIgnore` |

四样东西都是**配置**（决定"取哪些课的哪些数据"），不是内容。合并进设置页一张
`set-wide` 卡，顺序为：加载按钮 → 日期范围 → 勾选列表（含每行"详情/忽略"）→ 已忽略列表。

**关键约束：所有 DOM id 原样保留。** `$()` 走 `getElementById`，与节点在文档中的位置无关，
所以 `renderCourseCheckboxes()`、`fillIgnoreCourses()`、`range()` 等函数**一行都不用改**。
`index.html:206` 那句「id 不变，`renderCourseCheckboxes()` 直接往里写」的注释正是为此而写，
本轮要延续这个约定。这是一次**纯 DOM 搬迁**，风险集中在"搬漏了哪个 id"。

唯一需要改的 JS 是 `btnLoadCourses.onclick`（`app.js:633-645`）：它现在加载成功后
`switchPage("announce")` 跳去公告页 —— 那是"课程页是主工作区"时代的产物，搬到设置页后
这个跳转会让用户莫名其妙离开设置。改为**留在设置页**并给出加载成功提示。

### 4.2 侧栏「课程」→ 课程中心

`#page-courses` 的容器 id 与 `data-page="courses"` 保留，**内容整体换掉**：从"配置 + 勾选列表"
改为"课程卡片列表 → 点进某一门 → 四子标签"。

导航仍是 9 项，不增不减。用户已确认此方案。

课程中心是**两级**结构：

```
课程中心（列表页）
  └─ 点某一门课 → 课程中心（详情页，四子标签）
        ├─ 文件
        ├─ 公告
        ├─ 待办
        └─ 讨论
```

详情页顶部需要：课程名 + 一个「← 返回列表」。返回后回到列表页，不切走侧栏。

**一处容易猜错的地方**：设置页勾选列表每行的「详情」按钮（`courseRowActionsHtml`，
`app.js:659-663`）现在打开的是弹层 `openCourseDetail()`。本轮**保持它开弹层不变** ——
设置页是配置语境，「详情」是"快速看一眼"；课程中心的入口是侧栏「课程」与首页课表卡片（③）。
两者并存正是用户的要求，不要为了"统一"把这里也改成跳课程中心。

### 4.3 四个子标签的数据来源（均已在全局就绪）

| 子标签 | 全局变量 | 按课程过滤的方式 |
|---|---|---|
| 文件 | `fileCourses` | `fileCourses.find(c => c.course_id === cid)` |
| 公告 | `summaryResults` | `summaryResults.find(c => c.course_id === cid)` |
| 待办 | `todoItems` | `todoItems.filter(i => i.course_id === cid)` |
| 讨论 | `discussData.by_course` | **`by_course[String(cid)]`** —— 见下方警告 |

⚠️ **JSON 键字符串陷阱（会静默失败）。** 后端 `get_announcements` 返回
`dict[int, list]`（`canvas_client.py:461`），但经 JSON 往返后**键变成字符串**。
既有代码的应对是在读取时 `Number(k)` 归一（`app.js:207`）。
讨论子标签若写成 `by_course[cid]`（`cid` 是 Number）将**恒为 undefined 且不报错** ——
与上一轮 `renderHomeToday()` 字段臆造属于同一类"看着正常、其实是空的"故障。
**统一取用方式：`by_course[String(cid)] || []`。**

### 4.4 数据策略：混合（先渲染缓存，后台刷新）

用户未指定，按工程判断定为**混合**：

1. 进入某门课的子标签时，若全局变量已有该课程数据 → **立即渲染**（无等待）。
2. 同时在后台按该课程拉一次新数据，落地后若有变化则替换。

理由：纯缓存会让用户看到过期数据且无从察觉；纯现拉则在每次切换标签时都要等待。
两个极端都不好。混合的代价是"可能先显示旧值再跳变"，可接受。

**但"后台刷新"只对能按课程粒度的端点做**：

| 子标签 | 刷新方式 |
|---|---|
| 文件 | `api("list_files", {course_ids:[cid], ...})` |
| 讨论 | `api("discussions", {course_ids:[cid]})` |
| 公告 | 复用 `syncAnnouncements()` 的既有流程（它按 `selectedCourses()` 批量取，粒度是全部选中课） |
| 待办 | `/api/todo` 无课程参数，只能整批取；本轮**不做后台刷新**，只用全局缓存 |

这是本稿唯一一处四标签不对称的地方，写在此处以免实施时被当作遗漏。

## 5. 前端设计

### 5.1 文件划分（沿用现有四个文件，不新增）

| 文件 | 本轮职责 |
|---|---|
| `frontend/index.html` | 搬迁课程配置区块；`#page-courses` 换成课程中心骨架（列表容器 + 详情容器 + 四个标签容器）；文件页每行加可点元素 |
| `frontend/app.js` | ② 的打开逻辑；**④ 的全部渲染函数**（新增，见 5.4）；`btnLoadCourses.onclick` 去掉跳转副作用 |
| `frontend/shell.js` | ③ 今日课表卡片加 `data-code` + 点击委托；`PAGE_INIT` 挂 `initCourseHubTab` |
| `frontend/i18n.js` | 新增文案键（zh + en 同步） |
| `frontend/app.css` | 子标签栏样式；课程卡片列表样式；文件名可点态 |

### 5.2 ② 文件直链

**用户已确认：浏览器形态内联打开，桌面形态退化为下载。**

两处都要做（用户明确「两个都要」）：

**(a) 侧栏文件页**（`renderFiles`，`app.js:1152-1186`）
现在文件名是裸文本：`<div class="item-title">${esc(f.display_name)} …`（`app.js:1172-1175`），
`#filesArea` 的委托只认 `.caret` 与 `.cs`（`app.js:1211-1232`）。改为：

```html
<a href="#" class="file-open" data-cid="${c.course_id}" data-fid="${f.file_id}"
   data-name="${escAttr(f.display_name)}">${esc(f.display_name)}</a>
```

新增一个 `#filesArea` 委托分支处理 `.file-open`。

**(b) 课程 Modules 弹层**（`#modulePop`）
弹层现已有「打开文件 / 打开页面」两个按钮（`btnModuleOpenFile` / `btnModuleOpenPage`）。
本轮把弹层标题也变成可点（等同于点「打开文件」），这样"点名字就能打开"在模块列表里也成立。

**两种形态的行为分叉**（这是用户确认过的设计，实施时不要"顺手统一"）：

| 形态 | 判定 | 行为 |
|---|---|---|
| 浏览器 | `!window.CanvBridgeShell.isWebview()` | `fetch /api/module_file_stream` → `blob` → `URL.createObjectURL` → `window.open`（PDF 走浏览器内置查看器） |
| 桌面（内嵌窗口） | `isWebview()` | 调 `api("download_module_item", …)` 落盘，提示保存路径 |

桌面形态为什么不能内联打开 —— **这是已实测的结论，不是偷懒**：
`app.js:1041-1046` 在 `isWebview()` 时主动隐藏「打开文件」按钮，因为内嵌 WKWebView 没有
"新标签页 PDF 查看器"；而 `blob:` 也不被外链桥白名单接受（`app.js:17` 只放行 `^https?:`），
壳层 Python 侧 `_ShellApi` 只暴露 `openExternal` 且硬过滤 http(s)（`run_app.py:59`）。
要突破必须新增一项 JS→Python 能力（打开本地路径），属安全面扩张 —— 用户已选择**不做**。

前端实现要点：把 `btnModuleOpenFile.onclick`（`app.js:1115-1139`）里那段
`fetch → blob → window.open` 抽成具名函数 `openFileInline(courseId, fileId, name)`，
文件页与弹层共用；桌面分支抽成 `downloadFileTo(courseId, fileId, name)`。
`api()`（`util.js:26-33`）无条件 `await r.json()`，**不能用于二进制端点**，内联路径必须用裸 `fetch`。

### 5.3 ③ 首页今日课表卡片可点击

`renderHomeToday()` 在 **`shell.js:157-179`**（不在 app.js）。现在 `slots.push` 只有
`{start, end, room, label}`（`shell.js:170-171`），**不带课程 id**，必须补：

```js
slots.push({ start: m.start_min, end: m.end_min, room: m.room_short || m.room,
             label: `${c.code} ${c.section}`, code: c.code });
```

卡片加 `data-code="${escAttr(s.code)}"`，在 `#homeTodayList` 上挂一个委托（仿
`app.js:1900-1908` 的 `.cal-detail` 分支），点中后：

```js
const bc = banwebSchedule.courses.find(c => c.code === code) || null;
const canvas = matchCourseByCode(code);          // app.js:210
openCourseHub(canvas ? canvas.id : null, bc);    // 未匹配到 Canvas 时退化为纯 Banweb
```

两个坑：
- **不要给卡片加 `data-goto`** —— `#page-home` 已有一个 `[data-goto]` 委托（`shell.js:87-90`），
  会误触页面跳转。
- `openCourseHub` 必须能接受 `canvasId` 为 null（Banweb 有课但 Canvas 没匹配上），
  此时课程中心退化为只显示课表信息 + 提示未匹配到 Canvas 课程。

### 5.4 ④ 课程中心页

**不复用全局渲染函数。** `renderFiles` / `renderSummaries` / `renderTodo` 都把容器 id 写死
（`#filesArea` / `#summaries` / `#todoGroups`），且工具栏与勾选逻辑在顶层一次性绑在
`#page-files` 等页面的固定 id 上（`app.js:1202-1269`）。硬复用只有两条路：沿用同一批 id
（则弹层与全局页无法并存），或把这些函数参数化（动的是已在跑的代码，回归风险实打实）。

更根本的是：**这两者本来就不是同一个视图**。全局文件页是"所有课程、可展开收起"，
课程中心是"这一门课的文件"。一个是跨课程，一个是单课程。所以"复用"是假的经济性 ——
**给课程中心写独立的按课程渲染函数**，读同一批全局数据、各自渲染。风险为零，代价是
一些渲染代码重复（本稿明确接受这个代价）。

新增函数（均在 `app.js`）：

| 函数 | 职责 |
|---|---|
| `initCourseHubTab()` | `PAGE_INIT.courses` 的入口：首次进入加载课程列表（沿用 house pattern，见下） |
| `renderCourseHubList()` | 课程卡片列表（点进详情） |
| `openCourseHub(canvasId, banwebCourse)` | 进入某门课的详情视图，设定 `hubCid` / `hubBanweb` 并渲染 |
| `renderCourseHub()` | 详情视图外壳：课程名 + 返回 + 四标签栏 + 当前标签容器 |
| `switchHubTab(name)` | 切换标签（`hubTab`），高亮 + 重渲当前标签 |
| `renderHubFiles/Announce/Todo/Discuss(cid)` | 四个标签各自的渲染 |

**首次进入自动加载课程列表**（沿用既有 house pattern，不是新发明）：
`shell.js:11-18` 的 `PAGE_INIT` 里 `discuss` / `grades` / `home` / `schedule` 都是
"首次进入才拉数据"，`courses` 照此办理。设置页的加载按钮因此变成**手动刷新**，
而不是唯一入口 —— 否则用户在设置页点过一次加载之前，课程中心会一直是空的。
（若 `courseList` 为空且 Canvas 未配置，列表页显示空态并提示去设置页。）

**标签栏是新建组件。** 仓库里**不存在**任何 `role="tab"` / `.tabs` / `.segmented` 结构
（`app.css` 与 `index.html` 均无）。但有五个孤儿 i18n 键可复用作标签文案：
`tab.announce` / `tab.files` / `tab.schedule` / `tab.todo` / `tab.grades`
（`i18n.js:32-39`，英文 `:367-374`）—— 它们定义后从未被任何 JS 引用。
本轮复用其中三个（`tab.files` / `tab.announce` / `tab.todo`），讨论标签新增 `tab.discuss`。

**切语言要重渲新页面。** `applyLang()`（`i18n.js:680`）里已无条件调用
`renderSummaries(); renderFiles(); renderSchedule();`，课程中心的渲染函数必须一并挂进去，
否则切语言后新页面文案不刷新。

### 5.5 i18n

新增键（zh / en 各一份，必须成对）：

| 键 | 用途 |
|---|---|
| `nav.courses` | 已存在，语义从"课程配置"变为"课程中心"，文案可不变 |
| `tab.discuss` | 讨论标签（其余三个复用孤儿键） |
| `hub.back` | 「← 返回列表」 |
| `hub.empty` / `hub.need_config` | 列表页空态 / 未配置 Canvas |
| `hub.no_canvas` | Banweb 有课但未匹配到 Canvas 课程 |
| `hub.tab_empty.files/announce/todo/discuss` | 四个标签各自的空态（措辞要区分"没有"与"没加载"） |
| `file.open` / `file.open_fail` / `file.downloaded` | ② 的打开/失败/已下载提示 |
| `settings.group.course` | 设置页合并卡的组标题 |

**空态文案必须区分两种"空"**：这门课确实没有（Canvas 返回空）vs 尚未加载（全局变量为
null）。两者混用一句"暂无"会让用户以为数据没了 —— 上一轮 `files.no_files` 与
`files.empty` 就是分开的，沿用该做法。

## 6. 错误处理

沿用既有约定（`main.py` 不用 `HTTPException`，一律 200 + JSON）：

- ② 内联打开失败：`resp.ok` 为假或 `Content-Type` 是 `application/json` → 提示
  `file.open_fail` + 后端错误串，**并自动降级为下载**（不要只是报错，用户的目标是拿到文件）。
- ② 桌面形态下载失败：走既有 `download_module_item` 的 `{ok:false,error}` 提示。
- ④ 某标签数据缺失：该标签内显示空态，**不影响其余三个标签**（各标签独立渲染，无整块失败）。
- ④ 后台刷新失败：静默保留已渲染的缓存数据，只在标签内给一条弱提示，不清空。

## 7. 测试与验收

### 7.1 自动化（本轮必须写）

后端零改动 → **不新增 pytest 用例**（`tests/` 保持 228 passed 作为回归门）。

新增一个静态门 **`tools/check_i18n_keys.mjs`**：校验 `i18n.js` 里 `I18N.zh` 与 `I18N.en`
的键集合完全一致，并报告"定义了但从未被引用"的孤儿键。

理由：本轮要新增约 10 个键、还要复用 5 个孤儿键，而孤儿键这个现象本身就是
**没有检查导致的**（`tab.*` 那五个定义了从未使用，谁也没发现）。纯靠人工看双语文件
一定会漏。这个门和既有的 `tools/check_dom_ids.mjs` 是同一类防线。

### 7.2 静态门（沿用，全部必须绿）

```bash
for f in frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js; do node --check "$f"; done
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/c.js && node --check /tmp/c.js
node tools/check_dom_ids.mjs
node tools/check_i18n_keys.mjs      # 本轮新增
.venv/bin/python -m pytest -q       # 期望 228 passed，不变
```

四文件拼接那一道**不能省**：四个文件共享同一全局词法环境，重复的顶层 `const`/`let`
会抛 `SyntaxError` 并让整页白屏 —— 单文件 `--check` 查不出来。

### 7.3 人工验收（无法自动化，需人工执行）

用 `run_app.py --browser` 跑**浏览器形态**（② 的内联打开只在浏览器形态成立），逐条：

1. 设置页能看到合并后的课程卡：加载按钮、日期范围、勾选列表、已忽略列表**在同一张卡里**。
2. 点加载 → 列表填充，**页面不跳走**（旧行为会跳去公告页）。
3. 勾选/忽略在设置页改动后，课程中心列表同步变化。
4. 侧栏「课程」进去是课程列表，不是配置项。
5. 点某一门课 → 出现四个标签，逐个点开都有内容或明确的空态。
6. 文件页点文件名 → 浏览器新标签页内联渲染 PDF（**不是下载、不是 Canvas 网页**）。
7. 课程 Modules 弹层点标题 → 同上。
8. 首页今日课表点某张卡片 → 进入该课程的课程中心详情页。
9. 课程中心详情页点「← 返回列表」→ 回列表，侧栏不跳动。
10. 切语言（中↔英）→ 课程中心的列表、四个标签、空态文案**全部跟着变**（不刷新页面）。

再用默认形态（内嵌原生窗口，`run_app.py` 不带参数）跑一遍 6、7：**预期是下载而非内联打开**，
并提示保存路径。这是设计如此，不是缺陷。

## 8. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| DOM 搬迁搬漏 id | 相关功能静默失效（`$()` 返回 null） | 所有 id 原样保留；`check_dom_ids.mjs` 兜底；搬迁后逐 id 对照 |
| 讨论子标签用数字键查 `by_course` | 该标签恒为空且不报错 | §4.3 已写明用 `String(cid)`；人工验收第 5 条专门覆盖 |
| 四个标签的"未加载"被当成"没有" | 用户以为数据丢了 | §5.5 要求空态文案分两种；§6 要求标签间互不影响 |
| 切语言后新页面不刷新 | 文案停留在旧语言 | `applyLang()` 挂新渲染函数；人工验收第 10 条覆盖 |
| 桌面形态被误以为"坏了" | 用户报缺陷 | §7.3 明确写出预期为下载；`file.downloaded` 提示给出保存路径 |
| 课程中心与弹层两套渲染逻辑漂移 | 同一份数据两种呈现不一致 | 本轮接受该代价（§5.4）；若后续要收敛，应作为独立重构立项 |

## 9. 实施顺序建议

④ 依赖 ①（课程中心需要页面容器腾出来），③ 依赖 ④（点击要跳进课程中心），
② 与其余三项**完全独立**。建议顺序：① → ④ → ③ → ②，或把 ② 提前单独做掉。
具体拆分留给实施计划。
