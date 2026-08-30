# UI 重构 + 中英切换 + 课表周视图 + 课程筛选 — 设计文档

日期：2026-08-31
状态：待用户 review

## 目标

对既有 Canvas 课程助手做四项改动：

1. **UI 重构**：更现代化、更「原生工具」质感、去掉所有 emoji；配色跟随系统浅色/深色。
2. **整体中英切换**：新增语言选项，切换后所有界面文案与**之后新一次同步**的 LLM 总结输出语言一起变。
3. **课表日历视图**：课表 tab 从平铺课程块改为**周视图时间网格**。
4. **课程筛选**：公告总结与文件两个 tab 各加**单选下拉**，按课程过滤展示。

## 非目标（Out of scope）

- 不做历史总结的自动重翻译（切换语言只影响新同步）。
- 不做手动深/浅色开关（纯跟随系统 `prefers-color-scheme`）。
- 不改 Banweb 抓取、AppleScript 写入、文件下载的后端逻辑本身（仅课表 meeting 增加解析字段）。
- 不引入前端构建工具 / 框架（保持无构建、静态文件直接由 FastAPI 提供）。

## 架构

### 前端拆分（方案 B）

`frontend/index.html` 拆为四个文件，`main.py` 挂载静态目录提供：

```
frontend/
  index.html    # 结构 + 少量内联（字体/临界 CSS 可选）
  app.css       # 全部样式（含 :root token + 深色覆盖）
  i18n.js       # zh / en 文案字典 + t() + applyLang()
  app.js        # 状态、渲染、API、日历视图、筛选逻辑
```

`backend/main.py`：

- `app.mount("/static", StaticFiles(directory=frontend_dir), name="static")`
- `index.html` 通过 `<link rel="stylesheet" href="/static/app.css">` 与 `<script src="/static/i18n.js">`、`<script src="/static/app.js">` 引用。
- `/` 路由仍返回 `FileResponse(FRONTEND)`。

原则：每个文件单一职责，可独立阅读与测试；不改变现有 API 契约（除下文明确列出的 `language` 字段与 `summary` 字段名）。

## i18n

### 字典结构

`i18n.js`：

```js
const I18N = {
  zh: { "app.title": "Canvas 课程助手", "btn.sync": "同步并总结", ... },
  en: { "app.title": "Canvas Course Assistant", "btn.sync": "Sync & Summarize", ... },
};
const LANG = () => localStorage.getItem("sc_lang") || "zh";
const t = (key) => (I18N[LANG()] && I18N[LANG()][key]) ?? key;
```

- 所有硬编码中文文案（header、tab、按钮、label、placeholder、状态提示、`sched-badge` 文案、周几、月份）替换为 `t()` 调用。
- header 加语言切换按钮「中 / EN」，`onclick` 写 `sc_lang` → `applyLang()`。
- `applyLang()`：更新所有静态文案（含 `<title>`、`aria-label`、placeholder），并重渲当前 tab 数据区（数据仍在内存，不重新请求）。

### LLM 总结语言

- `SyncRequest`（`main.py`）新增 `language: str = "zh"`。
- `/api/sync_announcements` 把 `language` 透传 `llm_client.extract_course_summary(..., language=...)`。
- `extract_course_summary` 新增 `language: str` 参数；`_SCHEMA_INSTRUCTIONS` 改为按语言生成总结：
  - `summary` 字段替换原 `summary_cn`；提示词写明「write the summary in Chinese」/「write the summary in English」。
  - fallback 分支同样产出 `summary` 字段。
- 前端渲染 `c.summary` 替换 `c.summary_cn`。

### 字段改名影响面

`summary_cn` → `summary`：

- `backend/llm_client.py`（提示词、返回字段、fallback）
- `tests/test_llm_client.py`、`tests/test_main.py`
- `frontend/app.js` 渲染处

## 主题（跟随系统）

- `app.css` 在 `:root` 定义浅色 token（中性灰白、细边框、单一强调色）；`@media (prefers-color-scheme: dark)` 下覆盖为暗色 token。
- 视觉基调：极简专业。去掉紫色渐层、大圆角色块、emoji；保留必要的信息层级（卡片、`course-card` 左边框、状态色）。
- 强调色用一个克制的单色（默认蓝/靛，非紫色），`--ok` / `--err` / `--warn` 状态色保留但调成更中性。

