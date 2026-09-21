# 学校邮箱取信层改走 Gmail IMAP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把学校邮箱取信层从「浏览器截获 OWA bearer token」换成「Gmail IMAP + 应用专用密码」，邮件链路上不再有浏览器。

**Architecture:** `backend/mail_client.py` 内部整体重写、**对外契约一字不改**（`list_mail(days)` / `fetch_bodies(items)`），于是 `main.py` 两个端点、`llm_client.summarize_mail`、前端 65 处引用全部不动。`credentials.py` 参数化 service 名以复用同一套钥匙串逻辑。只读由纪律升级为构造：`select(readonly=True)`（= `EXAMINE`）+ 全程 `BODY.PEEK`。

**Tech Stack:** Python 标准库 `imaplib` / `email`（无新依赖）；FastAPI；原生 JS 无构建。

**Spec:** `docs/superpowers/specs/2026-09-21-gmail-imap-mail-design.md`（本计划从它论证而来，执行时两份都要读）

## Global Constraints

以下每条都**逐字**来自 spec，每个任务的要求都隐含包含本节。

- **凭据绝不落盘、绝不回传前端、绝不出现在日志/报告/提交信息里。** 钥匙串是唯一许可的存放处。
- **应用专用密码由用户自己输入**（设置页，或 Task 1 里用户自己在终端写钥匙串）。**绝不经过对话、文件、提交、日志。**
- **绝不后台轮询**：只在用户点刷新时连一次 IMAP，连完即断。无定时任务、无启动时抓取、无连接池。
- **认证失败即停，绝不重试。**
- **只读三件套**：`select(readonly=True)`；所有取件一律 `BODY.PEEK[...]`（`BODY[...]` / `RFC822` 会置 `\Seen`）；**永不发 `STORE` / `COPY` / `MOVE` / `EXPUNGE`**。
- **不动 `DT` cookie**。
- **请求节流**：批量 `UID FETCH` 之间留间隔。
- **测试零出网**（`tests/conftest.py` 的 socket 层守卫，拦截即失败）。`imaplib.IMAP4_SSL` 在测试里**全部替身**。
- **`_on_browser_thread` 不可重入**；新取信层**不使用**它，也不使用 `_retry_once`。
- 测试命令：`.venv/bin/python -m pytest -q`。
- 分支 `main`，**保持本地，先不推**。

**数值（spec §5.1，逐字）**：`IMAP_HOST="imap.gmail.com"`、`IMAP_PORT=993`、`MAILBOX="INBOX"`、`TIMEOUT=30`、`DEFAULT_DAYS=7`、`BODY_BATCH=5`、`_THROTTLE=0.5`、`MAX_BODY_CHARS=200_000`。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `backend/mail_client.py` | 学校邮箱取信层（对外契约不变） | 内部整体重写 |
| `backend/credentials.py` | 钥匙串读写（AIMS + Gmail 两套） | 参数化 service |
| `backend/main.py` | 三个 Gmail 凭据端点 | 新增 |
| `backend/banweb.py` | 删两个只服务邮件的浏览器入口 | 小改 |
| `frontend/index.html` | 设置页 Gmail 组 | 新增一段 |
| `frontend/app.js` | 凭据读写 + 去掉 `is_read` | 小改 |
| `frontend/i18n.js` | zh/en 两套新键 | 新增 |
| `tests/test_mail_client.py` | 取信层测试 | 整套重写 |
| `tests/test_credentials.py` | 补 Gmail 版用例 | 追加 |
| `tests/test_main.py` | 收紧字段断言 | 小改 |
| `tools/check_mail_frontend.mjs` | 夹具去 `is_read` | 小改 |
| `tools/probe_gmail_imap.py` | Task 1 的一次性探针 | 新建，**Task 1 结束即删** |

### 规划期裁定（与 spec 的差异登记）

写计划时逐行比对 spec 源码发现的三处，裁定如下。**spec 是约束力的来源，本计划是它的论证**；
凡有出入都登记在此，不留暗改。

| # | spec 原文 | 本计划 | 裁定与代价 |
|---|---|---|---|
| A | §5.3 的取头命令含 `MESSAGE-ID` | **去掉**，只取 `FROM SUBJECT DATE` | 对外契约（§4）只有四个键，`MESSAGE-ID` 没有任何消费方 —— 取了不用的字段是死重量，复审（正确地）会提。**若将来要做转发件去重**，那正是它的用武之地，届时一个字符串即可加回。 |
| B | §5.3 要求 `Date:` 解不出时**退回 `INTERNALDATE`** | **已照做**（`header_item(uid, msg, internaldate=None)`） | 这不是差异，是**自审时发现的漏项**：初稿的 `received_dt` 返回 `None` → `received: ""`，而 §4 说该字段喂给前端 `new Date(iso)`。已补 `parse_internaldate()` 与两条用例。 |
| C | §5.1 常量表里**没有** `HEADER_BATCH` | **新增 `HEADER_BATCH = 50`** | §5.1 只规定了 `BODY_BATCH`（正文批大小）。取头也需要一个批大小，spec 未定；50 与旧 `PAGE_SIZE` 同量级，且头很小。纯属补 spec 留白，不是背离。 |

另：§7「不能删的那两样」、§5.7「用公开的 `strip_html()`」、§12「用户自己写钥匙串」三处
spec 更正是**早先在写计划过程中**做的，已提交在 spec 里，本计划照改后的口径执行。

### 一处贯穿全局的测试纪律

**I/O 用例不得引入真实时钟。** 任何写死日期的 fixture 撞上 `select_scope` 的
「最近 N 天」窗口，都会在若干天后自己变红 —— 时间炸弹，而且变红时离改动已经很久，
排查成本极高。做法：Task 5 的 `_install` 默认把 `select_scope` 换成恒等函数，
日期窗口交给 Task 4 的纯函数用例（那边用**注入的 `now`**，完全可控）；
`test_do_list_applies_select_scope` 单独负责「接线没断」。Task 6 同理。

---

## Task 1: 实测 spec §10 的未知项

**为什么第一个做**：spec §10.1（能不能登录）与 §10.2（**转发件保不保留原始 `From`**）是整条链路的赌注。§10.2 若不成立，课程分组要重做、§4 的 `sender` 语义要重定 —— **那是重新设计，不是改代码**。不验就写等于赌。

**Files:**
- Create: `tools/probe_gmail_imap.py`（一次性；本任务最后删掉）
- Modify: `docs/superpowers/specs/2026-09-21-gmail-imap-mail-design.md`（把结果写回 §10）

**Interfaces:**
- Consumes: 无
- Produces: 一份**结构性事实**结论，写回 spec §10；若 §10.2 为「不保留」，**停止执行本计划并上报**

**前置（由用户执行，不由你执行）**：用户需先在自己的终端把应用专用密码写进钥匙串：

```
security add-generic-password -s cityu_gmail_imap -a '<Gmail地址>' -w
```

（`-w` 后会交互式提示输入密码，不进 shell 历史。）**这一步你绝不能代跑，也不能问用户密码是什么。**

- [ ] **Step 1: 写探针**

创建 `tools/probe_gmail_imap.py`。**它只打印结构性事实，绝不打印任何邮件正文、主题原文或完整地址。**

```python
#!/usr/bin/env python3
"""一次性探针：实测 spec §10 的未知项。用完即删。

隐私纪律：只打印**结构性事实**（布尔值、条数、字符数、域名后缀、时区偏移），
绝不打印正文、主题原文或完整邮箱地址。
"""
import email
import imaplib
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

HOST, PORT, BOX = "imap.gmail.com", 993, "INBOX"


def creds():
    r = subprocess.run(["security", "find-generic-password",
                        "-s", "cityu_gmail_imap"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("钥匙串里没有 cityu_gmail_imap 项 —— 请先按计划前置说明写入")
    m = re.search(r'"acct"<blob>="([^"]*)"', r.stdout + r.stderr)
    if not m:
        sys.exit("读不到账号名")
    acct = m.group(1)
    r2 = subprocess.run(["security", "find-generic-password", "-s", "cityu_gmail_imap",
                         "-a", acct, "-w"], capture_output=True, text=True)
    if r2.returncode != 0:
        sys.exit("读不到密码")
    return acct, r2.stdout.rstrip("\n")


def main():
    acct, pw = creds()
    print(f"账号域名后缀：{acct.split('@')[-1]}")

    # §10.1 能不能登录
    try:
        c = imaplib.IMAP4_SSL(HOST, PORT, timeout=30)
        c.login(acct, pw)
    except Exception as exc:
        sys.exit(f"§10.1 登录失败：{type(exc).__name__}: {exc}")
    print("§10.1 登录成功 ✓")

    try:
        # §10.9 UIDVALIDITY 的真实形状
        typ, data = c.select(BOX, readonly=True)
        print(f"§10.9 select → {typ}；相关 untagged 键："
              f"{sorted(k for k in c.untagged_responses if k in ('UIDVALIDITY', 'EXISTS', 'FLAGS'))}")
        print(f"§10.9 UIDVALIDITY 原样：{c.untagged_responses.get('UIDVALIDITY')!r}")

        # §10.3 SINCE 的时区口径：同一天用 UTC 与本地各搜一次，看条数是否一致
        fmt = "%d-%b-%Y"
        utc_day = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(fmt)
        loc_day = (datetime.now().astimezone() - timedelta(days=1)).strftime(fmt)
        n_utc = len((c.uid("SEARCH", None, "SINCE", utc_day)[1][0] or b"").split())
        n_loc = len((c.uid("SEARCH", None, "SINCE", loc_day)[1][0] or b"").split())
        print(f"§10.3 SINCE {utc_day}(UTC) → {n_utc} 封；SINCE {loc_day}(本地) → {n_loc} 封")

        # §10.5 BODY.PEEK 不置 \Seen
        uids = (c.uid("SEARCH", None, "ALL")[1][0] or b"").split()
        if not uids:
            print("邮箱是空的，§10.5 / §10.2 / §10.8 无法实测")
            return
        newest = uids[-1]
        before = c.uid("FETCH", newest, "(FLAGS)")[1]
        c.uid("FETCH", newest, "(BODY.PEEK[])")
        after = c.uid("FETCH", newest, "(FLAGS)")[1]
        print(f"§10.5 取正文前 FLAGS 的条数={len(before)}；取后={len(after)}；"
              f"未变={before == after}")

        # §10.2 转发件保不保留原始 From + §10.8 INTERNALDATE vs Date
        typ, data = c.uid("FETCH", newest,
                          "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] INTERNALDATE)")
        raw = next((e[1] for e in data if isinstance(e, tuple) and len(e) > 1), b"")
        msg = email.message_from_bytes(raw)
        frm = parseaddr(msg.get("From") or "")[1]
        dom = frm.split("@")[-1] if "@" in frm else "(无 From)"
        print(f"§10.2 最新一封 From 的域：{dom}"
              f"（是学校域 = 保留原始 From；是 Gmail 域 = 被改写）")
        subj = msg.get("Subject") or ""
        try:
            decoded = str(make_header(decode_header(subj)))
            print(f"§10.2 主题：原始 {len(subj)} 字符 → 解码后 {len(decoded)} 字符；"
                  f"含非 ASCII={any(ord(ch) > 127 for ch in decoded)}")
        except Exception as exc:
            print(f"§10.2 主题解码抛错：{type(exc).__name__}: {exc}")
        dt = parsedate_to_datetime(msg.get("Date") or "")
        print(f"§10.8 Date 头 → {dt.isoformat() if dt else None}；"
              f"时区={'有' if dt and dt.tzinfo else '无'}")
        intd = re.search(rb'INTERNALDATE "([^"]+)"', b" ".join(
            e[0] for e in data if isinstance(e, tuple)))
        print(f"§10.8 INTERNALDATE → {intd.group(1).decode() if intd else None}")

        # §10.6 常见正文体积（用整封大小近似，不下载正文）
        typ, st = c.uid("FETCH", newest, "(RFC822.SIZE)")
        print(f"§10.6 最新一封整封大小：{st[0]!r}")
    finally:
        try:
            c.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 请用户跑它**

让用户在自己的终端跑（或在本会话里用 `!` 前缀跑）。**你需要的是它打印出来的结构性事实。**

```bash
.venv/bin/python tools/probe_gmail_imap.py
```

- [ ] **Step 3: 把结果写回 spec §10**

把每条实测结果逐条写回 spec §10，**并标注日期与「实测」**（区别于该文件顶部那句「尚未实测」的警告 —— 实测过的条目要单独标出来）。同时把 §5.5 的「待确认（§10.9）」框换成实测到的真实形状。

- [ ] **Step 4: 判定 §10.2 的走向**

- **`From` 的域是学校域** → 原始 `From` 保留，课程分组照旧，继续 Task 2。
- **`From` 的域是 Gmail 域** → **停下**。原始发件人被改写，`sender` 语义与课程分组都要重新设计。上报用户，回到 brainstorming 重做 spec §4/§4.1 与分组那节。

- [ ] **Step 5: 删掉探针并提交**

```bash
rm tools/probe_gmail_imap.py
git add docs/superpowers/specs/2026-09-21-gmail-imap-mail-design.md
git commit -m "docs: 实测 Gmail IMAP 的关键未知项（spec §10），结果写回 spec"
```

---

## Task 2: `credentials.py` 参数化 + Gmail 四个函数

**Files:**
- Modify: `backend/credentials.py`
- Test: `tests/test_credentials.py`（追加，**不改动任何既有用例**）

**Interfaces:**
- Consumes: 无
- Produces:
  - `GMAIL_SERVICE = "cityu_gmail_imap"`
  - `save_gmail(address: str, app_password: str) -> None`
  - `get_gmail() -> tuple[str, str] | None`
  - `get_gmail_address() -> str`
  - `delete_gmail() -> bool`
  - 既有 `save_credentials` / `get_credentials` / `get_username` / `delete_credentials` **签名与行为一字不变**

**为什么参数化而不是复制**：`save_credentials` 里「换用户名先删旧项」是踩出来的修复（M2），`_parse_acct` 两个流都解析是跨 macOS 版本的适配。复制一份 = 两份副本必然漂移，而漂移的是**安全相关的删除逻辑**。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_credentials.py` 末尾。注意 `_patch` 的假 `_run` 按**子命令**路由、不看 service，所以现有夹具原样可用。

