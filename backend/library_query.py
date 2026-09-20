"""自然语言 → 结构化检索式。

产物是**结构化 JSON，不是 Primo 查询串**（spec §6）：可校验、可原样回显给
用户、单字段出错能降级而不是整个查询崩掉。
"""
from __future__ import annotations

import copy
from datetime import datetime

from . import llm_client

# 实测：rtype 的值是**复数**。单数形式返回 0 命中。
#
# `KNOWN_RTYPE` 是**给 LLM 的枚举**，也是 `validate_parsed` 的白名单。
# 它**不等于**任何一侧的词表 —— 它是两份实测词表的并集里「用户可能主动要」的那部分。
KNOWN_RTYPE = ["articles", "books", "journals", "databases", "dissertations",
               "conference_proceedings", "book_chapters", "preprint",
               "reviews", "text_resources"]

# 2026-09-20 实测。q = `any,contains,machine learning`，`facet_rtype,exact,<v>`，
# offset=0/limit=1 取 `info.total`。**两份词表是逐个值测出来的，不是抄一份求交。**
#
#   CentralIndex：articles 1,280,371 · books 28,672 · dissertations 24,801 ·
#                 conference_proceedings 219,737 · book_chapters 158,429 ·
#                 preprint 314,707 · reviews 1,616 · text_resources 269
#                 —— journals **1**、databases **0**（这两个是**本馆**的值）
#   MyInstitution：books 25,490 · dissertations 6,754 · conference_proceedings 1,212 ·
#                 videos 540 · journals 176 · audios 7 · other 3 · databases 2
#                 —— articles **0**（本馆没有这个类别）
#
# 把某 scope 不支持的值发过去，换回的是一个 **0** 或 **1** —— 在界面上都长得像
# 「没有结果」。这是用户报的那个症状的**第三条成因**（前两条：facet 缺 `facet_`
# 前缀；逐年 `creationdate` 在 PC 上完全空转）。
#
# 交集只有 **3** 个值：books / dissertations / conference_proceedings。
#
# 注意 `videos` / `audios` / `other` 只在本地词表里 —— 它们**目前 LLM 提不出来**
# （不在 `KNOWN_RTYPE` 里，`validate_parsed` 会剔掉）。仍然收进本地词表：这是测出来
# 的完整词表，缺了它们会给「日后把 videos 加进 LLM 枚举」的人埋一个 0 命中的坑。
KNOWN_RTYPE_PC = frozenset({
    "articles", "books", "dissertations", "conference_proceedings",
    "book_chapters", "preprint", "reviews", "text_resources",
})
KNOWN_RTYPE_LOCAL = frozenset({
    "books", "dissertations", "conference_proceedings",
    "videos", "journals", "audios", "other", "databases",
})

_MAX_ITEMS = 12        # 每个列表最多几条
_MAX_STR = 200         # 单个字符串最长多少字符
_MIN_YEAR = 1800

# 年份区间的**最大跨度**（年数）。它现在收窄的是区间两端之间的距离，不再是
# qInclude 参数条数 —— 2026-09-20 起年份发的是**一条区间表达式**（见 compile_filters）。
#
# ⚠️ **这个上限只剩下害处，是本次登记的已知收窄 —— 但它不再静默。**
# 它原本的理由是「不设界的话年份区间会展开成上百个 qInclude 参数，既撑大请求
# 也可能被上游拒」：按 `_MIN_YEAR`=1800 到 2026 算是 **227** 条，即使用户写
# 「1900 年以来」也有 **127** 条（两个数字指的是**不同起点**，不是互相矛盾）。
# 区间形式**永远只发 1 条**，那条理由已被编码方式本身消除。事后实测
# `facet_creationdate,exact,[1900 TO 2026]` 两个 scope 都正常返回
# （本馆 34,018 / PC 2,078,579），所以「区间语法未验证」也不再成立。
# 换言之：现在它**只会**把用户要的年份砍掉**老的那一端**（1900–2026 → 只过滤
# 1997–2026）。`parsed.year_from` 照旧回显 1900（用户输入不被改写），
# 但这次收窄**不再静默** —— `narrowing_warnings` 会报 `year_narrowed`。
#
# **本次不删**：改窗口语义是独立的一件事，混进「修无结果」这轮会让 diff 说不清。
# 留给下一次改动处理（`end` 直接取用户值即可，届时不报 `year_narrowed`）；
# 改的时候 tests/test_library_query.py 里那几条一起改。
#
# 收窄**只允许发生在老的那一端**（下界）：上界缺省时取今年，近端永远保到今年
# （见 `_year_window`，区间与收窄判定的唯一实现处）。曾经上界也被
# `year_from + CAP` 卡住，于是「1990 年以后」只查得到 1991–2020 ——
# 那不是收窄，是把最相关的几年丢掉。
_YEAR_SPAN_CAP = 30

