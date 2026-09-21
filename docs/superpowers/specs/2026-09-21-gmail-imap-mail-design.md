# 学校邮箱取信层改为 Gmail IMAP

**日期**：2026-09-21
**状态**：待实施

**取代**：`2026-09-18-school-mail-design.md` 的 §3（实测结论）、§4（架构）、§5（取信层详解）、
§8 数据流的前半、§10 中与 token/浏览器相关的行、§11（待验证项）

**修订**：同文件 §9（安全与合规边界）—— 逐条对照见本文 §2

> ⚠️ **本设计尚未对真实 Gmail 账号实测过。**
> 旧 spec 开头那句「文中标注『实测』的都是真跑出来的数字」**在本文件里不成立**。
> 本文件的 IMAP 参数来自 IMAP4 协议与 Gmail 文档，一律标「协议事实」；
> 凡未经真机验证的，全部列进 §10，**实施第一步就先跑掉它们**。
> 不要把本文当成已验的规格照抄。

---

## 0. 为什么改

用户已在学校侧配置好「邮箱转发到 Gmail」，取信改从 Gmail 读。

收益是**结构性**的，不是优化：

| | 改之前（OWA `service.svc`） | 改之后（Gmail IMAP） |
|---|---|---|
| 需要浏览器 | 是（复用 AIMS 的持久化 context） | **否** |
| 依赖 AIMS 登录态 | 是 —— AIMS 掉线，邮件一起坏 | **否** |
| 凭据寿命 | bearer token 约 1 小时，每次抓取重截 | 应用专用密码，不过期 |
| 只读保证 | 靠纪律「不调 `MarkAsRead`」 | **结构性**：`EXAMINE` + `BODY.PEEK` |
| 受看门狗 `_reset_browser()` 影响 | 是（邮件 tab 被清掉 → 需重试恢复） | **否** |
| 占用单 worker 浏览器线程 | 是 | **否** |

代价（用户已知情并接受）：

- 学校邮件的完整副本落在 Google（转发是服务端行为，逐封全量）
- **转发不回溯**：只有开启转发那一刻之后的邮件在 Gmail 里，历史邮件不在
- 新增一个凭据（应用专用密码）

---

## 1. 目标与非目标

目标形状**完全不变**：邮件本身、AI 两级分组、AI 总结、DDL/待办 + 一键写入。
只换取信来源。

沿用旧 spec §2 的非目标，其中一条的**理由**过期但结论不变：

- 「不做 Microsoft Graph API」仍然成立。旧理由是「只有一条路」，现在的理由改为
  「不需要」—— IMAP 已经够用，且不引入 GCP 项目与 OAuth 验证负担。

非目标新增：

- **不做 Gmail API / OAuth** —— 理由记在这里，别处不再重复：`gmail.readonly` 是
  **restricted scope**，正路要「应用验证 **+ 每年一次 CASA 安全评估**」（得先有网站和隐私政策），
  对单用户不值当。而且我查到的资料**自相矛盾**：一方说未验证应用走受限 scope 会带警告页放行
  （上限 100 用户），另一方说在验证 + CASA 之前一律 `access_denied`。**这个分歧我没能解决**，
  它本身就是避开 OAuth 的理由 —— 一条我无法确定能否走通的路，不该拿去赌
- **不做按 Gmail 标签筛选**：只读 `INBOX`。转发件落 INBOX，够用
- 不做附件下载（沿用旧非目标）、不做多账号

---

## 2. spec §9 修订（显式）

旧 §9 原文写着「**这一节是硬约束，实施时不得放宽**」。本次**不删除任何一条**，
只做两件事：一条**改写**（它在 IMAP 下字面不成立），一条**补强**。

