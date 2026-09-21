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

## 变异验证的执行纪律（本计划约定，**每个任务都适用**）

每张变异表都要按这个来跑。2026-09-21（Task 4 修复轮 1）实测的事故：驱动器在同一秒内对源文件做**等长度**替换，而 Python 的 `.pyc` 失效判据是 `(mtime, size)` ⇒ 下一次 `import` 复用**上一轮**编译的字节码，跑的其实是**上一个变异**的代码。

- **跑每行前先清掉** `backend/__pycache__` 与 `tests/__pycache__`。`-B`（或 `PYTHONDONTWRITEBYTECODE`）**只防「写」新缓存，不防「读」已存在的旧缓存** —— 单独用它等于没防。更稳的写法：每行给一个独立的 `PYTHONPYCACHEPREFIX`。
- **任何非红（绿 / 未红）的行，必须手工复现一次**再写进报告，不许直接当成「变异退化」。
- **报「红」也要说清怎么红的** —— 缓存假象同样能让红**张冠李戴**（报的是第 N 行的位置、跑的是第 N−1 行的代码），所以「红了」还不足以断言该行被判別住。判据是：红在**预期的那条用例**上、且**红的原因**正是该变异。
- 触发形状在变异表里很常见：等长重排、常量对调、格式串互换。
- **驱动器「写回 pristine」这一步也要防**：它必须还原到**本轮开始时的**工作区内容。2026-09-21 Task 4 修复轮 2 实测过一次事故：驱动器结尾写回的是**上一轮**留的备份，把本轮的改动**静默回滚**了（`git status` 不显示该文件，因为回滚后与 HEAD 一致 —— 反而更不容易发现）。做法：跑前把 `pristine` 备份的 sha256 与**当前工作区**的 sha256 对一次，不等就停下重取；跑完再对一次。
- **锚点命中数必须是 1**：命中 2 次说明这是个会改错地方的锚点（`days: int = DEFAULT_DAYS` 就同时出现在 `select_scope` 与 `list_mail` 上）。要么加足上下文，要么判 SKIP 报回，**不要挑第一个替换** —— 改错地方的变异红起来原因不同，等于换了道题。
- **🔴 活性探针的指纹公式：绝不要对 `repr(co_consts)` 取哈希。**（2026-09-21 Task 6 复审实测，控制器独立复现）
  含**嵌套 code object** 的函数（里面有列表推导 / 生成器 / 嵌套函数），其 `co_consts` 里那个 code object 的 `repr` **带内存地址**
  （`<code object <listcomp> at 0x1031466b0, file "…">`）⇒ 指纹**每次运行都不同**，无论变异有没有落盘。
  实测（同一个 pristine 源，连跑 3 次）：
  `_do_bodies` = `51d785cd` / `063bbcd9` / `6236f14e`，`_do_list` = `ed7ce795` / `c71657a5` / `32a5f0ff` —— **全不同**；
  而**不含**嵌套 code object 的 `_fetch_bodies_chunk` = `81d1d0c8` 三次恒定。
  ⇒ 对这三个含推导式的函数，`sha256(repr((co_code, co_consts, co_names)))` 报的「指纹 = 变」**一点鉴别力都没有**：
  哪怕变异根本没写进文件（陈旧 `.pyc`），它照样报「变」—— 正是它本该堵住的那条假绿通道。
  - **也别改用 `dis`**（我试过，会重蹈覆辙）：`dis.Bytecode(fn).dis()` 在外层函数里会把嵌套 code object 渲染成
    `LOAD_CONST <code object <listcomp> at 0x…>`，同样带地址 ⇒ 实测 `_do_bodies` = `1f37d35c` / `88d8b337` / `fb42c712`，**仍不稳定**。
  - **正确公式（已双向实测：跨进程稳定 + 变异敏感）**：递归指纹，嵌套 code object 用**自身递归指纹**而非 `repr`：
    ```python
    def stable(c):
        parts = [c.co_code, c.co_names, c.co_varnames, c.co_freevars, c.co_cellvars,
                 tuple(stable(k) if hasattr(k, "co_code") else k for k in c.co_consts)]
        return hashlib.sha256(repr(parts).encode()).hexdigest()
    ```
    实测：`_do_bodies` = `2cdef0e1` ×3、`_do_list` = `c54ff581` ×3、`_fetch_bodies_chunk` = `240c5df6` ×3（**稳定**）；
    对 `_do_list` 去掉批间节流 → `1334e895`，把 `if n:` 换成 `if 1:`（**等长**，最易失鲜的形状）→ `22bcd6b3`（**敏感**）。
  - **判据是两向的，缺一不可**：先证明它**稳定**（同一份源连跑 3 次同值），再证明它**敏感**（真做一次变异后变值）。
    只测其中一向都会翻车 —— 这次翻的正是「从不测稳定性、只看"变了没有"」。

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
| D | §5.3「**筛选**按 `INTERNALDATE` —— 「最近 N 天**收到**的邮件本就该按收到算」」 vs §5.6 骨架里那句 `# received 解析不出来的 → 保留` | **按 §5.3 执行**：窗口筛 INTERNALDATE（条目内部键 `_internaldate`），`received` 只作展示与退回 | **spec 自己前后不一致，本计划此前抄了 §5.6 那一侧。** §5.3 是带理由的规范句，§10.8 也复述「按 INTERNALDATE 筛选……照旧」；而 §5.6 的骨架沿用了**旧 OWA 实现的字段名** —— 那时 `received`（`receivedDateTime`）**就是到达时刻**，筛它是对的；换到 IMAP，`received` 改义成 `Date:` 头，那一行没跟着改。后果不是「差一天」：转发件的 `Date:` 是原始发信时间（可能几个月前），按它筛会**静默丢掉刚收到的旧邮件** —— 正是这个功能的目标场景。已同步更正 spec §5.6。 |

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
    - 窗口比的是条目里的**内部键 `_internaldate`（`datetime | None`，即 INTERNALDATE）**，**不是 `received`** —— 后者是 `Date:` 头（见下方「规划期裁定 D」）
    - `_internaldate` **不是对外契约的键**：Task 5 的 `_fetch_headers` 放进去、`_do_list` 在返回前剥掉（形状断言会盯着这件事）

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
    """缺时区的返回 None：naive 不带时区，无从判断它是哪一刻，宁可为空也不要当 UTC 往下传。

    （`received` 只作展示；窗口的取舍与 fail-open 守卫都在 `select_scope` 自己手里，
    比的是 `_internaldate`（INTERNALDATE），与本函数无关 —— 修复轮 1 更正。）
    """
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


# --- select_scope：按 INTERNALDATE 的纯日期窗口，fail-open（见裁定 D）---

def test_select_scope_keeps_recent_by_internaldate():
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    items = [{"_internaldate": datetime(2026, 9, 16, tzinfo=timezone.utc)},
             {"_internaldate": datetime(2026, 9, 1, tzinfo=timezone.utc)}]
    assert mail_client.select_scope(items, now, 7) == [items[0]]


def test_select_scope_filters_by_internaldate_not_date_header():
    """**本任务最重要的一条**：窗口按「收到时刻」，不按 `Date:` 头（spec §5.3）。

    Outlook→Gmail 转发件的 `Date:` 是**原始发信时间**（可能几个月前），而
    INTERNALDATE 是它落进 Gmail 的时刻。按 `Date:` 筛，会把「刚收到的旧邮件」
    静默丢掉 —— 那恰恰是这个功能存在的理由（用户就是为了收转发来的邮件才做的转发）。
    """
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    it = {"received": "2026-01-01T00:00:00+00:00",                     # Date 头：一月的
          "_internaldate": datetime(2026, 9, 16, tzinfo=timezone.utc)}  # 昨天到的
    assert mail_client.select_scope([it], now, 7) == [it]


def test_select_scope_keeps_missing_naive_or_bad_fail_open():
    """缺 `_internaldate` / `None` / naive —— 一律保留，且**不抛**。

    naive 与 aware 的 cutoff 比较会抛 TypeError；单封坏邮件不该炸穿整次抓取。
    """
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    items = [{"received": "???"},
             {"_internaldate": None},
             {"_internaldate": datetime(2026, 1, 1)}]      # naive
    assert mail_client.select_scope(items, now, 7) == items


def test_select_scope_has_no_unread_branch():
    """未读不再影响结果：老邮件只要超出窗口就丢弃，不管它读没读。

    （is_read 键已从契约删除；这里刻意塞一个 is_read=False 的老邮件证明它不再被保留。）
    """
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    items = [{"_internaldate": datetime(2026, 1, 1, tzinfo=timezone.utc),
              "is_read": False}]
    assert mail_client.select_scope(items, now, 7) == []


def test_select_scope_boundary_is_inclusive():
    """正好 `days` 天前算「最近 days 天」内 —— 边界收进来比漏掉安全。

    （**从 OWA 时代的用例搬来的，修复轮 2**：原版喂的是旧义的 `received` + `is_read`，
    新实现不看那两个键 ⇒ 它变成恒真的绿。Task 5 会整体删掉 OWA 块，
    这条性质若只留在旧块里就会**无声消失**。搬过来时改成 `_internaldate` 形状。）
    """
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    items = [{"_internaldate": now - timedelta(days=7)},      # 正好 7 天前：在内
             {"_internaldate": now - timedelta(days=8)}]      # 超一天：在外
    assert mail_client.select_scope(items, now, 7) == [items[0]]


def test_select_scope_defaults_to_default_days():
    """不传 `days` 时走 `DEFAULT_DAYS` —— spec 钉的 7。（同样是从 OWA 块搬来的。）"""
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    items = [{"_internaldate": now - timedelta(days=7)},
             {"_internaldate": now - timedelta(days=8)}]
    assert mail_client.select_scope(items, now) == [items[0]]
    assert mail_client.DEFAULT_DAYS == 7


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
    """附件内容绝不能混进正文。

    附件刻意用 **text/plain**：application/octet-stream 那种形状光靠 ctype 过滤
    （既非 text/plain 也非 text/html）就被排除了，**disposition 守卫在它身上不可达** ——
    实测删掉守卫用例照样绿（覆盖缺口，修复轮 1 更正）。text/plain 附件才是唯一
    能走到守卫那一步的形状。
    """
    m = EmailMessage()
    m.set_content("正文", subtype="plain", charset="utf-8")
    m.add_attachment(b"ATTACHMENT-BYTES", maintype="text",
                     subtype="plain", filename="a.txt")
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
    naive 不带时区，无从判断它是哪一刻，宁可为空也不要把坏值当 UTC 往下传。

    本函数的唯一生产调用点是 `received_dt`（`Date:` 头 → 展示 / 喂给模型）；
    **窗口的取舍不看这里** —— `select_scope` 比的是 `_internaldate`（INTERNALDATE），
    fail-open 守卫在 `select_scope` 自己手里。（修复轮 1 更正：原句称本函数是
    「唯一一条 fail-closed 路径、单封坏邮件能炸穿整个 select_scope」，那条因果
    在裁定 D 之后就不存在了 —— 归到 None 既不决定保留，也炸不穿任何东西。）

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
    注：`parsedate_to_datetime` 同样吃得下这个格式（spec §10.8 的**离线复验**，
    不是对着真实邮箱量的「实测」，两级不可混称），
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
    那是静默的坏值。两个都没有才给空串 —— **这只影响显示**：保留与否不看这个键，
    `select_scope` 比的是 `_internaldate`（INTERNALDATE），见裁定 D。
    （修复轮 1 更正：原句写「此时 select_scope 会 fail-open 保留它」，把保留
    归因到了本函数的产物上；结论碰巧一致（该情形下 `_internaldate` 也是 None，
    同样 fail-open），但因果是错的，照着它接线会错。）
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