```python
# --- Gmail 版（同一套逻辑，换 service）---

def test_gmail_service_name_is_distinct():
    """两个 service 必须不同 —— 否则 Gmail 密码会覆盖 AIMS 密码。"""
    assert credentials.GMAIL_SERVICE != credentials.SERVICE
    assert credentials.GMAIL_SERVICE == "cityu_gmail_imap"


def test_save_gmail_uses_gmail_service(monkeypatch):
    """service 用的是 Gmail 那个，不是 AIMS 的。"""
    calls = _patch(monkeypatch)
    credentials.save_gmail("me@gmail.com", "app-pw-16")
    add = _add_call(calls)
    assert add[add.index("-s") + 1] == credentials.GMAIL_SERVICE
    assert add[add.index("-a") + 1] == "me@gmail.com"
    assert add[add.index("-w") + 1] == "app-pw-16"


def test_save_gmail_rejects_empty(monkeypatch):
    calls = _patch(monkeypatch)
    with pytest.raises(credentials.CredentialsError):
        credentials.save_gmail("", "pw")
    with pytest.raises(credentials.CredentialsError):
        credentials.save_gmail("me@gmail.com", "")
    assert not any(a[1] == "add-generic-password" for a in calls)


def test_save_gmail_replaces_old_address(monkeypatch):
    """与 AIMS 同款 M2 修复：换地址先删旧项。"""
    calls = _patch(monkeypatch, acct="old@gmail.com")
    credentials.save_gmail("new@gmail.com", "pw")
    dele = next(a for a in calls if a[1] == "delete-generic-password")
    assert dele[dele.index("-s") + 1] == credentials.GMAIL_SERVICE
    assert dele[dele.index("-a") + 1] == "old@gmail.com"


def test_get_gmail_returns_pair(monkeypatch):
    _patch(monkeypatch, acct="me@gmail.com", pw="app-pw-16")
    assert credentials.get_gmail() == ("me@gmail.com", "app-pw-16")


def test_get_gmail_none_when_missing(monkeypatch):
    _patch(monkeypatch, acct_rc=44)
    assert credentials.get_gmail() is None


def test_gmail_address_is_read_from_gmail_service(monkeypatch):
    """get_gmail_address 查的是 Gmail 的 service。"""
    calls = _patch(monkeypatch, acct="me@gmail.com")
    assert credentials.get_gmail_address() == "me@gmail.com"
    find = next(a for a in calls if a[1] == "find-generic-password" and "-w" not in a)
    assert find[find.index("-s") + 1] == credentials.GMAIL_SERVICE


def test_delete_gmail_scopes_account(monkeypatch):
    calls = _patch(monkeypatch, acct="me@gmail.com")
    assert credentials.delete_gmail() is True
    dele = next(a for a in calls if a[1] == "delete-generic-password")
    assert dele[dele.index("-s") + 1] == credentials.GMAIL_SERVICE
    assert dele[dele.index("-a") + 1] == "me@gmail.com"


def test_aims_save_still_uses_aims_service(monkeypatch):
    """参数化之后 AIMS 那条路没被带偏。"""
    calls = _patch(monkeypatch)
    credentials.save_credentials("sc123456", "s3cret")
    add = _add_call(calls)
    assert add[add.index("-s") + 1] == credentials.SERVICE
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_credentials.py -q
```

预期：`test_gmail_service_name_is_distinct` 等因 `AttributeError: module 'backend.credentials' has no attribute 'GMAIL_SERVICE'` 失败。

- [ ] **Step 3: 参数化实现**

在 `backend/credentials.py` 中：

```python
SERVICE = "cityu_aims_login"
GMAIL_SERVICE = "cityu_gmail_imap"
```

把四个内部函数加 service 参数（**默认值 = `SERVICE`，既有调用点全不动**）：

```python
def _find_account(service: str = SERVICE) -> str:
    """查 service 当前命中的账号（首个匹配）；未存过返回空串。"""
    r = _run(["security", "find-generic-password", "-s", service])
    if r.returncode != 0:
        return ""
    return _parse_acct(r)


def _save(service: str, username: str, password: str, empty_msg: str) -> None:
    """把账号密码写入钥匙串（-U 已存在则覆盖）。

    改账号前先把旧账号的项删掉，避免 service 下残留两条、后续读/删命中歧义
    （M2：换了用户名后钥匙串里新旧各一条，App 却一直读到旧账号）。
    """
    if not username or not password:
        raise CredentialsError(empty_msg)
    old = _find_account(service)
    if old and old != username:
        _run(["security", "delete-generic-password", "-s", service, "-a", old])
    r = _run(["security", "add-generic-password", "-s", service,
              "-a", username, "-w", password, "-U"])
    if r.returncode != 0:
        raise CredentialsError("保存到钥匙串失败：" +
                               (r.stderr.strip() or r.stdout.strip() or "未知错误"))


def _load(service: str) -> tuple[str, str] | None:
    """返回 (username, password)；未存过或已删除则返回 None。

    密码按「读到的账号」配对查询（find ... -a <username> -w），防止 service 下
    有多条时把账号 A 的密码读成账号 B 的。
    """
    username = _find_account(service)
    if not username:
        return None
    r = _run(["security", "find-generic-password", "-s", service, "-a", username, "-w"])
    if r.returncode != 0:
        return None
    password = r.stdout.rstrip("\n")
    return (username, password) if password else None


def _delete(service: str) -> bool:
    """删除已存凭据。返回是否真的有被删的项。

    先查出当前账号再按 -a 删，确保删的是同一条、不会误删其它 service 的项。
    """
    username = _find_account(service)
    if not username:
        return False
    r = _run(["security", "delete-generic-password", "-s", service, "-a", username])
    return r.returncode == 0
```

四个既有公开函数变成薄包装（**行为一字不变**）：

```python
def save_credentials(username: str, password: str) -> None:
    _save(SERVICE, username, password, "AIMS 账号和密码都不能为空")


def get_credentials() -> tuple[str, str] | None:
    return _load(SERVICE)


def get_username() -> str:
    """只取账号（不读密码），供前端设置页回显。未存过返回空串。"""
    return _find_account(SERVICE)


def delete_credentials() -> bool:
    return _delete(SERVICE)
```

新增 Gmail 四个：

```python
# --- Gmail（学校邮箱转发目标）---

def save_gmail(address: str, app_password: str) -> None:
    """存 Gmail 地址与应用专用密码。密码由用户在设置页输入，绝不落盘为文件。"""
    _save(GMAIL_SERVICE, address, app_password, "Gmail 地址和应用专用密码都不能为空")


def get_gmail() -> tuple[str, str] | None:
    """返回 (地址, 应用专用密码)；未存过返回 None。"""
    return _load(GMAIL_SERVICE)


def get_gmail_address() -> str:
    """只取地址（不读密码），供前端设置页回显。"""
    return _find_account(GMAIL_SERVICE)


def delete_gmail() -> bool:
    return _delete(GMAIL_SERVICE)
```

同时更新模块 docstring：说明现在存两套 service，以及「新增一个凭据」是 spec §9 的显式修订。

- [ ] **Step 4: 跑测试**

```bash
.venv/bin/python -m pytest tests/test_credentials.py -q
```

预期：全绿，**既有 14 条一条不少**。

- [ ] **Step 5: 变异验证（双向）**

对每条新不变式跑一次变异，确认对应用例真的会红：

| 变异 | 应红的用例 |
|---|---|
| `GMAIL_SERVICE = SERVICE` | `test_gmail_service_name_is_distinct` |
| `save_gmail` 里改传 `SERVICE` | `test_save_gmail_uses_gmail_service` |
| `_save` 里删掉「先删旧账号」那三行 | `test_save_gmail_replaces_old_address` **和** `test_save_credentials_replaces_old_account` |
| `get_gmail_address` 改用 `_find_account(SERVICE)` | `test_gmail_address_is_read_from_gmail_service` |

**第三行是重点**：一条变异弄红 AIMS 与 Gmail 两个用例，说明参数化没有把两条路拆散。

- [ ] **Step 6: 提交**

```bash
git add backend/credentials.py tests/test_credentials.py
git commit -m "feat(credentials): 参数化 service，新增 Gmail 应用专用密码的一组"
```

---

## Task 3: 三个 Gmail 凭据端点