# 本模块能发出的**全部**警告码 —— 这是一份**跨语言契约**：前端按
# `library.warn.<code>` 查译文（`frontend/i18n.js`），后端发一个前端没有译文的码，
# 界面上就会**直接显示出这个 key 本身**（`t()` 的兜底链是 `?? I18N.zh[key] ?? key`），
# 英文界面还会因此冒中文。tests/test_library_query.py 里有守卫按这个集合核对
# i18n.js 的**两种语言**，另有一条扫描本模块的发码处，防止「发了码却没登记」。
WARNING_CODES = (
    "llm_degraded",      # parse_natural：LLM 挂了，已降级成原文关键词
    "domains_dropped",   # parse_natural：LLM 编了词表里没有的来源库名
    "rtype_narrowed",    # narrowing_warnings：多个**互斥**类型，只用了第一个
    "rtype_dropped",     # narrowing_warnings：用户要的类型本 scope 一个都没有
    "domains_narrowed",  # narrowing_warnings：多个来源库，只用了第一个
    "year_narrowed",     # narrowing_warnings：年份窗口被 _YEAR_SPAN_CAP 抬掉了老端
)

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

    # **这里故意不把用户选的 `area` 回填进 `kept["areas"]`**（spec §6）。
    #
    # area 在 §4.1 修正后不编译成任何参数，约束**只落在提示词上**（见 build_prompt
    # 的「你必须在 areas 里包含 …」）。规格自己写明「提示词是可以不被遵守的，代码
    # 拦不住」，并把补偿定在「`parsed.areas` 随 `parsed` 一起回显，用户能立刻看见
    # LLM 有没有照做」。回填恰好会毁掉这个补偿：它保证用户选的 area **永远**出现在
    # 回显里，于是「LLM 照做了」与「LLM 完全无视」在界面上显示得一模一样。
    # `parsed["areas"]` 在检索链路上没有任何消费者（`build_q` / `compile_filters`
    # 都不读它），所以回填对结果零影响 —— 它改的正是那个用来「看见真相」的显示。
    #
    # 因此：**保持 LLM 的真实输出**。要不要补充别的领域由 LLM 决定，不由代码代劳；
    # LLM 降级时 areas 为空也是正确的（那本来就没有 LLM 答案），降级本身另有
    # `llm_degraded` 警告码可见。
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


def _surviving_rtypes(parsed: dict, scope: str) -> list[str]:
    """`parsed["rtype"]` 里**本 scope 词表真的支持**的那些值，保持原顺序。

    求交规则只有这一处实现：`compile_filters`（发哪一个）与 `narrowing_warnings`
    （要不要为此报警告）都从这里取，免得两边各写一份、日后只改一处。
    """
    known = KNOWN_RTYPE_PC if scope == "CentralIndex" else KNOWN_RTYPE_LOCAL
    return [r for r in parsed.get("rtype") or [] if r in known]


