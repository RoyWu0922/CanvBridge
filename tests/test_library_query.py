"""检索式解析与编译。LLM 一律用替身，不打真实网络。"""
import re
from datetime import datetime
from pathlib import Path

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


def test_compile_filters_prefixes_every_facet_expression():
    """**根因**：Primo 的 qInclude 吃的是 facet 表达式 `facet_<name>,<precision>,<value>`。

    不带 `facet_` 前缀时 CentralIndex **一律 0 命中**（实测 `rtype,exact,articles`
    两侧都返回 0），本馆侧有的被**静默忽略**（`tlevel,exact,peer_reviewed` 返回
    34,105 = 完全没过滤的基线，看着「没报错」，其实那个勾根本没生效）。

    上游权威来源是 Primo UI 自己的 `facetToString`（`discovery/lib/bundle.js`）：
    它**永远**拼 `"facet_"+name+","+precision+","+value`，区间 facet 拼
    `"facet_"+name+","+precision+",["+a+" TO "+b+"]"`。

    这里逐个检查**编译出的每一个 facet 表达式**都带前缀 —— 漏掉任何一个，
    对应的那个筛选项就会静默失效。

    ⚠️ **覆盖范围的如实说明**：本用例的夹具**不含 `domains`**，所以它**看不到**
    `domain` 那条 —— 而 `domain` 正是**唯一刻意不带前缀**的 facet（两种写法实测
    结果不同，机制未查明，见 `compile_filters` 里的说明），把它放进夹具会让
    `startswith("facet_")` 直接红。`domain` 的形态由
    `test_compile_filters_puts_domain_on_external_scope_only` 单独钉住。
    """
    parsed = {"rtype": ["articles"], "peer_reviewed_only": True,
              "open_access_only": True, "year_from": 2024, "year_to": 2024}
    for scope in ("MyInstitution", "CentralIndex"):
        inc, _ = library_query.compile_filters(parsed, scope)
        assert inc, f"{scope} 竟然编译出空过滤集"
        for expr in inc:
            assert expr.startswith("facet_"), (scope, expr)


def test_compile_filters_uses_plural_rtype():
    """实测：rtype 的值是**复数**。'database' 返回 0，'databases' 才对。

    断言**整个列表**而不是 `in`：`facet_` 前缀、逗号精度、值三处任一写错都要弄红。
    """
    inc, exc = library_query.compile_filters({"rtype": ["databases"]}, "MyInstitution")
    assert inc == ["facet_rtype,exact,databases"]


def test_compile_filters_excludes_retracted():
    inc, exc = library_query.compile_filters({"exclude_retracted": True}, "CentralIndex")
    assert exc == ["facet_attribute,exact,retracted_publication"]


def test_compile_filters_tlevel_applies_to_both_scopes():
    """**实测推翻了 spec §6 的前提。** 规格写「tlevel 是 Primo Central 独有的 facet」，
    可本馆**有** tlevel，而且过滤真的生效：`any,contains,machine learning` 本馆
    无过滤 **34,105** → `facet_tlevel,exact,peer_reviewed` **45**。

    留着旧的 PC-only 门控 = 用户勾的「同行评审」「开放获取」在本馆那栏**静默失效**
    —— 这正是本次要修的那一类缺陷，不能在修别的 facet 时把它留在原地。两个 scope 都发。
    """
    parsed = {"peer_reviewed_only": True, "open_access_only": True}
    for scope in ("MyInstitution", "CentralIndex"):
        inc, _ = library_query.compile_filters(parsed, scope)
        assert "facet_tlevel,exact,peer_reviewed" in inc, scope
        assert "facet_tlevel,exact,open_access" in inc, scope


def test_compile_filters_attribute_stays_external_only():
    """`attribute`（撤稿标记）仍只在 PC：Retraction Watch 是外部数据源，
    本馆馆藏没有这个维度。spec §6 的门控对 attribute 成立（对 tlevel 不成立，
    见上一条 —— 同一个门控里两个 facet 的实测结论相反）。"""
    parsed = {"exclude_retracted": True}
    assert library_query.compile_filters(parsed, "MyInstitution")[1] == []
    assert library_query.compile_filters(parsed, "CentralIndex")[1] == [
        "facet_attribute,exact,retracted_publication"]