**Files:**
- Modify: `backend/main.py`（`AimsCredentials` 模型附近 + 在 `/api/mail/list` 之前）
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `credentials.save_gmail` / `get_gmail_address` / `delete_gmail`（Task 2）
- Produces:
  - `POST /api/mail/gmail/credentials` ← `{address, app_password}` → `{ok}`
  - `GET  /api/mail/gmail/credentials/status` → `{ok, has_credentials, address}`
  - `DELETE /api/mail/gmail/credentials` → `{ok}`
  - 响应体**任何情况下都不含密码**（键集合白名单钉死，见下）

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_main.py`。照抄该文件既有的 AIMS 凭据用例写法（用 `TestClient` + `monkeypatch` 打桩 `credentials`）。

```python
def test_gmail_credentials_status_empty_when_unset(monkeypatch):
    monkeypatch.setattr(credentials, "get_gmail_address", lambda: "")
    r = TestClient(main.app).get("/api/mail/gmail/credentials/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["has_credentials"] is False
    assert body["address"] == ""
    # 键集合白名单：新增任何键都弄红它。否定式断言（"password" not in ...）
    # 会给假保证 —— 见 memory: test-specs-need-positive-assertions 第 5 条。
    assert set(body) == {"ok", "has_credentials", "address"}


def test_gmail_credentials_status_echoes_address(monkeypatch):
    monkeypatch.setattr(credentials, "get_gmail_address", lambda: "me@gmail.com")
    body = TestClient(main.app).get("/api/mail/gmail/credentials/status").json()
    assert body["has_credentials"] is True
    assert body["address"] == "me@gmail.com"
    assert set(body) == {"ok", "has_credentials", "address"}


def test_gmail_save_passes_address_and_password(monkeypatch):
    seen = {}
    monkeypatch.setattr(credentials, "save_gmail",
                        lambda a, p: seen.update(address=a, password=p))
    r = TestClient(main.app).post("/api/mail/gmail/credentials",
                                  json={"address": " me@gmail.com ", "app_password": "pw16"})
    assert r.json() == {"ok": True}
    assert seen == {"address": "me@gmail.com", "password": "pw16"}


def test_gmail_save_error_is_reported_not_raised(monkeypatch):
    def boom(a, p):
        raise credentials.CredentialsError("钥匙串拒绝写入")
    monkeypatch.setattr(credentials, "save_gmail", boom)
    body = TestClient(main.app).post("/api/mail/gmail/credentials",
                                     json={"address": "me@gmail.com",
                                           "app_password": "pw16"}).json()
    assert body["ok"] is False
    assert "钥匙串拒绝写入" in body["error"]


def test_gmail_delete_ok(monkeypatch):
    monkeypatch.setattr(credentials, "delete_gmail", lambda: True)
    body = TestClient(main.app).delete("/api/mail/gmail/credentials").json()
    assert body == {"ok": True}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_main.py -q -k gmail
```

预期：全部 404（端点不存在）。

- [ ] **Step 3: 实现端点**

在 `backend/main.py` 里 `AimsCredentials` 之后加模型：

```python
class GmailCredentials(BaseModel):
    address: str
    app_password: str
```

在 `/api/mail/list` 之前加三个端点（照 AIMS 三件套的写法，**永不返回密码**）：

```python
@app.post("/api/mail/gmail/credentials")
def gmail_save_credentials(req: GmailCredentials):
    """把 Gmail 地址与应用专用密码存入本机钥匙串（只存本地，不返回密码）。

    密码由用户在此输入，绝不经过对话/文件/日志 —— spec §6.4。
    """
    try:
        credentials.save_gmail(req.address.strip(), req.app_password)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/mail/gmail/credentials/status")
def gmail_credentials_status():
    """返回是否已存 Gmail 凭据 + 地址（永不返回密码）。"""
    try:
        address = credentials.get_gmail_address()
        return {"ok": True, "has_credentials": bool(address), "address": address}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.delete("/api/mail/gmail/credentials")
def gmail_delete_credentials():
    """清除已存的 Gmail 地址与应用专用密码。"""
    try:
        credentials.delete_gmail()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
```

- [ ] **Step 4: 跑测试**

```bash
.venv/bin/python -m pytest tests/test_main.py -q -k gmail
```

预期：5 条全绿。

- [ ] **Step 5: 提交**

```bash
git add backend/main.py tests/test_main.py
git commit -m "feat(mail): Gmail 凭据的三个端点（存取删，永不返回密码）"
```

---

## Task 4: 取信层纯函数（头部解码 / MIME / 日期）

这一层**不碰网络**，是 Task 5/6 的地基。全程 TDD，每个函数一个测试周期。

**Files:**
- Modify: `backend/mail_client.py`（**先只加，不删 OWA 层** —— 删是 Task 5 的事）
- Test: `tests/test_mail_client.py`（**暂时追加**；Task 5 会连同 OWA 用例一起整体重写）

**Interfaces:**
- Consumes: `canvas_client.strip_html`（`backend/canvas_client.py:254`，公开函数）
- Produces:
  - `decode_mime_header(raw: str | None) -> str`
  - `sender_name(raw: str | None) -> str`
  - `_parse_ts(raw: str | None) -> datetime | None`（**保留函数名，改语义**）
  - `received_dt(msg) -> datetime | None`
  - `parse_internaldate(raw: str | None) -> datetime | None`
  - `_iso(dt) -> str`
  - `header_item(uid: str, msg, internaldate: datetime | None = None) -> dict`
  - `message_body(msg) -> str`
  - `select_scope(items, now, days=DEFAULT_DAYS) -> list[dict]`（**保留 fail-open，删「未读」分支**）

- [ ] **Step 1: 写失败的测试**

**先把既有测试文件里围绕 OWA 的部分留着不动**，在文件末尾追加本节的新用例。所需的 import 补到文件头：

```python
import email
from email.message import EmailMessage
```

```python
# ============ Gmail IMAP 取信层：纯函数（spec §4/§5.3/§5.6/§5.7）============

def _msg(subject="", sender="", date="", body="", ctype="text/plain", charset="utf-8"):
    m = EmailMessage()
    if subject:
        m["Subject"] = subject
    if sender:
        m["From"] = sender
    if date:
        m["Date"] = date
    m.set_content(body or "", subtype=ctype.split("/")[-1], charset=charset)
    return m


# --- RFC 2047 头部解码：Gmail 上中文主题是常态，不解码就是乱码 ---

def test_decode_mime_header_decodes_base64_utf8():
    """=?UTF-8?B?…?= 必须解出中文，逐字钉住。"""
    raw = "=?UTF-8?B?5L2c5Lia5oiq5q2i5pel5pyf?="
    assert mail_client.decode_mime_header(raw) == "作业截止日期"


def test_decode_mime_header_decodes_quoted_printable():
    assert mail_client.decode_mime_header("=?UTF-8?Q?=E6=B5=8B=E8=AF=95?=") == "测试"


def test_decode_mime_header_passes_plain_through():
    assert mail_client.decode_mime_header("Assignment 3") == "Assignment 3"


def test_decode_mime_header_handles_empty_and_none():
    assert mail_client.decode_mime_header("") == ""
    assert mail_client.decode_mime_header(None) == ""


def test_decode_mime_header_joins_adjacent_encoded_words():
    """相邻编码字之间的空白要吃掉，不能留缝。"""
    raw = "=?UTF-8?B?5L2c5Lia?= =?UTF-8?B?5oiq5q2i?="
    assert mail_client.decode_mime_header(raw) == "作业截止"


def test_decode_mime_header_returns_something_on_garbage():
    """解不出也不能抛 —— 返回 str 即可（值本身不钉，因为 fallback 形态是实现细节）。"""
    out = mail_client.decode_mime_header("=?UTF-8?B?!!!not-base64!!!?=")
    assert isinstance(out, str)


# --- 发件人：显示名优先，退回地址 ---

def test_sender_name_prefers_display_name():
    assert mail_client.sender_name("CityU Registry <reg@cityu.edu.hk>") == "CityU Registry"


def test_sender_name_decodes_encoded_display_name():
    """显示名是编码字时必须解出来。"""
    raw = "=?UTF-8?B?6ICB5biI?= <t@cityu.edu.hk>"
    assert mail_client.sender_name(raw) == "老师"


def test_sender_name_falls_back_to_address():
    assert mail_client.sender_name("<reg@cityu.edu.hk>") == "reg@cityu.edu.hk"


def test_sender_name_empty_input():
    assert mail_client.sender_name("") == ""
    assert mail_client.sender_name(None) == ""


# --- 日期：RFC 2822 → 带时区 ISO（UTC）---

def test_parse_ts_converts_to_utc_aware():
    dt = mail_client._parse_ts("Thu, 17 Sep 2026 16:31:00 +0800")
    assert dt is not None
    assert dt.tzinfo is not None
    assert mail_client._iso(dt) == "2026-09-17T08:31:00+00:00"


def test_iso_keeps_timezone_offset():
    """格式必须带时区 —— 前端 fmtLocal 解析不出会原样显示，所以这点会肉眼可见。"""
    dt = mail_client._parse_ts("Thu, 17 Sep 2026 08:31:00 +0000")
    assert mail_client._iso(dt).endswith("+00:00")


def test_parse_ts_rejects_naive():
    """缺时区的返回 None：naive 与 aware 的 cutoff 比较会抛 TypeError，
    那是唯一一条 fail-closed 路径，单封坏邮件能炸穿整个 select_scope。"""
    assert mail_client._parse_ts("17 Sep 2026 08:31:00") is None


def test_parse_ts_rejects_garbage():
    assert mail_client._parse_ts("???") is None
    assert mail_client._parse_ts("") is None
    assert mail_client._parse_ts(None) is None


def test_received_dt_reads_date_header():
    m = _msg(date="Thu, 17 Sep 2026 16:31:00 +0800")
    assert mail_client._iso(mail_client.received_dt(m)) == "2026-09-17T08:31:00+00:00"


def test_received_dt_none_when_no_date():
    assert mail_client.received_dt(_msg()) is None


# --- INTERNALDATE：Date 头缺失时的退路（spec §5.3）---

def test_parse_internaldate_reads_imap_format():
    """IMAP 的 INTERNALDATE 是日月年短横线、带偏移，须换算成 UTC。"""
    dt = mail_client.parse_internaldate("17-Sep-2026 16:31:00 +0800")
    assert mail_client._iso(dt) == "2026-09-17T08:31:00+00:00"


def test_parse_internaldate_rejects_garbage():
    assert mail_client.parse_internaldate("???") is None
    assert mail_client.parse_internaldate("") is None
    assert mail_client.parse_internaldate(None) is None


def test_header_item_falls_back_to_internaldate():
    """**Date 头缺失时不能给空串** —— 那会让前端 new Date("") 拿到 Invalid Date。
    退到 INTERNALDATE（spec §5.3）。"""
    m = _msg(subject="无日期头")
    it = mail_client.header_item("9", m,
                                 mail_client.parse_internaldate("17-Sep-2026 16:31:00 +0800"))
    assert it["received"] == "2026-09-17T08:31:00+00:00"


def test_header_item_prefers_date_header_over_internaldate():
    """两者都在时用 Date 头（原始发信时间），不用 INTERNALDATE（落进 Gmail 的时刻）。"""
    m = _msg(date="Thu, 10 Sep 2026 00:00:00 +0000")
    it = mail_client.header_item("9", m,
                                 mail_client.parse_internaldate("17-Sep-2026 16:31:00 +0800"))
    assert it["received"] == "2026-09-10T00:00:00+00:00"


# --- header_item：组装对外契约的条目 ---

def test_header_item_shape_exactly_four_keys():
    """键集合白名单 —— is_read 已删除，多一个少一个都要红。"""
    m = _msg(subject="作业", sender="老师 <t@cityu.edu.hk>",
             date="Thu, 17 Sep 2026 16:31:00 +0800")
    it = mail_client.header_item("42", m)
    assert set(it) == {"id", "subject", "sender", "received"}
    assert it["id"] == "42"
    assert it["subject"] == "作业"
    assert it["sender"] == "老师"
    assert it["received"] == "2026-09-17T08:31:00+00:00"


def test_header_item_survives_missing_headers():
    it = mail_client.header_item("7", _msg())
    assert it == {"id": "7", "subject": "", "sender": "", "received": ""}


# --- select_scope：纯日期窗口，fail-open ---

def test_select_scope_keeps_recent():
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    items = [{"received": "2026-09-16T00:00:00+00:00"},
             {"received": "2026-09-01T00:00:00+00:00"}]
    assert [i["received"] for i in mail_client.select_scope(items, now, 7)] == \
        ["2026-09-16T00:00:00+00:00"]


def test_select_scope_keeps_unparseable_fail_open():
    """坏日期保留 —— 宁可多给一封，不要静默丢件。"""
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    items = [{"received": "???"}, {"received": ""}]
    assert len(mail_client.select_scope(items, now, 7)) == 2


def test_select_scope_has_no_unread_branch():
    """未读不再影响结果：老邮件只要超出窗口就丢弃，不管它读没读。

    （is_read 键已从契约删除；这里刻意塞一个 is_read=False 的老邮件证明它不再被保留。）
    """
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    items = [{"received": "2026-01-01T00:00:00+00:00", "is_read": False}]
    assert mail_client.select_scope(items, now, 7) == []


# --- 正文抽取：MIME + charset + HTML ---

def test_message_body_plain():
    m = EmailMessage()
    m.set_content("正文内容", subtype="plain", charset="utf-8")
    assert "正文内容" in mail_client.message_body(m)


def test_message_body_prefers_plain_in_alternative():
    """multipart/alternative 里优先 text/plain，不取 HTML。"""
    m = EmailMessage()
    m.set_content("纯文本版", subtype="plain", charset="utf-8")
    m.add_alternative("<p>HTML 版</p>", subtype="html", charset="utf-8")
    body = mail_client.message_body(m)
    assert "纯文本版" in body
    assert "HTML 版" not in body


def test_message_body_falls_back_to_html_as_text():
    """只有 HTML 时转纯文本，标签不能留在正文里。"""
    m = EmailMessage()
    m.set_content("<p>段落一</p><p>段落二</p>", subtype="html", charset="utf-8")
    body = mail_client.message_body(m)
    assert "段落一" in body and "段落二" in body
    assert "<p>" not in body


def test_message_body_skips_attachments():
    """附件内容绝不能混进正文。"""
    m = EmailMessage()
    m.set_content("正文", subtype="plain", charset="utf-8")
    m.add_attachment(b"ATTACHMENT-BYTES", maintype="application",
                     subtype="octet-stream", filename="a.bin")
    body = mail_client.message_body(m)
    assert "正文" in body
    assert "ATTACHMENT-BYTES" not in body


def test_message_body_decodes_base64_chinese():
    """中文正文常是 base64 + utf-8。"""
    import base64
    m = EmailMessage()
    m.set_content("你好世界", subtype="plain", charset="utf-8")
    m.replace_header("Content-Transfer-Encoding", "base64")
    m.set_payload(base64.b64encode("你好世界".encode()).decode())
    assert "你好世界" in mail_client.message_body(m)


def test_message_body_unknown_charset_does_not_raise():
    m = EmailMessage()
    m.set_content("x", subtype="plain", charset="utf-8")
    m.set_param("charset", "no-such-charset")
    assert isinstance(mail_client.message_body(m), str)


def test_message_body_empty_message_is_empty_string():
    assert mail_client.message_body(EmailMessage()) == ""
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_mail_client.py -q
```

预期：新用例因 `AttributeError` 失败（`decode_mime_header` 等不存在）。

- [ ] **Step 3: 实现**

在 `backend/mail_client.py` 里加 import 与新函数。**`_parse_ts` 与 `select_scope` 是就地改写既有函数**（保留名字）。

```python
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from backend.canvas_client import strip_html      # spec §5.7：公开函数，直接用
```

```python
# ---------- 头部解码（spec §5.7）----------

def decode_mime_header(raw: str | None) -> str:
    """解 RFC 2047 编码字（=?UTF-8?B?…?=）。解不出就原样返回，绝不编造。

    Gmail 上中文主题/发件人是编码字属常态而非边缘情况，不做这一步就是乱码。
    """
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def sender_name(raw: str | None) -> str:
    """显示名优先，退回邮箱地址（口径与旧 OWA 版一致）。"""
    name, addr = parseaddr(raw or "")
    name = decode_mime_header(name)
    return name or addr


# ---------- 日期（spec §5.3：received 取 Date 头，转 UTC 带时区）----------

def _parse_ts(raw: str | None) -> datetime | None:
    """解析 RFC 2822 Date 头（如 Thu, 17 Sep 2026 08:31:00 +0800）。

    解析不出、或缺时区（naive）都返回 None。naive 返回 None 是刻意的：
    它与 aware 的 cutoff 比较会抛 TypeError，而那是本文件唯一一条 fail-closed
    路径 —— 单封坏邮件就能炸穿整个 select_scope。归到 None 即走「保留」分支。

    （旧版解析的是 OWA 的 ISO 时间戳；语义已改，函数名保留。）
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if ts is None or ts.tzinfo is None:
        return None
    return ts.astimezone(timezone.utc)


def received_dt(msg) -> datetime | None:
    """取邮件的「发送时间」。用 Date 头（原始发信时间），解不出返回 None。"""
    return _parse_ts(msg.get("Date"))


def parse_internaldate(raw: str | None) -> datetime | None:
    """解析 IMAP 的 INTERNALDATE（如 17-Sep-2026 08:31:00 +0000）。

    格式是 IMAP 专有的（日月年用短横线、无星期），用 strptime 把它钉死；
    带偏移的返回 UTC aware，解析不出（含不带偏移的串）返回 None。
    注：`parsedate_to_datetime` 同样吃得下这个格式（spec §10.8 有实测），
    保留手写版本是为了格式显式，不是因为标准库解析不了。
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.strptime(raw.strip(), "%d-%b-%Y %H:%M:%S %z")
    except (TypeError, ValueError):
        return None
    return ts.astimezone(timezone.utc)


def _iso(dt: datetime | None) -> str:
    """带时区的 ISO 8601（UTC），形如 2026-09-17T08:31:00+00:00。

    前端 fmtLocal() 解析不出会**原样显示** —— 格式错了会以肉眼可见的丑样子
    暴露，而不是静默。所以这里不需要额外兜底。
    """
    return dt.astimezone(timezone.utc).isoformat() if dt else ""


def header_item(uid: str, msg, internaldate: datetime | None = None) -> dict:
    """把一封邮件的头组装成对外契约的条目（键集合：id/subject/sender/received）。

    时间取 Date 头（原始发信时间）；**解不出时退回 INTERNALDATE**（邮件落进 Gmail
    的时刻），绝不返回空串 —— 空串会让前端 new Date("") 得到 Invalid Date，
    那是静默的坏值。两个都没有才给空串，此时 select_scope 会 fail-open 保留它。
    """
    return {
        "id": uid,
        "subject": decode_mime_header(msg.get("Subject")),
        "sender": sender_name(msg.get("From")),
        "received": _iso(received_dt(msg) or internaldate),
    }


# ---------- 正文（spec §5.7）----------

def _part_text(part) -> str:
    """解码单个 MIME part 的文本。charset 认不出就 replace，绝不抛。"""
    try:
        data = part.get_payload(decode=True)
    except Exception:
        return ""
    if not data:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return data.decode(charset, errors="replace")
    except (LookupError, ValueError):
        return data.decode("utf-8", errors="replace")


def message_body(msg) -> str:
    """抽正文纯文本。

    multipart/alternative 优先 text/plain；跳过带 Content-Disposition: attachment
    的 part；只有 HTML 就用 canvas_client.strip_html 转纯文本。
    """
    if msg.is_multipart():
        plains, htmls = [], []
        for part in msg.walk():
            if part.is_multipart():
                continue
            if (part.get_content_disposition() or "") == "attachment":
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                plains.append(_part_text(part))
            elif ctype == "text/html":
                htmls.append(_part_text(part))
        if any(t.strip() for t in plains):
            return "\n".join(t for t in plains if t.strip())
        if htmls:
            return strip_html("\n".join(htmls))
        return ""
    if msg.get_content_type() == "text/html":
        return strip_html(_part_text(msg))
    return _part_text(msg)
```

**改写** `select_scope`（删「未读」分支，保留 fail-open）：

```python
def select_scope(items: list[dict], now: datetime,
                 days: int = DEFAULT_DAYS) -> list[dict]:
    """取「最近 days 天」内的邮件。

    received 解析不出来的条目按「保留」处理 —— 宁可多给一封，不要静默丢件。

    没有「未读」分支：转发过来的邮件在 Gmail 里恒为未读，而只读访问永不改变它，
    那个分支恒真、等于没有（spec §4.1）。
    """
    cutoff = now - timedelta(days=days)
    return [it for it in items
            if (ts := _parse_ts(it.get("received", ""))) is None or ts >= cutoff]
```

- [ ] **Step 4: 跑测试**

```bash
.venv/bin/python -m pytest tests/test_mail_client.py -q
```

预期：**新用例全绿**；OWA 的旧用例此时应仍绿（OWA 层还没删）。若有旧用例因为 `_parse_ts` / `select_scope` 语义改变而红 —— 那是预期的，把它们记下来，Task 5 会连同 OWA 层一起删掉。

- [ ] **Step 5: 变异验证**

| 变异 | 应红的用例 |
|---|---|
| `decode_mime_header` 改成 `return raw or ""`（不解码） | `test_decode_mime_header_decodes_base64_utf8`、`..._quoted_printable`、`..._joins_adjacent_encoded_words`、`test_sender_name_decodes_encoded_display_name` |
| `sender_name` 改成 `return raw or ""` | `test_sender_name_prefers_display_name`、`..._falls_back_to_address` |
| `_parse_ts` 改回 `fromisoformat`（不再吃 RFC 2822） | `test_parse_ts_converts_to_utc_aware`、`test_received_dt_reads_date_header` |
| `_parse_ts` 去掉 `astimezone(timezone.utc)` | `test_parse_ts_converts_to_utc_aware` |
| `_parse_ts` 的 `ts.tzinfo is None` 判断改成 `False`（接受 naive） | `test_parse_ts_rejects_naive` |
| `_iso` 去掉 `astimezone` | `test_iso_keeps_timezone_offset` |
| `select_scope` 改回丢弃坏日期 | `test_select_scope_keeps_unparseable_fail_open` |
| `select_scope` 加回 `if not it.get("is_read"):` 分支 | `test_select_scope_has_no_unread_branch` |
| `message_body` 里把 `plains` 与 `htmls` 优先级对调 | `test_message_body_prefers_plain_in_alternative` |
| `message_body` 删掉 attachment 跳过 | `test_message_body_skips_attachments` |
| `header_item` 加回 `"is_read": False` | `test_header_item_shape_exactly_four_keys` |
| `header_item` 去掉 `or internaldate` 回退 | `test_header_item_falls_back_to_internaldate` |
| `header_item` 改成 `_iso(internaldate or received_dt(msg))`（顺序反了） | `test_header_item_prefers_date_header_over_internaldate` |
| `parse_internaldate` 去掉 `.astimezone(timezone.utc)` | `test_parse_internaldate_reads_imap_format` |

> 最后一行原写作「改用 `parsedate_to_datetime`」—— 2026-09-21 实测：两者对同一输入产出**逐字相同**的 ISO 串（`parsedate_to_datetime` 本就吃得下 INTERNALDATE），用例照绿，**是退化变异**，故换成去掉 `astimezone` 这一刀（产出 `2026-09-17T16:31:00+08:00`，必红）。

- [ ] **Step 6: 提交**

```bash
git add backend/mail_client.py tests/test_mail_client.py
git commit -m "feat(mail): IMAP 取信层的纯函数（RFC 2047 解码 / MIME 正文 / 日期）"
```

---

## Task 5: IMAP 连接 + 只读三件套 + `list_mail`

**Files:**
- Modify: `backend/mail_client.py`（**本任务删掉 OWA 层**）
- Test: `tests/test_mail_client.py`（**本任务把 OWA 用例整体换成 IMAP 用例**）

**Interfaces:**
- Consumes: `credentials.get_gmail`、Task 4 的纯函数
- Produces:
  - `IMAP_HOST` / `IMAP_PORT` / `MAILBOX` / `HEADER_BATCH`
  - `_connect() -> tuple[conn, uidvalidity]`
  - `_uidvalidity_of(conn) -> str` / `_uidvalidity_from_status(conn) -> str`
  - `_auth_msg(exc) -> str`
  - `_logout_quietly(conn) -> None`
  - `_chunks(seq, n) -> list[list]`
  - `_fetch_headers(conn, uids) -> list[dict]`
  - `_do_list(days) -> list[dict]`
  - `list_mail(days: int = DEFAULT_DAYS) -> list[dict]`（签名不变）
  - 模块级 `_uidvalidity`（Task 6 用）

**删掉的 OWA 层**（14 函数 + 6 常量，已逐个对照源码核实）：`_prop` / `_response_items` / `_sender_name` / `find_item_payload` / `get_item_payload` / `parse_find_items` / `parse_get_bodies` / `jwt_exp` / `extract_bearer` / `_post_json` / `_capture_token` / `collect_items` / `_do_list`(旧) / `_do_bodies`(旧)；常量 `SVC_URL` / `_HDR` / `PAGE_SIZE` / `MAIL_URL` / `_TOKEN_WAIT_TICKS` / `_TOKEN_WAIT_MS`。

> **Task 5 与 Task 6 必须连续执行，中间不交付。** 本任务删掉 OWA 层后 `fetch_bodies` 会失去依赖；把 `fetch_bodies` 与 `_do_bodies` 一并删掉、由 Task 6 立刻补上新的，**不要**留一个半死的实现。

- [ ] **Step 1: 写失败的测试**

**整体替换** `tests/test_mail_client.py` 的 OWA 部分（保留 Task 4 加的纯函数用例）。文件头的 import 与假连接：

```python
"""Gmail IMAP 取信层测试（纯函数 + I/O）。全程合成 fixture，不出网。"""
import email
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from backend import credentials, mail_client


#: 替身给每封邮件回的 INTERNALDATE。刻意与 _hdr() 默认的 Date 头
#: （16:31 +0800 = 08:31Z）**不同**，这样「Date 头优先」才可判。
_INTERNALDATE = b"17-Sep-2026 20:00:00 +0000"


class _FakeIMAP:
    """替身，记录**发出的每一条命令**。绝不真连网（netguard 兜底）。"""

    def __init__(self, *, uidvalidity=b"7", headers=None, login_err=None,
                 untagged=None):
        self.uidvalidity = uidvalidity
        self.headers = headers or {}     # {uid_bytes: raw_header_bytes}
        self.login_err = login_err
        self.sent = []
        if untagged is not None:
            self.untagged_responses = untagged
        elif uidvalidity:
            self.untagged_responses = {"UIDVALIDITY": [uidvalidity]}
        else:
            self.untagged_responses = {}

    # --- 被 mail_client 调到的四个方法 ---
    def login(self, user, pw):
        self.sent.append(("login", user))
        if self.login_err:
            raise self.login_err
        return ("OK", [b"logged in"])

    def select(self, mailbox, readonly=False):
        self.sent.append(("select", mailbox, readonly))
        return ("OK", [b"1"])

    def uid(self, cmd, *args):
        self.sent.append(("uid", cmd) + tuple(args))
        if cmd == "SEARCH":
            return ("OK", [b" ".join(self.headers) if self.headers else b""])
        if cmd == "FETCH":
            uids = args[0] if isinstance(args[0], bytes) else str(args[0]).encode()
            out = []
            for u in uids.split(b","):
                if u in self.headers:
                    out.append((b'1 (UID %s INTERNALDATE "%s" '
                                b'BODY[HEADER.FIELDS (...)] {%d}'
                                % (u, _INTERNALDATE, len(self.headers[u])),
                                self.headers[u]))
            return ("OK", out)
        return ("OK", [b""])

    def logout(self):
        self.sent.append(("logout",))
        return ("BYE", [b"bye"])


def _install(monkeypatch, fake, *, creds=("me@gmail.com", "app-pw-16"), scope=None):
    """把 IMAP 连接与凭据都换成替身。

    `select_scope` 默认换成**恒等函数**：本节的 I/O 用例关心的是「发了什么命令、
    拿到了什么」，而日期窗口由 Task 4 的纯函数用例**完整**覆盖（窗口、fail-open、
    无未读分支三条都有）。

    不这么做，这些用例就会把真实时钟引进来：fixture 里的日期一旦写死，
    过了那个窗口用例就自己变红 —— 时间炸弹。`test_do_list_applies_select_scope`
    专门负责「select_scope 确实被调用且参数正确」这一条。
    """
    monkeypatch.setattr(credentials, "get_gmail", lambda: creds)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda *a, **kw: fake)
    monkeypatch.setattr(mail_client, "select_scope",
                        scope or (lambda items, now, days: items))
    return fake


