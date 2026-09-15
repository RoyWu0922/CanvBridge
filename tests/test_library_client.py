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
