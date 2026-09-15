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


def test_build_q_dedupes_terms_keeping_first_order():
    """同一个词不能出现在 q 里两次。

    改动前实测复现：LLM 只给 synonyms 时 `parse_natural` 拿它顶上 keywords 却不清空
    来源字段 → `q = "any,contains,machine learning deep learning machine learning
    deep learning"`（每个词两遍）。
    """
    q = library_query.build_q({"keywords": ["ML", "DL"], "synonyms": ["ML", "DL"]})
    assert q == "any,contains,ML DL"
    assert q.count("ML") == 1 and q.count("DL") == 1


def test_synonym_promotion_does_not_duplicate_q(monkeypatch):
    """端到端走一遍 LLM 只给 synonyms 的降级提升路径，钉住 q 里的重复。"""
    monkeypatch.setattr(llm_client, "_call_chat",
                        lambda *a, **k: '{"keywords":[],"synonyms":["ML","DL"]}')
    parsed, warnings = library_query.parse_natural("u", "k", "m", "x")
    assert library_query.build_q(parsed) == "any,contains,ML DL"
    assert warnings == []


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


# =========================================================================
# 修复回合 1（复审 F1 / F2 / F3 / F4 / F6）
# 简报原有的 31 条用例逐字节未动，这里全部是新增。
# =========================================================================

# ---------- F1：颠倒区间不能让年份约束无声蒸发（真缺陷修复） ----------

def test_compile_filters_inverted_year_range_still_filters():
    """year_to < year_from 时 `range()` 为空 → **一条 creationdate 都不生成**，
    年份约束无声蒸发：不报错、不 0 命中，回显里却还写着 2024–2020。"""
    inc, _ = library_query.compile_filters({"year_from": 2024, "year_to": 2020},
                                           "MyInstitution")
    assert "creationdate,exact,2024" in inc


def test_compile_filters_future_year_from_survives_now_cap():
    """第二个可达入口：`_as_year` 明确放行 now+1，但 end 被 min(now, ...) 卡在 2026
    → start=max(2027,...)=2027、range(2027,2027) 为空，同样蒸发。"""
    inc, _ = library_query.compile_filters({"year_from": 2027, "year_to": None},
                                           "MyInstitution")
    assert "creationdate,exact,2027" in inc


# ---------- F2：rtype 是两侧通用维度，不能被 is_pc 门控 ----------

def test_compile_filters_keeps_rtype_on_local_scope():
    """既有真机实测：`rtype,exact,databases` + scope=MyInstitution 返回 CityU 全部
    66 个库 —— 所以 rtype **不是** PC 独有 facet（与 tlevel/attribute/domain 不同）。"""
    inc, _ = library_query.compile_filters({"rtype": ["articles"]}, "MyInstitution")
    assert "rtype,exact,articles" in inc


# ---------- F3：area 的全部约束力都押在提示词那句一致性约束上（spec §4.1） ----------

def test_build_prompt_states_area_consistency_constraint():
    """spec §4.1：area 不编译成任何参数（`areas` 在 §6 无编译条），所以提示词里
    「你选的 terms / rtype / domains 必须与该领域一致」这句是**唯一的约束力**。
    删掉整段 area_note 也曾全绿 —— 这条把话钉住。"""
    p = library_query.build_prompt("q", "comp", {"comp": "Computer Science"}, [])
    assert "Computer Science" in p
    assert "必须与该领域一致" in p
    for field in ("terms", "rtype", "domains"):
        assert field in p


def test_build_prompt_omits_area_constraint_when_no_area():
    """反面：用户没选领域时不该冒出「已限定」的约束。"""
    p = library_query.build_prompt("q", None, {}, [])
    assert "必须与该领域一致" not in p
    assert "用户没有指定学科领域" in p


# ---------- F4：提示词接线 ----------

def test_parse_natural_wires_area_and_domains_into_prompt(monkeypatch):
    """接线本身：area / domains / area_names 必须真的进 build_prompt。
    接错线（area 丢了、domains 传成 []）时 32 条曾经全绿。"""
    seen = {}

    def fake_prompt(text, area, area_names, domains):
        seen.update(text=text, area=area, area_names=area_names, domains=domains)
        return "PROMPT"

    monkeypatch.setattr(library_query, "build_prompt", fake_prompt)
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: '{"keywords":["x"]}')
    library_query.parse_natural("u", "k", "m", "原始文本", area="comp",
                                area_names={"comp": "Computer Science"},
                                domains=["MEDLINE"])
    assert seen["text"] == "原始文本"
    assert seen["area"] == "comp"
    assert seen["area_names"] == {"comp": "Computer Science"}
    assert seen["domains"] == ["MEDLINE"]