def test_compile_filters_puts_domain_on_external_scope_only():
    """domain 是 PC 侧概念（来源数据库），本馆馆藏没有这个维度。

    **`domain` 故意不带 `facet_` 前缀**：两种写法行为**不同**（实测 arXiv.org：
    `domain,exact,` **38** 条 vs `facet_domain,exact,` **142** 条），而 spec §3.3
    验证过的是**不带**前缀那条。差异机制未查明 ⇒ 按已验证的那条走，查明前不动它。
    """
    parsed = {"domains": ["MEDLINE"]}
    assert library_query.compile_filters(parsed, "CentralIndex")[0] == ["domain,exact,MEDLINE"]
    assert library_query.compile_filters(parsed, "MyInstitution")[0] == []


def test_compile_filters_year_becomes_one_range_expression():
    """**真缺陷（根因之二）**：逐年一条 `creationdate,exact,<year>` 在 CentralIndex 上是
    **完全空转**的 —— 实测 `any,contains,machine learning`：无年份过滤 **2,082,251**
    → `exact,2014` 仍 **2,082,251** → `exact,2014` + `exact,2015` 还是 **2,082,251**。
    本馆侧只是碰巧有效，于是「2020 年以后」在外部库里等于**根本没过滤**。

    区间形式两个 scope 都真的过滤（实测 `[2010 TO 2015]`：本馆 34,105 → **3,780**；
    PC 2,082,251 → **92,528**）。闭区间，**恰好一条** qInclude。
    """
    inc, _ = library_query.compile_filters({"year_from": 2022, "year_to": 2024}, "MyInstitution")
    assert inc == ["facet_creationdate,exact,[2022 TO 2024]"]


def test_compile_filters_year_range_span_is_capped():
    """`_YEAR_SPAN_CAP` 现在收窄的是**区间跨度**，不再是 qInclude 条数 ——
    区间形式永远只发 1 条，所以「别生成 127 个参数」那条理由已经消失（见常量处注释）。"""
    inc, _ = library_query.compile_filters({"year_from": 1900, "year_to": 2026}, "MyInstitution")
    years = _cd_years(inc)
    assert len(years) == library_query._YEAR_SPAN_CAP
    assert years[-1] == 2026                      # 保留靠今年的一端


def test_compile_filters_open_ended_year_to_uses_now():
    """上界缺省 ⇒ 今年。用 `datetime.now().year` 断言，**不写死年份**。"""
    now = datetime.now().year
    inc, _ = library_query.compile_filters({"year_from": now, "year_to": None}, "MyInstitution")
    assert inc == [f"facet_creationdate,exact,[{now} TO {now}]"]


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


def test_parse_natural_does_not_backfill_the_user_area(monkeypatch):
    """**LLM 的真实输出就是 `parsed.areas` 的全部**（spec §6 的观察面）。

    LLM 无视了「你必须在 areas 里包含 comp」这条提示词（提示词拦不住它，规格 :237
    自己承认），返回 `["edu"]` —— 于是回显必须是 `["edu"]`，**用户选的 `comp`
    一个字都不能被代码补回去**。补回去的话，「LLM 照做了」与「LLM 完全无视」在界面上
    显示得一模一样，规格 :237 用回显补偿「机制弱」的整个设计就归零了。

    变异：把 `parse_natural` 里那次 `kept["areas"].insert(0, area)` 加回来 ⇒ 本用例红。
    """
    monkeypatch.setattr(llm_client, "_call_chat",
                        lambda *a, **k: '{"keywords":["x"],"areas":["edu"]}')
    parsed, _ = library_query.parse_natural("u", "k", "m", "x", area="comp")
    assert parsed["areas"] == ["edu"]
    assert "comp" not in parsed["areas"]


def test_parse_natural_keeps_the_area_llm_did_obey(monkeypatch):
    """反向对照：LLM 自己把用户选的 area 放进了 areas ⇒ 原样保留、不重复、不置顶。

    删回填**不是**连正常路径一起改坏：LLM 照做时 `areas` 该长什么样还是什么样
    （这里是 `["edu","comp"]` —— 顺序即 LLM 给的顺序，代码不许重排）。
    """
    monkeypatch.setattr(llm_client, "_call_chat",
                        lambda *a, **k: '{"keywords":["x"],"areas":["edu","comp"]}')
    parsed, _ = library_query.parse_natural("u", "k", "m", "x", area="comp")
    assert parsed["areas"] == ["edu", "comp"]


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
    """year_to < year_from 时如果直接拿去做区间，`[2024 TO 2020]` 是**空区间** ——
    年份约束无声蒸发：不报错、不 0 命中，回显里却还写着 2024–2020。"""
    inc, _ = library_query.compile_filters({"year_from": 2024, "year_to": 2020},
                                           "MyInstitution")
    assert inc == ["facet_creationdate,exact,[2024 TO 2024]"]


