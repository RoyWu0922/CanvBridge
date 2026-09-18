# 学校邮箱（Outlook / Microsoft 365）接入设计

**日期**：2026-09-18
**状态**：待实施
**可行性验证**：2026-09-18 已全程实测通过。文中标注「实测」的都是真跑出来的数字与响应，
不是推导出来的。**§3 与 §5 的参数请直接照抄，不要重新推导。**

## 1. 目标

把学生邮箱（`@my.cityu.edu.hk`，Microsoft 365）接进 CanvBridge，产出四样东西：

1. **邮件本身** —— 能读到发件人、主题、时间、正文
2. **AI 分组** —— 按 **课程 × 类型** 两级归类
3. **AI 总结** —— 每封给出摘要，聚合成列表
4. **DDL / 待办** —— 从邮件里提取截止事项，**展示 + 一键写入**日历/提醒

## 2. 非目标（本阶段明确不做）

- **不做 Microsoft Graph API**（见 §4.1，路走不通）
- **不做后台轮询/定时同步** —— 只在你点刷新时抓（见 §9）
- 不做发信、回复、转发
- 不做附件下载
- 不做多账号
- 不做邮件全文永久存档
- 不改动 AIMS 的任何现有行为

## 3. 实测结论（先看这节，它决定整个架构）

### 3.1 登录：与 AIMS 共用同一个 Okta，不增加登录次数

| 事实 | 实测值 |
|---|---|
| 学生邮箱域 | `@my.cityu.edu.hk`（Okta app `exk1e3prukrWKD3xa5d7`，brand "City University of Hong Kong - Student"） |
| SSO 提供方 | `auth.cityu.edu.hk` —— **与 AIMS 同一个 Okta** |
| 克隆 profile + 钥匙串凭据 | 登录**成功**，**全程无 MFA** |
| 设备信任 `DT` cookie | 有效期至 **2027-10-05** |

**结论：接入邮箱不会增加任何一次登录。** 它复用 AIMS 已经在维护的那条 Okta 会话。
`@cityu.edu.hk` 是**另一个** Okta app，对该用户返 403 —— 别用错域名。

### 3.2 会话持久性：完整重启浏览器后需要重登，但不需要 MFA

冷启动实测路径（APFS 克隆 profile + 全新 Chrome 进程）：

```
outlook.office.com/mail/
  → login.microsoftonline.com/common/oauth2/v2.0/authorize   「Pick an account」
  → auth.cityu.edu.hk/app/office365/<appid>/sso/wsfed/passive
  → Okta「City University of Hong Kong - Sign In」，要求重新输 EID
```

根因（实测 cookie 属性）：

| cookie | 域 | 有效期 |
|---|---|---|
| `idx` | auth.cityu.edu.hk | **会话级（-1）** |
| `JSESSIONID` | auth.cityu.edu.hk | **会话级（-1）** |
| `DT` | auth.cityu.edu.hk | 持久（至 2027-10-05） |

Okta 的会话 cookie 是**进程级**的，浏览器一关就没；微软侧的持久 cookie 兜不住，
会被联邦回 Okta。**所以：重启后要重新输一次 EID+密码，但因为 `DT` 在，不需要 MFA。**

这与应用已有的 `auto_login` 是**完全同一套机制** —— 程序不需要新碰任何凭据。

**运维推论**：应用从不优雅关闭浏览器（`banweb.py` / `main.py` 都没有 `atexit`/shutdown 钩子），
Python 退出后 Chrome 成孤儿被 `pkill -9` 带走 —— 这是**非正常退出**，Chrome 反而会把会话
cookie 写回库并在下次恢复。真正会丢 Okta 会话的只有：优雅退出 Chrome
（`banweb.py:862/870` 的 `browser.close()`）、macOS 重启关机、清 profile。
故真实重登频率 ≈「每次 Mac 重启一次」。

### 3.3 DOM 点击路线不可靠（这是被实测否掉的方案）

原本计划用 Playwright 点开邮件读正文。实测**行不通**：

- `[role="option"]` 邮件行用真实鼠标点击会被 **React portal 浮层**（`data-portal-node`，
  含 `aria-live` 节点与 `customScrollBar`）拦截 pointer events，重试 13 次仍失败
- 右键菜单同样被拦截
- 现代 OWA 的 Reading Pane 设置里**已经没有「标记为已读」选项**，行为不再由用户控制

→ **放弃 DOM 点击路线，改走 REST（§5）。**

### 3.4 REST 路线实测通过

登录后 OWA 自己会打大量 API 请求，**从中直接截获 bearer token**：

| 项 | 实测值 |
|---|---|
| 带 `Authorization` 头的请求数 | **109** |
| 打在 `service.svc` 的请求数 | **53** |
| token 形态 | `Bearer <JWT>`，长度 **5019–5026** 字符 |