| 旧 §9 条目 | 处置 | 新表述 |
|---|---|---|
| token 绝不落盘、绝不返回前端 | 保留 + **澄清** | 任何凭据（AIMS 密码、Gmail 应用专用密码）**不得以明文文件落盘、不得回传前端、不得出现在日志/报告/提交信息里**。钥匙串是**唯一**许可的存放处，与 `cityu_aims_login` 同级。 |
| **不新增任何凭据使用** | **改写** | **不新增任何登录流程**（不要浏览器、不要 Okta、不要 OAuth、不要 MFA）。新增**一个**凭据：Gmail 应用专用密码，存钥匙串 service `cityu_gmail_imap`。 |
| 绝不后台轮询 | 保留 | 只在用户点刷新时连一次 IMAP，连完即断。无定时任务、无启动时抓取、无连接池。 |
| 登录失败即停，不重试 | 保留 + **加强** | IMAP 认证失败 → 立即抛错，**绝不重试**。原来那条「目标已关闭就重试」是给浏览器 tab 用的，随浏览器路线一起删除。 |
| 不调用 `MarkAsRead` | 保留 + **加强** | 不仅不调，而且**做不到**：`select(readonly=True)`（= `EXAMINE`）+ 全部 `BODY.PEEK`。永不发 `STORE` / `COPY` / `MOVE` / `EXPUNGE`。 |
| 不动 `DT` cookie | 保留 | 与本改动无关，但照旧（AIMS 侧仍需要它免 MFA）。 |
| 请求节流 | 保留 | 批量 `UID FETCH` 之间留间隔，见 §5.4。 |

**为什么必须改写而不是照旧**：应用专用密码是一个**新凭据**，字面上违反旧 §9。
用户已明确批准走这条路，所以在这里**显式修订**——绝不把一句与实际相反的话留在文档里。

---

## 3. 架构

```
┌─ frontend ─────────────────────────────────────────────┐
│  mail 页（不变）＋ 设置页新增「学校邮箱（Gmail）」一组     │
└───────────────┬────────────────────────────────────────┘
                │  POST /api/mail/list        （只读，不调 LLM）
                │  POST /api/mail/summarize   （调 LLM：分组+总结+DDL）
                │  POST/GET/DELETE /api/mail/gmail/credentials
┌───────────────▼────────────────────────────────────────┐
│  backend/mail_client.py（重写内部，对外契约不变）          │
│    ├ 连接：imaplib.IMAP4_SSL(imap.gmail.com:993)         │
│    ├ 只读：select(readonly=True) + BODY.PEEK 全程         │
│    └ 解析：email 标准库（MIME / RFC 2047 头）             │
├──────────────────────────────────────────────────────────┤
│  backend/credentials.py（参数化 service，加 Gmail 一组）   │
├──────────────────────────────────────────────────────────┤
│  backend/llm_client.py（不变）                            │
│  backend/banweb.py（**移除**邮件相关的那几样）             │
└──────────────────────────────────────────────────────────┘
```

**邮件这条链路上不再有浏览器**。`banweb` 的 executor / 看门狗 / `_retry_once`
与邮件彻底解耦。

---

## 4. 对外契约（不得变）

上层（`main.py` 两个端点、`llm_client.summarize_mail`、前端 65 处引用）
**全部不动**，前提是取信层继续满足：

```python
list_mail(days: int = DEFAULT_DAYS) -> list[dict]
    每项：{"id": str, "subject": str, "sender": str, "received": str}

fetch_bodies(items: list[dict]) -> dict[str, str]      # {id: body}
```

逐字段：

| 字段 | 规定 |
|---|---|
| `id` | IMAP **UID** 的十进制字符串。同一次 `list_mail` 产出的 id 必须能被随后的 `fetch_bodies` 认出来（见 §5.5 的 UIDVALIDITY 核对）。 |
| `subject` | 已解 RFC 2047 编码字（Gmail 上大量中文主题是 `=?UTF-8?…?=`），解不出就原样给，**绝不编造**。 |
| `sender` | 显示名优先，退回邮箱地址（**口径**沿用旧 `_sender_name`；那个函数本身随 OWA 层删除，见 §7）。同样要解编码字。 |
| `received` | **ISO 8601 且带时区**，转成 UTC，形如 `2026-09-17T08:31:00+00:00`。前端 `fmtLocal()` 直接 `new Date(iso)`；解析不出会**原样显示**，所以格式错了会以肉眼可见的丑样子暴露，而不是静默。 |
| `is_read` | **删除**（见 §4.1）。 |

### 4.1 为什么删掉 `is_read`

旧契约里 `is_read` 有两个用途：`select_scope` 的「未读 ∪ 最近 N 天」，和前端
`app.js:3068` 的未读标记。

转发之后这个字段**失去意义**：真实已读状态在 Outlook 那一侧，转发过来的邮件在
Gmail **一律是未读**，而我们的只读访问永远不会改变它。于是：

