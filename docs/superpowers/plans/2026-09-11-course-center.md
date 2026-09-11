# 课程中心 + 课程配置归位 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把课程配置收敛进设置页，把侧栏「课程」改成"列表 → 单课程四子标签"的课程中心，让文件在两个位置都能点名字直接打开源文件，并让首页今日课表卡片可点击直达该课程。

**Architecture:** 纯前端轮次，后端零改动。四项改动全部落在既有的四个 `<script>` 文件与 `index.html`/`app.css` 上，沿用「无构建步骤、无 ES 模块、共享同一全局词法环境」的既有约束。课程中心是新增的两级视图（列表 ↔ 详情），与既有课程详情弹层 `#detailModal` **并存**、互不替代。数据全部来自四个已在全局就绪的缓存变量（`fileCourses` / `summaryResults` / `todoItems` / `discussData`），不新增数据源。

**Tech Stack:** 原生 JS（classic script，无模块打包）、原生 CSS（CSS 变量主题）、Python + FastAPI 后端（本轮只读不改）、Node.js 仅用于静态门脚本（无 npm 依赖、无 `node_modules`）。

**Spec:** `docs/superpowers/specs/2026-09-11-course-center-design.md`（本计划是它的论证；二者冲突时以 spec 为准）

## Global Constraints

以下逐条抄自 spec，**每个任务的要求都隐含包含本节**：

- **不改后端**（`backend/**` 一行不动）。若实施中发现必须改，说明 spec 有缺陷，应停下来回去改设计，而不是就地改后端。
- **不动课程详情弹层 `#detailModal` 的既有板块** —— 用户明确要求弹层与课程中心**并存**。唯一例外：弹层内 Modules 弹窗 `#modulePop` 的**打开行为**属于 ② 的范围（Task 3）。
- **不做课程中心 ↔ 弹层的互相跳转优化**，两者各自独立可用即可。
- **不改 AIMS/Banweb 侧。**
- **不引入构建步骤、不用 ES 模块。** 四个文件仍是 `<body>` 末尾的 classic `<script>`，加载顺序硬性为 `i18n.js → util.js → app.js → shell.js`（`index.html:330-333`），共享同一全局词法环境。
- **不加"打开本地文件"的壳层能力**（安全面扩张，用户已选择不做）。
- **`main.py` 不用 `HTTPException`**；本轮不新增端点，此条仅为理解既有错误约定而列。
- 语言文案 **zh / en 必须成对**，新增键两侧同一次提交内加齐。
- 环境：Python 入口是 `.venv/bin/python`（裸 `python` 不在 PATH）；测试基线 **228 passed**，本轮**不新增 pytest 用例**；**macOS 没有 `timeout` 命令**。

### 计划级裁定（与 spec 文字的偏差，已记录）

| # | 裁定 | 理由 | 判断错了的代价 |
|---|---|---|---|
| R1 | 实施顺序改为 **① → ② → ④ → ③**，而非 spec §9 建议的 ① → ④ → ③ → ② | ④ 的文件子标签需要"点文件名打开"这一能力，而该能力由 ② 建立的共享函数 `openFileSmart()` 提供。把 ② 放前面，④ 直接复用；放后面则文件子标签要先做成不可点、再回头改一遍，纯属返工。spec §9 结尾已写明「具体拆分留给实施计划」，故此为授权范围内的拆分。 | 若顺序判断有误，代价是 Task 5 需要等 Task 3 先落地；但两者本就串行执行，无实际损失。 |
| R2 | 文件页的 `.file-open` 用 `data-ci`（`fileCourses` 下标）而非 spec §5.2 写的 `data-cid` | 同一行内既有的复选框已经用 `data-ci="${c._orig}"`（`app.js:1173`），`#btnDownloadFiles` 也按 `fileCourses[Number(i.dataset.ci)]` 取值（`app.js:1241`）。同一个容器里两种取数口径并存是后续误读的源头。 | 无——两种取法都能拿到同一门课，仅口径统一问题。 |
| R3 | 公告子标签的后台刷新**加 60 秒 TTL**（同一课程同一标签 60s 内不重复拉取） | spec §4.4 要求"先渲染缓存、后台刷新"，但公告走的是 `syncAnnouncements()` —— 一个**跨全部选中课程**的批量端点。每次点标签都跑一遍代价过大，且会在设置页之外弹 `setStatus` 噪音。 | 若 TTL 过长，用户可能看到最多 60 秒前的公告；属可接受范围，且切走再回来会重新计时。 |
| R4 | 公告子标签的正文用 `.announce-msg expanded`（**始终全文**），不复制"展开"按钮 | `.announce-msg` 默认 `-webkit-line-clamp:4; overflow:hidden`（`app.css:226-227`），而展开按钮的布线与点击委托都**只扫 `#summaries`**（`wireAnnounceExpands()` `app.js:869-870`、委托 `app.js:888`）。直接复用 `.announce-msg` 而不复用按钮，会导致长公告被**静默截断且无法展开**。加 `expanded` 一行解决，且不必改动正在跑的 `#summaries` 代码。 | 课程中心里公告不折叠，长公告卡片较长；功能正确，仅观感差异。 |
| R5 | 给 `#courseCheckboxes` 所在的 `.course-list` 补一条 flex 规则 | `.course-list` 这个类名在 `index.html:207` 出现，但 `app.css` 里**没有任何规则**（本轮已实测确认）。它现在落在设置页卡片内，而 `.chip` 自身无 margin，靠 `.chips` 的 `gap` 才有间距 —— 搬进设置页后会挤成一坨，看起来像"搬迁搬坏了"。 | 极小：仅课程勾选区的外观比现状好看，无功能面。 |
| R6 | 删除被本轮废弃的 `module.open_file_fail` 与 `module.fetching`，以及 `settings.group.ignore` | 这三个键在本轮之后不再有任何引用，留着就是死键，而本轮正好引入了死键基线门（Task 1）—— 让门一开工就自带"新增即失败"的压力，是任务范围内的清理，不是无关重构。 | 若误判（仍有引用），`check_i18n_keys.mjs` 的悬空引用检查会立刻报错，不会静默。 |
| R7 | Tasks 5–8 里「起真身，人工确认」的目视步骤**不在实施者侧执行**：换成结构性替代（新 id/结构确在 HTML 中、四文件拼接 `--check` 通过），并在报告中**逐条列出哪些目视项未验证**；浏览器实机核对改由控制方用 chrome-devtools 执行 | 实施者是子代理，看不见浏览器，无法诚实签收目视项——写"确认通过"就是不实报告。而 Task 3 那个 Critical 的教训恰恰是**静态门全绿、运行时行为全错**：把唯一能发现这类缺陷的环节交给看不见画面的人，等于没有这道环节 | 若控制方也漏做，这四步的目视项就无人验证；代价是缺陷漏到用户人工验收，已列入验收清单，不会静默消失 |

---

## 文件结构

| 文件 | 本轮职责 | 改动量级 |
|---|---|---|
| `tools/check_i18n_keys.mjs` | **新增**。静态门：zh/en 键集一致、无悬空引用、死键数不超基线 | ~85 行 |
| `frontend/index.html` | 搬迁课程配置区块到设置页；`#page-courses` 整体换成课程中心骨架（列表容器 + 详情容器 + 标签栏 + 面板） | 三处结构性改动 |
| `frontend/app.js` | ② 的共享打开器；④ 的全部渲染函数（列表/详情/四标签）；调整 `btnLoadCourses.onclick` | 新增 ~230 行 |
| `frontend/shell.js` | `PAGE_INIT.courses` 挂 `initCourseHubTab`；`renderHomeToday` 补 `code` 字段并加点击委托 | ~25 行 |
| `frontend/i18n.js` | 新增 `hub.*` / `file.*` / `tab.discuss` / `settings.group.course`；删除被废弃的 3 个键 | zh/en 各 ~16 行 |
| `frontend/app.css` | `.hub-card` / `.tab-bar` / `.tab-item` / `.file-open` / `.course-list` / `.today-card` 可点态 | ~30 行 |

**不新增 JS 文件。** 课程中心的全部函数写进 `app.js`（与其它板块一致），不单独拆文件 —— 这个仓库的约定是"app.js 装板块、shell.js 装壳层"，新开文件会打破 `index.html:330-333` 的硬性加载顺序约定。

---

## Task 1: 静态门 `tools/check_i18n_keys.mjs`

**Files:**
- Create: `tools/check_i18n_keys.mjs`

**Interfaces:**
- Consumes: 无（本任务不依赖任何前序任务）
- Produces: 可执行命令 `node tools/check_i18n_keys.mjs`，退出码 0 = 通过、1 = 失败。后续**每一个**任务的验收步骤都调用它。文件内的常量 `DEAD_BASELINE` 会被后续任务按本计划的预期值修改。

**背景（实施者必读）**：`frontend/i18n.js` 是一份双语字典，`I18N.zh`（1-336 行）与 `I18N.en`（337-671 行）各写一遍。三类错误都不会让页面报错、只会静默劣化：
1. 只在一侧加键 → 另一侧 `t()` 查不到 → 回退到 zh（`i18n.js:676` 的 `?? I18N.zh[key]`），英文界面里冒出中文。
2. 引用了不存在的键 → `t()` 原样返回键名 → 用户看到 `hub.no_canvas` 这种裸键。
3. 加了键却从没引用 → 死键堆积。

**门的判定规则（已实测确定，不要自由发挥）：**

| 规则 | 含义 | 当前实测值 |
|---|---|---|
| 键集一致 | `Object.keys(I18N.zh)` 与 `Object.keys(I18N.en)` 必须完全相同 | 338 = 338 ✓ |
| A. 判活 | 键在 `util.js`/`app.js`/`shell.js`/`index.html` 里作为**带引号的完整字面量**出现过（`"key"` 或 `'key'` 或 `` `key` ``） | 313 个活 |
| B. 动态家族 | 源码里存在以 `.` 结尾的引号字面量（如 `"wd."`），则该前缀下的键一律判活 | 仅 `wd.`（`app.js:1838` 的 `t("wd."+d)`），覆盖 `wd.0`…`wd.6` |
| 死键 | 既不符 A 也不符 B | **25 个** |
| 悬空引用 | 被引用的键里，`I18N` 中不存在的 | 0 个 |

**为什么规则 A 用「带引号字面量」而不是「`t(` 后紧跟字符串」**：本计划实测过更窄的口径，它把 14 个**活键**误判成死键 —— `alert.*` 9 个走数组表 `ALERTS`（`util.js:8`）、`unread.*` 3 个走 `BADGE_KEY[page] || "unread.announce"`（`shell.js:58`）、`announce.filter.all`/`files.filter.all` 走三元（`util.js:54`）。一个报 39 个死键（其中 14 个是活的）的门会被使用者学会忽略，比没有门更糟。带引号字面量口径同时覆盖这三种间接引用，且实测与更宽松的子串口径结论一致（都是 25），可信。

- [ ] **Step 1: 写门脚本**

创建 `tools/check_i18n_keys.mjs`：

