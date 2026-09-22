# 学校邮件「只显示转发件 + 详情/附件下载」实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 邮件列表只显示转发过来的邮件（判据：收件人是学校地址），并且点开一条能看到详情（主题/发件人/时间、正文、正文里的链接、附件列表），附件可单个下载落盘。

**Architecture:** 后端在**取头那一趟**多要 `TO CC` 两个词（零额外往返），用纯函数判据在服务端过滤，新增的内部键 `_school` 与既有的 `_internaldate` 同族、返回前按**白名单**剥掉；详情与附件各走一次只读 `BODY.PEEK[]`，不复用连接、不缓存字节；落盘锚定在 `download_dir` 内、同名加后缀绝不覆盖。前端沿用既有 `detailModal` 的弹层范式新开 `#mailDetail`，正文**逐段转义后再 linkify**。

**Tech Stack:** Python 3.11.5（`.venv`）+ FastAPI 0.141.1 + Pydantic 2.13.5 + `imaplib`/`email` 标准库；前端原生 JS（无构建，四段 `<script>`）；自检用 Node ESM 脚本 + `node:vm` DOM 替身。

**Spec:** `docs/superpowers/specs/2026-09-22-mail-forwarded-filter-detail-design.md`

## Global Constraints

（逐字照抄 spec §8；每条任务的要求都隐含包含本节，**不得放宽**）

凭据绝不落盘 / 绝不回传前端 / 绝不出现在日志、报告、提交信息里；应用专用密码由用户自己输入，**绝不经过对话**；
绝不后台轮询；认证失败即停、**绝不重试**；只读三件套（`select(readonly=True)`、全程 `BODY.PEEK`、
**永不发 `STORE`/`COPY`/`MOVE`/`EXPUNGE`**）；不调 `MarkAsRead`；不动 `DT` cookie；请求节流（`_THROTTLE`、
`BODY_BATCH = 5`）；测试零出网（`tests/conftest.py` socket 层守卫，拦截即失败）；测试命令
`.venv/bin/python -m pytest -q`；分支 `main`，**保持本地，先不推**。

补充（来自 spec §4/§5，同样是硬约束）：
- 详情与附件**不缓存**：进程内不驻留邮件字节、不加新状态。代价：下载 N 个附件 = N 次连接。
- `MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024`；超限抛 `MailError`，文案含「超过 20 MB」。
- `size` 解不出给 **`None`**，**绝不编造 0**。
- 两个新路由的失败一律 `{ok:false, error}`、**HTTP 200**，不抛 HTTP 异常。
- 落盘路径 `<download_dir>/学校邮件/<YYYY-MM-DD>_<净化名>`；`学校邮件` 是**固定字面量**、不做 i18n。

### 执行前必须先读的实测数字（本轮开始前已跑，勿再猜）

| 判据 | 实测值 | 命令 |
|---|---|---|
| pytest 基线 | **546 passed** | `.venv/bin/python -m pytest -q` |
| `check_mail_frontend.mjs` | **55** 条断言，rc=0 | `node tools/check_mail_frontend.mjs` |
| `check_gmail_ui.mjs` | **44** 条断言，rc=0 | `node tools/check_gmail_ui.mjs` |
| `check_dom_ids.mjs` | 打印「HTML 共 **157** 个 id」，rc=0 | `node tools/check_dom_ids.mjs` |
| `check_i18n_keys.mjs` | 键 **419**×2、死键 **21**（= `DEAD_BASELINE`），rc=0 | `node tools/check_i18n_keys.mjs` |

### 本计划对 spec 的两处**裁定**（已读源码核实，执行时记入 ledger）

1. **`_school` 加在 `_fetch_headers`，不加在 `header_item`。**
   spec §3.3 写「`header_item`（现 `:141`）增 `_school: bool`，与既有 `_internaldate` 同族」——
   这两半互相矛盾：`_internaldate` **不是** `header_item` 产出的，是 `_fetch_headers`
   （`backend/mail_client.py:374`）在拿到 `msg` 之后补上去的；而 `header_item` 至今只产出
   `{id, subject, sender, received}` 四个键，`tests/test_mail_client.py` 有**两条**用例逐字钉着这件事
   （`test_header_item_shape_exactly_four_keys` 的 docstring 原话：「多一个少一个都要红」）。
   按「同族」读 ⇒ 加在 `_fetch_headers`：`header_item` 的契约不动、两条既有用例一行不改，
   而 §3.5 的白名单（`_CONTRACT_KEYS`）照样把 `_school` 挡在对外契约之外。
   反向做法（改 `header_item` 的返回值）必须放宽那两条字段级白名单断言 —— 那正是本次
   §3.5 要防的「内部键泄漏」的守卫，代价更大。

2. **`tools/check_dom_ids.mjs` 不需要任何编辑。**
   spec §6 写「新增 id 补进 `tools/check_dom_ids.mjs`」。实读该脚本
   （`tools/check_dom_ids.mjs:25-50`）后确认：它**从源码推导** id 集合（扫 `frontend/*.js` 的
   `$("id")` 与 `index.html`/JS 里的 `id="..."`），**没有可维护的 id 清单**。新增 id 只会让打印的
   计数从 157 变成 **163**。本条为计划层更正，不需要动作；执行时只需核对该计数。

---

### Task 1: 用严格判据复测一次（spec §10，写任何实现之前）

**Files:**
- Create: `/tmp/mail_probe_strict.py`（**临时探针，绝不入库**；`/tmp` 在仓库之外）
- 不改任何仓库文件

**Interfaces:**
- Consumes: `backend.credentials.get_gmail()`、`backend.mail_client._connect()`、常量 `MAILBOX` / `TIMEOUT`
- Produces: 一份**只含计数与域名**的复测结论（写进报告，不入 git）

**为什么第一步是它**（spec §10 原话）：「**不得**带着未复测的判据往下做」。§2.3 的 7/94 是**子串**式
匹配量出来的，比 §2.1 的严格定义**宽**；「预期不变」是推断，不是实测。

- [ ] **Step 1: 写探针**

关键口径（照抄，别自由发挥）：
- 判据用**域名后缀**：`dom == "cityu.edu.hk" or dom.endswith(".cityu.edu.hk")`，`dom` = 地址中最后一个 `@` 之后的部分、`strip().lower()`。
- **只打印**计数、域名形状、日期（`YYYY-MM-DD`）。
- **绝不打印**地址原文、主题、正文、发件人。
- 只读三件套：`_connect()`（内部已 `EXAMINE`）、`UID SEARCH SINCE`、`UID FETCH (BODY.PEEK[HEADER.FIELDS (TO CC)] INTERNALDATE)`。
- `SINCE` 的月份**不要**用 `strftime("%b")`（locale 相关，会静默搜错日期）：用
  `["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]` 手写映射。
- 带空格/方括号的邮箱名要加引号（本步只用 `INBOX`，但别顺手扩到 All Mail）。
- 认证失败即停，**不重试**，不打印任何凭据。

```python
"""一次性只读复测：严格域名后缀判据下，INBOX 命中几封、负例是否零命中。只打印计数与域名。"""
import sys
from collections import Counter

sys.path.insert(0, "/Users/nothing/School_Calendar")
from email import message_from_bytes                       # noqa: E402
from email.utils import getaddresses                      # noqa: E402
from datetime import datetime, timedelta, timezone         # noqa: E402

from backend import mail_client                            # noqa: E402

MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DOMAIN = "cityu.edu.hk"
DAYS = 10


def dom_of(addr):
    return addr.rsplit("@", 1)[-1].strip().lower() if "@" in addr else ""


def hit(msg):
    for field in ("To", "Cc"):
        for _n, a in getaddresses(msg.get_all(field) or []):
            d = dom_of(a)
            if d == DOMAIN or d.endswith("." + DOMAIN):
                return True
    return False


conn, _uidv = mail_client._connect()
try:
    d = datetime.now(timezone.utc) - timedelta(days=DAYS + 1)
    since = f"{d.day:02d}-{MON[d.month - 1]}-{d.year}"
    typ, data = conn.uid("SEARCH", None, "SINCE", since)
    assert typ == "OK", typ
    uids = (data[0] or b"").split()
    total, hits, shapes = len(uids), 0, []
    neg = Counter()
    for i in range(0, len(uids), 50):
        typ, chunk = conn.uid(
            "FETCH", b",".join(uids[i:i + 50]),
            "(BODY.PEEK[HEADER.FIELDS (TO CC)] INTERNALDATE)")
        assert typ == "OK", typ
        for entry in chunk:
            if not isinstance(entry, tuple) or len(entry) < 2:
                continue
            msg = message_from_bytes(entry[1])
            if hit(msg):
                hits += 1
                shapes.append(tuple(sorted({
                    dom_of(a) for f in ("To", "Cc")
                    for _n, a in getaddresses(msg.get_all(f) or []) if dom_of(a)})))
            else:
                for f in ("To", "Cc"):
                    for _n, a in getaddresses(msg.get_all(f) or []):
                        if dom_of(a):
                            neg[dom_of(a)] += 1
    print(f"窗口: INBOX 近 {DAYS} 天  总数={total}  命中={hits}")
    print("命中件的 To/Cc 域名形状（去重后）:")
    for s in sorted(set(shapes)):
        print("   ", " | ".join(s))
    print("未命中件的收件人域名 top10（应全部为非学校域）:")
    for k, v in neg.most_common(10):
        print(f"    {k}  {v}")
    print("负例里是否出现学校域:", [k for k in neg if k == DOMAIN or k.endswith("." + DOMAIN)])
finally:
    conn.logout()
```

- [ ] **Step 2: 跑它**

Run: `.venv/bin/python /tmp/mail_probe_strict.py`
Expected: 打印四段，**没有**任何地址/主题/正文。这是唯一真连一次 Gmail 的步骤（只读 EXAMINE + PEEK）。

- [ ] **Step 3: 判定（判据写在下面，别临场放宽）**

| 观察项 | 硬判据（不符 ⇒ **停**） | 软判据（会漂，如实记录） |
|---|---|---|
| 负例一侧 | 「负例里是否出现学校域」必须是 **`[]`** | — |
| 命中件形状 | 每一封命中的，其 To/Cc 域名**全部**以 `cityu.edu.hk` 结尾 | — |
| 命中数 | 必须 **> 0**（= 0 说明转发件没保住 `To`，症状是列表变空） | 2026-09-22 的快照是 **7/94**；总数与命中数随新邮件到达而变，**按实测如实记录** |
| 未命中件域名 top10 | 不得出现学校域 | 与 §2.3 记录的噪声域大体一致即可 |

**若硬判据不符**：停下来，把原始输出交给控制者（**不要**自行改判据、不要带着未复测的判据往下做）。
后备见 spec §9 第 1 行（把 `Return-Path` 并入判据，或退到 From 域白名单）。

- [ ] **Step 4: 记录并删除探针**

把上面四段输出**原样**贴进报告（它只含计数与域名，不含隐私）。
Run: `rm -f /tmp/mail_probe_strict.py`
Run: `[ -e /tmp/mail_probe_strict.py ] && echo "仍在" || echo "已删除"`
Expected: `已删除`（**必须看到这行**——拒绝回执不作数，要回文件系统核）

- [ ] **Step 5: 不入库，无提交**

本任务**刻意没有 commit**：探针在 `/tmp`，仓库一行未动。
Run: `git status --porcelain`
Expected: 空输出

---

### Task 2: 判据纯函数 `recipient_is_school`

**Files:**
- Modify: `backend/mail_client.py`（常量区 `:40-48` 之后；函数插在 `sender_name` `:79` 之后）
- Test: `tests/test_mail_client.py`（新增一节，放在 `_msg` 助手 `:429` 之后）

**Interfaces:**
- Consumes: 无（纯函数，输入是 `email.message.Message`）
- Produces: `SCHOOL_DOMAIN: str = "cityu.edu.hk"`、`recipient_is_school(msg) -> bool`

- [ ] **Step 1: 写失败用例**

先加助手（`_msg` 附近）：

```python
def _recip(*tos, cc=()):
    """只带 To/Cc 的邮件 —— 判据的全部输入。"""
    m = EmailMessage()
    if tos:
        m["To"] = ", ".join(tos)
    if cc:
        m["Cc"] = ", ".join(cc)
    return m
```

再写用例（全部新加，一节）：

```python
# ============ 判据：收件人是学校地址（spec §2.1）============
# 判据是**域名后缀判定**，不是子串。两条反例各自成用例。

def test_recipient_is_school_accepts_exact_and_subdomain():
    assert mail_client.recipient_is_school(_recip("a@cityu.edu.hk"))
    assert mail_client.recipient_is_school(_recip("a@staff.cityu.edu.hk"))
    assert mail_client.recipient_is_school(_recip("a@grad.cityu.edu.hk"))


def test_recipient_is_school_is_case_and_space_insensitive():
    assert mail_client.recipient_is_school(_recip("A@CityU.EDU.HK"))
    assert mail_client.recipient_is_school(_recip("  a@Cityu.Edu.HK  "))


def test_recipient_is_school_rejects_dash_prefix_lookalike():
    """子串匹配会误判的第一类：`evil-cityu.edu.hk` 的域名前缀带 `-`。"""
    assert not mail_client.recipient_is_school(_recip("a@evil-cityu.edu.hk"))


def test_recipient_is_school_rejects_school_domain_in_the_middle():
    """子串匹配会误判的第二类：学校域出现在**攻击者控制的**域名中间。"""
    assert not mail_client.recipient_is_school(_recip("a@cityu.edu.hk.evil.com"))


def test_recipient_is_school_reads_cc_too():
    """To 不命中、Cc 命中 ⇒ 命中（判据是 To **或** Cc）。"""
    assert mail_client.recipient_is_school(_recip("a@example.invalid", cc=("b@cityu.edu.hk",)))


def test_recipient_is_school_false_without_recipient_headers():
    """`get_all` 取不到头时返回 None —— 不兜底就是 TypeError。"""
    assert not mail_client.recipient_is_school(_msg())
    assert not mail_client.recipient_is_school(_recip(""))


def test_recipient_is_school_reads_all_repeated_to_headers():
    """`To` 可以出现多次，**只读第一条会漏**（`get` 只给第一条，`get_all` 才拿得全）。

    用原始字节造：`EmailMessage` 把 To 当唯一头，第二次赋值直接 ValueError
    （实测确认过；这正是「标准库能不能吃这个输入」那类断言，不能凭印象写）。
    下面的顺序刻意让「只读第一条」判成 False —— 这才是能揪出实现的判别力。
    """
    m = email.message_from_string("To: a@example.invalid\r\nTo: b@cityu.edu.hk\r\n\r\n")
    assert mail_client.recipient_is_school(m)


def test_recipient_is_school_handles_display_name_with_at():
    """显示名里可以有 `@` —— 域名要取**地址那一半**的最后一个 `@`。"""
    assert mail_client.recipient_is_school(_recip('"a@b" <x@cityu.edu.hk>'))


def test_recipient_is_school_survives_empty_address_element():
    """`Team <>` 解析出一个**空地址** —— 取域名不能抛。"""
    assert not mail_client.recipient_is_school(_recip("Team <>"))
    assert mail_client.recipient_is_school(_recip("Team <>, real@cityu.edu.hk"))
```

- [ ] **Step 2: 跑一遍确认它红**

Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q -k recipient_is_school`
Expected: FAIL —— `AttributeError: module 'backend.mail_client' has no attribute 'recipient_is_school'`

- [ ] **Step 3: 实现**

常量区（`MAX_BODY_CHARS` 之后）加：

```python
#: 判据用的学校域名（spec §2.1）。只此一处定义 —— 实现与用例都从它取。
SCHOOL_DOMAIN = "cityu.edu.hk"
```

导入行 `:34` 改成：

```python
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
```

函数插在 `sender_name` 之后：

```python
def _addr_domain(addr: str) -> str:
    """地址的域名 = **最后一个 `@` 之后**的部分（去空白、小写化）。

    取最后一个 `@`：`getaddresses` 交回来的 `addr` 只是地址那一半，
    而它是可以含 `@` 的（`"a@b" <x@cityu.edu.hk>` 实测形状）——
    按第一个 `@` 切会得到 `b" ...`。
    没有 `@` 的（空串、`Team <>` 留下的空地址、垃圾串）返回空串，**绝不抛**。
    """
    return addr.rsplit("@", 1)[-1].strip().lower() if "@" in addr else ""


def recipient_is_school(msg) -> bool:
    """这封是不是转发过来的 —— To 或 Cc 中**存在任一地址命中**（spec §2.1）。

    命中 ⇔ 域名 == `SCHOOL_DOMAIN`，或以 `"." + SCHOOL_DOMAIN` 结尾（大小写不敏感）。

    **明确禁止子串匹配。** 在整条头里找 `cityu.edu.hk` 会误判两类输入，各有对应用例：
      - `a@evil-cityu.edu.hk`（前缀 `-` 让它看起来像子域）；
      - `a@cityu.edu.hk.evil.com`（学校域出现在**攻击者控制的**域名中间）。

    `To`/`Cc` 都可能缺失（`get_all` 返回 `None`）、也可能出现多次 —— 故 `or []` 兜底、
    且必须用 `get_all`（`get` 只给第一条，只读第一条会漏）。
    """
    for field in ("To", "Cc"):
        for _name, addr in getaddresses(msg.get_all(field) or []):
            dom = _addr_domain(addr)
            if dom == SCHOOL_DOMAIN or dom.endswith("." + SCHOOL_DOMAIN):
                return True
    return False
```

- [ ] **Step 4: 跑一遍确认它绿**

Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q -k recipient_is_school`
Expected: PASS（9 条）
Run: `.venv/bin/python -m pytest -q`
Expected: 546 + 9 passed，**零 fail**

