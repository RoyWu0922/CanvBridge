# 学校邮箱（Outlook / M365）接入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 CanvBridge 新增「邮件」一级页，复用 AIMS 已有的 Okta 会话从学校 M365 邮箱取信（未读 ∪ 最近 7 天），AI 按「课程 × 类型」两级分组并抽出摘要与 DDL，每条 DDL 经用户确认后一键写入日历/提醒。

**Architecture:** 新增 `backend/mail_client.py` 承担取信层，分纯函数层（payload 构造 / 响应解析 / 范围筛选 / JWT 过期，全部可合成 fixture 单测）与 I/O 层（Playwright 监听 `service.svc` 请求截获 bearer token + `requests` 打 REST）。**不新建浏览器**——复用 `banweb.py` 那一个 persistent context，邮件在同一个 context 里开自己的 tab，故须给 `banweb._find_page` 加邮件域排除，防止 AIMS 把邮件 tab 导航走。AI 层只在 `llm_client.py` 加一个 `summarize_mail()`。前端加一个一级页。

**Tech Stack:** Python 3 / FastAPI / pydantic / requests / Playwright（后端）；原生 JS，无构建、无工具链（前端）。

**Spec:** `docs/superpowers/specs/2026-09-18-school-mail-design.md`

## Global Constraints

- **无构建、无 JS 工具链。** `frontend/index.html:354-357` 四个经典 `<script>`，加载顺序硬性：`i18n.js → util.js → app.js → shell.js`。
- **新增顶层 `let`/`const`/`function` 前，必须先 grep `frontend/` 其余三个 JS 文件**——顶层 `let`/`const` 重名即 `SyntaxError`，整页白屏。顶层 `function` 重名 `node --check` **查不出来**，必须手工 grep。
- **i18n 新增 key 必须 zh / en 两侧同步**。当前各 **382** 键。死键基线 **21**，只许持平或下降。
- **HTML id 基线 141**（新增 id 允许，但所有 `$("id")` 必须能解析到）。
- **后端错误一律返回 200 + JSON**，判别字段是 `Content-Type: application/json`。前端 `api()`（`frontend/util.js:27`）已按此约定解析，失败时返回 `{ok:false, error}`。
- **测试不打真实网络。** `tests/conftest.py` 在 socket 层拦截 `getaddrinfo`/`create_connection`/`connect`/`connect_ex`，**任何被拦截的尝试都会让该测试失败**（`BLOCKED` 列表在拆卸中断言为空）。一律用合成 fixture + `monkeypatch`（范式见 `tests/test_canvas_client.py` 的 `_Resp`/`_Session`）。
- **测试用 `.venv/bin/python -m pytest`**（系统 Python 没有 pytest）。当前基线 **395 passed**。
- **新测试必须做变异检验**——临时改坏实现，确认测试真的失败，再还原。
- **token 绝不落盘、绝不返回前端。** 只在后端内存中存活。
- **绝不后台轮询、绝不自动重试登录。** 只在用户点按钮时抓；失败即停，复用已有 `#loginPrompt` 弹窗问是否手动登录。
- **绝不调用 `MarkAsRead`。** `FindItem`/`GetItem` 是纯读的（spec §3.4 实测），不要画蛇添足加还原逻辑。
- **不动 `DT` cookie。** 设备信任是免 MFA 的唯一凭据。
- **不改动 AIMS 现有行为。** 对 `banweb.py` 的改动只允许「排除邮件域」这一件事。

**门禁（每个任务收尾都要全绿）：**

```bash
.venv/bin/python -m pytest -q
node tools/check_i18n_keys.mjs
node tools/check_dom_ids.mjs
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/four.js && node --check /tmp/four.js
```

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `backend/mail_client.py` | OWA 取信：payload 构造、响应解析、范围筛选、token 截获、REST 调用 | 新建 |
| `backend/banweb.py` | 加 `MAIL_HOSTS` / `_is_mail_url` / `_find_mail_page`；`_find_page` 排除邮件域 | 改 |
| `backend/llm_client.py` | 加 `summarize_mail()` + 两个纯助手 | 改 |
| `backend/main.py` | 加 `/api/mail/list`、`/api/mail/summarize` 两个端点 | 改 |
| `frontend/index.html` | 侧栏 nav-item、`#page-mail`、`#confirmPrompt` | 改 |
| `frontend/i18n.js` | 17 个邮件/确认 key × zh/en | 改 |
| `frontend/shell.js` | `PAGES` 与 `PAGE_INIT` 加 mail | 改 |
| `frontend/app.js` | `initMailTab` / `loadMail` / 渲染 / `askConfirm` / 写入 | 改 |
| `frontend/app.css` | 邮件页样式 | 改 |
| `tests/test_mail_client.py` | 取信层单测 | 新建 |
| `tests/test_banweb.py` | `_is_mail_url` / `_find_page` 邮件排除 | 改 |
| `tests/test_llm_client.py` | `trim_bodies` / `_normalize_groups` / `summarize_mail` | 改 |
| `tests/test_main.py` | 两个新端点 | 改 |

## 关于 spec §11 的五个待验证项

spec §11 列了 5 项待实测。它们**不是**本计划的阻塞项，处置如下——实施时不要停下来去真跑浏览器验证，按这里的取值写，第 1、2 步端到端跑通时自然会被验证到：

| 项 | 本计划的取值 | 依据 |
|---|---|---|
| 1. token 有效期 | 每次抓取前**重新截获一次**，`jwt_exp()` 只用于「要不要复用」的判断 | 保守做法：不复用就不会踩过期。`jwt_exp` 仍需实现并单测，因为它是「复用」分支的前提 |
| 2. `GetItem` 批量上限 | `BODY_BATCH = 10` | 保守值；实测单封可用，10 是明显低于任何合理上限的选择 |
| 3. 7 天翻页边界 | `PAGE_SIZE = 50`，`IndexedPageView` 从头翻，遇到不足一页即停，再本地按 `received` 过滤 | 筛选在客户端做（`IsRead`/`DateTimeReceived` 已在列表响应里，不额外发请求） |
| 4. `service.svc` 频率限制 | 批量 `GetItem` 之间 `time.sleep(_THROTTLE)`，`_THROTTLE = 0.5` | spec §9 要求「保留间隔」 |
| 5. 正文截断阈值 | 单封 `_MAX_BODY = 4000`，总量 `_MAX_MAIL_TOTAL = 20000` | 对齐 `llm_client._MAX_SYLLABUS = 20000` 的先例 |

---

## Task 1: `mail_client.py` 纯函数层

只写不碰网络、不碰浏览器的部分：payload 构造、响应解析、范围筛选、JWT 解析。I/O 留到 Task 3。

**Files:**
- Create: `backend/mail_client.py`
- Test: `tests/test_mail_client.py`

**Interfaces:**
- Consumes: 无（本任务是叶子）
- Produces:
  - `SVC_URL: str`、`TIMEOUT = 30`、`DEFAULT_DAYS = 7`、`PAGE_SIZE = 50`、`BODY_BATCH = 10`、`_THROTTLE = 0.5`
  - `MailError(Exception)`
  - `find_item_payload(offset: int = 0, max_entries: int = PAGE_SIZE) -> dict`
  - `get_item_payload(item_ids: list[str]) -> dict`
  - `parse_find_items(resp: dict) -> list[dict]` — 每项 `{id, subject, sender, received, is_read}`
  - `parse_get_bodies(resp: dict) -> list[dict]` — 每项 `{id, body}`
  - `select_scope(items: list[dict], now: datetime, days: int = DEFAULT_DAYS) -> list[dict]`
  - `jwt_exp(token: str) -> datetime | None`
  - `extract_bearer(headers: dict) -> str | None`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_mail_client.py`：

```python
"""OWA 取信层纯函数测试。全程合成 fixture，不出网。"""
from datetime import datetime, timedelta, timezone

import pytest

from backend import mail_client


def _item(iid, subject="S", sender="老師", recv="2026-09-17T08:31:00Z", is_read=False):
    return {"ItemId": {"Id": iid}, "Subject": subject,
            "From": {"Mailbox": {"Name": sender}},
            "DateTimeReceived": recv, "IsRead": is_read}


def _find_resp(items):
    return {"Body": {"ResponseMessages": {"Items": [{"RootFolder": {"Items": items}}]}}}


def _get_resp(rows):
    return {"Body": {"ResponseMessages": {"Items": [
        {"Items": [{"ItemId": {"Id": i}, "Body": {"Value": b}} for i, b in rows]}]}}}


# --- payload 构造：钉住 spec §5.3 的实测结构，改坏即红 ---

def test_find_item_payload_shape():
    p = mail_client.find_item_payload(offset=20, max_entries=50)
    assert p["__type"] == "FindItemJsonRequest:#Exchange"
    assert p["Header"] == {"__type": "JsonRequestHeaders:#Exchange",
                           "RequestServerVersion": "Exchange2013"}
    body = p["Body"]
    assert body["__type"] == "FindItemRequest:#Exchange"
    assert body["ParentFolderIds"] == [
        {"__type": "DistinguishedFolderId:#Exchange", "Id": "inbox"}]
    assert body["Traversal"] == "Shallow"
    assert body["Paging"] == {"__type": "IndexedPageView:#Exchange",
                              "BasePoint": "Beginning", "Offset": 20,
                              "MaxEntriesReturned": 50}
    uris = [x["FieldURI"] for x in body["ItemShape"]["AdditionalProperties"]]
    assert uris == ["item:Subject", "message:From",
                    "item:DateTimeReceived", "message:IsRead"]


def test_get_item_payload_shape():
    p = mail_client.get_item_payload(["A", "B"])
    assert p["__type"] == "GetItemJsonRequest:#Exchange"
    assert p["Body"]["__type"] == "GetItemRequest:#Exchange"
    assert p["Body"]["ItemShape"]["BodyType"] == "Text"
    assert p["Body"]["ItemIds"] == [
        {"__type": "ItemId:#Exchange", "Id": "A"},
        {"__type": "ItemId:#Exchange", "Id": "B"}]


# --- 列表解析 ---

def test_parse_find_items_maps_fields():
    got = mail_client.parse_find_items(_find_resp([_item("A", "作业1", "李老師")]))
    assert got == [{"id": "A", "subject": "作业1", "sender": "李老師",
                    "received": "2026-09-17T08:31:00Z", "is_read": False}]


def test_parse_find_items_drops_entries_without_id():
    """没有 ItemId 的条目无法回取正文 → 必须丢弃，不能带个 None 下去。"""
    got = mail_client.parse_find_items(
        _find_resp([{"Subject": "无 id"}, _item("B")]))
    assert [x["id"] for x in got] == ["B"]