- `select_scope` 的「未读」分支恒真 → 整个筛选退化成「最近 N 天」
- 前端那个未读标记恒亮 → 是**假信息**

留着一个恒为 `false`/恒亮的字段，正是本仓最忌讳的形状。故**删掉**：
后端不再产出该键，前端 `app.js:3068` 一处随之去掉。筛选逻辑简化为纯日期窗口（§5.6）。

---

## 5. 取信层详解

### 5.1 常量

```python
IMAP_HOST   = "imap.gmail.com"
IMAP_PORT   = 993
MAILBOX     = "INBOX"
TIMEOUT     = 30        # 传给 IMAP4_SSL(timeout=…)
DEFAULT_DAYS = 7        # 沿用
BODY_BATCH  = 5         # 一批取几封正文（比旧的 10 保守，理由见 §5.4）
_THROTTLE   = 0.5       # 批次间隔（秒），旧 spec §9「请求节流」
MAX_BODY_CHARS = 200_000  # 单封正文上限，纯防病态巨型邮件
```

### 5.2 只读三件套（本设计的要害）

1. `conn.select(MAILBOX, readonly=True)` —— Python 的 `readonly=True` 发的是
   **`EXAMINE`** 而不是 `SELECT`，服务端层面就禁止改动
2. **所有取件一律 `BODY.PEEK[...]`**。`BODY[...]` / `RFC822` 会**置 `\Seen`**
3. 永不发 `STORE` / `COPY` / `MOVE` / `EXPUNGE`

第 2 条是最容易在维护中写错的一条（改个字母就静默标记已读），所以它要
**专门一条用例 + 一条双向变异**钉死（§8）。

### 5.3 列表

```
UID SEARCH SINCE <dd-Mon-yyyy>          # 粗筛，日期粒度
UID FETCH <set> (BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])
```

一次批量取头，不逐封。`SINCE` 只到**日**粒度且按服务端时区，所以：

- 粗筛口径放宽一天：`since = (now - timedelta(days=days + 1)).strftime("%d-%b-%Y")`
- 精确过滤在 Python 里做（§5.6）

**Gmail 的 `SINCE` 是标准 IMAP4，协议事实。**

**但 `SINCE` 比的是 `INTERNALDATE`（邮件落进 Gmail 的时刻），不是 `Date:` 头。**
转发场景下这两者**可能不同**（`Date:` 是原始发信时间）。分工如下，别混：

- **筛选**按 `INTERNALDATE` —— 「最近 N 天**收到**的邮件」本就该按收到算
- **`received` 字段**取 `Date:` 头 —— 用户要看的是「这封邮件什么时候发的」；
  `Date:` 缺失或解不出时退回 `INTERNALDATE`，绝不因此丢件（fail-open，§5.6）

两者的差异幅度列入 §10 实测。

### 5.4 正文

```
UID FETCH <set> (BODY.PEEK[])          # 整封 RFC822
```

- 一批 `BODY_BATCH = 5`，批间 `time.sleep(_THROTTLE)`
- 取回后用 `email.message_from_bytes` 解析（§5.7）
- 解析出的正文按 `MAX_BODY_CHARS` 截断

**`BODY_BATCH` 为什么比旧的 10 小**：`BODY.PEEK[]` 会把**附件**一起给你（IMAP 没有
「只要正文」的通用取法）。5 × 单封上限，最坏情况的内存占用可控。
更省的做法是先用 `BODYSTRUCTURE` 摸清结构、再按段号取 `BODY.PEEK[1.1]`——
**本轮不做**（附件本就是非目标），登记在 §11。

### 5.5 UID 与 UIDVALIDITY

`id` 用 UID（十进制字符串）。UID 在同一邮箱内稳定，但邮箱被重建时
`UIDVALIDITY` 会变，届时**旧 UID 可能指向另一封邮件**——那是静默取错件，
本仓最不能接受的形状。

处理：

- `UIDVALIDITY` 在 `EXAMINE` **自己的响应里**就返回，优先从那次 `select()` 的响应里取；
  取不到再 `status()` 问一次（多一个往返，但可靠）
- `list_mail` 时记下当前值（模块级 `_uidvalidity`）
- `fetch_bodies` 连上后**重新读一次**，与记下的值比对
- **不一致 → 抛 `MailError`**，文案让用户重新刷新。绝不"尽力而为"地取

