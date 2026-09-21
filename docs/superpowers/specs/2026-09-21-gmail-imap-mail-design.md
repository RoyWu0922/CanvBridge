# 学校邮箱取信层改为 Gmail IMAP

**日期**：2026-09-21
**状态**：待实施（2026-09-21：§10 已实测一轮，§10.2 闸门通过 —— 见 §10 逐条状态）

**取代**：`2026-09-18-school-mail-design.md` 的 §3（实测结论）、§4（架构）、§5（取信层详解）、
§8 数据流的前半、§10 中与 token/浏览器相关的行、§11（待验证项）

**修订**：同文件 §9（安全与合规边界）—— 逐条对照见本文 §2

> ⚠️ **本文件不是「已全验」的规格 —— 读之前先分清三种标注。**
> - **实测（2026-09-21）**：真机跑出来的值。来源是**两个一次性脚本**（跑完都已删除，见 §10 开头）。
>   逐字原始输出是**会话内的存档**（scratch 目录下的 `task-1-measurements.md`，
>   **被 `.gitignore` 忽略、不在本仓库里，按流程最终会被删除**）——
>   本节引用它只为交代来源，**读者不要到仓库里去找它**，也不要指望它长期存在。
>   **只有带日期的那句「实测」才算数。**
> - **未实测**：仍是假设，实施第一步要先跑掉。§10 里逐条标了状态。
> - **协议事实**：来自 IMAP4 协议与 Gmail 文档，**不是本机实测**。
>
> 旧 spec 开头那句「文中标注『实测』的都是真跑出来的数字」**在本文件里只对带日期的标注成立**。
> 不要把「协议事实」当成已验结论照抄，也不要把「未实测」项当成已验项。

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

> **实测（2026-09-21，§10.9）**：`conn.select(MAILBOX, readonly=True)` 返回 `OK`；
> `conn.untagged_responses` 里相关键为 `['EXISTS', 'FLAGS', 'UIDVALIDITY']` ——
> `UIDVALIDITY` **确实在那次 `select()` 自己的响应里**（本节第一条成立）。
> `conn.untagged_responses["UIDVALIDITY"]` 的**原样值**是 `[b'1']`：
> **值是 1，形态是「单元素 bytes 列表」**，既不是 `int` 也不是 `str`。
> 取值必须写成 `int(resp["UIDVALIDITY"][0])`（取首元素再转 `int`），
> **不可假定它直接是数字**。

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
- HTML 转纯文本**复用 `canvas_client.strip_html()`**
  （`backend/canvas_client.py:254`）。它是**公开函数**，内部就是 `_TextExtractor`
  （`HTMLParser` 子类，块级标签处换行）加空白归一 —— 正好是邮件正文需要的。

  ~~原稿写的是「跨模块 import 私有名 `_TextExtractor`，接受这个丑」~~
  **那条作废（2026-09-21 写计划时更正）**：`strip_html` 是公开的，
  直接用它即可，既没有私有名问题、也不必自己复制一个解析器。

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
| `backend/banweb.py` | **只删 `_find_mail_page` / `_ensure_mail_page`**（邮件客户端的浏览器入口，改走 IMAP 后无人调用）。**`MAIL_HOSTS` / `_is_mail_url` 与 `_find_page` 里的邮件域排除一律保留** —— 理由见下方「不能删的那两样」。 |
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

**不能删的那两样（2026-09-21 写计划时发现的 spec 缺陷，已就地更正）：**

原先本节写的是「连 `MAIL_HOSTS` / `_is_mail_url` / `_find_page` 的邮件域排除一起删掉」。
**那是错的，会静默吃掉用户自己的 Outlook 标签页。**

理由：后端有一条**附着到用户真实 Chrome** 的路径（`connect_over_cdp(127.0.0.1:9339)`，
`_browser_headless is None`）。这条路径下 `ctx.pages` 里是**用户自己开着的标签页** ——
其中很可能有他手动开着的 Outlook。`banweb.py` 里对此有白纸黑字的注释：