- [ ] **Step 5: 提交**

```bash
git add backend/mail_client.py tests/test_mail_client.py
git commit -m "feat(mail): 转发件判据 recipient_is_school（域名后缀判定，非子串）"
```

---

### Task 3: 接线 —— 多要 TO CC、打 `_school`、按白名单过滤与剥键

**Files:**
- Modify: `backend/mail_client.py`（模块 docstring `:1-25`；常量区；`_fetch_headers` `:347-376`；`_do_list` `:379-402`）
- Test: `tests/test_mail_client.py`（`_hdr` `:94`；`test_list_mail_uses_internaldate_when_date_header_missing` `:202`；新增 3 条）

**Interfaces:**
- Consumes: `recipient_is_school(msg) -> bool`（Task 2）
- Produces: `_CONTRACT_KEYS = ("id", "subject", "sender", "received")`；`_fetch_headers` 产出的条目多两个内部键 `_internaldate`、`_school`；`list_mail(days)` 的**形状不变**、**语义收窄为转发件**

**既有用例为什么会被牵连**：判据要 `To`/`Cc`，而 `_hdr()`（`:94`）今天一个都没有 ⇒
**每一封都被判为「不是转发件」** ⇒ `list_mail` 恒返回 `[]`。走 `_hdr()` 的那些用例由 Step 1
的默认值一并救回（它们的语义本来就是「一封转发过来的学校邮件」）；**只有
`test_list_mail_uses_internaldate_when_date_header_missing`（`:202`）自造邮件**，必须单独补。

其中 `test_do_list_applies_select_scope`（`:222`）**单独点名**：它的 spy **记录的是过滤之前**
交给 `select_scope` 的条目（执行顺序是 `select_scope` → 过滤）。缺 `To` 时那两条会被判据滤光，
但**红的是更前面的键形状断言**（那些条目带着 `_internaldate`，Task 3 之后还带着 `_school`），
`[it["id"] for it in out] == ["1","2"]` 那一行**根本走不到**。

⚠️ 这条是**实跑纠正过的**：计划原先写「它会以 `[] == ["1","2"]` 的形态红」，Task 3 实作者实跑后
指出按那句症状去定位会落到一条**从未执行**的断言上。机制（判据把信滤光）没变，**救法也没变** ——
仍在 Step 1 补 fixture，不是改这条用例的断言。

- [ ] **Step 1: 先修 fixture，让既有用例回到「转发件」语义**

`_hdr`（`:94`）加 `to` 参数并给**默认值是学校地址** —— 既有 I/O 用例讲的本来就是「一封转发过来的学校邮件」，
这样它们一行断言都不用改：

```python
def _hdr(subject="S", sender="T <t@cityu.edu.hk>", date="Thu, 17 Sep 2026 16:31:00 +0800",
         to="t@cityu.edu.hk"):
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = sender
    m["Date"] = date
    m["To"] = to
    return m.as_bytes()
```

`test_list_mail_uses_internaldate_when_date_header_missing`（`:202`）自造邮件，要补 `To`：

```python
    m = EmailMessage()
    m["Subject"] = "无 Date 头"
    m["From"] = "t@cityu.edu.hk"
    m["To"] = "t@cityu.edu.hk"        # 转发件判据要 To/Cc，缺了会被 Task 3 的过滤静默滤掉
```

- [ ] **Step 2: 跑一遍确认「补 fixture 之前是红的」**

先临时把 `_hdr` 的新增行注释掉跑一次留证，再恢复。
Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q`
Expected（未补 `To` 时）: **预期 5 条红** —— `test_list_mail_returns_contract_items`、
`test_list_mail_uses_internaldate_when_date_header_missing`、
`test_list_mail_prefers_date_header_over_internaldate`、`test_do_list_applies_select_scope`、
`test_do_list_returns_newest_first`。**这 5 条是按阅读得出的预期，不是实跑值** ——
以实跑为准；红的是别的条，先查清再往下（多红或少红都说明我漏了一条依赖）。
Expected（补完后）: 全绿

- [ ] **Step 3: 写新用例（先红）**

```python
# ============ 接线：只留转发件 + 白名单剥键（spec §3.4/§3.5）============

def test_fetch_headers_asks_for_to_and_cc(monkeypatch):
    """**机制断言**：FETCH 命令串里必须含 `TO CC`。

    为什么必须是机制断言：IMAP 替身**不解析命令串**、只回固定字节，所以
    「少要了两个头」在行为上表现为「判据恒 False ⇒ 列表恒空」，而那是**可见**的；
    真正抓不住的是「命令里有没有这两个词」。**它的局限**：只证明命令里有，
    不证明服务端确实返回了它们（后者只能靠真实探测，见 spec §10）。
    `TO CC` 与 `INTERNALDATE` 写在同一条 FETCH 里，零额外往返。
    """
    fake = _install(monkeypatch, _FakeIMAP(headers={b"1": _hdr()}))
    mail_client.list_mail(7)
    fetch = next(c for c in fake.sent if c[0] == "uid" and c[1] == "FETCH")
    assert "TO CC" in fetch[-1]
    assert "INTERNALDATE" in fetch[-1]


def test_list_mail_drops_mail_not_addressed_to_school(monkeypatch):
    """只留转发件：判据是 To/Cc 命中学校域名（spec §2.1）。"""
    _install(monkeypatch, _FakeIMAP(headers={
        b"1": _hdr(subject="S1"),                                  # 默认 To: t@cityu.edu.hk
        b"2": _hdr(subject="S2", to="someone@example.invalid"),
    }))
    out = mail_client.list_mail(7)
    assert [it["subject"] for it in out] == ["S1"]


def test_list_mail_returns_exactly_contract_keys(monkeypatch):
    """**白名单**：`_internaldate` 与 `_school` 都是内部键，返回前一个都不许留。

    这条钉的是「黑名单改成白名单」这件事本身 —— 黑名单（`k != "_internaldate"`）
    在新增 `_school` 时会**静默泄漏**一个不在契约里的键。
    """
    _install(monkeypatch, _FakeIMAP(headers={b"1": _hdr(), b"2": _hdr(subject="S3")}))
    out = mail_client.list_mail(7)
    assert out, "前提：必须有邮件通过过滤，否则下面两条是空转"
    assert all(set(it) == {"id", "subject", "sender", "received"} for it in out)
```

- [ ] **Step 4: 跑一遍确认它红**

Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q -k "asks_for_to_and_cc or not_addressed_to_school or exactly_contract_keys"`
Expected: FAIL（前两条红；第三条可能已绿）

- [ ] **Step 5: 实现**

模块 docstring（`:1-25`）在「只读三件套」段之后补一段：

```
**只显示转发件**（spec §2）：`list_mail` 的返回集合收窄为「窗口内的转发件」——
判据 `recipient_is_school`（收件人 To/Cc 命中学校域名），在服务端即可判定，
不需要 Gmail 标签、不需要新建过滤器。键集合与类型与旧版**完全一致**，只是集合变小。
```

常量区加：

```python
#: 对外契约的键集合（spec §3.5）。`_do_list` 返回前**按它放行**。
#: 白名单而不是黑名单：黑名单在新增内部键时静默泄漏，而泄漏的正是「不在契约里」的东西。
_CONTRACT_KEYS = ("id", "subject", "sender", "received")
```

`_fetch_headers` 的 FETCH 命令（`:354-356`）：

```python
    typ, data = conn.uid(
        "FETCH", b",".join(uids),
        "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE TO CC)] INTERNALDATE)")
```

同函数的 docstring 补一句：

```
    顺带取 TO CC：转发件判据要收件人（spec §2.1），**不取就没有判据** ——
    同一趟 FETCH 里多两个词，零额外往返。
```

同函数的条目组装（`:371-375`）改成：

```python
        item = header_item(m.group(1).decode(), msg, internaldate)
        # 两个内部键，对外契约里都没有它们，`_do_list` 返回前按白名单剥掉（spec §3.5）。
        # `_school` 加在这里而不是 `header_item` 里：`header_item` 的契约恰好四个键，
        # 有两条用例逐字钉着（见计划头部「裁定 1」）。
        item["_internaldate"] = internaldate
        item["_school"] = recipient_is_school(msg)
```

`_do_list` 的收尾（`:398-400`）改成：

```python
        kept = [it for it in select_scope(items, datetime.now(timezone.utc), days)
                if it.get("_school") is True]
        kept = sort_newest_first(kept)
        # 只放行对外契约的四个键（白名单）。`_internaldate` 与 `_school` 都是内部键。
        return [{k: it[k] for k in _CONTRACT_KEYS} for it in kept]
```

（顺序照 spec §3.4：`select_scope` → 按 `_school` 过滤 → `sort_newest_first` → 剥键。
`_do_list` 的 docstring 也补一行：「返回的只是转发件（spec §2.1 判据），键集合不变。」）

- [ ] **Step 6: 跑一遍确认全绿**

Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q`
Expected: PASS（含 `test_header_item_shape_exactly_four_keys` 与
`test_header_item_survives_missing_headers` —— 它们**一行未改**仍绿，这正是裁定 1 的收益）
Run: `.venv/bin/python -m pytest -q`
Expected: 546 + 12 passed，零 fail

- [ ] **Step 7: 提交**

```bash
git add backend/mail_client.py tests/test_mail_client.py
git commit -m "feat(mail): 列表只留转发件；内部键改用白名单剥离"
```

---

### Task 4: `_fetch_raw_chunk` 重构 + `fetch_detail`

**Files:**
- Modify: `backend/mail_client.py`（`_fetch_bodies_chunk` `:413-436`；`_do_bodies` `:439-457`；新增 `_check_uidvalidity` / `_fetch_raw_chunk` / `fetch_detail` / `_attachment_parts`）
- Test: `tests/test_mail_client.py`（新增 fixture `_raw_mail` + 一节用例）

**Interfaces:**
- Consumes: `_connect()`（返回 `(conn, uidvalidity)`）、`message_from_bytes`、`message_body`、`MAX_BODY_CHARS`、模块级 `_uidvalidity`
- Produces:
  - `_check_uidvalidity(current: str) -> None`（不符即抛 `MailError`）
  - `_fetch_raw_chunk(conn, uids: list[str]) -> list[tuple[str, bytes]]`
  - `_attachment_parts(msg) -> list`
  - `_att_name(part, index: int) -> str`（朴素版；Task 5 加固）
  - `_part_size(part) -> int | None`（朴素版；Task 5 加固）
  - `fetch_detail(item: dict) -> {"id": str, "body": str, "attachments": [{"index": int, "name": str, "size": int | None}]}`

- [ ] **Step 1: 写 fixture 与失败用例**

fixture 用**原始字节**造 multipart（理由同 `_body_msg` 的注释：stdlib 的 `add_attachment`
编码/换行行为要另花力气核，原始字节把「正文恰好是 text」这件事钉死）：

```python
_BOUND = b"SYNTHBOUNDARY"


def _raw_mail(atts=(), text="正文", to=b"t@cityu.edu.hk"):
    """一封 multipart/mixed 的**原始字节**。

    atts 是 `[(头部块 bytes, 内容 bytes)]`，头部块自带 Content-Disposition。
    附件序 = atts 的顺序（与 `msg.walk()` 的遍历顺序一致）。

    `text` 收 **str**、内部 `encode("utf-8")`：与本文件既有的 `_body_msg`
    同一惯例（`_body_msg("第一封正文")`），并且整类消掉「非 ASCII 的 bytes 字面量」
    这个坑 —— `b"正文"` 是 **SyntaxError**（bytes 字面量只能含 ASCII）。
    """
    b = _BOUND
    out = (b'Content-Type: multipart/mixed; boundary="' + b + b'"\r\n'
           b"MIME-Version: 1.0\r\n"
           b"To: " + to + b"\r\nFrom: T <t@cityu.edu.hk>\r\nSubject: S\r\n"
           b"Date: Thu, 17 Sep 2026 16:31:00 +0800\r\n\r\n"
           b"--" + b + b"\r\n"
           b'Content-Type: text/plain; charset="utf-8"\r\n'
           b"Content-Transfer-Encoding: 8bit\r\n\r\n" + text.encode("utf-8") + b"\r\n")
    for head, data in atts:
        out += b"--" + b + b"\r\n" + head + b"\r\n\r\n" + data + b"\r\n"
    return out + b"--" + b + b"--\r\n"


def _att_head(name=b"a.bin", ctype=b"application/octet-stream"):
    return (b"Content-Type: " + ctype + b"\r\n"
            b"Content-Disposition: attachment; filename=\"" + name + b"\"\r\n"
            b"Content-Transfer-Encoding: 8bit")
```

用例：

```python
# ============ 详情：正文 + 附件清单（spec §4）============

def _detail_install(monkeypatch, raw_by_uid):
    return _install(monkeypatch, _BodyIMAP(
        headers={u: _hdr() for u in raw_by_uid}, bodies=raw_by_uid))


def test_fetch_detail_returns_body_and_attachments(monkeypatch):
    raw = _raw_mail([(_att_head(b"a.bin"), b"AAAA"),
                     (_att_head(b"b.txt", b"text/plain"), b"BB")])
    _detail_install(monkeypatch, {b"1": raw})
    mail_client.list_mail(7)                       # 先记 uidvalidity
    d = mail_client.fetch_detail({"id": "1"})
    assert d["id"] == "1"
    assert d["body"] == "正文"
    assert [a["index"] for a in d["attachments"]] == [0, 1]
    assert [a["name"] for a in d["attachments"]] == ["a.bin", "b.txt"]
    assert [a["size"] for a in d["attachments"]] == [4, 2]


def test_fetch_detail_index_is_stable_across_two_parses(monkeypatch):
    """**承重不变式**：不缓存字节 ⇒ index 必须可复现。解析两次，清单逐字相同。"""
    raw = _raw_mail([(_att_head(b"a.bin"), b"AAAA"), (_att_head(b"b.bin"), b"BB")])
    _detail_install(monkeypatch, {b"1": raw})
    mail_client.list_mail(7)
    first = mail_client.fetch_detail({"id": "1"})
    second = mail_client.fetch_detail({"id": "1"})
    assert first["attachments"] == second["attachments"]


def test_fetch_detail_ignores_inline_and_body_parts(monkeypatch):
    """`inline` 部件既不算附件、也不进正文（spec §4）。"""
    inline = (b"Content-Type: image/png\r\n"
              b"Content-Disposition: inline\r\n"
              b"Content-Transfer-Encoding: 8bit")
    raw = _raw_mail([(inline, b"PNGDATA"), (_att_head(b"real.bin"), b"ZZ")])
    _detail_install(monkeypatch, {b"1": raw})
    mail_client.list_mail(7)
    d = mail_client.fetch_detail({"id": "1"})
    assert [a["name"] for a in d["attachments"]] == ["real.bin"]
    assert "PNGDATA" not in d["body"]


def test_fetch_detail_no_attachments_is_empty_list(monkeypatch):
    _detail_install(monkeypatch, {b"1": _raw_mail()})
    mail_client.list_mail(7)
    assert mail_client.fetch_detail({"id": "1"})["attachments"] == []


def test_fetch_detail_truncates_body(monkeypatch):
    """正文套 `MAX_BODY_CHARS`（spec §4）。"""
    _detail_install(monkeypatch, {b"1": _raw_mail(text="x" * 40)})
    monkeypatch.setattr(mail_client, "MAX_BODY_CHARS", 10)
    mail_client.list_mail(7)
    assert mail_client.fetch_detail({"id": "1"})["body"] == "x" * 10


def test_fetch_detail_survives_broken_mime(monkeypatch):
    """坏 MIME 不抛（spec §7）：声明 `multipart/mixed` 却**没有分隔线**、附件段是坏 base64。

    **判别力已实测界定（照实登记，别照抄旧说法）**：这份输入下 `msg.walk()` 解出
    **0 个附件 part**、`message_body` 返回的是那段原始头文本（是 str）。也就是说
    这条用例**根本不会调用 `_att_name` / `_part_size`** —— 它钉的是「`fetch_detail`
    在这份输入上不抛、且输出形状仍是 `str` + `list`」，**不覆盖那两个函数的守卫**
    （后者是 Task 5 才加的加固，见 Task 5「`_att_name` 守卫」那条）。
    末行是**实测形状**（`== []`）—— 它**是**钉住数量的。旧稿写的是
    `all(set(a) == {"index","name","size"} for a in d["attachments"])`，那句在空清单上
    恒真、**什么都没钉**（复审 M6 量出来的），已换成这一条；别照旧稿改回去。
    """
    raw = (b'Content-Type: multipart/mixed; boundary="NEVERAPPEARS"\r\n'
           b"To: t@cityu.edu.hk\r\nFrom: T <t@cityu.edu.hk>\r\nSubject: S\r\n\r\n"
           b"Content-Type: application/octet-stream\r\n"
           b'Content-Disposition: attachment; filename="broken.bin"\r\n'
           b"Content-Transfer-Encoding: base64\r\n\r\n"
           b"!!!not-base64!!!\r\n")
    _detail_install(monkeypatch, {b"1": raw})
    mail_client.list_mail(7)
    d = mail_client.fetch_detail({"id": "1"})
    assert isinstance(d["body"], str)
    assert isinstance(d["attachments"], list)
    assert d["attachments"] == []


def test_fetch_detail_raises_when_uid_is_gone(monkeypatch):
    """取不到就抛 —— 绝不返回一个空壳让前端显示「这封邮件没有正文」。"""
    _detail_install(monkeypatch, {b"1": _raw_mail()})
    mail_client.list_mail(7)
    with pytest.raises(mail_client.MailError):
        mail_client.fetch_detail({"id": "999"})