def test_parse_find_items_sender_falls_back_to_address():
    resp = _find_resp([{"ItemId": {"Id": "A"},
                        "From": {"Mailbox": {"Address": "x@my.cityu.edu.hk"}},
                        "DateTimeReceived": "", "IsRead": True}])
    assert mail_client.parse_find_items(resp)[0]["sender"] == "x@my.cityu.edu.hk"


def test_parse_find_items_tolerates_garbage():
    """响应缺层 / 类型不对 → 返回空表，不抛。"""
    assert mail_client.parse_find_items({}) == []
    assert mail_client.parse_find_items(
        {"Body": {"ResponseMessages": {"Items": None}}}) == []


# --- 正文解析 ---

def test_parse_get_bodies_pairs_id_and_body():
    got = mail_client.parse_get_bodies(_get_resp([("A", "正文甲"), ("B", "正文乙")]))
    assert got == [{"id": "A", "body": "正文甲"}, {"id": "B", "body": "正文乙"}]


def test_parse_get_bodies_non_string_body_becomes_empty():
    got = mail_client.parse_get_bodies(_get_resp([("A", None)]))
    assert got == [{"id": "A", "body": ""}]


# --- 范围筛选 ---

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


def _mk(iid, days_ago, is_read):
    recv = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": iid, "subject": iid, "sender": "", "received": recv, "is_read": is_read}


def test_select_scope_keeps_recent_read_and_all_unread():
    items = [_mk("近读", 1, True), _mk("旧读", 30, True),
             _mk("旧未读", 40, False), _mk("近未读", 0, False)]
    assert {x["id"] for x in mail_client.select_scope(items, NOW, 7)} == \
        {"近读", "旧未读", "近未读"}


def test_select_scope_boundary_is_inclusive():
    """正好 7 天前算「最近 7 天」内 —— 边界收进来比漏掉安全。"""
    items = [_mk("边界", 7, True), _mk("超一天", 8, True)]
    assert {x["id"] for x in mail_client.select_scope(items, NOW, 7)} == {"边界"}


def test_select_scope_unparsable_date_is_kept():
    """时间读不出来宁可多给一封，也不能静默丢件。"""
    items = [{"id": "坏", "subject": "", "sender": "", "received": "???", "is_read": True}]
    assert [x["id"] for x in mail_client.select_scope(items, NOW, 7)] == ["坏"]


# --- bearer / JWT ---

def test_extract_bearer_is_case_insensitive():
    assert mail_client.extract_bearer({"Authorization": "Bearer abc"}) == "abc"
    assert mail_client.extract_bearer({"authorization": "bearer xyz"}) == "xyz"


def test_extract_bearer_rejects_non_bearer():
    assert mail_client.extract_bearer({"Authorization": "Basic abc"}) is None
    assert mail_client.extract_bearer({}) is None
    assert mail_client.extract_bearer(None) is None


def _b64(obj):
    import base64
    import json as _j
    raw = _j.dumps(obj).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_jwt_exp_reads_exp():
    tok = f"h.{_b64({'exp': 1790000000})}.s"
    assert mail_client.jwt_exp(tok) == datetime.fromtimestamp(1790000000, tz=timezone.utc)


def test_jwt_exp_returns_none_on_garbage():
    assert mail_client.jwt_exp("not-a-jwt") is None
    assert mail_client.jwt_exp("") is None
    assert mail_client.jwt_exp("h." + _b64({"nope": 1}) + ".s") is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_mail_client.py -q
```

预期：`ModuleNotFoundError: No module named 'backend.mail_client'`（collection error）。

- [ ] **Step 3: 写实现**

创建 `backend/mail_client.py`：

```python
"""学校邮箱（OWA / Microsoft 365）取信层。

分两层：
  - 纯函数层（本文件上半）：payload 构造、响应解析、范围筛选、JWT 过期解析。
    全部可用合成 fixture 单测，不触网、不开浏览器。
  - I/O 层（本文件下半）：token 截获与 REST 调用。

为什么走 REST 而不是点 DOM：实测邮件行点击会被 React portal 浮层拦截
（重试 13 次仍失败），且现代 OWA 的 Reading Pane 已无「标记为已读」开关。
REST 路线另有一个决定性好处：FindItem / GetItem 是纯读的，不会把邮件标成
已读（实测 IsRead 读完仍为 False），所以「读完还原未读」不需要实现。
详见 docs/superpowers/specs/2026-09-18-school-mail-design.md §3。
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timedelta, timezone

SVC_URL = "https://outlook.cloud.microsoft/owa/service.svc"
TIMEOUT = 30          # 单个 REST 请求超时（秒）
DEFAULT_DAYS = 7      # 「最近 N 天」的 N
PAGE_SIZE = 50        # FindItem 每页条数
BODY_BATCH = 10       # 一次 GetItem 带几个 ItemId（spec §11 待验证项的保守取值）
_THROTTLE = 0.5       # 批量 GetItem 之间的间隔（秒），见 spec §9「请求节流」

_HDR = {"__type": "JsonRequestHeaders:#Exchange",
        "RequestServerVersion": "Exchange2013"}


class MailError(Exception):
    """取信层失败。文案直接面向用户，不含 token 等敏感内容。"""


def _prop(field_uri: str) -> dict:
    return {"__type": "PropertyUri:#Exchange", "FieldURI": field_uri}


def find_item_payload(offset: int = 0, max_entries: int = PAGE_SIZE) -> dict:
    """FindItem 请求体。结构为实测通过（spec §5.3），不要凭记忆改写。"""
    return {
        "__type": "FindItemJsonRequest:#Exchange",
        "Header": dict(_HDR),
        "Body": {
            "__type": "FindItemRequest:#Exchange",
            "ItemShape": {
                "__type": "ItemResponseShape:#Exchange",
                "BaseShape": "IdOnly",
                "AdditionalProperties": [
                    _prop("item:Subject"),
                    _prop("message:From"),
                    _prop("item:DateTimeReceived"),
                    _prop("message:IsRead"),
                ],
            },
            "ParentFolderIds": [
                {"__type": "DistinguishedFolderId:#Exchange", "Id": "inbox"}],
            "Traversal": "Shallow",
            "Paging": {"__type": "IndexedPageView:#Exchange",
                       "BasePoint": "Beginning",
                       "Offset": offset,
                       "MaxEntriesReturned": max_entries},
        },
    }


def get_item_payload(item_ids: list[str]) -> dict:
    """GetItem 请求体。结构为实测通过（spec §5.3）。"""
    return {
        "__type": "GetItemJsonRequest:#Exchange",
        "Header": dict(_HDR),
        "Body": {
            "__type": "GetItemRequest:#Exchange",
            "ItemShape": {"__type": "ItemResponseShape:#Exchange",
                          "BaseShape": "Default",
                          "BodyType": "Text",
                          "AdditionalProperties": [_prop("item:Body")]},
            "ItemIds": [{"__type": "ItemId:#Exchange", "Id": i} for i in item_ids],
        },
    }


def _response_items(resp) -> list:
    """两层响应共用的入口：Body.ResponseMessages.Items。取不到就给空表。"""
    if not isinstance(resp, dict):
        return []
    body = resp.get("Body")
    if not isinstance(body, dict):
        return []
    msgs = body.get("ResponseMessages")
    if not isinstance(msgs, dict):
        return []
    items = msgs.get("Items")
    return items if isinstance(items, list) else []


def _sender_name(raw) -> str:
    """From 是结构化对象；取不到就返回空串，绝不编造。"""
    if not isinstance(raw, dict):
        return ""
    mb = raw.get("Mailbox")
    if isinstance(mb, dict):
        return mb.get("Name") or mb.get("Address") or ""
    return ""


def parse_find_items(resp: dict) -> list[dict]:
    """从 FindItem 响应抽出邮件列表（不含正文）。"""
    out = []
    for m in _response_items(resp):
        if not isinstance(m, dict):
            continue
        root = m.get("RootFolder")
        rows = root.get("Items") if isinstance(root, dict) else None
        for it in (rows or []):
            if not isinstance(it, dict):
                continue
            iid = (it.get("ItemId") or {}).get("Id")
            if not iid:
                continue          # 没有 Id 就无法回取正文 → 丢弃，不带 None 下去
            out.append({
                "id": iid,
                "subject": it.get("Subject") or "",
                "sender": _sender_name(it.get("From")),
                "received": it.get("DateTimeReceived") or "",
                "is_read": bool(it.get("IsRead")),
            })
    return out


def parse_get_bodies(resp: dict) -> list[dict]:
    """从 GetItem 响应抽出 [{id, body}]。批量返回顺序不保证，靠 id 对应。"""
    out = []
    for m in _response_items(resp):
        if not isinstance(m, dict):
            continue
        for it in (m.get("Items") or []):
            if not isinstance(it, dict):
                continue
            iid = (it.get("ItemId") or {}).get("Id")
            if not iid:
                continue
            val = (it.get("Body") or {}).get("Value")
            out.append({"id": iid, "body": val if isinstance(val, str) else ""})
    return out


def _parse_ts(raw: str) -> datetime | None:
    """OWA 时间戳形如 2026-09-17T08:31:00Z。解析不出返回 None。"""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def select_scope(items: list[dict], now: datetime,
                 days: int = DEFAULT_DAYS) -> list[dict]:
    """取「未读 ∪ 最近 days 天」。

    未读不看时间：可能很旧但仍需处理。
    received 解析不出来的条目按「保留」处理 —— 宁可多给一封，不要静默丢件。
    """
    cutoff = now - timedelta(days=days)
    out = []
    for it in items:
        if not it.get("is_read"):
            out.append(it)
            continue
        ts = _parse_ts(it.get("received", ""))
        if ts is None or ts >= cutoff:
            out.append(it)
    return out


def jwt_exp(token: str) -> datetime | None:
    """解 JWT payload 取 exp（不验签，只读过期时间）。失败返回 None。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        return datetime.fromtimestamp(int(exp), tz=timezone.utc) if exp else None
    except (AttributeError, IndexError, ValueError, TypeError, binascii.Error):
        return None


