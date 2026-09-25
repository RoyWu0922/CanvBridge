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

**只显示转发件**（spec §2）：`list_mail` 的返回集合收窄为「窗口内的转发件」——
判据 `recipient_is_school`（收件人 To/Cc 命中学校域名），在服务端即可判定，
不需要 Gmail 标签、不需要新建过滤器。键集合与类型与旧版**完全一致**，只是集合变小。

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
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path

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
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024   # 单附件上限（spec §4；保守取值，非实测）
#: 超限文案里的「20 MB」由它生成 —— 改限额时文案跟着改，两者不会对不上。
MAX_ATTACHMENT_MB = MAX_ATTACHMENT_BYTES // (1024 * 1024)

#: 判据用的学校域名（spec §2.1）。只此一处定义 —— 实现与用例都从它取。
SCHOOL_DOMAIN = "cityu.edu.hk"

#: 对外契约的键集合（spec §3.5）。`_do_list` 返回前**按它放行**。
#: 白名单而不是黑名单：黑名单在新增内部键时静默泄漏，而泄漏的正是「不在契约里」的东西。
#: `sender_addr` 与 `sender` 并列：后者显示名优先、通常**不含**地址，规则匹配要的是地址。
_CONTRACT_KEYS = ("id", "subject", "sender", "sender_addr", "received")

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
    """把一封邮件的头组装成对外契约的条目（键集合：id/subject/sender/sender_addr/received）。

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
        # parseaddr 解出的地址部分（小写、首尾去空白），供规则匹配用；不是显示名。
        # **不保证是地址、不保证含 @、也不保证非空** —— 解不出地址时它会落回
        # parseaddr 内部的其它分支，那些分支的形状**别去猜**：本注释改过三版，
        # 被证伪的机制猜测：「恒为地址」「退回原文小写」「退回首个空白分隔词」。
        # 实测（生产路径 `message_from_bytes`，见 tests 里的锚用例）：
        #   `Li Wei <Li.Wei@CityU.EDU.HK>` → "li.wei@cityu.edu.hk"   `Li Wei` → "li"
        #   `"Li Wei"` → "li wei"   `Li:Wei` → "wei"   `a<b>` → "b"
        #   `, Li Wei` → ""   `Li <>` → ""   缺 From 头 → ""
        # 所以下游**只能按子串匹配**（可能是非地址值，也可能是空串），
        # 不得假定 `"@" in sender_addr`，也不得据此做域名后缀提取 —— `_addr_domain`
        # 对上面那些非地址值一律静默返回空串，建在它上面的规则会**永不命中**。
        # 另：裸 8-bit 的 From 头会让 `msg.get("From")` 返回 Header 对象，`parseaddr`
        # 对它抛 TypeError（在 `sender_name` 就先抛），走不到这里 ⇒ 本键在生产路径上
        # **不会**是非 ASCII 串。该崩溃是本键之外的既有问题（ledger 裁定 12），不在本任务范围。
        # `parseaddr` 不是新依赖：`sender_name` 内部已在用，`_addr_domain` 是同族先例 ——
        # 这是把「已经解出来又被丢掉」的东西留下。
        "sender_addr": parseaddr(msg.get("From"))[1].strip().lower(),
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


#: 排序哨兵：`_internaldate` 不可用（缺失 / `None` / naive / 非 datetime）时当「最旧」。
#: 方向与 `select_scope` 的 fail-open 一致 —— 宁可排得不准，也不丢件、也不抛穿整次抓取。
_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _sort_key(it: dict) -> datetime:
    ts = it.get("_internaldate")
    if isinstance(ts, datetime) and ts.tzinfo is not None:
        return ts
    return _OLDEST


def sort_newest_first(items: list[dict]) -> list[dict]:
    """按**到达时刻**（内部键 `_internaldate`）降序 —— 新→旧（spec §4.2）。

    排序键与 `select_scope` 的窗口键**是同一把**（INTERNALDATE）。若一个用
    `_internaldate` 筛、另一个用 `received` 排，就会出现「筛出来的范围与看到的顺序
    对不上」的**同名改义残留**：`received` 是 `Date:` 头，即**原始发信时间**，
    转发件上它可能很旧，按它排会把刚收到的旧邮件排到最底下。

    不可用的值按 `_OLDEST` 处理 ⇒ 排在最后；`sorted` 稳定 ⇒ 同键条目保持输入相对
    顺序。**返回新列表，不改动入参。**
    """
    return sorted(items, key=_sort_key, reverse=True)


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

    顺带取 TO CC：转发件判据要收件人（spec §2.1），**不取就没有判据** ——
    同一趟 FETCH 里多两个词，零额外往返。
    """
    typ, data = conn.uid(
        "FETCH", b",".join(uids),
        "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE TO CC)] INTERNALDATE)")
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
        # 两个内部键，对外契约里都没有它们，`_do_list` 返回前按白名单剥掉（spec §3.5）。
        # `_school` 加在这里而不是 `header_item` 里：`header_item` 的契约恰好五个键，
        # 有两条用例逐字钉着（见计划头部「裁定 1」）。
        item["_internaldate"] = internaldate
        item["_school"] = recipient_is_school(msg)
        out.append(item)
    return out