def test_fetch_detail_checks_uidvalidity(monkeypatch):
    """邮箱被重建 ⇒ 抛（复用 `_do_bodies` 的机制，spec §4）。

    末两行是**排序**断言，不是重复断言：只写 `pytest.raises` 的话，
    把 `_check_uidvalidity` 挪到取件**之后**这条用例**照样绿**
    （`_FakeIMAP.uid` 的 FETCH 分支对已知 uid 会回一行，`fetch_detail` 于是拿到东西、
    不抛）。口径与既有 `test_fetch_bodies_raises_on_uidvalidity_change` 的
    「一条 `BODY.PEEK[]` 都不许发」一致。
    """
    _detail_install(monkeypatch, {b"1": _raw_mail()})
    mail_client.list_mail(7)
    monkeypatch.setattr(mail_client, "_uidvalidity", "7")
    fake = _install(monkeypatch, _FakeIMAP(headers={b"1": _hdr()}, uidvalidity=b"8"))
    with pytest.raises(mail_client.MailError, match="UIDVALIDITY"):
        mail_client.fetch_detail({"id": "1"})
    assert not [c for c in fake.sent if c[0] == "uid" and "BODY.PEEK[]" in str(c[-1])], \
        "UIDVALIDITY 不符时不该取任何正文"


def test_attachment_parts_include_forwarded_message():
    """**作为附件转发的邮件**（`message/rfc822`）也算附件（spec §4）。

    实测形状：该 part `get_content_type()` 为 `message/rfc822` 而 **`is_multipart()` 为 True**
    —— 判据里多一条 `not is_multipart()` 就会把它**静默丢掉**（原实现即如此）。
    **只钉名字，不碰 size**：size 归 Task 5 加固后再钉（那之前 `_part_size` 对它给 `None`）。
    """
    inner = (b"From: a@x.com\r\nTo: t@cityu.edu.hk\r\n"
             b"Subject: inner\r\n\r\ninner body\r\n")
    eml = (b"Content-Type: message/rfc822\r\n"
           b'Content-Disposition: attachment; filename="fwd.eml"')
    msg = email.message_from_bytes(_raw_mail([(eml, inner)]))
    assert [p.get_filename() for p in mail_client._attachment_parts(msg)] == ["fwd.eml"]


def test_fetch_detail_raises_when_fetch_fails(monkeypatch):
    """服务端 FETCH 回非 OK ⇒ `MailError`（spec §7），不是把 imaplib 的错冒出去。

    打的是 `_fetch_raw_chunk` 里 `typ != "OK"` 那条分支 —— 它被 `_do_bodies` 与
    `fetch_detail` **共用**，抽出后却没人钉过（既有用例只覆盖 `status` 回非 OK）。

    ⚠️ **门必须只挡取正文那条 FETCH**（`"BODY.PEEK[]" in str(args[-1])`，
    与 `_BodyIMAP` 同一口径）。裸写 `if cmd == "FETCH"` 会**连取头一起挡**：
    `_fetch_headers` 发的是 `BODY.PEEK[HEADER.FIELDS (…)]`，实测该串**不含**
    `"BODY.PEEK[]"`（故两者可干净分开），于是 `list_mail(7)` 就先抛
    「读取邮件头失败」—— 用例红在**别的错**上，跟 `_fetch_raw_chunk` 那条分支无关。
    """

    class _NoFetch(_FakeIMAP):
        def uid(self, cmd, *args):
            if cmd == "FETCH" and "BODY.PEEK[]" in str(args[-1]):
                self.sent.append(("uid", cmd) + tuple(args))
                return ("NO", [b"server said no"])
            return super().uid(cmd, *args)

    _install(monkeypatch, _NoFetch(headers={b"1": _hdr()}))
    mail_client.list_mail(7)
    with pytest.raises(mail_client.MailError, match="读取邮件正文失败"):
        mail_client.fetch_detail({"id": "1"})


def test_logout_error_does_not_escape_or_mask(monkeypatch):
    """登出抛异常时：**不许**冒出 `fetch_detail`，也**不许**盖住真正的错。

    这条钉的是 `_logout_quietly`（`backend/mail_client.py:320`）存在的**唯一理由** ——
    它的 docstring 逐字：「登出，异常吞掉 —— **不能让它盖住真正的错**」。
    **变异已验证它承重**（实现者实测 M10）：把这个调用换回裸 `conn.logout()`，
    形状一立刻红 —— 登出的异常会直接冒出 `fetch_detail`。

    为什么必须有用例：全仓测试此前**只有** `("logout",) in fake.sent` 这类断言
    （`tests/test_mail_client.py:149,:159`），它们钉的是「登出被调用了」，
    **不是**「登出的异常被吞了」。裸 `logout()` 同样会让那两条绿 ——
    于是 I1 这处修复曾完全靠人眼守。两个形状各钉一件事：
    形状一钉「吞掉」，形状二钉「不盖住」。
    """
    class _BadLogout(_BodyIMAP):
        def logout(self):
            self.sent.append(("logout",))
            raise imaplib.IMAP4.error("socket already closed")

    # 形状一：取件成功、登出抛 ⇒ 结果照常返回，异常不许冒出去
    _install(monkeypatch, _BadLogout(headers={b"1": _hdr()},
                                     bodies={b"1": _raw_mail()}))
    mail_client.list_mail(7)
    assert mail_client.fetch_detail({"id": "1"})["body"] == "正文"

    # 形状二：取件失败、登出也抛 ⇒ 冒出来的必须是**取件**那个错
    class _BadFetch(_BadLogout):
        def uid(self, cmd, *args):
            if cmd == "FETCH" and "BODY.PEEK[]" in str(args[-1]):
                self.sent.append(("uid", cmd) + tuple(args))
                return ("NO", [b"server said no"])
            return super().uid(cmd, *args)

    _install(monkeypatch, _BadFetch(headers={b"1": _hdr()}))
    mail_client.list_mail(7)
    with pytest.raises(mail_client.MailError, match="读取邮件正文失败"):
        mail_client.fetch_detail({"id": "1"})
```

- [ ] **Step 2: 跑一遍确认它红**

Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q -k fetch_detail`
Expected: FAIL —— `AttributeError: ... has no attribute 'fetch_detail'`

- [ ] **Step 3: 实现**

把 `_fetch_bodies_chunk`（`:413-436`）拆成「取原始字节」+「转正文」两层：

```python
def _check_uidvalidity(current: str) -> None:
    """邮箱自上次刷新后是否被重建（UIDVALIDITY 变了）。

    前提：同一次刷新里 `list_mail` 已经先跑过。进程内第一次就直接调时
    `_uidvalidity` 还是空串，这里的核对会被跳过（生产路径不会这样调）。
    """
    if _uidvalidity and current != _uidvalidity:
        raise MailError(
            "邮箱自上次刷新后已被重建（UIDVALIDITY 变了），"
            "邮件与编号的对应关系不再可靠。请重新点「刷新邮件」。")


def _fetch_raw_chunk(conn, uids: list[str]) -> list[tuple[str, bytes]]:
    """批量取整封**原始字节**。**BODY.PEEK[]** —— 绝不置已读。

    `_fetch_bodies_chunk` 与 `fetch_detail`/`fetch_attachment` 共用这一条命令与这一套
    解析：FETCH 响应的形状（`UID n` 元数据 + 字节）只有**一处**知识，正则也只写一遍。
    """
    typ, data = conn.uid("FETCH", ",".join(uids).encode(), "(BODY.PEEK[])")
    if typ != "OK":
        raise MailError(f"读取邮件正文失败：{data}")
    out = []
    for entry in data:
        if not isinstance(entry, tuple) or len(entry) < 2:
            continue
        meta, raw = entry[0], entry[1]
        blob = meta if isinstance(meta, bytes) else str(meta).encode()
        m = re.search(rb"UID\s+(\d+)", blob)
        if not m:
            continue
        out.append((m.group(1).decode(), raw if isinstance(raw, bytes) else b""))
    return out


def _fetch_bodies_chunk(conn, uids: list[str]) -> list[tuple[str, str]]:
    """批量取整封。**BODY.PEEK[]** —— 绝不置已读（命令在 `_fetch_raw_chunk` 里）。

    注意 IMAP 会把附件一起给你（没有「只要正文」的通用取法）；message_body
    负责把附件排除在正文之外。
    """
    out = []
    for uid, raw in _fetch_raw_chunk(conn, uids):
        body = message_body(message_from_bytes(raw))
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS]
        out.append((uid, body))
    return out
```

`_do_bodies` 里那段 UIDVALIDITY 比对换成调用：

```python
        _check_uidvalidity(uidvalidity)
```

新增（放在 `fetch_bodies` 之前）：