> **待确认（§10.9）**：`imaplib` 具体用什么形状暴露这个 untagged 响应（`conn.untagged_responses`
> 的键名与值类型）**我没有实读过**，不要在实施时照抄本文的措辞。先跑一次真连接看它长什么样。

模块级全局是**有意**的：同一请求内 `list_mail` → `fetch_bodies` 顺序调用，值必然相同；
并发请求读到的是同一个 INBOX 的同一个值，不会误报。真发生重建时**报错才是对的**。
（这个取舍写进代码注释，别让后来者以为它是随手写的全局。）

### 5.6 日期过滤

纯日期窗口，`select_scope` 保留但其「未读」分支删除：

```python
def select_scope(items, now, days=DEFAULT_DAYS) -> list[dict]
    cutoff = now - timedelta(days=days)
    # received 解析不出来的 → 保留（fail-open：宁可多给一封，不要静默丢件）
```

fail-open 是**刻意的**，沿用旧实现并经旧测试验证：单封格式异常的邮件不该让整次抓取少件。

### 5.7 MIME 与头部解码

- 头部：`email.header.decode_header` + `make_header`，解 RFC 2047 编码字。
  **不做这一步，中文主题/发件人会是乱码**——Gmail 上这是常态而非边缘情况
- 正文：
  - `multipart/alternative` → **优先 `text/plain`**
  - 其它 `multipart/*` → 拼接各 `text/plain` 段，**跳过** `Content-Disposition: attachment`
  - 只有 `text/html` → 转纯文本
  - charset 声明解不出 → `errors="replace"`，绝不抛
- HTML 转纯文本**复用 `canvas_client._TextExtractor`**
  （`backend/canvas_client.py:233`，`HTMLParser` 子类，块级标签处换行）。

  **已知的丑**：跨模块 import 一个下划线私有名。接受它，因为另一条路是复制一个
  HTML 解析器——两处副本必然漂移，比一个私有名难看得多。若实施时发现
  `canvas_client` 的导入链过重，退路是在 `mail_client` 内实现等价物**并说明为何不复制**，
  不默认走退路。

### 5.8 错误与「不重试」

- 认证失败（`imaplib.IMAP4.error` 且含 `AUTHENTICATIONFAILED`）
  → `MailError`，文案指向**设置页**（检查 Gmail 地址 / 应用专用密码），**不重试**
- 连接/超时错误 → `MailError("连接 Gmail 失败：…")`
- **不使用 `banweb._on_browser_thread` / `_retry_once`** —— 那是浏览器路线的机制，
  邮件不再需要。旧 spec §5.1 约束 2 的「tab 被看门狗清掉要整体重发」**由构造消失**
- 连接在 `finally` 里登出，登出本身的异常吞掉（不能让它盖住真正的错）

**行为变更提示**：旧实现里那段关于「归一化位置与 `_target_closed` 重试」的长注释
（`mail_client.py:303-325`）连同机制一并作废——不要把它搬过来。

---

## 6. 凭据

### 6.1 存放

钥匙串 service `cityu_gmail_imap`，account = Gmail 地址，密码 = 16 位应用专用密码。
与 `cityu_aims_login` 完全同级的处理，**不落盘文件、不回传前端**。

### 6.2 `credentials.py` 参数化（不复制）

把 service 名参数化，现有四个 AIMS 函数**保持原签名**做薄包装（零行为变更），
新增四个 Gmail 版：`save_gmail / get_gmail / get_gmail_address / delete_gmail`。

**理由不是洁癖**：里面那个「换用户名先删旧项」（M2 修复，
`credentials.py:save_credentials`）和跨 macOS 版本读 stdout/stderr 的 `_parse_acct`，
都是踩出来的。复制一份 = 两份副本必然漂移，且漂移的是**安全相关的删除逻辑**。

### 6.3 端点与设置页

镜像现有 AIMS 三件套：

```
POST   /api/mail/gmail/credentials          {address, app_password}
GET    /api/mail/gmail/credentials/status   → {ok, has_credentials, address}
DELETE /api/mail/gmail/credentials
```

设置页新增一组「学校邮箱（Gmail）」，两个字段（地址 / 应用专用密码），
照 `index.html:288-291` 的 AIMS 组写法（`type="password"` + `autocomplete`）。
**i18n 必须 zh / en 两边都加**（只加一边不报错但会串语言）。