def _year_window(parsed: dict) -> tuple[int, int] | None:
    """按 `_YEAR_SPAN_CAP` 编译出的闭区间 `(start, end)`；没有年份约束时 `None`。

    **区间跨度的收窄只有这一处实现**：`compile_filters`（发什么区间）与
    `narrowing_warnings`（要不要报 `year_narrowed`）都从这里取 —— 两边各写一份的话，
    日后只改一处就会出现「报了没收窄」或「收窄了没报」，而后者正是本次在修的那类
    静默。
    """
    year_from, year_to = parsed.get("year_from"), parsed.get("year_to")
    if not year_from:
        return None
    # 上界缺省（用户说「X **以后**的论文」，LLM 就该给 year_to=null）= **今年**。
    # 曾经写成 `min(now, year_from + _YEAR_SPAN_CAP)`，那样两处同时错：
    #   year_from=1990, year_to=None → end 被卡在 2020，接着 start 又被抬到 1991，
    #   实际只过滤 1991–2020 —— 用户要「1990 年以后的」，拿到的最新结果是 **2020**，
    #   最近几年（最相关的那批）**静默消失**，而回显仍写着 1990，用户无从察觉。
    # 上界取今年才与下面「区间比 CAP 宽时保留靠今年的一端」自洽：收窄只发生在
    # 下界（老的那一端），近端永远保到今年。显式给了 year_to 的路径一字不变。
    end = year_to or datetime.now().year
    # 颠倒区间（year_to < year_from）必须夹回来：否则区间为空、**年份约束无声蒸发**
    # —— 不报错也不 0 命中，而回显里还写着 2024–2020，看不出没生效。
    # 同一路径也覆盖 year_from = now+1（_as_year 明确放行）而 end 被 now 卡住的情形。
    if end < year_from:
        end = year_from
    # 窗口长度是**闭区间**（end - start + 1 ≤ CAP），所以是 end - CAP + 1；
    # 写成 end - CAP 会多算一年，正好越过上限一条。
    # 区间比 CAP 宽时**保留靠今年的一端**（1900–2026 → 1997–2026）：用户写
    # 「1900 年以来」要的是「老到今」，丢掉近端等于把最相关的那几年排除掉。
    start = max(year_from, end - _YEAR_SPAN_CAP + 1)
    return start, end


def _year_window_is_narrowed(parsed: dict) -> bool:
    """用户给的 `year_from` 是否被 `_YEAR_SPAN_CAP` 抬高过（即真的发生了收窄）。"""
    window = _year_window(parsed)
    return bool(window) and window[0] != parsed.get("year_from")


def narrowing_warnings(parsed: dict, scope: str) -> list[str]:
    """这一栏**实际发出去的过滤**比用户的检索式窄时，返回要报的**警告码**。

    警告是**码**不是句子 —— 前端按 `library.warn.<code>` 翻译（照 `parse_natural`
    的既有约定）。目前四个码：

      · `rtype_narrowed`    —— 求交后仍多于一个**互斥**的文献类型，只用了第一个
      · `rtype_dropped`     —— 用户要的文献类型本 scope **一个都没有**，整条类型约束
                               消失（这一栏根本没按类型筛，回显却还写着它）
      · `domains_narrowed`  —— 多于一个来源库，只用了第一个（只在 PC 报，本馆不发
                               domain 过滤）
      · `year_narrowed`     —— `_YEAR_SPAN_CAP` 把年份窗口从**老的那一端**抬掉了
                               （`parsed.year_from` 照旧回显用户原值）

    **`rtype_narrowed` 的触发条件是「求交后仍剩多于一个」，不是「LLM 给了多于一个」。**
    值被词表挡掉属于**变宽**（用户仍拿得到结果，且是两套词表的常态差异），只要还剩
    至少一个可用值就**故意不报**（见 `compile_filters` 里的约定）；只有真的丢了
    **本可用**的第二个值才报 —— 那时回显里 `parsed.rtype` 仍是两个值，不说出来用户
    就会以为两个都筛了。

    **但「一个都不剩」是另一回事，必须报**（`rtype_dropped`）：那时这一栏完全没在按
    类型筛，返回的是**全部类型**，而回显照旧写着用户要的类型 —— 那是回显与实际查询
    不一致，不是「变宽」两个字能解释掉的。

    这个函数与 `compile_filters` 必须**同口径**：两个 scope 各调一次，所以同一次
    查询可能两个 scope 都报同一个码（前端按码去重即可，端点也做了去重）。
    """
    out: list[str] = []
    surviving = _surviving_rtypes(parsed, scope)
    if len(surviving) > 1:
        out.append("rtype_narrowed")
    if parsed.get("rtype") and not surviving:
        out.append("rtype_dropped")
    if scope == "CentralIndex" and len(parsed.get("domains") or []) > 1:
        out.append("domains_narrowed")
    if _year_window_is_narrowed(parsed):
        out.append("year_narrowed")
    return out


