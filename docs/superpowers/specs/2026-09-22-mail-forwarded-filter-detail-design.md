# 学校邮箱：只显示转发件 + 邮件详情（正文/链接/附件）设计

**状态**：待实施（2026-09-22 设计，判据已实测一轮，§2）

**与 `2026-09-21-gmail-imap-mail-design.md` 的关系**：取信层（IMAP 只读三件套、窗口、排序、
UIDVALIDITY 核对）全部沿用；那份的 §4「对外契约」**键集合与类型不变**，本文只**改写其语义**
（`list_mail` 的返回集合从「窗口内全部邮件」收窄为「窗口内的转发件」），依据是 2026-09-22 的新需求。

### 对前作明文的改动（逐条）

| 前作原文 | 处置 | 依据 |
|---|---|---|
| `2026-09-21-…-design.md:66`「**不做附件下载**（沿用旧非目标）、不做多账号」 | **推翻前半条** | 用户 2026-09-22 明确要求「点开后查看详情（包括里面的**文件和附件**）」。**「不做多账号」照旧保留。** |
| `2026-09-21-…-design.md:65`「**不做按 Gmail 标签筛选**：只读 `INBOX`」 | **保留** | 本文判据在服务端即可判定，不需要标签（§2.2） |
| `2026-09-21-…-design.md` §4 契约表 | **加注** | 键集合与类型不变；**语义收窄**（§3.1） |
| `2026-09-18-school-mail-design.md:29`「不做附件下载」 | **推翻** | 同上（该非目标一路沿用到 09-21，本次一并推翻） |

---

## 1. 目标与非目标

**目标**
1. 邮件列表**只显示转发过来的邮件**（判据见 §2），不再混入私人订阅等噪音。
2. 列表里**点开一条**可看详情：主题/发件人/时间、正文、正文里的链接、附件列表；附件可**单个下载**落盘。

**非目标（明确不做）**
- 不收发、不删除、不移动、不标记已读（只读三件套照旧）。
- 不做附件预览、不做内嵌图渲染、不做正文 HTML 原样排版。
- 不加「显示全部邮件」开关（2026-09-22 裁定：方案甲）。
- 不做多账号；不做按 Gmail 标签筛选。
- 不改 `list_mail` / `fetch_bodies` 的**键集合与类型**。
- 不在 Gmail 侧建过滤器或标签；不新增任何凭据。
- 不自动批量落盘附件；正文里的链接不代下载。

---

## 2. 判据

### 2.1 定义（严格）

```
地址域名 = 地址中**最后一个 `@` 之后**的部分，去掉两端空白与 <>
命中     ⇔ 域名 == "cityu.edu.hk" 或以 ".cityu.edu.hk" 结尾（大小写不敏感）
本判据   ⇔ To 或 Cc 中**存在任一地址命中**
```

**明确禁止子串匹配。** 子串式（在整条头里找 `cityu.edu.hk`）会误判两类输入：

- `a@evil-cityu.edu.hk` —— 域名的 `-` 前缀让它看起来像子域；
- `a@cityu.edu.hk.evil.com` —— 学校域出现在**攻击者控制的**域名中间。

两者都必须判**不**命中，并各有对应用例（§7）。

**地址怎么从头部取出来**（不许含糊）：候选实现是用 `email.utils.getaddresses` 解析
`To`/`Cc` 的全部取值，拿到地址列表后再对**每个地址**取最后一个 `@` 之后的部分。
⚠️ **`getaddresses` 对本邮箱真实头部的行为属于「标准库能不能吃这个输入」那类断言**——
中文显示名、多地址、显示名里带逗号或 `@` 这几种形状**必须先实跑一次确认**，
不得凭印象写进实现（本项目已因「凭空断言标准库行为」踩过坑）。

### 2.2 为什么是 To/Cc

| 候选头 | 处置 | 理由 |
|---|---|---|
| `To`、`Cc` | **采用** | 实测命中 7 封，且负例零假阳性 |
| `Return-Path` | **不采用**（留作后备，§9） | 实测对 7 封**零增量**（它命中的 4 封是 To 命中 7 封的子集）；且它等于学校地址时，会把「用户自己用学校地址发出去的信」误判成转发件 |
| `Delivered-To` / `X-Original-To` / `X-Forwarded-To` / `X-Forwarded-For` / `Envelope-To` / `Apparently-To` | **不采用** | 实测全为 0 命中 |
| Gmail 侧过滤器/标签 | **不做** | 服务端已可判定；引入它要用户手工配一次 Gmail，且取信层得改成读 label（与前作 `:65` 同向） |

### 2.3 实测记录（2026-09-22，只读探测，只打印计数与域名）

配置账号域：`gmail.com`。窗口：`INBOX` 近 10 天，共 **94 封**。