def _do_list(days: int) -> list[dict]:
    """取窗口内的**转发件**。返回的只是转发件（spec §2.1 判据），键集合不变。"""

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
        for n, chunk in enumerate(_chunks(uids, HEADER_BATCH)):
            if n:
                time.sleep(_THROTTLE)      # 批次之间留间隔（全局约束）
            items.extend(_fetch_headers(conn, chunk))
        kept = [it for it in select_scope(items, datetime.now(timezone.utc), days)
                if it.get("_school") is True]
        kept = sort_newest_first(kept)
        # 只放行对外契约的五个键（白名单）。`_internaldate` 与 `_school` 都是内部键。
        return [{k: it[k] for k in _CONTRACT_KEYS} for it in kept]
    finally:
        _logout_quietly(conn)


def list_mail(days: int = DEFAULT_DAYS) -> list[dict]:
    """拉取「最近 days 天」的邮件（不含正文）。全程只读，不改已读状态。

    **不**走 banweb 的浏览器线程，也**不**重试：认证失败即停（spec §5.8）。
    """
    return _do_list(days)


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


def _do_bodies(items: list[dict]) -> dict:
    conn, uidvalidity = _connect()
    try:
        _check_uidvalidity(uidvalidity)
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


def fetch_attachment(item: dict, index: int) -> tuple[str, bytes, str]:
    """取第 `index` 个附件，返回 `(净化后的文件名, 原始字节, 该邮件的 received)`。**BODY.PEEK[]**。

    `index` 是 `fetch_detail` 报出的那个（**附件序**，不是 part 序）；越界、
    不是附件、超过 `MAX_ATTACHMENT_BYTES` 都抛 `MailError`。
    **不缓存**：每次下载重连一次、重解一次整封（spec §4）——
    代价是 N 个附件 N 次连接，换来的是进程里不驻留邮件字节。

    第三个元素是**这封邮件自己**的 `received`（`Date:` 头的 UTC ISO，解不出是 `""`），
    供 `save_attachment` 定目录用（spec §5「该邮件 `received`」）。它**不是**调用方
    传进来的：`item` 只是个 `{"id": ...}` 句柄（三个函数都只读 `item["id"]`，各自
    重新解析），路由层拿不到也不该编一个日期。本次解析已经在上面发生，顺手带出去，
    免得为了一个日期再连一次 IMAP。
    与 `header_item` 的差别：这里**没有 INTERNALDATE 可退**（`_fetch_raw_chunk` 只回
    `(uid, bytes)`），`Date:` 头解不出的邮件就是 `""` ⇒ 落盘走「当天 UTC」回退。
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
    received = _iso(received_dt(msg))
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
    return name, data, received


#: 落盘子目录名。**固定字面量、不做 i18n** —— 它同时是磁盘路径，
#: 切界面语言不该把用户的目录挪走（spec §5）。
SCHOOL_MAIL_FOLDER = "学校邮件"


def _received_date(received: str) -> str:
    """落盘目录用的日期 = `fetch_attachment` 交回的 `received` 的 **UTC** 日期；
    解不出（`""` / 坏串 / naive）用当天 UTC（spec §5）。

    入参是**那封邮件自己的** `received` 字符串（`_iso` 的产物，见 `fetch_attachment`），
    **不是**请求里的字段 —— `item` 只是 `{"id": ...}` 句柄，客户端给不出可信日期。

    用 `fromisoformat` 而不是切前 10 个字符：切串会**默默假定** `received` 是 UTC，
    而 `_iso()` 一旦换了写法（比如带偏移）切出来的就不是日期了。
    这里显式要求带时区：naive 的一律当「解不出」。
    """
    try:
        dt = datetime.fromisoformat(received or "")
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
    日期取**这封邮件自己**的 `received`（`fetch_attachment` 交回的那个，spec §5）——
    绝不从 `item` 或请求里读：`item` 只是 `{"id": ...}` 句柄，路由层没有可信日期。
    落盘前最后一道是**锚点**：`resolve()` 后必须仍在 `resolve()` 后的 `download_dir` 之内
    （照抄 `files_downloader.confine_dest` 的纪律，spec §5）—— 用 `relative_to` 而不是
    前缀比较，前缀比较会把 `/a/bc` 判成在 `/a/b` 之内。
    """
    if not (download_dir or "").strip():
        raise MailError("没有设置下载目录，无法保存附件")
    root = Path(download_dir).expanduser()
    name, data, received = fetch_attachment(item, index)
    folder = root / SCHOOL_MAIL_FOLDER
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MailError(f"无法创建下载目录 {folder}：{exc}") from exc
    target = _unique_path(folder / f"{_received_date(received)}_{name}")
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        raise MailError("附件名落到了下载目录之外，已停止保存") from None
    try:
        target.write_bytes(data)
    except OSError as exc:
        raise MailError(f"保存附件失败：{exc}") from exc
    return str(target)


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
