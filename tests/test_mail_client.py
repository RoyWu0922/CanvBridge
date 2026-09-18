"""OWA 取信层纯函数测试。全程合成 fixture，不出网。"""
from datetime import datetime, timedelta, timezone

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