```python
def _attachment_parts(msg) -> list:
    """附件 part，按 `msg.walk()` 的顺序（spec §4）。

    「是附件」= `Content-Disposition: attachment` —— **判据只有这一条**（spec §4 原话：
    「部件有 `Content-Disposition: attachment`（沿用 `message_body` 现 `:193` 的口径）」，
    唯一被排除的是 `inline`）。`inline` 部件（内嵌图）既不算附件、也不进正文 —— v1 不显示。

    ⚠️ **不许再加 `not p.is_multipart()`**：那是从 `message_body` 的循环形状抄来的，
    在**附件**判据上是错的。实测（`message_from_bytes` 解一封「作为附件转发」的邮件）：
    `Content-Type: message/rfc822` + `Content-Disposition: attachment` 的部件
    **`is_multipart()` 为 True**（它的 payload 是内嵌的那封 Message），
    于是被静默丢掉 —— Gmail/Outlook 的「Forward as attachment」正是这个形状，
    而它恰恰是最该被看到的那类转发件（正文常常只有一句「见附件」）。

    这条例外只放行 `message/rfc822`：其余 multipart 部件（`multipart/*` 容器）仍然不算附件
    ——容器本身不是可下载的文件。
    """
    return [p for p in msg.walk()
            if (not p.is_multipart() or p.get_content_type() == "message/rfc822")
            and (p.get_content_disposition() or "") == "attachment"]


def fetch_detail(item: dict) -> dict:
    """取一封的正文 + 附件清单。**BODY.PEEK[]** —— 绝不置已读（spec §4）。

    返回 `{"id", "body", "attachments": [{"index", "name", "size"}]}`。
    `index` = 该 part 在**附件序**中的位置（`_attachment_parts` 的下标），
    `fetch_attachment` 用同一套判定、同一遍历顺序重新解析 —— **不缓存字节**，
    故 index 可复现（有用例钉住「解析两次相同」）。
    `size` 解不出是 `None`，**绝不编造 0**。
    """
    uid = item["id"]
    conn, uidvalidity = _connect()
    try:
        _check_uidvalidity(uidvalidity)
        rows = _fetch_raw_chunk(conn, [uid])
    finally:
        _logout_quietly(conn)
    if not rows:
        raise MailError(f"没有取到编号为 {uid} 的邮件（它可能已被移走或删除）")
    msg = message_from_bytes(rows[0][1])
    body = message_body(msg)
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS]
    atts = [{"index": i, "name": _att_name(p, i), "size": _part_size(p)}
            for i, p in enumerate(_attachment_parts(msg))]
    return {"id": uid, "body": body, "attachments": atts}
```

还要补上这两个**朴素可用**的版本 —— Task 5 会把它们换成加固版（加净化、编解码兜底、异常守卫）。
此处刻意**不写成 `return None` / `return f"attachment-{index}"` 那种桩**：
本任务 Step 1 的用例断言的是 `["a.bin","b.txt"]` 与 `[4,2]`（**最终契约**），
桩会让本任务自己的 Step 4 无法变绿；而「先让用例绿、再让它在更难输入下也绿」
正是 TDD 的走法 —— 朴素版对**朴素输入**已经是对的：

```python
def _att_name(part, index: int) -> str:
    """附件名。**朴素版**：Task 5 加净化与编解码兜底。"""
    return part.get_filename() or f"attachment-{index}"


def _part_size(part) -> int | None:
    """附件字节数。**朴素版**：Task 5 加异常守卫（解不出必须给 `None`，不是抛）。"""
    data = part.get_payload(decode=True)
    return len(data) if isinstance(data, bytes) else None
```

- [ ] **Step 4: 跑一遍确认它绿**

Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q -k fetch_detail`
Expected: PASS（**8 条** —— 本步骤执行时树上的状态）
⚠️ **条数是相对树状态说的，别当成常量**：这一节跑在**修复轮之前**，那时名字里带
`fetch_detail` 的是 8 条。修复轮各加 1 条后，同一个选择器给 **9 条**；再加
`-k "fetch_detail or attachment_parts or logout"` 给 **11 条**（两个新用例的名字里
**不含** `fetch_detail`，`-k` 选不到它们 —— **选择器本身也是断言**，改用例名就会失效）。
Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q -k fetch_bodies`
Expected: PASS（既有正文用例**一条都不许红** —— 这条证明重构没改行为）
Run: `.venv/bin/python -m pytest -q`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add backend/mail_client.py tests/test_mail_client.py
git commit -m "feat(mail): fetch_detail（正文 + 附件清单）；FETCH 响应解析收敛为 _fetch_raw_chunk"
```

---

### Task 5: 附件：名字净化、大小、单个取回

**Files:**
- Modify: `backend/mail_client.py`（替换 Task 4 的两个桩；新增 `safe_attachment_name` / `fetch_attachment` / `MAX_ATTACHMENT_BYTES` / `MAX_ATTACHMENT_MB` / `_reject_if_too_big`）
- Test: `tests/test_mail_client.py`（新增一节）

**Interfaces:**
- Consumes: `_fetch_raw_chunk`、`_check_uidvalidity`、`_attachment_parts`（Task 4）
- Produces:
  - `MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024`、`MAX_ATTACHMENT_MB = 20`
  - `safe_attachment_name(raw: str, index: int) -> str`
  - `_att_name(part, index) -> str`、`_part_size(part) -> int | None`
  - `fetch_attachment(item: dict, index: int) -> tuple[str, bytes]`

- [ ] **Step 1: 先实跑一次确认 `get_filename()` 吃不吃这几种头（spec §4 明确要求「先跑一次」）**

`email.get_filename()` 对 RFC 2231（`filename*=UTF-8''%E4%BD%9C%E4%B8%9A.pdf`）的覆盖
**未实测**。跑这一段并把输出抄进报告：

```bash
.venv/bin/python - <<'PY'
from email import message_from_string
from email.header import decode_header, make_header
cases = [
    ('ascii',       'Content-Disposition: attachment; filename="a.pdf"'),
    ('rfc2047',     'Content-Disposition: attachment; filename="=?utf-8?B?5L2c5LiaLnBkZg==?="'),
    ('rfc2231',     "Content-Disposition: attachment; filename*=utf-8''%E4%BD%9C%E4%B8%9A.pdf"),
    ('2231+2047',   'Content-Disposition: attachment; filename="=?utf-8?B?5L2c5Lia?="; '
                    "filename*=utf-8''%E4%BD%9C%E4%B8%9A.pdf"),
    ('none',        'Content-Disposition: attachment'),
    ('cjk-raw8bit', 'Content-Disposition: attachment; filename="作业.pdf"'),
]
for name, h in cases:
    m = message_from_string(h + '\r\n\r\nxx')
    raw = m.get_filename()
    try:
        dec = str(make_header(decode_header(raw or "")))
    except Exception as e:
        dec = f"<decode 失败 {e}>"
    print(f"{name:12} raw={raw!r}  decoded={dec!r}")

# ⚠️ 上面走的是 str 路径；**生产代码走的是 bytes 路径**（`_att_name` 的 part 来自
# `message_from_bytes(rows[0][1])`）。两者只在裸 8bit 那一格不同 —— 必须单独量：
from email import message_from_bytes
h = 'Content-Disposition: attachment; filename="作业.pdf"'
m = message_from_bytes(h.encode("utf-8") + b"\r\n\r\nxx")
raw = m.get_filename()
print(f"{'raw8bit/bytes':16} raw={raw!r}  decoded={str(make_header(decode_header(raw or '')))!r}")
PY
```

**上面最后一行实测应为 mojibake（`'������.pdf'`），不是 `'作业.pdf'`** —— 这是文档化的
已知限制，**不是**要你去修的 bug：裸 8bit 头是非合规来信，`decode_mime_header` 救不回。

**判据（已实跑，结论照抄下表，不要再自己推）**：

| 形状 | `get_filename()` 给什么 | 需要 `decode_mime_header()` 兜底？ |
|---|---|---|
| `ascii` | `'a.pdf'` | 否（对它幂等） |
| `rfc2047` | **编码字原文** `'=?utf-8?B?5L2c5LiaLnBkZg==?='` | **是** ← 兜底存在的唯一理由 |
| `rfc2231` | 直接 `'作业.pdf'`（CPython 的 `collapse_rfc2231_value` 已处理） | 否；加了也无害 |
| `2231+2047` | 只给 `filename=` 那半 | 是，但 **`filename*` 那半丢了**（见下） |
| `none` | `None` | —（走 `attachment-<index>`） |
| `cjk-raw8bit`（**str** 路径，即上面这段探针自己） | `'作业.pdf'` | 否 |
| `cjk-raw8bit`（**bytes** 路径，即**真实来信**） | **mojibake** `'������.pdf'` | 救不回 |

⇒ **结论：兜底必须有，但理由只有 `rfc2047` 一条。** 初稿的判据写「`cjk-raw8bit` 与 `rfc2047`
必须能还原」是**半错**的：它断言的是上面那段探针走了 `message_from_string`（str），
而生产代码走的是 `message_from_bytes`（`_att_name` 的 part 来自 `message_from_bytes(rows[0][1])`）
—— **bytes 路径下裸 8bit 文件名解出来就是 mojibake，`decode_mime_header` 也救不回**。
这只影响名字好看不好看，**不影响安全**（实测净化后无 `/`、`\`、`..` 残留）。
⇒ 裸 8bit 是**非合规来信**（头必须是 ASCII），实测登记为已知限制，**不为它加代码**。

⚠️ **另一处实测限制（登记，不修）**：`2231+2047` 同时给出 `filename=`（RFC 2047）与
`filename*=`（RFC 2231）时，`get_filename()` 只返回**前者**，`filename*` 那半**丢了** ——
实测 `作业` 而不是 `作业.pdf`（**扩展名被吃掉**）。修它要自己手解 `filename*`，
等于把 stdlib 的 RFC 2231 解析重写一遍，风险大于收益（只是名字少个后缀，净化仍然生效）。
⇒ 登记为已知限制。

- [ ] **Step 2: 写失败用例**

```python
# ============ 附件：净化、大小、单个取回（spec §4）============

@pytest.mark.parametrize("raw,expect", [
    ("../../etc/passwd", "____etc_passwd"),
    ("..\\..\\x", "____x"),
    ("a/b.txt", "a_b.txt"),
    ("a\x00b.txt", "ab.txt"),
    ("  .hidden.  ", "hidden"),
    (" . . ", "attachment-3"),
    ("", "attachment-3"),
    ("x" * 300, "x" * 200),
])
def test_safe_attachment_name(raw, expect):
    """净化后**永不**含路径分隔符、`..` 段或 NUL，且非空、不过 200 字符。

    ⚠️ 前两例的期望值是**实跑定的**，别按直觉往回改：净化顺序是「先 `/`、`\\` → `_`，
    **再** `..` → `_`」，所以 `../../etc/passwd` 先变成 `.._.._etc_passwd`，那两个
    `..` 随后各自被换成 `_`（连同夹在中间的 `_`），最终是 **`____etc_passwd`**（4 个下划线）。
    写成 `".._.._etc_passwd"` 会与**本用例第二条断言** `".." not in out` 自相矛盾 ——
    计划初稿就是这么错的，实跑 7 例抓出来的。

    ⚠️ **本表全是 ASCII，所以它绑不住 `_att_name` 的 RFC 2047 兜底** ——
    `get_filename()` 对 ASCII 名字本来就可读，兜底那条路一次都走不到。
    兜底的绑定在 `test_att_name_decodes_rfc2047_filename` 上，别以为这张表覆盖了它。

    ⚠️ `" . . "` → `attachment-3` 那一例钉的是「`.strip().strip(".")` 会把夹在点之间的
    空白**重新露到两端**」：旧写法下结果是 `" "`（一个空格），既不算空、又留着首尾空白，
    与 docstring 的「去掉首尾空白与点」不符（复审 Minor 4，实跑 `" . . "` → `' '` 抓出的）。
    """
    out = mail_client.safe_attachment_name(raw, 3)
    assert out == expect
    assert "/" not in out and "\\" not in out and "\x00" not in out and ".." not in out


def test_att_name_survives_get_filename_raising():
    """`_att_name` 的守卫：`get_filename()` **抛**时给 `attachment-<index>`，绝不往上抛。

    这条补的是 Task 4 `test_fetch_detail_survives_broken_mime` **声称**覆盖、实测**没有**
    覆盖的那一格（那份输入解出 0 个附件 part，`_att_name` 根本没被调用）。
    守卫本身是 Task 5 加的（Task 4 的 `_att_name` 是朴素版、没有 try），所以这条只能落在 Task 5。
    用替身而不是真 MIME：`compat32` 的 `get_filename()` 实际很难被喂到「抛」的形状，
    而这里要钉的是**我们的守卫**，不是解析器的行为。
    """
    class _BadName:
        def get_filename(self):
            raise ValueError("boom")
    assert mail_client._att_name(_BadName(), 4) == "attachment-4"


def test_part_size_is_none_when_undecodable():
    """解不出就是 `None`，**绝不编造 0**（spec §4）。

    ⚠️ 桩**必须**同时给出 `get_content_type()`：`_part_bytes` 第一句就调它，
    少了它抛的是 `AttributeError`（被同一个 `except` 吞掉），本用例就变成「钉住了一个
    别的失败」—— 名字说的解码失败一次都没走到，还会在一次把 `get_content_type()`
    挪出 `try` 的正当重构上无故变红（复审 Minor 5；实测该桩收到的唯一调用就是
    `get_content_type`，`get_payload` 根本没被调到）。
    """
    class _Bad:
        def get_content_type(self):
            return "application/octet-stream"

        def get_payload(self, decode=False):
            raise ValueError("boom")
    assert mail_client._part_size(_Bad()) is None


def test_reject_if_too_big_boundary_and_message():
    """边界与文案：`== MAX` 放行、`MAX+1` 抛，且文案逐字含「超过 20 MB」。"""
    limit = mail_client.MAX_ATTACHMENT_BYTES
    mail_client._reject_if_too_big(limit, "a.bin")          # 不抛
    with pytest.raises(mail_client.MailError) as ei:
        mail_client._reject_if_too_big(limit + 1, "a.bin")
    assert "超过 20 MB" in str(ei.value)


def test_fetch_attachment_returns_name_and_bytes(monkeypatch):
    raw = _raw_mail([(_att_head(b"a.bin"), b"AAAA"), (_att_head(b"b.bin"), b"BB")])
    _detail_install(monkeypatch, {b"1": raw})
    mail_client.list_mail(7)
    assert mail_client.fetch_attachment({"id": "1"}, 1) == ("b.bin", b"BB")


def test_fetch_attachment_agrees_with_detail_index(monkeypatch):
    """**组合绑定**：detail 报出的 index 拿回 `fetch_attachment`，取到的是**同一个**附件。"""
    raw = _raw_mail([(_att_head(b"a.bin"), b"AAAA"), (_att_head(b"b.bin"), b"BB")])
    _detail_install(monkeypatch, {b"1": raw})
    mail_client.list_mail(7)
    for a in mail_client.fetch_detail({"id": "1"})["attachments"]:
        name, data = mail_client.fetch_attachment({"id": "1"}, a["index"])
        assert name == a["name"]
        assert len(data) == a["size"]


def test_fetch_attachment_rejects_bad_index(monkeypatch):
    """越界 / 负数 / 非 int 一律抛，绝不「就近取一个」。

    **`True` 在本用例里是被「越界」顺带挡住的，不是 `bool` 守卫挡住的** ——
    这是**单附件**邮件，`True == 1` 恰好越界。所以本用例**不许**声称它钉住了
    `isinstance(index, bool)`：把守卫删掉，这里照样绿（变异实测 M5，brief 初稿的
    docstring 写「（含 bool）」正是**声明强于断言**）。真正钉住守卫的是紧随其后的
    `test_fetch_attachment_rejects_bool_index_even_when_in_range`（两个附件，
    `True`/`False` 都落在范围内）。**声明已改准，别再改回「（含 bool）」。**
    """
    _detail_install(monkeypatch, {b"1": _raw_mail([(_att_head(b"a.bin"), b"AAAA")])})
    mail_client.list_mail(7)
    for bad in (-1, 1, 99, "0", None, True, 0.0):
        with pytest.raises(mail_client.MailError):
            mail_client.fetch_attachment({"id": "1"}, bad)


def test_fetch_attachment_rejects_bool_index_even_when_in_range(monkeypatch):
    """`bool` 必须**显式**排掉，不能靠越界兜住（M5 的补钉，见上一条 docstring）。

    两个附件 ⇒ `True`(=1) 与 `False`(=0) **都在范围内**：少了守卫时 `True` 会
    **静默取回第 1 个附件**，正是要禁的「就近取一个」。前面的 0/1 是**正例锚**
    （否则下面的 `raises` 可能只是「一直抛」）。
    """
    raw = _raw_mail([(_att_head(b"a.bin"), b"AAAA"), (_att_head(b"b.bin"), b"BB")])
    _detail_install(monkeypatch, {b"1": raw})
    mail_client.list_mail(7)
    assert mail_client.fetch_attachment({"id": "1"}, 0)[0] == "a.bin"
    assert mail_client.fetch_attachment({"id": "1"}, 1)[0] == "b.bin"
    for bad in (True, False):
        with pytest.raises(mail_client.MailError):
            mail_client.fetch_attachment({"id": "1"}, bad)


def test_att_name_decodes_rfc2047_filename(monkeypatch):
    """RFC 2047 编码字的附件名必须解成人话 —— 这是 `_att_name` 里 `decode_mime_header`
    兜底**唯一的理由**（M6 的补钉）。

    上面那张参数化表**全是纯 ASCII**，`get_filename()` 对它们给的就是可读名字 ⇒
    兜底那条路**一次都没走到**，删掉兜底整节照绿。所以表里的结论必须在**这里**绑：
    钉的是**生产路径**（`message_from_bytes` 解出的 part → `_att_name`）。
    """
    raw = _raw_mail([(_att_head(b"=?utf-8?B?5L2c5LiaLnBkZg==?="), b"AAAA")])
    _detail_install(monkeypatch, {b"1": raw})
    mail_client.list_mail(7)

    # 前提锚：先证明这份输入**确实**落在兜底那一格（若某天 stdlib 自己会解编码字，
    # 兜底成死代码，本用例应红在**这里**，而不是红在名字上让人误以为是回归）
    part = mail_client._attachment_parts(email.message_from_bytes(raw))[0]
    assert part.get_filename() == "=?utf-8?B?5L2c5LiaLnBkZg==?=", \
        "get_filename() 已经自己能解编码字了 —— Step 1 表里 rfc2047 那一格的前提变了"

    d = mail_client.fetch_detail({"id": "1"})
    assert [a["name"] for a in d["attachments"]] == ["作业.pdf"]
    assert mail_client.fetch_attachment({"id": "1"}, 0)[0] == "作业.pdf"


def test_fetch_attachment_raises_when_fetch_fails(monkeypatch):
    """超限与「解不出字节」两条失败分支必须在**调用点**上钉住（复审 I1）。

    `_reject_if_too_big` 与 `_part_size` 各自的纯函数用例不能替代这两条：
    把 `fetch_attachment` 里那两个 `raise` 删掉，纯函数用例**照样绿** ——
    少了下半支，用户看到的会是 `TypeError: object of type 'NoneType' has no len()`。
    spec §4 的 20 MiB 上限是本任务的门面要求，它的执行点不该没有绑定。
    """
    raw = _raw_mail([(_att_head(b"a.bin"), b"AAAA")])
    _detail_install(monkeypatch, {b"1": raw})
    mail_client.list_mail(7)

    # 路径一：超限（`MAX_ATTACHMENT_BYTES` 是**调用时**读的模块全局，可以 monkeypatch 小）
    monkeypatch.setattr(mail_client, "MAX_ATTACHMENT_BYTES", 2)
    with pytest.raises(mail_client.MailError, match="超过"):
        mail_client.fetch_attachment({"id": "1"}, 0)

    # 路径二：字节解不出（`_part_bytes` 给 None）
    monkeypatch.setattr(mail_client, "_part_bytes", lambda part: None)
    with pytest.raises(mail_client.MailError):
        mail_client.fetch_attachment({"id": "1"}, 0)


def test_fetch_attachment_raises_when_uid_is_gone(monkeypatch):
    _detail_install(monkeypatch, {b"1": _raw_mail([(_att_head(b"a.bin"), b"AAAA")])})
    mail_client.list_mail(7)
    with pytest.raises(mail_client.MailError):
        mail_client.fetch_attachment({"id": "999"}, 0)


def test_fetch_attachment_logout_error_does_not_escape(monkeypatch):
    """`fetch_attachment` 的登出异常同样不许冒出去。

    与 Task 4 `test_logout_error_does_not_escape_or_mask` **同源同形但不同点**：
    那条钉 `fetch_detail` 的 `finally`，这条钉 `fetch_attachment` 的。
    两处**各自承重** —— 只钉一处的话，另一处换回裸 `conn.logout()`
    不会被任何用例发现（全仓此前没有任何用例让 `logout` 抛过）。
    """
    class _BadLogout(_BodyIMAP):
        def logout(self):
            self.sent.append(("logout",))
            raise imaplib.IMAP4.error("socket already closed")

    raw = _raw_mail([(_att_head(b"a.bin"), b"AAAA")])
    _install(monkeypatch, _BadLogout(headers={b"1": _hdr()}, bodies={b"1": raw}))
    mail_client.list_mail(7)
    assert mail_client.fetch_attachment({"id": "1"}, 0) == ("a.bin", b"AAAA")


def test_forwarded_message_attachment_round_trips(monkeypatch):
    """「作为附件转发」的邮件（`message/rfc822`）：**列得出、下载得回、size 对得上**。

    这是 Task 4 修好「静默丢掉」之后的**下半场**：`_part_bytes` 必须走
    `as_bytes()` 那条分支，否则清单里有它、点下载却报「无法解码」。
    断言刻意**不钉字节数与内容细节**（`as_bytes()` 是 stdlib 的再序列化，
    换行/折行会被规范化 —— 钉死那些数字就是钉 stdlib 的行为，不是钉我们的）；
    钉的是「内嵌正文确实在里面」+「下载长度 == 清单报的 size」这条组合绑定。
    """
    inner = (b"From: a@x.com\r\nTo: t@cityu.edu.hk\r\n"
             b"Subject: inner\r\n\r\ninner body\r\n")
    eml = (b"Content-Type: message/rfc822\r\n"
           b'Content-Disposition: attachment; filename="fwd.eml"')
    _detail_install(monkeypatch, {b"1": _raw_mail([(eml, inner)])})
    mail_client.list_mail(7)
    d = mail_client.fetch_detail({"id": "1"})
    assert [a["name"] for a in d["attachments"]] == ["fwd.eml"]
    name, data = mail_client.fetch_attachment({"id": "1"}, 0)
    assert name == "fwd.eml"
    assert b"inner body" in data, "内嵌那封邮件的正文必须在下载内容里"
    assert len(data) == d["attachments"][0]["size"]


# ============ 只读三件套：新路径也要**全流程**覆盖（spec §7）============
# 既有的 `test_all_fetches_use_body_peek` / `test_never_issues_mutating_commands`
# 只驱动 `list_mail` —— 症状是「取件那两条路有没有置已读」在整份测试里**没人问过**。
# 单靠 Task 12 的 grep 不够：grep 查的是字面量，查不出「用了某个会置已读的别名」。

def test_detail_and_attachment_paths_are_readonly(monkeypatch):
    """详情与附件取回：全程 PEEK、无 STORE/COPY/MOVE/EXPUNGE。

    **正例锚**（否定式断言的老毛病）：「一条命令都没发」时下面几条同时恒真，
    所以先证明这次运行真走到了取件那段路。三次调用各连一次（不缓存，spec §4），
    故 FETCH **三条** —— 一条 list_mail 取头、两条取整封（详情一条、附件一条）。
    """
    raw = _raw_mail([(_att_head(b"a.bin"), b"AAAA")])
    fake = _detail_install(monkeypatch, {b"1": raw})
    mail_client.list_mail(7)
    mail_client.fetch_detail({"id": "1"})
    mail_client.fetch_attachment({"id": "1"}, 0)
    fetches = [c for c in fake.sent if c[0] == "uid" and c[1] == "FETCH"]
    # 正例锚要**精确**：`>= 2` 会让「少发一条取件」的回归照样绿（复审 Minor 6）。
    # 确切条数由实跑定 —— 上面 docstring 数的是 1 条 list_mail + 2 条取整封 = 3，
    # 与紧随其后的 `select == 3`（三次调用各连一次）自洽。若实跑不是 3，
    # 要改的是 docstring 里那句「至少两条」，**不是**这个数。
    assert len(fetches) == 3, f"取件那几条路没真发 FETCH，下面的断言是空转的：{fetches}"
    assert fake.sent.count(("select", "INBOX", True)) == 3, \
        "三次调用各连一次，且每次都必须 EXAMINE（readonly=True）"
    for c in fetches:
        assert "BODY.PEEK" in c[-1], f"用了非 PEEK 取件：{c}"
        assert not re.search(r"BODY\[", c[-1]), f"出现裸 BODY[]：{c}"
    bad = [c for c in fake.sent
           if c[0] == "uid" and str(c[1]).upper() in ("STORE", "COPY", "MOVE", "EXPUNGE")]
    assert bad == [], f"取详情/附件时发了变更命令：{bad}"
    assert not any(str(x).upper().startswith(("STORE", "EXPUNGE"))
                   for c in fake.sent for x in c)
```

- [ ] **Step 3: 跑一遍确认它红**

Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q -k "attachment or part_size"`
Expected: FAIL（`AttributeError: ... 'safe_attachment_name'` 等）

- [ ] **Step 4: 实现**

常量区加：

```python
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024   # 单附件上限（spec §4；保守取值，非实测）
#: 超限文案里的「20 MB」由它生成 —— 改限额时文案跟着改，两者不会对不上。
MAX_ATTACHMENT_MB = MAX_ATTACHMENT_BYTES // (1024 * 1024)
```

把 Task 4 的两个**朴素实现**换成加固版（净化 + 编解码兜底 + 异常守卫）：

```python
def safe_attachment_name(raw: str, index: int) -> str:
    """把附件名净化成一个**安全文件名**（spec §4）。

    去 NUL、去 `/` 与 `\\`、中和 `..`、去掉首尾空白与点；净化后为空 → `attachment-<index>`；
    截断 200 字符。**落盘前另有一道独立的锚点断言**（`save_attachment`），
    不靠「净化过了」这一步推理保证安全。
    """
    name = raw or ""
    name = name.replace("\x00", "")
    name = name.replace("/", "_").replace("\\", "_")
    name = name.replace("..", "_")
    # 一次 strip 掉「空白与点」的**字符集合**，不要写 `.strip().strip(".")`：
    # 那两下会把夹在点之间的空白重新露到两端（`" . . "` → `" "`，实测），
    # 于是既不算空、又留着首尾空白，与上面 docstring 的承诺不符。
    name = name.strip(" .")
    if not name:
        name = f"attachment-{index}"
    return name[:200]


def _att_name(part, index: int) -> str:
    """附件名：`get_filename()`（覆盖 RFC 2047 与 RFC 2231）再过净化。

    `get_filename()` 对某几种头给不出可读名字时由 `decode_mime_header` 兜底
    （覆盖情况见 Task 5 Step 1 的实跑记录）。解不出一律给 `attachment-<index>`，
    **绝不**编一个名字。
    """
    try:
        raw = part.get_filename() or ""
    except Exception:
        raw = ""
    return safe_attachment_name(decode_mime_header(raw), index)


def _part_bytes(part) -> bytes | None:
    """附件字节。**单一来源** —— `_part_size` 与 `fetch_attachment` 都走它。

    这样「清单里报的 size」与「下载到的长度」**结构上**不可能对不上
    （`fetch_attachment` 那条组合绑定用例因此是恒真的，但它是防线不是理由：
    将来谁绕过 `_part_bytes` 直接 `get_payload`，它会立刻红）。

    两种形状：
      - 普通叶子部件 ⇒ `get_payload(decode=True)`；
      - `message/rfc822`（「作为附件转发」的邮件）⇒ 它是 **multipart**，
        `decode=True` 给 `None`，改用内嵌消息的 `as_bytes()`。
        实测：该 part `is_multipart()` 为 True、`get_payload()` 是 `[内嵌 Message]`。
        ⚠️ **如实登记**：`as_bytes()` 是 stdlib 的**再序列化**，不是发件人发来的原始字节
        （换行与折行会被规范化）。这是 stdlib 能给的最接近的东西；别在文案/注释里
        把它说成「原始字节」。
    解不出给 `None`，**绝不编造 0**（spec §4）。
    """
    try:
        if part.get_content_type() == "message/rfc822":
            payload = part.get_payload()
            if isinstance(payload, list) and payload:
                return payload[0].as_bytes()
            return None
        data = part.get_payload(decode=True)
    except Exception:
        return None
    return data if isinstance(data, bytes) else None


def _part_size(part) -> int | None:
    """附件字节数 = `len(_part_bytes(part))`；解不出给 **`None`**，绝不编造 0（spec §4）。

    代价（如实登记）：为了量大小要把该 part 解码一次，`fetch_attachment` 里还会再解一次。
    Gmail 单封上限 25 MB，最坏情况是一次几十 MB 的内存峰值，换来的是「不编造数字」。
    """
    data = _part_bytes(part)
    return len(data) if data is not None else None


def _reject_if_too_big(size: int, name: str) -> None:
    """超限即抛。抽成纯函数是为了能用小数字把边界钉死（20 MiB 的 fixture 又慢又占内存）。"""
    if size > MAX_ATTACHMENT_BYTES:
        raise MailError(
            f"附件超过 {MAX_ATTACHMENT_MB} MB，为避免占满磁盘已停止下载。（{name}）")


def fetch_attachment(item: dict, index: int) -> tuple[str, bytes]:
    """取第 `index` 个附件，返回 `(净化后的文件名, 原始字节)`。**BODY.PEEK[]**。

    `index` 是 `fetch_detail` 报出的那个（**附件序**，不是 part 序）；越界、
    不是附件、超过 `MAX_ATTACHMENT_BYTES` 都抛 `MailError`。
    **不缓存**：每次下载重连一次、重解一次整封（spec §4）——
    代价是 N 个附件 N 次连接，换来的是进程里不驻留邮件字节。
    """
    uid = item["id"]
    conn, uidvalidity = _connect()
    try:
        _check_uidvalidity(uidvalidity)
        rows = _fetch_raw_chunk(conn, [uid])
    finally:
        _logout_quietly(conn)
    if not rows:
        raise MailError(f"没有取到编号为 {uid} 的邮件（它可能已被移走或删除）")
    msg = message_from_bytes(rows[0][1])
    parts = _attachment_parts(msg)
    # `bool` 是 `int` 的子类，必须显式排掉（`isinstance(True, int)` 为真）。
    if isinstance(index, bool) or not isinstance(index, int) or not (0 <= index < len(parts)):
        raise MailError("这封邮件里没有这个附件（清单可能已过期，请重新打开邮件）")
    part = parts[index]
    name = _att_name(part, index)
    data = _part_bytes(part)
    if data is None:
        raise MailError(f"附件内容无法解码，未下载。（{name}）")
    _reject_if_too_big(len(data), name)
    return name, data
```

- [ ] **Step 5: 跑一遍确认它绿**

Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q`
Expected: PASS（含 `fetch_detail` 那一节 —— `_att_name`/`_part_size` 换成加固版后
Task 4 的断言必须仍然绿：`a.bin`/`b.txt`、size `4`/`2`。**若 Task 4 那几条红了，
说明加固版的 happy path 被改坏了** —— 回去修，别去动 Task 4 的断言）

- [ ] **Step 6: 确认只读三件套没被破坏**

Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q -k "readonly or store or seen or flags"`
Expected: PASS（既有只读用例**一条未改**仍绿）

- [ ] **Step 7: 提交**

```bash
git add backend/mail_client.py tests/test_mail_client.py
git commit -m "feat(mail): 附件取回与名字净化；20 MiB 上限与 size 绝不为 0"
```

---

### Task 6: `save_attachment` —— UTC 日期目录、不覆盖、落盘锚点

**Files:**
- Modify: `backend/mail_client.py`（新增 `SCHOOL_MAIL_FOLDER` / `_received_date` / `_unique_path` / `save_attachment`；`from pathlib import Path`）
- Test: `tests/test_mail_client.py`（新增一节）

**Interfaces:**
- Consumes: `fetch_attachment(item, index) -> (name, bytes)`（Task 5）
- Produces: `save_attachment(item: dict, index: int, download_dir: str) -> str`（返回**实际写入的路径**）

- [ ] **Step 1: 写失败用例**

```python
# ============ 落盘：目录、不覆盖、锚点（spec §5）============

def _fake_attachment(monkeypatch, name, data):
    monkeypatch.setattr(mail_client, "fetch_attachment", lambda item, index: (name, data))


def test_save_attachment_writes_into_school_folder(tmp_path, monkeypatch):
    _fake_attachment(monkeypatch, "a.bin", b"AAAA")
    out = mail_client.save_attachment(
        {"id": "1", "received": "2026-09-17T08:31:00+00:00"}, 0, str(tmp_path))
    assert out == str(tmp_path / "学校邮件" / "2026-09-17_a.bin")
    assert (tmp_path / "学校邮件" / "2026-09-17_a.bin").read_bytes() == b"AAAA"


def test_save_attachment_creates_missing_folder(tmp_path, monkeypatch):
    """目录不存在则创建 —— 用户填的下载目录里没有「学校邮件」是常态。"""
    _fake_attachment(monkeypatch, "a.bin", b"x")
    assert not (tmp_path / "学校邮件").exists()
    mail_client.save_attachment({"id": "1", "received": ""}, 0, str(tmp_path))
    assert (tmp_path / "学校邮件").is_dir()


def test_save_attachment_never_overwrites(tmp_path, monkeypatch):
    """同名**绝不覆盖**：第二次落 `-1`，第三次落 `-2`（spec §5）。"""
    item = {"id": "1", "received": "2026-09-17T08:31:00+00:00"}
    _fake_attachment(monkeypatch, "a.bin", b"first")
    p1 = mail_client.save_attachment(item, 0, str(tmp_path))
    _fake_attachment(monkeypatch, "a.bin", b"second")
    p2 = mail_client.save_attachment(item, 0, str(tmp_path))
    _fake_attachment(monkeypatch, "a.bin", b"third")
    p3 = mail_client.save_attachment(item, 0, str(tmp_path))
    assert p1.endswith("2026-09-17_a.bin")
    assert p2.endswith("2026-09-17_a-1.bin")
    assert p3.endswith("2026-09-17_a-2.bin")
    assert Path(p1).read_bytes() == b"first"          # 第一份没被覆盖
    assert Path(p2).read_bytes() == b"second"


def test_save_attachment_uses_utc_date_of_received(tmp_path, monkeypatch):
    """日期取 `received` 的 **UTC** 日 —— 晚间 +0800 的信可能落进「次日」目录（spec §9 已登记）。"""
    _fake_attachment(monkeypatch, "a.bin", b"x")
    out = mail_client.save_attachment(
        {"id": "1", "received": "2026-09-17T23:30:00+08:00"}, 0, str(tmp_path))
    assert "2026-09-17_a.bin" in out                  # 23:30+08:00 = 15:30Z，仍是 17 日


def test_save_attachment_falls_back_to_today_on_bad_date(tmp_path, monkeypatch):
    """`received` 解不出 / 是 naive 时用**当天 UTC**，绝不猜一个日期。

    ⚠️ 每个坏值必须用**各自的子目录**：初稿在同一个 `tmp_path` 里连落三次同名文件，
    第二、三次会被 `_unique_path` 加上 `-1`/`-2`，而 `f"{today}_a.bin" in out`
    在 `2026-09-22_a-1.bin` 上**恒假**（实测：`'_a.bin'` 不是 `'_a-1.bin'` 的子串）
    ⇒ 初稿从第二次迭代起必然红。`-1` 后缀本身是 `never_overwrites` 那条的职责，别在这儿重复测。
    另：跨 UTC 午夜时 `today` 会在调用前后不同，取**调用前后两个日期都接受**，
    否则这条用例每天有极小概率无故变红。（naive 那条实测 `tzinfo is None`，
    正是 `_received_date` 判为「解不出」的依据。）
    """
    _fake_attachment(monkeypatch, "a.bin", b"x")
    for i, bad in enumerate(("", "not-a-date", "2026-09-17T08:31:00")):
        before = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out = mail_client.save_attachment({"id": "1", "received": bad}, 0,
                                          str(tmp_path / str(i)))
        after = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert any(f"{d}_a.bin" in out for d in {before, after}), out


def test_save_attachment_rejects_empty_download_dir(monkeypatch):
    _fake_attachment(monkeypatch, "a.bin", b"x")
    for bad in ("", "   "):
        with pytest.raises(mail_client.MailError):
            mail_client.save_attachment({"id": "1", "received": ""}, 0, bad)


def test_save_attachment_refuses_path_escaping_the_root(tmp_path, monkeypatch):
    """锚点断言**独立于净化**：即使净化被改坏，落盘前仍要重新确认最终路径在根之内。

    用变异注入一个逃逸名来证明这道守卫不是装饰（净化正常时它永不触发 ——
    那正是「永不触发的守卫」最容易被后来者当成冗余删掉的原因）。

    ⚠️ 注入名必须是 **四个** `..`，不是两个 —— **实测定死的**，别按直觉改回去：
    落点被拼成 `f"{日期}_{名}"`，那个 `_` 把第一段 `2026-09-17_..` 变成**普通目录名**
    （不是父引用），于是它先吃掉一个 `..`：
      - `../../evil.bin`     ⇒ `<root>/学校邮件/evil.bin` —— **仍在根内**，锚点放行；
      - `../../../evil.bin`  ⇒ `<root>/evil.bin`        —— **仍在根内**，锚点放行；
      - `../../../../evil.bin` ⇒ `<root>/../evil.bin`    —— 才真逃出去，锚点抛。
    初稿写两个 `..` ⇒ 锚点根本不触发 ⇒ 本用例**必然红**；而末尾那句
    `not (tmp_path.parent / "evil.bin").exists()` 恰好对应四个 `..`
    —— 作者本意就是四个，只是少写了两个。
    """
    monkeypatch.setattr(mail_client, "fetch_attachment",
                        lambda item, index: ("../../../../evil.bin", b"x"))
    with pytest.raises(mail_client.MailError):
        mail_client.save_attachment({"id": "1", "received": ""}, 0, str(tmp_path))
    assert not (tmp_path.parent / "evil.bin").exists()
```

用例文件顶部要 `from pathlib import Path`（若尚未导入）。

- [ ] **Step 2: 跑一遍确认它红**

Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q -k save_attachment`
Expected: FAIL —— `AttributeError: ... 'save_attachment'`

- [ ] **Step 3: 实现**

```python
#: 落盘子目录名。**固定字面量、不做 i18n** —— 它同时是磁盘路径，
#: 切界面语言不该把用户的目录挪走（spec §5）。
SCHOOL_MAIL_FOLDER = "学校邮件"


def _received_date(item: dict) -> str:
    """落盘目录用的日期 = `received` 的 **UTC** 日期；解不出用当天 UTC（spec §5）。

    用 `fromisoformat` 而不是切前 10 个字符：切串会**默默假定** `received` 是 UTC，
    而 `_iso()` 一旦换了写法（比如带偏移）切出来的就不是日期了。
    这里显式要求带时区：naive 的一律当「解不出」。
    """
    try:
        dt = datetime.fromisoformat(item.get("received") or "")
    except (TypeError, ValueError):
        dt = None
    if dt is None or dt.tzinfo is None:
        dt = datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _unique_path(target: Path) -> Path:
    """同名绝不覆盖：已存在则退到 `<stem>-1<suffix>`、`-2`…（spec §5）。"""
    if not target.exists():
        return target
    n = 1
    while True:
        cand = target.with_name(f"{target.stem}-{n}{target.suffix}")
        if not cand.exists():
            return cand
        n += 1


def save_attachment(item: dict, index: int, download_dir: str) -> str:
    """取第 `index` 个附件并落盘，返回**实际写入的路径**。

    落点 = `<download_dir>/学校邮件/<YYYY-MM-DD>_<净化名>`；同名加 `-1`/`-2` 后缀。
    落盘前最后一道是**锚点**：`resolve()` 后必须仍在 `resolve()` 后的 `download_dir` 之内
    （照抄 `files_downloader.confine_dest` 的纪律，spec §5）—— 用 `relative_to` 而不是
    前缀比较，前缀比较会把 `/a/bc` 判成在 `/a/b` 之内。
    """
    if not (download_dir or "").strip():
        raise MailError("没有设置下载目录，无法保存附件")
    root = Path(download_dir).expanduser()
    name, data = fetch_attachment(item, index)
    folder = root / SCHOOL_MAIL_FOLDER
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MailError(f"无法创建下载目录 {folder}：{exc}") from exc
    target = _unique_path(folder / f"{_received_date(item)}_{name}")
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        raise MailError("附件名落到了下载目录之外，已停止保存") from None
    try:
        target.write_bytes(data)
    except OSError as exc:
        raise MailError(f"保存附件失败：{exc}") from exc
    return str(target)
```

文件顶部导入加 `from pathlib import Path`。

- [ ] **Step 4: 跑一遍确认它绿**

Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q -k save_attachment`
Expected: PASS（7 条）
Run: `.venv/bin/python -m pytest -q`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add backend/mail_client.py tests/test_mail_client.py
git commit -m "feat(mail): save_attachment（学校邮件/日期目录、同名不覆盖、落盘锚点）"
```

---

### Task 7: 两个路由 + 请求校验（`Any` + 手写校验，不抛 422）

**Files:**
- Modify: `backend/main.py`（模型 `:50-56` 之后；路由放在 `/api/mail/list` `:1001` 之后）
- Test: `tests/test_main.py`（`test_mail_list_*` 一节之后）

**Interfaces:**
- Consumes: `mail_client.fetch_detail`（Task 4）、`mail_client.save_attachment`（Task 6）
- Produces:
  - `POST /api/mail/detail` `{id}` → `{ok:true, detail}` / `{ok:false, error}`
  - `POST /api/mail/attachment` `{id, index, download_dir}` → `{ok:true, path}` / `{ok:false, error}`

**为什么字段用 `Any` 而不是 `int` / `str`（本轮**实跑**确认过，不是印象）**：
Pydantic 2.13.5 的 lax `int` 会把 `"3"` 强转成 `3`、`3.0` 成 `3`、`True` 成 `1` —— 直接违反
spec §5 的「JSON 里来的是字符串也要拒，不许隐式转换」；而 `str` 字段遇到 `3` 会抛
`ValidationError`，FastAPI 把它变成 **422**，违反「不抛」。

判据值不止上面三个 —— `test_mail_attachment_rejects_string_index` 的坏值表是 8 个，
把 8 个全跑一遍（`.venv/bin/python`，pydantic 2.13.5）才敢说 `called == []` 成立：

| 传入 `index` | `index: int` 字段 | `index: Any` + `_mail_index` |
|---|---|---|
| `"1"` / `"0"` | **静默转成 1 / 0** | 拒 |
| `1.0` | **静默转成 1** | 拒 |
| `True` / `False` | **静默转成 1 / 0** | 拒 |
| `-1` | 收下 −1 | 拒 |
| `None` / `[0]` | 抛 `ValidationError` ⇒ **422** | 拒（200）|

**两处错互相抵消**：若有人把 `Any` 「简化」回 `int`，`"1"`/`1.0`/`True` 会静默放行
（`called == []` 那半会红）、而 `None`/`[0]` 会变成 422（`status_code == 200` 那半会红）——
两半各自抓住不同的错，缺一半就只剩一半的防护。这也是这条用例两个断言都写的原因。

- [ ] **Step 1: 写失败用例**

```python
# --- 学校邮箱：详情与附件（spec §5）---

def test_mail_detail_returns_detail(monkeypatch):
    monkeypatch.setattr(mail_client, "fetch_detail", lambda item: {
        "id": item["id"], "body": "正文",
        "attachments": [{"index": 0, "name": "a.bin", "size": 4}]})
    body = TestClient(main.app).post("/api/mail/detail", json={"id": "A"}).json()
    assert body["ok"] is True
    assert body["detail"]["body"] == "正文"
    assert set(body) == {"ok", "detail"}


def test_mail_detail_rejects_missing_or_bad_id(monkeypatch):
    """`id` 必须是非空字符串；缺 / 数字 / 空串一律 `{ok:false}`，**HTTP 200、不抛**。"""
    called = []
    monkeypatch.setattr(mail_client, "fetch_detail",
                        lambda item: called.append(item) or {"id": "", "body": "", "attachments": []})
    for payload in ({}, {"id": ""}, {"id": "   "}, {"id": 3}, {"id": None}, {"id": ["A"]}):
        r = TestClient(main.app).post("/api/mail/detail", json=payload)
        assert r.status_code == 200, payload
        assert r.json()["ok"] is False, payload
    assert called == [], "校验没过就不许调到取信层"


def test_mail_detail_tolerates_bodyless_request(monkeypatch):
    """无 body 的 POST 也要走通（前端 api() 在 body 为 undefined 时不发 body）。"""
    r = TestClient(main.app).post("/api/mail/detail")
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "detail" not in r.json()          # 不能是 FastAPI 的 422 错误体


def test_mail_detail_failure_is_200_json(monkeypatch):
    def boom(item):
        raise mail_client.MailError("邮箱已被重建")
    monkeypatch.setattr(mail_client, "fetch_detail", boom)
    r = TestClient(main.app).post("/api/mail/detail", json={"id": "A"})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "邮箱已被重建"}


def test_mail_attachment_returns_path(monkeypatch):
    seen = {}
    monkeypatch.setattr(mail_client, "save_attachment",
                        lambda item, index, d: seen.update(item=item, index=index, d=d) or "/x/a.bin")
    body = TestClient(main.app).post("/api/mail/attachment", json={
        "id": "A", "index": 1, "download_dir": "/tmp"}).json()
    assert body == {"ok": True, "path": "/x/a.bin"}
    assert seen == {"item": {"id": "A"}, "index": 1, "d": "/tmp"}


def test_mail_attachment_rejects_string_index(monkeypatch):
    """**结论承重**：`"3"` 必须拒（spec §5「不许隐式转换」）。

    Pydantic 的 lax `int` 会把 `"3"` 变成 `3` —— 实跑确认过。所以这里必须用
    手写校验；`called == []` 是这条用例承重的一半：少了它，隐式转换能全绿。
    """
    called = []
    monkeypatch.setattr(mail_client, "save_attachment",
                        lambda item, index, d: called.append(index) or "/x")
    for bad in ("1", "0", 1.0, True, False, None, -1, [0]):
        r = TestClient(main.app).post("/api/mail/attachment", json={
            "id": "A", "index": bad, "download_dir": "/tmp"})
        assert r.status_code == 200, bad
        assert r.json()["ok"] is False, bad
    assert called == [], "隐式转换会走到这里 —— 这条是承重的一半"


def test_mail_attachment_rejects_missing_fields(monkeypatch):
    monkeypatch.setattr(mail_client, "save_attachment", lambda item, index, d: "/x")
    for payload in ({}, {"id": "A"}, {"id": "A", "index": 0},
                    {"id": "A", "index": 0, "download_dir": ""},
                    {"id": "", "index": 0, "download_dir": "/tmp"}):
        r = TestClient(main.app).post("/api/mail/attachment", json=payload)
        assert r.status_code == 200, payload
        assert r.json()["ok"] is False, payload


def test_mail_attachment_failure_is_200_json(monkeypatch):
    def boom(item, index, d):
        raise mail_client.MailError("附件超过 20 MB，为避免占满磁盘已停止下载。（a.bin）")
    monkeypatch.setattr(mail_client, "save_attachment", boom)
    r = TestClient(main.app).post("/api/mail/attachment", json={
        "id": "A", "index": 0, "download_dir": "/tmp"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "超过 20 MB" in r.json()["error"]
```

- [ ] **Step 2: 跑一遍确认它红**

Run: `.venv/bin/python -m pytest tests/test_main.py -q -k "mail_detail or mail_attachment"`
Expected: FAIL —— 全部 404（路由还不存在）

- [ ] **Step 3: 实现**

`backend/main.py` 顶部导入加 `from typing import Any`（`from pydantic import BaseModel` 之前）。

模型（`MailSummaryRequest` 之后）：

```python
class MailDetailRequest(BaseModel):
    #: 字段一律 `Any` + 手写校验：`int` 会把 `"3"` 隐式转成 3、`str` 遇到 3 会抛 422
    #: —— 两条都违反 spec §5（既不许隐式转换，也不许抛 HTTP 异常）。
    id: Any = None


class MailAttachmentRequest(BaseModel):
    id: Any = None
    index: Any = None
    download_dir: Any = None
```

校验助手（模型之后）：

```python
def _mail_str(v) -> str | None:
    """请求字段必须是非空**字符串**。不做任何隐式转换（spec §5）。"""
    return v.strip() if isinstance(v, str) and v.strip() else None


def _mail_index(v) -> int | None:
    """必须是非负整数。`bool` 要**显式排掉**：`bool` 是 `int` 的子类，
    `isinstance(True, int)` 为真，不排的话 `true` 会被当成 1。字符串同样拒。"""
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v if v >= 0 else None
```

路由（`/api/mail/list` 之后）：

```python
@app.post("/api/mail/detail")
def mail_detail(req: MailDetailRequest = MailDetailRequest()):
    """取一封的正文 + 附件清单。只读，不调 LLM、不改已读状态。

    字段校验失败一律 HTTP 200 + `{ok:false, error}`（与 `/api/mail/list` 同一条通道）——
    前端 `api()` 只判 `ok`，抛 HTTP 异常只会让用户看到 422 的解析失败文案。
    `req` 带默认实例，FastAPI 据此把 body 判为**可选**（同 `/api/mail/list` 的理由）。

    「一律」的**边界**（本轮实跑量过，写下来免得被读成疏漏）：`Any` + 默认实例只挡住
    **字段层**的隐式转换与缺失；body 本身不是 JSON 对象（`"hello"`、`[1,2]`、`3`、
    畸形 JSON）时，FastAPI 在进入本函数之前就答 **422** —— 与 `/api/mail/list` 逐格一致。
    spec §5 的「不抛」只约束**字段**校验、并要求「沿用现 `:1001` 写法」，故**不修**；
    前端 `api()` 只发对象或干脆不发 body，走不到这条路径。
    """
    uid = _mail_str(req.id)
    if uid is None:
        return {"ok": False, "error": "缺少邮件编号（id）"}
    try:
        return {"ok": True, "detail": mail_client.fetch_detail({"id": uid})}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/mail/attachment")
def mail_attachment(req: MailAttachmentRequest = MailAttachmentRequest()):
    """取单个附件并落盘。只读，不改已读状态。

    落盘位置由 `mail_client.save_attachment` 决定（`<download_dir>/学校邮件/…`），
    本路由只做请求校验与错误包装。
    """
    uid = _mail_str(req.id)
    if uid is None:
        return {"ok": False, "error": "缺少邮件编号（id）"}
    index = _mail_index(req.index)
    if index is None:
        return {"ok": False, "error": "附件编号（index）必须是非负整数"}
    download_dir = _mail_str(req.download_dir)
    if download_dir is None:
        return {"ok": False, "error": "缺少下载目录（download_dir）"}
    try:
        return {"ok": True,
                "path": mail_client.save_attachment({"id": uid}, index, download_dir)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
```

- [ ] **Step 4: 跑一遍确认它绿**

Run: `.venv/bin/python -m pytest tests/test_main.py -q -k "mail_detail or mail_attachment"`
Expected: PASS（8 条）
Run: `.venv/bin/python -m pytest -q`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add backend/main.py tests/test_main.py
git commit -m "feat(mail): /api/mail/detail 与 /api/mail/attachment（Any + 手写校验，不抛 422）"
```

---

### Task 8: 前端 i18n 键 + `MAIL_DAYS` + 空态文案

**Files:**
- Modify: `frontend/i18n.js`（zh `:13-27` 一节、en `:429-443` 一节）
- Modify: `frontend/app.js`（`mailState` `:2421` 附近；`loadMail` `:3088`；`mailSummarize` `:3191`；`renderMail` 的空态 `:3127`）

**Interfaces:**
- Consumes: 无
- Produces: `MAIL_DAYS = 7`；8 个新键（zh/en 各一份）；`mail.empty` 的新值

- [ ] **Step 1: 加键（zh 与 en **逐条对应**，占位符必须同名）**

zh（插在 **`mail.ddl_incomplete`（现 `:27`）之后**，即 zh 那一节 `mail.*` 的最后一条之后；
**不要**插在 `mail.need_refresh`（`:25`）后面 —— 那会把 `mail.wrote`/`mail.ddl_incomplete`
两条夹在中间，后面按名字找键的人会以为这一节到这里就结束了）：

```js
    "mail.empty": "最近 {n} 天没有转发过来的邮件",
```
（`mail.empty` 是**改值**，不是新增；旧值「没有需要处理的邮件」删除）

```js
    "mail.detail_open": "查看详情",
    "mail.detail_failed": "打不开这封邮件：",
    "mail.empty_body": "（这封邮件没有正文）",
    "mail.attachments": "附件",
    "mail.no_attachments": "这封邮件没有附件",
    "mail.download": "下载",
    "mail.download_failed": "下载失败：",
    "mail.saved_to": "已存到 {path}",
```

en（**对应位置**：插在 `mail.ddl_incomplete`（现 `:443`）之后，与 zh 那一节的**相对次序逐条一致**
—— `check_i18n_keys.mjs` 只查键集合、不查次序，但两侧次序错开会让下一个人改键时漏改一侧）：

```js
    "mail.empty": "No forwarded mail in the last {n} days",
    "mail.detail_open": "View details",
    "mail.detail_failed": "Could not open this message: ",
    "mail.empty_body": "(no body text)",
    "mail.attachments": "Attachments",
    "mail.no_attachments": "No attachments",
    "mail.download": "Download",
    "mail.download_failed": "Download failed: ",
    "mail.saved_to": "Saved to {path}",
```

- [ ] **Step 2: `MAIL_DAYS` 与三处接线**

`frontend/app.js` 在 `let mailState = …`（`:2421`）**紧上方**加：

```js
/* 列表窗口的天数：列表请求、AI 总结、空态文案**共用这一个数** ——
   三处各写一个 7 的话，改一处就会让「空态说 7 天、实际查了 14 天」这种错静默存在。 */
