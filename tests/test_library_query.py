"""检索式解析与编译。LLM 一律用替身，不打真实网络。"""
from datetime import datetime

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


def test_validate_parsed_year_upper_bound_is_pinned():
    """上界**本身**也要钉住：只测 1200 / 9999 的话，把上界从 now+1 放宽到
    now+5000 依然全绿（实测该变异存活）—— 但那时 2100 这种「不是年份」的
    客户端输入就会原样进上游。明年（in-press）要放行，过远的将来要挡。"""
    now = datetime.now().year
    assert library_query.validate_parsed({"year_to": now + 1})["year_to"] == now + 1
    assert library_query.validate_parsed({"year_to": now + 5})["year_to"] is None


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