**改写** `select_scope`（窗口改筛 INTERNALDATE、删「未读」分支，保留 fail-open —— 见裁定 D）：

```python
def select_scope(items: list[dict], now: datetime,
                 days: int = DEFAULT_DAYS) -> list[dict]:
    """取「最近 days 天**收到**」的条目（spec §5.3）。

    窗口比的是**收到时刻**（条目内部键 `_internaldate`，由 `_fetch_headers` 放进去），
    不是 `received` —— 那是 `Date:` 头，即**原始发信时间**；转发件上它可能很旧，
    按它筛会把「刚收到的旧邮件」静默丢掉。`_internaldate` 不是对外契约的键，
    `_do_list` 在返回前剥掉。

    缺失 / `None` / naive / 任何不可比的值一律按「保留」处理 —— fail-open：
    宁可多给一封，也不要因单封坏邮件静默丢件，更不要让它抛穿整次抓取。

    没有「未读」分支：转发过来的邮件在 Gmail 里恒为未读，而只读访问永不改变它，
    那个分支恒真、等于没有（spec §4.1）。
    """
    cutoff = now - timedelta(days=days)
    out = []
    for it in items:
        ts = it.get("_internaldate")
        if not isinstance(ts, datetime) or ts.tzinfo is None or ts >= cutoff:
            out.append(it)
    return out
```

- [ ] **Step 4: 跑测试**

```bash
.venv/bin/python -m pytest tests/test_mail_client.py -q
```

预期：**新用例全绿**；OWA 层的旧用例此时应仍绿（OWA 层还没删）。

**例外 —— 模块顶部「范围筛选」一节里的 `select_scope` 旧用例，本任务就删掉**（连同只服务它们的 `_mk` 助手；`NOW` 别删，OWA 的 `collect_items` 用例还在用）。理由是它们测的是**保留下来的函数** `select_scope`，所以既不属于「OWA 层」也不属于「Task 5 的活」，但喂的是旧义的 `received` + `is_read`：
- `test_select_scope_keeps_recent_read_and_all_unread` —— 断言的是**已删除的「未读」语义**（新行为由 `test_select_scope_has_no_unread_branch` 钉）；
- `test_select_scope_boundary_is_inclusive`、`test_select_scope_defaults_to_default_days` —— 性质仍然有效，**已按 `_internaldate` 形状搬到本任务的用例里**（同名），旧形状那份删掉；
- `test_select_scope_unparsable_date_is_kept`、`test_select_scope_no_offset_date_is_kept_and_does_not_raise` —— 新实现不看 `received` ⇒ 它们**恒真地绿**（fail-open 让断言必然通过），是「绿着的用例其实什么都没测」。性质由 `test_select_scope_keeps_missing_naive_or_bad_fail_open` 接手。

> 为什么不留给 Task 5：`select_scope` 的语义是**本任务**改的，它的旧测试面就属于本任务；留着的代价不是多几条用例，而是**绿着的用例在说假话**（读它的契约会以为窗口比的是 `received`），以及几条恒红用例把「红」变成噪音 —— 真正的破坏会藏在里面。

**本任务结束时应当只剩一条红，且是已知的那条**：`test_list_mail_returns_scoped_items`（OWA 层，猴补 `_post_json` + 冻结 `datetime`）。它红是因为 OWA 路径给不出 `_internaldate` ⇒ 新实现 fail-open 全留，与它期望的 `{"近读", "旧未读"}` 不符；而它期望里那个 `旧未读` 本身**就是已删除的「未读」语义** ⇒ 这条用例断言的是一条**走不通的路 + 一个不存在的特性**。它归 Task 5 连同 OWA 层一起删（计划已点名）。**除它以外还有红 ⇒ 停下来查，不要当成「预期内」放过。**

- [ ] **Step 5: 变异验证**

| 变异 | 应红的用例 |
|---|---|
| `decode_mime_header` 改成 `return raw or ""`（不解码） | `test_decode_mime_header_decodes_base64_utf8`、`..._quoted_printable`、`..._joins_adjacent_encoded_words`、`test_sender_name_decodes_encoded_display_name` |
| `sender_name` 改成 `return raw or ""` | `test_sender_name_prefers_display_name`、`..._falls_back_to_address` |
| `_parse_ts` 改回 `fromisoformat`（不再吃 RFC 2822） | `test_parse_ts_converts_to_utc_aware`、`test_received_dt_reads_date_header` |
| `_parse_ts` 的 `ts.tzinfo is None` 判断改成 `False`（接受 naive） | `test_parse_ts_rejects_naive` |
| `_iso` 改成 `dt.replace(tzinfo=None).isoformat()`（丢掉时区） | `test_iso_keeps_timezone_offset` |
| `select_scope` 改成按 `received`（`Date:` 头）筛选 —— **必须忠实地用 `datetime.fromisoformat` 解 `received`** | `test_select_scope_filters_by_internaldate_not_date_header` |
| `select_scope` 的 `ts.tzinfo is None` 守卫去掉（把 naive 也拿去比） | `test_select_scope_keeps_missing_naive_or_bad_fail_open` |
| `select_scope` 改回丢弃坏日期（`continue`，而非保留） | `test_select_scope_keeps_missing_naive_or_bad_fail_open` |
| `select_scope` 改回 OWA 原语义「未读 **∪** 最近 N 天」—— 在现有 keep 条件的**末尾加一个 OR 分支**：`... or ts >= cutoff or not it.get("is_read"):`（**必须是 OR**；**别按字面「嵌进分支内部」**，见本表下方警告） | `test_select_scope_has_no_unread_branch` |
| `select_scope` 的 `ts >= cutoff` → `ts > cutoff`（边界由「含」变「不含」） | `test_select_scope_boundary_is_inclusive` |
| `select_scope` 的 `days: int = DEFAULT_DAYS` → `days: int = 30`（默认值不再是 7）。**锚点要带上下文**：裸串 `days: int = DEFAULT_DAYS` 在文件里命中 **2 次**（`select_scope` 与 `list_mail`），拿裸串当锚点会改错地方 —— 而「改了 `list_mail` 的默认值」是**另一个变异**，红的原因也不同（张冠李戴的入口）。用 `def select_scope(...)` 两行上下文定位 | `test_select_scope_defaults_to_default_days` |
| `message_body` 里把 `plains` 与 `htmls` 优先级对调 | `test_message_body_prefers_plain_in_alternative` |
| `message_body` 删掉 attachment 跳过 | `test_message_body_skips_attachments` |
| `header_item` 加回 `"is_read": False` | `test_header_item_shape_exactly_four_keys` |
| `header_item` 去掉 `or internaldate` 回退 | `test_header_item_falls_back_to_internaldate` |
| `header_item` 改成 `_iso(internaldate or received_dt(msg))`（顺序反了） | `test_header_item_prefers_date_header_over_internaldate` |
| `parse_internaldate` 的格式串改成 `%Y-%m-%d %H:%M:%S %z`（写成 ISO 顺序） | `test_parse_internaldate_reads_imap_format` |

> **⚠️ T4-9 别按字面把 `is_read` 判断「嵌进 keep 分支」—— 那是个语义空操作。**
> （2026-09-21 实施者实测报回；控制器已回源码逐条复核，下面是核过的算式，不是转述。）
>
> 该用例只有**一封**邮件（`tests/test_mail_client.py:547-555`）：
> `{"_internaldate": datetime(2026, 1, 1, tzinfo=utc), "is_read": False}`，`now = 2026-09-17 12:00 UTC`、`days = 7`
> ⇒ `cutoff = 2026-09-10`。现有 keep 条件（`backend/mail_client.py:229`）是
> `if not isinstance(ts, datetime) or ts.tzinfo is None or ts >= cutoff:`。
>
> - **按字面嵌进去**（`out.append(it)` 外面再包一层 `if not it.get("is_read"):`）：外层三项**全假**
>   （`ts` 是 aware datetime、`2026-01-01 < cutoff`）⇒ 内层**永不可达** ⇒ `out == []` ⇒ 用例**照绿**，
>   产出**假的 `SURVIVED`**。
> - **OR 形式**（条件末尾加 `or not it.get("is_read")`）：`not False` 为真 ⇒ 该邮件被保留 ⇒ `out == [item]`
>   ⇒ `assert ... == []` 失败，**红**。
>
> 而 `SURVIVED` 最省事的处置恰恰是**把那条用例删掉** —— 正是本计划在 Task 4 结束态那节点名警告过的路。
> 一般规律：**变异要打在承重行上** —— 先问「这一行在什么输入下会改变返回值」，答「没有」的那一行是装饰，不是变异对象。