```js
#!/usr/bin/env node
/**
 * i18n 键交叉校验：比对 frontend/i18n.js 里 I18N.zh 与 I18N.en 的键集合，
 * 并扫描前端文件里对这些键的引用。
 *
 * 存在的意义：i18n.js 是一份双语字典，zh 与 en 各写一遍。下面三类错误
 * 都不会让页面报错，只会静默劣化，肉眼极难发现：
 *   - 只在一侧加键   → 另一侧 t() 查不到 → 回退到中文（英文界面冒中文）
 *   - 引用了不存在的键 → t() 原样返回键名 → 用户看到 "hub.no_canvas" 这种裸键
 *   - 加了键从未引用   → 死键堆积（本轮开工时已有 25 个）
 * 和 tools/check_dom_ids.mjs 是同一类防线：改完立刻能发现，而不是等跑起来看。
 *
 * 用法：node tools/check_i18n_keys.mjs
 * 退出码：0 = 通过，1 = 双语键集不一致 / 存在悬空引用 / 死键数超出基线
 */
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONTEND = join(ROOT, "frontend");

/* 死键基线：只允许持平或下降。新增死键（加完忘了用）会让计数超过它 → 失败。
   数字来自实测，不是估计值；某轮把死键用起来之后，应把它下调到新的实测值。 */
const DEAD_BASELINE = 25;

/* 动态家族前缀：源码里存在 "wd." 这样的引号字面量，键由它拼出来
   （app.js 的 t("wd."+d)）。这类键无法被字面量扫描判活，只能按前缀整体放行。 */
const DYNAMIC_PREFIXES = ["wd."];

/* 只取到 const LANG 为止：后半段是 t()/applyLang() 等运行时代码，
   在 Node 里跑会碰到 document / localStorage。前半段是一次纯对象字面量赋值。 */
const i18nSrc = readFileSync(join(FRONTEND, "i18n.js"), "utf8");
const cut = i18nSrc.indexOf("const LANG");
if (cut < 0) {
  console.error("✗ 在 frontend/i18n.js 里找不到 `const LANG`，无法切出字典片段。");
  console.error("  （若该文件被重构过，请同步更新本脚本的切片锚点。）");
  process.exit(1);
}
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(i18nSrc.slice(0, cut) + "\n;globalThis.__I18N = I18N;", sandbox);
const I18N = sandbox.__I18N;

const zhKeys = Object.keys(I18N.zh);
const enKeys = Object.keys(I18N.en);
const zhSet = new Set(zhKeys);
const enSet = new Set(enKeys);

const REF_FILES = ["util.js", "app.js", "shell.js", "index.html"];
const blob = REF_FILES.map(f => readFileSync(join(FRONTEND, f), "utf8")).join("\n");

const escapeRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/* 规则 A：键作为带引号的完整字面量出现（覆盖 t("k")、["k"]、cond ? "k" : "j"、X || "k"） */
const isLive = k => new RegExp("[\"'`]" + escapeRe(k) + "[\"'`]").test(blob);
/* 规则 B：动态家族 */
const isDynamic = k => DYNAMIC_PREFIXES.some(p => k.startsWith(p));

/* 悬空引用：t("…") 与 data-i18n*="…" 里出现的、字典中没有的键。
   以 `.` 结尾的是动态拼接（t("wd."+d)），按前缀跳过。 */