- 各头单独命中学校域的信数：`To` **7**、`Return-Path` **4**、其余候选头**全 0**。
- 交叉表（From 是否学校域 × 是否有收件人信号）：
  `From 学校域=True, 有信号=True` → **4**；`From 学校域=False, 有信号=True` → **3**（全是
  `instructure.com`，即学校 Canvas 的系统通知）；`False/False` → **87**。
- **负例对照**（会被滤掉的 87 封）From 域 top10：`steampowered.com` 18、`redditmail.com` 10、
  `google.com` 10、`news.statista.com` 9、`accounts.google.com` 9、`pixiv.net` 5、`github.com` 3、
  `marketing.octopus.com.hk` 3、`ko-fi.com` 3、`tencent.com` 2 —— **零假阳性**。
- 同一轮另测：`[Gmail]/All Mail` 与 `INBOX` 的域分布**逐域相同**（无标签把信挡在收件箱外）；
  `[Gmail]/Spam` 2 封、无学校件；`Trash` 0。4 封学校件的到达时刻：3 封 09-21、1 封 09-22（均在 7 天窗内）。

> **仪器更正（必须随判据一起读）**：上述探测的匹配用的是**子串**式，比 §2.1 的严格定义**宽**。
> 观测到的域名里不存在 §2.1 列出的两种误判形状，因此「7 封」**预期不变 —— 但这是推断，不是实测**。
> 实施第一步用**严格判据**复测（§10）。**不得**带着未复测的判据往下做。

---

## 3. 取头与过滤（`backend/mail_client.py`）

1. **取头**：`_fetch_headers`（现 `:356`）的
   `BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)]` → 加 `TO CC`。
   **同一条 FETCH 多两个词，零额外往返**（`To` 不取就没有判据）。
2. **判据函数**（新，纯函数、可单测）：
   ```python
   SCHOOL_DOMAIN = "cityu.edu.hk"
   def recipient_is_school(msg) -> bool
   ```
   实现口径见 §2.1；docstring 必须写明「不是子串匹配」与那两条反例。
3. **内部键**：`header_item`（现 `:141`）增 `_school: bool`，与既有 `_internaldate` 同族。
4. **`_do_list`（现 `:379`）顺序**：`select_scope` → 按 `_school` 过滤 → `sort_newest_first` → 剥键。
5. **剥键改白名单**：现 `:400` 是黑名单（`k != "_internaldate"`）—— 本次新增内部键后它会**静默泄漏**。
   改为
   ```python
   _CONTRACT_KEYS = ("id", "subject", "sender", "received")
   return [{k: it[k] for k in _CONTRACT_KEYS} for it in kept]
   ```
   日后再加内部键也不会漏进对外契约（结构性保证，替代逐键排除）。
6. **空结果**：返回 `[]`，**不是错误**（前端文案见 §6）。

### 3.1 契约影响（沿用本仓「保留 / 改写 / 删除」惯例）

| 项 | 处置 | 说明 |
|---|---|---|
| `list_mail(days) -> list[dict]` | **保留形状，改写语义** | 键集合、类型、`id` 可被 `fetch_bodies` 认出的性质**全不变**；返回**集合**收窄为转发件 |
| `fetch_bodies(items) -> {id: body}` | 保留 | 调用方传的 `items` 来自列表，天然已是子集，无需改动 |
| `main.py` `/api/mail/list` 响应 `{ok,count,mails}` | 保留 | 形状不变；`count` 语义随之变成「转发件数」 |
| 前端既有 65 处引用 | 保留 | 无一处依赖「返回全部」 |

---

## 4. 详情与附件（`backend/mail_client.py`）

```python
def fetch_detail(item: dict) -> dict
    # {"id": str, "body": str,
    #  "attachments": [{"index": int, "name": str, "size": int | None}]}

def fetch_attachment(item: dict, index: int) -> tuple[str, bytes]
    # 返回 (净化后的文件名, 原始字节)
```

- **index 承重不变式**：`index` = `msg.walk()` 中**满足附件条件**的序号；`fetch_detail` 与
  `fetch_attachment` 用**同一套判定、同一遍历顺序**重新解析。因为**不缓存字节**（见本节末），
  index 必须可复现 —— 必须有用例钉住「同一封解析两次 index 相同」与「detail 给的 index 拿得回同名附件」。
- **附件条件**：部件有 `Content-Disposition: attachment`（沿用 `message_body` 现 `:193` 的口径）。
  `inline` 部件（内嵌图）**不算附件、不进正文** —— v1 不显示。
- **name 解码**：RFC 2047 编码字（`decode_mime_header` 已存在）+ RFC 2231（`filename*=UTF-8''…`）。
  后者**无现成实现**，依赖 `email` 的 `get_filename()`；其覆盖情况**未实测**（§9），实施时先跑一次确认。