const MAIL_DAYS = 7;
```

`loadMail`（`:3088`）：`{ days: 7 }` → `{ days: MAIL_DAYS }`
`mailSummarize`（`:3191`）：`days: 7, language: LANG()` → `days: MAIL_DAYS, language: LANG()`
`renderMail` 空态（`:3127`）：

```js
    box.innerHTML = `<div class="card mail-empty">${esc(t("mail.empty", { n: MAIL_DAYS }))}</div>`;
```

- [ ] **Step 3: 确认 i18n 自检的状态（预期红在死键）**

Run: `node tools/check_i18n_keys.mjs; echo "rc=$?"`
Expected: 键数 **419 → 427**（zh/en 各 +8）；**本步结束时大概率 `rc=1`，红在「死键 29 > 基线 21」**。
原因：`mail.detail_open` 等新键在 Task 9/10 才被引用。**这是预期的。**
**键与消费者必须在同一个提交里**，所以本任务不单独提交。

- [ ] **Step 4: 无提交**

Run: `git status --porcelain`
Expected: 只有 `frontend/i18n.js` 与 `frontend/app.js` 处于修改态

---

### Task 9: 行可点（含键盘）+ `#mailDetail` 弹层 + Esc

**Files:**
- Modify: `frontend/index.html`（新弹层插在 `#modulePop` `:422` 之前）
- Modify: `frontend/app.js`（`mailRowHtml` `:3135`；`mailRenderGroups` 的 `map` `:3218`；`mailItemHtml` `:3240`；`#mailList` 点击处理器 `:3278`；Esc 链 `:337-347`）
- Modify: `frontend/app.css`（`:551` 之后，学校邮箱那一节末尾）