def test_compile_filters_future_year_from_survives():
    """第二个可达入口：`_as_year` 明确放行 now+1（未来年份）。

    **当前机制**（与上面那条不同，别照抄旧注释）：`end = year_to or datetime.now().year`
    —— 上界缺省时取**今年**。只要今年比 `year_from` 小，取今年得到的 `end` 就比
    `year_from` 小，于是紧跟着的夹取 `if end < year_from: end = year_from` 把它抬到
    `year_from`（未来那一年本身），区间因此非空 ⇒ 那一年保留。
    **抬它的是夹取那一行，不是取今年那一行。**

    历史（`3be358a` 之前）：那时上界写的是 `min(now, year_from + _YEAR_SPAN_CAP)`。
    但它**并没有吞掉未来年份** —— 同一版里夹取已经在（`3be358a^:239-240`），
    `year_from=2027` 照样被抬回 2027。那道 `min` 真正伤到的是**下界很远**的情形：
    `year_from=1990` 时 end 被卡在 2020，2021–2026 静默消失 —— 这才是
    `compile_filters` 里那段注释讲的缺陷。`min` 已删，本用例守的是删掉之后仍然
    成立的性质（未来年份不被静默吞掉）。测试名里的 `_now_cap` 已随之去掉。
    """
    inc, _ = library_query.compile_filters({"year_from": 2027, "year_to": None},
                                           "MyInstitution")
    assert _cd_years(inc) == [2027]


def test_compile_filters_inverted_range_clamps_end_exactly():
    """颠倒区间夹回来的**上端**必须正好是 `year_from`，不能多算一年。

    上面那条只断言 `2024` 被覆盖 —— 而 `end = year_from` 改成 `end = year_from + 1`
    时，区间变成 `[2024 TO 2025]`，仍然含 2024，所以那条**杀不掉**这个 off-by-one。
    这里直接断言**编译出的完整年份序列**，把上端也钉死。

    为什么值得钉：夹回的上端若多一年，`2024–2020` 这种输入会**静默**多放行一整年
    （2025）的结果 —— 回显仍写着 2024–2020，用户看不出多出来的那一年是哪儿来的。
    """
    inc, _ = library_query.compile_filters({"year_from": 2024, "year_to": 2020},
                                           "MyInstitution")
    assert _cd_years(inc) == [2024]


# ---------- F2：rtype 是两侧通用维度，但**两侧词表不同** ----------

def test_compile_filters_keeps_rtype_on_local_scope():
    """既有真机实测：rtype + scope=MyInstitution 返回 CityU 馆藏 —— 所以 rtype
    **不是** PC 独有 facet（与 domain/attribute 不同）。用的值得是本馆**有的**那个。"""
    inc, _ = library_query.compile_filters({"rtype": ["databases"]}, "MyInstitution")
    assert inc == ["facet_rtype,exact,databases"]


def test_compile_filters_drops_rtype_the_scope_does_not_have():
    """**根因之三：两个 scope 的 rtype 词表不同，交集只有 3 个值。**

    旧行为把值原样发到两侧，于是本馆不支持的值换回一个 **0**
    （实测 `facet_rtype,exact,articles` 本馆 = **0**），
    PC 不支持的值换回 **1 条**（`journals` = **1**、`databases` = **0**）——
    两者在界面上都长得像「没有结果」。

    所以按 scope 求交：不支持的**丢掉**。丢掉只会让那一栏**变宽**（返回不限类型的
    命中），不会变窄 —— 用户仍看得到结果，所以不加警告码：这是两套词表的**常态**
    差异，不是异常。给常发生的正常情况报警告，会把警告训练成噪声。

    **求交之后仍只会发出一个值**（见下一条用例）—— 所以这里断言的是「活下来的
    那一个」，不是一个列表。
    """
    # articles 只有 PC 有；journals/databases 只有本馆有；books 两侧都有
    parsed = {"rtype": ["articles", "journals", "books"]}
    assert library_query.compile_filters(parsed, "CentralIndex")[0] == [
        "facet_rtype,exact,articles"]
    assert library_query.compile_filters(parsed, "MyInstitution")[0] == [
        "facet_rtype,exact,journals"]