> 「没有可用 AIMS 页 → None（无窗口，或**只剩邮件 tab**）。与「无窗口」同等对待，
> **绝不擅自把邮件 tab 当 AIMS 页用**。」
> 「页面还在（如只剩邮件 tab）⇒ **绝不** close：……还会顺带关掉用户刚打开的邮件 tab。」

删掉排除逻辑，`_find_page` 就会把用户的 Outlook tab 认领为 AIMS 页，接着
`goto(TERM_PAGE)` 把它导航走 —— 用户自己的邮箱页面就此消失。**邮件功能不再用浏览器，
不等于用户不用。**

而且「没有可用 AIMS 页 → 返回 None」是**轮询绝不弹窗**那条约束的承重件：
没有了邮件域排除，只剩邮件 tab 的情况会被当成"有页可用"，`launch=False` 的分支就走不到。

`_find_page` 的邮件域排除**已有 4 条用例覆盖**（`tests/test_banweb.py` 的
`test_find_page_never_returns_a_mail_tab` / `..._a_mail_login_relay_tab` /
`test_find_page_prefers_aims_page_over_mail_tab` / `..._still_falls_back_to_unknown_blank_page`），
不需要补。**这些用例连同实现一起保留。**

真正该删的是 `_find_mail_page` / `_ensure_mail_page` —— 它们只服务邮件客户端，
改走 IMAP 后没有任何调用方。

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

> **本节状态（2026-09-21 更新，逐条见下）**：已在真实 Gmail 账号上跑过一轮，
> 共**两个一次性脚本**（A 放在 `tools/` 下但**从未被 git 跟踪**；B 在 `/tmp`；
> 两者跑完都已删除、**都没有进版本库**）：
> - **脚本 A** `tools/probe_gmail_imap.py` —— 登录、`select()` 形状、`SINCE` 口径、
>   `BODY.PEEK` 的 `FLAGS` 比对、`Date:`/`INTERNALDATE`、单封 `RFC822.SIZE`（§10.1/3/5/6/8/9）。
> - **脚本 B** `/tmp/probe_from_domains.py` —— **§10.2 闸门结论的唯一来源**。
>   它只打印最新 20 封的 `From` 域与 `Return-Path` 域**分布**（**只打印域**，
>   不打印地址、主题或正文），全程 `readonly=True` + `BODY.PEEK`。其源码不在本仓，亦未随本文件归档。
>
> 标「实测（2026-09-21）」的是实测值；标「**未实测**」的仍是假设，实施时仍须先验。
> 逐字原始输出是**会话内的存档**（scratch 目录下的 `task-1-measurements.md`，
> **被 `.gitignore` 忽略、不在本仓库里，按流程最终会被删除**）—— 本节引用它只为交代来源，
> **读者不要到仓库里去找它**。
> **下方「若…则」的应对方案（如 §10.2 的降级分组），未触发的不启用、只作后备保留。**

1. **应用专用密码能否登录 `imap.gmail.com:993`** —— 一切的前提
   - **实测（2026-09-21）**：**登录成功** ⇒ 应用专用密码 + `IMAP4_SSL(993)` 认证端到端可用。
