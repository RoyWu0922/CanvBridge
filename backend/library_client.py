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

# 多个过滤表达式在**一个** `qInclude` 值里的连接符。厂商 UI 用的就是这个
# （`bundle.js`：`qInclude: …map(facetToString).join("|,|")`），**实测必须是它**：
# **异 facet** 写成重复的同名参数时 Primo 只认第一个、其余静默丢弃（见 `build_params`；
# 同 facet 且取值可叠加时是取交集，机制未查明，别把前者推广成一律）。
_FILTER_JOIN = "|,|"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CanvBridge/1.0)",
            "Accept": "application/json"}

# 单次上游 HTTP 调用的超时（连接 + 读）。**这是「一次合法的 `search()` 最久能跑多久」
# 的唯一来源** —— 门内的两栏既不取 `_query_lock` 也不过节流，所以一次合法调用的墙钟
# 上界就是这个值。端点层给 `fut.result()` 的兜底超时以它为基准（见 `main._RESULT_TIMEOUT`）。
HTTP_TIMEOUT = 30


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

    `q_include` / `q_exclude` 是**完整过滤表达式**列表（如
    `"facet_rtype,exact,articles"`），这里把它们拼成**一个**值，用 `|,|` 连接。

    ⚠️ **不能写成重复的同名参数**（那是旧实现，2026-09-20 实测推翻）：
    **不同 facet** 重复时 Primo **只认第一个，其余静默丢弃** —— 不报错、不 0 命中，
    只是过滤没生效（`facet_rtype,exact,books` + `facet_creationdate,exact,[1900 TO 1901]`：
    重复参数 → 25,490 = books 单独的值；两条**互换顺序** → 1 = 日期单独的值，
    可见只有第一个参数被读到）。连接成一个值 → 1，两个都生效。
    厂商 UI 也正是发一个值：`bundle.js` 里 `…map(facetToString).join("|,|")`。

    **连接后 facet 之间是 AND**（实测三条不同 facet → PC 715,116，都生效），
    **同一 facet 之间也是 AND**（`articles|,|books` → **0**）。

    ⚠️ 但「同一 facet 只给一个值」**不是本层替调用方保证的约束**，它只是
    `compile_filters` 对**互斥值**（rtype / domain）的取舍：同一 facet 里
    **可叠加**的值本来就该全发 —— `tlevel` 的 `peer_reviewed` + `open_access`
    连接后是**交集**，实测本馆 **21**（45 ∩ 250）、PC **533,967**
    （896,913 ∩ 953,408），都非空，而交集正是用户勾两个框时想要的语义。

    另有一处**机制未查明、只登记实测**：同样写重复参数，**同 facet** 的两个
    tlevel 值返回的是交集（21），并不服从上面那条「只认第一个」（那样应是 45）。
    成因不明，所以本层不据此做任何假设 —— 统一走 `|,|` 连接，因为那是厂商 UI
    的写法，且上述情形实测都对。取舍规则与实测数字详见 `compile_filters`。
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
        params["qInclude"] = _FILTER_JOIN.join(q_include)
    if q_exclude:
        params["qExclude"] = _FILTER_JOIN.join(q_exclude)
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
# 现在：查询 = 一个节流单位（query_scope），查询内部的调用由调用方**显式**标出
# （`search(..., inside_query=True)`），门外调用一律走 `out_of_query()`。
#
# **「是否查询内部」是显式传入的事实，不是从锁状态推断的。** 曾经 `_throttle()`
# 用 `_query_lock.locked()` 判「我在不在查询里」，那是拿一把**进程级**全局锁的状态
# 去猜「当前调用来自哪个调用点」——两件事根本不同。别的请求正持锁做双 scope 检索时，
# 门外的一次调用（`list_domains()` 的探针）会看到 `locked()==True` 而跳过等待，
# 实测上游在飞峰值 = 3（spec §9 说这是唯一的一处并发、只允许 2）。
#
# **「门外调用不与门内两栏重叠」这条不变量要求所有门外上游调用都进门。** 两个
# 取网络的入口在这里：`_get()`（= Primo）与众调用方。Primo 那条由 `_get` 自己套
# `out_of_query()`；**另一条在 `library_areas.load_areas` 里**（CityU 的
# `db_subject.php`，不走 `requests` 之外的任何共享路径），必须由调用方显式套 ——
# 见 `main.library_search` / `main.library_areas_endpoint`，也见 `out_of_query()`。
#
# 相邻两次**查询**之间的最小间隔（spec §9）。用户手动翻页天然慢于此，
# 这条只是兜住连点。测试里 monkeypatch 成 0，免得拖慢用例。
#
# **粒度如实说明**：这条间隔度量的是「相邻两次**查询进入**（= 相邻两次
# `/api/library/search` 请求）之间 ≥ `_MIN_INTERVAL`」，**不是**「相邻两次**上游
# HTTP 调用**之间」。spec §9 该行的原文是「相邻**翻页请求**之间 ≥ 1s」，一次翻页
# 就是一次查询，且同一次查询的两栏按 §9 本就允许并发 —— 若按上游调用逐个间隔，
# 反而会同时违反并发那条。翻页场景下（查询耗时 < 1s）两种读法结果一致；
# 差别只在「上一次查询耗时 > 1s 时，下一次查询进门不必再等」。
_MIN_INTERVAL = 1.0
_last_call = 0.0                    # 仅用于「查询之外」的单次调用兜底
_last_query_at = 0.0                # 上一次查询的进入时刻 —— 跨查询节流的记账点
_throttle_lock = threading.Lock()   # 保护 _last_call（供直接调 _throttle 的路径）
_query_lock = threading.Lock()      # 一次只允许一个查询：spec §9「不同查询之间串行」


