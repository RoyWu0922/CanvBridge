# 邮件规则分类与待办 / 时间表 —— 设计

日期：2026-09-24
状态：设计已逐节确认，待实现计划

## 背景

学校邮件现在只有一条**LLM 驱动**的整理路径：`POST /api/mail/summarize`
（`backend/llm_client.py:383` `summarize_mail`）让模型按「课程 → 类型」两级归类，
产出 `calendar_events` 与 `reminders`，界面上再一键写进 Apple 日历。

这条路径解决不了两件事：

1. **分类不可控**：分到哪个课程由模型每次自由决定，用户无法规定「这位教授的信归到哪一栏」。
2. **去向不对**：它通向日历。用户要的是**待办清单 + 时间表**——一个看「要做什么」、
   一个看「什么时候」，而不是往日历里塞事件。

本设计新增的是**规则驱动**的分类：分类结果 100% 由用户自定义的规则决定，
可预测、可复现、改规则立刻生效；AI 只保留一件窄活——从正文里抽出待办与日期。

## 已确认的决定

| # | 议题 | 决定 |
|---|---|---|
| 1 | 规则与现有 AI 归类的关系 | **规则分类 + AI 只抽日期**。分类不经 AI；现有 `summarize_mail` 与邮件页的 AI 分组视图**原样保留、不碰** |
| 2 | 产出放哪 | **新开一页**（侧栏与「课表」「待办」并列），不动 `todo` / `schedule` 两页 |
| 3 | 一封邮件命中多条规则 | **多标签**。todolist 按邮件去重，同一件事不重复出现 |
| 4 | 页面形状 | **上下两块**：上「待办清单」（按分类分组、可勾选完成）、下「时间表」（只放抽得出日期的，按日期从近到远） |
| 5 | 规则在哪评估 | **方案 A：后端纯函数引擎**，规则随请求体传递，后端保持无状态，不引入新的后端持久化 |

## 非目标

- 不修改 `todo` 页、`schedule` 页、邮件页的 AI 分组视图。
- 不做邮件的写操作（不发信、不改已读、不移动、不删除）——沿用 `mail_client` 的只读保证
  （EXAMINE + `BODY.PEEK`）。
- 不做「按规则自动写日历」。
- 不引入后端规则持久化（规则在 localStorage）。

## 1. 契约改动：给邮件列表加 `sender_addr`

### 问题

`/api/mail/list` 返回的 `sender` 是**显示名优先，退回地址**
（`backend/mail_client.py:90` `sender_name`：`return name or addr`）。
再加上 `_CONTRACT_KEYS = ("id", "subject", "sender", "received")`
（`backend/mail_client.py:63`）是个**白名单**，`_do_list` 返回前按它裁剪
（`backend/mail_client.py:455`）。

**后果**：邮件列表里通常**根本没有邮箱地址**。用户按「教授邮箱」写的规则会
**一条都匹配不上，而且是静默的**——规则看起来完全正常，只是永远不命中。

### 改动

`header_item`（`backend/mail_client.py:187`）增加一个键，并把键名加进 `_CONTRACT_KEYS`：

```python
# 与既有 sender（显示名优先）并列；本键只用于规则匹配，不上屏
"sender_addr": parseaddr(msg.get("From"))[1].strip().lower(),
```

`parseaddr` 不是新依赖：`sender_name` 内部已在用它，`_addr_domain`
（`backend/mail_client.py:99`）是同族先例。这是把**已经解出来又被丢掉**的东西留下。

### 影响面

- `_CONTRACT_KEYS`（`mail_client.py:63`）加一个元素；`/api/mail/list` 与
  `/api/mail/summarize` 共用同一批条目，两者都会多出这个键。
- 前端只是多一个不显示、只用于匹配的字段。
- `tests/test_mail_client.py` 补契约断言。
- **待核**：本次只确认了 `tests/` 里没有直接引用 `_CONTRACT_KEYS` 的断言；
  是否存在「整个条目字典相等」这类**间接**断言尚未查。实现第一步先查，
  有则一并更新。

### 白拿的能力

