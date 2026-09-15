"""CityU / JULAC 图书馆检索客户端（Ex Libris Primo 公开 API）。

**本模块全程零登录。** 下面这些参数组合是 2026-09-14 对线上实例实测踩出来的，
改动前请先读注释 —— 三个坑都不会报错，只会静默返回 0 命中。

不返回 delivery 块（索书号/在架状态/全文链接），那些需要登录，属第二阶段。
"""
from __future__ import annotations

import contextlib
import json
import re
import threading
import time
from pathlib import Path
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


# ---------- 节流：作用域是「一次查询」，不是「一次 HTTP 调用」 ----------
#
# spec §9 的限速原文是「**相邻翻页请求之间** ≥ 1s」，并发一条是「**只允许两个 scope
# 并发**（同一次查询的两栏）……这是唯一的一处并发；**不同查询之间串行**」。
# 所以节流该管的是**不同查询之间**，同一次查询自己的两栏不该互相等。
#
# 旧实现把 _throttle() 放在 _get() 里按**单次调用**节流，两个后果：
#   ①两个 scope 串行时，同一次查询的第二次调用被当成「太频繁」自罚一秒
#     —— 每次检索白等约 2.0 秒（F-3 实测）；
#   ②两个 scope 一旦并发，两个线程读写同一个模块级 _last_call 还会打架。
# 现在：查询 = 一个节流单位（query_scope），查询内部的调用直接放行。
#
# 相邻两次**查询**之间的最小间隔（spec §9）。用户手动翻页天然慢于此，
# 这条只是兜住连点。测试里 monkeypatch 成 0，免得拖慢用例。
_MIN_INTERVAL = 1.0
_last_call = 0.0                    # 仅用于「查询之外」的单次调用兜底
_last_query_at = 0.0                # 上一次查询的进入时刻 —— 跨查询节流的记账点
_throttle_lock = threading.Lock()
_query_lock = threading.Lock()      # 一次只允许一个查询：spec §9「不同查询之间串行」


@contextlib.contextmanager
def query_scope():
    """一次查询 = 一个节流单位。端点在每次 `/api/library/search` 外面套一层。

    - **不同查询之间串行**（spec §9）：整段查询期间持有 `_query_lock`，后来的查询
      必须等前一个退出 —— 这是「唯一的一处并发只限同一次查询的两栏」的机械保证。
    - **相邻两次查询之间 ≥ `_MIN_INTERVAL`**（spec §9）：进入时距上次进入不足就等。
    - 查询**内部**的上游调用不互相节流（见 `_throttle`）：同一次查询的两个 scope
      本来就是并发发出的，在这里互相等等于把并发退回串行。
    """
    global _last_query_at
    with _query_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_query_at)
        if wait > 0:
            time.sleep(wait)
        _last_query_at = time.monotonic()
        yield


def _throttle() -> None:
    """单次上游调用的兜底节流。

    **查询内部直接放行**：`_query_lock` 被占着就说明正处在一次查询里，而同一次查询的
    两个 scope 是并发发出的 —— 若在这里互相等，并发就白做了（F-3 那 2.0 秒纯睡眠
    正是这么来的）。

    查询之外（直接调用本模块、或将来新增的独立调用点）仍按 `_last_call` 兜底，
    语义与改动前一致：距上次调用不足 `_MIN_INTERVAL` 就睡。
    """
    if _query_lock.locked():
        return
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
        data = resp.json()
        # 实测上游会返 HTTP 200 + 非对象 JSON（`null` / `[]` / 裸字符串）。
        # 放它过去，`data.get(...)` 就抛 AttributeError —— 那**不是** LibraryError，
        # 会越过 list_domains 的降级分支，把「不做领域收窄」变成整次检索失败。
        if not isinstance(data, dict):
            raise LibraryError("图书馆返回了非对象 JSON")
        return data
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

    `session=None`（端点走的就是这条）时**每次调用自建一个 `requests.Session`** ——
    `requests.Session` 不是线程安全的，两个 scope 是并发发出的，绝不能共用一个。
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

# domain 词表的缓存。spec §4.2 要求「缓存（**与 area 同策略**）」，所以照
# backend/library_areas.py 那套原样搬：7 天 TTL 的文件缓存 + 过期重抓 + 重抓失败
# 回退旧缓存 + 任何问题一律当没有、绝不抛错。缓存放**同一个应用数据目录**
# （与 area 缓存同址，同属这台机器的 CanvBridge 数据），只是换个文件名。
CACHE_TTL = 7 * 24 * 3600
CACHE_FILE = Path.home() / ".canvbridge" / "library_domains.json"


def _valid_domains(values) -> list[str]:
    """缓存条目形状校验（照 R13-a 对 area 缓存做的那条同一标准）。

    合法形状是**非空字符串列表**；只要有一条不是就整份当没有。缓存被人手改过、
    或跨版本 schema 漂移时（`{"domains": [1,2,3]}`），坏值绝不能流进 LLM 提示词。
    """
    if not isinstance(values, list) or not values:
        return []
    if not all(isinstance(v, str) and v.strip() for v in values):
        return []
    return values


def _read_cache() -> list[str]:
    """读缓存；不存在 / 坏 JSON / 形状不对 / 过期 —— 一律当没有。"""
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        domains = _valid_domains(raw.get("domains"))
        fetched_at = float(raw.get("fetched_at") or 0)
    except Exception:
        return []
    if not domains:
        return []
    if time.time() - fetched_at > CACHE_TTL:
        return []          # 过期。调用方会重抓；重抓失败时再单独回退到它
    return domains


def _read_stale_cache() -> list[str]:
    """不管过期与否都读出来 —— 只在重抓失败时当救兵。"""
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return _valid_domains(raw.get("domains"))
    except Exception:
        return []


def _write_cache(domains: list[str]) -> None:
    """写缓存失败不是错误（只读文件系统 / 没权限），下次再抓就是。"""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps({"fetched_at": time.time(), "domains": domains}, ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        pass


def list_domains(*, session=None) -> list[str]:
    """拉 Primo `domain` facet 的全部取值，供 LLM 挑「自动选库」（spec §4.2）。

    **值必须来自这里** —— ereslist 的库名（'ScienceDirect'）和 Primo 的
    domain 值（'Elsevier ScienceDirect Journals'）是两套词表，直接喂会 0 命中。

    **缓存（与 area 同策略）**：命中缓存就不打网络（词表极少变；每翻一页都去问一次
    上游既慢又没必要）。过期就重抓；重抓失败 / facet 没了则回退旧缓存。

    拿不到（网络失败 + 无旧缓存 / facet 没了且无旧缓存）→ 返回 `[]`，调用方据此
    不做库级收窄。**不抛错** —— 选库是增强，不是主干。
    """
    cached = _read_cache()
    if cached:
        return cached

    params = build_params(SCOPE_EXTERNAL, _DOMAIN_PROBE_Q)
    try:
        data = _get(SEARCH_PATH, params, session)
    except LibraryError:
        return _read_stale_cache()
    for facet in data.get("facets") or []:
        if facet.get("name") == "domain":
            domains = [v.get("value", "") for v in facet.get("values") or [] if v.get("value")]
            if not domains:
                break
            _write_cache(domains)
            return domains
    # 抓到了却没有词表（上游改版）→ 旧缓存也比没有强，与 area 的降级一致。
    return _read_stale_cache()
