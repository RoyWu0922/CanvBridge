"""Primo 客户端测试。全部用合成 JSON，不打真实网络。"""
import json
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from backend import library_client


@pytest.fixture(autouse=True)
def _never_touch_real_home(tmp_path, monkeypatch):
    """安全网：默认把 domain 词表缓存指向 tmp_path（照 tests/test_library_areas.py 的范式）。

    模块常量指向 `~/.canvbridge/library_domains.json`，那是**用户真实文件**，
    测试绝不允许读它、更不许写它（读了会串味：本机缓存里有词表的话，
    「缓存命中不打网络」这类用例会变成假绿）。涉及缓存的用例各自再显式 monkeypatch，
    这个 autouse fixture 是防止日后新增用例漏写时动到用户的缓存。
    """
    monkeypatch.setattr(library_client, "CACHE_FILE", tmp_path / "guard" / "domains.json")


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


def test_build_params_joins_filters_into_one_qInclude_value():
    """多个过滤表达式必须拼成**一个** `qInclude` 值，用 `|,|` 连接。

    **根因（2026-09-20 实测，全部走真实 API）**：写成**重复的同名参数**时，
    Primo **只认第一个，后面的静默丢弃** —— 不报错、不 0 命中，只是过滤没生效；
    于是「勾了同行评审 + 2020 年以后」拿回来的是一份没过滤的结果。实测
    （`q = any,contains,machine learning`）：

      · `facet_rtype,exact,books` + `facet_creationdate,exact,[1900 TO 1901]`
          - 重复参数        → 本馆 **25,490**（= 只要 books，日期被丢）
          - 互换顺序        → 本馆 **1**（= 只要日期，books 被丢）
          - 同一个值 `|,|` 连接 → 本馆 **1**（两个都生效）
      · PC 侧：`facet_rtype,exact,articles` + 同期
          - 重复参数 → **1,280,371**（articles 单独的值）
          - 连接后   → **2**
      · 三个不同 facet 连接后 PC = **715,116**（articles ∧ 日期 ∧ 同行评审 都生效）

    厂商 UI 就是发一个值：`bundle.js` 里
    `qInclude: …map(facetToString).join("|,|")`（`qExclude` 同）。
    `facetToString` 的 range 形态 `facet_<name>,<precision>,[<a> TO <b>]`
    也正是本仓 `compile_filters` 现在发的形态。
    """
    p = library_client.build_params(
        "CentralIndex", "q",
        q_include=["rtype,exact,articles", "tlevel,exact,peer_reviewed"],
        q_exclude=["attribute,exact,retracted_publication"])
    assert p["qInclude"] == "rtype,exact,articles|,|tlevel,exact,peer_reviewed"
    assert p["qExclude"] == "attribute,exact,retracted_publication"


def test_build_params_single_filter_has_no_separator():
    """只有一条过滤时**不能**凭空多出 `|,|` —— 发出去的就是那一条本身。"""
    p = library_client.build_params(
        "MyInstitution", "q", q_include=["facet_creationdate,exact,[2020 TO 2026]"])
    assert p["qInclude"] == "facet_creationdate,exact,[2020 TO 2026]"


