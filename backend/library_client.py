"""CityU / JULAC 图书馆检索客户端（Ex Libris Primo 公开 API）。

**本模块全程零登录。** 下面这些参数组合是 2026-09-14 对线上实例实测踩出来的，
改动前请先读注释 —— 三个坑都不会报错，只会静默返回 0 命中。

不返回 delivery 块（索书号/在架状态/全文链接），那些需要登录，属第二阶段。
"""
from __future__ import annotations

import re
from urllib.parse import quote

import requests

PRIMO_BASE = "https://julac-cuh.primo.exlibrisgroup.com"
# 实测：vid 必须是**带冒号的全限定形式**。只写 852JULAC_CUH 静默返回 0 命中。
PRIMO_VID = "852JULAC_CUH:CUH"
SEARCH_PATH = "/primaws/rest/pub/pnxs"

# 本设计只用这两个 scope（spec §3.2）。MyInst_and_CI 会混在一起，不如并发两次干净。
SCOPE_LOCAL = "MyInstitution"      # 本馆馆藏 / 纸本
SCOPE_EXTERNAL = "CentralIndex"    # Primo Central 中央索引（文章级）

# 上限（spec §9）。**由后端挡**，不能只靠前端不显示按钮。
PAGE_SIZE = 20
MAX_PAGES = 5

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CanvBridge/1.0)",
            "Accept": "application/json"}


class LibraryError(RuntimeError):
    """图书馆检索相关的失败。端点层捕获它转成 {ok:false,error}。"""


# ---------- 字段清洗 ----------

# MARC 子字段标记：$$Q $$N $$L … 实测 creator 里带这个
_SUBFIELD = re.compile(r"\$\$[A-Za-z0-9]")
_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")


