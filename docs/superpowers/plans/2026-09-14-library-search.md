# 图书馆检索（第一阶段）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户在新增的「图书馆」一级页里输入任意语言的自然语言，系统转成结构化检索式，在 CityU 馆藏与外部数据库两处检索，分两块展示可点链接，支持翻页。

**Architecture:** 后端三个新模块——`library_client`（Primo 公开 API 客户端）、`library_areas`（28 个领域的 HTML 抓取 + 缓存）、`library_query`（自然语言 ↔ 结构化检索式）；`main.py` 加两个端点。前端加一个一级导航页。**全程零登录。**

**Tech Stack:** Python 3 / FastAPI / pydantic / requests（后端）；原生 JS，无构建、无工具链（前端）。

**Spec:** `docs/superpowers/specs/2026-09-14-library-search-design.md`

## Global Constraints

- **无构建、无 JS 工具链。** `frontend/index.html:354-357` 四个经典 `<script>`，加载顺序硬性：`i18n.js → util.js → app.js → shell.js`。
- **新增顶层 `let`/`const` 前，必须先 grep `frontend/` 其余三个 JS 文件**——重名即 `SyntaxError`，整页白屏。顶层 `function` 重名 `node --check` **查不出来**，必须手工 grep。
- **i18n 新增 key 必须 zh / en 两侧同步**。当前各 **359** 键。死键基线 **21**，只许持平或下降。
- **HTML id 基线 124**（新增 id 允许，但所有 `$("id")` 必须能解析到）。
- **后端错误一律返回 200 + JSON**，判别字段是 `Content-Type: application/json`。前端 `api()`（`frontend/util.js:27`）已按此约定解析，失败时返回 `{ok:false, error}`。
- **测试不打真实网络。** 一律用合成 fixture + `monkeypatch`（范式见 `tests/test_canvas_client.py` 的 `_Resp`/`_Session`）。
- **新测试必须做变异检验**——临时改坏实现，确认测试真的失败，再还原。
- **翻页绝不重新调用 LLM。** 第 1 页解析一次，之后前端回传 `parsed`。
- **上限由后端挡**：`PAGE_SIZE = 20`、`MAX_PAGES = 5`；`page > MAX_PAGES` 直接拒绝，不能只靠前端不显示按钮。
- **不做定时/后台任务、不做全量收割。**

**门禁（每个任务收尾都要全绿）：**

```bash
pytest -q
node tools/check_i18n_keys.mjs
node tools/check_dom_ids.mjs
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/four.js && node --check /tmp/four.js
```

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `backend/library_client.py` | **新增。** Primo 公开 API：参数拼装、检索、分页、结果归一化、domain 词表。不含任何 HTML 解析。 |
| `backend/library_areas.py` | **新增。** 28 个学科领域的 HTML 抓取、解析、缓存、"拿不到当没有"。 |
| `backend/library_query.py` | **新增。** 自然语言 → 结构化检索式；`parsed` 校验归一化；编译成 Primo 过滤表达式。 |
| `backend/main.py` | **改。** 加两个端点。 |
| `frontend/index.html` | **改。** 导航项 + `#page-library`。 |
| `frontend/shell.js` | **改。** `PAGES` 数组 + `PAGE_INIT`。 |
| `frontend/app.js` | **改。** 图书馆页逻辑。 |
| `frontend/i18n.js` | **改。** zh / en 各加一批 key。 |
| `frontend/app.css` | **改。** 双栏结果样式。 |

---

### Task 1: Primo 参数拼装与结果归一化（纯函数）

**Files:**
- Create: `backend/library_client.py`
- Test: `tests/test_library_client.py`

**Interfaces:**
- Consumes: 无（本任务只写纯函数，不发请求）
- Produces:
  - `PRIMO_BASE: str`、`PRIMO_VID: str`、`SEARCH_PATH: str`
  - `SCOPE_LOCAL: str = "MyInstitution"`、`SCOPE_EXTERNAL: str = "CentralIndex"`
  - `PAGE_SIZE: int = 20`、`MAX_PAGES: int = 5`
  - `class LibraryError(RuntimeError)`
  - `record_url(record_id: str, context: str = "L") -> str`
  - `normalize_doc(doc: dict) -> dict` → `{"title","creator","year","type","journal","doi","language","source","record_id","url"}`
  - `build_params(scope: str, q: str, *, q_include: list[str] | None = None, q_exclude: list[str] | None = None, page: int = 1) -> dict`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_library_client.py`：

```python
"""Primo 客户端测试。全部用合成 JSON，不打真实网络。"""
from backend import library_client


# ---------- build_params：三个实测坑 + 过滤表达式 ----------

def test_build_params_uses_colon_qualified_vid():
    """vid 必须带冒号。实测：只写 852JULAC_CUH 会静默返回 0 命中。"""
    p = library_client.build_params("MyInstitution", "any,contains,ml")
    assert p["vid"] == "852JULAC_CUH:CUH"


def test_build_params_never_sends_inst():
    """实测：传了 inst 参数就 0 命中。这个键永远不能出现。"""
    p = library_client.build_params("MyInstitution", "any,contains,ml")
    assert "inst" not in p


def test_build_params_scope_is_present_and_verbatim():
    """scope 必填；漏掉是 HTTP 400 + 空 body，很难看出原因。"""
    assert library_client.build_params("CentralIndex", "q")["scope"] == "CentralIndex"
    assert library_client.build_params("MyInstitution", "q")["scope"] == "MyInstitution"


def test_build_params_offset_follows_page():
    """page 从 1 起，offset = (page-1) * PAGE_SIZE。"""
    assert library_client.build_params("MyInstitution", "q", page=1)["offset"] == "0"
    assert library_client.build_params("MyInstitution", "q", page=3)["offset"] == "40"
    assert library_client.build_params("MyInstitution", "q")["limit"] == "20"


def test_build_params_omits_empty_filter_keys():
    """没有过滤条件时不能出现 qInclude/qExclude 键（空值传给 Primo 会 0 命中）。"""
    p = library_client.build_params("MyInstitution", "q")
    assert "qInclude" not in p and "qExclude" not in p


def test_build_params_lists_filters_as_repeated_params():
    p = library_client.build_params(
        "CentralIndex", "q",
        q_include=["rtype,exact,articles", "tlevel,exact,peer_reviewed"],
        q_exclude=["attribute,exact,retracted_publication"])
    assert p["qInclude"] == ["rtype,exact,articles", "tlevel,exact,peer_reviewed"]
    assert p["qExclude"] == ["attribute,exact,retracted_publication"]


# ---------- normalize_doc：实测字段形状全是怪胎 ----------

def _doc(**pnx_parts):
    return {"context": "L", "pnx": pnx_parts}


def test_normalize_doc_handles_list_valued_fields():
    """实测：display.* 基本都是**列表**，不是字符串。"""
    d = _doc(
        display={"title": ["Automation of Feedback Control"],
                 "type": ["dissertation"],
                 "language": ["eng"],
                 "source": ["Alma"]},
        control={"recordid": ["alma991030012933203408"]},
    )
    item = library_client.normalize_doc(d)
    assert item["title"] == "Automation of Feedback Control"
    assert item["type"] == "dissertation"
    assert item["language"] == "eng"
    assert item["record_id"] == "alma991030012933203408"


def test_normalize_doc_strips_marc_subfield_noise():
    """实测 creator 长这样：'Hughes, Isaac S., author.$$QHughes, Isaac S.'
    $$ 后面是 MARC 子字段，不是人看得懂的东西，必须切掉。"""
    d = _doc(display={"title": ["T"], "creator": ["Hughes, Isaac S., author.$$QHughes, Isaac S."]},
             control={"recordid": ["alma1"]})
    assert library_client.normalize_doc(d)["creator"] == "Hughes, Isaac S., author."


def test_normalize_doc_year_prefers_addata_date_and_extracts_year():
    """日期可能在 addata.date（['2024'] 或 ['2024-05-01']），也可能只在 display.creationdate。"""
    a = _doc(display={"title": ["T"]}, addata={"date": ["2024-05-01"]},
             control={"recordid": ["alma1"]})
    assert library_client.normalize_doc(a)["year"] == "2024"
    b = _doc(display={"title": ["T"], "creationdate": ["2019"]}, control={"recordid": ["alma2"]})
    assert library_client.normalize_doc(b)["year"] == "2019"


def test_normalize_doc_tolerates_missing_and_none_fields():
    """实测 addata.doi / display.ispartof 经常整个缺失或为 None —— 不能抛错。"""
    d = _doc(display={"title": ["T"]}, addata={"doi": None, "jtitle": None},
             control={"recordid": ["alma1"]})
    item = library_client.normalize_doc(d)
    assert item["doi"] == "" and item["journal"] == ""
    assert item["title"] == "T"


def test_normalize_doc_journal_falls_back_to_ispartof():
    """期刊名优先 addata.jtitle；没有就退回 display.ispartof。"""
    d = _doc(display={"title": ["T"], "ispartof": ["Nature"]},
             addata={"jtitle": None}, control={"recordid": ["alma1"]})
    assert library_client.normalize_doc(d)["journal"] == "Nature"


def test_normalize_doc_survives_completely_empty_doc():
    """上游给个空壳也不能抛 —— 抛了整页结果就没了。"""
    item = library_client.normalize_doc({})
    assert item["title"] == "" and item["url"] == "" and item["record_id"] == ""


# ---------- record_url ----------

def test_record_url_uses_record_own_context():
    url = library_client.record_url("alma991030012933203408", "L")
    assert url.startswith("https://julac-cuh.primo.exlibrisgroup.com/discovery/fulldisplay?")
    assert "docid=alma991030012933203408" in url
    assert "vid=852JULAC_CUH%3ACUH" in url or "vid=852JULAC_CUH:CUH" in url
    assert "context=L" in url