### 6.4 前提

**Google 账号必须已开两步验证**——否则 Gmail 不提供应用专用密码入口。
实施前先确认；没开就先让用户开。

**凭据由用户在设置页输入，绝不经过对话/提交/日志。**

---

## 7. 删除清单

| 位置 | 删除内容 |
|---|---|
| `backend/mail_client.py` | OWA 取信整层，共 14 个函数 + 6 个常量（**已逐个对照源码核实**）：`_prop` / `_response_items` / `_sender_name` / `find_item_payload` / `get_item_payload` / `parse_find_items` / `parse_get_bodies` / `jwt_exp` / `extract_bearer` / `_post_json` / `_capture_token` / `collect_items` / `_do_list` / `_do_bodies`；常量 `SVC_URL` / `_HDR` / `PAGE_SIZE` / `MAIL_URL` / `_TOKEN_WAIT_TICKS` / `_TOKEN_WAIT_MS` |
| `backend/banweb.py` | `MAIL_HOSTS` / `_is_mail_url` / `_find_mail_page` / `_ensure_mail_page`，以及 `_find_page` 里的**邮件域排除**分支（`banweb.py:649/654`） |
| `frontend/app.js` | `app.js:3068` 的 `is_read` 取值 |
| `tests/test_mail_client.py` | 围绕 OWA 的用例整套重写 |
| `tools/check_mail_frontend.mjs` | 随 `is_read` 移除更新 |

**模块名保持 `mail_client.py`**，不改名 `gmail_client`：对外契约不变、文件职责不变
（「学校邮箱取信层」），变的只是传输。改名要动 `main.py`、测试与自检工具的 import，
零功能收益。

**不在上表里、但也不是原样保留的两样**——别当成"删掉了"或"没动"：

| 名字 | 处置 |
|---|---|
| `_parse_ts` | **保留函数名，改语义**：从解析 OWA 的 ISO 时间戳改为解析 RFC 2822 `Date:` 头（§5.3）。旧实现只认 `datetime.fromisoformat` 系的形状，直接拿来用会**全部解析失败 → fail-open 全留** —— 那会让「最近 7 天」静默失效，是本仓最忌讳的形状。 |
| `select_scope` | **保留函数名与 fail-open 语义，删掉「未读」分支**（§4.1、§5.6）。 |
| `MailError` / `TIMEOUT` / `DEFAULT_DAYS` / `BODY_BATCH` / `_THROTTLE` | 保留（`BODY_BATCH` 取值 10 → 5，见 §5.4）。 |

**`_find_page` 的邮件域排除要小心**：它是为 OWA tab 加的，现在没了 OWA tab 就是死代码；
但删它会改到 `_find_page` 的行为，**删之前先确认用例覆盖**（旧 spec §5.1 约束 1）。
若无用例覆盖，先补一条再删。

---

## 8. 测试与验证

**零出网是硬约束**（`tests/conftest.py` 的 socket 层守卫）。`imaplib.IMAP4_SSL`
在测试里**全部替身**，用一个假连接记录「被调用过什么」。

要钉死的不变式，每条**双向跑变异**（旧断言下先绿、新断言下再红）：

| # | 不变式 | 变异（应被抓住） |
|---|---|---|
| 1 | 取正文用 `BODY.PEEK` | 改成 `BODY[]` → 相关用例红 |
| 2 | `select(readonly=True)` | 改成 `readonly=False` → 红 |
| 3 | 不发任何 `STORE` | 加一个 `STORE` → 红 |
| 4 | UIDVALIDITY 不一致要抛错 | 改成忽略 → 红 |
| 5 | 认证失败**不重试** | 改成重试 → 红 |
| 6 | 批次之间有节流 | 去掉 `sleep` → 红 |
| 7 | 坏日期 fail-open（保留） | 改成丢弃 → 红 |
| 8 | RFC 2047 主题解码 | 不解码 → 红（断言中文主题逐字） |
| 9 | `received` 是带时区 ISO | 去掉时区 → 红 |

另需**正例**（不止否定式）：假连接上跑一遍，断言 `list_mail` 真的产出了带
`id/subject/sender/received` 的条目、`fetch_bodies` 真的把正文取回来了。
否定式断言只能证明"没做错事"，证明不了"做成了事"。