`@cityu.edu.hk` 这类**按域**匹配成为可能（子串匹配天然支持）。

## 2. 规则模型与纯引擎

### 规则形状

存在 localStorage，每次随请求体发（与 `canvas_token` 同一模式）：

```json
{ "id": "r-3", "name": "李教授",
  "senders": ["li.wei@cityu.edu.hk", "@cityu.edu.hk"],
  "keywords": ["实验报告", "lab report"],
  "search_body": false }
```

### 匹配语义

| 字段 | 匹配对象 | 方式 |
|---|---|---|
| `senders` | `sender_addr` | 大小写不敏感**子串** |
| `keywords` | **主题**；`search_body` 为真时**连正文一起** | 大小写不敏感**子串**（不分词） |

### 三条判定（一律「宁可报出来也不静默」）

1. **空规则 = 一条也匹配不上。** `senders` 与 `keywords` 皆空时，`label_mails` 那两句
   `r["senders"] and any(...)` / `r["keywords"] and any(...)` 逐项判断，空列表让 `any`
   对空序列为假；原文的「任何字符串都包含空串」说的是空**元素**，那条路已被 `blank_item` 剥掉。
   处置不变：编辑器存不下、后端丢弃并报码 —— 与图书馆静默失效同族，报出来比留着诚实。
2. **空白项过滤**：`["", "  ", "报告"]` 里的空串同上，`validate_rules` 丢弃并报码。
3. **多标签、与顺序无关**：命中几条就带几个标签。规则之间没有先后，
   **拖错顺序不会毁掉分类**——这是选多标签换来的性质，实现时不要引入隐式排序。

匹配不到任何规则的邮件进「未命中」桶。

### 模块

新增 `backend/mail_rules.py`，**无 I/O 的纯函数**，照 `backend/library_query.py` 的路子
（纯函数 + 独立测试文件 + 可变异检验）：

```python
def validate_rules(raw: list) -> tuple[list[dict], list[str]]   # 收敛 + 警告码
def label_mails(rules: list[dict], mails: list[dict]) -> dict    # {邮件 id: [规则 id, ...]}
```

警告码沿用图书馆那套**跨语言契约**（后端码表 ↔ `frontend/i18n.js` 的中英双语键），
键前缀 `mailtodo.warn.<code>`，与既有的 `library.warn.<code>` 同构。

## 3. 端点与数据流

### `POST /api/mail/classify` —— 纯规则，零 AI、零成本、可复现

- body：`{ days, rules }`（`days` 默认 **14**——待办跨周，邮件页的 7 天会挡住下周的事）
- 流程：`list_mail(days)` → `validate_rules` → `label_mails`
- 响应：`{ok, count, mails:[{id,subject,sender,sender_addr,received,labels:[...]}], warnings}`

**正文那趟的代价（重要）**：只要有**任意一条**规则 `search_body` 为真，就必须给窗口内
**所有**邮件取正文。曾考虑「已匹配上的邮件跳过取正文」，**那是错的**——多标签下，
一封已被「李教授」命中的邮件仍可能再命中一条只在正文出现的关键词规则，跳过即
**静默漏标签**。所以宁可慢也不漏：页面明示「正在读取正文…」，30 封约 3 秒
（现有 5 封一批、批间 0.5s 节流）。**默认关闭**，默认路径不碰正文。

### `POST /api/mail/extract` —— AI 只在这一趟

- body：`{llm_base_url, llm_api_key, llm_model, ids:[...], language}`
  （继承 `LLMConfig`，与 `MailSummaryRequest` 同形）
- 流程：`fetch_bodies(ids)` → LLM → 归一
- 响应：`{ok, items:[{id, summary, reminders:[{title,due_date,notes}],
  calendar_events:[{title,start,end,location,notes}]}], warnings}`

**新写聚焦的 prompt/schema，不套用 `summarize_mail`**：

- (a) 它强制的「课程 → 类型」两级归类正是本设计要丢掉的东西，白花 token；
- (b) 它的「每封必须被归类」「一封只能出现一次」是**分组**约束，套到抽取上会平白
  多出失败模式；
