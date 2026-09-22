"""Gmail IMAP 取信层测试（纯函数 + I/O）。全程合成 fixture，不出网。"""
import email
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest

from backend import credentials, mail_client


#: 替身给每封邮件回的 INTERNALDATE。刻意与 _hdr() 默认的 Date 头
#: （16:31 +0800 = 08:31Z）**不同**，这样「Date 头优先」才可判。
_INTERNALDATE = b"17-Sep-2026 20:00:00 +0000"


class _FakeIMAP:
    """替身，记录**发出的每一条命令**。绝不真连网（netguard 兜底）。"""

    def __init__(self, *, uidvalidity=b"7", headers=None, login_err=None,
                 untagged=None, status_reply=("OK", [b'"INBOX" (UIDVALIDITY 12345)']),
                 internaldates=None):
        self.uidvalidity = uidvalidity
        self.headers = headers or {}     # {uid_bytes: raw_header_bytes}
        #: {uid_bytes: INTERNALDATE bytes}；缺省一律用模块级 _INTERNALDATE。
        #: 顺序用例需要**逐封不同**的到达时刻，故留此口子。
        self.internaldates = internaldates or {}
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
                                % (u, self.internaldates.get(u, _INTERNALDATE),
                                   len(self.headers[u])),
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
         date="Thu, 17 Sep 2026 16:31:00 +0800",
         to="t@cityu.edu.hk"):
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = sender
    m["Date"] = date
    m["To"] = to
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
    m["To"] = "t@cityu.edu.hk"        # 转发件判据要 To/Cc，缺了会被 Task 3 的过滤静默滤掉
    _install(monkeypatch, _FakeIMAP(headers={b"1": m.as_bytes()}))
    out = mail_client.list_mail(7)
    assert out[0]["received"] == "2026-09-17T20:00:00+00:00"


def test_list_mail_prefers_date_header_over_internaldate(monkeypatch):
    """两者都在时用 Date 头（16:31+0800 → 08:31Z），不是 INTERNALDATE（20:00Z）。"""
    _install(monkeypatch, _FakeIMAP(headers={b"1": _hdr()}))
    out = mail_client.list_mail(7)
    assert out[0]["received"] == "2026-09-17T08:31:00+00:00"


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