# ---------- F4：归一化的其余未绑定行为 ----------

def test_parse_natural_degrades_when_llm_returns_non_dict(monkeypatch):
    """合法 JSON 但不是对象（数组）→ 同样降级。带 domains 调用能杀死「删掉
    isinstance 守卫」的变异：那时 `raw.get("domains")` 会 AttributeError 炸穿。"""
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: '["ml", "dl"]')
    parsed, warnings = library_query.parse_natural("u", "k", "m", "原文",
                                                   domains=["MEDLINE"])
    assert warnings == ["llm_degraded"] and parsed["keywords"] == ["原文"]


def test_parse_natural_degrades_on_empty_json_object(monkeypatch):
    """`{}` 是合法 JSON 但没有任何检索意图 → 降级，而不是「静默空检索式」。"""
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: "{}")
    parsed, warnings = library_query.parse_natural("u", "k", "m", "原文")
    assert warnings == ["llm_degraded"] and parsed["keywords"] == ["原文"]


def test_validate_parsed_coerces_flags_to_bool():
    """三个布尔开关必须真的归一成 bool（用 `is True` / `is False` 钉住，
    否则 1 / "yes" / 0 这类原样透传也能过）。"""
    p = library_query.validate_parsed({"open_access_only": 1,
                                       "peer_reviewed_only": "yes",
                                       "exclude_retracted": 0})
    assert p["open_access_only"] is True
    assert p["peer_reviewed_only"] is True
    assert p["exclude_retracted"] is False
    assert library_query.validate_parsed({})["open_access_only"] is False


def test_validate_parsed_normalizes_language():
    """language 记录 + 随 parsed 回显（spec §6 无编译条，故不生成过滤），
    但归一化该做：去空白、空串当没有、超长截断。"""
    assert library_query.validate_parsed({"language": "  en  "})["language"] == "en"
    assert library_query.validate_parsed({"language": ""})["language"] is None
    assert library_query.validate_parsed({"language": None})["language"] is None
    assert len(library_query.validate_parsed({"language": "x" * 5000})["language"]) <= 200


def test_validate_parsed_truncates_explanation():
    """explanation 上限是 _MAX_STR * 2（400），且 None 要变空串不是 "None"。"""
    assert len(library_query.validate_parsed({"explanation": "x" * 5000})["explanation"]) == 400
    assert library_query.validate_parsed({"explanation": None})["explanation"] == ""
    assert library_query.validate_parsed({})["explanation"] == ""


def test_as_list_drops_blank_entries_and_strips():
    """空串 / 纯空白条目要丢掉，非空条目要去掉首尾空白。"""
    p = library_query.validate_parsed({"keywords": ["", "   ", "a", "  b  "]})
    assert p["keywords"] == ["a", "b"]


def test_fallback_parsed_returns_full_shape_with_single_keyword():
    """退化结果也必须是**完整 parsed 形状**（前端要原样回显），
    关键词是原文一条 —— 不是把整串塞进 list 了事。"""
    p = library_query.fallback_parsed("  机器学习论文  ")
    assert set(p) == set(library_query.EMPTY_PARSED)
    assert p["keywords"] == ["机器学习论文"]
    assert library_query.fallback_parsed("")["keywords"] == []


def test_compile_filters_year_cap_keeps_the_recent_end():
    """区间被 _YEAR_SPAN_CAP 截断时保留**靠今年的一端**（1900–2026 → 1997–2026）：
    改成保留远端（1900–1929）时 32 条曾经全绿。"""
    inc, _ = library_query.compile_filters({"year_from": 1900, "year_to": 2026},
                                           "MyInstitution")
    cds = [e for e in inc if e.startswith("creationdate")]
    assert "creationdate,exact,2026" in cds
    assert "creationdate,exact,1900" not in cds
    assert len(cds) == 30