- (c) 现有的 AI 分组视图保留不动，两套互不干扰。

**但复用它的两个已验证零件**：

1. `_call_chat(..., json_mode=True)` 的重试写法（`llm_client.py:397-408`）；
2. 「**越界 `mail_index` 一律丢弃、绝不把 AI 编的内容挂到真实邮件上**」这条归一纪律
   （`llm_client.py:349-353`）。

新 schema 沿用现成字段形状（`calendar_events[{title,start,end,location,notes}]`、
`reminders[{title,due_date,notes}]`，见 `llm_client.py:275-300`），不另造一套。

### 成本与缓存

- **按需触发**（一个按钮），**不自动跑**——否则每开一次页烧一次 token。
- 结果按**邮件指纹**缓存进 localStorage（指纹的确切定义见第 4 节「稳定键」）。
- 第二次点只对**未缓存**的邮件发请求。
- **不用 IMAP UID 当键**：UID 会随 UIDVALIDITY 变，用它当键会让缓存与勾选状态在
  服务器重整后**整批失效**。勾选完成存 `sc_mailDone`，键 = 指纹 + 条目下标。

### 错误约定

跟随现有邮件端点：一律 **HTTP 200 + `{ok:false, error}`**
（`frontend/util.js` 的 `api()` 不看状态码）。

## 4. 页面、i18n、前端校验

### 加一页要同步的点（漏一处即静默不显示）

| # | 位置 | 改动 |
|---|---|---|
| 1 | `frontend/shell.js:5` `PAGES` | 加 `"mailtodo"`（不在数组里 → `switchPage` 静默回落 home） |
| 2 | `frontend/shell.js:11` `PAGE_INIT` | 加 `mailtodo: () => initMailTodoTab()` |
| 3 | `frontend/index.html:40` 之后 | nav 按钮 `<button class="nav-item" data-page="mailtodo">`（普通页插在 mail 之后，`nav-gap` 在 41 行、settings 固定在最底） |
| 4 | `frontend/index.html:248`（`#page-mail`）附近 | 插 `<div id="page-mailtodo" class="page" hidden>` |
| 5 | `frontend/i18n.js` | `nav.mailtodo` 写两处：zh 主序列 `2-12`（`nav.mail` 在 12 行）、en `427-437`（`nav.mail` 在 437 行） |
| 6 | `frontend/i18n.js` | 页面内全部文案键（工具条、两块标题、规则编辑器、警告码 `mailtodo.warn.*`）——**每个键都要 zh / en 各写一次**（I18N 对象收于 `:852`）。`check_i18n_keys.mjs` 会拦「只写一侧」与「写了没人引用」 |
| 7 | `frontend/app.js` | `initMailTodoTab()` / `renderMailTodo()` |

CSS **不需要改**：`app.css` 里没有任何 `#page-*` 选择器，页面显隐靠 `.page[hidden]`。

### 硬性纪律（`app.js` 有长注释记录过的白屏坑）

- 被 `applyLang()` 读到的状态，必须在 `app.js:2459` 那次顶层 `applyLang()` 调用
  **之前**声明。既有同族状态可参照：`app.js:2424` `MAIL_DAYS`、`app.js:2425` `mailState`。
- **不只是新增状态会踩**：给既有函数加一个**更早的调用时点**同样致命。
  判据是「这个函数读了哪些顶层 `let`，那些声明在**最早**调用时点之前吗」。
- `typeof` 守卫无法判别这类错误，`node --check` 也查不出来。

### DOM 骨架

工具条（刷新 / 「AI 抽取待办与日期」/ 「分类规则…」/ 天数选择 / 状态行）
＋ 规则编辑面板（默认折叠）＋ `#mtTodo`（按分类分组、可勾选）
＋ `#mtSchedule`（按日期排的议程，只放抽得出日期的）。

`sender_addr` 只用于匹配，**不上屏**。

### 两块各自放什么