def test_build_params_sends_exactly_one_qInclude_param_on_the_wire():
    """钉**真实发出去的字节**：URL 里 `qInclude` 只出现**一次**。

    这里刻意不读 `build_params` 的返回值，而是走 `requests` 的
    `Request.prepare()` 再 `parse_qs` —— 中间字典对了不等于线上编码对了。

    `|,|` 会以 `%7C%2C%7C` 出现在 query 里（`parse_qs` 会解回来），所以断言
    解出来的**唯一**那个值即可；同时断言参数个数是 1 —— 这是本用例的重点：
    重复参数那种写法在这里会解出 2 个，而**第 2 个是被 Primo 忽略的**。

    **规格 §11.3 的现状（已实测，别再当未决问题）**：
      · `qInclude` **facet 之间**是 AND —— 连接后三条都生效（PC 715,116）。
      · 「**同一 facet 内多值 OR**」**在本端点找不到表达方式**：试过重复参数、
        `|,|` 连接（名字写一次 / 写两次）、`multiFacets` 三种形态（重复参数 /
        值域合并 / `rtype:` 前缀分组）、`include` 精度、`include,...,1` 后缀、
        `blendFacetsSeparately=true` —— 9 种写法**全是 AND 或空转**：
        两条 rtype 一律 **0**，两个 domain 一律 **7**（MEDLINE ∩ arXiv.org）。
      所以 `compile_filters` 反过来保证**同一 facet 只发一个值**（见该函数与
      `_first_per_facet`），把「多值」这件事挡在编码之前，而不是指望这里能 OR。
    """
    p = library_client.build_params(
        "CentralIndex", "q",
        q_include=["facet_rtype,exact,articles", "facet_tlevel,exact,peer_reviewed"])
    req = requests.Request(
        "GET", library_client.PRIMO_BASE + library_client.SEARCH_PATH,
        params=p).prepare()
    qs = parse_qs(urlparse(req.url).query, keep_blank_values=True)

    assert qs["qInclude"] == [
        "facet_rtype,exact,articles|,|facet_tlevel,exact,peer_reviewed"]
    assert len(qs["qInclude"]) == 1          # 不是两个同名参数 —— 那样只有第一个生效


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
    # 两者都在时 addata.date 必须赢。上面两个各自只有一个候选，
    # 参数写反了也照样通过 —— 这一条才是「优先」的绑定。
    c = _doc(display={"title": ["T"], "creationdate": ["1999"]},
             addata={"date": ["2024-05-01"]}, control={"recordid": ["alma3"]})
    assert library_client.normalize_doc(c)["year"] == "2024"


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


def test_normalize_doc_journal_prefers_jtitle_over_ispartof():
    """两者都在时 addata.jtitle 优先。只有回退分支被绑定时，实现写反了也没人知道。"""
    d = _doc(display={"title": ["T"], "ispartof": ["Nature"]},
             addata={"jtitle": ["Journal of Testing"]}, control={"recordid": ["alma1"]})
    assert library_client.normalize_doc(d)["journal"] == "Journal of Testing"


def test_normalize_doc_survives_completely_empty_doc():
    """上游给个空壳也不能抛 —— 抛了整页结果就没了。"""
    item = library_client.normalize_doc({})
    assert item["title"] == "" and item["url"] == "" and item["record_id"] == ""
    # 实测 Primo 会给**显式 null**，不是缺键 —— `or {}` 就是为它准备的。
    # 改成 `.get(k, {})` 的话这里会抛 AttributeError。
    assert library_client.normalize_doc({"pnx": None})["title"] == ""
    assert library_client.normalize_doc(
        {"pnx": {"display": None, "addata": None, "control": None}})["title"] == ""


# ---------- record_url ----------

def test_record_url_uses_record_own_context():
    url = library_client.record_url("alma991030012933203408", "L")
    assert url.startswith("https://julac-cuh.primo.exlibrisgroup.com/discovery/fulldisplay?")
    assert "docid=alma991030012933203408" in url
    assert "vid=852JULAC_CUH%3ACUH" in url or "vid=852JULAC_CUH:CUH" in url
    assert "context=L" in url


def test_record_url_uses_given_context_argument():
    """context 是实参，不能硬编码成 'L' —— 编目来源不同，context 就不同。"""
    url = library_client.record_url("alma1", "CI")
    assert "context=CI" in url
    assert "context=L" not in url


def test_normalize_doc_url_takes_context_from_that_doc():
    """同一条记录自身的 context 要进链接，不能一律盖成 'L'。

    注意 _doc() 帮手写死了 context='L'，所以这里手动拼 doc。
    """
    doc = {"context": "CI",
           "pnx": {"display": {"title": ["T"]}, "control": {"recordid": ["alma1"]}}}
    url = library_client.normalize_doc(doc)["url"]
    assert "context=CI" in url and "docid=alma1" in url


def test_normalize_doc_url_empty_when_no_record_id():
    """没 recordid 就不能拼链接 —— 拼出个半截 URL 比没链接更糟。"""
    d = _doc(display={"title": ["T"]}, control={})
    assert library_client.normalize_doc(d)["url"] == ""


