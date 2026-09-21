"""OWA 取信层测试（纯函数 + I/O）。全程合成 fixture，不出网。"""
import email
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from backend import banweb, mail_client


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
    assert p["Body"]["ItemShape"]["BaseShape"] == "IdOnly"


def test_find_item_payload_defaults_to_page_size():
    """不传参时 MaxEntriesReturned 必须是 PAGE_SIZE —— spec 钉的 50。"""
    assert mail_client.find_item_payload()["Body"]["Paging"]["MaxEntriesReturned"] == 50


def test_get_item_payload_shape():
    p = mail_client.get_item_payload(["A", "B"])
    assert p["__type"] == "GetItemJsonRequest:#Exchange"
    assert p["Body"]["__type"] == "GetItemRequest:#Exchange"
    assert p["Body"]["ItemShape"]["BodyType"] == "Text"
    assert p["Body"]["ItemShape"]["BaseShape"] == "Default"
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


def test_select_scope_no_offset_date_is_kept_and_does_not_raise():
    """缺时区偏移的时间戳（naive）必须和「读不出来」同路：保留，不抛。

    它曾被解析成 naive datetime，再与 aware 的 cutoff 比较 → TypeError 炸穿整个
    select_scope —— 单封坏邮件就能让整次取信失败，且是本文件唯一一条 fail-closed 路径。
    """
    items = [{"id": "无偏移", "subject": "", "sender": "",
              "received": "2026-09-17T08:31:00", "is_read": True}]
    assert [x["id"] for x in mail_client.select_scope(items, NOW, 7)] == ["无偏移"]


def test_select_scope_defaults_to_default_days():
    """不传 days 时走 DEFAULT_DAYS —— spec 钉的 7。"""
    items = [_mk("边界", 7, True), _mk("超一天", 8, True)]
    assert {x["id"] for x in mail_client.select_scope(items, NOW)} == {"边界"}


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


def test_collect_items_throttles_between_pages(monkeypatch):
    """翻页之间要节流（spec §9）：inbox 无服务端日期过滤，大邮箱会被整仓扫一遍。

    只在**首个请求之后**睡 —— 首个请求前睡是白等。
    """
    pages = {0: [_item(f"A{n}") for n in range(mail_client.PAGE_SIZE)],
             mail_client.PAGE_SIZE: [_item("LAST")]}
    slept = []
    monkeypatch.setattr(mail_client.time, "sleep", slept.append)
    monkeypatch.setattr(mail_client, "_post_json",
                        lambda a, p, t: _find_resp(pages.get(p["Body"]["Paging"]["Offset"], [])))
    mail_client.collect_items("TOK", 7, NOW)
    assert slept == [mail_client._THROTTLE]


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
    def fake_post(action, payload, token):
        # 钉住 _capture_token 的返回值真的被送到了 REST 层（否则这条链可以硬编码任何串）
        assert token == "TOK"
        return _find_resp(rows)

    monkeypatch.setattr(mail_client, "_post_json", fake_post)
    monkeypatch.setattr(mail_client, "datetime", _FrozenDatetime)
    got = mail_client.list_mail(7)
    assert {x["id"] for x in got} == {"近读", "旧未读"}


def test_fetch_bodies_batches_and_maps_back_by_id(no_browser, monkeypatch):
    ids = [f"B{n}" for n in range(mail_client.BODY_BATCH + 3)]
    batches = []

    def fake_post(action, payload, token):
        assert action == "GetItem"
        assert token == "TOK"       # 同上：_do_bodies 也必须把截到的 token 送下去
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