> **⚠️ 本表不要写「去掉 `astimezone`」类的变异 —— 它们全部退化。** 原因是一个结构性事实：
> **UTC 归一本文件做了两遍** —— `_parse_ts` 与 `parse_internaldate` 各做一次
> `astimezone(timezone.utc)`，`_iso` **再做一次**。而三条日期用例断言的都是 `_iso(dt)`，
> 所以只改「表示形式」（去掉某一处的 `astimezone`）的变异，最终串逐字不变，**用例照绿**。
> 2026-09-21 本地实跑（三行全绿 ⇒ 全退化）：`parse_internaldate` 去掉 `astimezone` → `2026-09-17T08:31:00+00:00`（不变）；
> `_parse_ts` 去掉 `astimezone` → 同上（不变）；`_iso` 去掉 `astimezone` → `+0000` 输入本已 UTC，亦不变。
> ⇒ **能咬的只有两类**：**丢偏移**（`_iso` 那行）或**改错时刻/格式**（`_parse_ts` 的 `fromisoformat` 行、
> `parse_internaldate` 的格式串行）。表已按此选行。
> 历史：本行先写作「改用 `parsedate_to_datetime`」（本地实跑判为等价 ⇒ 退化），改后被复审指出**替换后的
> 「去掉 `astimezone`」同样退化**，遂换成现在这条 —— 它钉的是该用例 docstring 声称的性质（日月年短横线这个 IMAP 专有顺序）。
>
> **另一处陷阱（本表新增的 `select_scope` 那一行）**：该行要求「按 `received`（`Date:` 头）筛选」，
> 实现时**必须忠实地用 `datetime.fromisoformat` 去解 `received`** —— 若图省事拿 `_parse_ts`（只吃 RFC 2822）
> 去解那个 ISO 串，它会返回 `None`，条目被 fail-open 全量保留，**用例照绿**。
> 那正是本表要钉的缺陷被另一种错法掩盖的样子 —— 两处错互相抵消，比只有一处更难看见。

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

> ⚠️ **本任务结束时「全量 pytest」是红的，而且必须让它红着 —— 这不是缺陷，是设计。** 已核实（控制器，2026-09-21）：
> - `tests/test_main.py` 里有 **7 条** `test_mail_summarize_*` 用例会**红**：`test_mail_summarize_joins_bodies_and_groups`(:1859) / `test_mail_summarize_indexes_are_contiguous`(:1894) / `test_mail_summarize_returns_mails_too`(:1925) / `test_mail_summarize_fetch_bodies_failure_reports_mail_phase`(:1950) / `test_mail_summarize_llm_failure_reports_llm_phase`(:1968) / `test_mail_summarize_no_mail_skips_llm`(:1983) / `test_mail_summarize_missing_body_falls_back_to_empty`(:1996)。
> - **机制**：它们都 `monkeypatch.setattr(mail_client, "fetch_bodies", …)`，而 `monkeypatch.setattr` 默认 `raising=True`，属性不存在时抛 `AttributeError`（已用最小探针在 pytest 9.1.1 上实测复现：`AttributeError: <module …> has no attribute 'gone'`）。
> - `backend/main.py:1029` 的 `mail_client.fetch_bodies(mails)` 也会在**运行时**炸（只有走到 AI 总结的取正文那段才会触发，单测抓不到）。
> - **Task 6 会把它治好**：新 `fetch_bodies` 与旧**同名同签名**（`(items: list[dict]) -> dict`）—— 外部契约不变正是本计划的前提，那 7 条用例是猴补公开名，会自动恢复。
>
> - **标签更正（Task 5 实测，控制器 2026-09-21 核）：**这 7 条在 pytest 9.1.1 上记作 **`FAILED`，不是 `ERROR`**。机制完全不变（`raising=True` 的 `AttributeError`），差别只在结局标签：`ERROR` 是 fixture/收集阶段炸，而这里的 `setattr` 写在**用例体内**。**集合与计数逐字对得上**，验收口径不受影响 —— 但「预期 ERROR」这个写法本身是错的标签，读的人会去找收集阶段的报错。原措辞已在此更正，留痕。
>
> 因此本任务的验收口径是：**`pytest tests/test_mail_client.py -q` 全绿**；全量 `pytest -q` 预期**恰好这 7 条红**（标签 `FAILED`）。**除这 7 条以外还有红 ⇒ 停下来查。** 三条禁令：**不要**为了让全量变绿而留一个 stub `fetch_bodies`（那会把真断线伪装成绿）；**不要**改 `tests/test_main.py`（Task 9 才碰它）；**不要把 `fetch_bodies` 改成 `raising=False` 能修的东西**——病根是被调用的符号没了，不是猴补写得不对。报告里要把这 7 条**如实列为预期红**，不得写成「全绿」。

> ⚠️ **还有一个更早的中间态：Task 4 交付态到本任务完成之间，`list_mail` 返回的是「整个收件箱」（Task 4 复审发现，控制器 2026-09-21 裁定）。**
> - **机制**：Task 4 把 `select_scope` 改成比 `_internaldate`；而**此刻仍然在线**的 OWA 路径产出的条目**没有这个键**（`backend/mail_client.py:134` 给的是 `DateTimeReceived`）⇒ 新 `select_scope` 一律 **fail-open 全留** ⇒ `collect_items` 的翻页循环（`:416-427`）把 FindItem 的整个收件箱原样返回。
> - 窗口此前**只由 `select_scope` 一家承担**——`:418` 的注释自己写着「inbox 无服务端日期过滤」。所以这不是「多留了几封」，是**筛选整个消失了**。
> - **裁定：接受这个中间态，不加临时防线。** 理由三条：① Task 5/6 连续执行、中间不交付；② 失败方向是**多给**（不是丢件、不是静默错误值）；③ 只读路径，不置已读、不落盘、不泄凭据。代价是多花 LLM token（`trim_bodies` 的下限机制会让总额随 n 线性增长）。
> - **为什么不加「无 `_internaldate` 时退回旧口径」那种临时防线**：那是一段只活一个任务的代码，要写、要测、还要**记得删** —— 忘了就是永久的第二套筛选语义，比多花的 token 贵得多。且它会让 Task 5 的删除面变得不干净。
> - **操作后果（必须遵守）**：**本任务完成之前，不要启动 App、不要做端到端手工验证**（Task 9 收尾清单里「点刷新邮件能看到真实邮件」那条属于 Task 9 之后）。中间态要跑就只跑单测。

> 🔴 **本任务最大的一个陷阱：RED 阶段跑新用例，会驱动旧 OWA 实现去动真实浏览器。**（Task 5 实施时**真的发生了一次**，控制器 2026-09-21 补记）
> - **机制**：TDD 要求**先写测试再改实现**。此刻 `mail_client.list_mail` **还是旧的 OWA 实现**，而新用例是围绕 `_install(...)` 换上的 IMAP 替身写的 —— 替身装了，旧实现却**根本不看它**，照样走 `banweb._on_browser_thread(...)` → `_ensure_mail_page()` → **真实 Chrome**。`tests/conftest.py` 的 socket 守卫拦不住它（浏览器是独立进程，不是 Python 发的 socket）。
> - **实测后果（Task 5 复审 Important 3 修正后，控制器 2026-09-21 逐行核过 banweb 源码）**：
>   1. **附着（attach）是确定发生的**，不是「可能」。邮件路径必经
>      `_ensure_mail_page` → `_ensure_browser` → `_chrome` 的 `connect_over_cdp("http://127.0.0.1:9339")`（`backend/banweb.py:919`）。
>      实施者报告里「我没有、也不会去启动/附着/杀死任何浏览器」这句**与它自己承认的路径自相矛盾** —— 要走到邮件页就必须附着。**如实记为「确定附着过」。**
>   2. **开/切标签页很可能发生**：`_find_mail_page` 会 `ctx.new_page()`（`backend/banweb.py:668`），随后 `_capture_token` 对邮件页 `goto(MAIL_URL)`。**记为「很可能对那个浏览器开过一个邮件标签页」。**
>   3. **最重的一条：`kill -9` 的路径已经被武装，只差 60 秒。** `_on_browser_thread` 的看门狗在
>      `fut.result(timeout=BROWSER_OP_TIMEOUT)`（`backend/banweb.py:770`）上等 **`BROWSER_OP_TIMEOUT = 180`** 秒（`:77`）；
>      超时（`concurrent.futures.TimeoutError`）→ 第 0 次尝试走 `_recover_browser_subprocess()`（`:777`）→
>      **无条件**调 `_kill_zombie_chrome()`（`:716`）→ `lsof -tiTCP 9339 -sTCP:LISTEN` 拿 PID 逐个 `kill -9`，
>      再 `pkill -9 -f <PROFILE_DIR>` 兜底（`:839-873`）。**那会杀掉用户正在用的 PID 24178。**
>      那次运行是在 **120 秒**被 `TaskStop` 掐断的 —— **120 < 180，所以杀进程那一步根本没轮到**。
>      **是「掐断得够早」而不是任何安全设计挡住了它。**
>      （`_kill_zombie_chrome` 自己的 docstring 写着「只在该端口已连不上（connect_over_cdp 失败）时才会走到这里，不会误杀活窗口」
>      —— 这句话对**看门狗超时这个调用点不成立**，那是个**无条件**调用。该假声明已登记为终审待办，见 ledger Ruling AX。）
>  4. **修正后的完整记述**：「确定附着过；很可能开过/切过一个邮件标签页；一条 `kill -9` 活窗口的路径已武装、
>      靠 120 秒的掐断赶在 180 秒看门狗之前活了下来。」**报告原记述对「标签页状态」这一面是准的，作为「可达副作用清单」则偏窄。**
> - **因此本任务的 RED 与全部变异运行，必须先禁掉 banweb 的浏览器边界再跑**（把 `banweb._ensure_mail_page` / `_on_browser_thread` / `_retry_once` 临时替换成抛异常的东西）。**它不是卫生习惯，是承重的**：它挡的不只是「多发一次导航」，而是上面第 3 条那条 `kill -9` 活窗口的路径（长跑必炸，且**没有任何超时会救你** —— 看门狗本身就是那个杀手）。这条守卫只在 RED/变异阶段必要：GREEN 之后 `mail_client` 已经不再 import `banweb`，浏览器路径从取信层消失。
> - **教训（本仓通用）**：对一个**正要被替换掉的 I/O 层**跑新用例，先假设旧实现会做出它最擅长做的坏事 —— 先把那条边界堵上，再跑。

- [ ] **Step 1: 写失败的测试**

**整体替换** `tests/test_mail_client.py` 的 OWA 部分（保留 Task 4 加的纯函数用例）。文件头的 import 与假连接：

> ⚠️ **「范围筛选」一节的 `select_scope` 旧用例已在 Task 4 删掉了**（连同 `_mk`），`NOW` 还在、留给本任务其余的 OWA 用例。本任务替换 OWA 块时**顺手确认它们确实不在**：若还在（例如某人回滚了 Task 4），它们喂的是旧义的 `received` + `is_read`，新实现两个键都不看 ⇒ **恒真地绿**，谁照着它读契约就会以为窗口比的是 `received`。留着 `NOW` 是因为 OWA 的 `collect_items` 用例还在用它，别一起删。