def test_normalize_doc_url_empty_when_no_record_id():
    """没 recordid 就不能拼链接 —— 拼出个半截 URL 比没链接更糟。"""
    d = _doc(display={"title": ["T"]}, control={})
    assert library_client.normalize_doc(d)["url"] == ""
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_library_client.py -q
```

Expected: `ModuleNotFoundError: No module named 'backend.library_client'`

- [ ] **Step 3: 实现**

创建 `backend/library_client.py`：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_library_client.py -q
```

Expected: 全部 PASS

- [ ] **Step 5: 变异检验**

临时把 `build_params` 里的 `"vid": PRIMO_VID` 改成 `"vid": "852JULAC_CUH"`，跑测试，确认 `test_build_params_uses_colon_qualified_vid` **失败**。再把 `params["qInclude"] = list(q_include)` 的 `if q_include:` 守卫去掉，确认 `test_build_params_omits_empty_filter_keys` **失败**。两次都还原。

- [ ] **Step 6: 提交**

```bash
git add backend/library_client.py tests/test_library_client.py
git commit -m "feat(library): Primo 参数拼装与结果归一化（三个实测坑固化）"
```

---

### Task 2: Primo 检索、分页与 domain 词表

**Files:**
- Modify: `backend/library_client.py`（追加）
- Test: `tests/test_library_client.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `build_params`、`normalize_doc`、`PAGE_SIZE`、`MAX_PAGES`、`LibraryError`
- Produces:
  - `search(scope: str, q: str, *, q_include=None, q_exclude=None, page: int = 1, session=None) -> dict`
    → `{"scope": str, "items": list[dict], "count": int, "page": int, "has_more": bool}`
  - `list_domains(*, session=None) -> list[str]` → `[]` 表示拿不到
  - 模块级 `_MIN_INTERVAL: float`（测试可 monkeypatch 成 0）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_library_client.py`：

```python
# ---------- search：分页、has_more、错误 ----------

class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class _Session:
    """记录调用参数的假 session。"""

    def __init__(self, data):
        self._data, self.calls = data, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        return _Resp(self._data)

    def close(self):
        pass


def _search_payload(n_docs, total=100):
    return {"info": {"total": total},
            "docs": [{"context": "L",
                      "pnx": {"display": {"title": [f"T{i}"]},
                              "control": {"recordid": [f"alma{i}"]}}}
                     for i in range(n_docs)]}


def test_search_returns_normalized_items(monkeypatch):
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    s = _Session(_search_payload(3))
    r = library_client.search("MyInstitution", "any,contains,ml", session=s)
    assert [i["title"] for i in r["items"]] == ["T0", "T1", "T2"]
    assert r["count"] == 100 and r["page"] == 1


def test_search_hits_the_public_pnxs_endpoint(monkeypatch):
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    s = _Session(_search_payload(1))
    library_client.search("CentralIndex", "q", session=s)
    assert s.calls[0]["url"] == library_client.PRIMO_BASE + library_client.SEARCH_PATH
    assert s.calls[0]["params"]["scope"] == "CentralIndex"


def test_search_has_more_true_only_when_full_page_and_under_cap(monkeypatch):
    """has_more 由后端算，前端不自己推算 —— 否则上限一改前端就骗人。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    full = library_client.search("MyInstitution", "q", session=_Session(_search_payload(20)))
    assert full["has_more"] is True
    short = library_client.search("MyInstitution", "q", session=_Session(_search_payload(7)))
    assert short["has_more"] is False


def test_search_has_more_false_on_last_allowed_page(monkeypatch):
    """第 5 页（= MAX_PAGES）即使取满也不再有下一页。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    r = library_client.search("MyInstitution", "q", page=5,
                              session=_Session(_search_payload(20)))
    assert r["has_more"] is False


def test_search_rejects_page_above_max(monkeypatch):
    """上限是**后端**挡的。光靠前端不显示按钮，改个请求就绕过去了。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    import pytest
    with pytest.raises(library_client.LibraryError):
        library_client.search("MyInstitution", "q", page=6, session=_Session(_search_payload(1)))


def test_search_rejects_page_below_one(monkeypatch):
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    import pytest
    with pytest.raises(library_client.LibraryError):
        library_client.search("MyInstitution", "q", page=0, session=_Session(_search_payload(1)))


def test_search_wraps_transport_errors(monkeypatch):
    """requests 的异常要包成 LibraryError，端点层只认这一种。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    import pytest, requests

    class _Boom:
        def get(self, *a, **k):
            raise requests.ConnectionError("no route")

        def close(self):
            pass

    with pytest.raises(library_client.LibraryError):
        library_client.search("MyInstitution", "q", session=_Boom())


def test_search_survives_missing_info_block(monkeypatch):
    """上游少给 info 不能让整次检索崩掉。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    r = library_client.search("MyInstitution", "q", session=_Session({"docs": []}))
    assert r["count"] == 0 and r["has_more"] is False


# ---------- list_domains ----------

def test_list_domains_reads_domain_facet(monkeypatch):
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    payload = {"info": {"total": 1}, "docs": [], "facets": [
        {"name": "lang", "values": [{"value": "eng", "count": "5"}]},
        {"name": "domain", "values": [{"value": "ScienceDirect", "count": "3"},
                                      {"value": "MEDLINE", "count": "2"}]}]}
    assert library_client.list_domains(session=_Session(payload)) == ["ScienceDirect", "MEDLINE"]


def test_list_domains_returns_empty_when_facet_absent(monkeypatch):
    """没有 domain facet（上游改版）→ 空列表，不是抛错。调用方据此不做收窄。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    assert library_client.list_domains(session=_Session({"docs": [], "facets": []})) == []
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_library_client.py -q
```

Expected: FAIL — `AttributeError: module 'backend.library_client' has no attribute 'search'`

- [ ] **Step 3: 实现**

追加到 `backend/library_client.py`：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_library_client.py -q
```

Expected: 全部 PASS

- [ ] **Step 5: 变异检验**

把 `search` 里的 `page < MAX_PAGES` 改成 `True`，确认 `test_search_has_more_false_on_last_allowed_page` **失败**；把 `len(docs) >= PAGE_SIZE` 改成 `True`，确认 `test_search_has_more_true_only_when_full_page_and_under_cap` **失败**；把越界检查整段删掉，确认两个 reject 用例**失败**。全部还原。

- [ ] **Step 6: 提交**

```bash
git add backend/library_client.py tests/test_library_client.py
git commit -m "feat(library): Primo 检索、分页 has_more、domain 词表（页码上限后端挡）"
```

---

### Task 3: 学科领域清单（抓取 / 解析 / 缓存 / 拿不到当没有）

**Files:**
- Create: `backend/library_areas.py`
- Test: `tests/test_library_areas.py`

**Interfaces:**
- Consumes: 无（独立模块）
- Produces:
  - `AREAS_URL: str`
  - `parse_areas(html: str) -> list[dict]` → `[{"code": str, "name": str}, ...]`，解析不出返回 `[]`
  - `load_areas(*, force: bool = False, session=None) -> dict` → `{"available": bool, "areas": list[dict]}`
  - `CACHE_FILE: pathlib.Path`、`CACHE_TTL: int`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_library_areas.py`：

```python
"""领域清单测试。用合成 HTML —— 真实页面结构见 spec §4。"""
from backend import library_areas

# 真实页面（db_subject.php）的形态：<a href="//app3.../index.php?type=db&format=subj&subj=comp">Computer Science</a>
_HTML = """
<ul>
  <li><a href="//app3.lib.cityu.edu.hk/ereslist/index.php?type=db&format=subj&subj=comp">Computer Science</a></li>
  <li><a href="//app3.lib.cityu.edu.hk/ereslist/index.php?type=db&format=subj&subj=law">Law</a></li>
  <li><a href="//app3.lib.cityu.edu.hk/ereslist/index.php?type=db&format=subj&subj=busgen">Business</a></li>
  <li><a href="//app3.lib.cityu.edu.hk/ereslist/index.php?type=db&format=subj&subj=human">Arts &amp; Humanities</a></li>
</ul>
"""


def test_parse_areas_extracts_code_and_name():
    areas = library_areas.parse_areas(_HTML)
    assert {"code": "comp", "name": "Computer Science"} in areas
    assert {"code": "law", "name": "Law"} in areas
    assert len(areas) == 4


def test_parse_areas_unescapes_html_entities():
    """页面里写的是 'Arts &amp; Humanities'，显示给用户必须是 'Arts & Humanities'。"""
    names = {a["code"]: a["name"] for a in library_areas.parse_areas(_HTML)}
    assert names["human"] == "Arts & Humanities"


def test_parse_areas_dedupes_keeping_first_order():
    """同一 code 出现多次（页面有重复链接）只留一条，且保持页面顺序。"""
    html = _HTML + _HTML
    areas = library_areas.parse_areas(html)
    assert [a["code"] for a in areas] == ["comp", "law", "busgen", "human"]


def test_parse_areas_returns_empty_on_redesign():
    """改版 / 被拦 / 拿回一坨无关 HTML → 空列表，**不抛错**。
    调用方据此返回 available=False，前端整个不渲染选择器。"""
    assert library_areas.parse_areas("<html><body>nothing here</body></html>") == []
    assert library_areas.parse_areas("") == []


def test_parse_areas_skips_links_with_empty_text():
    html = '<a href="/ereslist/index.php?type=db&format=subj&subj=x"></a>'
    assert library_areas.parse_areas(html) == []


# ---------- load_areas：缓存与降级 ----------

class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, text=None, boom=False):
        self.text, self.boom, self.calls = text, boom, 0

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        if self.boom:
            raise RuntimeError("network down")
        return _Resp(self.text)

    def close(self):
        pass


def test_load_areas_fetches_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(library_areas, "CACHE_FILE", tmp_path / "areas.json")
    s = _Session(text=_HTML)
    r = library_areas.load_areas(session=s)
    assert r["available"] is True and len(r["areas"]) == 4
    assert s.calls == 1

    # 第二次走缓存，不再打网络
    s2 = _Session(text=_HTML)
    r2 = library_areas.load_areas(session=s2)
    assert r2["available"] is True and s2.calls == 0


def test_load_areas_force_bypasses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(library_areas, "CACHE_FILE", tmp_path / "areas.json")
    library_areas.load_areas(session=_Session(text=_HTML))
    s = _Session(text=_HTML)
    library_areas.load_areas(force=True, session=s)
    assert s.calls == 1


def test_load_areas_returns_unavailable_on_network_failure(tmp_path, monkeypatch):
    """网络挂了 → available=False，**不抛错**。搜索本身不受影响。"""
    monkeypatch.setattr(library_areas, "CACHE_FILE", tmp_path / "areas.json")
    r = library_areas.load_areas(session=_Session(boom=True))
    assert r == {"available": False, "areas": []}


def test_load_areas_falls_back_to_stale_cache_on_failure(tmp_path, monkeypatch):
    """有旧缓存 + 网络挂了 → 用旧的（标 available=True）。总比让选择器消失好。"""
    cache = tmp_path / "areas.json"
    monkeypatch.setattr(library_areas, "CACHE_FILE", cache)
    library_areas.load_areas(session=_Session(text=_HTML))

    import json, time
    raw = json.loads(cache.read_text(encoding="utf-8"))
    raw["fetched_at"] = time.time() - library_areas.CACHE_TTL - 10   # 弄成过期
    cache.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    r = library_areas.load_areas(session=_Session(boom=True))
    assert r["available"] is True and len(r["areas"]) == 4


def test_load_areas_unavailable_when_parse_yields_nothing(tmp_path, monkeypatch):
    """抓到了但解析不出东西（改版）→ 当拿不到，别给个空选择器。"""
    monkeypatch.setattr(library_areas, "CACHE_FILE", tmp_path / "areas.json")
    r = library_areas.load_areas(session=_Session(text="<html>nothing</html>"))
    assert r == {"available": False, "areas": []}


def test_load_areas_survives_corrupt_cache(tmp_path, monkeypatch):
    """缓存文件被写坏不能炸 —— 当没有，重新抓。"""
    cache = tmp_path / "areas.json"
    cache.write_text("{{{ not json", encoding="utf-8")
    monkeypatch.setattr(library_areas, "CACHE_FILE", cache)
    r = library_areas.load_areas(session=_Session(text=_HTML))
    assert r["available"] is True and len(r["areas"]) == 4
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_library_areas.py -q
```