- **待办清单**：`reminders` —— 一条待办一个条目，可勾选完成，按分类分组。
- **时间表**：`calendar_events`（按 `start`）与 `reminders`（按 `due_date`）**合并**
  后按时间从近到远排。**只有抽得出日期的条目能进**；抽不出日期的只留在上方清单里，
  不会在时间表里凭空占位。

### 多标签邮件在清单里怎么摆（**推翻了先前选项预览里的「只出现一次」**）

第 3 题选的是「多标签」，选项预览里写「待办列表中：只出现一次（按邮件去重）」。
**这两条不能同时兑现**：一封邮件带 N 个分类，清单又按分类分组，要「只出现一次」
就必须替它挑一个主分类——而挑主分类要么靠规则顺序（**正是多标签换来「拖错顺序
不会毁掉分类」所排除的东西**），要么靠别的任意判据。

所以本设计这样定，**若你不认同请在审规格时驳回**：

> 一封邮件命中 N 个分类，就在 N 个组下**各出现一次**；但**勾选状态共用同一把键**
> ——勾一处，各处同步。

理由是真正的伤害（同一件事被勾两次、或勾了一处忘了另一处）由共享状态消掉，
而重复的**行**只是分组视图的必然结果。**你选它时在意的是「同一件事不要被当三件事」，
那一点被保住了。**

### 稳定键（避免歧义）

- **邮件指纹** = `主题 + 发件人 + 收信时间` 拼成的**稳定字符串键**。
  **不是密码学哈希**——它只当 localStorage 的键，不需要抗碰撞，也不需要 `crypto.subtle`
  （那是异步的、还要 secure context）。
- **勾选状态的键 = 邮件指纹 + 条目下标**。同一封邮件抽出两条待办时，
  勾掉一条**不能**连带勾掉另一条——用邮件指纹当键就会犯这个错。

### 规则编辑器

- 增删改每条规则的 名称 / 发件人 / 关键词 / 正文开关。
- 名称为空，或两个匹配列表都空 → **保存按钮禁用并写明原因**。
- **导出 / 导入 JSON** 两个按钮：给「规则在 localStorage、换浏览器就没了」兜底。

### 三套前端自检会被触发

- `tools/check_dom_ids.mjs`：JS 里 `$("id")` 必须在 `index.html` 存在。
- `tools/check_i18n_keys.mjs`：双语键集必须一致；新键必须有人引用（死键数不得超基线）。
- `tools/check_mail_frontend.mjs`：1101 行，自带最小 DOM 替身、零出网，
  守 `renderMail` 的 `mail_index` 契约。

**未决（实现时先读再定）**：新页要不要并进 `check_mail_frontend.mjs`，取决于它的
DOM 替身装配方式是否可复用。**本设计不预先承诺**：能复用则加一段，不能复用则抽公共壳。
这一页最该被守的是：**勾选状态的键必须是指纹、不能是 UID**（「换成 UID」的变异要能被杀掉）。

## 5. 测试与验证

| 文件 | 内容 |
|---|---|
| `tests/test_mail_rules.py`（新） | 纯模块 TDD：空规则、空白项、大小写、子串、正文开关、多标签、未命中 |
| `tests/test_mail_client.py` | 补 `sender_addr` 契约断言 |
| `tests/test_main.py` | 两个新端点：请求校验、`200+{ok}` 错误约定、warnings 透传 |
| `tests/test_llm_client.py` | 新 schema 的归一（越界 index 一律丢） |

按仓库既有口径：先 RED 并**核对红的正是预期那几条**，再 GREEN 跑全量；
关键守卫做**变异检验**（空规则守卫、指纹 vs UID 的键），且变异要打在**承重**的那一行上。

## 6. 风险

- **`sender_addr` 是契约改动**，虽然只是加键，但 `/api/mail/list` 与
  `/api/mail/summarize` 共用条目，实现时须全量跑测试并核前端 `check_*` 三件套。
- **`search_body` 的耗时**对窗口大小敏感（`days` 越大越慢），页面须明示进度，
  否则用户会以为是卡死。
- **规则在 localStorage**：换浏览器/清缓存即丢，靠导出/导入兜底（已列入设计）。