> 另有一条**红着的** OWA 用例归本任务删：`test_list_mail_returns_scoped_items`（猴补 `_post_json` + 冻结 `datetime`，期望集合 `{"近读", "旧未读"}` 里那个 `旧未读` **就是已删除的「未读」语义**；而 OWA 路径给不出 `_internaldate` ⇒ 新实现 fail-open 全留）。它断言的是一条**走不通的路 + 一个不存在的特性**，是 Task 4 结束态**唯一允许存在的红**；删掉它，这条红线才算清。

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
    """_do_list 真的调用了 select_scope，参数正确，并**剥掉内部键后**原样返回它的结果。

    （其余 I/O 用例把 select_scope 换成了恒等函数，所以这一条是唯一
    「接线没断」的证据 —— 删掉 `return select_scope(...)` 只有它会红。）

    spy **原样返回收到的条目**（带着 `_internaldate`），于是这条同时钉住
    `_do_list` 的剥离：忘了剥 ⇒ 末两条断言红。`test_header_item_shape_exactly_four_keys`
    只看 `header_item` 的产出，够不到 `_do_list` 的返回（**覆盖缺口，修复轮 1 补上**；
    补之前用 `[{"id": "sentinel"}]` 那种不带内部键的返回值，剥与不剥都绿。）
    """
    seen = {}

    def spy(items, now, days):
        seen["ids"] = [it["id"] for it in items]
        seen["days"] = days
        seen["now_aware"] = now.tzinfo is not None
        seen["intd"] = [it.get("_internaldate") for it in items]
        return items

    _install(monkeypatch, _FakeIMAP(headers={b"1": _hdr(), b"2": _hdr()}), scope=spy)
    out = mail_client.list_mail(3)
    assert seen["ids"] == ["1", "2"]
    assert seen["days"] == 3
    assert seen["now_aware"], "now 必须是 aware，否则与 cutoff 比较会抛 TypeError"
    assert all(isinstance(x, datetime) and x.tzinfo is not None for x in seen["intd"]), \
        "交给 select_scope 的 _internaldate 必须是 aware datetime —— 串会被 fail-open 静默放过，窗口就死了"
    assert [set(it) for it in out] == [{"id", "subject", "sender", "received"}] * 2, \
        "对外契约恰好四个键 —— _internaldate 是内部键，_do_list 返回前必须剥掉"
    assert all("_internaldate" not in it for it in out)
    assert [it["id"] for it in out] == ["1", "2"]


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
        internaldate = parse_internaldate(intd.group(1).decode()) if intd else None
        msg = message_from_bytes(raw if isinstance(raw, bytes) else b"")
        item = header_item(m.group(1).decode(), msg, internaldate)
        # 窗口比的是收到时刻（spec §5.3），对外契约却只有四个键 ——
        # 收到时刻放内部键，`_do_list` 在返回前剥掉（形状断言盯着这件事）。
        item["_internaldate"] = internaldate
        out.append(item)
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
        kept = select_scope(items, datetime.now(timezone.utc), days)
        # 剥掉内部键 `_internaldate`：对外契约恰好四个键（spec §4）
        return [{k: v for k, v in it.items() if k != "_internaldate"} for it in kept]
    finally:
        _logout_quietly(conn)


def list_mail(days: int = DEFAULT_DAYS) -> list[dict]:
    """拉取「最近 days 天」的邮件（不含正文）。全程只读，不改已读状态。

    **不**走 banweb 的浏览器线程，也**不**重试：认证失败即停（spec §5.8）。
    """
    return _do_list(days)
```

同时把模块 docstring 整段重写，说明：这条路是 IMAP、为什么放弃 OWA（指向旧 spec §3 的实测）、只读三件套是什么。

> ⚠️ **已知缺口，故意留到 Task 6 补：头部批次循环没有节流。** `_do_list` 的
> `for chunk in _chunks(uids, HEADER_BATCH)` 逐字照抄自本计划，**里面没有 `time.sleep(_THROTTLE)`**；
> 而全局约束写着「批量 `UID FETCH` 之间留间隔」。这是**计划自身的矛盾**（Task 5 实施者按 brief 逐字实现、
> 没有自行加一段零覆盖的 sleep，处置正确）。缺口的归属与补法见 **Task 6 Step 3b** ——
> 放在那里是因为 `_THROTTLE` 与 `time` 到 Task 6 才第一次真正被消费，同一步里连用例与变异一起补齐。

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
| `_fetch_headers` 的 FETCH 命令去掉 `INTERNALDATE` | `test_list_mail_fetches_internaldate`（**只咬得动这一条**，见下） |
| `_fetch_headers` 不调 `parse_internaldate`（`internaldate = None`） | `test_list_mail_uses_internaldate_when_date_header_missing` |

> **为什么第 9 行必须拆成两行（Task 5 实测后拆分，控制器 2026-09-21）**：原写成「改命令」一行、
> 期望两条都红，**实测只红一条**。原因在替身而不在被测代码：`_FakeIMAP.uid` 的 FETCH 分支
> **无条件**把 `INTERNALDATE "…"` 拼进它返回的 meta blob，而 `_fetch_headers` 是**正则扫这个 blob**
> 取 INTERNALDATE 的 —— 命令里写不写 INTERNALDATE，替身的回答一模一样。所以「改命令」这一行
> 只能咬住「命令串里得有 INTERNALDATE」那条断言，够不到「拿它当退路」那条。
> **该用例本身不弱**：把变异改成「不调 `parse_internaldate`」，它当场变红（`assert '' == '2026-09-17T20:00:00+00:00'`）。
> **一般化的教训**：变异要喂到「断言真正比较的那个对象」上 —— 喂给一个**替身会忽略的输入**
> （命令串、参数、字段名）时，替身的补全会让变异静默失效；而它表现为「用例不该红却没红」，
> 最省事的处置（把用例删了或改成恒真）恰好会**拆掉真正的守卫**。
| `_do_list` 删掉 `return select_scope(...)`（改成 `return items`） | `test_do_list_applies_select_scope` |
| `_do_list` 里 `select_scope(items, now, days)` 的 `days` 写死成 `DEFAULT_DAYS` | `test_do_list_applies_select_scope` |
| `_do_list` **不剥** `_internaldate`（返回时原样带着内部键） | `test_do_list_applies_select_scope`（末两条断言：键集合恰好四个、`_internaldate` 不在） |
| `_fetch_headers` **不往条目里放** `_internaldate`（只放 `received`） | `test_do_list_applies_select_scope`（`seen["intd"]` 断言：交给 `select_scope` 的必须是 aware datetime） |

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

- [ ] **Step 3b: 给 Task 5 的头部批次循环补上节流（控制器 2026-09-21 裁定加入；Task 5 §Step 3 的说明里已留指针）**

**为什么落在本任务**：全局约束写着「批量 `UID FETCH` 之间留间隔」，而 Task 5 的 `_do_list` 逐字代码里**没有** `time.sleep(_THROTTLE)` —— 这是**计划自身的矛盾**。Task 5 实施者按 brief 逐字实现、没有自行加一段**零覆盖**的 sleep（处置正确，已在报告里报回）。`_THROTTLE` 与 `time` 到本任务才第一次被真正消费，所以连同**用例与变异**一起在此补齐 —— 一段没有任何用例钉住的 sleep 和没有它一样糟。

**改 `backend/mail_client.py` 的 `_do_list`，批次循环从两行变四行：**

```python
        items: list[dict] = []
        for n, chunk in enumerate(_chunks(uids, HEADER_BATCH)):
            if n:
                time.sleep(_THROTTLE)      # 批次之间留间隔（全局约束）
            items.extend(_fetch_headers(conn, chunk))
```

**加一条用例**（放在 `tests/test_mail_client.py` 的列表一节，与 Task 5 的其它 I/O 用例同格式；它是 `test_fetch_bodies_batches_and_throttles` 的镜像）：

```python
def test_list_mail_batches_and_throttles(monkeypatch):
    """51 封 → 2 批（HEADER_BATCH=50），批间有 sleep（全局约束：批量 UID FETCH 之间留间隔）。"""
    sleeps = []
    monkeypatch.setattr(mail_client.time, "sleep", lambda s: sleeps.append(s))
    ids = [str(i) for i in range(51)]
    fake = _install(monkeypatch, _FakeIMAP(headers={i.encode(): _hdr() for i in ids}))
    mail_client.list_mail(7)
    header_fetches = [c for c in fake.sent if c[0] == "uid" and c[1] == "FETCH"]
    assert len(header_fetches) == 2
    assert sleeps == [mail_client._THROTTLE]
```

> **两处要按实际文件核对，不符就以文件为准并在报告里写明**（Task 5 是照 brief 转录的，替身的名字或构造签名可能有细微差异）：`_FakeIMAP` 的构造参数名、`_hdr()` 的签名。**若实际差异会让这条用例变成「显然通过」，停下来报回**，别改成恒真的写法。

- [ ] **Step 3c: 补上 `_uidvalidity_from_status` 的覆盖（Task 5 复审 Important 2，控制器 2026-09-21 裁定归本任务）**

**为什么归本任务**：Task 5 交的 `_uidvalidity_from_status`（STATUS 退路）**零覆盖** —— 替身 `_FakeIMAP` 只定义了 `login`/`select`/`uid`/`logout`，**没有 `status`**，所以那条 `except Exception: return ""` 与 `typ != "OK"` 分支从来没有被执行过；唯一提到它的用例（`tests/test_mail_client.py` 里 `monkeypatch.setattr(mail_client, "_uidvalidity_from_status", lambda conn: "")` 那条）是把它**猴补掉**。而它是「主路径的形状假设若错了」与「用户看到一条硬错误」之间**唯一的**东西。改动落在同一个文件、你是下一个实现者，故在此补。

**先给替身加一个 `status`：** 构造参数新增 `status_reply=("OK", [b'"INBOX" (UIDVALIDITY 12345)'])`，并加方法

```python
    def status(self, mailbox, what):
        self.sent.append(("status", mailbox, what))
        return self.status_reply
```

**两条用例：**

```python
def test_uidvalidity_from_status_parses_reply(monkeypatch):
    """主路径（EXAMINE 的 untagged 响应）读不到时，退到 STATUS 再问一次。"""
    _install(monkeypatch, _FakeIMAP(untagged={}))       # 主路径给不出 ⇒ 必须走退路
    mail_client.list_mail(7)
    assert mail_client._uidvalidity == "12345"


def test_uidvalidity_from_status_rejects_non_ok(monkeypatch):
    """非 OK 的 STATUS 回答绝不能被当成有效 UIDVALIDITY。"""
    _install(monkeypatch, _FakeIMAP(
        untagged={}, status_reply=("NO", [b"NO [SERVERBUG] UIDVALIDITY 99999 unavailable"])))
    with pytest.raises(mail_client.MailError) as ei:
        mail_client.list_mail(7)
    assert "UIDVALIDITY" in str(ei.value)