Expected: `ModuleNotFoundError: No module named 'backend.library_areas'`

- [ ] **Step 3: 实现**

创建 `backend/library_areas.py`：

```python
"""图书馆学科领域清单。

⚠️ **这套分类没有 API。** 实测 `format=json` / `output=json` 都是假的
（照样返回 text/html），只能解析 HTML。这是本设计里唯一脆弱的依赖，
所以**拿不到就当没有**，绝不影响检索本身（spec §4.3）。

来源注意：是 `db_subject.php`。`db_subject.htm` 只有 63 字节，是一段
`window.location.href = "db_subject.php"` 的跳转桩，抓它等于什么都没抓到。
"""
from __future__ import annotations

import html as _html
import json
import re
import time
from pathlib import Path

import requests

AREAS_URL = "https://app3.lib.cityu.edu.hk/ereslist/db_subject.php"

CACHE_TTL = 7 * 24 * 3600          # 7 天。领域分类极少变，抓太勤没意义还招人烦。
CACHE_FILE = Path.home() / ".canvbridge" / "library_areas.json"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CanvBridge/1.0)"}

# <a href="//app3.../index.php?type=db&format=subj&subj=comp">Computer Science</a>
_AREA_LINK = re.compile(
    r'<a[^>]+href="[^"]*[?&]subj=([A-Za-z0-9_]+)"[^>]*>(.*?)</a>',
    re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def parse_areas(html: str) -> list[dict]:
    """解析出 `[{"code","name"}]`，保持页面顺序，同 code 去重留首条。

    解析不出任何一条 → 返回 `[]`（调用方当「拿不到」处理，**不抛错**）。
    页面改版、被拦、返回无关 HTML 都走这条路。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for code, inner in _AREA_LINK.findall(html or ""):
        code = (code or "").strip().lower()
        name = _WS.sub(" ", _html.unescape(_TAGS.sub("", inner))).strip()
        if not code or not name or code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": name})
    return out


def _read_cache() -> list[dict]:
    """读缓存；任何问题（不存在 / 坏 JSON / 形状不对 / 过期）一律当没有。"""
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        areas = raw.get("areas")
        fetched_at = float(raw.get("fetched_at") or 0)
    except Exception:
        return []
    if not isinstance(areas, list) or not areas:
        return []
    if time.time() - fetched_at > CACHE_TTL:
        return []          # 过期。调用方会重抓；重抓失败时再单独回退到它
    return areas


def _read_stale_cache() -> list[dict]:
    """不管过期与否都读出来 —— 只在重抓失败时当救兵。"""
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        areas = raw.get("areas")
        return areas if isinstance(areas, list) and areas else []
    except Exception:
        return []


def _write_cache(areas: list[dict]) -> None:
    """写缓存失败不是错误（只读文件系统 / 没权限），下次再抓就是。"""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps({"fetched_at": time.time(), "areas": areas}, ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        pass


def load_areas(*, force: bool = False, session=None) -> dict:
    """返回 `{"available": bool, "areas": [...]}`。

    `available=False` **不是错误** —— 前端据此**整个不渲染 area 选择器**
    （spec §4.3，用户裁定：拿不到就不显示）。绝不让它拖垮检索。
    """
    if not force:
        cached = _read_cache()
        if cached:
            return {"available": True, "areas": cached}

    own = session is None
    s = session or requests.Session()
    try:
        resp = s.get(AREAS_URL, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        areas = parse_areas(resp.text)
    except Exception:
        areas = []
    finally:
        if own:
            s.close()

    if not areas:
        stale = _read_stale_cache()      # 抓失败/改版了 → 旧缓存也比没有强
        if stale:
            return {"available": True, "areas": stale}
        return {"available": False, "areas": []}

    _write_cache(areas)
    return {"available": True, "areas": areas}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_library_areas.py -q
```

Expected: 全部 PASS

- [ ] **Step 5: 变异检验**

把 `_read_cache` 里的 TTL 判断改成 `return areas`（永不过期），确认 `test_load_areas_falls_back_to_stale_cache_on_failure` **失败**（它依赖"过期 → 重抓 → 失败 → 回退"这条链）。把 `load_areas` 末尾的 `if not areas: ... return {"available": False, "areas": []}` 改成 `return {"available": True, "areas": areas}`，确认 `test_load_areas_unavailable_when_parse_yields_nothing` **失败**。还原。

- [ ] **Step 6: 提交**

```bash
git add backend/library_areas.py tests/test_library_areas.py
git commit -m "feat(library): 28 个学科领域清单（HTML 抓取 + 7 天缓存 + 拿不到当没有）"
```

---

### Task 4: 自然语言 → 结构化检索式

**Files:**
- Create: `backend/library_query.py`
- Test: `tests/test_library_query.py`

**Interfaces:**
- Consumes: `llm_client._call_chat`、`llm_client._parse_json`（`backend/llm_client.py:79`、`:114`）
- Produces:
  - `EMPTY_PARSED: dict`
  - `KNOWN_RTYPE: list[str]`
  - `build_prompt(text: str, area: str | None, area_names: dict[str, str], domains: list[str]) -> str`
  - `validate_parsed(raw: dict | None, known_domains: list[str] | None = None) -> dict`
  - `fallback_parsed(text: str) -> dict`
  - `parse_natural(base_url, api_key, model, text, *, area=None, area_names=None, domains=None) -> tuple[dict, list[str]]` → `(parsed, warnings)`
  - `build_q(parsed: dict) -> str`
  - `compile_filters(parsed: dict, scope: str) -> tuple[list[str], list[str]]` → `(q_include, q_exclude)`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_library_query.py`：

```python
"""检索式解析与编译。LLM 一律用替身，不打真实网络。"""
from backend import llm_client, library_query


# ---------- validate_parsed：客户端可传的输入，必须归一化 ----------

def test_validate_parsed_fills_every_key():
    """缺键不能 KeyError —— 前端传回来的 parsed 可能被改过。"""
    p = library_query.validate_parsed({})
    assert set(p) == set(library_query.EMPTY_PARSED)
    assert p["keywords"] == [] and p["rtype"] == [] and p["domains"] == []


def test_validate_parsed_none_returns_empty():
    assert library_query.validate_parsed(None) == library_query.EMPTY_PARSED


def test_validate_parsed_coerces_year_strings():
    """LLM 常把年份写成字符串。'2020' 要变成 2020；乱写就丢掉。"""
    assert library_query.validate_parsed({"year_from": "2020"})["year_from"] == 2020
    assert library_query.validate_parsed({"year_from": "abc"})["year_from"] is None
    assert library_query.validate_parsed({"year_from": 1999.7})["year_from"] == 1999


def test_validate_parsed_drops_out_of_range_years():
    assert library_query.validate_parsed({"year_from": 1200})["year_from"] is None
    assert library_query.validate_parsed({"year_to": 9999})["year_to"] is None


def test_validate_parsed_coerces_scalar_to_list():
    """LLM 有时给字符串而不是列表。'articles' → ['articles']。"""
    assert library_query.validate_parsed({"rtype": "articles"})["rtype"] == ["articles"]
    assert library_query.validate_parsed({"keywords": "machine learning"})["keywords"] == ["machine learning"]


def test_validate_parsed_drops_unknown_rtype():
    """rtype 的值是**复数**且有限枚举。LLM 编一个出来 → 丢掉，别让上游 0 命中。"""
    p = library_query.validate_parsed({"rtype": ["articles", "banana", "books"]})
    assert p["rtype"] == ["articles", "books"]


def test_validate_parsed_drops_unknown_domains():
    """spec §6：不在词表里的 domain 必须**剔除**（不是报错）——
    LLM 编一个 Primo 里不存在的平台名，一过滤就 0 命中。"""
    p = library_query.validate_parsed({"domains": ["MEDLINE", "Fake Platform"]},
                                      known_domains=["MEDLINE", "ScienceDirect"])
    assert p["domains"] == ["MEDLINE"]


