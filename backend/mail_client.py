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
    """OWA 时间戳形如 2026-09-17T08:31:00Z。解析不出返回 None。

    缺时区偏移的（naive）也返回 None：它与 aware 的 cutoff 比较会抛 TypeError，
    而那是本文件唯一一条 fail-closed 路径 —— 单封坏邮件就能炸穿整个 select_scope。
    归到 None 即走「保留」分支，与其余坏输入的处理一致。
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return None if ts.tzinfo is None else ts


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