# ---------- search：分页、has_more、错误 ----------

class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data

    def raise_for_status(self):
        # 空桩会掩盖一整类用户可见的 bug：Primo 返 500/503 且 body 是 JSON 时，
        # 响应会被当成正常结果解析，前端看到的是「无结果」而不是报错。
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _Session:
    """记录调用参数的假 session。"""

    def __init__(self, data, status=200):
        self._data, self._status, self.calls = data, status, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return _Resp(self._data, self._status)

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
    r = library_client.search("MyInstitution", "any,contains,ml", session=s,
                              inside_query=False)
    assert [i["title"] for i in r["items"]] == ["T0", "T1", "T2"]
    assert r["count"] == 100 and r["page"] == 1


def test_search_hits_the_public_pnxs_endpoint(monkeypatch):
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    s = _Session(_search_payload(1))
    library_client.search("CentralIndex", "q", session=s, inside_query=False)
    assert s.calls[0]["url"] == library_client.PRIMO_BASE + library_client.SEARCH_PATH
    assert s.calls[0]["params"]["scope"] == "CentralIndex"


def test_search_has_more_true_only_when_full_page_and_under_cap(monkeypatch):
    """has_more 由后端算，前端不自己推算 —— 否则上限一改前端就骗人。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    full = library_client.search("MyInstitution", "q", session=_Session(_search_payload(20)),
                                 inside_query=False)
    assert full["has_more"] is True
    short = library_client.search("MyInstitution", "q", session=_Session(_search_payload(7)),
                                  inside_query=False)
    assert short["has_more"] is False


def test_search_has_more_false_on_last_allowed_page(monkeypatch):
    """第 5 页（= MAX_PAGES）即使取满也不再有下一页。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    r = library_client.search("MyInstitution", "q", page=5, inside_query=False,
                              session=_Session(_search_payload(20)))
    assert r["has_more"] is False
    # 回显的页码必须是请求的那一页 —— 硬编码 1 的话前端分页会静默错位。
    assert r["page"] == 5


def test_search_rejects_page_above_max(monkeypatch):
    """上限是**后端**挡的。光靠前端不显示按钮，改个请求就绕过去了。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    import pytest
    with pytest.raises(library_client.LibraryError):
        library_client.search("MyInstitution", "q", page=6, inside_query=False,
                              session=_Session(_search_payload(1)))


def test_search_rejects_page_below_one(monkeypatch):
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    import pytest
    with pytest.raises(library_client.LibraryError):
        library_client.search("MyInstitution", "q", page=0, inside_query=False,
                              session=_Session(_search_payload(1)))


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
        library_client.search("MyInstitution", "q", session=_Boom(), inside_query=False)


def test_search_raises_on_http_error_status(monkeypatch):
    """500/503 且 body 恰好是 JSON 时，绝不能当成正常响应解析。

    不抛的话 search 会返回 count=0/items=[]，用户看到的是「没有结果」——
    比报错更糟：他会以为图书馆真没有这本书。
    """
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    import pytest
    with pytest.raises(library_client.LibraryError):
        library_client.search("MyInstitution", "q", inside_query=False,
                              session=_Session({"info": {"total": 0}, "docs": []}, status=503))


def test_get_rejects_non_object_json(monkeypatch):
    """上游返 HTTP 200 + 非对象 JSON（Primo 真会）时必须是 LibraryError。

    AttributeError 不是 LibraryError，会越过调用方的降级分支一路炸上去。
    """
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    import pytest
    for payload in (None, [], "oops"):
        with pytest.raises(library_client.LibraryError):
            library_client.search("MyInstitution", "q", session=_Session(payload),
                                  inside_query=False)


def test_search_survives_missing_info_block(monkeypatch):
    """上游少给 info 不能让整次检索崩掉。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    r = library_client.search("MyInstitution", "q", session=_Session({"docs": []}),
                              inside_query=False)
    assert r["count"] == 0 and r["has_more"] is False


# ---------- list_domains ----------