@contextlib.contextmanager
def query_scope():
    """一次查询 = 一个节流单位。端点在每次 `/api/library/search` 外面套一层。

    - **不同查询之间串行**（spec §9）：整段查询期间持有 `_query_lock`，后来的查询
      必须等前一个退出 —— 这是「唯一的一处并发只限同一次查询的两栏」的机械保证。
    - **相邻两次查询之间 ≥ `_MIN_INTERVAL`**（spec §9）：进入时距上次进入不足就等。
      度量点是**查询进入**（= 一次 `/api/library/search` 请求），不是「相邻上游调用」，
      理由见 `_MIN_INTERVAL` 上面的注释。
    - 查询**内部**的上游调用不互相节流：同一次查询的两个 scope 本来就是并发发出的，
      在这里互相等等于把并发退回串行。放行靠**显式标志** —— 门内的提交点必须传
      `search(..., inside_query=True)`。
    - ⚠️ **忘记传的后果是永久死锁，不是「多睡一秒」。** 本函数在**端点线程**上持有
      非重入的 `_query_lock`；漏传标志的 pool worker 会走 `_get` 的默认路径
      （`out_of_query()`）去取同一把锁 ⇒ worker 永久阻塞，而端点在
      `fut.result()` 上等它 ⇒ 端点挂死、连接不释放。**默认值在这里是危险品**，
      所以 `inside_query` 是**必传参数**：漏传立刻 `TypeError`。
      （更正：本行原先写「忘记传的后果是多睡一秒」——复审用两组独立进程实测证伪。）
    - **不可重入**：`_query_lock` 是非重入锁，同一线程嵌套 `query_scope()` 会挂死。
      当前唯一调用点是 `main.library_search` 的端点，无嵌套路径。
    """
    global _last_query_at
    with _query_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_query_at)
        if wait > 0:
            time.sleep(wait)
        _last_query_at = time.monotonic()
        yield


def _throttle() -> None:
    """**查询之外**的单次上游调用兜底节流：距上次门外调用不足 `_MIN_INTERVAL` 就睡。

    查询**内部**的两栏根本不走这里 —— 它们由调用方用显式事实标出来
    （`search(..., inside_query=True)`），`_get` 见到就直接放行。

    **不再读 `_query_lock.locked()`**：那是拿全局锁的状态去推断「我来自哪个调用点」，
    而锁是**进程级**的 —— 另一个请求正持锁做双 scope 检索时，门外的调用会被误判成
    「查询内部」而跳过等待（实测峰值 3）。判据现在只有一个：调用方说了什么。
    """
    global _last_call
    with _throttle_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


@contextlib.contextmanager
def out_of_query():
    """「查询之外的上游调用」的纪律：**串行化 + 逐调用兜底节流**。

    作用域覆盖整次调用（含真正的 HTTP），所以任何两个门外调用之间、以及门外调用与
    门内的一次查询之间都不会重叠 ⇒ 上游在飞峰值恒 ≤ 2（spec §9「只允许两个 scope
    并发……这是唯一的一处并发」）。

    目前两处使用者，缺一不可：
      - `_get()` 的默认路径（= `list_domains()` 的探针）；
      - `main.library_search` / `main.library_areas_endpoint` 里
        `library_areas.load_areas()` 的抓取 —— 它走的是 `library_areas` 自己那条
        `requests` 路径，**不经过 `_get`**，不显式套这个门就完全不受约束
        （复审实测：冷 area 缓存 + 两请求并发时上游在飞峰值 = 3）。

    **不可重入、不可在 `query_scope()` 内部调用**：`_query_lock` 是非重入锁，
    同一线程再进来会自锁。
    """
    with _query_lock:
        _throttle()
        yield