def test_do_list_applies_select_scope(monkeypatch):
    """_do_list 真的调用了 select_scope，参数正确，并剥掉内部键后返回它的结果。

    返回前还会过一遍 `sort_newest_first`；本用例两封 INTERNALDATE **相同**，
    稳定排序下仍是输入序，所以下面那条顺序断言不受排序影响。

    （其余 I/O 用例把 select_scope 换成了恒等函数。删掉 `return select_scope(...)`
    时本用例会红；`test_do_list_returns_newest_first` 也覆盖同一根接线，
    故它不再是唯一一条证据。）

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


# ============ 显示顺序：新→旧（spec §4.2）============

def _utc(day: int) -> datetime:
    """顺序用例用的 aware 时刻：同月不同日。"""
    return datetime(2026, 9, day, 8, 0, tzinfo=timezone.utc)


def _seq(*pairs):
    """顺序用例的夹具：`(id, _internaldate)` 逐条给，值原样放进条目（含 None/naive）。"""
    return [{"id": i, "_internaldate": d} for i, d in pairs]


def test_sort_newest_first_orders_desc():
    """后收到的不许排在前收到的后面。

    输入刻意不按序（15/20/18）：恒等函数下得到的是 ["a","b","c"]，与本用例
    期望的 ["b","c","a"] 不同 ⇒ 这个用例对「根本没排」是**红**的。
    """
    out = mail_client.sort_newest_first(
        _seq(("a", _utc(15)), ("b", _utc(20)), ("c", _utc(18))))
    assert [it["id"] for it in out] == ["b", "c", "a"]


def test_sort_newest_first_keeps_unusable_last_without_dropping():
    """到达时刻缺失 / `None` / naive：**保留、不抛**，一律当最旧排在最后。

    三种错都咬：① 直接拿 `None` 比大小 ⇒ `TypeError`；
    ② 把不可用条目过滤掉 ⇒ 顺序断言红（会少一条）；
    ③ 排序不稳定 ⇒ 末三条的相对顺序断言红。
    """
    items = _seq(("none", None), ("oldest", _utc(18)), ("naive", datetime(2026, 9, 21, 8, 0)))
    items.append({"id": "missing"})               # 连键都没有
    out = mail_client.sort_newest_first(items)
    assert [it["id"] for it in out] == ["oldest", "none", "naive", "missing"]


def test_sort_newest_first_is_stable_and_does_not_mutate():
    """同一时刻的两条保持输入相对顺序；且**不改动入参**。"""
    items = _seq(("first", _utc(18)), ("second", _utc(18)))
    out = mail_client.sort_newest_first(items)
    assert [it["id"] for it in out] == ["first", "second"]
    assert [it["id"] for it in items] == ["first", "second"], "入参被就地改了"


def test_do_list_returns_newest_first(monkeypatch):
    """接线：`_do_list` 真的调用了排序 —— 删掉那一行，本用例红。

    两封的 `Date:` 头**相同**（`_hdr()` 默认）而 INTERNALDATE 不同，所以本用例
    顺带钉住「排序键是 INTERNALDATE，不是 `received`」：按 `received` 排会留下
    输入顺序 ["1","2"] ⇒ 红。

    `days=3650` 是为了**避开时间炸弹** —— 夹具的 INTERNALDATE 写死，若走真实时钟的
    7 天窗口，过了那天用例自己就红了（本文件 `_install` 的 docstring 已就此警告过）。
    窗口开到足够大，这里测的就只剩顺序。
    """
    fake = _FakeIMAP(
        headers={b"1": _hdr(), b"2": _hdr()},
        internaldates={b"1": b"15-Sep-2026 20:00:00 +0000",
                       b"2": b"20-Sep-2026 20:00:00 +0000"})
    _install(monkeypatch, fake, scope=mail_client.select_scope)   # 真的 select_scope
    out = mail_client.list_mail(3650)
    assert [it["id"] for it in out] == ["2", "1"], "旧的在最前 —— 排序没接上"


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


def _recip(*tos, cc=()):
    """只带 To/Cc 的邮件 —— 判据的全部输入。"""
    m = EmailMessage()
    if tos:
        m["To"] = ", ".join(tos)
    if cc:
        m["Cc"] = ", ".join(cc)
    return m


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


_BOUND = b"SYNTHBOUNDARY"


def _raw_mail(atts=(), text="正文", to=b"t@cityu.edu.hk"):
    """一封 multipart/mixed 的**原始字节**。

    atts 是 `[(头部块 bytes, 内容 bytes)]`，头部块自带 Content-Disposition。
    附件序 = atts 的顺序（与 `msg.walk()` 的遍历顺序一致）。

    理由同 `_body_msg` 的注释：stdlib 的 `add_attachment` 编码/换行行为要另花力气核，
    原始字节把「正文恰好是 text」这件事钉死。`text` 也跟 `_body_msg` 同一惯例 ——
    **str 进、内部 encode**。计划里那条 fixture 逐字抄下来是 `text=b"正文"`，那是
    **SyntaxError**（bytes 字面量只能含 ASCII），连收集都过不去。
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
    「正文一条 FETCH 都没发（header FETCH 把列表填满了）：[]」—— 空列表不可能被填满）。
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
    **0 个附件 part**（只产出它自己一个 part：`is_multipart()` 是 False、
    `Content-Disposition` 是 None）⇒ `_attachment_parts()` 返回 `[]`、`_att_name` /
    `_part_size` **一次都不会被调到**；`message_body` 返回的是那段原始头文本（是 str）。
    也就是说这条用例钉的是「`fetch_detail` 在这份输入上不抛、且输出形状仍是 `str` + `list`」，
    **不覆盖那两个函数的守卫** —— 那个守卫是 Task 5 才加的加固，覆盖也在 Task 5。
    末行钉的是**实测计数**（恰 0 条），不是原先那句在全清单上恒真的 `all(...)`；
    解析器行为一变它就会红。
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

    末句钉的是**顺序**：核对必须排在 `_fetch_raw_chunk` **之前** —— 重建后 UID 可能指向
    别的邮件，先取再核对等于「按错的编号取了一封」。只断 `raises` 的话，把核对挪到取件
    **之后**照样绿（仍然会抛），这一格就漏了；与 `fetch_bodies` 那条同一口径。
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
    """三个元素逐个钉死：名字、字节、**这封邮件自己的 `received`**。

    第三个元素是 Task 6 追加的落盘日期来源。**必须逐字断言**，否则它就是个
    「声明强于断言」的装饰字段：`_raw_mail` 的 `Date:` 头是
    `Thu, 17 Sep 2026 16:31:00 +0800`（`_hdr()` 给的是同一个时刻）⇒
    `_iso(received_dt(msg))` = `2026-09-17T08:31:00+00:00`。
    `save_attachment` 的日期目录正是靠它：把它喂错、或恒返回 `""`，
    落盘都会**静默**退回「当天 UTC」，而七条落盘用例各有各的 fake、抓不到这个。

    反面（`Date:` 解不出 ⇒ `""`）**没有**单独用例：本轮 brief 的修订明确限定
    「不得新增测试函数」（条数 7 与全量 590 要维持有效）。那条路的判据落在
    `_received_date` 自己的用例上（`falls_back_to_today_on_bad_date` 喂坏串），
    以及 `_iso(None) == ""` 的既有覆盖。
    """
    raw = _raw_mail([(_att_head(b"a.bin"), b"AAAA"), (_att_head(b"b.bin"), b"BB")])
    _detail_install(monkeypatch, {b"1": raw})
    mail_client.list_mail(7)
    assert mail_client.fetch_attachment({"id": "1"}, 1) == (
        "b.bin", b"BB", "2026-09-17T08:31:00+00:00")