2. **转发件保不保留原始 `From`** —— 课程分组完全靠它。M365 对外自动转发**通常**保留，
   但**原先未实测**（**现已实测，见下**）。若不保留（例如变成"转发人代表原发件人"），分组逻辑要改，
   §4 的 `sender` 语义也要重定
   - **实测（2026-09-21）—— 闸门通过：原始 `From` 被保留，没有被改写。**
     在最新 20 封（UID 6152..6171）中，`From` 域出现 **6 个互不相同的域**，其中含学校域
     `cityu.edu.hk`（3 封）；**没有任何一封的 `From` 域被改写成用户自己的 Gmail 域**
     （运行里账号后缀是 `gmail.com`，而它一次都没出现在 `From` 域里）。
   - **因此结论是**：**保留原始 `From`、课程分组照旧、§4 的 `sender` 语义不变，继续 Task 2。**
     §11 里「若被改写则改用 `Subject`/正文启发式分组」那条降级方案**不启用**，仅作后备保留。
   - **为什么改用「最新 N 封的域分布」而不是「最新一封的域」**：脚本 A 看的是「最新一封」，
     而那一封恰好是 **Google 自己发的安全提醒**（由「用户刚成功创建应用专用密码」这一动作触发，
     `From` 天然是 `accounts.google.com`）—— 它与「转发是否保留原始 `From`」**无关**，
     **那次测量因此失效**。改用分布口径：单一封次既容易被无关新邮件污染，一封也不足以断言转发行为。
   - **转发指纹（支持性旁证，不是判据）**：`cityu.edu.hk` / `my.cityu.edu.hk`、
     `instructure.com` / `portland.onmicrosoft.com` 这两对里，可见的 `From` 是原始发件人、
     `Return-Path`（信封发件人）是转发方 —— 形态上与「转发了、但原始 `From` 被保留」相符。
   - ⚠️ **但这**不**是转发判据**：源判读说的是「直投邮件两者**通常**同域」，**通常 ≠ 一律** ——
     **本样本里就有反例**：`redditmail.com` / `amazonses.com`（经 ESP 直投）、
     `pixiv.net` / `email.pixiv.net`、`accounts.google.com` / `gaia.bounces.google.com`（同组织子域）。
     20 行里两域不同的有 14 行，其中多数与学校转发无关。所以「`From` 域 ≠ `Return-Path` 域」
     **不能**证明一封邮件是转发来的。
   - **真正承重的是分布**：闸门判据是「`From` 有没有被改写成 Gmail 域」，由「6 个互不相同的 `From` 域、
     无一是 Gmail 域」这个**分布**支撑，**不依赖**本指纹。
   - **残余不确定性（如实标注，别写成绝对）**：样本中 3 封 `From` 为 `cityu.edu.hk`，
     可确证「没有被改写成 Gmail 域」；但**仅凭域**无法逐一证明每一封都是经转发抵达
     （理论上学校也可能直投该地址）。**闸门判据（是否被改写）不受此影响**，故判通过。
3. **`SINCE` 在 Gmail 上的时区口径** —— 决定 §5.3 放宽一天是否够
   - **实测（2026-09-21）**：UTC 口径与本地口径各搜一次，**都是 32 封**。
     但同数的原因**不是运气**：两次查询算出的日期字符串**就是同一个**（运行里两处都打印
     `20-Sep-2026`），所以这**没有构成一次有效对照** —— `SINCE` 的时区口径**仍未被区分**。
   - ⇒ 结论不变：`SINCE` **比的是 `INTERNALDATE`（邮件落进 Gmail 的时刻），不是 `Date:` 头**（§5.3），
     「粗筛放宽一天」的保险**必须保留**，不能拿这次同数当依据取消它。
4. **`UID SEARCH` / `UID FETCH` 的批量上限** —— `BODY_BATCH=5` 是保守猜测
   - **未实测**：本轮只取过「最新一封的正文」与「20 封的头部」，**没有试探批量上限**。
     `BODY_BATCH=5` 仍是保守猜测，实施时按实测调整。
5. **`BODY.PEEK[]` 实测确实不置 `\Seen`** —— 协议如此，但要在真账号上验一次
   - **实测（2026-09-21）**：取正文前后各 `UID FETCH (FLAGS)` 一次，**两次响应完全相同**（`未变=True`）。
     这与协议预期一致，但**推不出「未新增 `\Seen`」** —— 该比对只在「这封邮件当前未读」时才有信息量，
     见下条的边界。结论的依据是 **RFC 3501 的协议规定，不是这次本机比对**。
   - **这条实测的边界（如实标注）**：脚本 A 只打印了「两次响应相同」这个布尔，
     **没有打印那次 `FLAGS` 的内容**（打印出来的「条数=1」是 FETCH 响应列表的元素个数，
     **不是标志的个数**）。所以它**排除不了**「该邮件本来就带 `\Seen`、本次比对空转」这种情形。
     结论之所以仍可下，是因为「`BODY.PEEK` 不置 `\Seen`」本身就是 **RFC 3501 的协议规定**（§5.2 第 2 条），
     实测只是佐证；实施时照旧以 §8 的专门用例 + 双向变异钉死为准。