def _first(value) -> str:
    """pnx 的字段时而是字符串、时而是列表、时而整个缺失（None）。取首个非空标量。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        for v in value:
            s = str(v).strip()
            if s:
                return s
        return ""
    return str(value).strip()


def _clean(value) -> str:
    """取首值并切掉 MARC 子字段尾巴。
    'Hughes, Isaac S., author.$$QHughes, Isaac S.' → 'Hughes, Isaac S., author.'
    """
    return _SUBFIELD.split(_first(value))[0].strip()


def _year(*values) -> str:
    """从若干候选里挖出第一个四位年份。'2024-05-01' → '2024'。"""
    for v in values:
        m = _YEAR.search(_first(v))
        if m:
            return m.group(1)
    return ""


# ---------- 对外 ----------

def record_url(record_id: str, context: str = "L") -> str:
    """Primo 记录页永久链接。

    ⚠️ **实测警告**：这个 URL 对一个编造的 docid 照样返回 HTTP 200，
    且页面与真实记录**逐字节相同**（4784 字节的 SPA 外壳）。所以
    **HTTP 状态码不能用来验证记录是否存在**。格式取自 Primo 标准路由，
    `recordid` 与 `context` 都来自同一条记录自身，内部自洽；但「点开
    能否看到这条记录」只有人工点一次才能确认（见计划末尾验收）。
    """
    return (f"{PRIMO_BASE}/discovery/fulldisplay?docid={quote(record_id)}"
            f"&context={quote(context)}&vid={PRIMO_VID}&lang=en")


def normalize_doc(doc: dict) -> dict:
    """Primo 的一条 doc → 前端要的扁平形状。

    实测字段形状（全部来自真实响应，不是猜的）：
      - `pnx.display.title` 是**列表** `['...']`
      - `pnx.display.creator` 带 MARC 子字段噪声
      - `pnx.addata.date` 可能是 `['2024-05-01']`，也可能缺失 / None
      - `pnx.control.recordid` 形如 `alma991030012933203408`（已含 alma 前缀）
    任何一项缺失都不能抛 —— 抛了整页结果就没了。
    """
    pnx = doc.get("pnx") or {}
    display = pnx.get("display") or {}
    addata = pnx.get("addata") or {}
    control = pnx.get("control") or {}

    record_id = _first(control.get("recordid"))
    context = doc.get("context") or "L"
    return {
        "title": _clean(display.get("title")),
        "creator": _clean(display.get("creator")),
        "year": _year(addata.get("date"), display.get("creationdate")),
        "type": _first(display.get("type")),
        "journal": _clean(addata.get("jtitle")) or _clean(display.get("ispartof")),
        "doi": _first(addata.get("doi")),
        "language": _first(display.get("language")),
        "source": _first(display.get("source")),
        "record_id": record_id,
        "url": record_url(record_id, context) if record_id else "",
    }


def build_params(scope: str, q: str, *, q_include: list[str] | None = None,
                 q_exclude: list[str] | None = None, page: int = 1) -> dict:
    """拼 Primo 查询参数。三个实测坑全部固化在这里：

    - `vid` 必须带冒号（不带 → 静默 0 命中）
    - **不生成 `inst` 键**（传了 → 0 命中）
    - `scope` 必填（漏掉 → HTTP 400 + 空 body，不是 JSON 错误页）

    `q_include` / `q_exclude` 是**完整过滤表达式**列表（如 `"rtype,exact,articles"`），
    这里只负责拼成**重复的同名参数**（requests 对 list 值就是这么编码的）。
    """
    params = {
        "vid": PRIMO_VID,
        "tab": "Everything",
        "scope": scope,
        "q": q,
        "sort": "rank",
        "lang": "en",
        "skipDelivery": "Y",
        "blendFacetsSeparately": "false",
        "getMore": "0",
        "refEntryActive": "false",
        "rtaLinks": "true",
        "limit": str(PAGE_SIZE),
        "offset": str((page - 1) * PAGE_SIZE),
    }
    if q_include:
        params["qInclude"] = list(q_include)
    if q_exclude:
        params["qExclude"] = list(q_exclude)
    return params


import threading
import time

# 相邻两次请求之间的最小间隔（spec §9）。用户手动翻页天然慢于此，
# 这条只是兜住连点。测试里 monkeypatch 成 0，免得拖慢用例。
_MIN_INTERVAL = 1.0
_last_call = 0.0
_throttle_lock = threading.Lock()


def _throttle() -> None:
    global _last_call
    with _throttle_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _get(path: str, params: dict, session) -> dict:
    """发一次请求并把传输层异常统一成 LibraryError。"""
    _throttle()
    own = session is None
    s = session or requests.Session()
    try:
        resp = s.get(PRIMO_BASE + path, params=params, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise LibraryError(f"图书馆检索失败：{exc}") from exc
    finally:
        if own:
            s.close()


def search(scope: str, q: str, *, q_include: list[str] | None = None,
           q_exclude: list[str] | None = None, page: int = 1,
           session=None) -> dict:
    """查一个 scope 的一页。

    **页码越界在这里就拒** —— 上限必须由后端挡（spec §9）。
    `has_more` = 「本页取满 且 page < MAX_PAGES」，由后端算好给前端。
    """
    if page < 1 or page > MAX_PAGES:
        raise LibraryError(f"页码越界：{page}（允许 1..{MAX_PAGES}）")
    params = build_params(scope, q, q_include=q_include, q_exclude=q_exclude, page=page)
    data = _get(SEARCH_PATH, params, session)
    docs = data.get("docs") or []
    return {
        "scope": scope,
        "items": [normalize_doc(d) for d in docs],
        "count": int((data.get("info") or {}).get("total") or 0),
        "page": page,
        "has_more": len(docs) >= PAGE_SIZE and page < MAX_PAGES,
    }


# 拉 domain 词表用的宽查询。要的是 facets，不是 hits，所以 limit 用默认的一页就够。
_DOMAIN_PROBE_Q = "any,contains,the"


def list_domains(*, session=None) -> list[str]:
    """拉 Primo `domain` facet 的全部取值，供 LLM 挑「自动选库」（spec §4.2）。

    **值必须来自这里** —— ereslist 的库名（'ScienceDirect'）和 Primo 的
    domain 值（'Elsevier ScienceDirect Journals'）是两套词表，直接喂会 0 命中。

    拿不到（网络失败 / facet 没了）→ 返回 `[]`，调用方据此不做库级收窄。
    **不抛错** —— 选库是增强，不是主干。
    """
    params = build_params(SCOPE_EXTERNAL, _DOMAIN_PROBE_Q)
    try:
        data = _get(SEARCH_PATH, params, session)
    except LibraryError:
        return []
    for facet in data.get("facets") or []:
        if facet.get("name") == "domain":
            return [v.get("value", "") for v in facet.get("values") or [] if v.get("value")]
    return []