def test_fetch_attachment_agrees_with_detail_index(monkeypatch):
    """**组合绑定**：detail 报出的 index 拿回 `fetch_attachment`，取到的是**同一个**附件。"""
    raw = _raw_mail([(_att_head(b"a.bin"), b"AAAA"), (_att_head(b"b.bin"), b"BB")])
    _detail_install(monkeypatch, {b"1": raw})
    mail_client.list_mail(7)
    for a in mail_client.fetch_detail({"id": "1"})["attachments"]:
        name, data, _received = mail_client.fetch_attachment({"id": "1"}, a["index"])
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
    assert mail_client.fetch_attachment({"id": "1"}, 0) == (
        "a.bin", b"AAAA", "2026-09-17T08:31:00+00:00")


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
    name, data, _received = mail_client.fetch_attachment({"id": "1"}, 0)
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


# ============ 落盘：目录、不覆盖、锚点（spec §5）============

def _fake_attachment(monkeypatch, name, data, received=""):
    """把 `fetch_attachment` 换成三元组替身 —— **落盘日期从这里进**，不从 `item` 进。

    生产里 `item` 只是 `{"id": uid}` 句柄（`fetch_detail`/`fetch_attachment`/
    `save_attachment` 三个函数都只读 `item["id"]`，各自重新解析整封），Task 7 的路由
    收到的请求体也只有 `{id, index, download_dir}`：`item` 里**没有** `received`，
    日期必须由 `fetch_attachment` 从**那封邮件自己**的 `Date:` 头带出来（spec §5）。
    所以本节的日期一律经这个参数注入 —— 写成 `item={"received": ...}` 是在测一条
    生产里根本不存在的输入路径（那种写法下七条用例会全绿，而线上永远是「当天」）。
    """
    monkeypatch.setattr(mail_client, "fetch_attachment",
                        lambda item, index: (name, data, received))


def test_save_attachment_writes_into_school_folder(tmp_path, monkeypatch):
    _fake_attachment(monkeypatch, "a.bin", b"AAAA", "2026-09-17T08:31:00+00:00")
    out = mail_client.save_attachment({"id": "1"}, 0, str(tmp_path))
    assert out == str(tmp_path / "学校邮件" / "2026-09-17_a.bin")
    assert (tmp_path / "学校邮件" / "2026-09-17_a.bin").read_bytes() == b"AAAA"


def test_save_attachment_creates_missing_folder(tmp_path, monkeypatch):
    """目录不存在则创建 —— 用户填的下载目录里没有「学校邮件」是常态。"""
    _fake_attachment(monkeypatch, "a.bin", b"x", "")
    assert not (tmp_path / "学校邮件").exists()
    mail_client.save_attachment({"id": "1"}, 0, str(tmp_path))
    assert (tmp_path / "学校邮件").is_dir()


def test_save_attachment_never_overwrites(tmp_path, monkeypatch):
    """同名**绝不覆盖**：第二次落 `-1`，第三次落 `-2`（spec §5）。"""
    item = {"id": "1"}
    # 三次的日期必须**同一天**：日期一变，落点就成了另一个名字，
    # `-1`/`-2` 后缀断言会退化成「三个互不相干的文件」，与「同名不覆盖」无关。
    _fake_attachment(monkeypatch, "a.bin", b"first", "2026-09-17T08:31:00+00:00")
    p1 = mail_client.save_attachment(item, 0, str(tmp_path))
    _fake_attachment(monkeypatch, "a.bin", b"second", "2026-09-17T08:31:00+00:00")
    p2 = mail_client.save_attachment(item, 0, str(tmp_path))
    _fake_attachment(monkeypatch, "a.bin", b"third", "2026-09-17T08:31:00+00:00")
    p3 = mail_client.save_attachment(item, 0, str(tmp_path))
    assert p1.endswith("2026-09-17_a.bin")
    assert p2.endswith("2026-09-17_a-1.bin")
    assert p3.endswith("2026-09-17_a-2.bin")
    assert Path(p1).read_bytes() == b"first"          # 第一份没被覆盖
    assert Path(p2).read_bytes() == b"second"


