"""自然语言 → 结构化检索式。

产物是**结构化 JSON，不是 Primo 查询串**（spec §6）：可校验、可原样回显给
用户、单字段出错能降级而不是整个查询崩掉。
"""
from __future__ import annotations

import copy
from datetime import datetime

from . import llm_client

# 实测：rtype 的值是**复数**。单数形式返回 0 命中。
KNOWN_RTYPE = ["articles", "books", "journals", "databases", "dissertations",
               "conference_proceedings", "book_chapters", "preprint",
               "reviews", "text_resources"]

_MAX_ITEMS = 12        # 每个列表最多几条
_MAX_STR = 200         # 单个字符串最长多少字符
_MIN_YEAR = 1800

# 年份区间最多生成多少条 `creationdate` 过滤（**每条一个重复的 qInclude 参数**，
# 区间语法未验证，spec §6 明确不作默认）。**规格没有规定这个上限** —— 规格 §6 只说
# 「逐年一条」，30 是实现自己定的界，理由是：不设界的话「1900 年以前至今」会生成
# 227 个 qInclude 参数，既撑大请求也可能被上游拒。**真的被截断时用户看不到**：
# `parsed.year_from` 照旧回显 1900，被悄悄收窄的是实际编译出的过滤集
# （1900–2026 → 只过滤 1997–2026）。已知的静默收窄，故意保留在 30，
# 不改数值也不加提示（要改就得先有依据）。详见
# tests/test_library_query.py 里钉住这条行为的两个用例。
_YEAR_SPAN_CAP = 30

EMPTY_PARSED = {
    "keywords": [], "synonyms": [], "year_from": None, "year_to": None,
    "rtype": [], "language": None, "open_access_only": False,
    "peer_reviewed_only": False, "exclude_retracted": False,
    "areas": [], "domains": [], "explanation": "",
}


def _blank_parsed() -> dict:
    """EMPTY_PARSED 的**独立副本**——list 字段必须是新对象。

    dict(EMPTY_PARSED) 是浅拷贝，`parsed["areas"]` 会与模块常量**共用同一个
    list**；而端点会就地 insert 回填 area（spec §6 要求领域提示不被丢弃），
    于是常量被永久污染、跨请求泄漏。deepcopy 而非手写空 list：日后给
    EMPTY_PARSED 加 list 字段时手写版会静默失效。
    """
    return copy.deepcopy(EMPTY_PARSED)


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
        return _blank_parsed()

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
    parsed = _blank_parsed()
    parsed["keywords"] = _as_list(text.strip())
    return parsed


def build_prompt(text: str, area: str | None, area_names: dict[str, str],
                 domains: list[str]) -> str:
    """拼给 LLM 的提示词。词表为空也要能拼出来（只是没得挑）。"""
    area_lines = "\n".join(f"- {code}: {name}" for code, name in (area_names or {}).items())
    if area:
        chosen = area_names.get(area, area) if area_names else area
        area_note = (f"用户已限定学科领域为 **{chosen}**（code: {area}）。"
                     f"你必须在 areas 里包含 \"{area}\"，可以补充其它相关领域，但**不得删掉它**。"
                     f"你选的 terms / rtype / domains 必须与该领域一致。")
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
    """关键词 + 同义词 → Primo 的 q 参数。空的话给个合法兜底，上游才不 400。

    **去重（保序）**：`parse_natural` 在 LLM 只给 synonyms 时会拿它顶上 keywords，
    却不清空来源字段（那份 parsed 还要原样回显给用户，不该被改），于是同一批词会
    同时出现在两个字段里 —— 不去重的话 q 里每个词都写两遍
    （`any,contains,ML DL ML DL`，实测复现）。LLM 自己把同一个词写进两个字段时同理。

    在 build_q 里做而不是在提升处清空 synonyms：路由回传的 `parsed` 要原样重编译，
    两边走同一个函数才能保证第 2 页的 q 与第 1 页**逐字节相同**（spec §8）。
    """
    seen: set[str] = set()
    uniq: list[str] = []
    for term in list(parsed.get("keywords") or []) + list(parsed.get("synonyms") or []):
        if term not in seen:
            seen.add(term)
            uniq.append(term)
    return "any,contains," + " ".join(uniq)


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
        # 颠倒区间（year_to < year_from）必须夹回来：否则 range 为空、**年份约束
        # 无声蒸发** —— 不报错也不 0 命中，而回显里还写着 2024–2020，看不出没生效。
        # 同一路径也覆盖 year_from = now+1（_as_year 明确放行）而 end 被 now 卡住的情形。
        if end < year_from:
            end = year_from
        # +1 是为了让**闭区间**长度不超过 _YEAR_SPAN_CAP（end - start + 1 ≤ CAP）。
        # 写成 end - CAP 会得到 CAP+1 条，正好越过上限一条。
        # 区间比 CAP 宽时**保留靠今年的一端**（1900–2026 → 1997–2026）：用户写
        # 「1900 年以来」要的是「老到今」，丢掉近端等于把最相关的那几年排除掉。
        # 这一步只收窄**编译出的过滤集**，不动 `parsed.year_from` —— 回显仍是用户
        # 输入的原值，所以收窄是静默的（见 _YEAR_SPAN_CAP 处的说明）。
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