6. **单封正文的常见长度** —— 校准 `MAX_BODY_CHARS`
   - **实测（2026-09-21，样本量 n=1）**：最新一封的**整封** `RFC822.SIZE` = **12358 字节**
     （用整封大小近似正文体积，**没有单独下载正文**）。
     **只取了 1 封，只是量级参考，不构成分布证据** —— 条目问的是「*常见*正文体积」，n=1 答不了。
     就本样本而言（整封 ≥ 正文 ⇒ 正文必然更小）`MAX_BODY_CHARS = 200_000` **无需调整**；
     真实分布待实施时按实测再校准。
   - ⚠️ **别把 `5935` 当大小**：脚本 A 那行打印的是 `b'5935 (UID 6171 RFC822.SIZE 12358)'`，
     **行首的 `5935` 是消息序号**（该邮箱共 5935 封，最新一封序号自然就是 5935），
     **`RFC822.SIZE 12358` 里的 `12358` 才是字节数。**
7. **Gmail IMAP 有无速率限制** —— 大批量 `UID FETCH` 会不会被限流
   - **未实测**：本轮没有任何一次大批量取件，**速率限制仍是未知**。
     实施时按 §11 的对策走（批次 + `_THROTTLE`；被限流则明确报错，不静默重试）。
8. **`INTERNALDATE` 与 `Date:` 头差多少** —— 转发件上这两者的差值决定 §5.3 的分工是否要调整。
   最坏情况：`Date:` 被改写成转发时刻，那「原始发信时间」根本拿不到，§4 的 `received` 语义要重新定义
   - **实测（2026-09-21）**：`Date:` 头 = `2026-09-21T13:28:29+00:00`（**带时区**）；
     `INTERNALDATE` = `21-Sep-2026 13:28:30 +0000`。本样本上两者相差 1 秒。
   - **`parsedate_to_datetime` 能不能解 `INTERNALDATE`？—— 能。**
     **本轮的原始输出里没有这一项**（脚本 A 只打印了 `INTERNALDATE` 的值，没测过解析），
     我原先照抄 measurements 判读表写成「不能用 `parsedate_to_datetime` 解析」，
     **那句是错的，已在修复轮 1 更正。** 离线复验（项目自己的解释器 `.venv/bin/python`，3.11.5）：
     `parsedate_to_datetime("21-Sep-2026 13:28:30 +0000")` → `2026-09-21T13:28:30+00:00`；
     `"… +0800"` → `+08:00`，**时区偏移被正确带出**；`"… GMT"`、`" 1-Jan-2026 00:00:00 +0000"` 亦成功。
     机制：IMAP 的 `dd-Mon-yyyy HH:MM:SS ±ZZZZ` 恰好落在 `email.utils._parsedate_tz` 刻意兼容的
     **废弃 RFC 850 分支**（`len(data) == 3` 时把 `21-Sep-2026` 拆成 3 个 token 再补齐）。
   - **唯一会失败的情形是「没有时区偏移」**：`"21-Sep-2026 13:28:30"` →
     `ValueError: Invalid date value or format`。而 RFC 3501 规定 `INTERNALDATE` **恒带偏移**，
     故这个情形在实践中不触发。⇒ 形态上它确实不是 RFC 5322（是 RFC 3501 自己的 date-time），
     **但由「不是 RFC 5322」推不出「不能解析」**。
   - **实施口径**：直接用 `parsedate_to_datetime` 即可，**不必**手写解析器
     （这也意味着 `Date:` 头与 `INTERNALDATE` **可以共用一个解析器**，见 §7 的 `_parse_ts`）。
     若嫌依赖那个废弃分支，也可按 `%d-%b-%Y %H:%M:%S %z` 显式解析 —— **那是可选的洁癖，不是必需**。
   - **仍未证的部分（如实标注）**：本样本是**直投件**（Google 自己发的，见第 2 条那次失效测量），
     不是转发件；**「转发件上 `Date:` 会不会被改写成转发时刻、两者差多少」本轮没有单独测**，
     那个最坏情况**尚未证伪**。§5.3 的分工（按 `INTERNALDATE` 筛选、`received` 取 `Date:` 头、
     解不出退回 `INTERNALDATE`）照旧，其中 fail-open 的保险必须保留。