**Interfaces:**
- Consumes: `MAIL_DAYS`、i18n 新键（Task 8）、`renderMailDetail(detail)`（**Task 10 才产出** ——
  见 Step 8 的说明：本任务结束时 `openMailDetail` 引用了一个还不存在的函数，
  所以本任务**不单独提交**）
- Produces: `mailRowAttr(idx) -> str`、`mailRowTarget(e) -> dict|null`、`openMailDetailFromEvent(e)`、`openMailDetail(m)`、`closeMailDetail()`、`mailDetailState`；HTML id `mailDetail` / `mailDetailTitle` / `mailDetailClose` / `mailDetailMeta` / `mailDetailBody` / `mailDetailAtts`

**约束**：**不删不改任何现有弹层**；`#mailDetailAtts` 上的点击处理器留给 Task 10。

- [ ] **Step 1: HTML**

在 `#modulePop`（`:422`）之前插入：

```html
<div id="mailDetail" class="modal" role="dialog" aria-modal="true" aria-labelledby="mailDetailTitle" hidden>
  <div class="modal-backdrop"></div>
  <div class="modal-card modal-lg">
    <div class="modal-head">
      <h3 id="mailDetailTitle"></h3>
      <button id="mailDetailClose" class="modal-close" data-i18n-aria="detail.close">✕</button>
    </div>
    <div class="mail-detail-meta" id="mailDetailMeta"></div>
    <div id="mailDetailBody"></div>
    <div id="mailDetailAtts"></div>
  </div>
</div>
```

（`mailDetailTitle` 由 JS 用 `textContent` 填，**不加** `data-i18n`；`detail.close` 是既有键。）

- [ ] **Step 2: CSS**

`frontend/app.css` 的学校邮箱一节末尾（`.mail-ddl` 规则之后）追加：

```css
/* 邮件详情弹层：正文与附件清单。行可点，故给指针与键盘焦点环。 */
.mail-row[role="button"] { cursor: pointer; }
.mail-detail-meta { color: var(--muted, #888); font-size: 12px; margin-bottom: 8px; }
.mail-detail-body { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px;
                    max-height: 46vh; overflow: auto; }
.mail-detail-body a { color: inherit; text-decoration: underline; }
.mail-detail-label { font-size: 12px; color: var(--muted, #888); margin: 10px 0 4px; }
.mail-att { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 13px; }
.mail-att-name { flex: 1; overflow-wrap: anywhere; }
.mail-att-size { color: var(--muted, #888); font-size: 12px; }
```

- [ ] **Step 3: 行上的 `data-m`**

`app.js` 在 `mailRowHtml` 之前加：

```js
/* 行上的 `data-m` —— 点开详情时取行的锚（`mailState.mails` 的下标）。
   **只在索引确实是整数时才写**：`groups` 是**模型输出**，`mail_index` 未必是整数
   （后端只保证越界条目被过滤，前端再兜一层）。写成 `data-m="null"` 也能被读取侧的
   `/^\d+$/` 挡住，但一个看起来像值的坏值放在 DOM 里，迟早有人去 `Number()` 它 ——
   不给就是不给。 */
function mailRowAttr(idx){
  return Number.isInteger(idx) ? ` data-m="${idx}"` : "";
}
```

`mailRowHtml` 改成（**平铺列表**那条路，下标来自 `map` 的第二个参数）：

```js
function mailRowHtml(m, i){
  return `<div class="card mail-row"${mailRowAttr(i)} role="button" tabindex="0"
      title="${escAttr(t("mail.detail_open"))}">
    <div class="mail-row-head"><span class="mail-subject">${esc(m.subject || t("mail.no_subject"))}</span></div>
    <div class="mail-row-sub">${esc(m.sender || "")} · ${esc(fmtLocal(m.received))}</div>
  </div>`;
}
```

（`mailState.mails.map(mailRowHtml)` 本来就传下标，调用点不用改。）

`mailItemHtml` 的返回（**分组列表**那条路）改成带 `data-m="${it.mail_index}"`：

```js
  const rowAttr = mailRowAttr(it.mail_index);
  return `<div class="mail-item"${rowAttr}>
    <div class="mail-item-subject">${esc(m.subject || t("mail.no_subject"))}</div>
    <div class="mail-item-sum">${esc(it.summary || "")}</div>${ddl}</div>`;
```

- [ ] **Step 4: 弹层的开与关**

`app.js` 在 `fmtLocal` 之后加：