def extract_bearer(headers: dict) -> str | None:
    """从请求头里取 Bearer token。头名大小写不敏感。"""
    for k, v in (headers or {}).items():
        if str(k).lower() == "authorization" and isinstance(v, str) \
                and v.lower().startswith("bearer "):
            return v.split(" ", 1)[1].strip()
    return None
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_mail_client.py -q
```

预期：全绿（约 15 个用例）。

- [ ] **Step 5: 变异检验**

临时把 `select_scope` 里的 `ts >= cutoff` 改成 `ts > cutoff`，重跑 → `test_select_scope_boundary_is_inclusive` 必须红。再把 `if not it.get("is_read")` 改成 `if False` → `test_select_scope_keeps_recent_read_and_all_unread` 必须红。**改回原样**。

- [ ] **Step 6: 提交**

```bash
git add backend/mail_client.py tests/test_mail_client.py
git commit -m "feat(mail): OWA 取信层纯函数——payload/解析/范围筛选/JWT 过期"
```

---

## Task 2: `banweb.py` 邮件域隔离

邮件与 AIMS 共用同一个 persistent context。若 AIMS 的 `_find_page` 认领了邮件 tab，后续 `goto(TERM_PAGE)` 会把邮件 tab 导航走、毁掉邮件会话。本任务把邮件域从 AIMS 的选页逻辑里排除掉。

**Files:**
- Modify: `backend/banweb.py`（常量区 ~29-50；`_find_page` 618-622；`_ensure_browser` 849-864）
- Test: `tests/test_banweb.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `MAIL_HOSTS: tuple[str, ...]`
  - `_is_mail_url(url: str) -> bool`
  - `_find_mail_page(ctx) -> object`
  - **`_ensure_mail_page() -> object`** —— 邮件侧的入口：确保浏览器在跑，返回**邮件 tab**（没有就开一个）。Task 3 消费它
  - **`_find_page(ctx) -> object | None`** —— 签名语义变化：没页可挑时返回 `None`，不再自己 `ctx.new_page()`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_banweb.py` 末尾：

```python
# --- 邮件 tab 隔离（spec §5.1 约束 1）---

class _Page:
    def __init__(self, url):
        self.url = url


class _Ctx:
    def __init__(self, urls):
        self.pages = [_Page(u) for u in urls]
        self.new_pages = 0

    def new_page(self):
        self.new_pages += 1
        p = _Page("about:blank")
        self.pages.append(p)
        return p


def test_is_mail_url_matches_both_owa_hosts():
    assert banweb._is_mail_url("https://outlook.cloud.microsoft/mail/")
    assert banweb._is_mail_url("https://outlook.office.com/mail/")
    assert not banweb._is_mail_url("https://banweb.cityu.edu.hk/x")
    assert not banweb._is_mail_url("")


def test_find_page_prefers_aims_page_over_mail_tab():
    ctx = _Ctx(["https://outlook.cloud.microsoft/mail/",
                "https://banweb.cityu.edu.hk/twkwkmgr.P_DispMyWeek"])
    assert banweb._find_page(ctx).url.startswith(banweb.BANWEB)


def test_find_page_never_returns_a_mail_tab():
    """只剩邮件 tab 时必须返回 None —— 认领它会让 AIMS 把邮件页导航走。"""
    ctx = _Ctx(["https://outlook.cloud.microsoft/mail/"])
    assert banweb._find_page(ctx) is None
    assert ctx.new_pages == 0        # 且绝不能自己开页


def test_find_page_still_falls_back_to_unknown_blank_page():
    """非邮件域的非 AIMS 页仍照旧兜底 —— 现有行为不能变。"""
    ctx = _Ctx(["about:blank"])
    assert banweb._find_page(ctx).url == "about:blank"


def test_find_page_returns_none_when_no_pages():
    assert banweb._find_page(_Ctx([])) is None


def test_find_mail_page_reuses_existing_tab():
    ctx = _Ctx(["https://banweb.cityu.edu.hk/x",
                "https://outlook.cloud.microsoft/mail/"])
    assert banweb._find_mail_page(ctx).url.startswith("https://outlook.cloud.microsoft")
    assert ctx.new_pages == 0


def test_find_mail_page_opens_one_when_absent():
    ctx = _Ctx(["https://banweb.cityu.edu.hk/x"])
    assert banweb._find_mail_page(ctx).url == "about:blank"
    assert ctx.new_pages == 1


def test_ensure_mail_page_returns_mail_tab_not_aims_tab(monkeypatch):
    """关键：_ensure_browser 返回的是 **AIMS 页**，拿它去 goto 邮件 URL 会把
    AIMS tab 导航走。邮件必须走 _ensure_mail_page 拿到邮件 tab。"""
    ctx = _Ctx(["https://banweb.cityu.edu.hk/twkwkmgr.P_DispMyWeek",
                "https://outlook.cloud.microsoft/mail/"])
    monkeypatch.setattr(banweb, "_ensure_browser", lambda **k: ctx.pages[0])
    monkeypatch.setattr(banweb, "_ctx", ctx)
    got = banweb._ensure_mail_page()
    assert got.url.startswith("https://outlook.cloud.microsoft")


def test_ensure_mail_page_raises_when_browser_never_came_up(monkeypatch):
    """_ensure_browser 抛错时不要吞 —— 原样传播，让上层报「会话已失效」。"""
    def boom(**k):
        raise banweb.BanwebError("AIMS 登录窗口未打开")
    monkeypatch.setattr(banweb, "_ensure_browser", boom)
    with pytest.raises(banweb.BanwebError):
        banweb._ensure_mail_page()
```

`tests/test_banweb.py` 顶部若没有 `import pytest`，补上。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_banweb.py -q -k "mail or find_page"
```

预期：`AttributeError: module 'backend.banweb' has no attribute '_is_mail_url'`。

- [ ] **Step 3: 加常量与谓词**

在 `backend/banweb.py` 的 `LOGIN_HOST` / `BANWEB` 定义之后（约 30 行下方）插入：

```python
# 学校邮箱（OWA）的 host。邮件与 AIMS 共用同一个 persistent context，
# 但必须是两个独立的 tab —— 见 _find_page 的排除逻辑。
MAIL_HOSTS = ("https://outlook.cloud.microsoft", "https://outlook.office.com")


def _is_mail_url(url: str) -> bool:
    return any((url or "").startswith(h) for h in MAIL_HOSTS)
```

- [ ] **Step 4: 改 `_find_page`**

把 `backend/banweb.py:618-622` 整体替换为：

```python
def _find_page(ctx) -> object:
    """挑一个 AIMS 用的页；没有可用的返回 None（由调用方决定要不要开新页）。

    排除邮件 tab：邮件与 AIMS 共用同一个 persistent context，若 AIMS 认领了
    邮件页，后续 goto(TERM_PAGE) 会把邮件 tab 导航走、毁掉邮件会话。

    本函数**绝不** new_page —— 调用方 launch=False（轮询）时必须能拿到 None
    去走「未开窗」分支，自己开页会违反「轮询绝不弹窗」。
    """
    for p in ctx.pages:
        if _is_mail_url(p.url):
            continue
        if p.url.startswith(BANWEB) or p.url.startswith(LOGIN_HOST):
            return p
    for p in ctx.pages:
        if not _is_mail_url(p.url):
            return p          # 非邮件域的非 AIMS 页（如残留空白页）→ 照旧兜底
    return None


def _find_mail_page(ctx) -> object:
    """挑邮件 tab；没有就开一个。

    与 _find_page 不同，这里**允许** new_page：邮件操作永远由用户点按钮触发，
    开窗不违反「轮询绝不弹窗」。
    """
    for p in ctx.pages:
        if _is_mail_url(p.url):
            return p
    return ctx.new_page()


def _ensure_mail_page() -> object:
    """邮件侧的浏览器入口：确保浏览器在跑，返回**邮件 tab**。

    绝不能改用 _ensure_browser 的返回值 —— 它给的是 AIMS 页，拿它去
    goto(MAIL_URL) 会把 AIMS tab 导航走、毁掉 AIMS 会话。两者是同一个
    persistent context 里的两个 tab，必须各拿各的。
    """
    _ensure_browser(launch=True)          # 只为保证 _ctx 就绪，丢弃返回的 AIMS 页
    if _ctx is None:
        raise BanwebError("AIMS 浏览器未就绪，无法打开邮箱页")
    return _find_mail_page(_ctx)
```

- [ ] **Step 5: 改 `_ensure_browser` 适配 `None`**

把 `backend/banweb.py:849-864` 替换为：

```python
                if browser.contexts:
                    _ctx = browser.contexts[0]
                    # 没有可用 AIMS 页 → None（无窗口，或只剩邮件 tab）。
                    # 与「无窗口」同等对待，绝不擅自把邮件 tab 当 AIMS 页用。
                    _page = _find_page(_ctx) if _ctx.pages else None
                    if _page is not None:
                        return _page
                    if launch:
                        # 用户主动操作且进程还活着但没窗口 → 开个新标签页当窗口
                        _page = _ctx.new_page()
                        return _page
                    # 轮询（launch=False）：进程在但没窗口 → 视为未开窗，
                    # 绝不自己弹窗。干净断开连接后交给下方 needs_login 分支。
                    try:
                        browser.close()
                    except Exception:
                        pass
```

（`browser.close()` 那段与原文逐字相同，只是从 `if _ctx.pages:` 的 else 分支提到了同级——行为等价。）

- [ ] **Step 6: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_banweb.py -q
```

预期：全绿，且**原有用例一个不红**（`_find_page` 只有一个调用点，在 `_ensure_browser` 内，已一并适配）。

- [ ] **Step 7: 变异检验**

临时删掉 `_find_page` 里的两处 `_is_mail_url` 过滤 → `test_find_page_never_returns_a_mail_tab` 与 `test_find_page_prefers_aims_page_over_mail_tab` 必须红。临时把 `return None` 改回 `return ctx.pages[0] if ctx.pages else ctx.new_page()` → `test_find_page_never_returns_a_mail_tab` 与 `test_find_page_returns_none_when_no_pages` 必须红。**改回原样**。

- [ ] **Step 8: 提交**

```bash
git add backend/banweb.py tests/test_banweb.py
git commit -m "fix(banweb): _find_page 排除邮件 tab，防止 AIMS 导航走邮件页"
```

---

## Task 3: token 截获 + REST I/O

**Files:**
- Modify: `backend/mail_client.py`（追加 I/O 段）
- Test: `tests/test_mail_client.py`（追加）

**Interfaces:**
- Consumes: Task 1 的全部纯函数；`banweb._on_browser_thread`、`banweb._retry_once`、**`banweb._ensure_mail_page()`**（Task 2 产出）
- Produces:
  - `MAIL_URL: str`
  - `_post_json(action: str, payload: dict, token: str) -> dict`
  - `_capture_token(page) -> str`
  - `collect_items(token: str, days: int, now: datetime) -> list[dict]`
  - `list_mail(days: int = DEFAULT_DAYS) -> list[dict]`
  - `fetch_bodies(items: list[dict]) -> dict[str, str]`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_mail_client.py`：

