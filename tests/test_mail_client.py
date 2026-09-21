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
                 untagged=None, status_reply=("OK", [b'"INBOX" (UIDVALIDITY 12345)'])):
        self.uidvalidity = uidvalidity
        self.headers = headers or {}     # {uid_bytes: raw_header_bytes}
        self.login_err = login_err
        self.status_reply = status_reply
        self.sent = []
        if untagged is not None:
            self.untagged_responses = untagged
        elif uidvalidity:
            self.untagged_responses = {"UIDVALIDITY": [uidvalidity]}
        else:
            self.untagged_responses = {}

    # --- 被 mail_client 调到的五个方法 ---
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

    def status(self, mailbox, what):
        self.sent.append(("status", mailbox, what))
        return self.status_reply

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
    # 正例锚：两条否定式断言在「一条命令都没发」时同时恒真 ⇒ 先证明这次运行真
    # 走到了「可能发 STORE/MOVE」的那段路（SEARCH + FETCH 都发过）。
    assert any(c[0] == "uid" and c[1] == "SEARCH" for c in fake.sent), \
        "连 SEARCH 都没发，下面的否定式断言是空转的"
    assert any(c[0] == "uid" and c[1] == "FETCH" for c in fake.sent), \
        "连 FETCH 都没发，下面的否定式断言是空转的"
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

    空串会让前端日期栏**静默留白**、并让提示词里出现空的时间字段。
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

    这里刻意**不**冻结 `mail_client.datetime`：`select_scope` 的类型守卫
    （`isinstance(ts, datetime)`）读的正是这个模块级名字，冻结成子类后真实例
    对它 `isinstance` 为 False ⇒ 窗口在测试里静默 fail-open 死掉，而方向是「多给」
    所以不报错。要看窗口就把 `now` 显式传进 `select_scope`。
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
    # 正例锚：只钉返回值与「没发 FETCH」，在 `_do_list` 于 SEARCH 之前就
    # `return []` 时同样绿。必须证明它确实发过 SEARCH —— 即站到了 FETCH
    # 本该出现的位置的前一步。
    assert any(c[0] == "uid" and c[1] == "SEARCH" for c in fake.sent), \
        "连 SEARCH 都没发，'不发 FETCH' 是空转的"
    assert not [c for c in fake.sent if c[0] == "uid" and c[1] == "FETCH"]


_FIXED_NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
"""冻结的「现在」：取 UTC 正午 —— 离 UTC 午夜最远；且 HKT（UTC+8）= 当日 20:00，
   两种时区下同一天，所以期望值不受「代码按哪个时区格式化」影响。"""


class _FrozenDateTime(datetime):
    """只冻结 now()，strptime / 算术 / 比较全部继承 —— 不碰 datetime 模块本身。"""

    @classmethod
    def now(cls, tz=None):
        return _FIXED_NOW if tz is None else _FIXED_NOW.astimezone(tz)


def test_list_mail_search_since_is_coarsened_by_one_day(monkeypatch):
    """SINCE 放宽一天：它按日粒度且按服务端时区（spec §5.3）。

    期望值是**字面量**，不是拿 `now` 现算的 —— 现算就又是自证。
    原先那句 `since.date() == (datetime.now(timezone.utc) - timedelta(days=8)).date()` 是真偶红：
    左边由 list_mail **调用期间**的时钟决定、右边由**断言执行时**的时钟决定，
    两者差着整个 list_mail 的耗时 ⇒ 恰好跨 UTC 午夜就差一天。
    冻住时钟后偶红与偶漏一起消失；`03` 还顺手钉住 `%d` 的零填充（`%-d` 会给 `3`），
    `Sep` 钉住月份缩写。
    """
    fake = _install(monkeypatch, _FakeIMAP(headers={}))
    monkeypatch.setattr(mail_client, "datetime", _FrozenDateTime)
    mail_client.list_mail(7)
    search = next(c for c in fake.sent if c[0] == "uid" and c[1] == "SEARCH")
    assert search[-1] == "03-Sep-2026"


def test_list_mail_connects_once(monkeypatch):
    """一次 list_mail 只连一次。"""
    fake = _install(monkeypatch, _FakeIMAP(headers={b"1": _hdr()}))
    mail_client.list_mail(7)
    assert len([c for c in fake.sent if c[0] == "login"]) == 1


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


def test_uidvalidity_from_status_parses_reply(monkeypatch):
    """主路径（EXAMINE 的 untagged 响应）读不到时，退到 STATUS 再问一次。"""
    _install(monkeypatch, _FakeIMAP(untagged={}))       # 主路径给不出 ⇒ 必须走退路
    mail_client.list_mail(7)
    assert mail_client._uidvalidity == "12345"