- **name 净化**：去掉 `/` 与 `\` 与 NUL；去掉首尾空白与点；去掉 `..` 段；净化后为空 → `attachment-<index>`；
  截断到 200 字符。
- **size**：`len(解码后字节)`；解不出给 **`None`**，**绝不编造 0**（与 `subject`「解不出就原样给」同口径）。
- **上限**：`MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024`（**保守取值、非实测**；Gmail 单封上限 25 MB，留余量）。
  超限抛 `MailError`，文案含「超过 20 MB」。
- **body**：复用 `message_body()`（已跳过 attachment、HTML→纯文本），套 `MAX_BODY_CHARS`。
- **只读**：`BODY.PEEK[]`；UIDVALIDITY 核对**复用** `_do_bodies`（现 `:444`）比对 `_uidvalidity` 的机制，
  不符则抛「邮箱已被重建…请重新点刷新邮件」。
- **不缓存**：进程内不驻留邮件字节、不加新状态。代价：下载 N 个附件 = N 次连接（受 `_THROTTLE` 节流）。
  **被否决的替代方案**：打开详情时把全部附件取回并按 uid 缓存在进程里 —— 引入服务器状态与内存风险，不值得。

---

## 5. 路由（`backend/main.py`）

| 路由 | 请求体 | 成功 | 失败 |
|---|---|---|---|
| `POST /api/mail/detail` | `{id: str}` | `{ok: true, detail: {...}}` | `{ok: false, error: str}` |
| `POST /api/mail/attachment` | `{id: str, index: int, download_dir: str}` | `{ok: true, path: str}` | `{ok: false, error: str}` |

- **与既有 `/api/mail/*` 同形**：`try/except` → `{ok:false, error}`，**不抛 HTTP 异常**（沿用现 `:1001`/`:1016` 写法）。
- **落盘路径**：`<download_dir>/学校邮件/<YYYY-MM-DD>_<净化名>`。
  - 日期取该邮件 `received` 的 **UTC** 日期（与 `received` 同源、可复现、无时区歧义；代价见 §9）。
    `received` 解不出时用**当天 UTC** 日期。
  - `学校邮件` 是**固定字面量**，不做 i18n —— 它同时是磁盘路径，切界面语言不该挪动用户的目录。
- **同名绝不覆盖**：目标已存在则加 `-1`/`-2`… 后缀（**永不覆盖用户已有文件**）。
- **锚定**：最终路径 `resolve()` 后必须仍位于 `resolve()` 后的 `download_dir` **之内**，否则抛错
  （照抄 `files_downloader` 的「落盘锚点」纪律）。
- **请求校验**（缺失或类型不对一律 `{ok:false, error}`，不抛）：`id` 必须是非空字符串；
  `index` 必须是**非负整数**（JSON 里来的是字符串也要拒，不许隐式转换）；`download_dir` 必须是非空字符串。
- 目录不存在则创建。

---

## 6. 前端（`frontend/`）

- **列表行可点**（含键盘可达：`Enter`/`Space`）→ 打开 `mailDetail` 弹层。沿用现有 `detailModal`
  （`app.js` 现 `:336`）的范式：backdrop 点击关闭、Esc 关闭。**不删不改任何现有弹层。**
- **弹层内容**：主题、发件人、时间（`fmtLocal`）、正文、附件列表。
- **正文**：先 `esc()`（`util.js:63`）再 linkify —— **只认 `https?://`**，href 经 `escAttr()`，
  点击 `e.preventDefault()` 后走既有 `openExternal`（它自带 `/^https?:/i` 守卫）。
  **不得**把未转义的正文 HTML 直接塞进 DOM。
- **附件行**：名 + 大小（人类可读；`null` 时**留白**）+「下载」按钮 →
  `withBusy` 包裹 → `api("mail/attachment", {id, index, download_dir: downloadDir()})` →
  成功后显示「已存到 <path>」。
- **空态文案**：转发件为空 → 「最近 N 天没有转发过来的邮件」，**不显示「0 条」了事**。
- **i18n**：新增键 zh/en 双份，**且必须在 `app.js` 里真被引用**（`check_i18n_keys` 死键基线 21 只许持平或下降）。
- **DOM**：新增 id 补进 `tools/check_dom_ids.mjs`。
- **四个自检器全绿**：`check_mail_frontend` 55、`check_gmail_ui` 44、`check_dom_ids` 157、`check_i18n_keys` 419。

---

## 7. 测试（零出网，IMAP 全部替身）

**判据**
- `To` 命中；`Cc` **单独**命中；子域 `@grad.cityu.edu.hk` 命中；大小写与首尾空白。
- **假阳性两例**：`a@evil-cityu.edu.hk`、`a@cityu.edu.hk.evil.com` → **不**命中。
- 无 `To`/`Cc` → 不命中。

**过滤接线**
- `_do_list` 返回值 = 转发件子集。
- 返回值里**不含** `_school` 与 `_internaldate`（白名单断言，防内部键泄漏）。
- **FETCH 命令串里含 `TO CC`** —— 这是一条**机制断言**（与 `_do_list` 的接线断言同族），
  因为 IMAP 替身**不解析命令串**、只回固定字节，行为式断言抓不到「少要了两个头」。
  **它的局限要写在用例注释里**：它只证明命令里有这两个词，**不**证明服务端确实返回了它们
  （后者只能靠 §10 的真实探测）。少了它判据恒为 False，症状是**全部邮件都被滤掉**。

**详情与附件**
- 装配；多附件的 index 顺序；无附件 → `[]`；坏 MIME 不抛；body 截断；`size` 解不出 → `None`；超大 → `MailError`。
- **index 稳定性**：同一封解析两次 index 相同；**组合绑定**：detail 给的 index 拿去 `fetch_attachment`
  取回同名附件。
- **净化**：`../../etc/passwd`、`..\\..\\x`、`a/b.txt`、空名、超长名、含 NUL → 落点仍在 `学校邮件/` 内。

**落盘**
- 同名不覆盖（加后缀）；目录不存在则创建；**锚定**：目标解析后越出 `download_dir` 时被拒。

**只读**
- 全流程无 `STORE`/`COPY`/`MOVE`/`EXPUNGE`、无 `\Seen`（沿用既有 FLAGS 比对口径）。

**路由**（`tests/test_main.py`）
- `detail`/`attachment` 的成功与失败形状均为 `{ok: ...}`，**不抛**。

---

## 8. 全局约束（沿用，逐条不放宽）

凭据绝不落盘 / 绝不回传前端 / 绝不出现在日志、报告、提交信息里；应用专用密码由用户自己输入，**绝不经过对话**；
绝不后台轮询；认证失败即停、**绝不重试**；只读三件套（`select(readonly=True)`、全程 `BODY.PEEK`、
**永不发 `STORE`/`COPY`/`MOVE`/`EXPUNGE`**）；不调 `MarkAsRead`；不动 `DT` cookie；请求节流（`_THROTTLE`、
`BODY_BATCH = 5`）；测试零出网（`tests/conftest.py` socket 层守卫，拦截即失败）；测试命令
`.venv/bin/python -m pytest -q`；分支 `main`，**保持本地，先不推**。

---

## 9. 未验证边界与后备

| 边界 | 现状 | 后备 |
|---|---|---|
| 判据依赖 M365 转发保留 `To` | 实测成立（7/94，负例零假阳性） | 失灵症状是**列表变空**（可见，非静默）。后备：把 `Return-Path` 并入判据（一行），或退到「From 域白名单」 |
| 附件日期用 UTC | **已实测（2026-09-22）**：对 +0800 而言，UTC 日 **≤** 本地日，**永不**大于 | 落错目录的只有**凌晨**那一侧：本地 `00:00–07:59` 的信其 UTC 日**退到前一日**（`03:00+08:00` ⇒ 前一日 `19:00Z`）；`08:00–23:59` 两者同日。**初稿把方向写反了**（写的是「晚间落进次日」—— 晚间 `23:30+08:00` = `15:30Z` 仍是同日，实测）。若更看重本地日期，改成 `received` 的本地日期 |
| `MAX_ATTACHMENT_BYTES = 20 MiB` | **保守取值，非实测** | 未测真实附件大小分布；超限是明确报错，不是静默失败 |
| RFC 2231 文件名（`filename*=UTF-8''…`） | **未实测** | 依赖 `email.get_filename()`；实施时先跑一次确认，不覆盖则自行解析 |
| 正文 HTML→纯文本 | 既有行为，本次不改 | 复杂排版会丢；内嵌图不显示。若「文件」指的是正文里的附件链接，v1 只让它们**可点**，不代下载 |
| 转发件也可能落进 Spam | 本次实测 Spam **无**学校件 | 一旦发生，只读 INBOX 会看不见 —— **不在本次范围**，仅记录 |

---

## 10. 实施第一步（写任何实现之前先跑掉）

用 §2.1 的**严格判据**（域名后缀判定，**非**子串）重跑一次只读探测，核对：

1. 命中数是否仍为 **7/94**；
2. 负例对照是否仍**零命中**；
3. 顺手记录命中那几封的 `To`/`Cc` 域名形状（只打印域名）。

**只打印计数与域名，不打印地址、主题、正文。** 不符则回到 §2 重新定判据 ——
**不得**带着未复测的判据往下做。