def test_validate_parsed_keeps_domains_when_no_catalog():
    """拿不到词表（[]）时不做剔除 —— 剔了等于禁用选库。"""
    p = library_query.validate_parsed({"domains": ["MEDLINE"]}, known_domains=[])
    assert p["domains"] == ["MEDLINE"]


def test_validate_parsed_caps_list_lengths():
    """畸形/超长输入不能变成超长上游请求。"""
    p = library_query.validate_parsed({"keywords": [f"k{i}" for i in range(100)]})
    assert len(p["keywords"]) <= 12


def test_validate_parsed_normalizes_areas_to_lowercase():
    assert library_query.validate_parsed({"areas": ["COMP", "Law"]})["areas"] == ["comp", "law"]


def test_validate_parsed_truncates_long_strings():
    """超长单值同样要截 —— 用户/LLM 都可能塞进来。"""
    p = library_query.validate_parsed({"keywords": ["x" * 5000]})
    assert all(len(k) <= 200 for k in p["keywords"])


# ---------- build_q / compile_filters ----------

def test_build_q_joins_keywords_with_or():
    q = library_query.build_q({"keywords": ["a", "b"], "synonyms": ["c"]})
    assert q == "any,contains,a b c"


def test_build_q_falls_back_to_any_when_empty():
    """一个词都没有时也得给个合法 q —— 空 q 上游会 400。"""
    assert library_query.build_q({"keywords": [], "synonyms": []}).startswith("any,contains,")


def test_compile_filters_uses_plural_rtype():
    """实测：rtype 的值是复数。'database' 返回 0，'databases' 才对。"""
    inc, exc = library_query.compile_filters({"rtype": ["articles"]}, "CentralIndex")
    assert "rtype,exact,articles" in inc


def test_compile_filters_excludes_retracted():
    inc, exc = library_query.compile_filters({"exclude_retracted": True}, "CentralIndex")
    assert "attribute,exact,retracted_publication" in exc


def test_compile_filters_puts_pc_only_filters_on_external_scope_only():
    """spec §6 关键：tlevel/attribute 是 Primo Central 独有的 facet。
    套到 MyInstitution（本馆馆藏）上会**静默返回 0** —— 馆藏那栏会整块空掉。"""
    parsed = {"peer_reviewed_only": True, "open_access_only": True, "exclude_retracted": True}
    local_inc, local_exc = library_query.compile_filters(parsed, "MyInstitution")
    ext_inc, ext_exc = library_query.compile_filters(parsed, "CentralIndex")
    assert all("tlevel" not in e and "attribute" not in e for e in local_inc + local_exc)
    assert "tlevel,exact,peer_reviewed" in ext_inc
    assert "tlevel,exact,open_access" in ext_inc
    assert "attribute,exact,retracted_publication" in ext_exc


def test_compile_filters_puts_domain_on_external_scope_only():
    """domain 也是 PC 侧概念，本馆馆藏没有这个维度。"""
    parsed = {"domains": ["MEDLINE"]}
    assert "domain,exact,MEDLINE" in library_query.compile_filters(parsed, "CentralIndex")[0]
    assert library_query.compile_filters(parsed, "MyInstitution")[0] == []


def test_compile_filters_year_becomes_one_include_per_year():
    """逐年一条 qInclude（区间参数未验，spec §6 定它不作默认）。"""
    inc, _ = library_query.compile_filters({"year_from": 2022, "year_to": 2024}, "MyInstitution")
    assert inc.count("creationdate,exact,2022") == 1
    assert "creationdate,exact,2023" in inc and "creationdate,exact,2024" in inc


def test_compile_filters_year_range_capped():
    """别让 1900-2026 生成 127 条 qInclude。"""
    inc, _ = library_query.compile_filters({"year_from": 1900, "year_to": 2026}, "MyInstitution")
    assert len([e for e in inc if e.startswith("creationdate")]) <= 30


def test_compile_filters_open_ended_year_to_uses_now():
    inc, _ = library_query.compile_filters({"year_from": 2025, "year_to": None}, "MyInstitution")
    assert "creationdate,exact,2025" in inc


# ---------- parse_natural：降级 ----------

def test_parse_natural_returns_parsed_and_no_warnings(monkeypatch):
    monkeypatch.setattr(llm_client, "_call_chat",
                        lambda *a, **k: '{"keywords": ["ml"], "rtype": ["articles"]}')
    parsed, warnings = library_query.parse_natural("u", "k", "m", "机器学习论文")
    assert parsed["keywords"] == ["ml"] and parsed["rtype"] == ["articles"]
    assert warnings == []


def test_parse_natural_degrades_on_llm_failure(monkeypatch):
    """LLM 挂了 → 退化成「原文当关键词」，**不抛错**（spec §6 降级）。"""
    def boom(*a, **k):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(llm_client, "_call_chat", boom)
    parsed, warnings = library_query.parse_natural("u", "k", "m", "机器学习论文")
    assert parsed["keywords"] == ["机器学习论文"]
    assert warnings == ["llm_degraded"]      # 警告是**码**，前端翻译


def test_parse_natural_degrades_on_invalid_json(monkeypatch):
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: "not json at all")
    parsed, warnings = library_query.parse_natural("u", "k", "m", "query text")
    assert parsed["keywords"] == ["query text"]
    assert warnings == ["llm_degraded"]


def test_parse_natural_degrades_on_empty_llm_output(monkeypatch):
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: "")
    parsed, warnings = library_query.parse_natural("u", "k", "m", "abc")
    assert warnings == ["llm_degraded"] and parsed["keywords"] == ["abc"]


def test_parse_natural_warns_when_domains_dropped(monkeypatch):
    monkeypatch.setattr(llm_client, "_call_chat",
                        lambda *a, **k: '{"keywords":["x"],"domains":["Nope"]}')
    parsed, warnings = library_query.parse_natural(
        "u", "k", "m", "x", domains=["MEDLINE"])
    assert parsed["domains"] == []
    assert "domains_dropped" in warnings


def test_parse_natural_forces_user_area_into_parsed(monkeypatch):
    """spec §6：用户选的 area 是「不被丢弃的提示」——
    LLM 可以补充别的领域，但不得删掉用户选的那个。"""
    monkeypatch.setattr(llm_client, "_call_chat",
                        lambda *a, **k: '{"keywords":["x"],"areas":["edu"]}')
    parsed, _ = library_query.parse_natural("u", "k", "m", "x", area="comp")
    assert "comp" in parsed["areas"] and "edu" in parsed["areas"]


def test_parse_natural_blank_area_does_not_inject(monkeypatch):
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: '{"keywords":["x"]}')
    parsed, _ = library_query.parse_natural("u", "k", "m", "x", area=None)
    assert parsed["areas"] == []


def test_parse_natural_replaces_synonym_only_result_with_fallback(monkeypatch):
    """LLM 只给了 synonyms 没给 keywords → 用 synonyms 顶上，不算降级。"""
    monkeypatch.setattr(llm_client, "_call_chat",
                        lambda *a, **k: '{"keywords":[],"synonyms":["deep learning"]}')
    parsed, warnings = library_query.parse_natural("u", "k", "m", "x")
    assert parsed["keywords"] == ["deep learning"] and warnings == []


def test_parse_natural_degrades_when_both_keyword_fields_empty(monkeypatch):
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: '{"keywords":[]}')
    parsed, warnings = library_query.parse_natural("u", "k", "m", "原文")
    assert parsed["keywords"] == ["原文"] and warnings == ["llm_degraded"]


# ---------- prompt ----------

def test_build_prompt_lists_domains_and_areas():
    p = library_query.build_prompt("find papers", "comp",
                                   {"comp": "Computer Science"}, ["MEDLINE", "ScienceDirect"])
    assert "Computer Science" in p
    assert "MEDLINE" in p and "ScienceDirect" in p
    assert "find papers" in p


def test_build_prompt_tolerates_empty_catalogs():
    """词表拿不到时提示词照样能拼出来（只是没得挑）。"""
    p = library_query.build_prompt("q", None, {}, [])
    assert "q" in p
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_library_query.py -q
```

Expected: `ModuleNotFoundError: No module named 'backend.library_query'`

- [ ] **Step 3: 实现**

创建 `backend/library_query.py`：

```python
"""自然语言 → 结构化检索式。

产物是**结构化 JSON，不是 Primo 查询串**（spec §6）：可校验、可原样回显给
用户、单字段出错能降级而不是整个查询崩掉。
"""
from __future__ import annotations

from datetime import datetime

from . import llm_client

# 实测：rtype 的值是**复数**。单数形式返回 0 命中。
KNOWN_RTYPE = ["articles", "books", "journals", "databases", "dissertations",
               "conference_proceedings", "book_chapters", "preprint",
               "reviews", "text_resources"]

_MAX_ITEMS = 12        # 每个列表最多几条
_MAX_STR = 200         # 单个字符串最长多少字符
_MIN_YEAR = 1800
_YEAR_SPAN_CAP = 30    # 年份区间最多生成多少条 creationdate 过滤

EMPTY_PARSED = {
    "keywords": [], "synonyms": [], "year_from": None, "year_to": None,
    "rtype": [], "language": None, "open_access_only": False,
    "peer_reviewed_only": False, "exclude_retracted": False,
    "areas": [], "domains": [], "explanation": "",
}