def test_compile_filters_keeps_only_one_value_per_facet():
    """**互斥**取值在同一个 facet 里只发一个（先按 scope 词表求交，**再**取第一个）。

    PC 上 `["articles", "journals", "books"]` 求交后是 `[articles, books]`，
    取 `articles`；本馆求交后是 `[journals, books]`，取 `journals`。
    **顺序是先求交再取首个** —— 否则「第一个值本 scope 没有」会把整条过滤丢掉
    （= 变宽），而用户以为自己在按类型筛。

    ⚠️ **为什么不把两个都发出去**（2026-09-20 实测，真实 API）：
    对**互斥**的备选取值，发两个的实测结果是**0 命中** ——
    `facet_rtype,exact,articles|,|facet_rtype,exact,books` PC = **0**；
    两个 domain = **7**（MEDLINE ∩ arXiv.org，这是两个来源库的交集）。
    另有一条**异 facet** 的实测说明重复同名参数只认第一个：
    `facet_rtype,exact,books` 与 `facet_creationdate,…` 重复发 → 本馆 25,490
    （= books 单独的值，日期被丢）。

    ⚠️ **但「同一 facet 只发一个值」不是绝对律，别把本用例读成那个保证。**
    成立范围仅限**互斥**取值。反例就在本文件：
    `test_compile_filters_emits_both_tlevel_values_because_they_intersect` ——
    tlevel 的两个标志**可以同时成立**，两条都发换来的是**非空交集**
    （本馆 45 ∩ 250 = **21**、PC 896,913 ∩ 953,408 = **533,967**）。
    判据是「这几个取值互斥还是可叠加」，**不是**「是不是同一个 facet」。

    试过的 9 种「同 facet OR」写法全部无效（重复参数 / `|,|` 两种拼法 /
    `multiFacets` 三种形态 / `include` 精度 / `include,...,1` /
    `blendFacetsSeparately=true`）—— 详见 `test_library_client` 里那条
    钉 URL 的用例。**结论：OR 做不到，所以多值必须挡在编码之前。**

    「只发第一个」正是**改动之前事实上就在发生**的事（第二个值本来就被丢），
    所以这一步**不改变任何已有行为**；它只是把既成事实写成明文，并交给
    `narrowing_warnings` 把它**说出来** —— 静默窄化才是本仓的大忌。
    """
    parsed = {"rtype": ["articles", "journals", "books"]}
    assert library_query.compile_filters(parsed, "CentralIndex")[0] == [
        "facet_rtype,exact,articles"]
    assert library_query.compile_filters(parsed, "MyInstitution")[0] == [
        "facet_rtype,exact,journals"]
    # 两个 domain 同理：只留第一个（否则连接后是 AND → 7 条）
    parsed = {"domains": ["MEDLINE", "arXiv.org"]}
    assert library_query.compile_filters(parsed, "CentralIndex")[0] == [
        "domain,exact,MEDLINE"]


def test_narrowing_warnings_fire_only_when_a_value_was_actually_dropped():
    """窄化要**报警告码** —— 回显里 `parsed.rtype` 仍是两个值，
    不说出来用户就会以为筛选按两个值都做了（回显恒等于「照做了」= 补偿机制归零）。

    触发条件是「**求交后仍剩多于一个**」，不是「LLM 给了多于一个」：
    `["articles", "journals"]` 在 PC 上求交后只剩 `articles` 一个 —— 那是
    词表差异导致的**变宽**，按上一条用例的约定**故意不报警**（常态差异报警告
    会把警告训练成噪声）；只有真的丢了本可用的第二个值才报。
    """
    multi = {"rtype": ["articles", "books"]}
    assert library_query.narrowing_warnings(multi, "CentralIndex") == ["rtype_narrowed"]
    # 本馆词表里 articles 不存在 ⇒ 求交后只剩 books ⇒ 没窄化，不报
    assert library_query.narrowing_warnings(multi, "MyInstitution") == []

    # domains 只在 PC 发，所以只在 PC 报
    doms = {"domains": ["MEDLINE", "arXiv.org"]}
    assert library_query.narrowing_warnings(doms, "CentralIndex") == ["domains_narrowed"]
    assert library_query.narrowing_warnings(doms, "MyInstitution") == []

    # 单值 / 空值：无事发生
    assert library_query.narrowing_warnings({"rtype": ["books"]}, "CentralIndex") == []
    assert library_query.narrowing_warnings({}, "CentralIndex") == []