def test_list_domains_reads_domain_facet(monkeypatch):
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    payload = {"info": {"total": 1}, "docs": [], "facets": [
        {"name": "lang", "values": [{"value": "eng", "count": "5"}]},
        {"name": "domain", "values": [{"value": "ScienceDirect", "count": "3"},
                                      {"value": "MEDLINE", "count": "2"}]}]}
    s = _Session(payload)
    assert library_client.list_domains(session=s) == ["ScienceDirect", "MEDLINE"]
    # 词表来源必须钉死：探针走的是中央索引（CentralIndex）。
    # 换成 MyInstitution 就变成另一套词表了，而且不会报错，只会静默少命中。
    assert s.calls[0]["params"]["scope"] == library_client.SCOPE_EXTERNAL
    assert s.calls[0]["params"]["scope"] == "CentralIndex"
    assert s.calls[0]["timeout"] == 30


def test_domain_probe_query_is_pinned_and_actually_sent(monkeypatch):
    """R11-b：`_DOMAIN_PROBE_Q` 是**词表探测的输入**，值可被静默改坏而既有用例全绿
    —— 而 `list_domains` 正是靠它拿回那 66 个数据库。

    两件事一起钉，缺一不可：
      - 常量的**字面值**（写死 `"any,contains,the"`，不写
        `== library_client._DOMAIN_PROBE_Q` —— 那是同义反复，两边一起变，改坏了也绿）；
      - 它**真的被送上请求**（改值 / 删接线两种改坏方式各有一条断言管）。
    """
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    assert library_client._DOMAIN_PROBE_Q == "any,contains,the"

    s = _Session(_facet_payload(["MEDLINE"]))
    library_client.list_domains(session=s)
    assert s.calls[0]["params"]["q"] == "any,contains,the"


def test_list_domains_returns_empty_when_facet_absent(monkeypatch):
    """没有 domain facet（上游改版）→ 空列表，不是抛错。调用方据此不做收窄。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    assert library_client.list_domains(session=_Session({"docs": [], "facets": []})) == []


def test_list_domains_returns_empty_on_transport_error(monkeypatch):
    """拿不到 → []，不抛错。这个降级分支是该函数的**全部意义**（选库是增强不是主干）。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)

    class _Boom:
        def get(self, *a, **k):
            raise requests.ConnectionError("no route")

        def close(self):
            pass

    assert library_client.list_domains(session=_Boom()) == []


def test_list_domains_returns_empty_on_non_object_json(monkeypatch):
    """上游返 200 + 非对象 JSON 时也要降级成 []，而不是让 AttributeError 逃出去。

    AttributeError 会越过 `except LibraryError`，把「不做领域收窄」升级成整次检索失败。
    """
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    for payload in (None, [], "oops"):
        assert library_client.list_domains(session=_Session(payload)) == []


# ---------- list_domains 的缓存（spec §4.2「与 area 同策略」） ----------

def _facet_payload(domains):
    return {"info": {"total": 1}, "docs": [],
            "facets": [{"name": "domain",
                        "values": [{"value": d, "count": "1"} for d in domains]}]}


def test_list_domains_caches_and_second_call_hits_no_network(tmp_path, monkeypatch):
    """词表 7 天不变 —— 缓存命中就绝不再打上游（旧实现每翻一页都多打一次）。"""
    monkeypatch.setattr(library_client, "CACHE_FILE", tmp_path / "domains.json")
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    s = _Session(_facet_payload(["ScienceDirect", "MEDLINE"]))
    assert library_client.list_domains(session=s) == ["ScienceDirect", "MEDLINE"]
    assert len(s.calls) == 1

    s2 = _Session(_facet_payload(["ScienceDirect", "MEDLINE"]))
    assert library_client.list_domains(session=s2) == ["ScienceDirect", "MEDLINE"]
    assert s2.calls == []           # 命中缓存 → 一次网络都没打


def test_list_domains_refetches_when_cache_expired(tmp_path, monkeypatch):
    cache = tmp_path / "domains.json"
    monkeypatch.setattr(library_client, "CACHE_FILE", cache)
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    library_client.list_domains(session=_Session(_facet_payload(["OLD"])))

    raw = json.loads(cache.read_text(encoding="utf-8"))
    raw["fetched_at"] = time.time() - library_client.CACHE_TTL - 10    # 弄成过期
    cache.write_text(json.dumps(raw), encoding="utf-8")

    s = _Session(_facet_payload(["NEW"]))
    assert library_client.list_domains(session=s) == ["NEW"]
    # 过期 ⇒ 必须真的重抓过（否则测的是「缓存没过期」，重抓路径根本没走到）
    assert len(s.calls) == 1