拿截获到的 token 直接发 `FindItem`：**HTTP 200**，返回字段
`ItemId / Subject / From / DateTimeReceived / IsRead`（实测收件箱 20 封，其中未读 15 封）。

拿 `GetItem` 取正文：**HTTP 200，正文 2096 字符，`IsRead` 仍为 `False`**。

> **关键结论：走 API 读正文根本不会把邮件标成已读。**
> 「读完还原未读」这个需求随之**消失** —— 不需要还原，因为它压根没被标记。
> （EWS/OWA 里改已读必须显式调 `MarkAsRead`；`FindItem`/`GetItem` 是纯读的。）

## 4. 架构

```
┌─ frontend ─────────────────────────────────────────────┐
│  新增侧栏页 mail：两级分组展示 + 每条 DDL 的「写入」按钮   │
└───────────────┬────────────────────────────────────────┘
                │  POST /api/mail/list      （只读，不调 LLM）
                │  POST /api/mail/summarize （调 LLM，分组+总结+DDL）
┌───────────────▼────────────────────────────────────────┐
│  backend/mail_client.py（新增）                          │
│    ├ token 截获：Playwright 监听 service.svc 的 Authorization │
│    └ REST 调用：FindItem / GetItem（requests）           │
├──────────────────────────────────────────────────────────┤
│  backend/banweb.py（复用，不改行为）                      │
│    单 worker executor + 看门狗 + _retry_once             │
│    持久化 Chrome profile ~/.cityu_aims_profile           │
├──────────────────────────────────────────────────────────┤
│  backend/llm_client.py（复用）                            │
│    加一个 summarize_mail()，沿用 _call_chat / _parse_json │
└──────────────────────────────────────────────────────────┘
```

### 4.1 为什么只有一条取信路线

| 路线 | 判定 |
|---|---|
| Microsoft Graph API | 需应用注册 + 管理员同意，学生账号拿不到；未申请就走属 AUP 灰区。**不走。** |
| 设备码 OAuth（public client） | 同上，且 UX 别扭。**不走。** |
| **截获 bearer token + `service.svc` REST** | 复用现有 Chrome 与 Okta 会话，**零新增登录、零新增凭据**。**选它。** |
| DOM 点击抓取 | 实测被 portal 浮层拦截（§3.3）。**放弃。** |

**新增登录取信路线没有意义 —— 能用现成会话就没必要再谈一次授权。**

## 5. 取信层详解（照抄即可）

### 5.1 前置：复用哪个浏览器

必须复用 `banweb.py` 的**同一个持久化 context**。原因：Playwright 的
`launch_persistent_context` **只有一个 context**；另开一个 context = 另起一个 Chrome 进程 =
Okta 会话不共享 = **又要重新登录一次**，正好是要避免的事。

因此邮件在同一个 context 里开**自己的 tab**。带来两条约束：

1. **page 归属**：`banweb._find_page()`（`banweb.py:618`）优先挑 URL 以 `BANWEB` 或
   `LOGIN_HOST` 开头的页，否则退回 `ctx.pages[0]`。邮件 tab 的 URL 是
   `outlook.cloud.microsoft`，不匹配前两者；但只要 AIMS tab 不在，它就会变成 `pages[0]`
   被误认成 AIMS 页。**对策**：新增 `_find_mail_page(ctx)` 按邮件域匹配，
   并给 `_find_page` 加一条「排除邮件域」的过滤。
2. **看门狗恢复**：`_reset_browser()` 是整锅端的，会连邮件 tab 一起清掉。
   邮件操作必须能容忍 tab 消失并重建。好消息是 AIMS 与邮件的操作都跑在
   **同一个单 worker executor** 上，天然串行，不会互相踩。

### 5.2 token 截获

OWA 每次加载/重载都会自己打一批 `service.svc` 请求。所以截获时机就是**让邮件 tab 加载一次**：

```python
tok = {"v": None}
def _on_req(req):
    if "service.svc" in req.url and tok["v"] is None:
        a = req.headers.get("authorization")
        if a and a.lower().startswith("bearer"):
            tok["v"] = a.split(" ", 1)[1]

page.on("request", _on_req)
page.goto("https://outlook.office.com/mail/", wait_until="domcontentloaded")
# 等页面自然打出第一个 service.svc 请求
```

token 是 JWT，**可以解 payload 读 `exp`**（不验签，只读过期时间），据此在过期前主动重取。

### 5.3 调用 REST

固定项（实测通过，别改）：

```
POST https://outlook.cloud.microsoft/owa/service.svc?action=<Action>&app=Mail
Headers:
  Authorization: Bearer <token>
  Action: <Action>
  Content-Type: application/json; charset=utf-8
  X-OWA-Timezone: China Standard Time
```