---

## 9. 数据流

```
点「刷新邮件」
  → 从钥匙串取 Gmail 地址 + 应用专用密码
  → IMAP4_SSL 连上 → login → select(INBOX, readonly=True)
  → 记 UIDVALIDITY
  → UID SEARCH SINCE (now - days - 1)
  → UID FETCH 批量取头（BODY.PEEK）
  → 解 RFC 2047 → 精确日期过滤 → 断开
  → 返回前端展示（不调 LLM，快）
点「AI 总结」
  → POST /api/mail/summarize
  → 重新连一次 → 核对 UIDVALIDITY → UID FETCH 批量取正文（BODY.PEEK[]）
  → summarize_mail() → 课程×类型 + 摘要 + DDL/待办
  → 前端展示；每条 DDL 有「写入」按钮 → add_calendar_event / add_reminder
```

---

## 10. 待实测确认（实施第一步就先跑掉）

1. **应用专用密码能否登录 `imap.gmail.com:993`** —— 一切的前提
2. **转发件保不保留原始 `From`** —— 课程分组完全靠它。M365 对外自动转发**通常**保留，
   但**未实测**。若不保留（例如变成"转发人代表原发件人"），分组逻辑要改，
   §4 的 `sender` 语义也要重定
3. **`SINCE` 在 Gmail 上的时区口径** —— 决定 §5.3 放宽一天是否够
4. **`UID SEARCH` / `UID FETCH` 的批量上限** —— `BODY_BATCH=5` 是保守猜测
5. **`BODY.PEEK[]` 实测确实不置 `\Seen`** —— 协议如此，但要在真账号上验一次
6. **单封正文的常见长度** —— 校准 `MAX_BODY_CHARS`
7. **Gmail IMAP 有无速率限制** —— 大批量 `UID FETCH` 会不会被限流
8. **`INTERNALDATE` 与 `Date:` 头差多少** —— 转发件上这两者的差值决定 §5.3 的分工是否要调整。
   最坏情况：`Date:` 被改写成转发时刻，那「原始发信时间」根本拿不到，§4 的 `received` 语义要重新定义
9. **`imaplib` 怎么暴露 `UIDVALIDITY`** —— §5.5 的「待确认」框；实读一次 `select()` 的响应，
   把真实形状写回本文件

---

## 11. 风险与对策

| 风险 | 对策 |
|---|---|
| 转发件 `From` 被改写 → 课程分组失效 | §10.2 先验；若被改写则改为按 `Subject`/正文启发式分组（会降级，需重新设计该节） |
| `BODY.PEEK[]` 连附件一起拉，内存与带宽放大 | `BODY_BATCH=5` + `MAX_BODY_CHARS`；`BODYSTRUCTURE` 分段取登记为后续优化 |
| Gmail 速率限制 | 批次 + `_THROTTLE`；被限流则明确报错，不静默重试 |
| 应用专用密码失效（改 Google 密码会作废） | 认证失败文案直接指向设置页重填；**不重试** |
| 转发中断（学校侧规则被关） | 症状是"邮件不再更新"，非报错。前端显示最近一封的时间即可自查；不新增后台检测（旧 spec §9 禁轮询） |
| 邮箱重建导致 UID 错位 | §5.5 的 UIDVALIDITY 核对，不一致即报错 |
| 用户误以为 Gmail 里有全部历史邮件 | 旧非目标「不做全文存档」照旧；§0 已记「转发不回溯」，需在设置页/UI 有对应说明 |

---

## 12. 落地顺序

1. **先跑 §10 的实测**（尤其 1 与 2）—— 不验就写代码等于赌
2. `credentials.py` 参数化 + Gmail 四个函数 + 端点（钥匙串这层可独立测）
3. `mail_client.py` 重写为 IMAP 取信层（纯函数先行：头部解码、MIME、日期过滤）
4. 接上 I/O 层：连接、只读三件套、批量取件、UIDVALIDITY
5. 删除清单（§7）逐项清掉，含 `banweb.py` 的邮件残留
6. 前端：`is_read` 移除 + 设置页 Gmail 组 + zh/en i18n
7. `tools/check_mail_frontend.mjs` 与 `tests/test_mail_client.py` 收尾

第 2、3 步做完就该能端到端跑通「列出邮件」——**先验证取信层，再碰 UI**。