def test_uidvalidity_from_status_rejects_non_ok(monkeypatch):
    """非 OK 的 STATUS 回答绝不能被当成有效 UIDVALIDITY。

    payload 里的 `UIDVALIDITY 99999` 是刻意的：它是「漏掉 `typ != "OK"` 检查」
    会静默取到错值的**唯一**形状。换成不含这个词的报错串（如 `b"boom"`），
    正则匹配不到 ⇒ 退化成 `""` ⇒ 结局仍是报错，变异就咬不动了。
    """
    _install(monkeypatch, _FakeIMAP(
        untagged={}, status_reply=("NO", [b"NO [SERVERBUG] UIDVALIDITY 99999 unavailable"])))
    with pytest.raises(mail_client.MailError) as ei:
        mail_client.list_mail(7)
    assert "UIDVALIDITY" in str(ei.value)


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
    """**Date 头缺失时不能给空串** —— 空串会让前端日期栏静默留白、
    并让提示词里出现空的时间字段（`fmtLocal` 的 `!iso` 分支直接返回空串，
    根本走不到 `new Date()`；`llm_client` 那边是 f-string 直插）。
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
    """一整封的原始字节，正文**恰好**是 text。

    **刻意不用 `EmailMessage.set_content()`**：它交给 stdlib 的 `_encode_text`，
    后者把正文按 `b"\\n".join(lines) + b"\\n"` 收尾（base64 分支是先把同一个串
    编码再转 base64，尾换行**编进内容**里 ⇒ 事后截掉原始字节的尾换行也去不掉）。
    于是解析回来的正文恒多一个 `"\\n"`，断言里就得写 `"x\\n"` —— 那是**关于 fixture
    的知识，不是关于 `fetch_bodies` 的知识**，写进去只会让后来者以为正文该带尾换行。
    直接给原始字节，正文就是 text 本身。

    `Content-Transfer-Encoding: 8bit` 配原始 UTF-8 字节：`get_payload(decode=True)`
    走 surrogateescape 原样还原，正是 `_part_text` 期望的形状。
    """
    head = (f'Content-Type: text/{ctype}; charset="utf-8"\r\n'
            "Content-Transfer-Encoding: 8bit\r\n"
            "MIME-Version: 1.0\r\n"
            "\r\n").encode("ascii")
    return head + text.encode("utf-8")


def test_fetch_bodies_returns_id_to_body(monkeypatch):
    _install(monkeypatch, _BodyIMAP(
        headers={b"1": _hdr(), b"2": _hdr()},
        bodies={b"1": _body_msg("第一封正文"), b"2": _body_msg("第二封正文")}))
    mail_client.list_mail(7)              # 先记 uidvalidity
    out = mail_client.fetch_bodies([{"id": "1"}, {"id": "2"}])
    assert out == {"1": "第一封正文", "2": "第二封正文"}


def test_fetch_bodies_uses_body_peek(monkeypatch):
    """取正文整封走 BODY.PEEK[] —— 裸 BODY[] 会置 \\Seen。

    筛选条件只认 `(uid, FETCH)`，断言钉的是**命令内容**，两者不同口径。
    独立的一档只有两条（各配一条变异）：`all(...)` 管「发出去的每一条都不能是裸
    `BODY[`」；`any("BODY.PEEK[]")` 管「**正文**那一条确实发过」。
    `assert fetches` **不单独构成一档** —— 它是前提守卫，失败条件被 `any(...)` **严格
    蕴含**（`any` 为真 ⇒ 列表必然非空）。实测：删掉它、再把 `_do_bodies` 改成早退
    `return {}`，本用例**照样 `1 failed`**（红行前移到 `any(...)`）。
    留它的价值在**空列表**：那时它先炸、消息直白（`any(...)` 那条 f-string 会打出
    「header FETCH 把列表填满了：[]」—— 空列表不可能被填满）。
    少了 `any(...)`，本用例自己 `list_mail(7)` 发出的 header FETCH
    （`BODY.PEEK[HEADER.FIELDS …]`）就会把 `fetches` 填满、并通过上面那句 `all(...)`
    ——「正文一条都没取」于是全绿（把 `_do_bodies` 改成早退 `return {}` 照绿）。
    **顺序是刻意的**：`any(...)` 放在 `all(...)` **之后**，两条变异才各红各的行 ——
    正文改成裸 `BODY[]` 红在 `all(...)`（`BODY[` 被子串匹配抓到），
    正文一条都不发红在 `any(...)`。反过来放会遮住前一条。
    原先前两句是同一个谓词（筛选按 `"BODY.PEEK[]" in c[-1]`，下一句又断言同一个
    谓词）⇒ 第二句恒真，改成 `BODY[]` 时红的是 `assert fetches` 的空列表，
    第二句从未真正做过判断。
    """
    fake = _install(monkeypatch, _BodyIMAP(headers={b"1": _hdr()},
                                           bodies={b"1": _body_msg("x")}))
    mail_client.list_mail(7)
    mail_client.fetch_bodies([{"id": "1"}])
    fetches = [c for c in fake.sent if c[0] == "uid" and c[1] == "FETCH"]
    assert fetches, "没发出任何 FETCH，下面的断言是空转的"
    assert all("BODY.PEEK" in str(c[-1]) and not re.search(r"BODY\[", str(c[-1]))
               for c in fetches), f"有 FETCH 不是 PEEK 取件：{fetches}"
    assert any("BODY.PEEK[]" in str(c[-1]) for c in fetches), \
        f"正文一条 FETCH 都没发（header FETCH 把列表填满了）：{fetches}"


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
    """截断留**开头**不留结尾 —— 期望值是字面量，不是拿 `huge[:MAX]` 现算的。

    原先只断长度：把 `body[:MAX_BODY_CHARS]` 写成 `body[-MAX_BODY_CHARS:]` 照绿，
    而用户拿到的是邮件结尾而不是开头。首尾形状做成可区分（全 A / 全 B），
    期望串就能同时钉住「长度对」和「留的是哪一头」。
    """
    huge = "A" * 199_999 + "B" * 10_000          # 总长 209_999，首尾形状完全不同
    _install(monkeypatch, _BodyIMAP(headers={b"1": _hdr()},
                                    bodies={b"1": _body_msg(huge)}))
    mail_client.list_mail(7)
    out = mail_client.fetch_bodies([{"id": "1"}])
    assert out["1"] == "A" * 199_999 + "B"       # 长度 200_000，且钉住"是开头不是结尾"


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