def _hdr(subject="S", sender="T <t@cityu.edu.hk>",
         date="Thu, 17 Sep 2026 16:31:00 +0800"):
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = sender
    m["Date"] = date
    return m.as_bytes()
```

新用例：

```python
# ============ 只读三件套（spec §5.2，本设计的要害）============

def test_select_is_readonly(monkeypatch):
    """readonly=True 才发 EXAMINE。改成 False 就静默置已读。"""
    fake = _install(monkeypatch, _FakeIMAP())
    mail_client.list_mail(7)
    assert ("select", "INBOX", True) in fake.sent


def test_all_fetches_use_body_peek(monkeypatch):
    """**最要害的一条**：所有取件都必须 BODY.PEEK。

    BODY[] / RFC822 会置 \\Seen —— 用户在学校邮箱里的未读状态会被我们改掉。
    """
    fake = _install(monkeypatch, _FakeIMAP(headers={b"1": _hdr()}))
    mail_client.list_mail(7)
    fetches = [c for c in fake.sent if c[0] == "uid" and c[1] == "FETCH"]
    assert fetches, "没发出任何 FETCH，用例是空转的"
    for c in fetches:
        assert "BODY.PEEK" in c[-1], f"用了非 PEEK 取件：{c}"
        assert not re.search(r"BODY\[", c[-1]), f"出现裸 BODY[]：{c}"