```

> **第二条的 payload 是刻意选的**：里面**含一个像模像样的 `UIDVALIDITY 99999`**。这正是「漏掉 `typ != "OK"` 检查」会静默取到错值的那种输入 —— 换个纯文本报错（如 `b"boom"`）就咬不动了（正则匹配不到，退化成 `""`，结局仍是报错）。**别把它改简单。**

**变异表补两行：**

| 变异 | 应红的用例 |
|---|---|
| `_uidvalidity_of` 不再退到 `_uidvalidity_from_status`（读不到就直接 `return ""`） | `test_uidvalidity_from_status_parses_reply` |
| `_uidvalidity_from_status` 去掉 `typ != "OK"` 检查 | `test_uidvalidity_from_status_rejects_non_ok` |

> **`except Exception: return ""` 那条分支不单独覆盖**（控制器裁定，记录在案）：它 err 之后的**用户可见结局与「非 OK」完全相同**（都是 `""` → `_connect` 抛同一条 `MailError`），再写一条只是重复同一个结局。若你发现两者结局其实不同，**停下来报回**。

- [ ] **Step 4: 跑测试**

```bash
.venv/bin/python -m pytest tests/test_mail_client.py -q
```

预期：全绿。**并且全量 `pytest -q` 也要全绿** —— Task 5 留的那 7 条预期红（`tests/test_main.py` 的 `test_mail_summarize_*`，`AttributeError: … has no attribute 'fetch_bodies'`）在本任务用同名同签名补回后**必须自动愈合**。若全量还有红，**先确认那 7 条确实已绿**，再查别的：`backend/main.py:1029` 的运行时断裂（Task 5 的中间态）也到这一步才算治好。**从本任务起，全仓不应再有任何预期红**（Task 7 会另外删掉它自己那批用例，那是有意的）。

- [ ] **Step 5: 变异验证**

| 变异 | 应红的用例 |
|---|---|
| `BODY.PEEK[]` → `BODY[]` | `test_fetch_bodies_uses_body_peek` |
| 去掉 `if n: time.sleep(_THROTTLE)` | `test_fetch_bodies_batches_and_throttles` |
| 去掉 UIDVALIDITY 比对 | `test_fetch_bodies_raises_on_uidvalidity_change` |
| 去掉 `MAX_BODY_CHARS` 截断 | `test_fetch_bodies_truncates_huge_body` |
| 空输入时不早返回（改成照样连） | `test_fetch_bodies_empty_input_does_not_connect` |
| 去掉 `_do_list` 批次间的 `if n: time.sleep(_THROTTLE)`（Step 3b 加的） | `test_list_mail_batches_and_throttles` |

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

预期：命中只剩两处，且**都归本任务处理**（控制器 2026-09-21 实测，勿按「无输出」判）：

- `backend/banweb.py` 的**定义处** —— Step 2 删掉。
- `tests/test_banweb.py` 里 **4 条用例** —— **本任务一并删掉**（见 Step 2 第二段）。它们是：
  `test_find_mail_page_reuses_existing_tab`（`:722-726`）/ `test_find_mail_page_opens_one_when_absent`（`:729-732`）/
  `test_ensure_mail_page_returns_mail_tab_not_aims_tab`（`:735-743`）/ `test_ensure_mail_page_raises_when_browser_never_came_up`（`:746-759`）。

（`tests/test_mail_client.py` 里的 monkeypatch 已由 Task 5/6 删掉，这里**不应**再看到它们。）**若还有别的调用点，停下**，先弄清它为什么还在用。

- [ ] **Step 2: 删函数 + 删它们那 4 条用例**

删掉 `backend/banweb.py` 里的 `_find_mail_page` 与 `_ensure_mail_page` 两个函数**及其 docstring**。

**同时删掉 `tests/test_banweb.py` 里那 4 条用例**（文件头的 `Test:` 行写的就是「只删，不新增」）。
**为什么必须删而不是留着**：函数没了，它们会对 `banweb._find_mail_page` 取属性 ⇒ `AttributeError` ⇒
用例**红**，Step 4 的「全绿」就不可能成立。
**标签是 `FAILED`，不是 `ERROR`**（控制器 2026-09-21 实测，pytest 9.1.1）：异常发生在**调用阶段**（用例体内），
`ERROR` 只留给 fixture/收集阶段的异常。三种形状都实测过 —— 直接取属性、`monkeypatch.setattr` 取属性、
以及**被 `pytest.raises` 包住**的取属性（第 4 条用例正是这种：`AttributeError` 不是 `BanwebError`，
所以 `pytest.raises` 以「抛了别的异常」判红）—— **三种一律记 `FAILED`**。别按 `ERROR` 去找那 4 条红。
**别删错邻居**：`:693-711` 那三条（`test_find_page_never_returns_a_mail_login_relay_tab` /
`test_find_page_prefers_aims_page_over_mail_tab` / `test_find_page_never_returns_a_mail_tab`）与
`:797-810` 的 `test_poll_with_only_mail_tab_must_not_close_browser` 覆盖的是**`_find_page` 的邮件域排除**，
是本任务**要保住**的东西（见上方 ⚠️），它们引用的名字里带 `mail` 但**不引用**这两个被删的函数。
`:718` 的 `test_find_page_returns_none_when_no_pages`（无参数、包在 `test_find_page(...)` 里）同样要保住。

**删除边界的精确值**（控制器 2026-09-21 逐行读过 `tests/test_banweb.py:716-762`）：
保留面占 `:693`–`:721`，删除面占 `:722`–`:761`，**连续不交错**。
最后一条用例的代码行是 `:759`，其后 `:760`/`:761` 是它的两个尾随空行，`:762` 是下一段注释
`# --- 轮询（launch=False）…`。**要删到 `:761`**（含尾随空行）—— 只删到 `:759` 会让 `:720/:721`
那两个空行与 `:760/:761` 叠成**三个**空行，是可见的整洁度退化。
**删前先确认这 4 条没有 `@pytest.mark` 之类的装饰器**（控制器已核：这 4 条都没有；但同仓别处的用例有，
凭印象按 `def` 行删会留下孤儿装饰器 ⇒ `SyntaxError`）。删完用
`pytest --collect-only -q tests/test_banweb.py` 确认收集正常，再用 `git diff` 确认**只少了这 4 个函数**（无别的行被动过）。

- [ ] **Step 3: 确认 `_find_page` 的邮件域排除**还在

```bash
sed -n '/^def _find_page/,/^def /p' backend/banweb.py | grep -c "_is_mail_url"
```

预期：**2**（第一段循环的 `continue` 和兜底循环的条件）。少于 2 就是删过头了。

- [ ] **Step 3b: 证明保留下来的守卫用例**不空转（本任务删掉 4 条用例，必须证明留下的那 4 条真的在咬）

本任务删掉 4 条用例之后，「`_find_page` 的邮件域排除受保护」这句话就**全靠**剩下那 4 条了。
**「它们还在且是绿的」不等于「它们在咬」** —— 一个恒真的守卫和一个没写一样。所以要**变异一次**证明：

**变异必须落在第二段兜底循环，绝不能落在第一段**（控制器 2026-09-21 实测后改正 —— 原因见下面的引用块）：
把 `_find_page` 第二段的 `if not _is_mail_url(p.url):` 改成 `if True:`，跑 `tests/test_banweb.py`，
**预期至少这三条红**：`test_find_page_never_returns_a_mail_login_relay_tab`、
`test_find_page_never_returns_a_mail_tab`、以及本任务点名要保住的
`test_poll_with_only_mail_tab_must_not_close_browser`（第三条红在 `DID NOT RAISE BanwebError`）。
然后**立刻还原**，并用 `shasum -a 256 backend/banweb.py` 与还原前的值对一次。

> **第一段循环的那个 `continue` 是行为空操作 —— 摘掉它照绿是正确答案，不是守卫失效**
> （Task 7 实施者 2026-09-21 实测；控制器同日复核源码后确认）。
> 第一段只在 `p.url.startswith(BANWEB) or p.url.startswith(LOGIN_HOST)` 成立时才 `return p`，而
> `MAIL_HOSTS`（`outlook.cloud.microsoft` / `outlook.office.com` / `login.microsoftonline.com`）与
> `{BANWEB, LOGIN_HOST}`（`banweb.cityu.edu.hk` / `auth.cityu.edu.hk`）**按前缀两两不相交**
> （`backend/banweb.py:33-35` 把这条写成了不变式，`tests/test_banweb.py:673` 有用例守着）
> ⇒ 那个 `and not _is_mail_url(...)` 合取项对**每一个会 `return p` 的页**恒为真，摘掉它不改变任何输入下的返回值。
> **它是冗余防御，保留**（与它的注释、不变式用例是一套的）—— 但**不能拿它当变异**。
> 本计划原先写的正是它 ⇒ 那是**计划缺陷**，由控制器 2026-09-21 改正。
>
> **整道邮件域保护确有活性，但它全部由第二段兜底循环承载** —— 所以变异要打第二段。

- 变异纪律照「变异验证的执行纪律」一节：**每步清缓存或给独立的 `PYTHONPYCACHEPREFIX`**；
  锚点命中数必须 == 1；**活性探针要用那一节里给的递归指纹公式**（`repr(co_consts)` 与 `dis` 对含推导式的函数都不稳定）。
- **若四条全绿**（一条都不红）⇒ **停下报回**：说明这道守卫名存实亡，那是比删错用例更严重的事。
- 报告里写清：变异后**哪条**用例红了、红的断言是什么。**「红了」不足以断言被判別住** ——
  要红在预期的那条上、且原因正是该变异。

- [ ] **Step 4: 跑测试**

```bash
.venv/bin/python -m pytest tests/test_banweb.py -q
```

预期：全绿（那 4 条引用已删函数的用例在 Step 2 一并删掉了；**若还在 ⇒ 它们红（标签 `FAILED`），全绿不可能成立**）。**特别注意这 4 条必须仍然绿**，它们是邮件域排除的守卫：

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
git add backend/banweb.py tests/test_banweb.py
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

### 执行后的追加（2026-09-21，Task 9 修复轮 1 / commit `53a06b0`）—— **F4：邮件失败路径不再开 AIMS 登录窗**

**本任务漏了一件「没人认领的行为」。** 上面的步骤只规定了「删 `is_read` 渲染」和「加 Gmail 设置页一组」，
**从头到尾没说邮件的*失败*路径该做什么** ⇒ OWA 时代的行为原样继承了下来：
取信失败 ⇒ 叙述「多半是 Okta 会话失效 —— 与 AIMS 同一个会话」⇒ `offerManualLogin(...)`
⇒ 弹「要打开 Chrome 窗口手动登录吗」⇒ `banweb/open_login`。