**`FindItem`（列表）** —— body 实测可用：

```json
{
  "__type": "FindItemJsonRequest:#Exchange",
  "Header": {"__type": "JsonRequestHeaders:#Exchange", "RequestServerVersion": "Exchange2013"},
  "Body": {
    "__type": "FindItemRequest:#Exchange",
    "ItemShape": {"__type": "ItemResponseShape:#Exchange", "BaseShape": "IdOnly",
      "AdditionalProperties": [
        {"__type":"PropertyUri:#Exchange","FieldURI":"item:Subject"},
        {"__type":"PropertyUri:#Exchange","FieldURI":"message:From"},
        {"__type":"PropertyUri:#Exchange","FieldURI":"item:DateTimeReceived"},
        {"__type":"PropertyUri:#Exchange","FieldURI":"message:IsRead"}]},
    "ParentFolderIds":[{"__type":"DistinguishedFolderId:#Exchange","Id":"inbox"}],
    "Traversal":"Shallow",
    "Paging":{"__type":"IndexedPageView:#Exchange","BasePoint":"Beginning",
              "Offset":0,"MaxEntriesReturned":20}
  }
}
```

**`GetItem`（正文）** —— body 实测可用：

```json
{
  "__type": "GetItemJsonRequest:#Exchange",
  "Header": {"__type": "JsonRequestHeaders:#Exchange", "RequestServerVersion": "Exchange2013"},
  "Body": {
    "__type": "GetItemRequest:#Exchange",
    "ItemShape": {"__type": "ItemResponseShape:#Exchange", "BaseShape": "Default", "BodyType": "Text",
      "AdditionalProperties": [
        {"__type":"PropertyUri:#Exchange","FieldURI":"item:Body"},
        {"__type":"PropertyUri:#Exchange","FieldURI":"message:IsRead"}]},
    "ItemIds":[{"__type":"ItemId:#Exchange","Id":"<FindItem 返回的 ItemId>"}]
  }
}
```

结果路径：`Body.ResponseMessages.Items[*].RootFolder.Items[*]`（FindItem）／
`Body.ResponseMessages.Items[0].Items[0]`（GetItem）。**实测结构，直接按此解析。**

### 5.4 拉取范围：未读 + 最近 7 天

- `FindItem` 用 `Paging`（`IndexedPageView`）翻页，按 `DateTimeReceived` 过滤出最近 7 天
- 取并集：7 天内的全部 **∪** 全部未读（未读可能更旧，故需单独一遍）
- `IsRead` 已在列表响应里，**筛选不需要额外请求**
- 正文用 `GetItem` **批量取**（`ItemIds` 是数组，一次传多个可显著减少请求数）

## 6. AI 层

复用 `backend/llm_client.py`，**不新建 AI 子系统**。新增：

```python
def summarize_mail(base_url, api_key, model, mails: list[dict], language="zh") -> dict
```

`mails` 每项：`{index, subject, sender, received, body}`。

### 6.1 输出 schema（课程 × 类型 两级）

```json
{
  "groups": [
    {"course": "CS1315", "types": [
      {"type": "作业", "items": [
        {"mail_index": 3,
         "summary": "…",
         "calendar_events": [{"title":"…","start":"YYYY-MM-DDTHH:MM:SS",
                              "end":"YYYY-MM-DDTHH:MM:00","location":"…","notes":"…"}],
         "reminders": [{"title":"…","due_date":"YYYY-MM-DDTHH:MM:SS","notes":"…"}]}
      ]}
    ]}
  ]
}
```

- **`mail_index` 是回指的锚**：前端靠它把 AI 结果对回原文，绝不靠标题字符串匹配
- `calendar_events[] + reminders[]` 沿用与 `extract_course_summary` **完全相同**的形状，
  这样写入链路不用改
- 非课程邮件归入固定的「学校行政」组

### 6.2 对齐与回退

沿用 `extract_course_summary` 已验证的「逐条回退」思路：

- 模型漏条 / 返回非列表 / `summary` 非字符串 → 该条回退成原文摘录
- 整体 JSON 解析失败 → 重试一次，仍失败返回 `warning` 字段而非静默降级
- **`mail_index` 越界的一律丢弃**（不能把 AI 编的内容挂到真实邮件上）

### 6.3 token 预算

邮件正文可能很长。双控：**单封截断** + **总预算**（参照 `_MAX_SYLLABUS = 20000` 的先例）。
截断后仍要保证逐条对齐（截断不影响条数）。

## 7. 展示与写入层