def _as_list(value) -> list[str]:
    """LLM 有时给标量、有时给列表。统一成清洗过的字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for v in value:
        s = str(v).strip()[:_MAX_STR]
        if s:
            out.append(s)
    return out[:_MAX_ITEMS]


def _as_year(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        year = int(float(value))
    except (TypeError, ValueError):
        return None
    now = datetime.now().year
    if year < _MIN_YEAR or year > now + 1:
        return None
    return year


def validate_parsed(raw: dict | None, known_domains: list[str] | None = None) -> dict:
    """把任意输入（LLM 产物 / **客户端回传的 `parsed`**）归一化成合法检索式。

    `parsed` 在第 2 页起由客户端回传（spec §5.3），所以这里**是防御性边界**：
    类型、枚举、长度、年份上下界全部要挡。这不是安全问题（用户查的是自己的
    东西），是健壮性问题 —— 畸形 `parsed` 不能变成畸形上游请求。
    """
    if not isinstance(raw, dict):
        return dict(EMPTY_PARSED)

    rtype = [r for r in _as_list(raw.get("rtype")) if r in KNOWN_RTYPE]
    domains = _as_list(raw.get("domains"))
    if known_domains:            # 词表拿得到才剔除；[] 表示拿不到，别误伤
        allowed = set(known_domains)
        domains = [d for d in domains if d in allowed]

    return {
        "keywords": _as_list(raw.get("keywords")),
        "synonyms": _as_list(raw.get("synonyms")),
        "year_from": _as_year(raw.get("year_from")),
        "year_to": _as_year(raw.get("year_to")),
        "rtype": rtype,
        "language": (str(raw.get("language")).strip()[:_MAX_STR]
                     if raw.get("language") else None),
        "open_access_only": bool(raw.get("open_access_only")),
        "peer_reviewed_only": bool(raw.get("peer_reviewed_only")),
        "exclude_retracted": bool(raw.get("exclude_retracted")),
        "areas": [a.lower() for a in _as_list(raw.get("areas"))],
        "domains": domains,
        "explanation": str(raw.get("explanation") or "")[:_MAX_STR * 2],
    }


def fallback_parsed(text: str) -> dict:
    """LLM 不可用时的退化：把用户原文整句当关键词。**不抛错。**"""
    parsed = dict(EMPTY_PARSED)
    parsed["keywords"] = _as_list(text.strip())
    return parsed


def build_prompt(text: str, area: str | None, area_names: dict[str, str],
                 domains: list[str]) -> str:
    """拼给 LLM 的提示词。词表为空也要能拼出来（只是没得挑）。"""
    area_lines = "\n".join(f"- {code}: {name}" for code, name in (area_names or {}).items())
    if area:
        chosen = area_names.get(area, area) if area_names else area
        area_note = (f"用户已限定学科领域为 **{chosen}**（code: {area}）。"
                     f"你必须在 areas 里包含 \"{area}\"，可以补充其它相关领域，但**不得删掉它**。")
    else:
        area_note = "用户没有指定学科领域，由你判断。"

    domain_lines = "\n".join(f"- {d}" for d in (domains or []))

    return f"""把下面的检索意图转成一个 JSON 对象。只输出 JSON，不要解释。

用户输入：{text}

{area_note}

可用的学科领域 code（areas 只能从这里取）：
{area_lines or "（暂不可用）"}

可用的来源数据库（domains 只能从这里取，取不到就留空数组）：
{domain_lines or "（暂不可用）"}