新机制下这话已经不成立：**新取信层没有浏览器、不依赖 AIMS 登录态**
（spec「收益是结构性的」表：依赖 AIMS 登录态 = **否**）。
spec §11 逐字要求「应用专用密码失效 | **认证失败文案直接指向设置页重填**；**不重试**」，
而后端 `backend/mail_client.py` 的 `_auth_msg` **本来就已产出合格文案** ——
缺的不是文案，是**前端拿这条消息干了什么**：它去开登录窗，而不是把消息给用户看。

**改动（修复轮 1 已落地）**：

| 位置 | 改前（旧行号，改后已位移） | 改后（**当前行号，已核**） |
|---|---|---|
| `initMailTab` 的续延 | `.then(ok => { if(!ok) offerManualLogin(mailState.error) })` | `frontend/app.js:3077` `return loadMail();` —— 续延整条删掉 |
| `loadMail` | 无返回值 | `frontend/app.js:3082-3096` 返回布尔；失败时 `mailState.error = r.error \|\| t("mail.load_failed")` 后 `return false`（**不再自己发问**） |
| `refreshMail` | `try{…}catch(err){ if(err) offerManualLogin(err) }` / 取信阶段失败 ⇒ `offerManualLogin(failMsg)` | `frontend/app.js:3101-3103` 只剩 `await withBusy(t("mail.refresh"), $("btnMailRefresh"), loadMail);` —— 续延整条删掉 |
| 注释 ×2 | 「多半是 Okta 会话失效 —— 与 AIMS 同一个会话」「邮箱与 AIMS 共用同一个 Okta 会话」 | `frontend/app.js:3098-3100` 改述为「取信走 IMAP，没有浏览器、也不依赖 AIMS 登录态」 |
| `backend/main.py` 的 `mail_summarize` docstring | 「前者其实是 Okta 会话失效（该去重登）」 | 改述补救动作；**`reason` 键保留**（前端仍要区分取信失败与 AI 失败） |
| `tests/test_main.py` 的 docstring | 「前端据此才敢发手动登录问询：这一段失败多半是 Okta 会话失效」 | 同上改述 |
| `tools/check_mail_frontend.mjs` | 无 | **新增 I 段（9 条断言，总数 52）**：钉住邮件失败路径不发 AIMS 登录问询 |

呈现路径不变：`renderMail()` 的错误卡（`frontend/app.js:3116-3117`）本来就是 `mailState.error` 的渲染出口，
后端 `_auth_msg` 那条「请到设置页检查 Gmail 地址与应用专用密码」原样落到用户眼前 —— **所以不需要新文案**。

> ⚠️ **组合坑（本轮最容易踩的一条）：`offerManualLogin` 被两条线共用。**
> AIMS / Canvas 的失败路径（`frontend/app.js` 的 `:205`、`:1885`、`:1923`、`:2393`）**一个字都不许动** ——
> 那里「Okta 会话失效」是**真的**，仍应发手动登录问询。**改一处必须证明另一处没坏。**

> **教训（写给下一个写计划的人）：计划没规定的地方，旧行为会原样继承。**
> 换机制时，旧机制里**每一处提到旧机制的话**都要重新过一遍 —— 不只代码，还有注释、docstring、
> 以及「失败之后该把用户引到哪」这类**没有代码承载的承诺**。
> 判断办法很土但有效：**把新机制的硬约束逐条对着旧代码问「这条现在还成立吗」**，
> 而不是只对着计划里写了的东西问。

---

## Task 9: 收紧断言 + 全量验证

**Files:**
- Modify: `tests/test_main.py`
- Modify: `tests/test_mail_client.py`（**仅 Step 2b/2c**：堵 Task 6 复审查出的两个覆盖缺口 + 补 Task 5 复审留下的四条）
- Modify: `backend/mail_client.py`（**仅 Step 2b 第 (3) 项**：加一句前提注释，不改逻辑）

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

**同时重写该用例上方那一整段注释（`tests/test_main.py:1811-1819`）** —— 控制器 2026-09-21 核过，它**通篇是跨引用**，Task 5 之后大半失效，只改一句不够：

| 它现在写的 | 为什么失效 |
|---|---|
| `# mail dict 由 backend/mail_client.py:126-130 构造` | Task 5 重写该文件，这个行号**一定**不准了 |
| `id/subject/sender/received/is_read 五个键` | `is_read` 已删，是**四个**键（见 spec §4.1） |
| 字段级钉子见 `tests/test_mail_client.py:63 的 test_parse_find_items_maps_fields` | 那是 **OWA 用例**，Task 5 已整体删除；行号也全变了 |
| `bearer token 是 _capture_token 的局部值，经入参传给 collect_items/_post_json` | 三个符号**全部随 OWA 层消失** |

**改法**：保留这段话的**用意**（它讲的是「为什么原先那句 `assert "token" not in json.dumps(body)` 是真空断言」，这条教训与实现无关、仍然成立），把机制部分改写成新实现的事实 —— 新取信层里**根本不存在 bearer token**（Gmail 只读三件套，凭据在钥匙串、只在 `credentials.get_gmail` 里取用），响应里能出现的只有 `id/subject/sender/received` 四个键加 `index`/`body`。**别再写行号**：行号会随每次改动漂移，写函数名/用例名。若找不到对应的字段级用例，就指到 `test_header_item_shape_exactly_four_keys`（Task 4 新增）。

**⚠️ 同一处毛病还有第二份，别只改一份**（控制器 2026-09-21 全仓扫描发现 —— 这就是 memory 里「修一处 ≠ 修一类」那一条）：
`tests/test_main.py:1880-1889` 是**另一个用例**里的同族注释，同样两处失效：

- `:1880` 仍写「邮箱 **bearer token** 只活在 mail_client 的 HTTP 层（header）」—— 新取信层里没有 bearer token。
- `:1888-1889` 明写「**用 `<=` 而不是 `==`**：端点允许往后加字段（本轮的失败路径就加过 `reason`）」——
  **Step 1 一旦把 `:1890` 改成 `==`，这两行就成了对下面代码的直接否认**（注释说用 `<=`，代码是 `==`）。

⇒ 一并改写。机制部分按新实现重写；`<=` → `==` 的用意**改写成「刻意收紧」**，但**必须说准它多咬的是哪一头**：
`<=` 只在**多出白名单外的键**时变红，**少了一个白名单内的键**它照样绿 —— `==` 正是为了把后者也咬住
（Task 9 复审实测双向：元素塞 `zzz_extra` ⇒ `<=` 与 `==` **都红**；元素少 `index` ⇒ `<=` 绿、`==` 红）。
红了就来这里显式决定「这个字段该不该出现」，而不是让它静默通过。
（原来那句「允许往后加字段」的用意，正是 Task 5 删掉 `is_read` 时**没有任何用例变红**的原因 ——
那是**少键**：`<=` 放行，`==` 会红。）
**别写成「白名单就是要在加字段时变红」** —— 那句话方向是反的，且正是本计划原先逐字指定的措辞（Task 9 复审 F2）。

**不要**顺手删 `backend/mail_client.py:8-13` 里的 bearer / OWA 字样 —— 控制器核过，那是**刻意留下的决策记录**
（「为什么是 IMAP，而不是继续截获 OWA 的 bearer token」），是**活的文档**，不是残留。
**残留与理由长得一样，所以要逐个打开看它是在陈述历史、还是在陈述现状。**

- [ ] **Step 2: 清掉 test_main.py 里其余 `is_read`**

```bash
grep -n "is_read" tests/test_main.py
```

把各处 fixtures 里的 `"is_read": …` 逐处删掉。**每删一处就确认该用例的断言不依赖它。**

- [ ] **Step 2b: 堵掉 Task 6 复审查出的两个覆盖缺口**（`tests/test_mail_client.py`），外加一条注释澄清

前两处**不是**本任务「顺手改进」，是复审实测出来的**真缺口**，且都属本任务的主题（收紧断言）：

**(1) `tests/test_mail_client.py:668` 是一条恒真断言。** 现在是：

```python
    assert fetches
    assert all("BODY.PEEK[]" in str(c[-1]) for c in fetches)
```

第二行复述的正是**上一行 `fetches` 的筛选条件** ⇒ 它永远不可能是红的那一行（实测：改成 `BODY[]` 时红的是
`assert fetches`，报 `E assert []`）。**删掉这第二行**，或改成与筛选条件**不同口径**的断言。

**2026-09-21 实际落地的形状**（`tests/test_mail_client.py:713-718`，Task 9 修复轮 1；**按这个来**）：

```python
    fetches = [c for c in fake.sent if c[0] == "uid" and c[1] == "FETCH"]
    assert fetches, "没发出任何 FETCH，下面的断言是空转的"
    assert all("BODY.PEEK" in str(c[-1]) and not re.search(r"BODY\[", str(c[-1]))
               for c in fetches), f"有 FETCH 不是 PEEK 取件：{fetches}"
    assert any("BODY.PEEK[]" in str(c[-1]) for c in fetches), \
        f"正文一条 FETCH 都没发（header FETCH 把列表填满了）：{fetches}"
```

> ⚠️ **`any(...)` 必须放在 `all(...)` 之后 —— 顺序承重，不是排版。**（Task 9 修复轮 1 裁定，控制器采纳）
> 两条守的是**两件不同的事**：`all` 守「没有裸 `BODY[` 的取件」，`any` 守「**正文**取件真的发生过」。
> 该用例先调 `list_mail(7)`，那一步自己发的 **header** FETCH
> （`(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] INTERNALDATE)`，`backend/mail_client.py:330`）
> 本身是 peek、但**不含**子串 `BODY.PEEK[]` ⇒
> · 变异「正文一条都不发」⇒ `all` **照样通过**（header 那条满足它），红在 `any`
>   （Task 9 复审实测：补这条锚之前，该变异下用例 **1 passed** —— 假阴性）；
> · 变异「正文 `BODY.PEEK[]`→`BODY[]`」⇒ 两条都不满足，红在**先写的** `all`。
> **把 `any` 挪到 `all` 前面，第二条变异的红就全落到 `any` 上、`all` 永远走不到** ——「各红各的行」就没了。