def test_list_domains_falls_back_to_stale_cache_on_failure(tmp_path, monkeypatch):
    """过期了、重抓又失败 → 旧词表也比没有强（与 area 的降级逐条对齐）。"""
    cache = tmp_path / "domains.json"
    monkeypatch.setattr(library_client, "CACHE_FILE", cache)
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    library_client.list_domains(session=_Session(_facet_payload(["MEDLINE"])))

    raw = json.loads(cache.read_text(encoding="utf-8"))
    raw["fetched_at"] = time.time() - library_client.CACHE_TTL - 10
    cache.write_text(json.dumps(raw), encoding="utf-8")

    class _Boom:
        def get(self, *a, **k):
            raise requests.ConnectionError("no route")

        def close(self):
            pass

    assert library_client.list_domains(session=_Boom()) == ["MEDLINE"]


def test_list_domains_treats_corrupt_cache_as_absent(tmp_path, monkeypatch):
    """缓存被写坏（截断 / 手工改）→ 当没有，重新抓，不抛错。"""
    cache = tmp_path / "domains.json"
    cache.write_text("{{{ not json", encoding="utf-8")
    monkeypatch.setattr(library_client, "CACHE_FILE", cache)
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    s = _Session(_facet_payload(["MEDLINE"]))
    assert library_client.list_domains(session=s) == ["MEDLINE"]
    assert len(s.calls) == 1


def test_list_domains_rejects_malformed_cache_entries(tmp_path, monkeypatch):
    """形状校验（照 R13-a 对 area 缓存做的同一标准）：`{"domains":[1,2,3]}` 是合法
    JSON、也是非空 list，但不是契约里的「字符串列表」—— 当没有，重抓；
    重抓也失败时返回 `[]`，绝不能把 [1,2,3] 喂进 LLM 提示词。
    """
    cache = tmp_path / "domains.json"
    monkeypatch.setattr(library_client, "CACHE_FILE", cache)
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    cache.write_text(json.dumps({"fetched_at": time.time(), "domains": [1, 2, 3]}),
                     encoding="utf-8")

    s = _Session(_facet_payload(["MEDLINE"]))
    assert library_client.list_domains(session=s) == ["MEDLINE"]
    assert len(s.calls) == 1        # 畸形缓存没有被当成命中

    cache.write_text(json.dumps({"fetched_at": time.time(), "domains": [1, 2, 3]}),
                     encoding="utf-8")

    class _Boom:
        def get(self, *a, **k):
            raise requests.ConnectionError("no route")

        def close(self):
            pass

    assert library_client.list_domains(session=_Boom()) == []


# ---------- 限流 ----------

def test_throttle_sleeps_between_two_calls(monkeypatch):
    """相邻两次请求之间必须真的等 —— 这条兜的是用户连点翻页。"""
    slept = []
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0.05)
    monkeypatch.setattr(library_client, "_last_call", 0.0)
    monkeypatch.setattr(library_client.time, "sleep", lambda s: slept.append(s))
    library_client._throttle()   # _last_call 是 0（很久以前）→ 不该睡
    library_client._throttle()   # 第二次必须睡
    assert len(slept) == 1 and slept[0] > 0