def test_rtype_vocabularies_are_the_measured_ones():
    """钉住两份**逐个测出来的**词表（2026-09-20，`any,contains,machine learning`，
    `facet_rtype,exact,<v>`）。不是从 PC 那份推的，也不是抄 spec 的。

    两处最反直觉、也是本次缺陷来源的点：
    - `articles` 在**本馆 = 0**（本馆根本没有这个类别）；
    - `journals` / `databases` 在 **PC = 1 / 0**（它们是本馆的值，不该出现在 PC 枚举里）。

    实测计数见两侧词表常量处的注释。
    """
    assert library_query.KNOWN_RTYPE_PC == frozenset({
        "articles", "books", "dissertations", "conference_proceedings",
        "book_chapters", "preprint", "reviews", "text_resources"})
    assert library_query.KNOWN_RTYPE_LOCAL == frozenset({
        "books", "dissertations", "conference_proceedings",
        "videos", "journals", "audios", "other", "databases"})
    assert library_query.KNOWN_RTYPE_PC & library_query.KNOWN_RTYPE_LOCAL == {
        "books", "dissertations", "conference_proceedings"}
    assert "articles" not in library_query.KNOWN_RTYPE_LOCAL
    assert "journals" not in library_query.KNOWN_RTYPE_PC
    assert "databases" not in library_query.KNOWN_RTYPE_PC


# ---------- F3：area 的全部约束力都押在提示词那句一致性约束上（spec §4.1） ----------