> ⚠️ **也别把 `any` 那句写成 `all("BODY.PEEK[]" in str(c[-1]) for c in fetches)` —— 未变异时就为假。**
> （2026-09-21 Task 9 实测报回；控制器的派发曾把它写成 `all(...)`，**推翻了本计划这段本来正确的文字**，
> 实施者跑出来顶回 —— **本会话第三次同一类错**。）
> **先确认未变异时绿，再谈变异红。**
>
> 一般规律：**筛选条件与断言条件同口径 ⇒ 恒真**（这正是本条要修的东西），
> 而把口径改宽成 `all(...)` 又会把**同一个测试里别的命令**卷进来 —— 两头都要防。

**(2) `tests/test_mail_client.py:699` 只钉长度、不钉哪一头。** 现在是
`assert len(out["1"]) == mail_client.MAX_BODY_CHARS` ⇒ 复审在 `/tmp` 实测
**把 `body[:MAX_BODY_CHARS]` 写成 `body[-MAX_BODY_CHARS:]` 照绿（1 passed）**，
而用户拿到的是邮件**结尾**而不是开头。
把关：让 fixture 正文**首尾可区分**，断言**具体的前缀串**而不是只断长度：

```python
    huge = "A" * 199_999 + "B" * 10_000          # 总长 209_999，首尾形状完全不同
    ...
    assert out["1"] == "A" * 199_999 + "B"       # 长度 200_000，且钉住"是开头不是结尾"
```

**这个期望值是字面量、不是拿 `huge[:MAX]` 现算的** —— 现算就又是自证。控制器的算术实测：
`huge[:200_000] == "A" * 199_999 + "B"` 为 **True**（长度 200_000 ✓），
而 `huge[-200_000:]`（即那条变异）≠ 该期望 ⇒ **变异会红**。

两处改完各跑一次聚焦，并各**变异一次**证明它们是承重的：
把第一处的 `BODY.PEEK[]` 改回 `BODY[]`（**删掉恒真那行后，红必须落在新断言上**）；
把第二处的 `body[:MAX_BODY_CHARS]` 改成 `body[-MAX_BODY_CHARS:]`（**改前绿、改后红**，
按「变异验证的执行纪律」：锚点命中数 == 1、活性探针用递归指纹、跑完还原并核 sha256）。

**(3) 一条注释澄清**（`backend/mail_client.py`，复审判定「不是缺陷、但值得写明前提」）。
`_do_bodies` 的 UIDVALIDITY 核对是 `if _uidvalidity and uidvalidity != _uidvalidity:` ——
**进程内从未 `list_mail` 过就调 `fetch_bodies` 时，这个核对会被静默跳过**（模块级 `_uidvalidity` 还是空串）。
当前生产不可达（唯一调用点 `backend/main.py:1026-1029` 在同一次请求内先 `list_mail`），所以**不改代码**，
只在那一行上方补一句注释说明这个前提，否则下一个人会以为它无条件生效：

```python
        # 前提：同一次刷新里 list_mail 已经先跑过。进程内第一次就直接调 fetch_bodies 时
        # `_uidvalidity` 还是空串，这里的核对会被跳过（生产路径不会这样调）。
```

**这一处只加注释、不改逻辑**（改逻辑会让 UIDVALIDITY 的语义与 brief 的逐字实现分叉）。

- [ ] **Step 2c: 补上 Task 5 复审留下、却漏进本计划的那几条**（`tests/test_mail_client.py`）

这几条在 Task 5 结束时**被记进了 ledger、判给了本任务，却漏进了本计划** ——
控制器 2026-09-21 拿 ledger 与 plan 对账时才发现（见 ledger「漂移」条）。都属本任务主题（收紧断言 / **除锈**）。
**行号由控制器 2026-09-21 逐条打开文件核过**（不是从 ledger 转抄 —— ledger 里那条自己的引用就把行号写偏了）。

> ⚠️ **控制器自纠（2026-09-21，同日）**：本步骤**第一版只补了两条断言 + UTC 那条，漏掉了下面的 (4) 错字** ——
> 也就是说，**同一类漏项在「补漏」这一步里又发生了一次**。发现方式是写完 brief 后**回头 grep 每个行号**：
> `grep ":446" task-9-brief.md` **零命中**。⇒ 记一条：**做完「补漏」后，要拿每个被补的锚点回 brief 里搜一遍**，
> 确认它真的落地了。补漏本身也需要一道验证。

**(1) `tests/test_mail_client.py:121` `test_never_issues_mutating_commands` 是空转的否定式断言。**
它两条断言全是否定式（`assert bad == []`、`assert not any(...)`）⇒ `list_mail` 若**一条命令都不发**，
两条**同时恒真**。补正例锚，证明这次运行真走到了「可能发 STORE/MOVE」的那段路：

```python
    assert any(c[0] == "uid" and c[1] == "SEARCH" for c in fake.sent), \
        "连 SEARCH 都没发，下面的否定式断言是空转的"
    assert any(c[0] == "uid" and c[1] == "FETCH" for c in fake.sent), \
        "连 FETCH 都没发，下面的否定式断言是空转的"
```

**(2) `tests/test_mail_client.py:249` `test_list_mail_returns_empty_without_fetching` 名不副实。**
用例名与 docstring 都承诺「不发 FETCH」，断言里却**没有一条**在核这件事的另一面 ——
只钉了返回值 `== []`（`_do_list` 若在 SEARCH 之前就 `return []`，这几行照样绿）。
补一条锚，证明它**确实发过 SEARCH**，即站到了 FETCH 本该出现的位置前一步：

```python
    assert any(c[0] == "uid" and c[1] == "SEARCH" for c in fake.sent), \
        "连 SEARCH 都没发，'不发 FETCH' 是空转的"
```

**(3) `tests/test_mail_client.py:262` 的断言跨 UTC 午夜会偶红 —— 冻住时钟，改成字面量期望。**
现在是 `assert since.date() == (datetime.now(timezone.utc) - timedelta(days=8)).date()`：
左边由 `list_mail` **调用期间**的时钟决定，右边由**断言执行时**的时钟决定，两者差着整个 `list_mail` 的耗时
⇒ 恰好跨 UTC 午夜就差一天 ⇒ 红（概率 ~ε/日，ε = 该调用的毫秒级耗时）。这是**真**偶红，不是理论上的。

**不要**改成「接受两个候选日期」那种写法 —— 控制器 2026-09-21 推过一遍，**它放行真缺陷**：
`(T-7d)` 与 `(T_a-8d)` 只差 `1d-ε`，当 `T_a-8d` 落在 UTC 午夜之后 ε 之内时两者落在同一日
⇒ **`days + 1` 写成 `days` 的那个 off-by-one 照样绿**。那等于「偶红没了、偶漏来了」。
**改成冻结时钟 + 字面量期望**，偶红与偶漏**一起**消失：

```python
_FIXED_NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
"""冻结的「现在」：取 UTC 正午 —— 离 UTC 午夜最远；且 HKT（UTC+8）= 当日 20:00，
   两种时区下同一天，所以期望值不受「代码按哪个时区格式化」影响。"""


class _FrozenDateTime(datetime):
    """只冻结 now()，strptime / 算术 / 比较全部继承 —— 不碰 datetime 模块本身。"""

    @classmethod
    def now(cls, tz=None):
        return _FIXED_NOW if tz is None else _FIXED_NOW.astimezone(tz)
```

放在该用例紧上方（与 `_body_msg` 贴在它那批用例旁的既有先例一致）。用例改成：

```python
def test_list_mail_search_since_is_coarsened_by_one_day(monkeypatch):
    """SINCE 放宽一天：它按日粒度且按服务端时区（spec §5.3）。"""
    fake = _install(monkeypatch, _FakeIMAP(headers={}))
    monkeypatch.setattr(mail_client, "datetime", _FrozenDateTime)
    mail_client.list_mail(7)
    search = next(c for c in fake.sent if c[0] == "uid" and c[1] == "SEARCH")
    assert search[-1] == "03-Sep-2026"
```

**这个字面量的来历**（控制器 2026-09-21 按 `backend/mail_client.py:359-360` 现算核过，不是抄的）：
代码是 `(datetime.now(timezone.utc) - timedelta(days=days + 1)).strftime("%d-%b-%Y")`；
`list_mail(7)` ⇒ `days + 1 == 8`；`2026-09-11 12:00Z - 8d = 2026-09-03 12:00Z` ⇒ `"03-Sep-2026"`。

**换来的额外覆盖**（旧写法没有的）：`%d` 是**零填充**，把日期选在 `03` 就顺手钉住了填充
（`%-d` ⇒ `"3-Sep-2026"`，而旧写法的 `strptime("%d-%b-%Y")` 对两种都接受、**钉不住**）；月份缩写 `Sep` 同样被钉住。

**这个 patch 的边界**：`monkeypatch.setattr(mail_client, "datetime", …)` 只替换**该模块的全局名**，
不动 `datetime` 模块，也不影响 `backend/mail_client.py:126` 的 `datetime.strptime`（子类继承，返回值仍是 `datetime`）。
它不是本仓 Minor-6 那种「猴补全进程」的写法。

**(4) `tests/test_mail_client.py:446` 的 docstring 有个错字：`根不到` → `根本走不到`。**
在 `test_header_item_falls_back_to_internaldate` 的 docstring 里，原句是
「`fmtLocal` 的 `!iso` 分支直接返回空串，**根不到** `new Date()`」—— 缺一个「本」字，读起来不成句。
控制器 2026-09-21 打开该行核过。**只改 docstring 文字，不改任何代码、不改断言。**
这一条**不需要变异**：它不改变任何行为，没有可咬的东西；改完确认那个词变成「根本走不到」即可。

前三条改完各**变异一次**证明承重（按「变异验证的执行纪律」：清缓存 / 活性探针用递归指纹 / 锚点命中数 == 1）：
(1) 在 `_do_list` 里、`conn.uid("SEARCH", …)` 之后插一句提前 `return []` ⇒ 第 (1)(2) 条的正例锚**必须红**
（控制器已核过 `_do_list` 的命令序：`SEARCH` 在 `if not uids: return []` **之前**发出，
所以 `headers={}` 时 SEARCH 确实发过 ⇒ 第 (2) 条的锚有东西可锚）；
(2) 把 `backend/mail_client.py:360` 的 `days + 1` 改成 `days` ⇒ 第 (3) 条**必须红**（`04-Sep-2026`）；
(3) 跑完立即还原并核 `shasum -a 256 backend/mail_client.py`。

- [ ] **Step 3: 全仓确认无残留**

```bash
grep -rnE '\bis_read\b' backend/ frontend/ tests/ tools/
```

**预期不是「无输出」** —— 控制器 2026-09-21 实测过，照原样写「无输出」会**删掉正确的用例**。三条口径：