- 侧栏新增 `data-page="mail"`（跟着现有 10 项的写法）
- i18n 键**必须 zh / en 两边都加**（`t()` 会回退到 zh，只加一边不会报错但会串语言）
- 两级分组：课程 → 类型 → 条目；每条展开可见 AI 摘要 + DDL/待办
- **写入走「展示 + 一键写入」**：复用已有的 `add_calendar_event` / `add_reminder` 端点，
  **每条经用户确认后才写**。邮件里的日期比公告更容易被 AI 提取错，不自动写。

## 8. 数据流

```
点「刷新邮件」
  → 确保 mail tab 存在（没有就开，落到 OWA 并按需 auto_login）
  → 页面加载产生 service.svc 请求 → 截获 bearer token
  → FindItem 翻页 → 筛出「未读 ∪ 最近7天」
  → GetItem 批量取正文        ← 至此均不碰已读状态
  → 返回前端展示（不调 LLM，快）
点「AI 总结」
  → POST /api/mail/summarize（带上 llm_base_url/key/model，沿用现有逐请求传参）
  → summarize_mail() → 课程×类型 + 摘要 + DDL/待办
  → 前端展示；每条 DDL 有「写入」按钮 → add_calendar_event / add_reminder
```

## 9. 安全与合规边界

**这一节是硬约束，实施时不得放宽。**

- **token 绝不落盘、绝不返回前端。** bearer token = 完整邮箱访问权。
  只在后端内存中存活，用完即弃。前端只拿到解析后的邮件字段。
- **绝不后台轮询。** 只在用户点刷新时抓。这是防「账号被风控」最重要的一条。
  没有任何定时任务、没有启动时自动抓取。
- **登录失败即停，不重试。** 复用 `#loginPrompt` 弹窗（`.modal.modal-sm`）问用户
  是否手动登录。**绝不自动重试登录。**
  （更正：该弹窗**不是** 2026-09-17 既有的，而是**本分支内**的 `00d6d6f feat(aims):
  自动登录失败改为先弹窗询问再手动登录` 引入的 —— `#loginPrompt` 在分支起点
  `69f1930` 上并不存在。复用它仍然成立，只是来历要写对。）
- **不新增任何凭据使用。** 邮箱重登与 AIMS 是同一套 `auto_login` + 同一个钥匙串条目
  `cityu_aims_login`。
- **不调用 `MarkAsRead`。** 除非将来有明确需求，否则永不改用户的已读状态。
- **不动 `DT` cookie。** 设备信任是免 MFA 的唯一凭据，清掉就要重新走 MFA。
- 请求节流：REST 路线本身就比 DOM 快得多，但仍应在批量 `GetItem` 之间保留间隔。

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| token 过期（约 1 小时） | 解 JWT `exp`，过期前重取；每次抓取前都重新截获一次 |
| 邮件 tab 被 AIMS 看门狗 `_reset_browser()` 清掉 | 邮件操作容忍 tab 消失并重建；单 worker 串行天然隔离 |
| `_find_page` 把邮件 tab 误认成 AIMS 页 | `_find_mail_page` + 给 `_find_page` 加邮件域排除 |
| 7 天范围翻页量大 | `Paging` 分页 + `DateTimeReceived` 过滤；未读单独一遍 |
| AI 把 DDL 日期提取错 | 逐条确认后才写入，绝不自动写 |
| OWA 前端改版导致 `service.svc` 变化 | 有版本风险，但比 DOM 选择器稳得多；失败时明确报错，不静默 |

## 11. 待验证项（实施时先跑掉这些）

1. **token 实际有效期** —— 解 JWT `exp` 确认，而不是假设 1 小时
2. **`GetItem` 批量上限** —— 一次能传几个 `ItemId`
3. **7 天范围的翻页边界** —— `Paging` + 时间过滤的确切组合
4. **`service.svc` 有无频率限制** —— 连发 N 个请求会不会被限流
5. **正文超长时的截断阈值** —— 与 LLM token 预算联调

> 第 3.4 节已证明「读正文不改已读」。若实施中发现某条路径**确实**改了已读状态，
> 可用的还原手段是 `MarkAsRead` + `IsRead:false`（动作名与字段已实测存在）。
> 但按当前证据，**这个还原逻辑不需要写**。

## 12. 落地顺序

1. `backend/mail_client.py`：token 截获 + `FindItem` / `GetItem` + `_find_mail_page`
2. `banweb.py`：`_find_page` 加邮件域排除（最小改动，不动现有行为）
3. 端点 `/api/mail/list`（只读，不调 LLM）
4. `llm_client.summarize_mail()` + 端点 `/api/mail/summarize`
5. 前端 `mail` 页 + zh/en i18n + 两级分组渲染
6. 对接 `add_calendar_event` / `add_reminder` 的逐条写入

第 1、2 步做完就该能端到端跑通「列出邮件」——**先验证取信层，再碰 AI 和 UI**。