```python
# --- I/O 层：替身接管浏览器与 HTTP，仍不出网 ---
# 注意 tests/conftest.py 的 netguard：任何真实 socket 尝试都会让用例红。


class _FakePage:
    """只用于占位 —— _capture_token 与 _post_json 在测试里都被替身接管。"""
    url = mail_client.MAIL_URL


@pytest.fixture
def no_browser(monkeypatch):
    """把浏览器边界与 REST 边界都换成替身，并让 executor 变成同步直调。"""
    monkeypatch.setattr(mail_client.banweb, "_on_browser_thread", lambda fn: fn())
    monkeypatch.setattr(mail_client.banweb, "_retry_once", lambda fn: fn())
    monkeypatch.setattr(mail_client.banweb, "_ensure_mail_page", lambda: _FakePage())
    monkeypatch.setattr(mail_client, "_capture_token", lambda page: "TOK")


def test_collect_items_paginates_until_short_page(monkeypatch):
    pages = {
        0: [_item(f"A{n}") for n in range(mail_client.PAGE_SIZE)],
        mail_client.PAGE_SIZE: [_item("LAST")],
    }
    calls = []

    def fake_post(action, payload, token):
        assert action == "FindItem"
        assert token == "TOK"
        off = payload["Body"]["Paging"]["Offset"]
        calls.append(off)
        return _find_resp(pages.get(off, []))

    monkeypatch.setattr(mail_client, "_post_json", fake_post)
    got = mail_client.collect_items("TOK", 7, NOW)
    assert calls == [0, mail_client.PAGE_SIZE]      # 第二页不满 → 停，不翻第三页
    assert len(got) == mail_client.PAGE_SIZE + 1


def test_collect_items_stops_on_empty_page(monkeypatch):
    calls = []

    def fake_post(action, payload, token):
        calls.append(payload["Body"]["Paging"]["Offset"])
        return _find_resp([])

    monkeypatch.setattr(mail_client, "_post_json", fake_post)
    assert mail_client.collect_items("TOK", 7, NOW) == []
    assert calls == [0]


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def test_list_mail_returns_scoped_items(no_browser, monkeypatch):
    old = (NOW - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = (NOW - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [_item("旧读", recv=old, is_read=True),
            _item("近读", recv=new, is_read=True),
            _item("旧未读", recv=old, is_read=False)]
    monkeypatch.setattr(mail_client, "_post_json", lambda a, p, t: _find_resp(rows))
    monkeypatch.setattr(mail_client, "datetime", _FrozenDatetime)
    got = mail_client.list_mail(7)
    assert {x["id"] for x in got} == {"近读", "旧未读"}


def test_fetch_bodies_batches_and_maps_back_by_id(no_browser, monkeypatch):
    ids = [f"B{n}" for n in range(mail_client.BODY_BATCH + 3)]
    batches = []

    def fake_post(action, payload, token):
        assert action == "GetItem"
        got = [x["Id"] for x in payload["Body"]["ItemIds"]]
        batches.append(got)
        return _get_resp([(i, "正文" + i) for i in got])

    monkeypatch.setattr(mail_client, "_post_json", fake_post)
    monkeypatch.setattr(mail_client.time, "sleep", lambda s: None)   # 不真睡
    out = mail_client.fetch_bodies([{"id": i} for i in ids])
    assert [len(b) for b in batches] == [mail_client.BODY_BATCH, 3]
    assert set(out) == set(ids)
    assert out["B0"] == "正文B0"


def test_fetch_bodies_empty_input_makes_no_request(no_browser, monkeypatch):
    """没有邮件时绝不能去开浏览器 —— 用户没点刷新就不该有浏览器动作。"""
    called = []
    monkeypatch.setattr(mail_client.banweb, "_ensure_mail_page",
                        lambda: called.append(1))
    assert mail_client.fetch_bodies([]) == {}
    assert called == []


def test_capture_token_raises_when_page_never_emits(monkeypatch):
    """页面没登入 → 截不到 token → 明确报错，不静默返回空串。"""
    class _P:
        def on(self, ev, fn): pass
        def remove_listener(self, ev, fn): pass
        def goto(self, *a, **k): pass
        def wait_for_timeout(self, ms): pass

    monkeypatch.setattr(mail_client, "_TOKEN_WAIT_TICKS", 2)
    with pytest.raises(mail_client.MailError):
        mail_client._capture_token(_P())
```

**注意**：`test_list_mail_returns_scoped_items` 把 `mail_client.datetime` 换成冻结类。`_do_list` 里写的是 `datetime.now(timezone.utc)`，替身后返回 `NOW`，与 fixture 的时间戳对齐。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_mail_client.py -q -k "collect or list_mail or fetch_bodies or capture"
```

预期：`AttributeError: module 'backend.mail_client' has no attribute 'collect_items'`。

- [ ] **Step 3: 写 I/O 实现**

把 `backend/mail_client.py` 顶部 import 区改成：

```python
import base64
import binascii
import json
import time
from datetime import datetime, timedelta, timezone

from . import banweb
```

并在文件末尾追加：

```python
# ---------------------------------------------------------------------------
# I/O 层
# ---------------------------------------------------------------------------

MAIL_URL = "https://outlook.cloud.microsoft/mail/"
_TOKEN_WAIT_TICKS = 40        # 截获 token 的轮询次数
_TOKEN_WAIT_MS = 500          # 每次间隔 → 上限 20s


def _post_json(action: str, payload: dict, token: str) -> dict:
    """调用 service.svc。独立成函数，便于测试替身接管（本仓测试禁止真实出网）。"""
    import requests
    try:
        r = requests.post(
            SVC_URL,
            params={"action": action, "app": "Mail"},
            json=payload,
            timeout=TIMEOUT,
            headers={"Authorization": f"Bearer {token}",
                     "Action": action,
                     "Content-Type": "application/json; charset=utf-8",
                     "X-OWA-Timezone": "China Standard Time"},
        )
    except Exception as exc:
        raise MailError(f"OWA {action} 请求失败：{exc}") from exc
    if not r.ok:
        raise MailError(f"OWA {action} 失败：HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as exc:
        raise MailError(f"OWA {action} 返回的不是 JSON") from exc


def _capture_token(page) -> str:
    """让邮件页加载一次，从它自己打出的 service.svc 请求里截获 bearer token。

    token 只在本函数返回值里存活，调用方用完即弃 —— 绝不落盘、绝不返回前端。
    """
    box: dict = {}

    def _on_req(req):
        if "service.svc" in req.url and "token" not in box:
            t = extract_bearer(req.headers)
            if t:
                box["token"] = t

    page.on("request", _on_req)
    try:
        page.goto(MAIL_URL, wait_until="domcontentloaded", timeout=60000)
        for _ in range(_TOKEN_WAIT_TICKS):
            if "token" in box:
                break
            page.wait_for_timeout(_TOKEN_WAIT_MS)
    finally:
        try:
            page.remove_listener("request", _on_req)
        except Exception:
            pass
    token = box.get("token")
    if not token:
        # 多半是 Okta 会话已失效 → 页面停在登录页。文案里绝不带 token 相关内容。
        raise MailError("邮箱会话已失效，未能截获访问令牌。请先在 AIMS 页重新登录后再试。")
    return token


def collect_items(token: str, days: int, now: datetime) -> list[dict]:
    """FindItem 翻页取列表 → 筛出「未读 ∪ 最近 days 天」。不含正文。"""
    items: list[dict] = []
    offset = 0
    while True:
        resp = _post_json("FindItem", find_item_payload(offset), token)
        batch = parse_find_items(resp)
        if not batch:
            break
        items += batch
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return select_scope(items, now, days)


def _do_list(days: int) -> list[dict]:
    page = banweb._ensure_mail_page()      # 邮件 tab，绝不是 AIMS 页
    return collect_items(_capture_token(page), days, datetime.now(timezone.utc))


def list_mail(days: int = DEFAULT_DAYS) -> list[dict]:
    """拉取「未读 ∪ 最近 days 天」的邮件（不含正文）。"""
    return banweb._on_browser_thread(lambda: banweb._retry_once(lambda: _do_list(days)))


def _do_bodies(items: list[dict]) -> dict:
    ids = [it["id"] for it in items]
    page = banweb._ensure_mail_page()      # 邮件 tab，绝不是 AIMS 页
    token = _capture_token(page)
    out: dict = {}
    chunks = [ids[i:i + BODY_BATCH] for i in range(0, len(ids), BODY_BATCH)]
    for n, chunk in enumerate(chunks):
        if n:
            time.sleep(_THROTTLE)     # spec §9 请求节流
        for row in parse_get_bodies(_post_json("GetItem", get_item_payload(chunk), token)):
            out[row["id"]] = row["body"]
    return out


def fetch_bodies(items: list[dict]) -> dict:
    """批量取正文，返回 {item_id: body}。

    空输入直接返回 —— 绝不为了「取零封邮件的正文」去开浏览器。
    全程纯读：不调 MarkAsRead，不改用户已读状态。
    """
    if not items:
        return {}
    return banweb._on_browser_thread(lambda: banweb._retry_once(lambda: _do_bodies(items)))
```

**关于 spec §5.1 约束 2（邮件 tab 被看门狗清掉）**：不需要额外代码，本设计天然容忍。
`_reset_browser()` 端掉整个 context 后，下一次 `_ensure_mail_page()` 会经
`_ensure_browser(launch=True)` 重建、`_find_mail_page` 发现没有邮件 tab 就新开一个；
`_retry_once` 也会在 "has been closed" 时重跑整条 `_do_list` / `_do_bodies`。
AIMS 与邮件的操作都跑在同一个单 worker executor 上，天然串行、不会互相踩。

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_mail_client.py -q
```

预期：全绿。

- [ ] **Step 5: 变异检验**

把 `offset += PAGE_SIZE` 改成 `offset += 1` → `test_collect_items_paginates_until_short_page` 的 `calls` 断言必红。把 `if len(batch) < PAGE_SIZE: break` 删掉 → 同用例会持续命中 `pages.get(off, [])` 返回空表而终止，`calls` 断言仍红。**两条分别验，改回原样。**（`time.sleep` 那条不做变异检验：替身已接管，改 `if n:` 不会红。）

- [ ] **Step 6: 提交**

```bash
git add backend/mail_client.py tests/test_mail_client.py
git commit -m "feat(mail): token 截获 + FindItem/GetItem REST 调用与节流"
```

---

## Task 4: 端点 `/api/mail/list`

**Files:**
- Modify: `backend/main.py`（import 元组 ~15-16；模型区 ~44 之后；端点区末尾）
- Test: `tests/test_main.py`（追加）

**Interfaces:**
- Consumes: `mail_client.list_mail(days) -> list[dict]`（Task 3），每项 `{id, subject, sender, received, is_read}`
- Produces: `POST /api/mail/list`，body `{days?: int}` → `{ok: true, count: int, mails: [...]}` 或 `{ok: false, error: str}`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_main.py` 末尾：

```python
# --- 学校邮箱 ---

def test_mail_list_returns_mails(monkeypatch):
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [
        {"id": "A", "subject": "作业", "sender": "李老師",
         "received": "2026-09-17T08:31:00Z", "is_read": False}])
    body = TestClient(main.app).post("/api/mail/list", json={"days": 7}).json()
    assert body["ok"] is True
    assert body["count"] == 1
    assert body["mails"][0]["subject"] == "作业"
    # 硬约束：响应里绝不能出现 token
    assert "token" not in json.dumps(body)