```js
/* ---------- 学校邮箱：详情弹层 ---------- */

/* `id` 是「当前这一封」的凭据：**迟到的响应不许顶掉后打开的那一封**。
   用户点了 A、又点了 B，A 的响应晚到（IMAP 一次往返可能好几秒）——
   没有这道守卫，弹层会在 B 的标题下面显示 A 的正文，是**静默的错内容**。 */
let mailDetailState = { id: "" };

function closeMailDetail(){
  const el = $("mailDetail");
  if(el) el.hidden = true;
  mailDetailState.id = "";        // 关掉即作废
}
$("mailDetailClose").onclick = closeMailDetail;
$("mailDetail").querySelector(".modal-backdrop").addEventListener("click", closeMailDetail);

/* 行 → 详情。取行一律按 `data-m`（`mailState.mails` 的下标），**绝不**按渲染顺序或标题反推。 */
function mailRowTarget(e){
  const el = e.target && e.target.closest ? e.target.closest("[data-m]") : null;
  if(!el) return null;
  const raw = el.dataset.m;
  /* 必须过 `/^\d+$/`：`Number("")` 是 0，一个空 `data-m` 会被静默当成第 0 封。 */
  if(!/^\d+$/.test(raw || "")) return null;
  return mailState.mails[Number(raw)] || null;
}

function openMailDetailFromEvent(e){
  const m = mailRowTarget(e);
  if(m) return openMailDetail(m);
}

async function openMailDetail(m){
  if(!m || !m.id) return;
  const id = m.id;
  mailDetailState.id = id;
  $("mailDetailTitle").textContent = m.subject || t("mail.no_subject");
  $("mailDetailMeta").textContent = `${m.sender || ""} · ${fmtLocal(m.received)}`;
  $("mailDetailBody").innerHTML = `<div class="mail-empty">…</div>`;
  $("mailDetailAtts").innerHTML = "";
  $("mailDetail").hidden = false;
  const r = await api("mail/detail", { id });
  if(mailDetailState.id !== id) return;      // 期间又开了别的 / 已关掉 ⇒ 丢弃
  if(r.ok !== true){
    $("mailDetailBody").innerHTML =
      `<div class="card mail-error">${esc(t("mail.detail_failed") + (r.error || ""))}</div>`;
    $("mailDetailAtts").innerHTML = "";
    return;
  }
  renderMailDetail(r.detail || {});
}
```

- [ ] **Step 5: 把行点击挂进**既有那个**监听器 + 新增 keydown**

`#mailList` 的点击处理器开头（`:3278-3280`）：

```js
$("mailList").addEventListener("click", async e => {
  const b = e.target.closest("button[data-w]");
  /* 不是写入按钮 ⇒ 可能是点在某一「行」上（打开详情）。**必须留在同一个监听器里**：
     tools/check_mail_frontend.mjs 的 H0b 断言 `#mailList` 上**恰好一个** click 处理器，
     而它守的是「写入按钮的 data-* 取自它自己那一行」。 */
  if(!b) return openMailDetailFromEvent(e);
```

紧跟在该监听器之后加键盘可达（`role="button"` + `tabindex="0"` 的另一半）：

```js
/* 键盘可达：`Enter`/`Space` 与点击同路（spec §6）。
   `preventDefault` **只在命中行时**调用 —— 无条件吞掉 Space 会让邮件页没法用空格滚动。 */
$("mailList").addEventListener("keydown", e => {
  if(e.key !== "Enter" && e.key !== " ") return;
  const m = mailRowTarget(e);
  if(!m) return;
  e.preventDefault();
  openMailDetail(m);
});
```

- [ ] **Step 6: Esc**

`app.js` 的 Esc 链（`:346` 的 `detailModal` 那一行之后）追加一行：

```js
  if(!$("mailDetail").hidden){ closeMailDetail(); return; }
```

- [ ] **Step 7: 跑四个自检器**

Run: `node tools/check_dom_ids.mjs; echo "rc=$?"`
Expected: `rc=0`，打印「HTML 共 **163** 个 id」（157 + 6）
Run: `node tools/check_mail_frontend.mjs; echo "rc=$?"`
Expected: **可能红** —— B0/B1/B2 因为 `<div class="mail-item">` 上多了 `data-m` 而切不开。
这是**预期**的，Task 11 修切分。**若除 B0/B1/B2 之外还有红，停下来报告**，别顺手改。
Run: `node tools/check_gmail_ui.mjs; echo "rc=$?"`
Expected: `rc=0`（44 条）

- [ ] **Step 8: 无提交**

**本任务刻意不单独提交**，两个理由，都不是洁癖：

1. `openMailDetail` 的失败分支引用了 `renderMailDetail` —— 它**Task 10 才存在**。
   单独提交就是提交一个「一用就 ReferenceError」的函数。（加载时不抛：ReferenceError
   只在调用时发生，而 Task 9 结束时还没有任何东西会调它 —— 所以**自检器全绿也证明不了它可用**，
   这正是不能提交的原因：绿是绿的另一个东西。）
2. Task 8 增的 8 个 i18n 键要到 Task 9/10 才被引用，此时它们是死键，
   `check_i18n_keys.mjs` 会红在「死键 29 > 基线 21」。**键与消费者必须同一个提交**
   —— 否则这个提交里的自检是红的，而下一个人会以为红是「未完成」而不是「迟早会绿」。

Task 10 Step 4 一次性提交 Task 8 + 9 + 10 三份改动。

---

### Task 10: 详情渲染 —— 正文转义后 linkify、附件清单、下载按钮

**Files:**
- Modify: `frontend/app.js`（在 Task 9 的 `openMailDetail` 之后）
- **不改 `frontend/app.css`** —— `.mail-att*` 的样式由 Task 9 一并写好（本任务只渲染
  `<div class="mail-att">` 这套类名）。Step 4 的 `git add frontend/app.css` **要保留**：
  Task 8/9 的改动此时尚未提交，必须由这个合并提交带上。

**Interfaces:**
- Consumes: `mailDetailState`、`closeMailDetail`、`#mailDetailBody` / `#mailDetailAtts`（Task 9）；`esc` / `escAttr` / `api` / `withBusy` / `setStatus` / `downloadDir`（`util.js`）
- Produces: `renderMailDetail(detail)`、`mailLinkify(text)`、`mailAttsHtml(atts)`、`fmtBytes(n)`；`#mailDetailAtts` 上的点击处理器

- [ ] **Step 1: 实现**

**先看清一件事：正文里的链接不需要、也不许再加点击处理器。** spec §6 说「点击
`e.preventDefault()` 后走既有 `openExternal`」—— 那条路**已经存在**：`app.js:27-39`
是一个 **document 级、捕获阶段**的 `a[href]` 委托，它算 `abs` 与 `location.origin` 比对，
外部链接就交给 `window.CanvBridgeShell.openExternal`（有桥 ⇒ 系统浏览器 + `preventDefault`；
无桥 ⇒ 放行，保持原浏览器行为，与本应用其他所有链接一致）。
**再加一个 handler 会变成双重处理**（两个 handler 各自 preventDefault / 各自调 openExternal），
所以这里只负责把 `<a>` 渲染对：**href 必须是绝对的 `https://`** ——
`new URL(href, location.href).origin !== location.origin` 才成立；href 若退化成相对路径，
链接会在**应用窗口内部**跳转（静默地把用户带离界面）。K6 逐字钉住绝对 href，就是钉这件事。

```js
/* 正文 → 安全 HTML：**先按原始文本切开，再逐段转义**，最后把链接那几段换成 <a>。

   顺序不能反：先 `esc()` 再切，`&` 变成 `&amp;` 会让 `?a=1&b=2` 在 `https?://\S+`
   里断在 `&` 前（链接只剩前半截），而 `&amp;` 里的分号也会被当成正文；
   两次转义还会把 `&amp;amp;` 这类东西显出来。**绝不能把未转义的正文直接塞进 innerHTML。**

   只认 `https?://`：`javascript:` / `data:` 一律不成为链接。三道守卫是**独立**的：
   ① 这里的正则；② `href` 走 `escAttr`；③ 点击走既有的 `openExternal`（自带 /^https?:/i）。 */
function mailLinkify(text){
  if(!text) return "";
  const s = String(text);
  const re = /https?:\/\/[^\s<>"']+/g;      // 局部：带 /g 的正则不能跨调用共享 lastIndex
  let out = "", last = 0;
  for(let m; (m = re.exec(s)); ){
    /* 句末标点不算链接的一部分（`见 https://x.edu/a.` 的句号）——
       不剥的话 href 会带上它，链接照开但落到 404。只剥尾部这些，不做别的解读。 */
    const url = m[0].replace(/[.,;:!?)\]}]+$/, "");
    if(!url) continue;                       // 整段是标点 ⇒ 不是链接（exec 已前进，不会死循环）
    out += esc(s.slice(last, m.index));
    out += `<a href="${escAttr(url)}" rel="noreferrer">${esc(url)}</a>`;
    last = m.index + url.length;             // 剥掉的那几个标点留在原地，下一轮回正文
  }
  return out + esc(s.slice(last));
}

/* 字节数 → 人读串。`null` / 非数字**留白**，**绝不显示「0 B」** ——
   显示 0 B 是把「不知道」说成「是空的」（spec §4：size 解不出给 None）。 */