def test_build_prompt_states_area_consistency_constraint():
    """spec §4.1：area 不编译成任何参数（`areas` 在 §6 无编译条），所以提示词里
    「你选的 terms / rtype / domains 必须与该领域一致」这句是**唯一的约束力**。
    删掉整段 area_note 也曾全绿 —— 这条把话钉住。"""
    p = library_query.build_prompt("q", "comp", {"comp": "Computer Science"}, [])
    assert "Computer Science" in p
    assert "必须与该领域一致" in p
    # 原先这里是 `for field in ("terms","rtype","domains"): assert field in p` —— **恒真**：
    # 这三个词在提示词**下方的输出字段清单里本来就出现**（`- rtype: ...` / `- domains: ...`），
    # 于是变异「把那句一致性要求缩成只提 terms」**存活**。逐个 `in` 测不出「同一句」。
    # 判据换成**整句**：三个词必须在**同一句**里同时出现（短语逐字取自 spec :109 的规格原文）。
    assert "你选的 terms / rtype / domains 必须与该领域一致" in p


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
    改成保留远端（1900–1929）时旧写法曾经全绿。

    断言**完整年份序列**（含两端）：`1900 in years` 与 `len == 30` 两条各自都能被
    不同的错法绕过，合起来才把窗口两端同时钉死。
    """
    inc, _ = library_query.compile_filters({"year_from": 1900, "year_to": 2026},
                                           "MyInstitution")
    years = _cd_years(inc)
    assert years == list(range(1997, 2027))
    assert years[0] == 1997 and years[-1] == 2026


def test_year_span_cap_narrows_the_filter_but_not_parsed():
    """F-7：30 年是**实现自定**的界（规格 §6 只写「逐年一条」，没有任何上限），
    截断只发生在**编译出的过滤集**上 —— `parsed.year_from` 照旧是用户原值。
    钉住「选定的行为」：保留靠今年的一端、只收窄过滤集、`_YEAR_SPAN_CAP` 不动。

    ⚠️ **这个上限如今只剩下害处**：它原本的理由是「别生成上百个 qInclude 参数」
    （按 `_MIN_YEAR`=1800 到今年算是 **227** 条，「1900 年以来」也有 **127** 条），
    而区间形式**永远只发 1 条**，那条理由已被本次修复消除。实测 `[1900 TO 2026]`
    两个 scope 都正常返回（本馆 34,018 / PC 2,078,579），所以收窄已无必要。
    本次**不动它**（改窗口语义属于另一件事）。

    **收窄本身已不再静默**：`narrowing_warnings` 会报 `year_narrowed`
    （见 `test_narrowing_warnings_reports_a_clamped_year_window`）——
    本用例钉的是「编译出的区间怎么算」，不涉及那个警告。改掉这个上限时两条一起改。
    """
    parsed = {"year_from": 1900, "year_to": 2026}
    inc, _ = library_query.compile_filters(parsed, "MyInstitution")
    assert inc == ["facet_creationdate,exact,[1997 TO 2026]"]              # 恰好 30 年
    assert parsed["year_from"] == 1900      # 用户输入不被改写（回显仍是原值）


# ---------- 修复轮：缺省 year_to 的上界必须是**今年**（真缺陷） ----------

def _cd_years(inc: list[str]) -> list[int]:
    """从 qInclude 列表里取出 creationdate 过滤覆盖的年份，展开成逐年列表（升序）。

    期望形式是**唯一一条闭区间** `facet_creationdate,exact,[<start> TO <end>]`。
    找不到区间返回 `[]`；**多于一条也返回 `[]`**（调用方断言的是完整年份列表，
    多条时长度必然对不上 ⇒ 弄红；这里不静默取第一条）。
    """
    ranges = [e for e in inc if e.startswith("facet_creationdate,exact,[")]
    if len(ranges) != 1:
        return []
    body = ranges[0][len("facet_creationdate,exact,["):-1]
    start, _, end = body.partition(" TO ")
    return list(range(int(start), int(end) + 1))


def test_compile_filters_open_ended_year_from_keeps_the_recent_end():
    """缺省 `year_to`（用户说「X **以后**的论文」，LLM 就该给 null）⇒ 上界 = **今年**。

    真缺陷：改前是 `end = year_to or min(now, year_from + _YEAR_SPAN_CAP)`，
    上界被卡在「起始年 + 30」，下面的 `start = max(year_from, end - CAP + 1)`
    又把下界抬一年 —— 两端同时错。`year_from=1990, year_to=None` 只过滤 1991–2020，
    用户要「1990 年以后的」，拿到的最新结果是 **2020**，最近几年静默消失。
    期望值用 `datetime.now().year` 算，**不写死年份**（否则明年这条就红）。
    """
    now = datetime.now().year
    inc, _ = library_query.compile_filters({"year_from": 1990, "year_to": None},
                                           "MyInstitution")
    cap = library_query._YEAR_SPAN_CAP
    assert _cd_years(inc) == list(range(now - cap + 1, now + 1))    # 恰好收到今年为止


def test_compile_filters_open_ended_year_from_within_cap_is_verbatim():
    """起始年在 CAP 之内时，缺省上界同样是今年，且起始年一年都不丢。"""
    now = datetime.now().year
    inc, _ = library_query.compile_filters({"year_from": now - 2, "year_to": None},
                                           "MyInstitution")
    assert _cd_years(inc) == list(range(now - 2, now + 1))


def test_compile_filters_explicit_year_to_is_unchanged_by_the_fix():
    """显式给了 `year_to` 的路径一字不变：窗口仍是 `[end-CAP+1, end]`。

    区间两头都显式 ⇒ 断言不依赖「今年是哪年」，永远稳定 —— 这条是防止
    修缺省分支时把显式分支一起改掉的护栏。
    """
    inc, _ = library_query.compile_filters({"year_from": 1990, "year_to": 2020},
                                           "MyInstitution")
    assert _cd_years(inc) == list(range(1991, 2021))


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

    当年端点会就地 `insert` 回填用户选的 area，于是共享的那个 list 被永久改写、
    模块常量跨请求泄漏（那两处回填本轮已删）。构造函数的这条不变量仍然要钉住 ——
    它是「返回值可改」的地基，删掉回填只是拿掉了**当时**的触发者，不是拿掉了风险。
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

    端点上那条 `parsed["areas"].insert(0, req.area)`（本轮已删）当年就是靠这条兜住的；
    这里继续用等价的就地改写当探针，守住「构造函数返回独立 list」本身。
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


def test_compile_filters_emits_both_tlevel_values_because_they_intersect():
    """**同一 facet 的两个值并非一律只发一个** —— 判据是「互斥还是可叠加」。

    `rtype` / `domain` 的取值**互斥**，交集恒为空，所以只发第一个（见上一条）；
    `tlevel` 的两个标记可以**同时成立**，两条一起发换来的是**交集**，
    而那正是用户勾两个框时想要的语义（「同行评审**且**开放获取」）。

    2026-09-20 实测（q = `any,contains,machine learning`，`|,|` 连接成一个 qInclude）：
      · 本馆：peer_reviewed **45**、open_access **250** → 两个都发 **21**（= 交集，非空）
      · PC  ：896,913 / 953,408               → 两个都发 **533,967**（= 交集，非空）

    两条都**严格小于**各自单独的值 ⇒ tlevel 是逐条可多值的，交集真的有内容，
    不是被 AND 成空集。若哪天有人为「统一」把它改成只发第一条，
    用户勾的两个框里会有一个静默失效，这条用例会红。
    """
    both = {"peer_reviewed_only": True, "open_access_only": True}
    for scope in ("MyInstitution", "CentralIndex"):
        inc, _ = library_query.compile_filters(both, scope)
        assert [e for e in inc if e.startswith("facet_tlevel")] == [
            "facet_tlevel,exact,peer_reviewed",
            "facet_tlevel,exact,open_access",
        ], scope


def test_narrowing_warnings_reports_a_type_filter_that_vanished():
    """**整条类型约束消失**与「窄化」是两回事，必须有自己的码。

    `journals` 在本馆词表里、`articles` 在 PC 词表里，但**都不在对方的词表里** ——
    于是各自都有一栏**根本没按类型筛**，返回的是**全部类型**，而回显里
    `parsed.rtype` 照旧写着用户要的那个类型。不说出来，用户会以为那一栏筛过了。

    与「求交后还剩至少一个值」的对比：那种情况那一栏**仍被类型筛着**，只是比用户
    要的窄（两套词表的常态差异），**不报** —— 报了会把警告训练成噪声。
    """
    assert "rtype_dropped" in library_query.narrowing_warnings(
        {"rtype": ["journals"]}, "CentralIndex"), "journals 不在 PC 词表"
    assert "rtype_dropped" in library_query.narrowing_warnings(
        {"rtype": ["articles"]}, "MyInstitution"), "articles 不在本馆词表"
    # 还剩可用类型 ⇒ 仍被类型筛着 ⇒ 不报 dropped
    assert "rtype_dropped" not in library_query.narrowing_warnings(
        {"rtype": ["articles", "books"]}, "MyInstitution")
    # 交集非空的正常情形也不该冒出这个码
    assert "rtype_dropped" not in library_query.narrowing_warnings(
        {"rtype": ["books"]}, "CentralIndex")
    # 根本没提类型 ⇒ 谈不上「消失」
    assert "rtype_dropped" not in library_query.narrowing_warnings({}, "CentralIndex")


def test_narrowing_warnings_reports_a_clamped_year_window():
    """`_YEAR_SPAN_CAP` 的收窄以前是**静默**的：回显 `year_from=1900`，
    实际只筛 1997–2026。现在由 `year_narrowed` 说出来。

    触发条件是**真的被抬过**，不是「有年份」—— 窗口落在 CAP 内时一字未改，不报。
    """
    assert "year_narrowed" in library_query.narrowing_warnings(
        {"year_from": 1900, "year_to": 2026}, "MyInstitution")
    assert "year_narrowed" not in library_query.narrowing_warnings(
        {"year_from": 2020, "year_to": 2026}, "MyInstitution"), "30 年内，没被抬过"
    assert "year_narrowed" not in library_query.narrowing_warnings({}, "MyInstitution")


# ---------- 警告码的跨语言契约 ----------

I18N_JS = Path(__file__).resolve().parents[1] / "frontend" / "i18n.js"


def _i18n_language_blocks() -> dict[str, dict[str, str]]:
    """从 `frontend/i18n.js` 取出两种语言各自的**键 → 值**映射。

    用正则切块而不是求值 JS：那个文件的两种语言是两个**形状固定**的字面量对象
    （`  zh: {` / `\n  en: {` 起、`\n};` 收）。切不出块或切出的键太少就直接 raise
    —— 守卫**绝不能在文件改版后静默退化成空集**、从此永远通过。

    取值时**必须认转义**：文件里有 `"… click \\"Load courses\\"."` 这种值（8 处），
    用 `"([^"]*)"` 会在转义引号处截断。截断虽不会把空值变成非空，但会让
    「值是什么」这件事从守卫的视野里消失 —— 而这里正是要查值的。
    """
    src = I18N_JS.read_text()
    if "  zh: {" not in src or "\n  en: {" not in src:
        raise AssertionError("frontend/i18n.js 的语言块形状变了，切块逻辑要跟着更新")
    zh_block = src.split("  zh: {", 1)[1].split("\n  en: {", 1)[0]
    en_block = src.split("\n  en: {", 1)[1].split("\n};", 1)[0]
    blocks = {}
    for lang, block in (("zh", zh_block), ("en", en_block)):
        pairs = re.findall(r'"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', block)
        if len(pairs) < 100:
            raise AssertionError(f"{lang} 只切出 {len(pairs)} 个键值对，切块逻辑错了")
        blocks[lang] = dict(pairs)
    return blocks


def test_every_warning_code_has_a_translation_in_both_languages():
    """警告码是**跨语言契约**：后端发一个 i18n.js 里没有的码，`t()` 的兜底链
    （`?? I18N.zh[key] ?? key`）会把**键名本身**当文案显示给用户；
    只有中文有、英文没有时，英文界面会冒中文。两种语言都必须有，且非空。

    **「非空」不是凑数的话**（2026-09-20 复审 NEW-1）：`t()` 返回空串时
    `app.js` 的 `renderLibraryWarnings` 仍会把 `#libraryWarnings` 置为可见，
    于是警告变成一行空白 —— 用户界面上什么也没有。若那是本次唯一的警告，
    症状与「后端根本没发码」**完全一样**，而这正是本仓列为头号大罪的静默错误。
    所以空串必须红，否则这条守卫拦不住它唯一该拦的东西。
    """
    blocks = _i18n_language_blocks()
    bad = []
    for code in library_query.WARNING_CODES:
        key = f"library.warn.{code}"
        for lang in ("zh", "en"):
            if key not in blocks[lang]:
                bad.append(f"{lang}:{key} 缺键")
            elif not blocks[lang][key].strip():
                bad.append(f"{lang}:{key} 译文是空白")
    assert not bad, f"这些警告码的译文不可用：{bad}"


def test_i18n_has_the_same_keys_in_both_languages():
    """两种语言的键集合必须**逐个相同**：少一个键就回退到中文，
    于是英文界面上出现中文 —— 而这种缺陷**没有任何运行时报错**。
    """
    blocks = _i18n_language_blocks()
    zh_keys, en_keys = set(blocks["zh"]), set(blocks["en"])
    assert zh_keys == en_keys, {
        "only_zh": sorted(zh_keys - en_keys),
        "only_en": sorted(en_keys - zh_keys),
    }


def test_declared_warning_codes_match_every_code_the_module_emits():
    """`WARNING_CODES` 是本模块发码的**唯一清单**，不能与实际发码处脱节。

    扫源码（不求值）—— 本模块发码只有三种写法，源码里都是字面量：
    `warnings.append("x")`、`out.append("x")`、返回元组里的 `["x"]`
    （`return fallback_parsed(text), ["llm_degraded"]`）。

    断言的是**相等**：多登记一个从不发出的码、或发出一个没登记的码，两者都会红。
    扫不到任何码时也红 —— 防止正则失效后守卫**静默变成空转**。
    """
    src = (Path(__file__).resolve().parents[1]
           / "backend" / "library_query.py").read_text()
    emitted = set(re.findall(r'(?:warnings|out)\.append\("([a-z_]+)"\)', src))
    emitted |= set(re.findall(r'(?:,\s*|\+\s*)\["([a-z_]+)"\]', src))
    assert emitted, "一个发码处都没扫到 —— 正则失效了，别让它静默通过"
    assert emitted == set(library_query.WARNING_CODES), {
        "declared_only": sorted(set(library_query.WARNING_CODES) - emitted),
        "emitted_only": sorted(emitted - set(library_query.WARNING_CODES)),
    }