def test_min_interval_production_value_actually_throttles(monkeypatch):
    """R11-a：**生产取值的** `_MIN_INTERVAL` 必须真有节流力度，且真的被用上。

    为什么不能只靠 `test_throttle_sleeps_between_two_calls`：那条自己把常量
    monkeypatch 成 `0.05` —— 它验的是**机制**，从头到尾没碰过生产取值，所以把
    `_MIN_INTERVAL = 1.0` 改成 `0.0` 时它照样绿。而这条常量是「不做全量收割」那道
    闸的机械保证（spec §9「相邻翻页请求之间 ≥ 1s」）。

    判据刻意**不 monkeypatch 这个常量**：把 `_last_call` 拨成「刚刚」、spy 住
    `time.sleep`，于是 `_throttle()` 请求的睡眠时长必须 ≈ 当前取值 —— 一次同时钉住
    「取值有力度」与「它真的被用上」（少了后者的实现可以直接把 `_throttle` 架空）。
    """
    slept = []
    monkeypatch.setattr(library_client.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(library_client, "_last_call", time.monotonic())   # 刚刚调过一次
    library_client._throttle()
    # 距上次调用的真实间隔是微秒级，所以睡眠 ≈ _MIN_INTERVAL（少那一丁点）
    assert slept == [pytest.approx(library_client._MIN_INTERVAL, abs=0.01)]
    # 下界取 0.5 的理由：spec §9 的原文下限是 **1s**；0.5 是「这条常量仍在履行限流
    # 职责」的下界（连规格的一半都不到就已经不是同一件事了），同时给将来**向上**
    # 调参留空间 —— 本批要杀的是「→ 0.0 静默废掉限流」，不是钉死某个具体数字。
    assert library_client._MIN_INTERVAL >= 0.5


def test_get_actually_goes_through_throttle(monkeypatch):
    """_throttle 自己对了还不够 —— 调用点把它删掉的话限流等于不存在。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    calls = []
    monkeypatch.setattr(library_client, "_throttle", lambda: calls.append(1))
    s = _Session(_search_payload(1))
    library_client.search("MyInstitution", "q", session=s, inside_query=False)
    library_client.list_domains(session=s)
    assert calls == [1, 1]


# ---------- 节流与并发的作用域（F-3） ----------

def test_query_scope_sleeps_between_two_queries(monkeypatch):
    """相邻两次**查询**之间 ≥ _MIN_INTERVAL —— spec §9 的原文是「相邻翻页请求之间」
    （一次翻页 = 一次查询），不是「一次查询内部的各次调用之间」。"""
    slept = []
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0.05)
    monkeypatch.setattr(library_client, "_last_query_at", 0.0)
    monkeypatch.setattr(library_client.time, "sleep", lambda s: slept.append(s))
    with library_client.query_scope():      # _last_query_at 是 0（很久以前）→ 不该睡
        pass
    with library_client.query_scope():      # 第二次必须睡
        pass
    assert len(slept) == 1 and slept[0] > 0


def test_inside_query_is_a_required_keyword_argument(monkeypatch):
    """`inside_query` **必须传**：漏传立刻 `TypeError`，绝不落到一个「默默当 False」的默认值。

    为什么默认值在这里是危险品：漏传的后果**不是**「多睡一秒」，而是**永久死锁** ——
    `query_scope()` 在端点线程上持有非重入的 `_query_lock`，端点在 `fut.result()` 上等
    worker，而漏传的 worker 会走 `_get` 的默认路径去取同一把锁 ⇒ 互等。
    （复审用两组独立进程实测过：漏传 6 秒不返回；传了立刻 ok。）

    **这条用例刻意不碰网络、也不经过任何替身**：判据就是签名本身，`TypeError` 在函数体
    执行之前就抛出来了 —— 这正是「不可能静默发生」的含义。反面教训是 `search` 的替身
    （`lambda scope, q, **k`）会把漏传照单全收：`tests/test_main.py` 的 `_patch_library`
    把 `search` 整个换掉，所以那条路径**结构上看不见**这个问题，不能拿它来验这一条。
    """
    import pytest
    # 兜底：万一有人把默认值加回来，本用例也必须**在到达网络之前**就失败。
    # （不加这条的话，变异实现会让下面第二行真的打一次真实 Primo —— 实测踩过：
    # 这正是「测试不打真实网络」这条硬约束的反面教材。）
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(library_client, "_last_call", 0.0)
    monkeypatch.setattr(library_client, "_last_query_at", 0.0)
    monkeypatch.setattr(library_client, "_http_get",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("漏传必须在这里之前就抛 TypeError，绝不能走到网络")))

    with pytest.raises(TypeError):
        library_client.search("MyInstitution", "q")           # 漏 inside_query
    with pytest.raises(TypeError):
        library_client._get("/primaws/rest/pub/pnxs", {}, None)   # 漏 inside_query


def test_the_inside_query_flag_is_what_stands_the_throttle_down(monkeypatch):
    """**同一次查询内部**的上游调用不互相节流（F-3 的 2.0 秒纯睡眠就是在这里白等的）。

    「放行」的判据本轮换成了**调用方显式声明的 `inside_query=True`**，不再是
    `_throttle` 自己读 `_query_lock.locked()` 猜（见 D1：那把锁是进程级的，别的请求
    持锁时门外的调用会被误判成门内）。两条语义同时钉住：
    标了的放行，**没标的一律按「查询之外」等满间隔**。
    """
    slept = []
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 1.0)
    monkeypatch.setattr(library_client, "_last_call", time.monotonic())   # 刚调过一次
    monkeypatch.setattr(library_client.time, "sleep", lambda s: slept.append(s))
    s = _Session(_search_payload(1))

    library_client.search("MyInstitution", "q", session=s, inside_query=True)
    assert slept == []                       # 门内的两栏：不等（传了标志）

    library_client.search("MyInstitution", "q", session=s, inside_query=False)
    assert len(slept) == 1                   # 门外：照等（没传标志）


def test_list_domains_probe_is_throttled(monkeypatch):
    """`list_domains()` 的探针是**查询之外**的调用 ⇒ 逐调用兜底节流照旧生效。

    复审 D1 的实测反例：旧实现里这次探针读到「别的查询正持锁」就跳过等待
    （等待 0.0s）。现在它只有一个判据 —— 自己没传 `inside_query`。
    """
    slept = []
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 1.0)
    monkeypatch.setattr(library_client, "_last_call", time.monotonic())   # 刚调过一次
    monkeypatch.setattr(library_client.time, "sleep", lambda s: slept.append(s))
    library_client.list_domains(session=_Session(_facet_payload(["MEDLINE"])))
    assert len(slept) == 1


def test_an_out_of_query_call_never_overlaps_a_query(monkeypatch):
    """**上游在飞峰值恒 ≤ 2**（spec §9 只允许同一次查询的两个 scope 并发）。

    复现 D1 的原始场景：查询 A 已在门内、两栏在飞；查询 B 走到 `list_domains()`
    的探针（冷缓存）。旧实现里探针读 `_query_lock.locked()`，看到 A 持锁 ⇒ 误判
    「我也在查询里」⇒ 放行，三条上游请求同时在飞（复审实测峰值 3）。
    现在探针是门外的调用，整段持有 `_query_lock` ⇒ 只能排在 A 之后。

    确定性：门内两栏卡在 `release_query` 上直到测试放行，所以「两栏在飞」是被
    观察到的状态而不是睡眠赌出来的；探针若真被放行（变异实现），它必然在这两栏
    还卡着的时候完成 —— `probe_done.wait` 那一行会先红，`peak == 2` 再红一次。
    """
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(library_client, "_last_call", 0.0)
    monkeypatch.setattr(library_client, "_read_cache", lambda: [])   # 冷缓存 ⇒ 要走探针

    in_flight = 0
    peak = 0
    counter_lock = threading.Lock()
    two_in_flight = threading.Event()
    release_query = threading.Event()

    class _CountingSession:
        """进 `get` 就记账（这就是「上游在飞」的度量点），门内那两个还额外卡住。"""

        def __init__(self, hold=False):
            self._hold = hold

        def get(self, *a, **k):
            nonlocal in_flight, peak
            with counter_lock:
                in_flight += 1
                peak = max(peak, in_flight)
                if in_flight >= 2:
                    two_in_flight.set()
            try:
                if self._hold:
                    release_query.wait(5)
            finally:
                with counter_lock:
                    in_flight -= 1
            return _Resp(_search_payload(1))

        def close(self):
            pass

    def run_query():
        with library_client.query_scope():
            # 端点的两个提交点等价物：都显式传 inside_query=True
            threads = [threading.Thread(target=library_client.search,
                                        args=("MyInstitution", "q"),
                                        kwargs={"session": _CountingSession(hold=True),
                                                "inside_query": True})
                       for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(5)

    probe_done = threading.Event()

    def run_probe():
        library_client.list_domains(session=_CountingSession())
        probe_done.set()

    query_thread = threading.Thread(target=run_query)
    query_thread.start()
    assert two_in_flight.wait(5)          # 门内两栏确实同时在飞

    probe_thread = threading.Thread(target=run_probe)
    probe_thread.start()

    # 正确实现：探针被门挡住，进不了网络（这 0.5s 是给线程被调度起来的余量）；
    # 变异实现（把标志改回读锁状态）：探针立刻打进网络 → 这一行就是红的。
    assert probe_done.wait(0.5) is False

    release_query.set()
    query_thread.join(5)
    probe_thread.join(5)
    assert probe_done.is_set()            # 门一放开，探针照常完成（不是被永久挡住）
    assert peak == 2                      # 全程峰值 2，不是 3


def test_query_scope_serializes_queries(monkeypatch):
    """不同查询之间**串行**：后一个查询在前一个退出前进不了门（spec §9
    「这是唯一的一处并发；不同查询之间串行」）。

    有界 join 只是给第二个查询一个（错误地）闯进来的机会：并发实现必然在那个窗口内
    留下 `inside` 记录 → 断言失败；串行实现则**任何时序下**都通过。
    """
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(library_client, "_last_query_at", 0.0)
    inside = []
    first_in = threading.Event()
    release_first = threading.Event()

    def first_query():
        with library_client.query_scope():
            first_in.set()
            release_first.wait(5)

    def second_query():
        with library_client.query_scope():
            inside.append("second")

    t1 = threading.Thread(target=first_query)
    t1.start()
    assert first_in.wait(5)
    t2 = threading.Thread(target=second_query)
    t2.start()
    t2.join(0.3)
    assert inside == []                  # 门被第一个查询占着 → 第二个进不来

    release_first.set()
    t1.join(5)
    t2.join(5)
    assert inside == ["second"]          # 第一个退出后它才进 —— 顺序没丢


def test_concurrent_searches_each_use_their_own_session(monkeypatch):
    """`requests.Session` **不是线程安全的**：并发发出的两个 scope 绝不能共用一个
    （spec §3.2 的并发前提）。search(session=None) 每次调用自建、自关。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    ids = []
    barrier = threading.Barrier(2, timeout=5)

    class _ProbeSession:
        def __init__(self):
            ids.append(id(self))

        def get(self, *a, **k):
            barrier.wait()       # 两次调用必须同时在飞，否则并发根本没发生
            return _Resp({"info": {"total": 0}, "docs": []})

        def close(self):
            pass

    monkeypatch.setattr(library_client.requests, "Session", _ProbeSession)
    errors = []

    def run(scope):
        try:
            # `inside_query=True` = 端点两个提交点的等价物。**并发的只有这两栏**
            # （spec §9）：查询之外的调用现在串行（见 _get），所以只有门内的调用
            # 才可能同时在飞 —— Session 线程安全这条不变量也只在这里受考验。
            library_client.search(scope, "q", inside_query=True)
        except Exception as exc:      # noqa: BLE001 —— 收集起来在断言里看
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(s,))
               for s in (library_client.SCOPE_LOCAL, library_client.SCOPE_EXTERNAL)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert errors == []
    assert len(set(ids)) == 2            # 两次调用拿到的是两个不同的 Session


def test_get_closes_only_the_session_it_created(monkeypatch):
    """自建的 session 必须自己关（否则每次检索漏一个连接池）；调用方传进来的不能关。"""
    monkeypatch.setattr(library_client, "_MIN_INTERVAL", 0)
    closed = []

    class _FakeSession:
        def get(self, *a, **k):
            return _Resp({"info": {"total": 0}, "docs": []})

        def close(self):
            closed.append(True)

    monkeypatch.setattr(library_client.requests, "Session", _FakeSession)
    library_client.search("MyInstitution", "q", inside_query=False)   # session=None → _get 自建
    assert closed == [True]

    class _SessionTracked(_Session):
        def close(self):
            closed.append(True)

    closed.clear()
    library_client.search("MyInstitution", "q", inside_query=False,
                          session=_SessionTracked(_search_payload(1)))
    assert closed == []
