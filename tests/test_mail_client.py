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
    下面的 `all(...)` 在空清单上恒真，故它连「附件数量」也钉不住 —— 那是 index 用例的事。
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
    assert all(set(a) == {"index", "name", "size"} for a in d["attachments"])


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