## 课表周视图日历

### 数据侧（后端）

`backend/banweb.py` 新增纯函数 `enrich_meetings(course)`（或等价处理），为每个 meeting 追加：

```python
{
  "start_min": 720,        # 24 小时换算成分钟（12:00 pm → 720）
  "end_min": 890,          # 14:50 pm → 890
  "days_list": ["F"],      # 由 "MWF" 拆成的单字母列表；原 days 字符串保留
}
```

- 复用已有 `parse_time_range` 的 am/pm 换算，`start_min`/`end_min` 供前端落位，避免前端重复解析。
- `get_schedule` 返回前对每个课程块应用，保持原字段不变（`time` / `days` 原样保留，供其它展示用）。
- 解析失败的 meeting（无 `time`/`range`）跳过，不产出 `start_min/end_min`（前端归入「无固定时间」区）。

### 前端渲染

- 周视图网格：左时间轴（默认 08:00–22:00，可因课程起止自动扩展上下界），7 列周一~周日。
- 每节有 `start_min/end_min` + `days` 的课，按 `(day, start_min)` 定位成块，块高 = `end_min - start_min`；同一 `code` 一个颜色（调色板循环）。
- 无 `start_min/end_min` 的课（无固定时间）→ 网格下方「无固定时间」区，灰显、不可选（沿用现有 disabled 逻辑）。
- 选择交互：课程块可点击切换选中（`code:section` 键），选中态高亮（描边/加深）；替代原 checkbox 列表。写入仍走 `banweb/write_calendar`，`selected` 集合语义不变。
- 结果徽标（`sched-badge`：已新建/已存在/更新/失败）仍在块上展示。

## 课程筛选

- 公告总结 tab：`summaryResults` 渲染成卡片前，按「全部课程 / 各 `course_name`」过滤。下拉数据源 = 当前 `summaryResults` 的 `course_name` 去重。
- 文件 tab：`fileCourses` 同理，按 `name` 过滤。
- 两个 tab 各一个 `select`，默认「全部课程」；切换后仅重渲对应数据区，不触发网络请求。
- 下拉为空（未同步/未加载）时禁用并显示占位。

## 后端改动小结

| 文件 | 改动 |
|------|------|
| `backend/main.py` | 挂 `StaticFiles`；`SyncRequest.language`；透传 language |
| `backend/llm_client.py` | `extract_course_summary(language=...)`；`summary_cn`→`summary`；提示词按语言 |
| `backend/banweb.py` | `enrich_meetings` / meeting 解析字段 |
| `frontend/*` | 拆 4 文件；i18n；主题；日历视图；筛选 |

## 测试

- `tests/test_llm_client.py`：`summary` 字段断言；`language` 参数影响提示词（可断言 `_build_prompt` 输出含目标语言指令）。
- `tests/test_main.py`：`sync_announcements` 透传 `language`；断言返回字段 `summary`；`/` 与 `/static` 路由可达。
- `tests/test_banweb.py`：`enrich_meetings` 对 `12:00 pm - 2:50 pm` → `start_min=720, end_min=890`；`days "MWF"` → `["M","W","F"]`；无 time 的 meeting 不产出字段。
- 前端（日历视图 / i18n / 筛选 / 主题）无自动化测试，靠手动验证：本地起服务，逐 tab 走一遍现有端到端流程，含语言切换与深色模式。

## 风险与注意

- 前端拆分后 `/static` 路由要确认不遮挡 `/api/*`（`StaticFiles` 挂 `/static`，天然隔离）。
- `applyLang()` 需覆盖到动态生成的状态提示（`setStatus` 的字符串模板）——状态文案也走 `t()`，带插值的用 `t` 占位替换。
- 课表块颜色循环要稳定（同一 code 每次渲染同色），用 code 哈希到固定索引。
- 保持 `banwebSchedule` localStorage 结构不变，老用户已存的预览仍能加载。