def test_year_span_cap_is_silent_and_does_not_rewrite_parsed():
    """F-7：30 年是**实现自定**的界（规格 §6 只写「逐年一条」，没有任何上限），
    截断只发生在**编译出的过滤集**上 —— `parsed.year_from` 照旧是用户原值，
    所以这次收窄是静默的（回显写着 1900–2026，实际只过滤 1997–2026）。
    钉住「选定的行为」：保留靠今年的一端、只收窄过滤集、`_YEAR_SPAN_CAP` 不动。
    """
    parsed = {"year_from": 1900, "year_to": 2026}
    inc, _ = library_query.compile_filters(parsed, "MyInstitution")
    cds = [e for e in inc if e.startswith("creationdate")]
    assert cds == [f"creationdate,exact,{y}" for y in range(1997, 2027)]   # 恰好 30 条
    assert parsed["year_from"] == 1900      # 用户输入不被改写 —— 这正是「静默」之处


# ---------- F6：把 3 条弱断言补出判别力（新增用例，原用例不动） ----------

def test_build_q_empty_is_exactly_the_bare_prefix():
    """原用例只 startswith，加个尾巴（"any,contains, "）也能过。"""
    assert library_query.build_q({"keywords": [], "synonyms": []}) == "any,contains,"
    assert library_query.build_q({}) == "any,contains,"


def test_build_prompt_marks_missing_catalogs_placeholder():
    """原用例是 smoke（只断言输入出现在输出里）。词表缺失时两处占位都要在。"""
    p = library_query.build_prompt("q", None, {}, [])
    assert p.count("（暂不可用）") == 2      # 学科领域一处、来源数据库一处
    assert "用户没有指定学科领域" in p


def test_known_rtype_values_are_plural_and_singulars_dropped():
    """原用例没验证「复数」这件事本身 —— 实测 rtype 的值是复数，单数返回 0 命中，
    所以单数必须被挡在门外（而不是被「补个 s」）。"""
    assert "articles" in library_query.KNOWN_RTYPE
    assert library_query.validate_parsed(
        {"rtype": ["article", "database", "journal"]})["rtype"] == []


# ---------- 别名不变量：构造函数绝不能返回与 EMPTY_PARSED 共享的 list ----------

def test_fallback_parsed_returns_independent_lists():
    """`parsed["areas"] is EMPTY_PARSED["areas"]` 曾经为 True（dict() 是浅拷贝）。

    端点会就地 `insert` 回填用户选的 area，于是共享的那个 list 被永久改写，
    模块常量跨请求泄漏。这里直接钉住「不是同一个对象」。
    """
    p = library_query.fallback_parsed("anything")
    assert p["areas"] is not library_query.EMPTY_PARSED["areas"]
    assert p["domains"] is not library_query.EMPTY_PARSED["domains"]


def test_validate_parsed_none_returns_independent_lists():
    """同一条路径：`validate_parsed(None)` 也是浅拷贝的受害点。"""
    p = library_query.validate_parsed(None)
    assert p["areas"] is not library_query.EMPTY_PARSED["areas"]
    assert p["domains"] is not library_query.EMPTY_PARSED["domains"]


def test_blank_parsed_returns_independent_lists():
    """两个降级入口之外，直接钉住构造函数本身：日后新增 list 字段也自动覆盖。"""
    for key, value in library_query.EMPTY_PARSED.items():
        if isinstance(value, list):
            assert library_query._blank_parsed()[key] is not value, key


def test_mutating_returned_parsed_does_not_pollute_empty_parsed():
    """污染回归：改返回值（append/insert）后，常量长度必须纹丝不动。

    端点上那条 `parsed["areas"].insert(0, req.area)` 就是靠这条兜住的。
    """
    before = {k: len(v) for k, v in library_query.EMPTY_PARSED.items()
              if isinstance(v, list)}
    p = library_query.fallback_parsed("x")
    p["areas"].insert(0, "comp")
    p["domains"].append("MEDLINE")
    p["keywords"].append("more")
    p2 = library_query.validate_parsed(None)
    p2["areas"].append("math")
    p2["domains"].insert(0, "MEDLINE")
    after = {k: len(v) for k, v in library_query.EMPTY_PARSED.items()
             if isinstance(v, list)}
    assert before == after
    assert library_query.EMPTY_PARSED["areas"] == []
    assert library_query.EMPTY_PARSED["domains"] == []
    assert library_query.validate_parsed(None)["areas"] == []