def test_never_issues_mutating_commands(monkeypatch):
    """STORE / COPY / MOVE / EXPUNGE 一条都不许有。"""
    fake = _install(monkeypatch, _FakeIMAP(headers={b"1": _hdr()}))
    mail_client.list_mail(7)
    bad = [c for c in fake.sent
           if c[0] == "uid" and str(c[1]).upper() in
           ("STORE", "COPY", "MOVE", "EXPUNGE")]
    assert bad == []
    assert not any(str(x).upper().startswith(("STORE", "EXPUNGE"))
                   for c in fake.sent for x in c)


def test_logs_out_on_success(monkeypatch):
    fake = _install(monkeypatch, _FakeIMAP(headers={b"1": _hdr()}))
    mail_client.list_mail(7)
    assert ("logout",) in fake.sent


def test_logs_out_on_failure(monkeypatch):
    """出错也要登出 —— 连接不能泄漏。"""
    fake = _install(monkeypatch, _FakeIMAP(headers={b"1": _hdr()}))
    monkeypatch.setattr(mail_client, "select_scope",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(Exception):
        mail_client.list_mail(7)
    assert ("logout",) in fake.sent


# ============ 认证失败：即停，不重试（spec §5.8）============

def test_auth_failure_raises_without_retry(monkeypatch):
    fake = _install(monkeypatch, _FakeIMAP(
        login_err=imaplib.IMAP4.error("AUTHENTICATIONFAILED Invalid credentials")))
    with pytest.raises(mail_client.MailError) as ei:
        mail_client.list_mail(7)
    assert "设置页" in str(ei.value)
    assert "两步验证" in str(ei.value)
    assert len([c for c in fake.sent if c[0] == "login"]) == 1, "登录被重试了"


def test_no_credentials_points_at_settings(monkeypatch):
    _install(monkeypatch, _FakeIMAP(), creds=None)
    with pytest.raises(mail_client.MailError) as ei:
        mail_client.list_mail(7)
    assert "设置页" in str(ei.value)


# ============ 列表：正例（证明真的做成了事，不只是没做错事）============

def test_list_mail_returns_contract_items(monkeypatch):
    _install(monkeypatch, _FakeIMAP(
        headers={b"1": _hdr(subject="作业", sender="老师 <t@cityu.edu.hk>"),
                 b"2": _hdr(subject="Announcement")}))
    out = mail_client.list_mail(7)
    by_id = {x["id"]: x for x in out}
    assert set(by_id) == {"1", "2"}
    assert by_id["1"]["subject"] == "作业"
    assert by_id["1"]["sender"] == "老师"
    assert by_id["1"]["received"] == "2026-09-17T08:31:00+00:00"
    assert all(set(x) == {"id", "subject", "sender", "received"} for x in out)


def test_list_mail_fetches_internaldate(monkeypatch):
    """命令里必须带 INTERNALDATE，否则 spec §5.3 的退路无从谈起。"""
    fake = _install(monkeypatch, _FakeIMAP(headers={b"1": _hdr()}))
    mail_client.list_mail(7)
    fetch = next(c for c in fake.sent if c[0] == "uid" and c[1] == "FETCH")
    assert "INTERNALDATE" in fetch[-1]


def test_list_mail_uses_internaldate_when_date_header_missing(monkeypatch):
    """Date 头缺失时 received 退到 INTERNALDATE，**不是空串**（spec §5.3）。

    空串会让前端 new Date("") 得到 Invalid Date。
    """
    m = EmailMessage()
    m["Subject"] = "无 Date 头"
    m["From"] = "t@cityu.edu.hk"
    _install(monkeypatch, _FakeIMAP(headers={b"1": m.as_bytes()}))
    out = mail_client.list_mail(7)
    assert out[0]["received"] == "2026-09-17T20:00:00+00:00"


def test_list_mail_prefers_date_header_over_internaldate(monkeypatch):
    """两者都在时用 Date 头（16:31+0800 → 08:31Z），不是 INTERNALDATE（20:00Z）。"""
    _install(monkeypatch, _FakeIMAP(headers={b"1": _hdr()}))
    out = mail_client.list_mail(7)
    assert out[0]["received"] == "2026-09-17T08:31:00+00:00"


def test_do_list_applies_select_scope(monkeypatch):
    """_do_list 真的调用了 select_scope，参数正确，并原样返回它的结果。

    （其余 I/O 用例把 select_scope 换成了恒等函数，所以这一条是唯一
    「接线没断」的证据 —— 删掉 `return select_scope(...)` 只有它会红。）
    """
    seen = {}

    def spy(items, now, days):
        seen["ids"] = [it["id"] for it in items]
        seen["days"] = days
        seen["now_aware"] = now.tzinfo is not None
        return [{"id": "sentinel"}]

    _install(monkeypatch, _FakeIMAP(headers={b"1": _hdr(), b"2": _hdr()}), scope=spy)
    out = mail_client.list_mail(3)
    assert out == [{"id": "sentinel"}]
    assert seen["ids"] == ["1", "2"]
    assert seen["days"] == 3
    assert seen["now_aware"], "now 必须是 aware，否则与 received 比较会抛 TypeError"


def test_list_mail_returns_empty_without_fetching(monkeypatch):
    """没有邮件时不发 FETCH。"""
    fake = _install(monkeypatch, _FakeIMAP(headers={}))
    assert mail_client.list_mail(7) == []
    assert not [c for c in fake.sent if c[0] == "uid" and c[1] == "FETCH"]


def test_list_mail_search_since_is_coarsened_by_one_day(monkeypatch):
    """SINCE 放宽一天：它按日粒度且按服务端时区（spec §5.3）。"""
    fake = _install(monkeypatch, _FakeIMAP(headers={}))
    mail_client.list_mail(7)
    search = next(c for c in fake.sent if c[0] == "uid" and c[1] == "SEARCH")
    since = datetime.strptime(search[-1], "%d-%b-%Y").replace(tzinfo=timezone.utc)
    assert since.date() == (datetime.now(timezone.utc) - timedelta(days=8)).date()


def test_list_mail_connects_once(monkeypatch):
    """一次 list_mail 只连一次。"""
    fake = _install(monkeypatch, _FakeIMAP(headers={b"1": _hdr()}))
    mail_client.list_mail(7)
    assert len([c for c in fake.sent if c[0] == "login"]) == 1


# ============ UIDVALIDITY（spec §5.5）============

def test_list_mail_records_uidvalidity(monkeypatch):
    _install(monkeypatch, _FakeIMAP(uidvalidity=b"99", headers={}))
    mail_client.list_mail(7)
    assert mail_client._uidvalidity == "99"


def test_missing_uidvalidity_raises(monkeypatch):
    """读不到 UIDVALIDITY 说明协议假设变了 —— 报错，不静默跳过。"""
    _install(monkeypatch, _FakeIMAP(untagged={}, headers={}))
    monkeypatch.setattr(mail_client, "_uidvalidity_from_status", lambda conn: "")
    with pytest.raises(mail_client.MailError) as ei:
        mail_client.list_mail(7)
    assert "UIDVALIDITY" in str(ei.value)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_mail_client.py -q
```

预期：新用例因 `mail_client.IMAP_HOST` 等不存在、以及 `list_mail` 仍走浏览器而失败。

- [ ] **Step 3: 实现**

在 `backend/mail_client.py` 中**删掉整个 OWA 层**，换成：

> **import 区块是累加的，不是替换。** Task 4 加的那几条（`from email import message_from_bytes`、
> `from email.header import decode_header, make_header`、`from email.utils import parseaddr,
> parsedate_to_datetime`、`from backend.canvas_client import strip_html`）**必须保留** ——
> 本任务下面的代码用到 `message_from_bytes`（`_fetch_headers`）与 `parse_internaldate`（同）。
> 另外 `import re` 也是本任务新代码需要的（`_fetch_headers` 解 UID / INTERNALDATE、
> `_uidvalidity_from_status` 解 UIDVALIDITY）。删 OWA 层时一并核对 `re` / `email` 还在不在。

```python
import imaplib
import re
import time
from datetime import datetime, timedelta, timezone

from backend import credentials

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
MAILBOX = "INBOX"
TIMEOUT = 30
DEFAULT_DAYS = 7
HEADER_BATCH = 50          # 一次 UID FETCH 取几封的头
BODY_BATCH = 5             # 一次 UID FETCH 取几封的整封（比旧值保守：见 spec §5.4）
_THROTTLE = 0.5            # 批次间隔（秒），旧 spec §9「请求节流」
MAX_BODY_CHARS = 200_000   # 单封正文上限，纯防病态巨型邮件

#: 最近一次 list_mail 记下的 INBOX UIDVALIDITY；fetch_bodies 靠它判断邮箱是否被重建。
#: 模块级全局是**有意**的：同一请求内 list_mail → fetch_bodies 顺序调用，值必然相同；
#: 并发请求读到的是同一个 INBOX 的同一个值，不会误报。真发生重建时报错才是对的。
_uidvalidity: str = ""


def _chunks(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def _auth_msg(exc: Exception) -> str:
    """把 imaplib 的英文错误转成指得动用户的中文。"""
    text = str(exc)
    if "AUTHENTICATIONFAILED" in text.upper() or "Invalid credentials" in text:
        return ("Gmail 认证失败：请到设置页检查 Gmail 地址与应用专用密码。"
                "（应用专用密码需要该账号已开启两步验证；改过 Google 密码后旧密码会作废，"
                "需要重新生成一个。）")
    return f"Gmail 操作失败：{text}"


def _logout_quietly(conn) -> None:
    """登出，异常吞掉 —— 不能让它盖住真正的错。"""
    if conn is None:
        return
    try:
        conn.logout()
    except Exception:
        pass


def _uidvalidity_from_status(conn) -> str:
    """退路：用 STATUS 再问一次 UIDVALIDITY（多一个往返）。"""
    try:
        typ, data = conn.status(MAILBOX, "(UIDVALIDITY)")
    except Exception:
        return ""
    if typ != "OK" or not data:
        return ""
    raw = data[0] if isinstance(data[0], bytes) else str(data[0]).encode()
    m = re.search(rb"UIDVALIDITY\s+(\d+)", raw)
    return m.group(1).decode() if m else ""


def _uidvalidity_of(conn) -> str:
    """读 INBOX 的 UIDVALIDITY。

    优先从 select() 自己的 untagged 响应里取（省一个往返），取不到再 STATUS 问一次。
    **两者的真实形状由 spec §10.9 实测确定** —— 见 Task 1 Step 3 写回的结果。
    """
    for raw in conn.untagged_responses.get("UIDVALIDITY", []):
        val = raw.decode() if isinstance(raw, bytes) else str(raw)
        if val.strip().isdigit():
            return val.strip()
    return _uidvalidity_from_status(conn)


def _connect():
    """连上 Gmail 并 EXAMINE INBOX。返回 (conn, uidvalidity)。

    只读由构造保证：readonly=True 发的是 EXAMINE，服务端层面禁止改动。
    """
    creds = credentials.get_gmail()
    if not creds:
        raise MailError("尚未设置学校邮箱：请到设置页填写 Gmail 地址与应用专用密码")
    address, app_password = creds
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=TIMEOUT)
        conn.login(address, app_password)
        typ, data = conn.select(MAILBOX, readonly=True)      # EXAMINE
        if typ != "OK":
            raise MailError(f"打开邮箱 {MAILBOX} 失败：{data}")
        uidvalidity = _uidvalidity_of(conn)
        if not uidvalidity:
            raise MailError(
                "读不到邮箱的 UIDVALIDITY —— 邮件与编号的对应关系无法确认。"
                "这是协议层面的意外，已停止取信以免取错邮件。")
        return conn, uidvalidity
    except MailError:
        _logout_quietly(conn)
        raise
    except imaplib.IMAP4.error as exc:
        _logout_quietly(conn)
        raise MailError(_auth_msg(exc)) from exc
    except Exception as exc:
        _logout_quietly(conn)
        raise MailError(f"连接 Gmail 失败：{exc}") from exc


def _fetch_headers(conn, uids: list[bytes]) -> list[dict]:
    """批量取邮件头。**BODY.PEEK** —— 绝不置已读。

    顺带取 INTERNALDATE：它是 Date 头解不出时的退路（spec §5.3）。INTERNALDATE
    是 FETCH 的**数据项**（不是信封字段），所以和 BODY.PEEK[…] 并列写在同一条命令里，
    不额外多一个往返。
    """
    typ, data = conn.uid(
        "FETCH", b",".join(uids),
        "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] INTERNALDATE)")
    if typ != "OK":
        raise MailError(f"读取邮件头失败：{data}")
    out = []
    for entry in data:
        if not isinstance(entry, tuple) or len(entry) < 2:
            continue
        meta, raw = entry[0], entry[1]
        blob = meta if isinstance(meta, bytes) else str(meta).encode()
        m = re.search(rb"UID\s+(\d+)", blob)
        if not m:
            continue
        intd = re.search(rb'INTERNALDATE "([^"]+)"', blob)
        msg = message_from_bytes(raw if isinstance(raw, bytes) else b"")
        out.append(header_item(
            m.group(1).decode(), msg,
            parse_internaldate(intd.group(1).decode()) if intd else None))
    return out


def _do_list(days: int) -> list[dict]:
    global _uidvalidity
    conn, uidvalidity = _connect()
    try:
        _uidvalidity = uidvalidity
        # SINCE 按日粒度且按服务端时区，放宽一天再由 select_scope 精确过滤（spec §5.3）
        since = (datetime.now(timezone.utc)
                 - timedelta(days=days + 1)).strftime("%d-%b-%Y")
        typ, data = conn.uid("SEARCH", None, "SINCE", since)
        if typ != "OK":
            raise MailError(f"搜索邮件失败：{data}")
        uids = (data[0] or b"").split()
        if not uids:
            return []
        items: list[dict] = []
        for chunk in _chunks(uids, HEADER_BATCH):
            items.extend(_fetch_headers(conn, chunk))
        return select_scope(items, datetime.now(timezone.utc), days)
    finally:
        _logout_quietly(conn)


def list_mail(days: int = DEFAULT_DAYS) -> list[dict]:
    """拉取「最近 days 天」的邮件（不含正文）。全程只读，不改已读状态。

    **不**走 banweb 的浏览器线程，也**不**重试：认证失败即停（spec §5.8）。
    """
    return _do_list(days)
```

同时把模块 docstring 整段重写，说明：这条路是 IMAP、为什么放弃 OWA（指向旧 spec §3 的实测）、只读三件套是什么。

**注意**：旧的 `list_mail` 里那段关于「归一化位置与 `_target_closed` 重试」的长注释**整段作废**，随 OWA 层一起删掉 —— 那个机制在 IMAP 下不存在。

- [ ] **Step 4: 跑测试**

```bash
.venv/bin/python -m pytest tests/test_mail_client.py -q
```

预期：全绿（`fetch_bodies` 与 `_do_bodies` 此时已删，Task 6 立刻补回）。

- [ ] **Step 5: 变异验证**

| 变异 | 应红的用例 |
|---|---|
| `select(MAILBOX, readonly=True)` → `readonly=False` | `test_select_is_readonly` |
| `BODY.PEEK[HEADER.FIELDS …]` → `BODY[HEADER.FIELDS …]` | `test_all_fetches_use_body_peek` |
| 加一句 `conn.uid("STORE", uids[0], "+FLAGS", "(\\Seen)")` | `test_never_issues_mutating_commands` |
| 删掉 `finally: _logout_quietly(conn)` | `test_logs_out_on_failure` |
| 认证失败时在 `except` 里再 `conn.login(...)` 一次 | `test_auth_failure_raises_without_retry` |
| `days + 1` → `days` | `test_list_mail_search_since_is_coarsened_by_one_day` |
| `_uidvalidity_of` 读不到时返回 `"0"` 而不是报错 | `test_missing_uidvalidity_raises` |
| `header_item` 里 `subject` 改用 `msg.get("Subject") or ""`（不解码） | `test_list_mail_returns_contract_items` |
| `_fetch_headers` 的 FETCH 命令去掉 `INTERNALDATE` | `test_list_mail_fetches_internaldate` 与 `..._uses_internaldate_when_date_header_missing` |
| `_do_list` 删掉 `return select_scope(...)`（改成 `return items`） | `test_do_list_applies_select_scope` |
| `_do_list` 里 `select_scope(items, now, days)` 的 `days` 写死成 `DEFAULT_DAYS` | `test_do_list_applies_select_scope` |

- [ ] **Step 6: 提交**

```bash
git add backend/mail_client.py tests/test_mail_client.py
git commit -m "feat(mail): 取信层换成 Gmail IMAP（只读三件套 + UIDVALIDITY 核对）

删掉整个 OWA 层：14 个函数 + 6 个常量。邮件链路上不再有浏览器。"
```

---

## Task 6: `fetch_bodies` + UIDVALIDITY 核对

**Files:**
- Modify: `backend/mail_client.py`
- Test: `tests/test_mail_client.py`

**Interfaces:**
- Consumes: Task 5 的 `_connect` / `_chunks` / `_uidvalidity` / `message_body`
- Produces:
  - `_fetch_bodies_chunk(conn, uids) -> list[tuple[str, str]]`
  - `_do_bodies(items) -> dict`
  - `fetch_bodies(items: list[dict]) -> dict`（签名不变）

- [ ] **Step 1: 写失败的测试**

```python
# ============ 正文：批量 + 节流 + 截断（spec §5.4）============

class _BodyIMAP(_FakeIMAP):
    """除头之外还能给整封。"""

    def __init__(self, bodies=None, **kw):
        super().__init__(**kw)
        self.bodies = bodies or {}     # {uid_bytes: raw_message_bytes}

    def uid(self, cmd, *args):
        if cmd == "FETCH" and "BODY.PEEK[]" in str(args[-1]):
            self.sent.append(("uid", cmd) + tuple(args))
            uids = args[0] if isinstance(args[0], bytes) else str(args[0]).encode()
            return ("OK", [(b"1 (UID %s BODY[] {%d}" % (u, len(self.bodies[u])),
                            self.bodies[u]) for u in uids.split(b",") if u in self.bodies])
        return super().uid(cmd, *args)


def _body_msg(text, ctype="plain"):
    m = EmailMessage()
    m.set_content(text, subtype=ctype, charset="utf-8")
    return m.as_bytes()


def test_fetch_bodies_returns_id_to_body(monkeypatch):
    _install(monkeypatch, _BodyIMAP(
        headers={b"1": _hdr(), b"2": _hdr()},
        bodies={b"1": _body_msg("第一封正文"), b"2": _body_msg("第二封正文")}))
    mail_client.list_mail(7)              # 先记 uidvalidity
    out = mail_client.fetch_bodies([{"id": "1"}, {"id": "2"}])
    assert out == {"1": "第一封正文", "2": "第二封正文"}


def test_fetch_bodies_uses_body_peek(monkeypatch):
    fake = _install(monkeypatch, _BodyIMAP(headers={b"1": _hdr()},
                                           bodies={b"1": _body_msg("x")}))
    mail_client.list_mail(7)
    mail_client.fetch_bodies([{"id": "1"}])
    fetches = [c for c in fake.sent if c[0] == "uid" and c[1] == "FETCH"
               and "BODY.PEEK[]" in str(c[-1])]
    assert fetches
    assert all("BODY.PEEK[]" in str(c[-1]) for c in fetches)


def test_fetch_bodies_empty_input_does_not_connect(monkeypatch):
    fake = _install(monkeypatch, _BodyIMAP())
    assert mail_client.fetch_bodies([]) == {}
    assert not [c for c in fake.sent if c[0] == "login"]


def test_fetch_bodies_batches_and_throttles(monkeypatch):
    """一次 12 封 → 3 批（BODY_BATCH=5），批间有 sleep（spec §9 请求节流）。"""
    sleeps = []
    monkeypatch.setattr(mail_client.time, "sleep", lambda s: sleeps.append(s))
    ids = [str(i) for i in range(12)]
    fake = _install(monkeypatch, _BodyIMAP(
        headers={i.encode(): _hdr() for i in ids},
        bodies={i.encode(): _body_msg("x") for i in ids}))
    mail_client.list_mail(7)
    mail_client.fetch_bodies([{"id": i} for i in ids])
    body_fetches = [c for c in fake.sent if c[0] == "uid" and c[1] == "FETCH"
                    and "BODY.PEEK[]" in str(c[-1])]
    assert len(body_fetches) == 3
    assert sleeps == [mail_client._THROTTLE, mail_client._THROTTLE]


def test_fetch_bodies_truncates_huge_body(monkeypatch):
    huge = "字" * (mail_client.MAX_BODY_CHARS + 500)
    _install(monkeypatch, _BodyIMAP(headers={b"1": _hdr()},
                                    bodies={b"1": _body_msg(huge)}))
    mail_client.list_mail(7)
    out = mail_client.fetch_bodies([{"id": "1"}])
    assert len(out["1"]) == mail_client.MAX_BODY_CHARS


def test_fetch_bodies_raises_on_uidvalidity_change(monkeypatch):
    """邮箱被重建 → UID 可能指向别的邮件 → 报错，绝不尽力而为地取（spec §5.5）。"""
    fake = _install(monkeypatch, _BodyIMAP(uidvalidity=b"7",
                                           headers={b"1": _hdr()},
                                           bodies={b"1": _body_msg("x")}))
    mail_client.list_mail(7)
    fake.untagged_responses = {"UIDVALIDITY": [b"8"]}     # 邮箱被重建
    with pytest.raises(mail_client.MailError) as ei:
        mail_client.fetch_bodies([{"id": "1"}])
    assert "重建" in str(ei.value)
    assert not [c for c in fake.sent if c[0] == "uid" and "BODY.PEEK[]" in str(c[-1])], \
        "UIDVALIDITY 不符时不该取任何正文"


def test_fetch_bodies_ok_when_uidvalidity_matches(monkeypatch):
    _install(monkeypatch, _BodyIMAP(headers={b"1": _hdr()},
                                    bodies={b"1": _body_msg("x")}))
    mail_client.list_mail(7)
    assert mail_client.fetch_bodies([{"id": "1"}]) == {"1": "x"}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_mail_client.py -q -k fetch_bodies
```

预期：失败（`fetch_bodies` 不存在）。

- [ ] **Step 3: 实现**

```python
def _fetch_bodies_chunk(conn, uids: list[str]) -> list[tuple[str, str]]:
    """批量取整封。**BODY.PEEK[]** —— 绝不置已读。

    注意 IMAP 会把附件一起给你（没有「只要正文」的通用取法）；message_body
    负责把附件排除在正文之外。更省的做法（BODYSTRUCTURE + 分段取）见 spec §11。
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
        msg = message_from_bytes(raw if isinstance(raw, bytes) else b"")
        body = message_body(msg)
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS]
        out.append((m.group(1).decode(), body))
    return out


def _do_bodies(items: list[dict]) -> dict:
    conn, uidvalidity = _connect()
    try:
        if _uidvalidity and uidvalidity != _uidvalidity:
            raise MailError(
                "邮箱自上次刷新后已被重建（UIDVALIDITY 变了），"
                "邮件与编号的对应关系不再可靠。请重新点「刷新邮件」。")
        ids = [it["id"] for it in items]
        out: dict = {}
        for n, chunk in enumerate(_chunks(ids, BODY_BATCH)):
            if n:
                time.sleep(_THROTTLE)      # 旧 spec §9 请求节流
            for uid, body in _fetch_bodies_chunk(conn, chunk):
                out[uid] = body
        return out
    finally:
        _logout_quietly(conn)


def fetch_bodies(items: list[dict]) -> dict:
    """批量取正文，返回 {item_id: body}。

    空输入直接返回 —— 绝不为了「取零封邮件的正文」去连一次 IMAP。
    全程只读：EXAMINE + BODY.PEEK，不碰用户已读状态。
    """
    if not items:
        return {}
    try:
        return _do_bodies(items)
    except MailError:
        raise
    except Exception as exc:
        raise MailError(f"读取邮件正文失败：{exc}") from exc
```

- [ ] **Step 4: 跑测试**

```bash
.venv/bin/python -m pytest tests/test_mail_client.py -q
```

预期：全绿。

- [ ] **Step 5: 变异验证**

| 变异 | 应红的用例 |
|---|---|
| `BODY.PEEK[]` → `BODY[]` | `test_fetch_bodies_uses_body_peek` |
| 去掉 `if n: time.sleep(_THROTTLE)` | `test_fetch_bodies_batches_and_throttles` |
| 去掉 UIDVALIDITY 比对 | `test_fetch_bodies_raises_on_uidvalidity_change` |
| 去掉 `MAX_BODY_CHARS` 截断 | `test_fetch_bodies_truncates_huge_body` |
| 空输入时不早返回（改成照样连） | `test_fetch_bodies_empty_input_does_not_connect` |

- [ ] **Step 6: 提交**

```bash
git add backend/mail_client.py tests/test_mail_client.py
git commit -m "feat(mail): IMAP 取正文（BODY.PEEK 批量 + 节流 + UIDVALIDITY 核对）"
```

---

## Task 7: 删掉 banweb 的邮件浏览器入口

**Files:**
- Modify: `backend/banweb.py`
- Test: `tests/test_banweb.py`（**只删，不新增**）

**Interfaces:**
- Consumes: 无
- Produces: `banweb.py` 少两个函数

> ⚠️ **只删 `_find_mail_page` / `_ensure_mail_page`。**
> **`MAIL_HOSTS` / `_is_mail_url` 与 `_find_page` 的邮件域排除一律保留** ——
> 后端会附着到用户真实 Chrome（`connect_over_cdp(127.0.0.1:9339)`），那条路径下
> `ctx.pages` 是用户自己的标签页，其中很可能有他手动开着的 Outlook。删掉排除逻辑，
> `_find_page` 会把用户的 Outlook tab 认领为 AIMS 页，接着 `goto(TERM_PAGE)`
> 把它导航走 —— **用户自己的邮箱页面就此消失**。
> 而且「没有可用 AIMS 页 → 返回 None」是**轮询绝不弹窗**的承重件。
> spec §7 已就此事更正（原文曾写「一起删掉」，是错的）。

- [ ] **Step 1: 确认这两个函数无人调用**

```bash
grep -rn "_find_mail_page\|_ensure_mail_page" backend/ tests/ tools/ frontend/
```

预期：除 `backend/banweb.py` 的定义处外**无输出**（Task 5/6 已把 `tests/test_mail_client.py` 里的 monkeypatch 删掉）。若还有别的调用点，**停下**，先弄清它为什么还在用。

- [ ] **Step 2: 删函数**

删掉 `backend/banweb.py` 里的 `_find_mail_page` 与 `_ensure_mail_page` 两个函数**及其 docstring**。

- [ ] **Step 3: 确认 `_find_page` 的邮件域排除**还在

```bash
sed -n '/^def _find_page/,/^def /p' backend/banweb.py | grep -c "_is_mail_url"
```

预期：**2**（第一段循环的 `continue` 和兜底循环的条件）。少于 2 就是删过头了。

- [ ] **Step 4: 跑测试**

```bash
.venv/bin/python -m pytest tests/test_banweb.py -q
```

预期：全绿。**特别注意这 4 条必须仍然绿**，它们是邮件域排除的守卫：

- `test_find_page_never_returns_a_mail_tab`
- `test_find_page_never_returns_a_mail_login_relay_tab`
- `test_find_page_prefers_aims_page_over_mail_tab`
- `test_find_page_still_falls_back_to_unknown_blank_page`

- [ ] **Step 5: 全量回归**

```bash
.venv/bin/python -m pytest -q
```

- [ ] **Step 6: 提交**

```bash
git add backend/banweb.py
git commit -m "chore(banweb): 删掉只服务邮件的两个浏览器入口

保留 MAIL_HOSTS / _is_mail_url 与 _find_page 的邮件域排除 —— 用户自己的
Chrome 里可能开着 Outlook，删掉会让 AIMS 把它导航走。"
```

---

## Task 8: 前端（`is_read` 移除 + 设置页 Gmail 组 + i18n）

**Files:**
- Modify: `frontend/app.js`（`:3068` 去掉 `is_read`；新增凭据读写与事件绑定）
- Modify: `frontend/index.html`（设置页新增一组）
- Modify: `frontend/i18n.js`（zh + en 两套）
- Modify: `tools/check_mail_frontend.mjs`（夹具去掉 `is_read`）

**Interfaces:**
- Consumes: Task 3 的三个端点
- Produces: 无（终端用户界面）

- [ ] **Step 1: 去掉 `is_read` 的渲染**

先看清现状：

```bash
grep -n "is_read\|flag" frontend/app.js
```

`frontend/app.js:3068` 附近有一个 `const flag = m.is_read ? …` 和它在模板字符串里的插值处。**两处一起删掉**（连同 `flag` 变量本身）。

- [ ] **Step 2: 设置页新增一组**

在 `frontend/index.html` 的 AIMS 那组 `<section class="set-card">` **之后**插入（照它的写法）：

```html
        <section class="set-card">
          <span class="eyebrow" data-i18n="settings.group.gmail"></span>
          <p class="muted" data-i18n="settings.gmail_hint"></p>
          <div class="grid">
            <label class="field"><span data-i18n="settings.gmail_user"></span><input id="gmailAddress" type="text" autocomplete="username" placeholder="you@gmail.com"></label>
            <label class="field"><span data-i18n="settings.gmail_pass"></span><input id="gmailAppPassword" type="password" autocomplete="off" placeholder="••••••••••••••••"></label>
          </div>
          <div class="row" style="margin-top:10px">
            <button id="btnSaveGmail" class="btn btn-primary" data-i18n="btn.save_gmail"></button>
            <button id="btnClearGmail" class="btn btn-ghost" data-i18n="btn.clear_gmail" hidden></button>
            <span id="gmailSavedHint" class="muted"></span>
          </div>
        </section>
```

注意 `autocomplete="off"`（不是 `current-password`）—— 这是应用专用密码，不是 Google 账号密码，不该让浏览器拿账号密码去填。

- [ ] **Step 3: 前端逻辑**

先读 AIMS 那套（`refreshAimsUi` / `btnSaveAims` / `btnClearAims`，约 `frontend/app.js:120`、`:198-215`），**照它的实际写法**（`api()` 的签名、`withBusy` 是否存在、`setStatus` 的签名、`t()` 的用法）在紧邻处加一份对称的：

```javascript
async function refreshGmailUi(){
  const r = await api("mail/gmail/credentials/status");
  const has = r.ok === true && r.has_credentials === true;
  $("gmailAddress").value = has ? (r.address || "") : "";
  $("btnClearGmail").hidden = !has;
  $("gmailSavedHint").textContent = has ? t("status.gmail_saved_hint") : "";
}

$("btnSaveGmail").onclick = async () => {
  const address = $("gmailAddress").value.trim();
  const app_password = $("gmailAppPassword").value;
  if(!address || !app_password){ setStatus(t("status.gmail_need_both"), "err"); return; }
  await withBusy(t("status.gmail_saving"), $("btnSaveGmail"), async ()=>{
    const r = await api("mail/gmail/credentials", { address, app_password });
    if(r.ok !== true){ setStatus(t("status.gmail_save_fail") + (r.error||""), "err"); return; }
    $("gmailAppPassword").value = "";
    setStatus(t("status.gmail_saved_ok"), "ok", 4000);
    await refreshGmailUi();
  });
};

$("btnClearGmail").onclick = async () => {
  await withBusy(t("status.gmail_clearing"), $("btnClearGmail"), async ()=>{
    const r = await api("mail/gmail/credentials", undefined, "DELETE");
    if(r.ok !== true){ setStatus(t("status.gmail_clear_fail") + (r.error||""), "err"); return; }
    $("gmailAddress").value=""; $("gmailAppPassword").value="";
    setStatus(t("status.gmail_cleared"), "ok");
    await refreshGmailUi();
  });
};
```

并把它挂进设置页打开时的刷新（找 AIMS 的 `refreshAimsUi()` 调用点，在旁边加 `refreshGmailUi()`）。

**保存成功后立刻清空密码框** —— 不要把密码留在 DOM 里。
**若 AIMS 那套的实际写法与上面不同（函数名不同、没有 `withBusy`），以 AIMS 为准改写，不要照搬本段。**

- [ ] **Step 4: i18n 两套都加**

`frontend/i18n.js` 的 `I18N.zh`（约 `:165`）与 `I18N.en`（约 `:567`）**各加一份**：

zh：

```javascript
    "settings.group.gmail": "学校邮箱（Gmail）",
    "settings.gmail_hint": "学校邮箱的邮件会转发到这里。请在 Google 账号设置里生成「应用专用密码」（需先开启两步验证），填在下面。注意：转发只对设置之后的新邮件生效，之前的历史邮件不会补过来。",
    "settings.gmail_user": "Gmail 地址",
    "settings.gmail_pass": "应用专用密码",
    "btn.save_gmail": "保存",
    "btn.clear_gmail": "清除",
    "status.gmail_need_both": "Gmail 地址和应用专用密码都要填",
    "status.gmail_saving": "正在保存…",
    "status.gmail_save_fail": "保存失败：",
    "status.gmail_saved_ok": "已保存到钥匙串",
    "status.gmail_clearing": "正在清除…",
    "status.gmail_clear_fail": "清除失败：",
    "status.gmail_cleared": "已清除",
    "status.gmail_saved_hint": "已保存",
```

en：

```javascript
    "settings.group.gmail": "School mail (Gmail)",
    "settings.gmail_hint": "Your school mail is forwarded here. Generate an App Password in your Google account settings (2-Step Verification required) and paste it below. Note: forwarding only applies to new mail arriving after you set it up — older messages are not backfilled.",
    "settings.gmail_user": "Gmail address",
    "settings.gmail_pass": "App password",
    "btn.save_gmail": "Save",
    "btn.clear_gmail": "Clear",
    "status.gmail_need_both": "Both the Gmail address and app password are required",
    "status.gmail_saving": "Saving…",
    "status.gmail_save_fail": "Save failed: ",
    "status.gmail_saved_ok": "Saved to Keychain",
    "status.gmail_clearing": "Clearing…",
    "status.gmail_clear_fail": "Clear failed: ",
    "status.gmail_cleared": "Cleared",
    "status.gmail_saved_hint": "Saved",
```

- [ ] **Step 5: 更新自检夹具**

`tools/check_mail_frontend.mjs:219-220` 的夹具去掉 `is_read`：

```javascript
    { index: 0, subject: "SYNTH-SUBJ-0", sender: "s0@example.invalid", received: "2026-09-01T00:00:00Z" },
    { index: 1, subject: "SYNTH-SUBJ-1", sender: "s1@example.invalid", received: "2026-09-02T00:00:00Z" },
```

若该文件里有断言专门查未读标记，一并删掉：

```bash
grep -n "is_read\|未读\|unread" tools/check_mail_frontend.mjs
```

- [ ] **Step 6: 跑三个自检**

```bash
node tools/check_mail_frontend.mjs && node tools/check_dom_ids.mjs && node tools/check_i18n_keys.mjs
```

预期：三个都退出码 0。

`check_dom_ids.mjs` 会验证新加的 5 个 id（`gmailAddress` / `gmailAppPassword` / `btnSaveGmail` / `btnClearGmail` / `gmailSavedHint`）在 HTML 里存在、且 JS 的 `$("…")` 引用没有悬空。
`check_i18n_keys.mjs` 会验证 zh/en 键集一致、无悬空引用、**死键数不超基线**（新加的 15 个键都被 `data-i18n` 或 `t()` 引用，不会计入死键）。

- [ ] **Step 7: 变异验证自检工具不是空转**

```bash
node tools/check_mail_frontend.mjs --mutate
```

预期：红的条数与改动前**一致**（记录改动前的数字作为基线，改动后必须相同）。若多了或少了，说明 Step 1 删 `is_read` 时动到了别的东西，**停下查清楚**。

- [ ] **Step 8: 提交**

```bash
git add frontend/ tools/check_mail_frontend.mjs
git commit -m "feat(mail): 设置页 Gmail 凭据一组；去掉 is_read 渲染（转发件恒未读，是假信息）"
```

---

## Task 9: 收紧断言 + 全量验证

**Files:**
- Modify: `tests/test_main.py`

**Interfaces:**
- Consumes: 全部
- Produces: 无

- [ ] **Step 1: 把 `<=` 改成 `==`**

`tests/test_main.py:1890` 现在是：

```python
    assert all(set(m) <= {"id", "subject", "sender", "received", "is_read", "index", "body"}
```

`<=` 是**子集**断言：删掉 `is_read` 它照样绿 —— 这正是本仓 memory 里记的「声明强于断言」。改成集合**相等**：

```python
    assert all(set(m) == {"id", "subject", "sender", "received", "index", "body"}
```

同时把该用例上方 `:1813` 的注释里的「id/subject/sender/received/is_read 五个键」改成「id/subject/sender/received 四个键（`is_read` 已随转发方案删除，见 spec §4.1）」。

- [ ] **Step 2: 清掉 test_main.py 里其余 `is_read`**

```bash
grep -n "is_read" tests/test_main.py
```

把各处 fixtures 里的 `"is_read": …` 逐处删掉。**每删一处就确认该用例的断言不依赖它。**

- [ ] **Step 3: 全仓确认无残留**

```bash
grep -rn "is_read" backend/ frontend/ tests/ tools/
```

预期：**无输出**。

```bash
grep -rn "_capture_token\|extract_bearer\|_ensure_mail_page\|_find_mail_page\|MarkAsRead" backend/ tests/ tools/
```

预期：**无输出**（OWA 层与邮件浏览器入口已删干净）。

- [ ] **Step 4: 全量测试**

```bash
.venv/bin/python -m pytest -q
```

预期：全绿，**0 failed**。Task 1-8 会删掉一批 OWA 用例、加上一批 IMAP 用例，总数会变；记录本任务的实际数字。

- [ ] **Step 5: 三个前端自检**

```bash
node tools/check_mail_frontend.mjs && node tools/check_dom_ids.mjs && node tools/check_i18n_keys.mjs
```

- [ ] **Step 6: 出网守卫自检**

```bash
.venv/bin/python -m pytest -q -k "net or guard or socket"
```

预期：全绿。确认新加的 IMAP 用例**一条都没真出网**。

- [ ] **Step 7: 变异批跑**

把 Task 4/5/6 三张表里的变异合并成一支脚本跑一遍（或逐条手工跑），确认**每条都让指定用例变红**。任何一条变异存活 = 那条用例在空转，**必须补强后重跑**。

- [ ] **Step 8: 提交**

```bash
git add tests/test_main.py
git commit -m "test(mail): 收紧字段断言为集合相等；清掉 is_read 残留"
```

---

## 收尾检查（全部任务完成后）

- [ ] 用户**手动端到端验证**：点「刷新邮件」能看到真实邮件；点「AI 总结」能出分组与 DDL
- [ ] 确认学校邮箱里的邮件**仍未读**（只读三件套真的生效了）
- [ ] 确认用户自己的 Chrome 里开着的 Outlook 标签页没有被导航走
- [ ] 列表里每封邮件的 `received` 都**非空**且能正常本地化显示（INTERNALDATE 回退在真实数据上生效 —— 单测钉的是形状，这里是真实转发件）
- [ ] 设置页提示语含「转发不回溯」说明（spec §11 的对应项）
- [ ] 用户知道**转发中断的症状是「邮件不再更新」而非报错**（spec §11）：靠列表里最近一封的时间自查，本轮不新增后台检测
- [ ] `tools/probe_gmail_imap.py` 已删除（Task 1 Step 5）
- [ ] 把实测结论与实现要点写进 memory
- [ ] **保持本地，先不推**（用户未授权推送）
