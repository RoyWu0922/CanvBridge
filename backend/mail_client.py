"""学校邮箱取信层（Gmail IMAP + 应用专用密码）。

分两层：
  - 纯函数层（本文件上半）：头部解码、日期解析、MIME 正文抽取、范围筛选。
    全部可用合成 fixture 单测，不触网、不开浏览器。
  - I/O 层（本文件下半）：连接、只读取件、登出。

**为什么是 IMAP，而不是继续截获 OWA 的 bearer token**：浏览器那条路把取信绑在
一个活着的 AIMS/Okta 会话上 —— 会话一过期就取不到信，而邮件本可以独立于 AIMS
存在。学校邮箱转发到 Gmail 后，用应用专用密码走 IMAP：取信链路里**不再有浏览器**，
没有 tab、没有 token 截获、没有看门狗恢复，也**不**使用 `_on_browser_thread`
（它不可重入）与 `_retry_once`。旧 OWA 路线的实测记录（行点击被 React portal
浮层拦截、`FindItem` / `GetItem` 是纯读的因而 `IsRead` 保持 `False`）见
docs/superpowers/specs/2026-09-18-school-mail-design.md §3。

**只读三件套**（spec §5.2，本设计的要害）：
  1. `select(MAILBOX, readonly=True)` —— 发的是 EXAMINE，服务端层面禁止改动；
  2. 所有取件一律 `BODY.PEEK[...]` —— `BODY[...]` / `RFC822` 会置 `\\Seen`；
  3. 永不发 `STORE` / `COPY` / `MOVE` / `EXPUNGE`。
用户在 Gmail 里的未读状态必须原样保留 —— 我们只是看一眼，不是替他处理邮件。

凭据只从钥匙串读（`credentials.get_gmail`），绝不落盘、绝不回传前端、绝不出现在
日志或报告里。认证失败即停、绝不重试（spec §5.8）；没有后台轮询 —— 只在用户点
刷新时连一次，连完即断。
"""
from __future__ import annotations

import imaplib
import re
import time
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from backend.canvas_client import strip_html

from . import credentials

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


class MailError(Exception):
    """取信层失败。文案直接面向用户，不含凭据等敏感内容。"""


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
    的时刻），有可退的日期时绝不返回空串 —— 空串会让前端日期栏**静默留白**、
    并让提示词里出现空的时间字段（`fmtLocal` 的 `!iso` 分支直接返回空串，根本走不到
    `new Date()`；`llm_client` 那边是 f-string 直插），那是静默的坏值。
    两个都没有才给空串 —— **这只影响显示**：保留与否不看这个键，
    `select_scope` 比的是 `_internaldate`（INTERNALDATE），见裁定 D。
    （修复轮 1 更正：原句写「此时 select_scope 会 fail-open 保留它」，把保留
    归因到了本函数的产物上；结论碰巧一致（该情形下 `_internaldate` 也是 None，
    同样 fail-open），但因果是错的，照着它接线会错。
    修复轮 3 更正：原句写「空串会让前端 new Date("") 得到 Invalid Date，那是静默
    的坏值」—— 机制是假的且方向相反：前端根本到不了 `new Date()`，真实后果是
    静默留白，不是「会很吵」。一句错的因果会让人省掉本该有的守卫。）
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


# ---------------------------------------------------------------------------
# I/O 层
# ---------------------------------------------------------------------------

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