输出这些字段：
- keywords: 字符串数组，核心检索词（英文，2-4 个）
- synonyms: 字符串数组，同义词 / 缩写 / 相关词
- year_from, year_to: 整数或 null
- rtype: 数组，取值只能来自 {KNOWN_RTYPE}
- language: 字符串或 null
- open_access_only, peer_reviewed_only, exclude_retracted: 布尔
- areas: 学科领域 code 数组
- domains: 来源数据库名数组（**只能取自上面那份列表**）
- explanation: 一句中文，说明你理解成了什么
"""


def parse_natural(base_url: str, api_key: str, model: str, text: str, *,
                  area: str | None = None, area_names: dict[str, str] | None = None,
                  domains: list[str] | None = None) -> tuple[dict, list[str]]:
    """自然语言 → `(parsed, warnings)`。

    **绝不因为 LLM 出问题就失败**（spec §6 降级）：调用失败、返回非 JSON、
    返回空，一律退化成「原文当关键词」，并回一个 `llm_degraded` 警告码。

    警告是**码**不是句子 —— 前端按 `library.warn.<code>` 翻译，否则英文界面
    会冒中文。
    """
    warnings: list[str] = []
    domains = domains or []
    try:
        raw_text = llm_client._call_chat(
            base_url, api_key, model,
            build_prompt(text, area, area_names or {}, domains))
        raw = llm_client._parse_json(raw_text)
        if not isinstance(raw, dict) or not raw:
            raise ValueError("LLM 未返回对象")
    except Exception:
        return fallback_parsed(text), ["llm_degraded"]

    kept = validate_parsed(raw, known_domains=domains)
    if domains and raw.get("domains") and not kept["domains"]:
        warnings.append("domains_dropped")     # LLM 编了词表里没有的库名

    # 用户选的 area 是「不被丢弃的提示」（spec §6）：LLM 可以补充，不能删。
    if area and area not in kept["areas"]:
        kept["areas"].insert(0, area)
    if not kept["keywords"] and kept["synonyms"]:
        kept["keywords"] = kept["synonyms"][:_MAX_ITEMS]
    if not kept["keywords"]:
        return fallback_parsed(text), (warnings + ["llm_degraded"])
    return kept, warnings


def build_q(parsed: dict) -> str:
    """关键词 + 同义词 → Primo 的 q 参数。空的话给个合法兜底，上游才不 400。"""
    terms = list(parsed.get("keywords") or []) + list(parsed.get("synonyms") or [])
    return "any,contains," + " ".join(terms)


def compile_filters(parsed: dict, scope: str) -> tuple[list[str], list[str]]:
    """把检索式编译成 `(qInclude, qExclude)` 两份过滤表达式。

    **按 scope 分流**（spec §6）——这是本模块最容易写错的地方：
    `tlevel` / `attribute` / `domain` 都是 **Primo Central 独有**的 facet，
    本馆馆藏记录没有这些维度。把它们套到 `MyInstitution` 上不会报错，
    只会**静默返回 0 命中**，馆藏那栏整块空掉，而且看不出原因。

    只有 `CentralIndex` 吃全部过滤。
    """
    include: list[str] = []
    exclude: list[str] = []
    is_pc = scope == "CentralIndex"

    for rtype in parsed.get("rtype") or []:
        include.append(f"rtype,exact,{rtype}")

    year_from, year_to = parsed.get("year_from"), parsed.get("year_to")
    if year_from:
        end = year_to or min(datetime.now().year, year_from + _YEAR_SPAN_CAP)
        # +1 是为了让**闭区间**长度不超过 _YEAR_SPAN_CAP（end - start + 1 ≤ CAP）。
        # 写成 end - CAP 会得到 CAP+1 条，正好越过上限一条。
        start = max(year_from, end - _YEAR_SPAN_CAP + 1)
        for year in range(start, end + 1):
            include.append(f"creationdate,exact,{year}")

    if is_pc:
        for domain in parsed.get("domains") or []:
            include.append(f"domain,exact,{domain}")
        if parsed.get("peer_reviewed_only"):
            include.append("tlevel,exact,peer_reviewed")
        if parsed.get("open_access_only"):
            include.append("tlevel,exact,open_access")
        if parsed.get("exclude_retracted"):
            exclude.append("attribute,exact,retracted_publication")

    return include, exclude
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_library_query.py -q
```

Expected: 全部 PASS

- [ ] **Step 5: 变异检验**

三处，逐个改坏再还原：

1. `compile_filters` 里把 `is_pc = scope == "CentralIndex"` 改成 `is_pc = True` → `test_compile_filters_puts_pc_only_filters_on_external_scope_only` 与 `..._puts_domain_on_external_scope_only` 必须失败。
2. `parse_natural` 的 `except Exception:` 分支改成 `raise` → 三个降级用例必须失败。
3. `validate_parsed` 里删掉 `if known_domains:` 守卫 → `test_validate_parsed_keeps_domains_when_no_catalog` 必须失败。

- [ ] **Step 6: 提交**

```bash
git add backend/library_query.py tests/test_library_query.py
git commit -m "feat(library): 自然语言→检索式（按 scope 分流编译、parsed 防御性归一化、LLM 降级）"
```

---

### Task 5: 两个后端端点

**Files:**
- Modify: `backend/main.py`（导入 + 两个 pydantic 模型 + 两个端点）
- Test: `tests/test_main.py`（追加）

**Interfaces:**
- Consumes: `library_client.search/list_domains/SCOPE_LOCAL/SCOPE_EXTERNAL/MAX_PAGES/LibraryError`、`library_areas.load_areas`、`library_query.parse_natural/validate_parsed/build_q/compile_filters`
- Produces:
  - `POST /api/library/areas` → `{"ok": True, "available": bool, "areas": [{"code","name"}]}`
  - `POST /api/library/search` → `{"ok": True, "parsed": {...}, "local": {...}, "external": {...}, "warnings": [...]}`
    每块 = `{"items": [...], "count": int, "page": int, "has_more": bool}`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_main.py`：

```python
# ---------- 图书馆端点 ----------

def _patch_library(monkeypatch, calls=None):
    """把三个新模块全部换成替身。calls 用来断言 LLM 有没有被调到。"""
    from backend import library_areas, library_client, library_query
    monkeypatch.setattr(library_areas, "load_areas",
                        lambda **k: {"available": True,
                                     "areas": [{"code": "comp", "name": "Computer Science"}]})
    monkeypatch.setattr(library_client, "list_domains", lambda **k: ["MEDLINE"])
    if calls is None:
        calls = []
    monkeypatch.setattr(library_query, "parse_natural",
                        lambda *a, **k: (calls.append("llm") or
                                         ({"keywords": ["ml"], "synonyms": [], "year_from": None,
                                           "year_to": None, "rtype": [], "language": None,
                                           "open_access_only": False, "peer_reviewed_only": False,
                                           "exclude_retracted": False, "areas": [], "domains": [],
                                           "explanation": ""}, [])))
    monkeypatch.setattr(library_client, "search",
                        lambda scope, q, **k: {"scope": scope,
                                               "items": [{"title": scope, "url": "u",
                                                          "record_id": "r"}],
                                               "count": 5, "page": k.get("page", 1),
                                               "has_more": False})
    return calls


def test_library_areas_endpoint(monkeypatch):
    _patch_library(monkeypatch)
    r = client.post("/api/library/areas", json={})
    assert r.json() == {"ok": True, "available": True,
                        "areas": [{"code": "comp", "name": "Computer Science"}]}


def test_library_areas_unavailable_is_not_an_error(monkeypatch):
    """拿不到不是错误 —— 前端据此不渲染选择器，不该看到红字。"""
    from backend import library_areas
    monkeypatch.setattr(library_areas, "load_areas", lambda **k: {"available": False, "areas": []})
    body = client.post("/api/library/areas", json={}).json()
    assert body["ok"] is True and body["available"] is False


def test_library_search_returns_two_blocks(monkeypatch):
    calls = _patch_library(monkeypatch)
    r = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "text": "ml papers"})
    body = r.json()
    assert body["ok"] is True
    assert body["local"]["items"][0]["title"] == "MyInstitution"
    assert body["external"]["items"][0]["title"] == "CentralIndex"
    assert calls == ["llm"]


def test_library_search_second_page_does_not_call_llm(monkeypatch):
    """spec §5.3 核心：翻页绝不重新调用 LLM ——
    否则 LLM 两次产出不同，第 2 页的检索式与第 1 页不同，结果集中途漂移。"""
    calls = _patch_library(monkeypatch)
    parsed = {"keywords": ["ml"], "synonyms": [], "year_from": None, "year_to": None,
              "rtype": [], "language": None, "open_access_only": False,
              "peer_reviewed_only": False, "exclude_retracted": False,
              "areas": [], "domains": [], "explanation": ""}
    r = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "page": 2, "parsed": parsed})
    assert r.json()["ok"] is True
    assert calls == []          # LLM 一次都没被调


def test_library_search_page_beyond_max_rejected(monkeypatch):
    """上限由后端挡 —— 前端不显示按钮拦不住改请求的人。"""
    _patch_library(monkeypatch)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "text": "x", "page": 6}).json()
    assert body["ok"] is False and "页码" in body["error"]


def test_library_search_requires_text_or_parsed(monkeypatch):
    _patch_library(monkeypatch)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m"}).json()
    assert body["ok"] is False


def test_library_search_wraps_upstream_failure(monkeypatch):
    _patch_library(monkeypatch)
    from backend import library_client

    def boom(scope, q, **k):
        raise library_client.LibraryError("上游炸了")

    monkeypatch.setattr(library_client, "search", boom)
    body = client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "text": "x"}).json()
    assert body["ok"] is False and "上游炸了" in body["error"]


def test_library_search_passes_area_hint_to_llm(monkeypatch):
    """area 传给 LLM 当提示，且**不**被当成过滤参数（spec §4.1）。"""
    seen = {}
    from backend import library_query
    _patch_library(monkeypatch)

    def spy(*a, **k):
        seen.update(k)
        return ({"keywords": ["x"], "synonyms": [], "year_from": None, "year_to": None,
                 "rtype": [], "language": None, "open_access_only": False,
                 "peer_reviewed_only": False, "exclude_retracted": False,
                 "areas": ["comp"], "domains": [], "explanation": ""}, [])

    monkeypatch.setattr(library_query, "parse_natural", spy)
    client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "text": "x", "area": "comp"})
    assert seen.get("area") == "comp"


def test_library_search_compiles_two_different_param_sets(monkeypatch):
    """两个 scope 的过滤参数**不是同一套**（spec §6）。"""
    _patch_library(monkeypatch)
    seen = {}
    from backend import library_client

    def spy(scope, q, **k):
        seen[scope] = k
        return {"scope": scope, "items": [], "count": 0, "page": 1, "has_more": False}

    monkeypatch.setattr(library_client, "search", spy)
    client.post("/api/library/search", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "text": "x", "parsed": {"keywords": ["x"], "peer_reviewed_only": True}})
    assert "tlevel,exact,peer_reviewed" in (seen["CentralIndex"].get("q_include") or [])
    assert "tlevel,exact,peer_reviewed" not in (seen["MyInstitution"].get("q_include") or [])
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_main.py -q -k library
```

Expected: FAIL — 404（端点不存在）

- [ ] **Step 3: 实现**

在 `backend/main.py` 的 `from . import ...` 那行加上三个模块（保持字母序）：

```python
from . import (apple_script, banweb, canvas_client, credentials, files_downloader,
               library_areas, library_client, library_query, llm_client)
```

在其它 `LLMConfig` 子类附近（如 `SummarizeSyllabusRequest` 之后）加：

```python
class LibrarySearchRequest(LLMConfig):
    text: str = ""
    area: str | None = None
    page: int = 1
    # 第 1 页不带 parsed（后端调 LLM 解析）；第 2 页起由前端原样回传，
    # 后端**不再调 LLM** —— 否则每页的检索式都可能不同，结果集中途漂移。
    parsed: dict | None = None
```

在文件末尾加两个端点：

```python
@app.post("/api/library/areas")
def library_areas_endpoint():
    """学科领域清单。**拿不到不是错误**：available=False，前端整个不渲染选择器。"""
    try:
        return {"ok": True, **library_areas.load_areas()}
    except Exception as exc:          # 本模块内部已尽力兜底，这里是最后一道
        return {"ok": True, "available": False, "areas": [], "error": str(exc)}


@app.post("/api/library/search")
def library_search(req: LibrarySearchRequest):
    """馆藏 + 外部数据库两块检索。全程零登录。"""
    if req.page < 1 or req.page > library_client.MAX_PAGES:
        return {"ok": False, "error": f"页码越界：{req.page}"}

    warnings: list[str] = []
    domains = library_client.list_domains()
    area_catalog = library_areas.load_areas()
    area_names = {a["code"]: a["name"] for a in area_catalog["areas"]}

    if req.parsed is not None:
        # 翻页路径：不碰 LLM（spec §5.3）。parsed 是客户端可传的输入，必须归一化。
        parsed = library_query.validate_parsed(req.parsed, known_domains=domains)
        if not parsed["keywords"] and not parsed["synonyms"]:
            return {"ok": False, "error": "parsed 缺少 keywords"}
    else:
        if not (req.text or "").strip():
            return {"ok": False, "error": "缺少 text 或 parsed"}
        parsed, warnings = library_query.parse_natural(
            req.llm_base_url, req.llm_api_key, req.llm_model, req.text,
            area=req.area, area_names=area_names, domains=domains)
        # 领域提示是「不被丢弃的提示」（spec §6）：LLM 降级丢掉了也要补回来。
        if req.area and req.area not in parsed["areas"]:
            parsed["areas"].insert(0, req.area)

    q = library_query.build_q(parsed)
    blocks = {}
    try:
        for scope, key in ((library_client.SCOPE_LOCAL, "local"),
                           (library_client.SCOPE_EXTERNAL, "external")):
            include, exclude = library_query.compile_filters(parsed, scope)
            blocks[key] = library_client.search(
                scope, q, q_include=include, q_exclude=exclude, page=req.page)
    except Exception as exc:
        # 上游失败也把 parsed 带回去 —— 否则前端第 2 页起就回传不了了。
        return {"ok": False, "error": str(exc), "parsed": parsed, "warnings": warnings}

    return {"ok": True, "parsed": parsed, "warnings": warnings, **blocks}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_main.py -q -k library
```

Expected: 全部 PASS

- [ ] **Step 5: 变异检验**

把 `if req.page < 1 or req.page > library_client.MAX_PAGES` 里第二个条件删掉，确认 `test_library_search_page_beyond_max_rejected` **失败**。把 `if req.parsed is not None:` 分支删掉（强制走 LLM），确认 `test_library_search_second_page_does_not_call_llm` **失败**。还原。

- [ ] **Step 6: 全量回归 + 提交**

```bash
pytest -q
```

Expected: 全绿（本分支基线 **242** + 本计划新增）

```bash
git add backend/main.py tests/test_main.py
git commit -m "feat(library): /api/library/areas 与 /api/library/search（翻页不调 LLM、页码上限后端挡）"
```

---

### Task 6: 前端骨架 —— 导航页、i18n、静态结构

**Files:**
- Modify: `frontend/index.html`、`frontend/shell.js`、`frontend/i18n.js`、`frontend/app.css`、`frontend/app.js`

**Interfaces:**
- Consumes: 无（本任务不调后端）
- Produces:
  - `id="page-library"` 页面容器（含 `libraryQuery`、`libraryArea`、`librarySearchBtn`、`libraryParsed`、`libraryWarnings`、`libraryLocal`、`libraryExternal`、`libraryPager`、`libraryPrev`、`libraryPageInfo`、`libraryNext`）
  - `PAGES` 含 `"library"`；`PAGE_INIT.library` 指向 `initLibraryTab()`
  - i18n 键：`nav.library` + 16 个 `library.*`（zh / en 各 17 个）

- [ ] **Step 1: 加导航项**

`frontend/index.html`，`<button class="nav-item" data-page="courses">` 那一行之后插入：

```html
      <button class="nav-item" data-page="library"><span data-i18n="nav.library"></span></button>
```

- [ ] **Step 2: 加页面容器**

`frontend/index.html`，在 `<div id="page-settings" class="page" hidden>` **之前**插入：

```html
    <div id="page-library" class="page" hidden>
      <div class="card">
        <div class="lib-searchbar">
          <input id="libraryQuery" type="text" data-i18n-ph="library.placeholder" />
          <select id="libraryArea" hidden></select>
          <button class="btn btn-primary" id="librarySearchBtn" data-i18n="library.search"></button>
        </div>
        <div class="lib-parsed" id="libraryParsed" hidden></div>
        <div class="lib-warnings" id="libraryWarnings" hidden></div>
      </div>
      <div class="lib-columns">
        <div class="card lib-col">
          <h3 data-i18n="library.local_title"></h3>
          <div id="libraryLocal"></div>
        </div>
        <div class="card lib-col">
          <h3 data-i18n="library.external_title"></h3>
          <div id="libraryExternal"></div>
        </div>
      </div>
      <div class="lib-pager" id="libraryPager" hidden>
        <button class="btn" id="libraryPrev" data-i18n="library.prev"></button>
        <span id="libraryPageInfo"></span>
        <button class="btn" id="libraryNext" data-i18n="library.next"></button>
      </div>
    </div>
```

两点说明：

- `#libraryArea` 初始 `hidden` —— **拿不到领域清单就不显示**（spec §4.3）。
- **静态文案一律走 `data-i18n` / `data-i18n-ph` 属性，不在 JS 里 `textContent = t(...)`。** `frontend/i18n.js:725` 的 `applyI18n()` 只重写带这两个属性的元素；JS 里赋的值在切语言时**不会跟着变**，会留下一屏旧语言。动态内容（结果列表、页码、警告）没有属性、由渲染函数自己管，切语言时重跑一次渲染即可。

- [ ] **Step 3: 加 i18n 键（zh 与 en 各一份，键集逐字相同）**

`frontend/i18n.js`：在 `zh` 块里 `"nav.courses"` 那行之后加：

```js
    "nav.library": "图书馆",
```

在 `zh` 块末尾前加：

```js
    "library.placeholder": "用任意语言描述你要找的文献，例如：2020 年后关于机器学习在教育中应用的同行评审论文",
    "library.search": "检索",
    "library.area_any": "不限领域",
    "library.local_title": "本馆馆藏",
    "library.external_title": "外部数据库",
    "library.count": "共 {n} 条",
    "library.empty": "没有找到结果",
    "library.need_input": "请先输入要检索的内容",
    "library.loading": "检索中…",
    "library.parsed": "理解为：{s}",
    "library.prev": "上一页",
    "library.next": "下一页",
    "library.page": "第 {p} / {m} 页",
    "library.no_link": "（无链接）",
    "library.warn.llm_degraded": "AI 解析失败，已按原文关键词检索",
    "library.warn.domains_dropped": "AI 选的来源库不在可选列表里，已忽略",
```

在 `en` 块里 `"nav.courses"` 之后加：

```js
    "nav.library": "Library",
```

在 `en` 块末尾前加：

```js
    "library.placeholder": "Describe what you're looking for, in any language — e.g. peer-reviewed papers on machine learning in education after 2020",
    "library.search": "Search",
    "library.area_any": "Any field",
    "library.local_title": "CityU Holdings",
    "library.external_title": "External Databases",
    "library.count": "{n} results",
    "library.empty": "No results",
    "library.need_input": "Please enter something to search for",
    "library.loading": "Searching…",
    "library.parsed": "Understood as: {s}",
    "library.prev": "Previous",
    "library.next": "Next",
    "library.page": "Page {p} of {m}",
    "library.no_link": "(no link)",
    "library.warn.llm_degraded": "AI parsing failed — searched using your raw keywords",
    "library.warn.domains_dropped": "AI picked source databases that aren't available — ignored",
```

- [ ] **Step 4: 注册页面**

`frontend/shell.js` 的 `PAGES` 数组里 `"courses"` 之后加 `"library"`：

```js
const PAGES = ["home","announce","discuss","schedule","todo","files","grades","courses","library","settings"];
```

`PAGE_INIT` 对象里 `courses:` 那行之后加：

```js
  library:  () => initLibraryTab(),
```

- [ ] **Step 5: 加占位实现**

`initLibraryTab()` 的完整实现在 Task 7。本步先在 `frontend/app.js` 末尾加个空占位，让 `PAGE_INIT` 能解析、页面能开关而不报错：

```js
/* ---------- 图书馆检索（第一阶段，零登录） ---------- */
function initLibraryTab(){}
```

（静态文案已由 HTML 上的 `data-i18n` / `data-i18n-ph` 承载，这里**不需要**再赋值。）

- [ ] **Step 6: 加样式**

追加到 `frontend/app.css`：

```css
/* 图书馆检索：双栏结果 */
.lib-searchbar { display: flex; gap: 8px; align-items: center; }
.lib-searchbar input { flex: 1; }
.lib-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }
@media (max-width: 900px) { .lib-columns { grid-template-columns: 1fr; } }
.lib-col h3 { margin: 0 0 8px; }
.lib-item { padding: 8px 0; border-bottom: 1px solid var(--border); }
.lib-item a { text-decoration: none; }
.lib-item .lib-meta { font-size: 12px; opacity: .7; }
.lib-pager { display: flex; gap: 12px; align-items: center; justify-content: center; margin-top: 16px; }
.lib-parsed, .lib-warnings { font-size: 12px; opacity: .8; margin-top: 8px; }
```

`--border` 是仓库既有变量（`frontend/app.css:4` 浅色 / `:27` 深色两套都有定义），可直接用。

- [ ] **Step 7: 跑门禁**

```bash
node tools/check_i18n_keys.mjs
node tools/check_dom_ids.mjs
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/four.js && node --check /tmp/four.js
```

Expected: 三个都通过。i18n 键数 359 → 376（各加 17）；死键 ≤ 21。HTML id 124 → 135（新增 11 个）。

- [ ] **Step 8: 手工确认页面能开**

启动后端，点左侧新增的「图书馆」项：页面显示输入框、检索按钮、两个空栏；分页条隐藏；**领域下拉框不可见**（此时还没加载）。切到设置页再切回来不报错（浏览器控制台无红字）。

- [ ] **Step 9: 提交**

```bash
git add frontend/index.html frontend/shell.js frontend/i18n.js frontend/app.css frontend/app.js
git commit -m "feat(library): 前端骨架 —— 一级导航页 + 双栏结构 + i18n（zh/en 同步）"
```

---

### Task 7: 前端检索、渲染与翻页

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/i18n.js`（在 `applyLang()` 末尾挂一行重渲）

**Interfaces:**
- Consumes: Task 5 的两个端点、Task 6 的 DOM id 与 i18n 键、`util.js` 的 `api()` / `$()` / `t()` / `setStatus()` / `settings()`
- Produces:
  - `initLibraryTab()`（覆盖 Task 6 的占位实现）
  - `loadLibraryAreas()`、`runLibrarySearch(page)`、`renderLibraryAreas()`、`renderLibraryBlock(el, block)`、`renderLibraryParsed(parsed)`、`renderLibraryWarnings(warnings)`、`renderLibraryPager(r)`、`renderLibraryPage()`
  - 模块级状态 `let libraryState`

- [ ] **Step 1: 先 grep 防重名**

**这一步不能跳。** 顶层 `let`/`const` 重名会让整页白屏，而 `node --check` 查不出来：

```bash
grep -n "libraryState\|runLibrarySearch\|renderLibraryBlock\|renderLibraryParsed\|renderLibraryWarnings\|renderLibraryPager\|loadLibraryAreas\|initLibraryTab" frontend/i18n.js frontend/util.js frontend/shell.js
```

Expected: 只有 `shell.js` 里的一次 `initLibraryTab`（Task 6 的 `PAGE_INIT`）。若出现其它名字，**改名**再继续。

同时确认 `setStatus` 与 `settings` 确实存在（`frontend/util.js`）：

```bash
grep -n "function setStatus\|function settings\|function withBusy" frontend/util.js
```

若 `withBusy` 不存在，把 `runLibrarySearch` 里的 `withBusy(...)` 换成直接 `await api(...)`，并把按钮的 `disabled` 手动置位/复位。

- [ ] **Step 2: 替换占位实现**

把 Task 6 加的那段整体替换成：

```js
/* ---------- 图书馆检索（第一阶段，零登录） ---------- */

/* 当前检索的会话状态。翻页要靠 parsed 原样回传 —— 后端第 2 页起不调 LLM，
   所以这份 parsed 必须与结果同源，不能中途重新解析。
   areas / last 留着是为了**切语言时能就地重渲**（见 renderLibraryPage）。 */
let libraryState = { parsed: null, page: 1, loaded: false, areas: [], last: null };

const LIBRARY_MAX_PAGES = 5;   /* 与后端 library_client.MAX_PAGES 一致；后端才是权威 */

function initLibraryTab(){
  if (libraryState.loaded) return;      // 只绑一次
  libraryState.loaded = true;

  $("librarySearchBtn").onclick = () => runLibrarySearch(1);
  $("libraryPrev").onclick = () => runLibrarySearch(libraryState.page - 1);
  $("libraryNext").onclick = () => runLibrarySearch(libraryState.page + 1);
  $("libraryQuery").addEventListener("keydown", e => {
    if (e.key === "Enter") runLibrarySearch(1);
  });
  loadLibraryAreas();
}

/* 领域清单：拿不到就**整个不渲染选择器**（spec §4.3，用户裁定）。
   不显示空下拉、不显示禁用态、不显示报错 —— 就当这个功能不存在。 */
async function loadLibraryAreas(){
  const r = await api("library/areas", {});
  libraryState.areas = (r.ok && r.available && r.areas) ? r.areas : [];
  renderLibraryAreas();
}

function renderLibraryAreas(){
  const sel = $("libraryArea");
  const prev = sel.value;
  if (!libraryState.areas.length){ sel.hidden = true; return; }
  sel.innerHTML = `<option value="">${t("library.area_any")}</option>` +
    libraryState.areas.map(a => `<option value="${a.code}">${a.name}</option>`).join("");
  /* 切语言会重跑这里。用户选的领域不能因为一次语言切换就被重置。 */
  if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
  sel.hidden = false;
}

/* 切语言时由 applyLang() 调用：只重画内存里已有的东西，**不发起网络请求**。
   没有这一条，结果列表/页码/领域下拉会一直停在旧语言，直到重启应用
   —— 与 selTodoAlert 那个旧bug 是同一类（见 i18n.js 里的注释）。 */
function renderLibraryPage(){
  renderLibraryAreas();
  if (!libraryState.last) return;
  renderLibraryBlock($("libraryLocal"), libraryState.last.local);
  renderLibraryBlock($("libraryExternal"), libraryState.last.external);
  renderLibraryParsed(libraryState.last.parsed);
  renderLibraryWarnings(libraryState.last.warnings);
  renderLibraryPager(libraryState.last);
}

async function runLibrarySearch(page){
  if (page < 1) return;

  /* 第 1 页：发原文，后端调 LLM 解析。
     第 2 页起：回传上一页拿到的 parsed，后端不调 LLM —— 否则每页的检索式
     都可能不同，结果集在翻页中途漂移，而且不报任何错（spec §5.3）。 */
  const body = { ...settings(), page };
  if (page > 1){
    if (!libraryState.parsed) return;      // 没解析过就没有第 2 页
    body.parsed = libraryState.parsed;
  } else {
    body.text = $("libraryQuery").value.trim();
    const area = $("libraryArea").hidden ? "" : $("libraryArea").value;
    if (area) body.area = area;
    if (!body.text){ setStatus(t("library.need_input"), "err"); return; }
  }

  $("libraryLocal").textContent = t("library.loading");
  $("libraryExternal").textContent = t("library.loading");

  const r = await api("library/search", body);
  if (!r.ok){
    setStatus(r.error || "error", "err");
    $("libraryLocal").textContent = "";
    $("libraryExternal").textContent = "";
    $("libraryPager").hidden = true;
    return;
  }

  if (page === 1) libraryState.parsed = r.parsed;
  libraryState.page = page;
  libraryState.last = r;
  renderLibraryPage();
}

function renderLibraryParsed(parsed){
  const el = $("libraryParsed");
  const text = parsed && parsed.explanation;
  if (!text){ el.hidden = true; return; }
  el.textContent = t("library.parsed", { s: text });
  el.hidden = false;
}

/* 警告是**码**不是句子，在这里翻译 —— 后端回句子的话英文界面会冒中文。 */
function renderLibraryWarnings(warnings){
  const el = $("libraryWarnings");
  if (!warnings || !warnings.length){ el.hidden = true; return; }
  el.textContent = warnings.map(w => t("library.warn." + w)).join(" · ");
  el.hidden = false;
}

function renderLibraryBlock(el, block){
  if (!block){ el.textContent = ""; return; }
  const count = `<div class="lib-meta">${t("library.count", { n: block.count })}</div>`;
  if (!block.items || !block.items.length){
    el.innerHTML = count + `<div class="lib-meta">${t("library.empty")}</div>`;
    return;
  }
  el.innerHTML = count + block.items.map(it => {
    const meta = [it.creator, it.year, it.journal].filter(Boolean).join(" · ");
    const title = it.url
      ? `<a href="${it.url}" target="_blank" rel="noopener">${esc(it.title || it.record_id)}</a>`
      : `${esc(it.title || "—")} <span class="lib-meta">${t("library.no_link")}</span>`;
    return `<div class="lib-item"><div>${title}</div>
            <div class="lib-meta">${esc(meta)}</div></div>`;
  }).join("");
}

function renderLibraryPager(r){
  const pager = $("libraryPager");
  if (!r){ pager.hidden = true; return; }
  const has = (r.local && r.local.items && r.local.items.length) ||
              (r.external && r.external.items && r.external.items.length);
  if (!has){ pager.hidden = true; return; }
  $("libraryPageInfo").textContent = t("library.page", { p: libraryState.page, m: LIBRARY_MAX_PAGES });
  $("libraryPrev").disabled = libraryState.page <= 1;
  /* has_more 由**后端**算好（spec §5.3）—— 前端不自己推算，否则上限一改
     前端就骗人。两栏任何一个还有下一页就允许继续翻。 */
  const more = (r.local && r.local.has_more) || (r.external && r.external.has_more);
  $("libraryNext").disabled = !more;
  pager.hidden = false;
}
```

三处刻意的写法：

- **标题/作者/期刊全部过 `esc()`**（`frontend/util.js:63`）。这些字符串来自上游书目数据，不是自家文案，直接插进 `innerHTML` 等于让第三方数据决定 DOM。
- **`renderLibraryPage()` 只读内存状态、不发请求** —— 与 `applyLang()` 里其它重渲函数同一契约。
- `LIBRARY_MAX_PAGES` 只用于**显示**「第 N / 5 页」；能不能继续翻由后端的 `has_more` 决定。

- [ ] **Step 3: 把 `renderLibraryPage` 挂进 applyLang**

`frontend/i18n.js` 的 `applyLang()` 末尾（`renderHomeGradeCard` 那行之后）加：

```js
  /* 图书馆页：结果列表/页码/领域下拉都是 JS 生成的，切语言得就地重画
     （只在内存数据上工作，不触发网络）。 */
  if (typeof renderLibraryPage === "function") renderLibraryPage();
```

用 `typeof` 守卫是跟着上面几行既有写法走的。

- [ ] **Step 4: 跑门禁**

```bash
node tools/check_i18n_keys.mjs
node tools/check_dom_ids.mjs
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/four.js && node --check /tmp/four.js
pytest -q
```

Expected: 全绿。

- [ ] **Step 5: 手工验收（真实网络，只跑一次）**

启动应用，切到「图书馆」页：

1. 领域下拉框**应出现**（真实网络下）；选一个领域（如 Computer Science）。
2. 输入 `transformer architecture`，点检索。
3. 两块结果都应出现，每条有标题、作者/年份/期刊、可点链接。
4. **点开一条链接，确认 Primo 页面真的显示那条记录** —— 这是唯一能验证 `record_url` 的方式（HTTP 200 证明不了任何事，见 `record_url` 的注释）。
5. 点「下一页」：结果变化、页码变 2、**输入框内容不变**（翻页不重解析）。
6. 一路翻到第 5 页，「下一页」变灰。
7. 输入一个不存在的词（如 `zzzqqqxxx`），两栏应显示「没有找到结果」而**不是报错**。
8. 断网后点检索：应显示错误提示，页面不白。

- [ ] **Step 6: 提交**

```bash
git add frontend/app.js frontend/i18n.js
git commit -m "feat(library): 检索触发、双栏渲染、翻页（回传 parsed、不重调 LLM）"
```

---

## 自审记录

**1. Spec 覆盖**

| spec 章节 | 落在哪个任务 |
|---|---|
| §3.1 端点与三个坑 | Task 1（`build_params` + 三个专门用例） |
| §3.2 只用两个 scope | Task 2（`SCOPE_LOCAL`/`SCOPE_EXTERNAL`）、Task 5（两次 `search`） |
| §3.3 `qInclude` / rtype 复数 | Task 1（表达式拼装）、Task 4（`KNOWN_RTYPE`、`compile_filters`） |
| §3.4 facet 形状 | Task 1（`normalize_doc` 字段清洗）、Task 2（`list_domains`） |
| §3.5 无 delivery（第一阶段不做） | 非目标，无任务 |
| §4 28 个领域 + 拿不到就不显示 | Task 3、Task 6 Step 3/5、Task 7 `loadLibraryAreas` |
| §4.1 area 不编译成参数 | Task 4 `compile_filters`（无 areas 分支）、Task 5（`area` 只传给 LLM） |
| §4.2 选库走 Primo 词表 | Task 2 `list_domains`、Task 4 prompt + `validate_parsed` 剔除 |
| §4.3 降级表 | Task 3（`available:false`）、Task 6/7（选择器整体不渲染） |
| §5.1 组件划分 | 文件结构表 |
| §5.2 数据流 | Task 5 + Task 7 |
| §5.3 端点契约 / 翻页不调 LLM / `has_more` 后端算 | Task 5、Task 7 `runLibrarySearch`/`renderLibraryPager` |
| §6 检索式契约 / 按 scope 分流 / 降级 | Task 4 全部 |
| §7 第二阶段 | **不在本计划**（spec 明示另起） |
| §8 测试策略 | 各任务 Step 1 |
| §9 上限与限速 | Task 1 常量、Task 2 越界拒绝 + `_throttle`、Task 5 端点再挡一次 |
| §10 本仓约束 | Global Constraints |
| §11 待验证四项 | 见下 |

**2. 占位符扫描**：无 TBD / TODO；每个代码步骤都有完整代码。

**3. 类型一致性**：`search` 返回键 `{scope,items,count,page,has_more}` 在 Task 2 定义、Task 5 消费、Task 7 读 `has_more`；`validate_parsed` 的键集在 Task 4 的 `EMPTY_PARSED` 定义、Task 5 与测试共用；`initLibraryTab` 在 Task 6 建占位、Task 7 替换；`_MIN_INTERVAL` 在 Task 2 定义、测试 monkeypatch。

**3b. 跨任务的两个隐式契约**（Task 7 两处都单独成步，不能省）：

- `renderLibraryPage()` 由 `frontend/i18n.js` 的 `applyLang()` 按 `typeof` 守卫调用 —— 契约是**只读内存状态、不发网络请求**，与那里的 `renderHomeAnnounceList` 等同一约束。
- 前端**静态**文案在 HTML 上用 `data-i18n` / `data-i18n-ph`，**动态**内容由 `renderLibraryPage()` 重画。两者混用会得到「切语言后留下一屏旧语言、直到重启」的 bug（`frontend/i18n.js:731-735` 里 `selTodoAlert` 的注释就是同一个坑的旧案）。

**4. 未覆盖的 spec 项**

§11 的四项待验证**没有独立任务**，是刻意如此——它们是参数细节，不影响架构：

| 待验证项 | 处理 |
|---|---|
| 链接格式 | Task 7 Step 4 第 4 条给了明确的人工验证动作。HTTP 200 证明不了任何事。 |
| 日期区间参数 | 默认走逐年 `qInclude`（spec §6），已在 Task 4 实现。 |
| 多 domain 的 OR/AND 语义 | **若实测是 AND，多选库会 0 命中。** 唯一改动点是 Task 4 的 `compile_filters`，届时按 spec §11 处理。 |
| 分页深度上限 | `MAX_PAGES = 5` 已定死，实测更深也不放宽（spec §9）。 |