def _get(path: str, params: dict, session, *, inside_query: bool) -> dict:
    """发一次请求并把传输层异常统一成 LibraryError。

    `inside_query` 是**调用方显式声明的事实**，不是从锁状态猜的，而且**必传**
    （没有默认值）：

    - `True`：本次调用是某次查询的两栏之一（`main.library_search` 的
      `ThreadPoolExecutor` 两个提交点传的）。直接放行 —— 两栏本就该并发。
    - `False`：**查询之外**的调用（`list_domains()` 的探针、直接调用本模块）。
      走 `out_of_query()`：逐调用兜底节流 + **整段调用期间持有 `_query_lock`**。
      代价：查询外的调用会排在正在进行的查询之后（单用户本地工具，可接受；
      而且它本来也要在 `query_scope()` 门口再排一次）。

    **为什么必传而不是默认 `False`**：默认值在这里是危险品。`query_scope()` 在
    **端点线程**上持有非重入的 `_query_lock`，端点在 `fut.result()` 上等 worker；
    此时漏传标志的 worker 走这条默认路径 ⇒ 取同一把锁 ⇒ **worker 与端点互等，
    请求永久挂死**（复审：两组独立进程实测，漏传 6 秒不返回，传了立刻 ok）。
    必传之后漏传立刻 `TypeError`（`main.library_search` 的提交点在 `pool.submit`
    时就把参数交出去了，worker 里抛出的 TypeError 由 `fut.result()` 原样带出来，
    端点的 `except Exception` 转成 200 + JSON）。端点还另有一道 `fut.result(timeout=)`
    兜底，两道防线见 `main._RESULT_TIMEOUT`。

    **不可在 `query_scope()` 内部用 `inside_query=False` 调用**（同一线程会自锁；
    换个线程则会与端点线程互等）。
    """
    if inside_query:
        return _http_get(path, params, session)
    with out_of_query():
        return _http_get(path, params, session)


def _http_get(path: str, params: dict, session) -> dict:
    """真正的那一次 HTTP 调用。节流 / 并发策略全在 `_get` 里，这里只管传输。"""
    own = session is None
    s = session or requests.Session()
    try:
        resp = s.get(PRIMO_BASE + path, params=params, headers=_HEADERS,
                     timeout=HTTP_TIMEOUT)
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
           session=None, inside_query: bool) -> dict:
    """查一个 scope 的一页。

    **页码越界在这里就拒** —— 上限必须由后端挡（spec §9）。
    `has_more` = 「本页取满 且 page < MAX_PAGES」，由后端算好给前端。

    `session=None`（端点走的就是这条）时**每次调用自建一个 `requests.Session`** ——
    `requests.Session` 不是线程安全的，两个 scope 是并发发出的，绝不能共用一个。

    `inside_query`（**必传**）声明「本次调用是不是 `query_scope()` 内的两栏之一」——
    由调用方显式给出，**不由本函数从锁状态推断、也不给默认值**（见 `_get`：
    漏传的后果是永久死锁，所以这里要的是 `TypeError` 而不是一个「猜错就挂死」的默认值）。
    `True` = 本次是门内两栏之一；`False` = 查询之外的调用（受逐调用节流，
    且不与门内的两栏并发）。
    """
    if page < 1 or page > MAX_PAGES:
        raise LibraryError(f"页码越界：{page}（允许 1..{MAX_PAGES}）")
    params = build_params(scope, q, q_include=q_include, q_exclude=q_exclude, page=page)
    data = _get(SEARCH_PATH, params, session, inside_query=inside_query)
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

    **探针是「查询之外」的调用**：走 `_get(... , inside_query=False)`（逐调用节流 +
    整段持有 `_query_lock`），所以别的请求正在门内做双 scope 检索时，本探针会排队等它，
    不会把上游在飞并发顶到 3。**不可在 `query_scope()` 内部调用**（会自锁）。
    """
    cached = _read_cache()
    if cached:
        return cached

    params = build_params(SCOPE_EXTERNAL, _DOMAIN_PROBE_Q)
    try:
        data = _get(SEARCH_PATH, params, session, inside_query=False)
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