def compile_filters(parsed: dict, scope: str) -> tuple[list[str], list[str]]:
    """把检索式编译成 `(qInclude, qExclude)` 两份过滤表达式。

    **三条实测约束决定了整个形状** —— 改动前先读这三处：

    1. **每个 facet 都要带 `facet_` 前缀**（`facet_<name>,<precision>,<value>`）。
       不带前缀时 CentralIndex **一律 0 命中**，MyInstitution 侧有的被**静默忽略**
       （`tlevel,exact,peer_reviewed` 返回 34,105 = 完全没过滤的基线，看着没报错，
       其实那个勾没生效）。两种表现都是「没有结果」。
       上游权威来源是 Primo UI 自己的 `facetToString`（`discovery/lib/bundle.js`）。
       **唯一例外是 `domain`**——两种写法结果不同，见下方注释。
    2. **两个 scope 的 rtype 词表不同**，交集只有 3 个值（见 `KNOWN_RTYPE_LOCAL`
       / `KNOWN_RTYPE_PC` 处的实测计数）。不支持的值**丢掉**，不能发出去换一个 0/1。
    3. **年份必须发区间表达式**。逐年 `creationdate,exact,<year>` 在 CentralIndex 上
       **完全空转**（见下方注释）。

    **按 scope 分流**（spec §6）：`domain`（来源数据库）与 `attribute`（撤稿标记）
    只在 `CentralIndex`。**`tlevel` 是例外 —— spec §6 写它「PC 独有」，实测为假**，
    两个 scope 都发（见下方注释）。
    """
    include: list[str] = []
    exclude: list[str] = []
    is_pc = scope == "CentralIndex"

    # **只取求交后的第一个值。**
    # 求交：本 scope 没有的类别丢掉（发出去只会换回 0 条 —— `articles` 本馆实测 0；
    # 或 1 条 —— `journals`/`databases` PC 实测 1 / 0；界面上都长得像「没有结果」）。
    # 取第一个：`|,|` 连接后同一 facet 之间是 **AND**，两个**互斥**的 rtype 实测换回
    # **0** 条（`articles|,|books` PC = 0）—— 那正是本次在修的症状；而本端点**没有**
    # 「同 facet OR」的表达方式（9 种写法实测全是 AND 或空转，见 `build_params` 的说明）。
    # 这是**改动之前事实上就在发生**的事（重复参数写法下 Primo 本来也只认第一个），
    # 不改变已有行为；被丢掉的第二个值由 `narrowing_warnings` 报出来，不静默。
    #
    # ⚠️ **「同一 facet 只发一个值」不是绝对律，别把本函数读成那个保证。**
    # 它成立的范围仅限**互斥的备选值**：rtype 的取值、domain 的来源库 —— 这些取两个
    # 的交集恒为空，没有任何用户会想要。
    # 反例是下面的 `tlevel`：`peer_reviewed` 与 `open_access` 可以**同时成立**，两条
    # 都发换来的是**交集**，而交集恰恰是用户勾两个框时想要的语义 —— 实测本馆
    # 45 ∩ 250 = **21**、PC 896,913 ∩ 953,408 = **533,967**，都不是 0
    # （2026-09-20 实测，q = `any,contains,machine learning`）。
    # 所以判据是「这几个值互斥还是可叠加」，**不是**「是不是同一个 facet」。
    #
    # 词表挡掉值只在**整条类型约束都消失**时才报 `rtype_dropped`：还剩至少一个值 ⇒
    # 这一栏仍被类型筛着，只是比用户要的窄（属两套词表的常态差异，为它报警会把警告
    # 训练成噪声）；**一个都不剩 ⇒ 这一栏根本没在按类型筛**，而回显里 `parsed.rtype`
    # 照旧写着用户要的类型 —— 那是「回显与实际查询不一致」，必须说出来。
    surviving_rtypes = _surviving_rtypes(parsed, scope)
    if surviving_rtypes:
        include.append(f"facet_rtype,exact,{surviving_rtypes[0]}")

    year_window = _year_window(parsed)
    if year_window:
        start, end = year_window
        # ⚠️ **发区间，不是逐年一条。** 旧的逐年写法
        # `facet_creationdate,exact,<year>` 在 CentralIndex 上是**完全空转**的：
        # 实测无年份过滤 2,082,251 → `exact,2014` 仍 2,082,251 → `exact,2014` +
        # `exact,2015` 还是 2,082,251。本馆侧只是碰巧有效 —— 于是「2020 年以后」
        # 在外部库里等于**根本没过滤**，用户看不出来。
        # 闭区间形式两个 scope 都真的过滤（实测 `[2010 TO 2015]`：本馆 34,105 →
        # 3,780；PC 2,082,251 → 92,528）。顺带把最多 30 个 qInclude 参数压成 **1** 个。
        #
        # 窗口本身（上界为什么取今年、区间为什么夹回来、跨度怎么卡在 CAP）**全在
        # `_year_window` 里**，与本函数同源 —— 别在这里重算一遍。
        # 它只收窄**编译出的过滤集**，不动 `parsed.year_from`：回显仍是用户输入的原值；
        # 那处收窄如今由 `year_narrowed` 警告码说出来，不再静默。
        include.append(f"facet_creationdate,exact,[{start} TO {end}]")

    if is_pc:
        # `domain` 是**唯一不带 `facet_` 前缀的 facet**，故意如此：
        # 两种写法行为**不同**（实测 arXiv.org：`domain,exact,` **38** 条 vs
        # `facet_domain,exact,` **142** 条），而 spec §3.3 验证过的是**不带**前缀那条。
        # 差异机制未查明 ⇒ 按已验证的那条走。查明之前不要为了「统一」把它改带前缀。
        #
        # **同样只取第一个**：两个 domain 连接后是 AND —— 实测
        # `domain,exact,MEDLINE|,|domain,exact,arXiv.org` = **7** 条
        # （20096 与 21602 的交集），用户看到的就是「没有结果」。
        domains = parsed.get("domains") or []
        if domains:
            include.append(f"domain,exact,{domains[0]}")

    # **tlevel 两个 scope 都发 —— spec §6 的门控在这里是错的。**
    # 规格写「tlevel / attribute / domain 都是 Primo Central 独有的 facet」，
    # 实测推翻了 tlevel 那半：本馆**有** tlevel，而且过滤真的生效 ——
    # `any,contains,machine learning` 本馆无过滤 **34,105** →
    # `facet_tlevel,exact,peer_reviewed` **45**、`facet_tlevel,exact,open_access` **250**。
    # 若照规格只在 PC 发，用户勾的「同行评审 / 开放获取」在本馆那栏就**静默失效**
    # —— 正是本次在修的那一类缺陷，不能修别的 facet 时把它留在原地。
    #
    # **两个勾同时给出两条同 facet 表达式，是刻意的 —— 别把它「统一」成只发一条。**
    # 两条连接后是 AND = **交集**，而交集恰恰是「同行评审**且**开放获取」的语义：
    # 实测本馆 45 ∩ 250 = **21**、PC 896,913 ∩ 953,408 = **533,967**，两个都非空且都
    # 严格小于各自单独的值 —— 可见 tlevel 是**逐条可多值**的，交集真的有内容。
    # （对比 rtype / domain：那两个的值互斥，交集恒为空，所以只发第一个并报窄化。）
    # 若这里改成只发第一条，用户勾的两个框会有一个**静默失效** —— 那正是本次的缺陷类型。
    if parsed.get("peer_reviewed_only"):
        include.append("facet_tlevel,exact,peer_reviewed")
    if parsed.get("open_access_only"):
        include.append("facet_tlevel,exact,open_access")

    # `attribute`（撤稿标记）仍**只在 PC** —— 与 tlevel 相反，这个门控实测成立：
    # 本馆把它当 qExclude 发出去**一条都不排除**（实测 34,105 → 34,105，差 0），
    # 说明本馆没有这个 facet。Retraction Watch 本来就是外部数据源。
    if is_pc and parsed.get("exclude_retracted"):
        exclude.append("facet_attribute,exact,retracted_publication")

    return include, exclude