const referenced = new Set();
for (const m of blob.matchAll(/\bt\(\s*["'`]([^"'`]+)["'`]/g)) if (!m[1].endsWith(".")) referenced.add(m[1]);
for (const m of blob.matchAll(/data-i18n(?:-ph|-aria)?="([^"]+)"/g)) referenced.add(m[1]);

const fail = [];

const onlyZh = zhKeys.filter(k => !enSet.has(k)).sort();
const onlyEn = enKeys.filter(k => !zhSet.has(k)).sort();
if (onlyZh.length || onlyEn.length) {
  fail.push("I18N.zh 与 I18N.en 的键集不一致：");
  onlyZh.forEach(k => fail.push(`  zh 有、en 缺: ${k}`));
  onlyEn.forEach(k => fail.push(`  en 有、zh 缺: ${k}`));
}

const missing = [...referenced].filter(k => !zhSet.has(k)).sort();
if (missing.length) {
  fail.push("以下键被引用但字典里没有定义（页面上会显示成裸键名）：");
  missing.forEach(k => fail.push(`  ${k}`));
}

const dead = zhKeys.filter(k => !isLive(k) && !isDynamic(k)).sort();

if (fail.length) {
  console.error("✗ i18n 键检查未通过：\n");
  fail.forEach(l => console.error("  " + l));
  console.error("");
  process.exit(1);
}

console.log(`✓ I18N.zh / I18N.en 键集一致（各 ${zhKeys.length} 键）`);
console.log(`✓ 被引用的键全部有定义（共 ${referenced.size} 个不同键）`);
if (dead.length > DEAD_BASELINE) {
  console.error(`\n✗ 死键 ${dead.length} 个，超出基线 ${DEAD_BASELINE}：\n`);
  dead.forEach(k => console.error("  " + k));
  console.error(`\n新增了键却没有引用它。要么用起来，要么删掉；`);
  console.error(`确实要保留（例如给下一个任务预留）就下调 DEAD_BASELINE 并说明原因。`);
  process.exit(1);
}
console.log(`✓ 死键 ${dead.length} 个（基线 ${DEAD_BASELINE}，允许持平或下降）`);
if (dead.length) dead.forEach(k => console.log(`    · ${k}`));
```

- [ ] **Step 2: 确认门在**当前**代码树上是绿的，且报出的死键数正好是 25**

Run:
```bash
node tools/check_i18n_keys.mjs
```
Expected: 退出码 0。前三行 `✓` 各一条，且 `死键 25 个（基线 25，…）`，随后列出 25 个键，首个是 `btn.settings`、末个是 `tab.todo`。

**注意：这一步不会"先失败"** —— 当前代码树的键集本来就一致、也没有悬空引用。这是实测结论，不是猜测。所以本任务用**故障注入**来证明门真的有牙齿，见 Step 3。若此处报出的不是 25，**停下来**：说明规则口径与实测不符，先修正脚本再往下走，不要改基线数字去迁就。

- [ ] **Step 3: 故障注入 —— 证明门能抓住三类错误**

三处注入、三次验证、三次还原。**每做完一项立刻还原**，用 `git diff --stat` 确认回到干净状态再做下一项。

**注入 A（双语不对称）**：在 `frontend/i18n.js` 的 `I18N.zh` 段内任意位置加一行 `"zz.probe": "探针",`。

Run: `node tools/check_i18n_keys.mjs`
Expected: 退出码 1，输出包含 `zh 有、en 缺: zz.probe`。

还原：删掉该行，`git diff --stat` 应无输出。

**注入 B（悬空引用）**：在 `frontend/shell.js` 末尾加一行 `const _probe = t("zz.no_such_key");`

Run: `node tools/check_i18n_keys.mjs`
Expected: 退出码 1，输出包含 `zz.no_such_key`。

还原：删掉该行。

**注入 C（死键超基线）**：在 `frontend/i18n.js` 的 `I18N.zh` 与 `I18N.en` **两段**各加一行 `"zz.dead": "死键",` 与 `"zz.dead": "dead",`（两侧都加，这样不会先触发键集不一致）。

Run: `node tools/check_i18n_keys.mjs`
Expected: 退出码 1，输出包含 `死键 26 个，超出基线 25`。

还原：两行都删掉。

- [ ] **Step 4: 确认还原干净并提交**

Run:
```bash
git diff --stat
node tools/check_i18n_keys.mjs && echo "GATE OK"
```
Expected: `git diff --stat` 只显示 `tools/check_i18n_keys.mjs` 一个新增文件（若它已被 `git add`，则显示为空）；门输出以 `GATE OK` 结尾。

```bash
chmod +x tools/check_i18n_keys.mjs
git add tools/check_i18n_keys.mjs
git commit -m "test: 新增 i18n 键静态门（双语一致/悬空引用/死键基线）"
```

---

## Task 2: ① 课程配置搬进设置页

**Files:**
- Modify: `frontend/index.html`（`#page-courses` 190-208 清空为占位；`#page-settings` 第 7 张卡 281-288 换成合并卡）
- Modify: `frontend/app.js:633-645`（`btnLoadCourses.onclick` 去掉切页副作用）
- Modify: `frontend/i18n.js`（zh/en 各加 1 键、各删 1 键）
- Modify: `frontend/app.css`（`.course-list` / `.set-sep` 补规则）

**Interfaces:**
- Consumes: Task 1 的 `node tools/check_i18n_keys.mjs`
- Produces: `#page-courses` 被腾空（Task 4 接手填内容）。**所有既有 DOM id 原样保留**：`btnLoadCourses` / `inpStart` / `inpEnd` / `courseCheckboxes` / `ignoreCourses` / `btnClearIgnore` 全部仍在文档中，只是换了父节点 —— `$()` 走 `getElementById`，与位置无关，所以 `renderCourseCheckboxes()` / `fillIgnoreCourses()` / `range()` / `defaultRange()` 一行都不用改。

**这是一次纯 DOM 搬迁。** 风险集中在"搬漏了哪个 id"，由 `tools/check_dom_ids.mjs` 兜底。**不要顺便重命名 id、不要顺便加功能。**

- [ ] **Step 1: 确认搬迁前的基线是绿的**

Run:
```bash
for f in frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js; do node --check "$f" || exit 1; done
node tools/check_dom_ids.mjs
node tools/check_i18n_keys.mjs
.venv/bin/python -m pytest -q
```
Expected: 四条全绿；pytest 输出 `228 passed`。

- [ ] **Step 2: 把课程配置搬进设置页**

在 `frontend/index.html` 中，把 `#page-courses` 整块（190-208 行）替换为：

```html
    <div id="page-courses" class="page" hidden>
      <!-- 课程中心（列表 ↔ 单课程四标签）。本轮先留骨架，内容由 Task 4 填。
           原先放在这里的课程配置（加载按钮/日期范围/勾选列表）已搬进设置页。 -->
      <div class="page-head"><h2 data-i18n="nav.courses"></h2></div>
    </div>
```

再把 `#page-settings` 里最后那张卡（原 281-288 行，`settings.group.ignore` 那张）整块替换为：

```html
        <section class="set-card set-wide">
          <span class="eyebrow" data-i18n="settings.group.course"></span>
          <div class="row" style="margin-bottom:12px">
            <button id="btnLoadCourses" class="btn btn-primary" data-i18n="btn.load_courses"></button>
            <span class="sp"></span>
            <span class="muted" data-i18n="courses.hint_selected"></span>
          </div>
          <div class="range-row">
            <label class="field"><span data-i18n="courses.range_label"></span></label>
            <input id="inpStart" type="date">
            <span class="range-dash">–</span>
            <input id="inpEnd" type="date">
          </div>
          <!-- 原有课程勾选容器：id 不变，renderCourseCheckboxes() 直接往里写 -->
          <div id="courseCheckboxes" class="course-list"></div>
          <hr class="set-sep">
          <div class="sub-label" data-i18n="settings.ignore_heading"></div>
          <div id="ignoreCourses" class="chips" style="margin-bottom:6px"></div>
          <div class="row" style="gap:10px;flex-wrap:wrap">
            <button id="btnClearIgnore" class="btn btn-ghost" data-i18n="settings.clear_ignore"></button>
            <span class="muted" data-i18n="settings.ignore_hint"></span>
          </div>
        </section>
```

三个 id（`inpStart` / `inpEnd` / `courseCheckboxes`）连同 `btnLoadCourses` / `ignoreCourses` / `btnClearIgnore` 必须**逐字照抄**，不得改名。原来的 `.sp` 从 `range-row` 里挪到了上一行的 `.row` 里 —— 两处都有 flex，`.page-head .sp, .range-row .sp{flex:1}` 规则（`app.css:95`）在 `.row` 内不生效，所以 `.sp` 需要一条通用规则，见 Step 3。

- [ ] **Step 3: 补 `.course-list` 与 `.sp` 规则（裁定 R5）**

在 `frontend/app.css` 中 `.chips` 规则之后插入：

```css
/* 课程勾选容器与设置页内的分区线。.course-list 这个类名此前只有 HTML 没有样式，
   靠 .chips 的 gap 才有间距；搬进设置页后必须自己撑开，否则 chips 会挤成一坨。 */
.course-list { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }
.set-sep { border:0; border-top:1px solid var(--border); margin:16px 0 12px; }
/* .row 里的占位撑开（原规则只覆盖 .page-head 与 .range-row 两处） */
.row > .sp { flex:1; }
```

- [ ] **Step 4: 去掉 `btnLoadCourses` 的切页副作用**

在 `frontend/app.js` 中，把 `btnLoadCourses.onclick`（633-645 行）替换为：

```js
$("btnLoadCourses").onclick = async () => {
  const s=settings();
  if(!s.canvas_url||!s.canvas_token){ setStatus(t("status.need_canvas_config"),"err"); return; }
  await withBusy(t("status.loading_courses"), $("btnLoadCourses"), async ()=>{
    const r=await api("courses", s);
    if(!r.ok){ setStatus(t("status.courses_fail")+r.error,"err"); return; }
    courseList=r.courses;
    renderCourseCheckboxes(courseList);
    setStatus(t("status.courses_loaded", {n: courseList.length}),"ok");
  });
  // 课程配置已搬进设置页：这里不再 switchPage("announce")。
  // 旧行为会让用户在设置页点一下加载就被弹去公告页，莫名其妙。
  // 公告同步改由这条自动路径补一次，且没有可选课程时不发请求、不弹错。
  if (selectedCourses().length) syncAnnouncements();
};
```

- [ ] **Step 5: 增删 i18n 键**

在 `frontend/i18n.js` 的 `I18N.zh` 段内，**紧跟** `"settings.ignore_hint"` 那一行之后加入：

```js
    "settings.group.course": "课程",
```

在 `I18N.en` 段内的同名位置之后加入：

```js
    "settings.group.course": "Courses",
```

然后**删除**两个语言段里的 `"settings.group.ignore"` 行（该键随卡片标题一起被 `settings.group.course` 取代，不再有任何引用）。

Run:
```bash
node tools/check_i18n_keys.mjs
```
Expected: 退出码 0，且报出 `死键 24 个（基线 25…）`。键数**仍是 338**（加 1 删 1，净变化为零）。

**若此处数字不是 24，停下来核对**：死键数**只**受"某个键由死转活"影响 —— 这里只有 `settings.ignore_heading` 由死转活（−1）。而 `settings.group.ignore` 被删时是**活键**，删一个活键不改变死键计数（键总数与活键数同时 −1）。25 − 1 = 24。

- [ ] **Step 6: 更新死键基线**

把 `tools/check_i18n_keys.mjs` 里的 `const DEAD_BASELINE = 25;` 改为 `const DEAD_BASELINE = 24;`。

Run: `node tools/check_i18n_keys.mjs`
Expected: 退出码 0，`死键 24 个（基线 24…）`。

- [ ] **Step 7: 跑全部静态门**

Run:
```bash
for f in frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js; do node --check "$f" || exit 1; done
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/c.js && node --check /tmp/c.js && echo "拼接检查通过"
node tools/check_dom_ids.mjs
node tools/check_i18n_keys.mjs
.venv/bin/python -m pytest -q
```
Expected: 全绿，pytest `228 passed`。

四文件拼接那一道**不能省**：四个文件共享同一全局词法环境，重复的顶层 `const`/`let` 会抛 `SyntaxError` 并让整页白屏，而单文件 `--check` 查不出来。

- [ ] **Step 8: 提交**

```bash
git add frontend/index.html frontend/app.js frontend/i18n.js frontend/app.css tools/check_i18n_keys.mjs
git commit -m "feat: 课程配置（加载/日期范围/勾选/忽略）合并进设置页一张卡"
```

---

## Task 3: ② 文件直链（共享打开器 + 两处接入）

**Files:**
- Modify: `frontend/app.js`（新增 `openFileInline` / `downloadFileTo` / `openFileSmart`；改 `btnModuleOpenFile.onclick`；给 `#modulePopTitle` 加点击；`renderFiles` 的文件名改超链接；`#filesArea` 加委托分支）
- Modify: `frontend/i18n.js`（zh/en 各加 5 键、各删 2 键）
- Modify: `frontend/app.css`（`.file-open` 可点态）

**Interfaces:**
- Consumes: 无前序功能依赖（Task 1 的门 + Task 2 的基线）
- Produces: 三个函数，**Task 5（文件子标签）会直接调用**，签名如下 —— 必须逐字一致：
  - `openFileInline(courseId: number, fileId: number, name: string) => Promise<{ok: boolean, error?: string}>`
  - `downloadFileTo(courseId: number, fileId: number, name: string, courseName: string, moduleName: string) => Promise<{ok: boolean, error?: string, dest?: string, saved?: boolean}>`
  - `openFileSmart(courseId: number, fileId: number, name: string, courseName: string, moduleName: string, btnEl?: HTMLElement|null) => Promise<void>`

**行为分叉（用户确认过的设计，不要"顺手统一"）：**

| 形态 | 判定 | 行为 |
|---|---|---|
| 浏览器 | `!window.CanvBridgeShell.isWebview()` | `fetch /api/module_file_stream` → `blob` → `URL.createObjectURL` → `window.open`（PDF 走浏览器内置查看器） |
| 桌面（内嵌窗口） | `isWebview()` | 调 `api("download_module_item", …)` 落盘，提示保存路径 |

桌面形态**为什么不能内联打开**——这是已实测的结论，不是偷懒：`app.js:1041-1046` 在 `isWebview()` 时主动隐藏「打开文件」按钮，因为内嵌 WKWebView 没有"新标签页 PDF 查看器"；`blob:` 也不被外链桥白名单接受（`app.js:17` 只放行 `^https?:`），壳层 Python 侧 `_ShellApi` 只暴露 `openExternal` 且硬过滤 http(s)（`run_app.py:59`）。要突破必须新增一项 JS→Python 能力（打开本地路径），属安全面扩张，用户已选择**不做**。

**`api()` 不能用于二进制端点**：`util.js:26-33` 无条件 `await r.json()`。内联路径必须用裸 `fetch`。

- [ ] **Step 1: 写共享打开器**

在 `frontend/app.js` 中 `downloadModuleFile` 函数（1062-1079 行）**之后**插入：

```js
/* ===== 文件直链：浏览器形态内联打开，桌面（内嵌窗口）形态退化为下载 =====
   三个调用点共用：侧栏文件页、课程 Modules 弹层、课程中心的文件子标签。
   注意 api()（util.js:26-33）无条件 await r.json()，二进制端点必须走裸 fetch。 */
async function openFileInline(courseId, fileId, name){
  const s = settings();
  let resp;
  try {
    resp = await fetch("/api/module_file_stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ canvas_url: s.canvas_url, canvas_token: s.canvas_token,
                             course_id: courseId, file_id: fileId }),
    });
  } catch (e) {
    // 与 api()（util.js:26-28）同一约定：网络失败返回错误对象而不是抛出。
    // 三个调用点都不 await 本函数，抛出会变成未处理的 rejection。
    return { ok: false, error: t("status.backend_fail") };
  }
  const ctype = (resp.headers.get("Content-Type") || "").split(";")[0].trim().toLowerCase();
  // 后端出错时同样是 200 + JSON（main.py 约定），所以 Content-Type 是判据，不能只看 resp.ok
  if (!resp.ok || ctype === "application/json"){
    let msg = "";
    try { const j = await resp.json(); msg = j.error || ""; } catch (e) { /* 忽略解析失败 */ }
    return { ok: false, error: msg };
  }
  let blob;
  try { blob = await resp.blob(); }
  catch (e) { return { ok: false, error: t("status.backend_fail") }; }
  const url = URL.createObjectURL(blob);
  /* 这里**不能**拿 window.open 的返回值判断成败。HTML 规范在 window open 步骤末尾规定：
     "If noopener is true or windowType is 'new with no opener', then return null."
     —— 带 noopener 时返回值**无条件**为 null，与标签页是否真的打开无关。
     若写成 `const win = window.open(...); if (!win) return {ok:false}`，本函数将永远走降级
     分支，「浏览器内联打开」这个功能会整个消失、每次点击都变成下载 —— 而且所有静态门都是绿的。
     noopener 必须保留：Canvas 上的 .html 文件经 blob: 渲染后与本站同源，去掉它便可经
     window.opener 反向操控本页。代价是弹窗被拦时无法探测；这与既有的 btnModuleOpenPage
     （app.js:1173，同样的调用且忽略返回值）取舍一致，用户已接受。 */
  window.open(url, "_blank", "noopener");
  // objectURL 交给新打开的文档用；延迟释放避免新标签还没加载完就被回收
  setTimeout(() => { try { URL.revokeObjectURL(url); } catch (e) {} }, 60000);
  return { ok: true };
}

async function downloadFileTo(courseId, fileId, name, courseName, moduleName){
  const s = settings();
  const r = await api("download_module_item", {
    canvas_url: s.canvas_url, canvas_token: s.canvas_token,
    download_dir: downloadDir(), course_id: courseId,
    course_name: courseName || "", module_name: moduleName || "", file_id: fileId });
  if (r.ok !== true) return { ok: false, error: r.error || "" };
  return { ok: true, dest: r.dest_path || "", saved: !!r.saved };
}

/* 统一入口：按形态分叉 + 失败自动降级。
   用户点文件名要的是"拿到文件"，所以内联失败时报错之余还要替他下载一次，
   而不是只丢一句红字让他自己再找下载按钮。 */
async function openFileSmart(courseId, fileId, name, courseName, moduleName, btnEl){
  if (!fileId){ setStatus(t("file.open_fail"), "err"); return; }
  /* 三个调用点都不 await 本函数（点一下就返回），所以这里必须自己兜住所有异常：
     withBusy 只有 try/finally、没有 catch（util.js:34-40），抛出去就成了未处理的 rejection ——
     遮罩消失、没有提示、降级下载也不会跑，正是"点了没反应"那种失败。 */
  try {
    await withBusy(t("file.opening", {f: name}), btnEl || null, async () => {
      if (window.CanvBridgeShell && window.CanvBridgeShell.isWebview()){
        const r = await downloadFileTo(courseId, fileId, name, courseName, moduleName);
        if (!r.ok){ setStatus(t("file.open_fail") + (r.error || ""), "err"); return; }
        setStatus(r.saved ? t("file.downloaded_saved", {f: name})
                          : t("file.downloaded", {p: r.dest}), "ok");
        return;
      }
      const r = await openFileInline(courseId, fileId, name);
      if (r.ok) return;
      setStatus(t("file.open_fail") + (r.error || ""), "err");
      const d = await downloadFileTo(courseId, fileId, name, courseName, moduleName);
      if (d.ok) setStatus(t("file.downloaded", {p: d.dest}), "ok");
    });
  } catch (e) {
    setStatus(t("file.open_fail") + (e && e.message ? e.message : ""), "err");
  }
}
```

`withBusy`（`util.js:34-40`）的第二个参数为 falsy 时只是不 disable 按钮，不报错，所以 `btnEl` 传 `null` 是安全的。

- [ ] **Step 2: 弹层标题变可点 + 两个按钮改走共享入口**

把 `btnModuleOpenFile.onclick`（1115-1139 行）整块替换为：

```js
/* 标题可点 = 等同于点「打开文件」（spec §5.2(b)）：让"点名字就能打开"在模块列表里也成立 */
$("modulePopTitle").onclick = () => {
  const ctx = modulePopCtx;
  if (!ctx) return;
  const c = detailCourse;
  closeModulePop();
  openFileSmart(c ? c.id : 0, ctx.fid, ctx.title, (c && c.name) || "", ctx.moduleName, null);
};
$("btnModuleOpenFile").onclick = () => {
  const ctx = modulePopCtx;
  if (!ctx) return;
  const c = detailCourse;
  const btn = $("btnModuleOpenFile");
  closeModulePop();
  openFileSmart(c ? c.id : 0, ctx.fid, ctx.title, (c && c.name) || "", ctx.moduleName, btn);
};
```

注意先 `closeModulePop()` 再调用 —— 原实现也是先关弹层再请求，避免加载遮罩盖在弹层上。

**还要取消桌面形态下对 `#btnModuleOpenFile` 的隐藏。** 把 `openModuleFilePop` 里的这一小段（`app.js:1041-1047`）：

```js
  if (window.CanvBridgeShell && window.CanvBridgeShell.isWebview()){
    // 内嵌窗口没有“新标签页内联 PDF”：页内预览不可用。
    // 有 Canvas 链接 → 走系统浏览器「打开页面」；纯文件项 → 直接下载到本地，不弹空 popover。
    const fbtn = $("btnModuleOpenFile");
    if (fbtn) fbtn.hidden = true;
    if (!pageUrl){ closeModulePop(); downloadModuleFile(itemEl); return; }
  }
```

改为：

```js
  if (window.CanvBridgeShell && window.CanvBridgeShell.isWebview()){
    // 内嵌窗口没有“新标签页内联 PDF”，所以「打开文件」在这里不做内联预览。
    // 但它不再是死按钮：经 openFileSmart 分叉后它落盘下载（Step 1），所以保持可见 ——
    // 隐藏它会让弹层里唯一带标签的文件入口消失，只剩标题上那个没有任何提示的点击。
    // 纯文件项（无 Canvas 链接）仍然直接下载并关掉弹窗，省一次点击。
    if (!pageUrl){ closeModulePop(); downloadModuleFile(itemEl); return; }
  }
```

**并给标题补提示。** 在 `$("modulePopTitle").textContent = modulePopCtx.title;`（`app.js:1039`）之后加一行：

```js
  $("modulePopTitle").title = t("file.open");
```

理由：标题是 `<div>`，无 `title`、无 `tabindex`、无 `role`，唯一线索是 CSS 的 `cursor:pointer`，鼠标可达性极差；
文件页那侧的同名入口是带 `title` 的真 `<a>`（`app.js:1226`），两边应对齐。

- [ ] **Step 3: 文件页文件名变超链接**

在 `frontend/app.js` 的 `renderFiles()` 中，把每行模板里的文件名那一段（1174 行）替换为：

```js
          <div><div class="item-title"><a href="#" class="file-open" data-ci="${c._orig}" data-fid="${escAttr(f.file_id)}" data-name="${escAttr(f.display_name)}" title="${escAttr(t("file.open"))}">${esc(f.display_name)}</a> <span class="muted">（${esc(f.content_type)}）</span>${f.saved?` <span class="file-saved">${esc(t("files.saved"))}</span>`:""}</div>
```

用 `data-ci`（`fileCourses` 下标）而非 `data-cid`，与同一行既有的复选框 `data-ci="${c._orig}"` 及 `#btnDownloadFiles` 的取值口径保持一致（裁定 R2）。

- [ ] **Step 4: 文件页加委托分支**

在 `frontend/app.js` 的 `$("filesArea").addEventListener("click", …)` 中，把 `.caret` 分支**之前**插入：

```js
  const fo = e.target.closest(".file-open");
  if (fo){
    e.preventDefault();                       // href="#" 只是占位，别让页面跳到顶部
    const c = fileCourses[Number(fo.dataset.ci)];
    if (!c) return;
    openFileSmart(c.course_id, Number(fo.dataset.fid), fo.dataset.name, c.name, "", null);
    return;
  }
```

- [ ] **Step 5: 加 `.file-open` 样式**

在 `frontend/app.css` 中 `.file-path` 规则之后插入：

```css
/* 文件名可点：默认去掉下划线，hover 才提示可点，避免整页看起来到处是链接 */
.file-open { color:inherit; text-decoration:none; border-bottom:1px solid transparent; cursor:pointer; }
.file-open:hover { color:var(--accent); border-bottom-color:var(--accent); }
.module-pop-title { cursor:pointer; }
```

- [ ] **Step 6: 增删 i18n 键**

在 `frontend/i18n.js` 的 `I18N.zh` 段内，**紧跟** `"files.saved"` 那一行之后加入：

```js
    "file.open": "打开文件",
    "file.opening": "正在打开 {f}…",
    "file.open_fail": "打开文件失败：",
    "file.downloaded": "已下载到 {p}",
    "file.downloaded_saved": "文件已存在，未重复下载：{f}",
```

在 `I18N.en` 段的同名位置之后加入：

```js
    "file.open": "Open file",
    "file.opening": "Opening {f}…",
    "file.open_fail": "Failed to open file: ",
    "file.downloaded": "Saved to {p}",
    "file.downloaded_saved": "Already downloaded, skipped: {f}",
```

再**删除**两个语言段里的 `"module.open_file_fail"` 与 `"module.fetching"` 两行（本步骤已移除它们的全部引用）。

Run:
```bash
node tools/check_i18n_keys.mjs
```
Expected: 退出码 0，`死键 24 个（基线 24…）`。

**若数字不是 24，停下来核对**：预期加 5 键（全部立即被引用，不增死键）、删 2 键。注意**删两个活键不改变死键计数**（键总数与活键数同时 −1），所以这一任务死键数**持平**，仍是 24。

- [ ] **Step 7: 更新死键基线并跑全部静态门**

`DEAD_BASELINE` **保持不变，仍是 `24`**（本任务死键数持平）。

Run:
```bash
for f in frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js; do node --check "$f" || exit 1; done
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/c.js && node --check /tmp/c.js && echo "拼接检查通过"
node tools/check_dom_ids.mjs
node tools/check_i18n_keys.mjs
.venv/bin/python -m pytest -q
```
Expected: 全绿，`228 passed`。

- [ ] **Step 8: 提交**

```bash
git add frontend/app.js frontend/i18n.js frontend/app.css tools/check_i18n_keys.mjs
git commit -m "feat: 文件直链——文件名可点直接打开源文件（浏览器内联/桌面下载）"
```

---

## Task 4: ④-a 课程中心骨架（列表 ↔ 详情 + 四标签栏）

**Files:**
- Modify: `frontend/index.html`（`#page-courses` 换成骨架）
- Modify: `frontend/app.js`（新增课程中心模块，追加在文件末尾）
- Modify: `frontend/shell.js`（`PAGE_INIT` 挂 courses）
- Modify: `frontend/i18n.js`（zh/en 各加 13 键）
- Modify: `frontend/app.css`（`.hub-card` / `.tab-bar` / `.tab-item`）

**Interfaces:**
- Consumes: Task 2 腾空的 `#page-courses`；Task 1 的门与基线；Task 3 的 `openFileSmart` 暂不调用（Task 5 才用）
- Produces（**后续任务必须逐字使用这些名字**）：
  - `let hubCid: number|null` —— 当前详情页的 Canvas course id；`null` = 未匹配到 Canvas 课程
  - `let hubTab: "files"|"announce"|"todo"|"discuss"`
  - `initCourseHubTab(): void` —— `PAGE_INIT.courses` 的入口
  - `renderCourseHubList(): void`
  - `openCourseHub(canvasId: number|null, banwebCourse: object|null): void` —— **Task 8 会调用**
  - `renderCourseHub(): void`
  - `switchHubTab(name: string): void`
  - `hubCourseName(): string` —— **Task 5 会调用**
  - `hubShouldRefresh(key: string): boolean` + `HUB_REFRESH_TTL` —— **Task 5/6/7 的后台刷新闸门**
  - `hubRefreshCurrent(): void`
  - 四个标签渲染函数（Task 5-7 实现，本任务先建占位）：`renderHubFiles(cid)` / `renderHubAnnounce(cid)` / `renderHubTodo(cid)` / `renderHubDiscuss(cid)`
  - 三个刷新函数（Task 5-7 实现，本任务先建占位）：`hubRefreshFiles(cid)` / `hubRefreshAnnounce()` / `hubRefreshDiscuss(cid)`

**不复用全局渲染函数（spec §5.4）。** `renderFiles` / `renderSummaries` / `renderTodo` 都把容器 id 写死（`#filesArea` / `#summaries` / `#todoGroups`），且工具栏与勾选逻辑在顶层一次性绑死在 `#page-files` 等页面的固定 id 上（`app.js:1202-1269`）。硬复用只有两条路：沿用同一批 id（则弹层与全局页无法并存），或把这些函数参数化（动的是已在跑的代码，回归风险实打实）。更根本的是**这两者本来就不是同一个视图** —— 全局文件页是"所有课程、可展开收起"，课程中心是"这一门课的文件"。所以给课程中心写独立的按课程渲染函数，读同一批全局数据、各自渲染。风险为零，代价是一些渲染代码重复，**本计划明确接受**。

**标签栏是新建组件。** 仓库里**不存在**任何 `role="tab"` / `.tabs` / `.segmented` 结构（本轮已实测确认 `app.css` 与 `index.html` 均无）。但有五个孤儿 i18n 键可复用作标签文案：`tab.announce` / `tab.files` / `tab.schedule` / `tab.todo` / `tab.grades`（`i18n.js:32-39`，英文 `:367-374`）—— 它们定义后从未被任何 JS 引用。本轮复用其中三个（`tab.files` / `tab.announce` / `tab.todo`），讨论标签新增 `tab.discuss`。

- [ ] **Step 1: 换掉 `#page-courses` 的骨架**

把 `frontend/index.html` 里的 `#page-courses` 整块替换为：

```html
    <div id="page-courses" class="page" hidden>
      <!-- 课程中心：列表视图 ↔ 单课程详情视图，同一时刻只显示其一。
           与课程详情弹层 #detailModal 并存，两者互不替代。 -->

      <div id="hubList">
        <div class="page-head"><h2 data-i18n="nav.courses"></h2></div>
        <div id="hubListArea"></div>
      </div>

      <div id="hubDetail" hidden>
        <div class="page-head">
          <button id="btnHubBack" class="btn btn-ghost" data-i18n="hub.back"></button>
          <h2 id="hubTitle"></h2>
          <span class="sp"></span>
        </div>
        <div id="hubTabs" class="tab-bar" role="tablist">
          <button class="tab-item" role="tab" data-tab="files" data-i18n="tab.files"></button>
          <button class="tab-item" role="tab" data-tab="announce" data-i18n="tab.announce"></button>
          <button class="tab-item" role="tab" data-tab="todo" data-i18n="tab.todo"></button>
          <button class="tab-item" role="tab" data-tab="discuss" data-i18n="tab.discuss"></button>
        </div>
        <div id="hubPanel"></div>
      </div>
    </div>
```

- [ ] **Step 2: 挂上 `PAGE_INIT`**

在 `frontend/shell.js` 的 `PAGE_INIT`（11-18 行）里，`settings:` 那一行之后加入：

```js
  courses:  () => initCourseHubTab(),
```

- [ ] **Step 3: 写课程中心模块**

在 `frontend/app.js` 的**文件末尾**追加以下内容（文件末尾追加可避免与既有顶层语句的顺序纠缠；函数声明会提升，而 `$("hubListArea").addEventListener` 这类顶层语句需要元素已存在 —— `app.js` 在 `<body>` 末尾加载，DOM 已就绪）：

```js
/* ===== 课程中心：列表 → 单课程四子标签 =====
   与课程详情弹层（#detailModal）并存，两者各自独立可用。
   本页只呈现"这一门课"的内容，不做跨课程聚合 —— 那是侧栏各全局页的职责。 */
let hubCid = null;        // 当前详情页的 Canvas course id（null = 未匹配到 Canvas 课程）
let hubBanweb = null;     // 从首页课表进来时带的 Banweb 课程对象（可为 null）
let hubTab = "files";     // 当前标签
let hubInit = false;      // 首次进入是否已拉过课程列表

/* 同一课程同一标签 60 秒内不重复后台拉取：切标签是高频动作，
   每次都打一次网络既慢又无意义。 */
const HUB_REFRESH_TTL = 60000;
const hubRefreshed = new Map();          // "cid:tab" -> 上次刷新时间戳
function hubShouldRefresh(key){
  const last = hubRefreshed.get(key) || 0;
  if (Date.now() - last < HUB_REFRESH_TTL) return false;
  hubRefreshed.set(key, Date.now());
  return true;
}

/* 详情页标题：优先 Canvas 课程名；未匹配到 Canvas 时退化为 Banweb 的 code+section */
function hubCourseName(){
  if (hubCid != null){
    const c = (courseList || []).find(x => x.id === hubCid);
    if (c) return c.name;
  }
  if (hubBanweb) return `${hubBanweb.code || ""} ${hubBanweb.section || ""}`.trim();
  return hubCid != null ? ("#" + hubCid) : "";
}

/* 首次进入课程中心：课程配置现在在设置页，不能指望用户先去点「加载课程」，
   所以这里自行拉一次（有缓存就直接渲染，不闪空白）。与 discuss/grades/home
   的"首次进入才拉"是同一个 house pattern。 */
function initCourseHubTab(){
  if (hubInit) return;
  hubInit = true;
  if ((courseList || []).length){ renderCourseHubList(); return; }
  const s = settings();
  if (!s.canvas_url || !s.canvas_token){ renderCourseHubList(); return; }   // 未配置 → 空态
  loadCourseHubList();
}
async function loadCourseHubList(){
  const r = await api("courses", settings());
  if (r.ok) courseList = r.courses || [];
  renderCourseHubList();
}

function renderCourseHubList(){
  const box = $("hubListArea");
  if (!box) return;
  const s = settings();
  if (!s.canvas_url || !s.canvas_token){
    box.innerHTML = `<div class="empty">${t("hub.need_config")}</div>`;
    return;
  }
  const ignored = savedCourseIgnored() || new Set();
  const list = (courseList || []).filter(c => !ignored.has(c.id));
  if (!list.length){
    box.innerHTML = `<div class="empty">${t("hub.empty")}</div>`;
    return;
  }
  box.innerHTML = list.map(c => `
    <button type="button" class="hub-card" data-id="${c.id}">
      <span class="hub-card-name">${esc(c.name)}</span>
      <span class="hub-card-code">${esc(c.course_code || "")}</span>
    </button>`).join("");
}
$("hubListArea").addEventListener("click", (e) => {
  const b = e.target.closest(".hub-card");
  if (b) openCourseHub(Number(b.dataset.id), null);
});

function backToHubList(){
  hubCid = null; hubBanweb = null;
  $("hubDetail").hidden = true;
  $("hubList").hidden = false;
  renderCourseHubList();
}
$("btnHubBack").onclick = backToHubList;

/* 进入某门课的详情。canvasId 允许为 null（Banweb 有课但没匹配上 Canvas 课程），
   此时退化为只显示课表信息 + 一条明确提示。 */
function openCourseHub(canvasId, banwebCourse){
  hubCid = (canvasId == null ? null : Number(canvasId));
  hubBanweb = banwebCourse || null;
  hubTab = "files";
  $("hubList").hidden = true;
  $("hubDetail").hidden = false;
  renderCourseHub();
  // 详情页住在 #page-courses 里，若当前不在课程页则切过去（侧栏高亮随之同步）。
  // 放最后：switchPage 会触发 PAGE_INIT.courses，而 hubInit 守卫保证它不会重拉数据。
  if (typeof switchPage === "function" && currentPage !== "courses") switchPage("courses");
  hubRefreshCurrent();
}

function renderCourseHub(){
  if (hubCid == null && !hubBanweb) return;     // 没有目标课程：什么都不写，避免误清空
  $("hubTitle").textContent = hubCourseName();
  $$("#hubTabs .tab-item").forEach(b => {
    const on = b.dataset.tab === hubTab;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  renderHubTab();
}
$("hubTabs").addEventListener("click", (e) => {
  const b = e.target.closest(".tab-item");
  if (b) switchHubTab(b.dataset.tab);
});
function switchHubTab(name){
  if (!["files", "announce", "todo", "discuss"].includes(name)) return;
  hubTab = name;
  renderCourseHub();
  hubRefreshCurrent();
}

/* Banweb 课表信息（未匹配到 Canvas 时唯一能显示的内容）。
   字段取自 renderHomeToday 已在用的那批（shell.js:169-171），不用未验证的字段。 */
function hubBanwebInfoHtml(){
  if (!hubBanweb) return "";
  const ms = (hubBanweb.meetings || []).map(m =>
    `<div class="file-path">${esc((m.days_list || []).join(""))} ${esc(fmtTime(m.start_min))}–${esc(fmtTime(m.end_min))}${m.room ? " · " + esc(m.room) : ""}</div>`).join("");
  const head = `${hubBanweb.code || ""} ${hubBanweb.section || ""}`.trim();
  return `<div class="glass-card"><div class="sub-label">${esc(head)}</div>${ms}</div>`;
}

/* 标签分发。四个 renderHub* 由后续任务实现（函数声明提升，运行时可见）。 */
function renderHubTab(){
  const box = $("hubPanel");
  if (!box) return;
  if (hubCid == null){
    box.innerHTML = `<div class="muted">${t("hub.no_canvas")}</div>` + hubBanwebInfoHtml();
    return;
  }
  const fn = { files: renderHubFiles, announce: renderHubAnnounce,
               todo: renderHubTodo, discuss: renderHubDiscuss }[hubTab];
  (fn || renderHubFiles)(hubCid);
}

/* 后台刷新：只对能按课程粒度的端点做（spec §4.4）。
   待办不做 —— /api/todo 没有课程参数，只能整批取，本轮只用全局缓存。
   注意各 renderHub* 自身绝不触发刷新，否则会形成 渲染→刷新→渲染 的死循环。 */
function hubRefreshCurrent(){
  if (hubCid == null) return;
  const key = hubCid + ":" + hubTab;
  if (!hubShouldRefresh(key)) return;
  if (hubTab === "files") hubRefreshFiles(hubCid);
  else if (hubTab === "discuss") hubRefreshDiscuss(hubCid);
  else if (hubTab === "announce") hubRefreshAnnounce();
}

/* 占位：由 Task 5 替换为真实实现 */
function renderHubFiles(cid){ $("hubPanel").innerHTML = `<div class="muted">${t("hub.tab_empty.files_unloaded")}</div>`; }
function hubRefreshFiles(cid){}
/* 占位：由 Task 6 替换为真实实现 */
function renderHubAnnounce(cid){ $("hubPanel").innerHTML = `<div class="muted">${t("hub.tab_empty.announce_unloaded")}</div>`; }
function hubRefreshAnnounce(){}
/* 占位：由 Task 7 替换为真实实现 */
function renderHubTodo(cid){ $("hubPanel").innerHTML = `<div class="muted">${t("hub.tab_empty.todo_unloaded")}</div>`; }
function renderHubDiscuss(cid){ $("hubPanel").innerHTML = `<div class="muted">${t("hub.tab_empty.discuss_unloaded")}</div>`; }
function hubRefreshDiscuss(cid){}
```

**这六个占位必须在 Task 5/6/7 里被逐个替换，一个都不能留到收尾。** 它们存在的唯一理由是让 Task 4 的产物自身可运行、可验收（Task 9 Step 4 有专项检查）。

- [ ] **Step 4: 让设置页的加载按钮同步刷新课程中心列表**

在 `frontend/app.js` 的 `btnLoadCourses.onclick` 里（Task 2 已改过这个函数），把 `renderCourseCheckboxes(courseList);` 那一行替换为两行：

```js
    renderCourseCheckboxes(courseList);
    if (typeof renderCourseHubList === "function") renderCourseHubList();   // 课程中心列表跟着更新
```

- [ ] **Step 5: 加课程中心样式**

在 `frontend/app.css` 末尾追加：

```css
/* ===== 课程中心：课程卡片列表 + 子标签栏 ===== */
.hub-card { display:flex; align-items:center; gap:10px; width:100%; text-align:left;
  padding:14px 16px; margin-bottom:10px; cursor:pointer; font:inherit; color:inherit;
  background:var(--surface-2); border:1px solid var(--border); border-left:4px solid var(--accent);
  border-radius:var(--radius-sm); }
.hub-card:hover { border-color:var(--accent); }
.hub-card-name { font-weight:700; font-size:14px; }
.hub-card-code { margin-left:auto; color:var(--muted); font-size:12px; font-variant-numeric:tabular-nums; }

.tab-bar { display:flex; gap:6px; margin-bottom:16px; border-bottom:1px solid var(--border); }
.tab-item { border:0; background:none; cursor:pointer; font:inherit; font-size:13.5px;
  color:var(--muted); padding:8px 14px; border-bottom:2px solid transparent; }
.tab-item:hover { color:var(--ink); }
.tab-item.active { color:var(--accent); border-bottom-color:var(--accent); font-weight:600; }
```

若 `app.css` 中没有 `--surface-2` / `--radius-sm` 变量，用文件里既有的同类变量替代（先 `grep -n '\-\-surface' frontend/app.css` 看清单再定），**不要新引入变量**。

- [ ] **Step 6: 加 i18n 键**

在 `frontend/i18n.js` 的 `I18N.zh` 段内，**紧跟** `"tab.grades"` 那一行之后加入：

```js
    "tab.discuss": "讨论",
    "hub.back": "← 返回列表",
    "hub.empty": "还没有课程。请到设置页点「加载课程」。",
    "hub.need_config": "尚未配置 Canvas。请到设置页填写地址与令牌。",
    "hub.no_canvas": "这门课在 Canvas 里没有匹配到（可能是本学期未开或未选）。以下只有课表信息。",
    "hub.tab_empty.files_unloaded": "文件尚未加载。",
    "hub.tab_empty.announce_unloaded": "公告尚未同步。请到设置页点「加载课程」。",
    "hub.tab_empty.todo_unloaded": "待办尚未加载。请到「待办」页点一次刷新。",
    "hub.tab_empty.discuss_unloaded": "讨论尚未加载。",
```

**只有 9 个键加在这一步。** `hub.tab_empty.files` / `.announce` / `.todo` / `.discuss` 这**四个"确实没有"的文案由 Task 5/6/7 各自加入** —— 它们要到那三个任务才被引用。一次性加在这里会让它们立刻变成死键，把死键计数从 21 顶到 25，本任务自己的门就会失败。这正是 Task 1 建的基线门要防的事。

在 `I18N.en` 段的同名位置之后加入：

```js
    "tab.discuss": "Discussion",
    "hub.back": "← Back to list",
    "hub.empty": "No courses yet. Go to Settings and click \"Load courses\".",
    "hub.need_config": "Canvas is not configured. Fill in the URL and token in Settings.",
    "hub.no_canvas": "This course has no match in Canvas (not offered or not enrolled this term). Only schedule info is shown.",
    "hub.tab_empty.files_unloaded": "Files not loaded yet.",
    "hub.tab_empty.announce_unloaded": "Announcements not synced yet. Click \"Load courses\" in Settings.",
    "hub.tab_empty.todo_unloaded": "To-dos not loaded yet. Refresh once on the To-do page.",
    "hub.tab_empty.discuss_unloaded": "Discussions not loaded yet.",
```

**空态文案必须区分两种"空"**：这门课确实没有（Canvas 返回空，用 `hub.tab_empty.X`）vs 尚未加载（全局变量为 null 或无该课程条目，用 `hub.tab_empty.X_unloaded`）。两者混用一句"暂无"会让用户以为数据没了 —— 上一轮 `files.no_files` 与 `files.empty` 就是分开的，沿用该做法。

`tab.files` / `tab.announce` / `tab.todo` 三个既有孤儿键在本步骤被 `index.html` 的 `data-i18n` 引用，由死转活。

Run:
```bash
node tools/check_i18n_keys.mjs
```
Expected: 退出码 0，`死键 21 个（基线 24…）`。

**若数字不是 21，停下来核对**：本步加 9 键（全部立即被引用，不增死键）、三个 `tab.*` 孤儿转活（死键减 3），24 − 3 = 21。

- [ ] **Step 7: 更新死键基线并跑全部静态门**

把 `DEAD_BASELINE` 改为 `21`。

Run:
```bash
for f in frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js; do node --check "$f" || exit 1; done
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/c.js && node --check /tmp/c.js && echo "拼接检查通过"
node tools/check_dom_ids.mjs
node tools/check_i18n_keys.mjs
.venv/bin/python -m pytest -q
```
Expected: 全绿，`228 passed`。

- [ ] **Step 8: 起真身，人工确认骨架可用**

Run（后台起服务，浏览器形态）：
```bash
.venv/bin/python run_app.py --browser
```

在打开的页面里逐条确认：
1. 侧栏点「课程」→ 出现课程列表（不是配置项、不是空白页）。
2. 设置页里课程配置那张卡仍在，课程勾选列表正常显示（`.course-list` 的 flex 生效，chips 之间有间距）。
3. 点某张课程卡片 → 切到详情视图，顶部有「← 返回列表」、课程名，下方四个标签。
4. 逐个点四个标签 → 都能切换、都不报错（此刻显示的都是"尚未加载"占位符，Task 5-7 会替换）。
5. 点「← 返回列表」→ 回到列表，**侧栏高亮仍停在「课程」**，不跳走。

关掉服务。此项为目视确认，无自动化断言。

- [ ] **Step 9: 提交**

```bash
git add frontend/index.html frontend/app.js frontend/shell.js frontend/i18n.js frontend/app.css tools/check_i18n_keys.mjs
git commit -m "feat: 课程中心骨架——课程列表 ↔ 单课程详情（四子标签栏）"
```

---

## Task 5: ④-b 文件子标签

**Files:**
- Modify: `frontend/app.js`（用真实实现替换 `renderHubFiles` / `hubRefreshFiles` 两个占位；`#hubPanel` 加点击委托）
- Modify: `frontend/i18n.js`（zh/en 各加 1 键：`hub.tab_empty.files`）

**Interfaces:**
- Consumes: Task 3 的 `openFileSmart(courseId, fileId, name, courseName, moduleName, btnEl)`；Task 4 的 `hubCid` / `hubTab` / `hubCourseName()`；全局变量 `fileCourses`、函数 `downloadDir()`、`settings()`
- Produces: `renderHubFiles(cid: number): void`（纯渲染，**绝不触发网络**）、`hubRefreshFiles(cid: number): Promise<void>`

**数据形态（实测确认，不要臆造字段）**：`fileCourses[i]` 形如
`{course_id, name, files: [{file_id, display_name, path, content_type, size, dest_path, saved}], error?}` —— 来自 `backend/main.py:545-560`。按课程过滤用 `fileCourses.find(c => c.course_id === cid)`。

**死循环陷阱（本任务最容易踩的坑）**：`hubRefreshFiles` 落地后会重新渲染，而如果渲染函数又去触发刷新，就会形成 渲染 → 刷新 → 渲染 的无限网络循环。**分工必须严格**：`renderHubFiles` 只读缓存、只写 DOM；刷新只由 `hubRefreshCurrent()`（Task 4 已写好）在切标签/进入详情时发起；刷新完成后若用户还在同一标签才重新渲染。

- [ ] **Step 1: 用真实实现替换两个占位**

在 `frontend/app.js` 里把 Task 4 留下的这两行：

```js
/* 占位：由 Task 5 替换为真实实现 */
function renderHubFiles(cid){ $("hubPanel").innerHTML = `<div class="muted">${t("hub.tab_empty.files_unloaded")}</div>`; }
function hubRefreshFiles(cid){}
```

**整行删除**（含那行注释），替换为：

```js
/* 文件子标签。只读 fileCourses 缓存，不触发网络（刷新由 hubRefreshCurrent 统一发起）。 */
function renderHubFiles(cid){
  const box = $("hubPanel");
  if (!box) return;
  const c = (fileCourses || []).find(x => x.course_id === cid);
  if (!c){                                   // 全局缓存里根本没有这门课 → 尚未加载
    box.innerHTML = `<div class="muted">${t("hub.tab_empty.files_unloaded")}</div>`;
    return;
  }
  if (c.error){ box.innerHTML = `<div class="muted">${esc(c.error)}</div>`; return; }
  const files = c.files || [];
  if (!files.length){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.files")}</div>`; return; }
  box.innerHTML = files.map(f => `
    <div class="item">
      <div>
        <div class="item-title"><a href="#" class="file-open" data-cid="${cid}" data-fid="${escAttr(f.file_id)}" data-name="${escAttr(f.display_name)}" title="${escAttr(t("file.open"))}">${esc(f.display_name)}</a> <span class="muted">（${esc(f.content_type || "")}）</span>${f.saved ? ` <span class="file-saved">${esc(t("files.saved"))}</span>` : ""}</div>
        <div class="file-path">${esc(f.path || "/")}</div>
      </div>
    </div>`).join("");
}

/* 后台按本课程刷新一次文件列表。失败静默 —— 保留已渲染的缓存，不把标签打回空态。 */
async function hubRefreshFiles(cid){
  const s = settings();
  if (!s.canvas_url || !s.canvas_token) return;
  const r = await api("list_files", { ...s, course_ids: [cid], download_dir: downloadDir() });
  if (r.ok !== true) return;
  const fresh = (r.courses || [])[0];
  if (!fresh) return;
  const i = (fileCourses || []).findIndex(x => x.course_id === cid);
  if (i >= 0) fileCourses[i] = fresh; else fileCourses.push(fresh);
  // 用户可能已经切走或返回列表：只在仍停留在本课程的文件标签时才重渲染
  if (hubCid === cid && hubTab === "files") renderHubFiles(cid);
}
```

**注意**：`hubRefreshFiles` 走的是 `/api/list_files`（`app.js:1202` 的全局文件页用它），不是 `/api/files`。写代码前先 `grep -n 'api("list_files"\|api("files"' frontend/app.js` 确认端点名，用实际存在的那个。

**同一步内还要补上本任务首次使用的 i18n 键**（它由 Task 4 的裁定移到这里，因为只有本任务的代码会引用它）。在 `frontend/i18n.js` 的 `I18N.zh` 段内、`hub.tab_empty.files_unloaded` 那一行之后加：

```js
    "hub.tab_empty.files": "这门课没有文件。",
```

在 `I18N.en` 段的同名位置之后加：

```js
    "hub.tab_empty.files": "No files in this course.",
```

加完即可被本步的 `renderHubFiles` 引用（`if (!files.length)` 分支），**不会引入死键**。

- [ ] **Step 2: 给 `#hubPanel` 加文件点击委托**

在 `frontend/app.js` 中 `$("hubTabs").addEventListener(...)` 那段之后插入：

```js
/* 课程中心里的文件名点击：与侧栏文件页共用 openFileSmart，行为完全一致 */
$("hubPanel").addEventListener("click", (e) => {
  const fo = e.target.closest(".file-open");
  if (!fo) return;
  e.preventDefault();
  openFileSmart(Number(fo.dataset.cid), Number(fo.dataset.fid), fo.dataset.name, hubCourseName(), "", null);
});
```

- [ ] **Step 3: 跑全部静态门**

Run:
```bash
for f in frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js; do node --check "$f" || exit 1; done
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/c.js && node --check /tmp/c.js && echo "拼接检查通过"
node tools/check_dom_ids.mjs
node tools/check_i18n_keys.mjs
.venv/bin/python -m pytest -q
```
Expected: 全绿，`228 passed`，死键 `21 个（基线 21…）`（本任务不增删死键）。

**若数字不是 21，停下来核对。** 门的判定是 `dead.length > DEAD_BASELINE` 才失败 —— **只允许持平或下降**。
本任务只加 1 个键（`hub.tab_empty.files`）且**当场就被 `renderHubFiles` 引用**，故不增死键；删/加活键都不改变死键计数。
所以死键数与基线都**停在 Task 4 结束时的 21，不要下调**。把它下调到实际值以下会让**下一次**跑门直接失败（`21 > 18` → 退出码 1）。

- [ ] **Step 4: 起真身，人工确认文件标签**

Run: `.venv/bin/python run_app.py --browser`

1. 先进设置页点「加载课程」（否则文件缓存为空）。
2. 侧栏「课程」→ 点某门课 → 「文件」标签显示该课程的文件列表；若为空，显示的是**「这门课没有文件。」**（"没有"）而不是「文件尚未加载。」（"未加载"）—— 两者必须区分。
3. 点某个文件名 → 浏览器**新标签页内联渲染**该文件（PDF 走浏览器内置查看器），**不是下载、不是 Canvas 网页**。
4. 返回列表再点进另一门课 → 文件列表换成那门课的内容，不串课。

此项为目视确认，无自动化断言。

- [ ] **Step 5: 提交**

```bash
git add frontend/app.js frontend/i18n.js
git commit -m "feat: 课程中心文件子标签（复用文件直链打开器）"
```

---

## Task 6: ④-c 公告子标签

**Files:**
- Modify: `frontend/app.js`（替换 `renderHubAnnounce` / `hubRefreshAnnounce` 两个占位）
- Modify: `frontend/i18n.js`（zh/en 各加 1 键：`hub.tab_empty.announce`）

**Interfaces:**
- Consumes: Task 4 的 `hubCid` / `hubTab`；全局 `summaryResults`、既有函数 `syncAnnouncements()`
- Produces: `renderHubAnnounce(cid: number): void`、`hubRefreshAnnounce(): Promise<void>`

**数据形态（实测确认）**：`summaryResults[i]` 形如
`{course_id, course_name, announcements: [{id, title, message, posted_at}], error, ...}` —— 由 `syncAnnouncements()`（`app.js:749-752`）构造。按课程过滤用 `summaryResults.find(c => c.course_id === cid)`。

**`-webkit-line-clamp` 陷阱（必须按裁定 R4 处理）**：`.announce-msg` 默认 `-webkit-line-clamp:4; overflow:hidden`（`app.css:226-227`），文本被**截断**；而把截断文本展开的按钮 `wireAnnounceExpands()` 与它的点击委托**都只扫 `#summaries`**（`app.js:869-870`、`app.js:888`）。所以在本标签里直接用 `.announce-msg` 而不带按钮，会让长公告**静默截断且无法展开**。本任务用 `class="announce-msg expanded"` 让文本始终完整（`.announce-msg.expanded{-webkit-line-clamp:unset;}`，`app.css:228`），从而既不复用按钮、也不必改动正在跑的 `#summaries` 代码。

- [ ] **Step 1: 替换占位**

在 `frontend/app.js` 里把 Task 4 留下的这两行：

```js
/* 占位：由 Task 6 替换为真实实现 */
function renderHubAnnounce(cid){ $("hubPanel").innerHTML = `<div class="muted">${t("hub.tab_empty.announce_unloaded")}</div>`; }
function hubRefreshAnnounce(){}
```

**整行删除**（含那行注释），替换为：

```js
/* 公告子标签。只读 summaryResults 缓存。正文用 .announce-msg.expanded 保持全文 ——
   展开按钮的布线与委托都只扫 #summaries，这里复制一个按钮会点不动（裁定 R4）。 */
function renderHubAnnounce(cid){
  const box = $("hubPanel");
  if (!box) return;
  const g = (summaryResults || []).find(c => c.course_id === cid);
  if (!g){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.announce_unloaded")}</div>`; return; }
  if (g.error){ box.innerHTML = `<div class="muted">${esc(g.error)}</div>`; return; }
  const anns = g.announcements || [];
  if (!anns.length){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.announce")}</div>`; return; }
  const cvUrl = (settings().canvas_url || "").replace(/\/+$/, "");
  box.innerHTML = anns.map(a => {
    const titleHtml = (cvUrl && a.id)
      ? `<a href="${escAttr(cvUrl)}/courses/${escAttr(cid)}/announcements/${escAttr(a.id)}" target="_blank" rel="noopener">${esc(a.title)}</a>`
      : esc(a.title);
    return `<div class="item"><div>
      <div class="item-title">${titleHtml} <span class="muted">${esc(String(a.posted_at || "").slice(0, 10))}</span></div>
      <div class="announce-msg-wrap"><div class="announce-msg expanded"><div class="announce-msg-inner">${esc(a.message)}</div></div></div>
    </div></div>`;
  }).join("");
}

/* 公告没有"按单门课拉取"的端点：syncAnnouncements() 是跨全部选中课程的批量操作
   （app.js:741-758）。所以这里只在缓存里没有这门课时才主动同步一次，
   否则每次切到公告标签都跑一遍全量同步，代价过大（裁定 R3）。 */
async function hubRefreshAnnounce(){
  if ((summaryResults || []).some(c => c.course_id === hubCid)) return;   // 有缓存 → 不重复批量拉
  const ok = await syncAnnouncements();
  if (ok && hubCid != null && hubTab === "announce") renderHubAnnounce(hubCid);
}
```

**同一步内还要补上本任务首次使用的 i18n 键**（由 Task 4 的裁定移到这里）。在 `frontend/i18n.js` 的 `I18N.zh` 段内、`hub.tab_empty.announce_unloaded` 那一行之后加：

```js
    "hub.tab_empty.announce": "这门课没有公告。",
```

在 `I18N.en` 段的同名位置之后加：

```js
    "hub.tab_empty.announce": "No announcements in this course.",
```

- [ ] **Step 2: 跑全部静态门**

Run:
```bash
for f in frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js; do node --check "$f" || exit 1; done
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/c.js && node --check /tmp/c.js && echo "拼接检查通过"
node tools/check_dom_ids.mjs
node tools/check_i18n_keys.mjs
.venv/bin/python -m pytest -q
```
Expected: 全绿，`228 passed`。

- [ ] **Step 3: 起真身，人工确认公告标签**

Run: `.venv/bin/python run_app.py --browser`

1. 先进设置页点「加载课程」（公告同步随之发生）。
2. 侧栏「课程」→ 点某门课 → 「公告」标签显示该课程公告。
3. 找一条**长公告**，确认正文**完整显示、没有被截断**（这是裁定 R4 要防的故障）。
4. 点公告标题 → 新标签页打开 Canvas 的公告页面。
5. 切到另一门课 → 公告换成那门课的，不串课。

此项为目视确认，无自动化断言。

- [ ] **Step 4: 提交**

```bash
git add frontend/app.js frontend/i18n.js
git commit -m "feat: 课程中心公告子标签（全文展示，规避 clamp 无按钮陷阱）"
```

---

## Task 7: ④-d 待办 + 讨论子标签

**Files:**
- Modify: `frontend/app.js`（替换 `renderHubTodo` / `renderHubDiscuss` / `hubRefreshDiscuss` 三个占位）
- Modify: `frontend/i18n.js`（zh/en 各加 2 键：`hub.tab_empty.todo`、`hub.tab_empty.discuss`）

**Interfaces:**
- Consumes: Task 4 的 `hubCid` / `hubTab`；全局 `todoItems`、`discussData`；既有函数 `topicRowHtml(tp)`（`app.js:1321-1331`，纯行渲染、不绑容器）、`fmtDue()`、`refreshBadges()`
- Produces: `renderHubTodo(cid: number): void`、`renderHubDiscuss(cid: number): void`、`hubRefreshDiscuss(cid: number): Promise<void>`

**数据形态（实测确认）**：
- `todoItems[i]` 形如 `{id, type, title, course_id, course_name, due_at, html_url, points_possible, overdue}` —— 来自 `backend/canvas_client.py:624-635`。
- `discussData` 形如 `{by_course: {cid: [topic]}, errors: {cid: msg}} | null`（`app.js:1293`），topic 形如 `{title, html_url, read_state, unread_count, replies_count, author, last_reply_at, posted_at}`。

⚠️ **JSON 键字符串陷阱（会静默失败，本任务的头号风险）**：后端 `get_announcements` 返回 `dict[int, list]`（`canvas_client.py:461`），但经 JSON 往返后**键变成字符串**。既有代码的应对是在读取时 `Number(k)` 归一（`app.js:207`）。讨论子标签若写成 `by_course[cid]`（`cid` 是 Number）将**恒为 undefined 且不报错** —— 与"看着正常、其实是空的"属同一类故障。**统一取用方式：`String(cid)`。** 本任务有三处必须用到它（读列表、读错误、写回缓存），一处写错就前功尽弃。

- [ ] **Step 1: 替换三个占位**

在 `frontend/app.js` 里把 Task 4 留下的这三行（连同注释）：

```js
/* 占位：由 Task 7 替换为真实实现 */
function renderHubTodo(cid){ $("hubPanel").innerHTML = `<div class="muted">${t("hub.tab_empty.todo_unloaded")}</div>`; }
function renderHubDiscuss(cid){ $("hubPanel").innerHTML = `<div class="muted">${t("hub.tab_empty.discuss_unloaded")}</div>`; }
function hubRefreshDiscuss(cid){}
```

**整块删除**（含那行注释），替换为：

```js
/* 待办子标签。只读 todoItems 缓存，不做后台刷新 ——
   /api/todo 没有课程参数，只能整批取（spec §4.4 明确本轮不刷新）。 */
function renderHubTodo(cid){
  const box = $("hubPanel");
  if (!box) return;
  const all = todoItems || [];
  if (!all.length){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.todo_unloaded")}</div>`; return; }
  const mine = all.filter(i => i.course_id === cid);
  if (!mine.length){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.todo")}</div>`; return; }
  box.innerHTML = mine.map(it => `
    <div class="item"><div>
      <div class="item-title">${it.html_url
        ? `<a href="${escAttr(it.html_url)}" target="_blank" rel="noopener">${esc(it.title)}</a>`
        : esc(it.title)}
        ${it.overdue ? `<span class="sched-badge err">${esc(t("todo.overdue_badge"))}</span>` : ""}</div>
      <div class="file-path">${esc(it.type || "")}${it.due_at ? " · " + esc(t("announce.due")) + " " + esc(fmtDue(it.due_at)) : ""}${it.points_possible != null ? " · " + esc(t("common.points", { n: it.points_possible })) : ""}</div>
    </div></div>`).join("");
}

/* 讨论子标签。复用既有 topicRowHtml（纯行渲染，不绑容器），不复制一份。
   ⚠️ discussData.by_course 的键经 JSON 往返后是字符串，必须 String(cid) 取用。 */
function renderHubDiscuss(cid){
  const box = $("hubPanel");
  if (!box) return;
  if (!discussData){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.discuss_unloaded")}</div>`; return; }
  const key = String(cid);
  const errs = discussData.errors || {};
  if (errs[key] !== undefined){ box.innerHTML = `<div class="muted">${t("discuss.course_fail")}${esc(errs[key])}</div>`; return; }
  const by = discussData.by_course || {};
  const list = by[key];
  if (list === undefined){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.discuss_unloaded")}</div>`; return; }
  if (!list.length){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.discuss")}</div>`; return; }
  box.innerHTML = list.map(topicRowHtml).join("");
}

/* 后台按本课程刷新讨论。只替换这一门课的结果，不动其它课程的缓存。 */
async function hubRefreshDiscuss(cid){
  const s = settings();
  if (!s.canvas_url || !s.canvas_token) return;
  const r = await api("discussions", { canvas_url: s.canvas_url, canvas_token: s.canvas_token, course_ids: [cid] });
  if (r.ok !== true) return;
  const key = String(cid);                                    // 同上：字符串键
  const prev = discussData || { by_course: {}, errors: {} };
  const by = Object.assign({}, prev.by_course || {});
  const errs = Object.assign({}, prev.errors || {});
  by[key] = (r.by_course || {})[key] || [];
  if (r.errors && r.errors[key] !== undefined) errs[key] = r.errors[key]; else delete errs[key];
  discussData = { by_course: by, errors: errs };
  if (hubCid === cid && hubTab === "discuss") renderHubDiscuss(cid);
  refreshBadges();                                            // 讨论未读数影响侧栏徽章
}
```

`by[key] === undefined` 与 `!list.length` 必须分开判断：前者是"这门课还没拉过"（未加载），后者是"拉过了但这门课没有讨论"（确实没有）。这与 Task 4 的空态分工一致。

**同一步内还要补上本任务首次使用的两个 i18n 键**（由 Task 4 的裁定移到这里）。在 `frontend/i18n.js` 的 `I18N.zh` 段内、`hub.tab_empty.discuss_unloaded` 那一行之后加：

```js
    "hub.tab_empty.todo": "这门课没有待办。",
    "hub.tab_empty.discuss": "这门课没有讨论。",
```

在 `I18N.en` 段的同名位置之后加：

```js
    "hub.tab_empty.todo": "No to-dos in this course.",
    "hub.tab_empty.discuss": "No discussions in this course.",
```

两个键在本步的 `renderHubTodo` / `renderHubDiscuss` 里都有对应分支引用，**不引入死键**。

- [ ] **Step 2: 验证字符串键确实被用到（防回归）**

Run:
```bash
grep -n 'String(cid)' frontend/app.js
```
Expected: 至少 2 处命中，且全部落在本次新增的 `renderHubDiscuss` / `hubRefreshDiscuss` 内。

Run（反向确认没有数字键直取）:
```bash
grep -n 'by_course\[cid\]' frontend/app.js || echo "OK：没有数字键直取 by_course"
```
Expected: `OK：没有数字键直取 by_course`。

- [ ] **Step 3: 跑全部静态门**

Run:
```bash
for f in frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js; do node --check "$f" || exit 1; done
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/c.js && node --check /tmp/c.js && echo "拼接检查通过"
node tools/check_dom_ids.mjs
node tools/check_i18n_keys.mjs
.venv/bin/python -m pytest -q
```
Expected: 全绿，`228 passed`。

- [ ] **Step 4: 起真身，人工确认两个标签**

Run: `.venv/bin/python run_app.py --browser`

**待办**：
1. 先进「待办」页点一次刷新（待办缓存由该页填充）。
2. 侧栏「课程」→ 点某门课 → 「待办」标签显示该课程的待办；为空时显示「这门课没有待办。」。

**讨论**（重点是字符串键）：
3. 先进「讨论」页（触发一次全量加载）。
4. 侧栏「课程」→ 点某门课 → 「讨论」标签**必须显示该课程的讨论条目**。若显示「讨论尚未加载。」或「这门课没有讨论。」而侧栏讨论页明明有这门课的条目，就是 `String(cid)` 用漏了。
5. 点讨论条目 → 新标签页打开对应 Canvas 讨论。

此项为目视确认，无自动化断言。

- [ ] **Step 5: 提交**

```bash
git add frontend/app.js frontend/i18n.js
git commit -m "feat: 课程中心待办/讨论子标签（讨论键按 String(cid) 取用）"
```

---

## Task 8: ③ 首页今日课表卡片可点击

**Files:**
- Modify: `frontend/shell.js`（`renderHomeToday` 补 `code` 字段与 `data-code`；`#homeTodayList` 加点击委托）
- Modify: `frontend/app.css`（`.today-card` 可点态）

**Interfaces:**
- Consumes: Task 4 的 `openCourseHub(canvasId, banwebCourse)`；`app.js:210` 的 `matchCourseByCode(code)`；全局 `banwebSchedule`
- Produces: 无（本任务是四个功能里最后一个，不被后续任务消费）

**两个坑（spec §5.3 已写明，务必照做）**：
1. **不要给卡片加 `data-goto`** —— `#page-home` 上已有一个 `[data-goto]` 委托（`shell.js:87-90`），会误触页面跳转。
2. `openCourseHub` 必须能接受 `canvasId` 为 `null`（Banweb 有课但 Canvas 没匹配上），此时课程中心退化为只显示课表信息 + 提示未匹配到 Canvas 课程。Task 4 已实现该退化路径。

- [ ] **Step 1: 给 slot 补上课程代号**

在 `frontend/shell.js` 的 `renderHomeToday()`（157-179 行）里，把 `slots.push` 那两行替换为：

```js
      slots.push({ start: m.start_min, end: m.end_min, room: m.room_short || m.room,
                   label: `${c.code} ${c.section}`, code: c.code });   // code 供点击时反查课程
```

- [ ] **Step 2: 卡片带上 `data-code`**

同函数内，把写 `#homeTodayList` 的那一段替换为：

```js
  $("homeTodayList").innerHTML = slots.map(s =>
    `<div class="today-card" data-code="${escAttr(s.code || "")}"><b>${esc(fmtTime(s.start))}–${esc(fmtTime(s.end))}</b>
       <span>${esc(s.label)}</span><em>${esc(s.room || "")}</em></div>`).join("");
```

- [ ] **Step 3: 加点击委托**

在 `frontend/shell.js` 中 `$("page-home").addEventListener("click", …)`（87-90 行）那一段**之后**插入：

```js
/* 今日课表卡片 → 该课程的课程中心详情。
   注意不能走 data-goto：上面那个 #page-home 委托会把点击当成页面跳转。 */
$("homeTodayList").addEventListener("click", (e) => {
  const card = e.target.closest(".today-card");
  if (!card) return;
  const code = card.dataset.code || "";
  if (!code) return;
  const bw = ((banwebSchedule && banwebSchedule.courses) || []).find(c => c.code === code) || null;
  const cv = matchCourseByCode(code);        // app.js:210，按「字母+4位数字」匹配，忽略 A/C 等后缀
  openCourseHub(cv ? cv.id : null, bw);
});
```

`matchCourseByCode` 与 `openCourseHub` 都是 `app.js` 里的函数声明，共享同一全局词法环境且 `app.js` 在 `shell.js` 之前加载，运行时可见 —— 与 `shell.js` 现有对 `summaryResults`、`renderCourseCheckboxes` 的用法一致。

- [ ] **Step 4: 加可点态样式**

在 `frontend/app.css` 中 `.today-card em` 规则之后插入：

```css
/* 今日课表卡片可点（进该课程的课程中心） */
.today-card[data-code]:not([data-code=""]) { cursor:pointer; }
.today-card[data-code]:not([data-code=""]):hover { border-color:var(--accent); }
```

- [ ] **Step 5: 跑全部静态门**

Run:
```bash
for f in frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js; do node --check "$f" || exit 1; done
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/c.js && node --check /tmp/c.js && echo "拼接检查通过"
node tools/check_dom_ids.mjs
node tools/check_i18n_keys.mjs
.venv/bin/python -m pytest -q
```
Expected: 全绿，`228 passed`，死键 `21 个（基线 21…）`（本任务不增删死键，与 Task 4 结束值持平）。

- [ ] **Step 6: 起真身，人工确认**

Run: `.venv/bin/python run_app.py --browser`

1. 先确保 AIMS 已登录并拉过课表（否则首页今日课表是空的，无从点击）。
2. 首页今日课表卡片 → 鼠标悬停有可点提示。
3. 点某张卡片 → **进入该课程的课程中心详情页**（不是跳到课表页）。
4. 若该 Banweb 课程在 Canvas 有对应 → 显示四标签且文件/公告有内容。
5. 若某门课 Canvas 没匹配上 → 显示「这门课在 Canvas 里没有匹配到…」+ 课表信息，不白屏、不报错。

此项为目视确认，无自动化断言。

- [ ] **Step 7: 提交**

```bash
git add frontend/shell.js frontend/app.css
git commit -m "feat: 首页今日课表卡片可点击，直达该课程的课程中心"
```

---

## Task 9: 收尾 —— 切语言接线 + 全量验收

**Files:**
- Modify: `frontend/i18n.js`（`applyLang()` 挂课程中心的渲染函数）
- Modify: `tools/check_i18n_keys.mjs`（仅在实测值与预期不符时）

**Interfaces:**
- Consumes: 前八个任务的全部产物
- Produces: 无

**为什么必须接线**：`applyLang()`（`i18n.js:680-706`）在切语言时重渲所有既有板块 —— 因为 `$$("[data-i18n]").forEach(...)` 只刷新**静态**节点，动态渲染的内容必须显式重渲（注释见 `i18n.js:701-702` 与 `705`）。课程中心的列表与四个标签都是动态渲染的，不挂进去就会**切语言后文案停留在旧语言**。

- [ ] **Step 1: 挂上课程中心的渲染函数**

在 `frontend/i18n.js` 的 `applyLang()` 中，把最后那三行：

```js
  if (typeof renderHomeAnnounceList === "function") renderHomeAnnounceList();
  if (typeof renderHomeDdlList === "function") renderHomeDdlList();
  if (typeof renderHomeGradeCard === "function") renderHomeGradeCard();
```

替换为：

```js
  if (typeof renderHomeAnnounceList === "function") renderHomeAnnounceList();
  if (typeof renderHomeDdlList === "function") renderHomeDdlList();
  if (typeof renderHomeGradeCard === "function") renderHomeGradeCard();
  /* 课程中心：列表与标签内容都是动态渲染的，data-i18n 那一轮刷不到它们。
     renderCourseHub() 自己在没有目标课程时会早退，所以这里不必碰 hubCid
     （它是 app.js 的 let，i18n.js 在它之前加载，直接读会踩 TDZ）。 */
  if (typeof renderCourseHubList === "function") renderCourseHubList();
  if (typeof renderCourseHub === "function") renderCourseHub();
```

`typeof 函数声明 === "function"` 这个守卫写法与上方既有行一致：函数声明是 var 作用域，在 `app.js` 执行前 `typeof` 得到 `"undefined"`，不会抛错。而 `hubCid` 是 `let`，在 `app.js` 执行前处于 TDZ，`typeof` 也会抛 —— 所以**绝不能在 i18n.js 里直接引用 `hubCid`**，判断交给 `renderCourseHub()` 自己做。

- [ ] **Step 2: 跑全部静态门（本轮的回归门）**

Run:
```bash
for f in frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js; do node --check "$f" || exit 1; done
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/c.js && node --check /tmp/c.js && echo "拼接检查通过"
node tools/check_dom_ids.mjs
node tools/check_i18n_keys.mjs
.venv/bin/python -m pytest -q
```
Expected: 全绿；pytest 输出 **`228 passed`**（与开工前一致，本轮不新增 pytest 用例）；`check_i18n_keys` 报 `死键 21 个（基线 21…）`。

若死键数不是 21，**不要去改基线** —— 先查清是哪一步多留了键，把该步的预期值对回来。

- [ ] **Step 3: 确认后端零改动**

Run:
```bash
git diff --stat $(git merge-base main HEAD) -- backend/ tests/ | tail -3
```
Expected: 无输出，或只有 `0 files changed`。

若后端有改动，**停下来**：spec §3 明确「不改后端。若实施中发现必须改，说明本稿有缺陷，应回来改设计」。应停止本任务并向用户汇报，而不是继续。

- [ ] **Step 4: 确认没有遗留占位函数**

Task 4 留下的六个占位必须在 Task 5-7 被逐个替换。Run:

```bash
grep -n '占位：由 Task' frontend/app.js || echo "OK：没有遗留占位"
```
Expected: 打印 `OK：没有遗留占位`。

（不要用「搜 `hub.tab_empty.*_unloaded` 应为 0 条」来判占位残留 —— Task 5/6/7 的**真实实现**里那些键仍被"未加载"分支合法引用，那个检查是假阳性。占位只认 `占位：由 Task` 这个注释锚点。）

若还有残留，回到对应任务补齐。

- [ ] **Step 5: 全量人工验收（浏览器形态）**

Run: `.venv/bin/python run_app.py --browser`

逐条走完 spec §7.3 的十项：

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

- [ ] **Step 6: 全量人工验收（默认桌面形态）**

Run: `.venv/bin/python run_app.py`（不带参数，内嵌原生窗口）

只走第 6、7 条：**预期是下载而非内联打开**，并提示保存路径（`file.downloaded` / `file.downloaded_saved`）。

**这是设计如此，不是缺陷** —— 内嵌 WKWebView 没有"新标签页 PDF 查看器"，且 `blob:` 不被外链桥的白名单接受（`app.js:17` 只放行 `^https?:`），壳层只暴露 `openExternal`（`run_app.py:59`）。要突破必须新增一项 JS→Python 能力（打开本地路径），属安全面扩张，用户已明确选择不做。

- [ ] **Step 7: 提交**

```bash
git add frontend/i18n.js
git commit -m "feat: 切语言时重渲课程中心（列表 + 详情标签）"
```

---

## 收尾：全量验收

Task 9 的 Step 2-6 即为收尾验收，不再重复。此处只记录**本轮验收的能力边界**，以免后来者误以为验过了：

| 层次 | 覆盖 | 手段 |
|---|---|---|
| 静态（每次改动都跑） | 语法、四文件拼接、DOM id 交叉引用、i18n 键一致/悬空/死键基线 | 5 条命令，全自动 |
| 结构（人工核对） | 函数是否被替换、字符串键是否用对、后端是否零改动 | 3 条 `grep`/`git diff`，全自动 |
| 行为（人工目视） | 上述十项 + 桌面形态两项 | **需人工执行**，本轮无自动化断言 |

**本轮没有行为层的自动化测试**，这是有意为之：仓库无构建步骤、无 `node_modules`、无 `package.json`，引入 jsdom 或 Playwright 会直接违反 Global Constraints 里的「不引入构建步骤」。spec §1 也已把本轮定性为测试策略偏重静态门与人工目视。后来者若要补行为层自动化，应作为**独立立项**，而不是塞进本轮。

---

## 自查记录（Self-Review）

**1. Spec 覆盖检查** —— spec 每一节都有任务承接：

| spec 章节 | 承接任务 |
|---|---|
| §2 目标 1（课程配置合并进设置页） | Task 2 |
| §2 目标 2（课程中心两级 + 四标签） | Task 4（骨架）+ 5/6/7（四个标签） |
| §2 目标 3（两处文件直链） | Task 3（Step 3-4 侧栏文件页；Step 2 弹层标题） |
| §2 目标 4（首页今日课表可点击） | Task 8 |
| §2 目标 5 / §3 非目标（后端零改动） | Task 9 Step 3 专门核对 |
| §4.1 纯 DOM 搬迁、id 原样保留 | Task 2 Step 2（逐字照抄六个 id） |
| §4.1 去掉 `switchPage("announce")` | Task 2 Step 4 |
| §4.2 详情页返回按钮、不切侧栏 | Task 4 Step 3（`backToHubList`） |
| §4.2 设置页「详情」按钮保持开弹层不变 | **本计划未触碰 `courseRowActionsHtml`** —— 该函数在 Task 2/4 中一行未改，即自动满足 |
| §4.3 四个标签的数据来源 | Task 5/6/7 的「数据形态」小节，逐条给出实测字段 |
| §4.3 `String(cid)` 警告 | Task 7（含 Step 2 的专项 grep 防回归） |
| §4.4 混合数据策略 | Task 4 的 `hubShouldRefresh` + Task 5/6/7 各自的刷新函数 |
| §4.4 待办不做后台刷新 | Task 7 的 `renderHubTodo` 注释明写，且不实现刷新函数 |
| §5.1 文件划分（不新增 JS 文件） | 「文件结构」节 |
| §5.2 两处都要 + 形态分叉 | Task 3 的 `openFileSmart` |
| §5.2 `api()` 不能用于二进制端点 | Task 3 Step 1 的注释明写 |
| §5.3 不加 `data-goto` | Task 8 Step 3 的注释明写 |
| §5.3 `canvasId` 可为 null | Task 4 的 `openCourseHub` + `hub.no_canvas` 分支 |
| §5.4 不复用全局渲染函数 | Task 4 的「不复用」小节 |
| §5.4 首次进入自动加载课程列表 | Task 4 Step 3 的 `initCourseHubTab` |
| §5.4 复用三个孤儿 `tab.*` 键 | Task 4 Step 1（HTML 引用）+ Step 6 |
| §5.4 切语言要重渲 | Task 9 Step 1 |
| §5.5 两种空态文案 | Task 4 Step 6 + Task 5/6/7 的分支判断 |
| §6 内联失败自动降级为下载 | Task 3 Step 1（`openFileSmart` 的降级分支） |
| §6 标签间互不影响 | Task 4 的 `renderHubTab` 分发（每个函数只写 `#hubPanel`） |
| §6 后台刷新失败静默保留缓存 | Task 5/7 刷新函数的 `if (r.ok !== true) return;` |
| §7.1 新增静态门 | Task 1 |
| §7.2 静态门清单 | 每个任务的验收步骤 |
| §7.3 十项人工验收 | Task 9 Step 5-6 |
| §8 风险表 | 逐条落到任务里的「陷阱」小节 |
| §9 实施顺序 | 裁定 R1 |

**2. 占位符扫描** —— 全文无 "TBD" / "TODO" / "implement later" / "add error handling" / "similar to Task N"。每个代码步骤都给出完整可粘贴的代码。唯一的"占位"是 Task 4 明确要求**当场创建、并在 Task 5-7 逐个删除**的六个临时函数，它们有具体实现（写一条空态文案），不是空洞的 stub，且 Task 9 Step 4 有专项检查确保清干净。

**3. 类型与命名一致性** —— 跨任务引用的名字逐一核对：

| 名字 | 定义于 | 被谁引用 | 一致 |
|---|---|---|---|
| `openFileSmart(courseId, fileId, name, courseName, moduleName, btnEl)` | Task 3 | Task 3（3 处）、Task 5 | ✓ |
| `openFileInline` / `downloadFileTo` | Task 3 | Task 3 | ✓ |
| `hubCid` / `hubTab` / `HUB_REFRESH_TTL` / `hubShouldRefresh` | Task 4 | Task 5/6/7 | ✓ |
| `openCourseHub(canvasId, banwebCourse)` | Task 4 | Task 8 | ✓ |
| `hubCourseName()` | Task 4 | Task 5 | ✓ |
| `renderHubFiles/Announce/Todo/Discuss(cid)` | Task 4 建占位 → 5/6/7 换实现 | Task 4 的分发表 | ✓ 签名一致 |
| `hubRefreshFiles(cid)` / `hubRefreshAnnounce()` / `hubRefreshDiscuss(cid)` | Task 4 建占位 → 5/6/7 换实现 | Task 4 的 `hubRefreshCurrent` | ✓ 签名一致（announce 无参） |
| `DEAD_BASELINE` | Task 1 | 25（T1）→ 24（T2）→ 24（T3，持平）→ 21（T4）→ 21（T5-T9）；T9 Step 2 校验 21 | ✓ |

**4. 实测数据核对** —— 计划中所有"当前值"均来自本轮真实执行，非估计：

| 结论 | 实测方式 |
|---|---|
| `I18N.zh` / `I18N.en` 各 338 键、集合完全一致 | `node:vm` 求值 `i18n.js` 前 672 行后比对 |
| 死键 25 个（含具体名单） | 两种口径（子串 / 带引号字面量）独立测得，结论一致 |
| 悬空引用 0 个 | 扫描 `t("…")` 与 `data-i18n*` |
| 动态家族只有 `wd.` | 正则扫 `"前缀."` 形式的拼接 |
| `.course-list` 在 `app.css` 中无任何规则 | `grep` 确认只在 `index.html:207` 出现 |
| 仓库不存在 `role="tab"` / `.tabs` / `.segmented` | `grep -rn` 确认无命中 |
| 展开按钮的布线与委托只扫 `#summaries` | 读 `app.js:869-870` 与 `app.js:888` |
| 四个标签的数据源都含 `course_id` 或可按 cid 取 | 读 `canvas_client.py:624-635`、`main.py:545-560`、`app.js:749-752`、`app.js:1293` |