def test_mail_list_defaults_to_seven_days(monkeypatch):
    seen = {}
    monkeypatch.setattr(mail_client, "list_mail",
                        lambda days: seen.setdefault("d", days) or [])
    TestClient(main.app).post("/api/mail/list", json={})
    assert seen["d"] == 7


def test_mail_list_failure_is_200_json(monkeypatch):
    """取信失败不能裸 500 —— 前端 api() 靠 ok 字段走兜底。"""
    def boom(days):
        raise mail_client.MailError("邮箱会话已失效")
    monkeypatch.setattr(mail_client, "list_mail", boom)
    r = TestClient(main.app).post("/api/mail/list", json={})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "邮箱会话已失效"}
```

`tests/test_main.py` 顶部若没有 `import json`，补上；并确认已 `from backend import mail_client`（若无则加）。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_main.py -q -k mail_list
```

预期：`AttributeError`（`mail_client` 未导入）或 404。

- [ ] **Step 3: 加 import 与模型**

`backend/main.py:15` 的 import 元组里加 `mail_client`（保持字母序）：

```python
from . import (apple_script, banweb, canvas_client, credentials, files_downloader,
               llm_client, mail_client, ...)
```

在 `LLMConfig`（44 行）之后插入：

```python
class MailListRequest(BaseModel):
    days: int = 7
```

- [ ] **Step 4: 加端点**

追加到 `backend/main.py` 端点区末尾：

```python
@app.post("/api/mail/list")
def mail_list(req: MailListRequest):
    """列出「未读 ∪ 最近 N 天」的学校邮箱邮件。只读，不调 LLM、不改已读状态。"""
    try:
        mails = mail_client.list_mail(req.days)
        return {"ok": True, "count": len(mails), "mails": mails}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
```

- [ ] **Step 5: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_main.py -q -k mail_list
```

预期：3 个用例全绿。返回值里绝无 token 字段 —— 这是 Global Constraints 的硬要求。

- [ ] **Step 6: 变异检验**

把 `MailListRequest.days` 默认值改成 `30` → `test_mail_list_defaults_to_seven_days` 必须红。把 `except Exception` 里的 `return` 改成 `raise` → `test_mail_list_failure_is_200_json` 必须红。**改回原样**。

- [ ] **Step 7: 提交**

```bash
git add backend/main.py tests/test_main.py
git commit -m "feat(mail): /api/mail/list 端点（只读取信）"
```

---

## Task 5: `llm_client.summarize_mail()`

**Files:**
- Modify: `backend/llm_client.py`（末尾追加）
- Test: `tests/test_llm_client.py`（追加）

**Interfaces:**
- Consumes: 现有 `_call_chat(base_url, api_key, model, prompt, json_mode=True) -> str`、`_parse_json(text) -> dict`
- Produces:
  - `_MAX_BODY = 4000`、`_MAX_MAIL_TOTAL = 20000`、`_MIN_BODY = 400`
  - `trim_bodies(mails: list[dict], budget: int = _MAX_MAIL_TOTAL) -> list[dict]`
  - `_normalize_groups(data: dict, mails: list[dict], language: str) -> dict`
  - `summarize_mail(base_url, api_key, model, mails: list[dict], language="zh") -> dict`
    - `mails` 每项：`{index: int, subject, sender, received, body}`
    - 返回 `{"groups": [...], "warnings": [str, ...]}`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_llm_client.py`：

```python
# --- summarize_mail ---

def _mail(i, subject="作业", body="正文"):
    return {"index": i, "subject": subject, "sender": "李老師",
            "received": "2026-09-17T08:31:00Z", "body": body}


def test_trim_bodies_preserves_count():
    """条数永远不变 —— 对齐靠 index，丢一条就整体错位。"""
    mails = [_mail(i, body="x" * 50000) for i in range(4)]
    out = llm_client.trim_bodies(mails, budget=2000)
    assert len(out) == 4
    assert [m["index"] for m in out] == [0, 1, 2, 3]
    assert all(len(m["body"]) <= 500 for m in out)


def test_trim_bodies_caps_long_body():
    out = llm_client.trim_bodies([_mail(0, body="y" * 99999)])
    assert len(out[0]["body"]) == llm_client._MAX_BODY


def test_trim_bodies_never_shrinks_below_floor():
    """邮件极多时也不能把每封压到 0 字 —— 有下限，宁可超预算。"""
    mails = [_mail(i, body="z" * 10000) for i in range(500)]
    out = llm_client.trim_bodies(mails, budget=20000)
    assert all(len(m["body"]) == llm_client._MIN_BODY for m in out)


def test_trim_bodies_does_not_mutate_input():
    m = _mail(0, body="q" * 99999)
    llm_client.trim_bodies([m])
    assert len(m["body"]) == 99999


def test_summarize_mail_happy_path(monkeypatch):
    payload = {"groups": [{"course": "CS1315", "types": [
        {"type": "作业", "items": [{"mail_index": 0, "summary": "交实验报告",
            "calendar_events": [], "reminders": [
                {"title": "交报告", "due_date": "2026-09-25T23:59:00", "notes": ""}]}]}]}]}
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: json.dumps(payload))
    r = llm_client.summarize_mail("u", "k", "m", [_mail(0)])
    assert r["warnings"] == []
    item = r["groups"][0]["types"][0]["items"][0]
    assert item["mail_index"] == 0
    assert item["reminders"][0]["title"] == "交报告"


def test_summarize_mail_drops_out_of_range_index(monkeypatch):
    """AI 编出的越界 index 必须丢 —— 绝不能把内容挂到不存在的邮件上。"""
    payload = {"groups": [{"course": "C", "types": [{"type": "T", "items": [
        {"mail_index": 99, "summary": "幽灵", "calendar_events": [], "reminders": []}]}]}]}
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: json.dumps(payload))
    r = llm_client.summarize_mail("u", "k", "m", [_mail(0)])
    assert r["groups"] == []
    assert r["warnings"]          # 丢了东西必须留痕，不静默


def test_summarize_mail_falls_back_to_excerpt_for_bad_summary(monkeypatch):
    payload = {"groups": [{"course": "C", "types": [{"type": "T", "items": [
        {"mail_index": 0, "summary": 12345, "calendar_events": [], "reminders": []}]}]}]}
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: json.dumps(payload))
    r = llm_client.summarize_mail("u", "k", "m", [_mail(0, body="课程正文内容")])
    s = r["groups"][0]["types"][0]["items"][0]["summary"]
    assert isinstance(s, str) and "课程正文内容" in s


def test_summarize_mail_retries_once_then_warns(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_client, "_call_chat",
                        lambda *a, **k: calls.append(1) or "不是 JSON")
    r = llm_client.summarize_mail("u", "k", "m", [_mail(0)])
    assert len(calls) == 2        # 重试一次
    assert r["groups"] == []
    assert r["warnings"]


def test_summarize_mail_empty_input_skips_llm(monkeypatch):
    called = []
    monkeypatch.setattr(llm_client, "_call_chat",
                        lambda *a, **k: called.append(1) or "{}")
    assert llm_client.summarize_mail("u", "k", "m", []) == {"groups": [], "warnings": []}
    assert called == []
```

`tests/test_llm_client.py` 顶部若没有 `import json`，补上。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_llm_client.py -q -k "mail or trim_bodies"
```

预期：`AttributeError: module 'backend.llm_client' has no attribute 'trim_bodies'`。

- [ ] **Step 3: 写实现**

追加到 `backend/llm_client.py` 末尾：

```python
# ---------------------------------------------------------------------------
# 邮件 AI 总结（课程 × 类型 两级）
# ---------------------------------------------------------------------------

_MAX_BODY = 4000          # 单封正文截断
_MAX_MAIL_TOTAL = 20000   # 全部正文的总预算（对齐 _MAX_SYLLABUS 的先例）
_MIN_BODY = 400           # 每封的下限：宁可超预算，也不把邮件压成 0 字