function fmtBytes(n){
  if(typeof n !== "number" || !isFinite(n) || n < 0) return "";
  if(n < 1024) return `${n} B`;
  if(n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/* 附件清单。`index` 取自条目自己（`a.index`），**不是**渲染序号：
   后端按同一把锚取附件，渲染序号在清单被重排时会与它对不上。取不到就早退，绝不编一个。 */
function mailAttsHtml(atts){
  const label = `<div class="mail-detail-label">${esc(t("mail.attachments"))}</div>`;
  if(!atts.length){
    return `${label}<div class="mail-empty">${esc(t("mail.no_attachments"))}</div>`;
  }
  const rows = atts.map(a => {
    const idx = a && a.index;
    if(!Number.isInteger(idx) || idx < 0) return "";
    return `<div class="mail-att">
      <span class="mail-att-name">${esc(a.name || "")}</span>
      <span class="mail-att-size">${esc(fmtBytes(a.size))}</span>
      <button type="button" class="btn btn-xs" data-att="${idx}"
        >${esc(t("mail.download"))}</button></div>`;
  }).join("");
  return label + rows;
}

function renderMailDetail(detail){
  const body = detail.body || "";
  $("mailDetailBody").innerHTML = body
    ? `<div class="mail-detail-body">${mailLinkify(body)}</div>`
    : `<div class="mail-empty">${esc(t("mail.empty_body"))}</div>`;
  $("mailDetailAtts").innerHTML = mailAttsHtml(detail.attachments || []);
}

/* 单个下载。防重入由 `withBusy` 负责（它在**第一个 await 之前**同步置灰按钮，与写入路径
   同一条纪律）；`index` 与 `id` 都取自**点中的那个按钮与当前打开的那一封**。 */
$("mailDetailAtts").addEventListener("click", async e => {
  const b = e.target.closest("button[data-att]");
  if(!b) return;
  const raw = b.dataset.att;
  if(!/^\d+$/.test(raw || "")) return;
  const id = mailDetailState.id;
  if(!id) return;
  await withBusy(t("mail.download"), b, async () => {
    const r = await api("mail/attachment",
      { id, index: Number(raw), download_dir: downloadDir() });
    if(r.ok === true) setStatus(t("mail.saved_to", { path: r.path || "" }), "ok");
    else setStatus(t("mail.download_failed") + (r.error || ""), "err");
  });
});
```

- [ ] **Step 2: 先手验一次 linkify 的两条边界**

Run:

```bash
node -e 'const esc=s=>String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");const re=/https?:\/\/[^\s<>"\x27]+/g;let out="",last=0;const s="见 https://x.edu/a?p=1&q=2. 和 javascript:alert(1)";for(let m;(m=re.exec(s));){const u=m[0].replace(/[.,;:!?)\]}]+$/,"");if(!u)continue;out+=esc(s.slice(last,m.index))+`<a href="${esc(u)}">${esc(u)}</a>`;last=m.index+u.length;}console.log(out+esc(s.slice(last)))'
```

Expected: 输出里 `href="https://x.edu/a?p=1&amp;q=2"`（**不含**句末句号）、`javascript:alert(1)` 是**纯文本**、且 `&` 显示为 `&amp;`

- [ ] **Step 3: 四个自检器 + 全量测试**

Run: `node tools/check_i18n_keys.mjs; echo "rc=$?"`
Expected: `rc=0`（死键回到 ≤ 21 —— 8 个新键至此全部被引用）
Run: `node tools/check_dom_ids.mjs; echo "rc=$?"`
Expected: `rc=0`（HTML 共 163 个 id）
Run: `node tools/check_mail_frontend.mjs; echo "rc=$?"`
Expected: 仍**只**红在 B0/B1/B2（Task 11 修）；若多红，停下报告
Run: `node tools/check_gmail_ui.mjs; echo "rc=$?"`
Expected: `rc=0`
Run: `.venv/bin/python -m pytest -q`
Expected: 全绿

- [ ] **Step 4: 提交（把 Task 8/9/10 一起落 —— 键与消费者必须同一个提交）**

```bash
git add frontend/i18n.js frontend/app.js frontend/app.css frontend/index.html
git commit -m "feat(mail): 行可点开详情弹层（正文 linkify、附件清单、单个下载）"
```

---

### Task 11: 自检器 —— 让 B 段容忍新属性，并加 K 段钉住详情弹层

**Files:**
- Modify: `tools/check_mail_frontend.mjs`（文件头注释 `:12-46`；变异开关 `:104-135`；变异替换区；B 段切分 `:285`；文件末尾 J 段之后）

**Interfaces:**
- Consumes: Task 8/9/10 的 `data-m` / `#mailDetail*` / `#mailDetailAtts` 处理器 / 空态文案 / `MAIL_DAYS`
- Produces: 55 → **78** 条断言；两个新变异开关 `--mutate-kmap`、`--mutate-kread`

- [ ] **Step 1: B 段切分改成容忍属性**

`:285`：

```js
const items = out.split(/<div class="mail-item"[^>]*>/).slice(1);
```

并在该行上方补一句注释：

```js
/* 按 <div class="mail-item" …> 切开（**属性容忍**）：Task 9 之后这一行会带上
   `data-m`，写死 `<div class="mail-item">` 会在功能完全正常时变红。 */
```

- [ ] **Step 2: 加两个变异开关的定义**

在 `:104-135` 那一片（与 `MUTATE_RM_FILTER` / `MUTATE_NO_ESC` 并列）：

```js
/* `--mutate-kmap`：行上的 `data-m` 从「`mail_index`」改成「条目序号 `ii`」
   —— 与 `--mutate-map` 同一个错位形态，只**反方向**（渲染侧 vs 读取侧）。
   `--mutate-kread`：取行不再读 `data-m`，恒取第 0 封 —— 证明 K2/K3/K4 不是恒真。 */
const MUTATE_KMAP  = process.argv.includes("--mutate-kmap");
const MUTATE_KREAD = process.argv.includes("--mutate-kread");
```

- [ ] **Step 3: 加两个变异替换**

在 `if (f === "app.js") {` 里、`MUTATE_WIDX` 那一段之后：

```js
    if (MUTATE_KMAP) {
      src = mutate(src, "mailRowAttr(it.mail_index)", "mailRowAttr(ii)", "kmap");
    }
    if (MUTATE_KREAD) {
      src = mutate(src, "const raw = el.dataset.m;", 'const raw = "0";', "kread");
    }
```

- [ ] **Step 4: 加 K 段（插在 J 段之后、最后的 `console.log` 之前）**

```js
/* K：详情弹层 —— 打开的是哪一封、正文怎么变成 HTML、附件按钮带回哪个 index、
   迟到响应不许顶掉后打开的那一封。这三条**都不在** A–J 的覆盖里，而前两条的失败模式
   是**静默**的：① B 的标题配 A 的正文；② 下下来的不是那一个附件。
   与 I 段同款：本段自带 `sandbox.fetch` 桩、跑完还原，其余 URL 一律 throw（仍是零出网）。
   全部夹具为合成串（`SYNTH-*` / `example.invalid`），不含任何真实邮件内容。
   载荷证明（**预期的**红集，由本任务 Step 6 实测核对 —— 不是实测值，实测值以那一步为准）：
     · `--mutate-kmap`（渲染侧：`data-m` 取自条目序号而非 `mail_index`）→ **K0 红**。
       K1–K4 仍绿是**对的**：`kClickRow` 喂的是**写死的** `"0"`/`"1"`，
       不读 DOM，故渲染侧的错位碰不到它 —— 这正是「渲染侧」这条变异的作用边界。
     · `--mutate-kread`（读取侧：取行恒取第 0 封）→ **K2 K3 K4 K13 K16 红**。
       K2/K3/K4 是直接命中（拿错邮件）；**K13 与 K16 是同一错位的独立症状**：
       下载按钮与 Enter 都按 `mailDetailState.id`/`mailRowTarget` 取那一封，
       取错之后它们跟着错。**K12 仍绿也是对的** —— 它只断言 index，不看 id。 */
const kBaseFetch = sandbox.fetch;
const kLog = [];
const kPending = [];
const K_DETAIL = {
  "SYNTH-K-ID-0": { ok: true, detail: { id: "SYNTH-K-ID-0", body: "A-BODY", attachments: [] } },
  "SYNTH-K-ID-1": { ok: true, detail: { id: "SYNTH-K-ID-1",
    body: 'B-BODY 见 https://example.invalid/a. <img src=x onerror=SYNTH-XSS> javascript:SYNTH-ALERT',
    attachments: [{ index: 0, name: "SYNTH-ATT-0.pdf", size: 2048 },
                  { index: 1, name: "SYNTH-ATT-1.bin", size: null }] } },
};
sandbox.fetch = (url, opts) => {
  const u = String(url);
  kLog.push({ url: u, body: JSON.parse((opts && opts.body) || "{}") });
  /* `/api/mail/detail` 的响应**手动落地**：这一段的要点之一就是「先点的晚回」，
     自动 resolve 的桩造不出那个顺序。 */
  if (u === "/api/mail/detail") return new Promise(res => kPending.push(res));
  if (u === "/api/mail/attachment")
    return Promise.resolve({ ok: true, status: 200,
      json: async () => ({ ok: true,
        path: "/tmp/SYNTH-DIR/学校邮件/2026-09-17_SYNTH-ATT-1.bin" }) });
  throw new Error("自检不该发起 fetch：" + u);
};
const kAnswer = (i, payload) => kPending[i]({ ok: true, status: 200, json: async () => payload });
/* 行的点击**一律过 `#mailList` 那个唯一的 click 处理器**（H0b 钉着它只有 1 个），
   不在探针里直接调 openMailDetail —— 那样就绕过了「点行能不能打开」这条契约。 */
const kClickRow = m => clickers[0]({ target: {
  closest: sel => (sel === "[data-m]" ? { dataset: { m: String(m) } } : null) } });

run(`
  mailState.loading = false; mailState.error = "";
  mailState.mails = [
    { id: "SYNTH-K-ID-0", subject: "SYNTH-K-SUBJ-0", sender: "s0@example.invalid", received: "2026-09-01T00:00:00Z" },
    { id: "SYNTH-K-ID-1", subject: "SYNTH-K-SUBJ-1", sender: "s1@example.invalid", received: "2026-09-02T00:00:00Z" },
  ];
  mailState.groups = [{ course: "SYNTH-K-COURSE", types: [
    { type: "SYNTH-K-TYPE", items: [{ mail_index: 1, summary: "SYNTH-K-SUM" }] }] }];
  renderMail();
  globalThis.__kList = document.getElementById("mailList").innerHTML;
`);
/* 前提断言（正例锚）：`data-m` 必须取自 `mail_index`（=1），不是条目下标（=0）。
   少了它，下面凡是用 `data-m` 取行的地方都会因为「属性压根不在」而红得莫名其妙。 */
ok(sandbox.__kList.includes('data-m="1"') && !sandbox.__kList.includes('data-m="0"'),
   `K0 前提：分组条目的 data-m 取自 mail_index（实得 ${JSON.stringify(sandbox.__kList.slice(0, 300))}）`);

const kOpenA = kClickRow(0);           // 先点第 0 封（响应挂起）
const kOpenB = kClickRow(1);           // 再点第 1 封（响应挂起）
kAnswer(1, K_DETAIL["SYNTH-K-ID-1"]);  // 后点的**先**回
await settle(kOpenB);
kAnswer(0, K_DETAIL["SYNTH-K-ID-0"]);  // 先点的**晚**回 —— 不许顶掉上面那一封
await settle(kOpenA);
run(`globalThis.__kTitle = document.getElementById("mailDetailTitle").textContent;
     globalThis.__kBody  = document.getElementById("mailDetailBody").innerHTML;
     globalThis.__kAtts  = document.getElementById("mailDetailAtts").innerHTML;
     globalThis.__kOpen  = document.getElementById("mailDetail").hidden;`);
const kDetailLog = kLog.filter(f => f.url === "/api/mail/detail");
ok(kDetailLog.length === 2, `K1 两次点击各发一次 /api/mail/detail（实得 ${kDetailLog.length} 次）`);
ok(kDetailLog[0].body.id === "SYNTH-K-ID-0" && kDetailLog[1].body.id === "SYNTH-K-ID-1",
   `K2 取的是点中那一封自己的 id（实得 ${JSON.stringify(kDetailLog.map(f => f.body.id))}）`);
ok(sandbox.__kTitle === "SYNTH-K-SUBJ-1",
   `K3 弹层标题是后点开的那一封（实得 ${JSON.stringify(sandbox.__kTitle)}）`);
ok(sandbox.__kBody.includes("B-BODY") && !sandbox.__kBody.includes("A-BODY"),
   "K4 迟到的那一封没有顶掉后打开的这一封（B 的正文在场、A 的不在场）");
ok(sandbox.__kBody.includes("&lt;img src=x onerror=SYNTH-XSS&gt;") && !sandbox.__kBody.includes("<img"),
   "K5 正文里的标签被转义成文本，不成为 DOM");
ok(sandbox.__kBody.includes('<a href="https://example.invalid/a" rel="noreferrer">https://example.invalid/a</a>'),
   `K6 https:// 成为链接，且句末的句号没被吃进 href（实得 ${JSON.stringify(sandbox.__kBody)}）`);
ok(!/href="javascript:/i.test(sandbox.__kBody) && sandbox.__kBody.includes("javascript:SYNTH-ALERT"),
   "K7 javascript: 不成为链接（原样当正文）");
ok(sandbox.__kAtts.includes('data-att="0"') && sandbox.__kAtts.includes('data-att="1"'),
   "K8 附件按钮各自带自己的 index");
ok(sandbox.__kAtts.includes("SYNTH-ATT-0.pdf") && sandbox.__kAtts.includes("2.0 KB"),
   `K9 附件名与人类可读大小（实得 ${JSON.stringify(sandbox.__kAtts)}）`);
ok(!sandbox.__kAtts.includes("0 B"), "K10 size 为 null 时留白，绝不显示「0 B」");

/* 附件下载：**点中的那个按钮**决定 index。 */
const kAttClickers = (elListeners["mailDetailAtts"] || {}).click || [];
ok(kAttClickers.length === 1, `K11 捕获到 #mailDetailAtts 上的点击处理器（实得 ${kAttClickers.length} 个）`);
const kAttStub = att => ({ dataset: { att }, disabled: false });
await settle(kAttClickers[0]({ target: {
  closest: sel => (sel === "button[data-att]" ? kAttStub("1") : null) } }));
const kAttLog = kLog.filter(f => f.url === "/api/mail/attachment");
ok(kAttLog.length === 1 && kAttLog[0].body.index === 1,
   `K12 下载的是按钮自己那一个 index（实得 ${JSON.stringify(kAttLog.map(f => f.body))}）`);
ok(kAttLog.length === 1 && kAttLog[0].body.id === "SYNTH-K-ID-1"
   && kAttLog[0].body.download_dir === sandbox.downloadDir(),
   "K13 下载请求带上那一封的 id 与设置页的下载目录");
ok(nodes["status"].textContent.includes("SYNTH-DIR"),
   `K14 成功路径把落盘路径告诉用户（实得 ${JSON.stringify(nodes["status"].textContent)}）`);

/* 键盘可达（spec §6）。判据分两半，缺一不可：
   正的 —— Enter 也能打开详情；负的 —— **不是行上的按键不许吞**（无条件 preventDefault
   会让邮件页没法用空格滚动）。 */
const kKeydowns = (elListeners["mailList"] || {}).keydown || [];
ok(kKeydowns.length === 1, `K15 捕获到 #mailList 上的 keydown 处理器（实得 ${kKeydowns.length} 个）`);
const kBeforeKey = kLog.length;
let kPrevented = 0;
kKeydowns[0]({ key: "Enter", preventDefault(){ kPrevented++; },
               target: { closest: sel => (sel === "[data-m]" ? { dataset: { m: "1" } } : null) } });
ok(kLog.length === kBeforeKey + 1 && kLog[kLog.length - 1].url === "/api/mail/detail"
   && kLog[kLog.length - 1].body.id === "SYNTH-K-ID-1",
   "K16 Enter 也能打开详情（键盘可达）");
ok(kPrevented === 1, "K17 命中行时 preventDefault（Space 不滚页面）");
kPrevented = 0;
kKeydowns[0]({ key: "Enter", preventDefault(){ kPrevented++; }, target: { closest: () => null } });
ok(kPrevented === 0, "K18 不是行上的按键不吞（preventDefault 只在命中行时调用）");

ok(sandbox.__kOpen === false, "K19 前提：详情弹层处于打开态");
/* **按 Esc 之前必须把前面几段留下的弹层状态清干净**（这不是洁癖）：
   app.js 的 Esc 链是**顺序 if**，`#loginPrompt` 那一条排在 `#mailDetail` **前面**
   —— I 段（`:589`）留着 `nodes["loginPrompt"].hidden === false`，`iDismiss()` 只是
   `$("btnLoginPromptNo").click()`、**并不直接置 hidden**。留着不管的话，这一发 Esc 会被
   前面那条分支截胡，K20 以一个与详情毫无关系的形态变红（本仓吃过同类亏：
   `mkNode` 的 `hidden` 默认为 false 时，G 段的 Esc 断言根本走不到 `#confirmPrompt`）。 */
run(`for(const id of ["loginPrompt", "confirmPrompt", "detailModal", "modulePop"])
       document.getElementById(id).hidden = true;`);
ok(nodes["loginPrompt"].hidden === true && nodes["detailModal"].hidden === true
   && nodes["modulePop"].hidden === true,
   "K19b 前提：Esc 链上排在 #mailDetail 之前的弹层都已合上（否则 K20 是替别人红）");
pressEscape();
ok(nodes["mailDetail"].hidden === true, "K20 Esc 关掉详情弹层");
sandbox.fetch = kBaseFetch;

/* 空态：转发件为空时说的是「最近 N 天没有转发过来的邮件」，不是「0 条」了事（spec §6）。
   判据正负成对：正半证明新文案在场，负半证明**旧文案不在场**（只查在场排除不了
   两处文案拼进同一格、或旧值被加回字典再用上）。旧文案只写**字面量**。 */
const K_EMPTY = "最近 7 天没有转发过来的邮件";
run(`globalThis.__kEmptyText = t("mail.empty", { n: MAIL_DAYS });`);
ok(sandbox.__kEmptyText === K_EMPTY,
   `K21 前提：t("mail.empty", {n:7}) == ${JSON.stringify(K_EMPTY)}（实得 ${JSON.stringify(sandbox.__kEmptyText)}）`);
run(`mailState.loading = false; mailState.error = ""; mailState.mails = [];
     mailState.groups = null; renderMail();
     globalThis.__kEmpty = document.getElementById("mailList").innerHTML;`);
ok(sandbox.__kEmpty.includes(K_EMPTY) && !sandbox.__kEmpty.includes("没有需要处理的邮件"),
   `K22 空态文案逐字带天数，且不含旧文案（实得 ${JSON.stringify(sandbox.__kEmpty)}）`);
```

- [ ] **Step 5: 更新文件头注释（用法与红集）**

在 `--mutate-rm-filter` / `--mutate-no-esc` 之后补：

```
 *     --mutate-kmap        行上的 data-m 从 mail_index 改成条目序号 ii（渲染侧错位）
 *     --mutate-kread       取行不再读 data-m、恒取第 0 封（读取侧错位）
```

并保留 `--mutate` 的红集说明**不变**（= 8 条：B1 B2 B4 B5 C2 E3 H1 H2），补一句：

```
 *   新增的 K 段（详情弹层）在 --mutate 下**必须全绿** —— 那四个变异都不碰详情路径。
 *   若 --mutate 的红集变了（多了或少了），先查清是谁干的，别改红集去迁就。
```

- [ ] **Step 6: 跑（每个开关各自跑，逐条核对）**

Run: `node tools/check_mail_frontend.mjs; echo "rc=$?"`
Expected: `rc=0`，`✓ 全部通过（共 78 条断言）`
Run: `node tools/check_mail_frontend.mjs --mutate-kmap; echo "rc=$?"`
Expected: `rc=1`，**K0 红**（其余 K 条必须仍绿 —— 若 K2/K3/K4 也红了，说明这两个变异的作用域重叠，要回去查）
Run: `node tools/check_mail_frontend.mjs --mutate-kread; echo "rc=$?"`
Expected: `rc=1`，**K2 K3 K4 K13 K16 红**（K12 **必须仍绿** —— 它只断言 index）。
**实测红集与本步写的对不上时，先把差异查清再改本行** —— 差一条往往意味着某条断言在
测别的东西（或压根没测到它声称的东西），而不是「本行写错了」。
Run: `node tools/check_mail_frontend.mjs --mutate; echo "rc=$?"`
Expected: `rc=1`，红的**恰好**是 B1 B2 B4 B5 C2 E3 H1 H2（8 条），K 段全绿

- [ ] **Step 7: 提交**

```bash
git add tools/check_mail_frontend.mjs
git commit -m "test(mail): 自检新增 K 段（详情弹层）与两个变异开关；B 段切分容忍属性"
```

---

### Task 12: 收尾核对（四个自检器 + 全量测试 + 只读三件套复核）

**Files:** 不改任何文件（**只跑、只核对**；发现不符就回去改，不要在这里放宽判据）

**Interfaces:**
- Consumes: 前 11 个任务的全部产出
- Produces: 一份逐条对照的核对结果

- [ ] **Step 1: 全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 全绿（基线 546 + 本轮新增 **55** 个 test item）；**没有新增 skip/xfail**
按任务的账面数（= 用例**条数**，参数化那条**按展开后的 item 数计一次**）：
Task 2 = 9、Task 3 = 3、Task 4 = **11**、Task 5 = **17**、Task 6 = 7、Task 7 = 8，合计 **55**。
**已核实的中间量**：Task 4 实现后全量 = **566 passed**（546 + 9 + 3 + 8），与逐任务账面**逐位吻合**；
Task 4 修复轮后 = **568 passed**（566 + 本轮新增的 2 条），亦逐位吻合。
⛔ 计划初稿写的 `Task 5 = 15（8 + 参数化 7）` 是**重复计数**：那 8 个 def 里**已经含**参数化那一个，
展开后应为 7 个 plain + 7 个 item = 14，不是 15（初稿的合计 50 因此也虚高 1）。
若再看到「def 数 + 展开数」这种写法，先确认两者是否重叠。
**账面数的三处增补**（都由实跑或复审触发，见各自用例的 docstring）：
- Task 4 加 2 条：`test_attachment_parts_include_forwarded_message`（`message/rfc822` 被
  `not is_multipart()` **静默丢掉**，实测该 part `is_multipart()` 为 True）、
  `test_fetch_detail_raises_when_fetch_fails`（`_fetch_raw_chunk` 的 `typ != "OK"` 分支原先无人钉）。
- Task 5 加 2 条：`test_att_name_survives_get_filename_raising`（Task 4 那条
  `survives_broken_mime` 实测解出 0 个附件 part、根本不调用 `_att_name`，守卫改由 Task 5 直接钉）、
  `test_forwarded_message_attachment_round_trips`（`_part_bytes` 的 rfc822 分支）。
- **Task 4 与 Task 5 各再加 1 条登出用例**（`test_logout_error_does_not_escape_or_mask` /
  `test_fetch_attachment_logout_error_does_not_escape`）：复审 I1 的修复**曾被实测证明完全无覆盖**
  （实现者 M10：换回裸 `conn.logout()` 后 16/16 照绿）。全仓此前**没有任何用例让 `logout` 抛过** ——
  `("logout",) in fake.sent` 那两条只钉「被调用」，裸 `logout()` 同样让它们绿。
  两个调用点各钉一条：只钉一处，另一处回退不会被发现。
账面数与实跑数对不上时，**以实跑为准，但必须先把差额查清**（多半是某条用例名里带了筛选词、
或某条被 `-k` 漏掉），不要把账面数改大去凑。


- [ ] **Step 2: 四个自检器**

Run: `node tools/check_mail_frontend.mjs; echo "rc=$?"`
Expected: `rc=0`，**78 条**（基线 55 + 23）
Run: `node tools/check_gmail_ui.mjs; echo "rc=$?"`
Expected: `rc=0`，**44 条**（未改）
Run: `node tools/check_dom_ids.mjs; echo "rc=$?"`
Expected: `rc=0`，「HTML 共 **163** 个 id」（157 + 6）
Run: `node tools/check_i18n_keys.mjs; echo "rc=$?"`
Expected: `rc=0`，键 **427** ×2（419 + 8）、死键 **≤ 21**（基线持平）

- [ ] **Step 3: 只读三件套复核（零容忍）**

Run: `grep -n "STORE\|COPY\|MOVE\|EXPUNGE\|MarkAsRead\|readonly=False" backend/mail_client.py backend/main.py`
Expected: **只**出现 `readonly=True` 那处与注释里的说明；任何一处真的 `STORE`/`COPY`/`MOVE`/`EXPUNGE` 立刻停。
Run: `grep -c "BODY.PEEK" backend/mail_client.py`
Expected: **2**（`_fetch_headers` 一处、`_fetch_raw_chunk` 一处）
Run: `grep -n "BODY\[" backend/mail_client.py`
Expected: 只出现在 `BODY.PEEK[...]` 里（无裸 `BODY[`）

- [ ] **Step 4: 凭据与隐私复核**

Run: `grep -rn "app_password\|get_gmail()" backend/mail_client.py backend/main.py`
Expected: 只有 `_connect()` 里那一次取用，且**从不**进入任何返回给前端的 dict、从不进日志/print
Run: `git log --oneline -12`
Expected: 提交信息里**没有**任何地址、密码、token、真实邮件内容
Run: `grep -rn "print(" backend/mail_client.py; echo "rc=$?"`
Expected: `rc=1`（没有任何 print）

- [ ] **Step 5: 分支与推送状态**

Run: `git status --porcelain; git branch --show-current; git log --oneline origin/main..HEAD 2>/dev/null | wc -l`
Expected: 工作区干净；分支 `main`；**本地领先、未推送**（spec §8「保持本地，先不推」）
**不要** `git push`。

- [ ] **Step 6: 把核对结果写进报告**

逐条列出：实际数字 vs 期望数字、每条命令的 `rc`、以及**任何不符**（不符一律如实报，不许改期望值）。

---

## 附：本计划刻意**不做**的事（防越界）

- 不加「显示全部邮件」开关（spec §1，方案甲）。
- 不做附件预览、不做内嵌图渲染、不做正文 HTML 原样排版；`inline` 部件不显示。
- 不改 `list_mail` / `fetch_bodies` 的键集合与类型；不改 `/api/mail/list` 的响应形状。
- 不建 Gmail 过滤器/标签；不读 `[Gmail]/All Mail`、不动 Spam 范围。
- 不自动批量落盘附件；正文里的链接只让它们**可点**，不代下载。
- 不在进程里缓存邮件字节；不加后台轮询；不加任何新凭据。
- 不动 `files_downloader.py`（它的 `_2` 编号是既有实现，本处按 spec §5 的 `-1`/`-2` 写，
  **不去统一** —— 统一是另一件事，不在本计划范围）。
```