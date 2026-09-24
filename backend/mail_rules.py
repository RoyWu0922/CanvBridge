"""邮件规则引擎：**无 I/O 的纯函数**（照 `backend/library_query.py` 的路子）。

分类结果只由用户的规则决定，不经过模型 —— 可预测、可复现、改一条立刻生效。
规则本身存在前端 localStorage，随请求体传进来，本模块不持有任何状态。
"""
from __future__ import annotations

#: 跨语言契约的警告码（与 `library_query.WARNING_CODES` 同构）。
#: 前端按 `mailtodo.warn.<code>` 取双语句子 —— 这里只出**码**，绝不出句子
#: （出句子会让英文界面冒中文）。
WARNING_CODES = (
    "bad_rule",     # 不是对象 / 缺 id / id 重复
    "empty_rule",   # 两个匹配列表都空 ⇒ 子串匹配下会命中**一切**
    "blank_item",   # 某个匹配项是空白串 ⇒ 同上
)


def _as_list(v) -> list:
    return v if isinstance(v, list) else []


def _clean_items(raw, warnings: list[str]) -> list[str]:
    """收敛一个匹配列表：只留非空字符串，去空白、小写化。"""
    out: list[str] = []
    for it in _as_list(raw):
        if not isinstance(it, str):
            continue
        s = it.strip()
        if not s:
            warnings.append("blank_item")
            continue
        out.append(s.lower())
    return out


def _dedupe(seq: list[str]) -> list[str]:
    """警告码去重（保持首次出现的次序）—— 一条坏规则报一次就够，
    否则 50 个空白项会把提示条刷成一面墙。"""
    out: list[str] = []
    for x in seq:
        if x not in out:
            out.append(x)
    return out


def validate_rules(raw) -> tuple[list[dict], list[str]]:
    """把请求体里的规则收敛成可信结构，并报出被丢弃的原因。

    **丢弃一律报码。** 空规则在子串匹配下等于「匹配一切」，静默接受会让整个邮箱
    被收进一个分类而界面上毫无异常 —— 与图书馆那轮修掉的静默失效同族。
    """
    warnings: list[str] = []
    rules: list[dict] = []
    seen: set[str] = set()
    for r in _as_list(raw):
        if not isinstance(r, dict):
            warnings.append("bad_rule")
            continue
        rid = r.get("id")
        if not isinstance(rid, str) or not rid.strip() or rid.strip() in seen:
            warnings.append("bad_rule")
            continue
        rid = rid.strip()
        seen.add(rid)
        senders = _clean_items(r.get("senders"), warnings)
        keywords = _clean_items(r.get("keywords"), warnings)
        if not senders and not keywords:
            warnings.append("empty_rule")
            continue
        name = r.get("name")
        rules.append({
            "id": rid,
            "name": name.strip() if isinstance(name, str) and name.strip() else rid,
            "senders": senders,
            "keywords": keywords,
            # 严格 `is True`：正文那趟很贵，不能被真值语义悄悄打开
            "search_body": r.get("search_body") is True,
        })
    return rules, _dedupe(warnings)


def label_mails(rules: list[dict], mails: list[dict]) -> dict[str, list[str]]:
    """{邮件 id: [规则 id, ...]}。命中几条带几个标签，**与规则顺序无关**。

    没命中任何规则的邮件**不出现在返回值里** —— 调用方用 `.get(id, [])` 取，
    「不在字典里」与「空列表」是同一件事，不必制造两种表示。
    """
    out: dict[str, list[str]] = {}
    for m in mails:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            continue
        addr = str(m.get("sender_addr") or "").lower()
        subject = str(m.get("subject") or "").lower()
        body = str(m.get("body") or "").lower()
        hits: list[str] = []
        for r in rules:
            if r["senders"] and any(s in addr for s in r["senders"]):
                hits.append(r["id"])
                continue
            hay = subject + ("\n" + body if r["search_body"] else "")
            if r["keywords"] and any(k in hay for k in r["keywords"]):
                hits.append(r["id"])
        if hits:
            out[mid] = hits
    return out