def test_capture_token_reads_first_bearer_from_service_svc(monkeypatch):
    """截 token 的全部接线：认 service.svc、只认第一个、截到即停、必摘监听器。"""
    class _P:
        def __init__(self):
            self.h, self.ticks, self.url = {}, 0, None
            self.removed = []                        # 摘除记录；见用例末尾的断言
        def on(self, ev, fn): self.h[ev] = fn
        def remove_listener(self, ev, fn): self.removed.append((ev, fn))
        def goto(self, url, **k):
            self.url = url
            for u, tok in (("https://x/static/a.js", "WRONG"),
                           ("https://x/owa/service.svc?action=FindItem", "TOK1"),
                           ("https://x/owa/service.svc?action=GetItem", "TOK2")):
                self.h["request"](type("R", (), {"url": u,
                                                 "headers": {"Authorization": f"Bearer {tok}"}})())
        def wait_for_timeout(self, ms): self.ticks += 1

    p = _P()
    assert mail_client._capture_token(p) == "TOK1"   # 只认 service.svc、只认第一个
    assert p.url == mail_client.MAIL_URL
    assert p.ticks == 0                              # 截到即 break，不空转满 40 次

    # 摘除必须**真发生**且摘的是 on 注册的同一个对象：断言放在用例里（而不是替身的
    # remove_listener 内部）—— 放内部时，把 finally 整块删掉就没人调用它，断言根本不跑，
    # 用例照样绿。每次都漏摘会让这个长寿命 page 对象累积监听器。
    assert len(p.removed) == 1
    ev, fn = p.removed[0]
    assert ev == "request"
    assert fn is p.h["request"]        # 传 None / 别的函数都必须红


# --- 公开入口的错误契约：归一成 MailError，但绝不二次包装 ---

class _RawBrowserBoom(Exception):
    """冒充 Playwright 抛出的原始异常（如 60s goto 超时）。"""


def _boom(*_a, **_k):
    raise _RawBrowserBoom("Timeout 60000ms exceeded.")


def test_list_mail_normalizes_raw_browser_error(no_browser, monkeypatch):
    """Playwright 原始异常必须变成 MailError，不能从公开入口原样冒出。

    否则 Task 4 的端点认不出「会话失效」，只会回一个 500 + 英文 Playwright 文案，
    而不是 #loginPrompt。
    """
    monkeypatch.setattr(mail_client, "_do_list", _boom)
    with pytest.raises(mail_client.MailError):
        mail_client.list_mail(7)


def test_fetch_bodies_normalizes_raw_browser_error(no_browser, monkeypatch):
    """同上，fetch_bodies 也走公开入口，同样不能漏。"""
    monkeypatch.setattr(mail_client, "_do_bodies", _boom)
    with pytest.raises(mail_client.MailError):
        mail_client.fetch_bodies([{"id": "A"}])


def test_public_entries_pass_own_errors_through_unwrapped(no_browser, monkeypatch):
    """MailError / BanwebError 必须是**同一个对象**穿过去 —— 文案要原样到用户面前。"""
    for exc in (mail_client.MailError("邮箱会话已失效"),
                banweb.BanwebError("AIMS 浏览器无响应")):
        def _raise(*_a, _exc=exc, **_k):
            raise _exc

        monkeypatch.setattr(mail_client, "_do_list", _raise)
        with pytest.raises(type(exc)) as caught:
            mail_client.list_mail(7)
        assert caught.value is exc


def test_list_mail_keeps_target_closed_retry(monkeypatch):
    """归一化之后，「目标已关闭」的重试仍要照跑一次（spec §5.1 约束 2）。

    本用例锁的是这条**独立性质**：归一化没有破坏「邮件 tab 被看门狗清掉」的恢复路径。
    它**不**判别归一写在哪儿 —— 把转换搬进 _retry_once 包着的那层，本用例照样绿
    （那时 _target_closed 靠归一后的消息逐字回显原始异常文本才命中）。

    所以这条恢复路径的存活与**消息文案**无关：只要归一留在 _retry_once 之外，
    _retry_once 看到的就是**原始**异常，改 MailError(f"...{exc}") 的措辞（例如去掉
    {exc}）不会影响它。**但一旦有人把归一搬进那层，耦合就出现了** —— 那时本用例
    是唯一会红的守卫。下面故意**不替身** _retry_once，走真实的它。
    """
    monkeypatch.setattr(mail_client.banweb, "_on_browser_thread", lambda fn: fn())
    monkeypatch.setattr(mail_client.banweb, "_ensure_mail_page", lambda: _FakePage())
    monkeypatch.setattr(mail_client.banweb, "_reset_browser", lambda: None)  # 绝不碰真浏览器
    monkeypatch.setattr(mail_client, "_post_json", lambda a, p, t: _find_resp([]))
    calls = []

    def flaky_capture(page):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Target page, context or browser has been closed")
        return "TOK"

    monkeypatch.setattr(mail_client, "_capture_token", flaky_capture)
    assert mail_client.list_mail(7) == []
    assert len(calls) == 2      # 第一次被 _retry_once 吞下并整体重跑


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