def trim_bodies(mails: list[dict], budget: int = _MAX_MAIL_TOTAL) -> list[dict]:
    """单封截断 + 总量预算。**条数永远不变**（对齐靠 index，丢条会整体错位）。"""
    n = max(1, len(mails))
    cap = min(_MAX_BODY, max(_MIN_BODY, budget // n))
    return [{**m, "body": (m.get("body") or "")[:cap]} for m in mails]


def _excerpt(m: dict) -> str:
    """模型没给可用摘要时的回退 —— 用原文摘录，不编造。"""
    text = (m.get("body") or "").strip() or (m.get("subject") or "").strip()
    return text[:200]


_MAIL_SCHEMA_ZH = """只输出如下 JSON，不要任何解释、不要 markdown 代码块：
{"groups":[{"course":"课程代码或「学校行政」","types":[
  {"type":"作业|考试|通知|行政|其他","items":[
    {"mail_index":<输入里的邮件编号，整数>,
     "summary":"一句话中文摘要",
     "calendar_events":[{"title":"","start":"YYYY-MM-DDTHH:MM:SS","end":"YYYY-MM-DDTHH:MM:SS","location":"","notes":""}],
     "reminders":[{"title":"","due_date":"YYYY-MM-DDTHH:MM:SS","notes":""}]}]}]}]}
规则：
- 邮件里**没有**明确截止时间时，calendar_events 与 reminders 都留空数组，绝不臆造日期。
- 一封邮件只能出现一次。所有输入邮件都必须被覆盖到。
- 归不进具体课程的（校方通知、图书馆、IT 等）course 填「学校行政」。"""

_MAIL_SCHEMA_EN = """Output only the following JSON, no prose, no markdown fences:
{"groups":[{"course":"course code or \\"Administration\\"","types":[
  {"type":"assignment|exam|notice|admin|other","items":[
    {"mail_index":<the mail number from the input, integer>,
     "summary":"one-sentence summary",
     "calendar_events":[{"title":"","start":"YYYY-MM-DDTHH:MM:SS","end":"YYYY-MM-DDTHH:MM:SS","location":"","notes":""}],
     "reminders":[{"title":"","due_date":"YYYY-MM-DDTHH:MM:SS","notes":""}]}]}]}]}
Rules:
- If a mail states **no** explicit deadline, leave calendar_events and reminders empty. Never invent dates.
- Each mail appears exactly once. Every input mail must be covered.
- Non-course mail goes under course "Administration"."""


def _build_mail_prompt(mails: list[dict], language: str = "zh") -> str:
    if language == "en":
        head = ("You are triaging a CityU student's school mailbox. Group the mails "
                "below by course then type, with a summary and any deadlines.\n")
        schema = _MAIL_SCHEMA_EN
    else:
        head = ("你在帮一名香港城市大学学生整理学校邮箱。下面是本次要处理的邮件，"
                "请按「课程 → 类型」两级归类，并给出摘要与截止事项。\n")
        schema = _MAIL_SCHEMA_ZH
    parts = [head, schema, "\n---\n"]
    for m in mails:
        parts.append(
            f"### 邮件 #{m['index']}\n"
            f"主题：{m.get('subject', '')}\n"
            f"发件人：{m.get('sender', '')}\n"
            f"时间：{m.get('received', '')}\n"
            f"正文：\n{m.get('body', '')}\n\n")
    return "".join(parts)


def _normalize_groups(data: dict, mails: list[dict], language: str) -> dict:
    """把模型输出收敛成可信结构：越界 index 丢弃、坏字段回退、全程留痕。"""
    other = "Other" if language == "en" else "其他"
    warnings: list[str] = []
    dropped = 0
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return {"groups": [], "warnings": [
            "AI 返回的结构不可用，未生成分组" if language == "zh"
            else "AI returned an unusable structure; no groups produced"]}
    out = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        types_out = []
        for ty in (g.get("types") or []):
            if not isinstance(ty, dict):
                continue
            items_out = []
            for it in (ty.get("items") or []):
                if not isinstance(it, dict):
                    continue
                idx = it.get("mail_index")
                # 越界 / 非整数一律丢：绝不能把 AI 编的内容挂到真实邮件上
                if isinstance(idx, bool) or not isinstance(idx, int) \
                        or not (0 <= idx < len(mails)):
                    dropped += 1
                    continue
                summary = it.get("summary")
                items_out.append({
                    "mail_index": idx,
                    "summary": summary if isinstance(summary, str) and summary.strip()
                               else _excerpt(mails[idx]),
                    "calendar_events": it.get("calendar_events")
                                       if isinstance(it.get("calendar_events"), list) else [],
                    "reminders": it.get("reminders")
                                 if isinstance(it.get("reminders"), list) else [],
                })
            if items_out:
                types_out.append({"type": str(ty.get("type") or other), "items": items_out})
        if types_out:
            out.append({"course": str(g.get("course") or other), "types": types_out})
    if dropped:
        warnings.append(
            f"AI 返回的 {dropped} 条结果指向了不存在的邮件，已丢弃" if language == "zh"
            else f"Dropped {dropped} AI item(s) pointing at nonexistent mail")
    return {"groups": out, "warnings": warnings}


def summarize_mail(base_url: str, api_key: str, model: str,
                   mails: list[dict], language: str = "zh") -> dict:
    """对邮件做「课程 × 类型」两级归类 + 摘要 + DDL/待办抽取。

    `mails` 每项：{index, subject, sender, received, body}。
    返回 {"groups": [...], "warnings": [...]}；整体失败时 groups 为空、warnings 有话说，
    **绝不静默降级**。
    """
    if not mails:
        return {"groups": [], "warnings": []}
    trimmed = trim_bodies(mails)
    prompt = _build_mail_prompt(trimmed, language)
    data = _parse_json(_call_chat(base_url, api_key, model, prompt, json_mode=True))
    if not data:
        data = _parse_json(_call_chat(base_url, api_key, model, prompt, json_mode=True))
    if not data:
        return {"groups": [], "warnings": [
            "AI 总结失败：返回内容无法解析为 JSON" if language == "zh"
            else "AI summary failed: response was not parsable JSON"]}
    return _normalize_groups(data, trimmed, language)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_llm_client.py -q
```

预期：全绿，原有用例一个不红。

- [ ] **Step 5: 变异检验**

把 `_normalize_groups` 里的越界判断 `not (0 <= idx < len(mails))` 改成 `False` → `test_summarize_mail_drops_out_of_range_index` 必须红。把 `trim_bodies` 的 `{**m, ...}` 改成原地 `m["body"] = ...` → `test_trim_bodies_does_not_mutate_input` 必须红。把重试那次 `_call_chat` 删掉 → `test_summarize_mail_retries_once_then_warns` 必须红。**改回原样**。

- [ ] **Step 6: 提交**

```bash
git add backend/llm_client.py tests/test_llm_client.py
git commit -m "feat(mail): llm_client.summarize_mail 课程×类型两级归类与 DDL 抽取"
```

---

## Task 6: 端点 `/api/mail/summarize`

**Files:**
- Modify: `backend/main.py`（模型区；端点区）
- Test: `tests/test_main.py`（追加）

**Interfaces:**
- Consumes: `mail_client.list_mail`、`mail_client.fetch_bodies(items) -> dict[str, str]`（Task 3）、`llm_client.summarize_mail(...)`（Task 5）
- Produces: `POST /api/mail/summarize`，body `{llm_base_url, llm_api_key, llm_model, days?: int, language?: str}` → `{ok: true, mails: [...], groups: [...], warnings: [...]}`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_main.py`：

```python
def test_mail_summarize_joins_bodies_and_groups(monkeypatch):
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [
        {"id": "A", "subject": "作业", "sender": "李老師",
         "received": "2026-09-17T08:31:00Z", "is_read": False}])
    monkeypatch.setattr(mail_client, "fetch_bodies", lambda items: {"A": "交报告"})
    seen = {}

    def fake_summarize(base, key, model, mails, language="zh"):
        seen["mails"] = mails
        seen["language"] = language
        return {"groups": [], "warnings": ["w"]}

    monkeypatch.setattr(llm_client, "summarize_mail", fake_summarize)
    body = TestClient(main.app).post("/api/mail/summarize", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "days": 7, "language": "zh"}).json()
    assert body["ok"] is True
    assert body["warnings"] == ["w"]
    # index 是回指锚，必须从 0 连续编号
    assert [m["index"] for m in seen["mails"]] == [0]
    assert seen["mails"][0]["body"] == "交报告"
    assert "token" not in json.dumps(body)


def test_mail_summarize_indexes_are_contiguous(monkeypatch):
    """两封起验证：index 必须是 0,1 而不是固定值 —— 前端靠它回指原文。"""
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [
        {"id": "A", "subject": "甲", "sender": "", "received": "", "is_read": False},
        {"id": "B", "subject": "乙", "sender": "", "received": "", "is_read": True}])
    monkeypatch.setattr(mail_client, "fetch_bodies", lambda items: {"A": "a", "B": "b"})
    seen = {}
    monkeypatch.setattr(llm_client, "summarize_mail",
                        lambda b, k, m, mails, language="zh": seen.setdefault("m", mails) or
                        {"groups": [], "warnings": []})
    TestClient(main.app).post("/api/mail/summarize", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m"})
    assert [m["index"] for m in seen["m"]] == [0, 1]
    assert [m["body"] for m in seen["m"]] == ["a", "b"]


def test_mail_summarize_returns_mails_too(monkeypatch):
    """前端要把 AI 结果的 mail_index 对回原邮件 → 响应必须同时带上 mails。"""
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [
        {"id": "A", "subject": "作业", "sender": "", "received": "", "is_read": False}])
    monkeypatch.setattr(mail_client, "fetch_bodies", lambda items: {"A": "b"})
    monkeypatch.setattr(llm_client, "summarize_mail",
                        lambda *a, **k: {"groups": [], "warnings": []})
    body = TestClient(main.app).post("/api/mail/summarize", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m"}).json()
    assert body["mails"][0]["id"] == "A"
    assert body["mails"][0]["body"] == "b"


def test_mail_summarize_failure_is_200_json(monkeypatch):
    def boom(days):
        raise mail_client.MailError("邮箱会话已失效")
    monkeypatch.setattr(mail_client, "list_mail", boom)
    r = TestClient(main.app).post("/api/mail/summarize", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m"})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "邮箱会话已失效"}


def test_mail_summarize_no_mail_skips_llm(monkeypatch):
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [])
    monkeypatch.setattr(mail_client, "fetch_bodies", lambda items: {})
    called = []
    monkeypatch.setattr(llm_client, "summarize_mail",
                        lambda *a, **k: called.append(1) or {"groups": [], "warnings": []})
    body = TestClient(main.app).post("/api/mail/summarize", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m"}).json()
    assert body["ok"] is True
    assert body["mails"] == []
    assert called == []          # 没邮件就别浪费一次 LLM 调用
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_main.py -q -k mail_summarize
```

预期：404（端点不存在）→ 断言红。

- [ ] **Step 3: 加模型与端点**

`backend/main.py` 模型区，紧跟 `MailListRequest` 之后：

```python
class MailSummaryRequest(LLMConfig):
    days: int = 7
    language: str = "zh"
```

端点区末尾追加：

```python
@app.post("/api/mail/summarize")
def mail_summarize(req: MailSummaryRequest):
    """取信 + AI 两级归类/摘要/DDL。取信仍是纯读，不改已读状态。"""
    try:
        mails = mail_client.list_mail(req.days)
        if not mails:
            return {"ok": True, "mails": [], "groups": [], "warnings": []}
        bodies = mail_client.fetch_bodies(mails)
        # index 是前端把 AI 结果对回原文的锚，必须是连续整数，不能靠标题匹配
        for i, m in enumerate(mails):
            m["index"] = i
            m["body"] = bodies.get(m["id"], "")
        r = llm_client.summarize_mail(
            req.llm_base_url, req.llm_api_key, req.llm_model, mails,
            language=req.language)
        return {"ok": True, "mails": mails, **r}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_main.py -q
```

预期：全绿。

- [ ] **Step 5: 变异检验**

把 `if not mails: return ...` 早退删掉 → `test_mail_summarize_no_mail_skips_llm` 必须红。把 `enumerate(mails)` 的 `i` 换成固定 `0` → `test_mail_summarize_indexes_are_contiguous` 必须红。**改回原样**。

- [ ] **Step 6: 提交**

```bash
git add backend/main.py tests/test_main.py
git commit -m "feat(mail): /api/mail/summarize 端点（取信 + AI 分组）"
```

---

## Task 7: 前端邮件页骨架 + i18n

本任务只做「能打开、能列出邮件」。AI 分组渲染与写入留到 Task 8。

**Files:**
- Modify: `frontend/index.html`（侧栏 ~39；`#page-mail`）
- Modify: `frontend/i18n.js`
- Modify: `frontend/shell.js`（5-20）
- Modify: `frontend/app.js`（追加）
- Modify: `frontend/app.css`（追加）

**Interfaces:**
- Consumes: `POST /api/mail/list`（Task 4）
- Produces:
  - `initMailTab()`、`loadMail()`、`renderMail()`、`fmtLocal(iso)`
  - `mailState = {mails: [], groups: null, loading: false, error: ""}`
  - `#page-mail`、`#btnMailRefresh`、`#btnMailSummary`、`#mailMeta`、`#mailList`

- [ ] **Step 1: 先跑基线门禁**

```bash
.venv/bin/python -m pytest -q            # 期望全绿
node tools/check_i18n_keys.mjs           # 记录基线键数与死键数
node tools/check_dom_ids.mjs             # 记录 id 基线
```

把三个数字记下来，收尾要对得上。

- [ ] **Step 2: 查重名**

```bash
grep -n "mailState\|initMailTab\|loadMail\|renderMail\|fmtLocal" frontend/i18n.js frontend/util.js frontend/shell.js
```

预期：无输出。**有输出就先改名再继续**——顶层重名是 `SyntaxError`，整页白屏。

- [ ] **Step 3: 加 i18n key**

`frontend/i18n.js` 的 `zh` 与 `en` 两个对象里**各加同样的 17 个 key**（位置放在 `nav.library` 之后、`nav.settings` 之前，与侧栏顺序一致）：

```js
    "nav.mail": "邮件",
    "mail.refresh": "刷新邮件",
    "mail.summarize": "AI 总结",
    "mail.empty": "没有需要处理的邮件",
    "mail.count": "共 {n} 封",
    "mail.unread": "未读",
    "mail.group_other": "其他",
    "mail.uncategorized": "未分类",
    "mail.no_body": "（无正文）",
    "mail.ddl": "截止 / 待办",
    "mail.write_event": "写入日历",
    "mail.write_reminder": "写入提醒",
    "mail.ai_failed": "AI 总结失败：",
    "mail.need_refresh": "请先刷新邮件",
    "mail.wrote": "已写入 {n} 条",
    "confirm.cancel": "取消",
    "confirm.ok": "确认",
```

`en` 侧对应：

```js
    "nav.mail": "Mail",
    "mail.refresh": "Refresh mail",
    "mail.summarize": "AI summary",
    "mail.empty": "Nothing needs your attention",
    "mail.count": "{n} messages",
    "mail.unread": "Unread",
    "mail.group_other": "Other",
    "mail.uncategorized": "Uncategorized",
    "mail.no_body": "(no body)",
    "mail.ddl": "Deadlines / to-dos",
    "mail.write_event": "Add to calendar",
    "mail.write_reminder": "Add reminder",
    "mail.ai_failed": "AI summary failed: ",
    "mail.need_refresh": "Refresh mail first",
    "mail.wrote": "Wrote {n}",
    "confirm.cancel": "Cancel",
    "confirm.ok": "Confirm",
```

- [ ] **Step 4: 加侧栏项与页面容器**

`frontend/index.html:39` 之后（`library` 与分隔线之间）插入：

```html
      <button class="nav-item" data-page="mail"><span data-i18n="nav.mail"></span></button>
```

在 `#page-library` 那个 `</div>` 之后、`#page-settings` 之前插入：

```html
    <div id="page-mail" class="page" hidden>
      <div class="card">
        <div class="mail-toolbar">
          <button class="btn btn-primary" id="btnMailRefresh" data-i18n="mail.refresh"></button>
          <button class="btn" id="btnMailSummary" data-i18n="mail.summarize"></button>
          <span class="mail-meta" id="mailMeta"></span>
        </div>
      </div>
      <div id="mailList"></div>
    </div>
```

- [ ] **Step 5: 注册页面**

`frontend/shell.js:5`：

```js
const PAGES = ["home","announce","discuss","schedule","todo","files","grades","courses","library","mail","settings"];
```

`frontend/shell.js:19` 之后（`library` 那行之后）：

```js
  mail:     () => initMailTab(),
```

- [ ] **Step 6: 加前端逻辑**

下文用到的既有工具（已核对存在，直接用，不要另起）：`esc()`（`frontend/util.js:63`）、
`settings()`（`frontend/util.js:12`）、`LANG()` 与 `t()`（`frontend/i18n.js:762-763`）、
`api()` / `withBusy()` / `setStatus()`（`frontend/util.js`）。

追加到 `frontend/app.js` 末尾：

```js
/* ---------- 学校邮箱 ---------- */
let mailState = { mails: [], groups: null, loading: false, error: "" };

function initMailTab(){
  if(mailState.mails.length || mailState.loading){ renderMail(); return; }
  // 首次进页没有遮罩，失败了直接发问
  loadMail().then(ok => { if(!ok) offerManualLogin(mailState.error); });
}

/* 返回布尔：是否成功。**不在这里发问** —— 点刷新时外面套着 withBusy，
   而 #overlay 的 z-index(50) 高于弹层(40)，在遮罩里弹会被盖住。
   发问一律由调用方在 withBusy 之外做（同 memory 里 AIMS 那条坑）。 */
async function loadMail(){
  if(mailState.loading) return false;
  mailState.loading = true; mailState.error = ""; mailState.groups = null;
  renderMail();
  const r = await api("mail/list", { days: 7 });
  mailState.loading = false;
  if(r.ok !== true){
    mailState.error = r.error || "";
    renderMail();
    return false;          // 多半是 Okta 会话失效 —— 与 AIMS 同一个会话
  }
  mailState.mails = r.mails || [];
  renderMail();
  return true;
}

/* 刷新的唯一出口：遮罩内干活，遮罩外发问（spec §9「登录失败即停，绝不自动重试」） */
async function refreshMail(){
  let err = "";
  await withBusy(t("mail.refresh"), $("btnMailRefresh"), async () => {
    if(!await loadMail()) err = mailState.error;
  });
  if(err) offerManualLogin(err);
}

function renderMail(){
  const box = $("mailList");
  if(!box) return;
  const meta = $("mailMeta");
  if(meta){
    meta.textContent = (mailState.loading || !mailState.mails.length) ? ""
      : t("mail.count", { n: mailState.mails.length });
  }
  if(mailState.error){
    box.innerHTML = `<div class="card mail-error">${esc(mailState.error)}</div>`;
    return;
  }
  if(mailState.loading){
    box.innerHTML = `<div class="card mail-empty">…</div>`;
    return;
  }
  if(!mailState.mails.length){
    box.innerHTML = `<div class="card mail-empty">${esc(t("mail.empty"))}</div>`;
    return;
  }
  box.innerHTML = mailState.mails.map(mailRowHtml).join("");
}

function mailRowHtml(m){
  const flag = m.is_read ? ""
    : `<span class="mail-badge">${esc(t("mail.unread"))}</span>`;
  return `<div class="card mail-row">
    <div class="mail-row-head">${flag}<span class="mail-subject">${esc(m.subject || t("mail.no_body"))}</span></div>
    <div class="mail-row-sub">${esc(m.sender || "")} · ${esc(fmtLocal(m.received))}</div>
  </div>`;
}

/* ISO8601(UTC) → 本地可读串；解析不了就原样返回，绝不显示 Invalid Date */
function fmtLocal(iso){
  if(!iso) return "";
  const d = new Date(iso);
  if(isNaN(d.getTime())) return iso;
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

$("btnMailRefresh").onclick = () => refreshMail();
```

**注意**：`$("btnMailRefresh").onclick` 是顶层语句，`app.js` 在 `shell.js` 之前加载但 `index.html` 的 DOM 早已就绪（`app.js` 在 `</body>` 前），与现有 `$("btnWriteReminders").onclick` 写法一致，可直接用。

`offerManualLogin(autoError)` 是 `frontend/app.js:158` 已有的函数，作用就是弹 `#loginPrompt` 问「是否手动登录」，确认后调 `/api/banweb/open_login`。邮箱与 AIMS 共用同一个 Okta 会话，所以复用它正是 spec §9 要求的做法，**不要另写一套**。

- [ ] **Step 7: 加样式**

追加到 `frontend/app.css` 末尾：

```css
/* ---------- 学校邮箱 ---------- */
.mail-toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.mail-meta { color: var(--muted, #888); font-size: 13px; }
.mail-row { padding: 10px 14px; }
.mail-row-head { display: flex; align-items: center; gap: 8px; }
.mail-subject { font-weight: 600; }
.mail-row-sub { color: var(--muted, #888); font-size: 12px; margin-top: 4px; }
.mail-badge { background: #d33; color: #fff; border-radius: 8px;
              font-size: 11px; padding: 1px 6px; }
.mail-empty, .mail-error { padding: 14px; color: var(--muted, #888); }
.mail-error { color: var(--err, #c33); }
```

- [ ] **Step 8: 跑门禁**

```bash
node tools/check_i18n_keys.mjs
node tools/check_dom_ids.mjs
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/four.js && node --check /tmp/four.js
.venv/bin/python -m pytest -q
```

预期：i18n 两侧各 **+17**、死键**仍为 21**；id **+4**（`page-mail`、`btnMailRefresh`、`btnMailSummary`、`mailMeta`、`mailList` 共 5 个 → 以工具输出为准）；`node --check` 静默通过；pytest 全绿。

- [ ] **Step 9: 手工验证**

```bash
.venv/bin/python -m uvicorn backend.main:app --port 8331
```

打开页面 → 点侧栏「邮件」→ 应看到邮件列表（若 AIMS 未登录则显示「邮箱会话已失效…」错误行，这也是正确行为）。**验证过程中不要把任何邮件主题/正文/发件人贴进记录。**

- [ ] **Step 10: 提交**

```bash
git add frontend/index.html frontend/i18n.js frontend/shell.js frontend/app.js frontend/app.css
git commit -m "feat(mail): 前端邮件页骨架——侧栏项、列表渲染、i18n"
```

---

## Task 8: 两级分组渲染 + 逐条写入

**Files:**
- Modify: `frontend/index.html`（加 `#confirmPrompt`）
- Modify: `frontend/app.js`（追加 + 改 `renderMail` 一行）
- Modify: `frontend/app.css`（追加）

**Interfaces:**
- Consumes: `POST /api/mail/summarize`（Task 6）→ `{mails, groups, warnings}`；`POST /api/add_calendar_event`（`{calendar_name,title,start,end,location,notes}`）；`POST /api/add_reminder`（`{list_name,title,due_date,notes}`）
- Produces: `mailSummarize()`、`mailRenderGroups()`、`mailItemHtml()`、`askConfirm(title, body) -> Promise<boolean>`

- [ ] **Step 1: 查重名**

```bash
grep -n "askConfirm\|mailSummarize\|mailRenderGroups\|mailItemHtml\|confirmResolve" frontend/*.js
```

预期：无输出。**有输出就先改名再继续。**

本任务用到的既有 id 与 key 已核对存在，直接用：日历下拉 `#selCalendar`、提醒下拉
`#selList`（`frontend/index.html:301-302`）；`status.need_calendar` / `status.need_list` /
`status.write_fail`（`frontend/i18n.js:188/193/192`，en 侧 567/572/571）。

- [ ] **Step 2: 加确认弹层**

`frontend/index.html` 的 `#loginPrompt` 那个收尾 `</div>` 之后插入：

```html
<div id="confirmPrompt" class="modal" role="dialog" aria-modal="true" aria-labelledby="confirmPromptTitle" hidden>
  <div class="modal-backdrop"></div>
  <div class="modal-card modal-sm">
    <div class="modal-head">
      <h3 id="confirmPromptTitle"></h3>
      <button id="btnConfirmClose" class="modal-close" data-i18n-aria="detail.close">✕</button>
    </div>
    <p class="confirm-text" id="confirmPromptBody"></p>
    <div class="modal-actions">
      <button id="btnConfirmNo" class="btn btn-ghost" data-i18n="confirm.cancel"></button>
      <button id="btnConfirmYes" class="btn btn-primary" data-i18n="confirm.ok"></button>
    </div>
  </div>
</div>
```

- [ ] **Step 3: 加确认与分组逻辑**

追加到 `frontend/app.js` 末尾：

```js
/* 通用确认弹层。并发的询问共用同一个 promise，不叠弹窗
   （范式同 confirmManualLogin）。 */
let confirmResolve = null;
function askConfirm(titleText, bodyText){
  const m = $("confirmPrompt");
  if(!m) return Promise.resolve(false);   // 弹层缺失 → 一律当取消，绝不擅自写入
  if(confirmResolve) return confirmResolve;
  confirmResolve = new Promise(resolve => {
    const done = yes => { confirmResolve = null; m.hidden = true; resolve(yes); };
    $("confirmPromptTitle").textContent = titleText;
    $("confirmPromptBody").textContent = bodyText;
    m.querySelector(".modal-backdrop").onclick = () => done(false);
    $("btnConfirmClose").onclick = () => done(false);
    $("btnConfirmNo").onclick    = () => done(false);
    $("btnConfirmYes").onclick   = () => done(true);
    m.hidden = false;
    $("btnConfirmYes").focus();
  });
  return confirmResolve;
}

async function mailSummarize(){
  if(!mailState.mails.length){ setStatus(t("mail.need_refresh"), "err"); return; }
  const s = settings();
  await withBusy(t("mail.summarize"), $("btnMailSummary"), async () => {
    const r = await api("mail/summarize", {
      llm_base_url: s.llm_base_url, llm_api_key: s.llm_api_key, llm_model: s.llm_model,
      days: 7, language: LANG() });
    if(r.ok !== true){ setStatus(t("mail.ai_failed") + (r.error || ""), "err"); return; }
    mailState.mails = r.mails || mailState.mails;
    mailState.groups = r.groups || [];
    if((r.warnings || []).length) setStatus(r.warnings.join("；"), "info", 8000);
    renderMail();
  });
}

function mailRenderGroups(){
  const gs = mailState.groups || [];
  if(!gs.length) return mailState.mails.map(mailRowHtml).join("");
  let html = "";
  gs.forEach((g, gi) => {
    html += `<div class="card mail-group"><h3>${esc(g.course || t("mail.group_other"))}</h3>`;
    (g.types || []).forEach((ty, ti) => {
      html += `<div class="mail-type"><h4>${esc(ty.type || t("mail.uncategorized"))}</h4>`;
      (ty.items || []).forEach((it, ii) => {
        const m = mailState.mails[it.mail_index];
        if(!m) return;                       // 越界（后端已过滤，前端再兜一层）
        html += mailItemHtml(it, m, gi, ti, ii);
      });
      html += `</div>`;
    });
    html += `</div>`;
  });
  return html;
}

function mailItemHtml(it, m, gi, ti, ii){
  const evs = (it.calendar_events || []).map((e, ei) =>
    `<div class="mail-ddl"><span>${esc(e.title || "")} · ${esc(e.start || "")}</span>
       <button class="btn btn-sm" data-w="ev" data-g="${gi}" data-t="${ti}" data-i="${ii}" data-e="${ei}"
         >${esc(t("mail.write_event"))}</button></div>`).join("");
  const rms = (it.reminders || []).map((e, ei) =>
    `<div class="mail-ddl"><span>${esc(e.title || "")} · ${esc(e.due_date || "")}</span>
       <button class="btn btn-sm" data-w="rm" data-g="${gi}" data-t="${ti}" data-i="${ii}" data-e="${ei}"
         >${esc(t("mail.write_reminder"))}</button></div>`).join("");
  const ddl = (evs || rms)
    ? `<div class="mail-ddls"><div class="mail-ddl-label">${esc(t("mail.ddl"))}</div>${evs}${rms}</div>`
    : "";
  return `<div class="mail-item">
    <div class="mail-item-subject">${esc(m.subject || t("mail.no_body"))}</div>
    <div class="mail-item-sum">${esc(it.summary || "")}</div>${ddl}</div>`;
}
```

- [ ] **Step 4: 接上渲染与写入**

把 `renderMail()` 里的

```js
  box.innerHTML = mailState.mails.map(mailRowHtml).join("");
```

替换为：

```js
  box.innerHTML = mailRenderGroups();
```

在 `$("btnMailRefresh").onclick` 那行之后追加：

```js
$("btnMailSummary").onclick = () => mailSummarize();

/* 逐条写入：每条都先问一次，确认才写。
   邮件里的日期比公告更容易被 AI 提取错，绝不自动批量写。 */
$("mailList").addEventListener("click", async e => {
  const b = e.target.closest("button[data-w]");
  if(!b) return;
  const g  = (mailState.groups || [])[Number(b.dataset.g)];
  const ty = g && (g.types || [])[Number(b.dataset.t)];
  const it = ty && (ty.items || [])[Number(b.dataset.i)];
  if(!it) return;
  const isEv = b.dataset.w === "ev";
  const arr = isEv ? (it.calendar_events || []) : (it.reminders || []);
  const row = arr[Number(b.dataset.e)];
  if(!row) return;
  const label = isEv ? `${row.title || ""} · ${row.start || ""}`
                     : `${row.title || ""} · ${row.due_date || ""}`;
  if(!await askConfirm(t(isEv ? "mail.write_event" : "mail.write_reminder"), label)) return;
  const cal  = $("selCalendar") ? $("selCalendar").value : "";
  const list = $("selList")     ? $("selList").value     : "";
  if(isEv && !cal){ setStatus(t("status.need_calendar"), "err"); return; }
  if(!isEv && !list){ setStatus(t("status.need_list"), "err"); return; }
  const r = isEv
    ? await api("add_calendar_event", { calendar_name: cal, title: row.title,
        start: row.start, end: row.end || row.start, location: row.location || "",
        notes: row.notes || "" })
    : await api("add_reminder", { list_name: list, title: row.title,
        due_date: row.due_date, notes: row.notes || "" });
  if(r.ok === true){ setStatus(t("mail.wrote", { n: 1 }), "ok"); b.disabled = true; }
  else setStatus(t("status.write_fail") + (r.error || ""), "err");
});
```

- [ ] **Step 5: 加样式**

追加到 `frontend/app.css` 末尾：

```css
.mail-group h3 { margin: 0 0 8px; }
.mail-type h4 { margin: 10px 0 6px; color: var(--muted, #888); font-size: 13px; }
.mail-item { border-top: 1px solid var(--line, #eee); padding: 8px 0; }
.mail-item-subject { font-weight: 600; }
.mail-item-sum { font-size: 13px; margin-top: 3px; }
.mail-ddls { margin-top: 6px; }
.mail-ddl-label { font-size: 12px; color: var(--muted, #888); }
.mail-ddl { display: flex; align-items: center; justify-content: space-between;
            gap: 10px; padding: 3px 0; font-size: 13px; }
.btn-sm { padding: 2px 8px; font-size: 12px; }
```

- [ ] **Step 6: 跑门禁**

```bash
node tools/check_i18n_keys.mjs
node tools/check_dom_ids.mjs
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/four.js && node --check /tmp/four.js
.venv/bin/python -m pytest -q
```

预期：i18n 若在 Step 1 补了 `status.need_calendar` 则再 +2（zh/en），否则不变；死键仍 21；id +5（`confirmPrompt` 系列）以工具输出为准；`node --check` 通过；pytest 全绿。

- [ ] **Step 7: 手工验证**

启动服务 → 邮件页 → 点「AI 总结」→ 应出现两级分组；点某条 DDL 的「写入」→ 弹确认 → 确认后状态栏显示「已写入 1 条」，按钮置灰。**验证时不要把邮件正文或摘要贴进记录。**

- [ ] **Step 8: 提交**

```bash
git add frontend/index.html frontend/app.js frontend/app.css frontend/i18n.js
git commit -m "feat(mail): 课程×类型两级分组渲染 + 逐条确认写入日历/提醒"
```

---

## 收尾检查

- [ ] **全量门禁**

```bash
.venv/bin/python -m pytest -q
node tools/check_i18n_keys.mjs
node tools/check_dom_ids.mjs
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/four.js && node --check /tmp/four.js
```

- [ ] **逐条核对 Global Constraints 里的硬约束**

```bash
grep -rn "MarkAsRead" backend/                            # 必须无输出
grep -rn "token" backend/main.py                          # 返回值里绝不能出现 token 字段
grep -rn "logout\|clear_cookies" backend/mail_client.py   # 必须无输出（不动 DT cookie）
grep -rn "setInterval\|setTimeout" backend/mail_client.py # 必须无输出（不后台轮询）
```

- [ ] **更新记忆**

`memory/aims-banweb-integration.md` 的「学校邮箱」小节需改写：把「可行的是 DOM 路线」改为「DOM 路线已被实测否掉（React portal 拦截 pointer events），改走 `service.svc` REST + Playwright 截获 bearer；`FindItem`/`GetItem` 是纯读的，`IsRead` 读完仍为 `False`，故『读完还原未读』不需要实现」。并补一条 `_find_page` 排除邮件域的原因（共用 persistent context，认领邮件 tab 会让 AIMS 把它导航走）。

- [ ] **提交收尾**

```bash
git add memory/aims-banweb-integration.md memory/MEMORY.md
git commit -m "docs(memory): 学校邮箱取信改为 REST 路线，DOM 路线已否"
```
