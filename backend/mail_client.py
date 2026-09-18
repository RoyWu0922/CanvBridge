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
import time
from datetime import datetime, timedelta, timezone

from . import banweb

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
        if offset:
            time.sleep(_THROTTLE)     # spec §9 请求节流：inbox 无服务端日期过滤，
                                      # 大邮箱会被整仓扫一遍，不能连发
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
    try:
        return banweb._on_browser_thread(lambda: banweb._retry_once(lambda: _do_list(days)))
    except (MailError, banweb.BanwebError):
        raise
    except Exception as exc:
        # Playwright 的原始异常（如 goto 的 60s 超时）在这里归一成 MailError，
        # 否则公开入口会漏出英文的 Playwright 文案 + 未处理的 500，Task 4 就认不出
        # 「会话失效」这条 #loginPrompt 路径。归一放在最外层边界**本身就对**：上面的
        # except (MailError, BanwebError): raise 让已成型的实例原样穿过，且与 banweb
        # 自己的边界归一惯例一致（banweb.py:1146 / :1200 / :1230 都在公开入口转换）。
        #
        # **不是**因为「做进 _retry_once 包着的那层会毁掉重试」—— 那样并不会：实测把
        # 归一搬进去，四条公开入口用例仍全绿，重试照跑。
        #
        # 「目标已关闭」的重试（spec §5.1 约束 2）与文案无关，靠的是位置：
        # _retry_once 包着的是 _do_list 本身，它在归一化**之前**就拿到了 Playwright
        # 的原始异常，_target_closed（banweb.py:778-784）的 str(exc) 子串匹配判的是
        # 原文，不是这条消息；重试耗尽之后才轮到本转换兜底。所以措辞是自由的 ——
        # 去掉 {exc} 实测 30 条全绿，不会有别的用例变红。
        #
        # 这份自由随转换位置而来：**一旦有人把归一搬进 _retry_once 包着的那层**，
        # _retry_once 看到的就是 MailError，_target_closed 便只剩一条命中路径 ——
        # f"…{exc}" 逐字回显的原始文本。搬家者必须连同 {exc} 的回显一起保留（哪怕
        # 只是为了给用户更干净的中文也不能删），否则 spec §5.1 约束 2 的恢复路径
        # （邮件 tab 被看门狗清掉 → _reset_browser() → 整体重发）会静默失效，且全仓
        # 只有 test_list_mail_keeps_target_closed_retry 会红（实测：搬进去且去掉）。
        raise MailError(f"读取邮件列表失败：{exc}") from exc


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
    try:
        return banweb._on_browser_thread(lambda: banweb._retry_once(lambda: _do_bodies(items)))
    except (MailError, banweb.BanwebError):
        raise
    except Exception as exc:
        raise MailError(f"读取邮件正文失败：{exc}") from exc