def test_save_attachment_uses_utc_date_of_received(tmp_path, monkeypatch):
    """日期取 `received` 的 **UTC** 日（spec §5；代价见 §9）。

    ⚠️ 输入必须选一个 **UTC 日 ≠ 本地日** 的时刻，否则这条用例**分不出** UTC 与本地。
    初稿用的是 `2026-09-17T23:30:00+08:00`，**它的本地日与 UTC 日都是 17 号**
    ⇒ 把 `_received_date` 的 `astimezone(utc)` 去掉、直接 `dt.strftime("%Y-%m-%d")`
    （= 本地日），这条用例**照绿**（实测：两种实现都得到 `2026-09-17_a.bin`）
    —— 「声明 UTC 的用例」在那种输入下根本没有在测 UTC。本仓对这类
    「声明强于断言」的盲区已有多次记录，所以这里换成一个能分开两者的输入。
    对 +0800 而言**唯一**能分开的是**凌晨**那一侧（晚间两侧相同，实测）：
    `03:00+08:00` = 前一日 `19:00Z` ⇒ **UTC 日是 16 号、本地日是 17 号**。
    """
    _fake_attachment(monkeypatch, "a.bin", b"x", "2026-09-17T03:00:00+08:00")
    out = mail_client.save_attachment({"id": "1"}, 0, str(tmp_path))
    assert "2026-09-16_a.bin" in out       # 03:00+08:00 = 前一日 19:00Z ⇒ UTC 日是 16 号


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
    for i, bad in enumerate(("", "not-a-date", "2026-09-17T08:31:00")):
        # 替身必须在**循环内**重打：打在循环外的话三轮都带着第一个坏值，
        # 后两轮的 `bad` 根本没进过被测代码（"2026-09-17T08:31:00" 是 naive，
        # 它的 tzinfo is None 断言就成了摆设）。
        _fake_attachment(monkeypatch, "a.bin", b"x", bad)
        before = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out = mail_client.save_attachment({"id": "1"}, 0, str(tmp_path / str(i)))
        after = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert any(f"{d}_a.bin" in out for d in {before, after}), out


def test_save_attachment_rejects_empty_download_dir(monkeypatch):
    _fake_attachment(monkeypatch, "a.bin", b"x", "")
    for bad in ("", "   "):
        with pytest.raises(mail_client.MailError):
            mail_client.save_attachment({"id": "1"}, 0, bad)


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

    ⚠️ **那个 `mkdir` 是承重的，别当它是清场代码删掉。** 修复轮 1 实测（复审独立复现过）：
    不建这一级时，把整个锚点块 `target.resolve().relative_to(root.resolve())` 删掉，
    本用例**照样绿**（`-k refuses_path_escaping` ⇒ 1 passed）—— 因为逃逸路径的中间那一级
    `<root>/学校邮件/2026-09-17_..` 不存在，`write_bytes` 先以 ENOENT 失败，抛的是
    `MailError("保存附件失败：…")`：**两个不同的失败，被同一个 `pytest.raises(MailError)` 接住**。
    把这一级**先建出来**，逃逸路径就变成真正可写的 ⇒ 删掉锚点后 `evil.bin` 会**真的落在根外**，
    本用例随即变红（实测两条红：`DID NOT RAISE` + 末尾那句不存在断言）。
    这样钉的是**危害**（文件跑到根外），而不是错误文案 —— 断言文案会把用例绑死在字句上。
    （`_unique_path` 用的是**不 resolve** 的 `target.exists()`：预建这一级不会让它以为目标已存在
    而改落 `-1` 后缀；实测落点仍是 `2026-09-17_evil.bin` 那一支。）

    第三个元素钉**固定日期**（不是 `""`）：目录名必须确定，否则预建的那一级与真实落点
    会对不上（`""` 走「当天 UTC」，跨 UTC 午夜时预建的目录名与实际落点分属两天 ⇒ 无故变红）。
    """
    monkeypatch.setattr(mail_client, "fetch_attachment",
                        lambda item, index: ("../../../../evil.bin", b"x",
                                             "2026-09-17T08:31:00+00:00"))
    (tmp_path / "学校邮件" / "2026-09-17_..").mkdir(parents=True)
    with pytest.raises(mail_client.MailError):
        mail_client.save_attachment({"id": "1"}, 0, str(tmp_path))
    assert not (tmp_path.parent / "evil.bin").exists()