1. **必须加 `\b`**：不加时 `tests/test_credentials.py` 里的 `test_gmail_address_is_read_from_gmail_service` 会因 `is_read` 是 `is_read_from` 的**子串**而误报（实测：不加 25 处，加 `\b` 24 处）。**别去改那条用例的名字** —— 它与邮件无关。
2. **`backend/` 与 `frontend/` 必须为 0**。任何命中都是真残留（`backend/mail_client.py` 那处随 Task 5 消失；前端那半 Task 8 已清）。
3. **`tests/test_mail_client.py` 里预期还剩约 4 处，且都是 Task 4 故意留的探针，必须原样保留**：`test_header_item_shape_exactly_four_keys` 的 docstring（「`is_read` 已删除，多一个少一个都要红」）、以及那条**刻意**往老邮件里塞 `"is_read": False` 来证明它不再被保留的 `select_scope` 用例。**判据不是行号**（Task 5 重写过这个文件，行号已全变），而是「**这一处能不能说出它为什么必须留**」—— 说不出就是残留，说得出就别动。
4. `tools/check_i18n_keys.mjs` 里那条讲本次变更的历史注记（1 处）**保留**。

```bash
grep -rn "_capture_token\|extract_bearer\|_ensure_mail_page\|_find_mail_page\|MarkAsRead" backend/ tests/ tools/
```

预期：**无输出**（OWA 层与邮件浏览器入口已删干净）。**注意这条 grep 必须包含 `tests/`**：Step 1 之前，`tests/test_main.py` 那段注释里还有一处 `_capture_token` —— 如果你在 Step 1 之后还看到它，说明那段注释没重写完。若倒过来、这里的命中来自 `backend/banweb.py` 或 `tests/test_banweb.py`，说明 **Task 7 没做干净**（它的 Step 2 要求删掉那 4 条用例）⇒ 报回，别在这里补删。

**还有一处「删掉的特性」的残留是 `is_read` 抓不到的**（Task 4 复审发现，控制器 2026-09-21 补）：

> ⚠️ **别全仓 grep `未读`。** 控制器实测（2026-09-21）：`未读` 在 `backend/ frontend/ tests/ tools/` 下命中 **11 个文件约 30 行**，其中**绝大多数与邮件无关** —— `frontend/i18n.js`（`unread.announce` 等）、`frontend/app.js`（公告/讨论的未读徽标）、`backend/canvas_client.py`（`unread_count`）、`tests/test_canvas_client.py`、`tools/check_i18n_keys.mjs` 都是 **Canvas 公告与讨论**那条线的，另一个子系统，**本任务一处都不许碰**。
> 更危险的是：命中里有两处是 **Task 4 刚写的、正确的、承重的** 说明 —— `backend/mail_client.py:314`（`select_scope` 的「没有『未读』分支：转发过来的邮件在 Gmail 里恒为未读，而只读访问永不改变它」）与 `tests/test_mail_client.py:562`（「未读不再影响结果：老邮件只要超出窗口就丢弃，不管它读没读」）。**它们必须原样留着** —— 删了就是把「本设计刻意不做未读过滤」这个要点从代码里抹掉。
> 所以只 grep 这条线的四个文件：

```bash
grep -rn "未读" backend/llm_client.py backend/main.py backend/mail_client.py tests/test_mail_client.py
```

**逐处处置（控制器已核过的清单，按它办，不要自由发挥）**：

| 命中 | 处置 |
|---|---|
| `backend/llm_client.py:258` | **改** —— 本步骤的对象 |
| `backend/main.py:1003`（endpoint docstring「列出『未读 ∪ 最近 N 天』的学校邮箱邮件」） | **改** —— Task 5 不重写 `main.py`，这行会活下来，而新实现**没有未读过滤**了。改成「列出最近 N 天的学校邮箱邮件」 |
| `backend/mail_client.py:222` | **不要动** —— Task 4 新写的正确说明（本表原写 `:314`，行号漂了，内容一致） |
| `tests/test_mail_client.py:548` | **不要动** —— Task 4 新写的正确说明（本表原写 `:562`，同上） |
| `backend/mail_client.py:20` | **不要动** —— 只读三件套的活说明（「未读状态必须原样保留 —— 我们只是看一眼，不是替他处理邮件」）。**本表原缺此行**，见下 |
| `tests/test_mail_client.py:76` | **不要动** —— `_install` 的 docstring，说明 Task 4 的纯函数覆盖了「无未读分支」等三条。**本表原缺** |
| `tests/test_mail_client.py:110` | **不要动** —— `test_all_fetches_use_body_peek`，说明 `BODY[]` 会改用户未读状态。**本表原缺** |
| `backend/mail_client.py:11` / `:413` / `:437`、`tests/test_mail_client.py:216` / `:225` | **不应出现** —— 它们随 OWA 层在 Task 5 被删。若这里还看得到，说明 Task 5 没删干净 ⇒ **如实报回，不要顺手补删**（本任务不动 `mail_client.py`） |

`backend/llm_client.py:256-260`（`trim_bodies` 的 docstring）里有一句：取信路径
`list_mail` / `select_scope` 都不限条数，「只由**邮箱里未读邮件的多少**兜着」。**「未读」这个量在本计划里已经不存在了**
（它随 OWA 层的 `is_read` 一起删掉）。**而且它不只是措辞陈旧 —— 它描述的界在新设计下更松**：
旧设计里 `n` 由「未读数」兜着，新设计里 `n` 是「最近 `DEFAULT_DAYS`（7）天内的**全部**邮件数」，
通常大一个数量级 ⇒ 这段 docstring 赖以成立的「线性无上界」结论**依然成立，但触发得更容易**。

改法（**只改这一句机制叙述，数字与结论都不动**）：把「只由邮箱里未读邮件的多少兜着」改成
「只由最近 `DEFAULT_DAYS` 天内的全部邮件数兜着（旧实现是 `未读 ∪ 最近 N 天`，是这条的**超集**；
删掉未读分支后这个界只会**更紧**，不会更松）」。

> **⚠️ 方向别写反（Task 9 复审 F3，控制器已回旧版本源码核实）。** 本计划原先逐字指定的是
> 「旧实现的未读过滤已随 OWA 层删除，**界比那时更松**」—— **反了**。
> `git show 2f27333:backend/mail_client.py`（OWA 版 `select_scope`）的 keep 条件是
> 「**未读 ∪ 最近 days 天**」：`if not it.get("is_read"): out.append(it); continue` 然后才看窗口 ——
> 即**并集**，是「只看窗口」的**超集**。集合更大 ⇒ 删掉未读分支后 n **只可能更小** ⇒ 界**更紧**。
> （「更松」只有在拿**旧 docstring 自称的**「未读数」当参照物时才成立 —— 而那句话本身就是低估旧实现的。
> 参照物有歧义时，按**能跑的旧代码**写，别按旧注释写。）
**别顺手改「10×」那类数字** —— 它们是对的，见 [[test-specs-need-positive-assertions]] 的「改一处 ≠ 改一类」。
这条 grep 的中文命中数**不是 1 处**。（控制器原写「预期恰好 1 处」，那是**照上面那张表数出来的**，
而表当时只列了 5 个位置。）Task 9 实测 **7 处**（2026-09-21 实施者报回，控制器复核后把表补全）：
**2 处改、5 处留**。判据是 brief 给的「这一处能不能说出它为什么必须留」，**不是数字** ——
按「多于 1 处就是残留」去删，会删掉 4 处正确且承重的说明。

跑完本步这条 grep 仍会命中 **6 处**（`llm_client.py` 1 + `mail_client.py` 2 + `test_mail_client.py` 3）：
`main.py` 那处随改写消失，而 `llm_client.py` 改写后的句子里**故意保留**了「未读」二字
（「旧实现的未读过滤已随 OWA 层删除」），其余 5 处就是上表点名的留项。**6 不是没删干净。**

表里的行号以实施时实测为准（`:222`、`:548`）；**行号也是断言**，照旧行号去核会核到别的东西。

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
git add tests/test_main.py tests/test_mail_client.py backend/mail_client.py backend/llm_client.py backend/main.py
git commit -m "test(mail): 收紧字段断言为集合相等；清掉 is_read 残留"
```

> **本任务的改动落在那 5 个文件上，别只 stage 第一个。**（2026-09-21 实施者实测报回：Step 8 原先只写了
> `git add tests/test_main.py` —— 那是本步骤刚写时的样子，Step 1 只碰一个文件；但本任务后来长出
> Step 2b/2c（`tests/test_mail_client.py`、`backend/mail_client.py`）与 Step 3（`backend/llm_client.py`、
> `backend/main.py`）⇒ 照字面只 stage 一个文件，其余 4 个文件的改动会**悬在工作区**。
> 实施者的处置是拆成两个 commit（先按字面、再补一个），也可一次全 stage；两种都能接受，
> **不可接受的是留下未提交的改动**。绝不 `git add -A`（`docs/` 与 `.superpowers/` 都不许进这次提交）。

---

## 收尾检查（全部任务完成后）

- [ ] 用户**手动端到端验证**：点「刷新邮件」能看到真实邮件；点「AI 总结」能出分组与 DDL
- [ ] 确认学校邮箱里的邮件**仍未读**（只读三件套真的生效了）
- [ ] 确认用户自己的 Chrome 里开着的 Outlook 标签页没有被导航走
- [ ] 列表里每封邮件的 `received` 都**非空**且能正常本地化显示（INTERNALDATE 回退在真实数据上生效 —— 单测钉的是形状，这里是真实转发件）
- [ ] **在真实数据上确认窗口真的在筛**（Task 4 复审发现的一条潜伏项要求补这一条）：让用户找一个**7 天以前**收到的转发件（转发不回溯，所以这需要等或临时把 `DEFAULT_DAYS` 调小来验），确认它**不**出现在列表里。**为什么必须单列这一条**：如果 `parse_internaldate` 因为进程 locale 而解析失败（`%b` 依赖 locale，IMAP 的月份缩写却是协议固定的英文），它会对**每一封**返回 `None` ⇒ `select_scope` 全部 fail-open ⇒ 窗口静默消失，而**上一条检查照样通过**（`received` 回退到 `Date:` 头，仍然非空）。单测钉的是形状，抓不到这种「整体静默失效」。
- [ ] 设置页提示语含「转发不回溯」说明（spec §11 的对应项）
- [ ] 用户知道**转发中断的症状是「邮件不再更新」而非报错**（spec §11）：靠列表里最近一封的时间自查，本轮不新增后台检测
- [ ] `tools/probe_gmail_imap.py` 已删除（Task 1 Step 5）
- [ ] 把实测结论与实现要点写进 memory
- [ ] **保持本地，先不推**（用户未授权推送）
