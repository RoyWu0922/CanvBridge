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