9. **`imaplib` 怎么暴露 `UIDVALIDITY`** —— §5.5 的「待确认」框；实读一次 `select()` 的响应，
   把真实形状写回本文件
   - **实测（2026-09-21）**：`select()` 返回 `OK`；相关 untagged 键为
     `['EXISTS', 'FLAGS', 'UIDVALIDITY']`；`conn.untagged_responses["UIDVALIDITY"]`
     的**原样值**是 `[b'1']` ⇒ **值 = 1，形态 = 单元素 bytes 列表**。
     解析必须按 `int(resp["UIDVALIDITY"][0])`，**不可假定它是 `int` 或 `str`**。
     真实形状**已写回 §5.5**（该框已从「待确认」改为「实测」）。

---

## 11. 风险与对策

| 风险 | 对策 |
|---|---|
| 转发件 `From` 被改写 → 课程分组失效 | **实测（2026-09-21，§10.2）已排除**：最新 20 封中原始 `From` 被保留、含学校域 `cityu.edu.hk`，无一封被改写成 Gmail 域。回退方案（按 `Subject`/正文启发式分组）**不启用**，仅作后备保留 |
| `BODY.PEEK[]` 连附件一起拉，内存与带宽放大 | `BODY_BATCH=5` + `MAX_BODY_CHARS`；`BODYSTRUCTURE` 分段取登记为后续优化 |
| Gmail 速率限制 | 批次 + `_THROTTLE`；被限流则明确报错，不静默重试 |
| 应用专用密码失效（改 Google 密码会作废） | 认证失败文案直接指向设置页重填；**不重试** |
| 转发中断（学校侧规则被关） | 症状是"邮件不再更新"，非报错。前端显示最近一封的时间即可自查；不新增后台检测（旧 spec §9 禁轮询） |
| 邮箱重建导致 UID 错位 | §5.5 的 UIDVALIDITY 核对，不一致即报错 |
| 用户误以为 Gmail 里有全部历史邮件 | 旧非目标「不做全文存档」照旧；§0 已记「转发不回溯」，需在设置页/UI 有对应说明 |

---

## 12. 落地顺序

1. **先跑 §10 的实测**（尤其 1 与 2）—— 不验就写代码等于赌

   **顺序上的坑（写计划时发现）**：实测需要钥匙串里有应用专用密码，而「在设置页填」
   是第 6 步才有的东西。解法：**用户自己**在终端跑一条
   `security add-generic-password -s cityu_gmail_imap -a <Gmail地址> -w` 直接写进钥匙串。
   信任边界与设置页完全相同（都是钥匙串），且**密码同样不经过对话/文件/日志**。
   不要在设置页做完之前就把实测卡住。
2. `credentials.py` 参数化 + Gmail 四个函数 + 端点（钥匙串这层可独立测）
3. `mail_client.py` 重写为 IMAP 取信层（纯函数先行：头部解码、MIME、日期过滤）
4. 接上 I/O 层：连接、只读三件套、批量取件、UIDVALIDITY
5. 删除清单（§7）逐项清掉，含 `banweb.py` 的邮件残留
6. 前端：`is_read` 移除 + 设置页 Gmail 组 + zh/en i18n
7. `tools/check_mail_frontend.mjs` 与 `tests/test_mail_client.py` 收尾

第 2、3 步做完就该能端到端跑通「列出邮件」——**先验证取信层，再碰 UI**。
