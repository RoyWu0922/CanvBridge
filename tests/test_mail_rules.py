"""`mail_rules` 纯引擎用例。无 I/O：不碰 IMAP、不碰 LLM、不碰网络。"""
from backend import mail_rules


def _mail(mid="1", addr="", subject="", body=""):
    return {"id": mid, "sender_addr": addr, "subject": subject, "body": body}


# --- validate_rules：空规则是「匹配一切」的陷阱 ---

def test_empty_rule_is_dropped_and_reported():
    """`senders` 与 `keywords` 都空时，子串匹配退化成「任何字符串都包含空串」
    ⇒ 整个邮箱被收进一个分类，而界面上看不出任何异常。必须丢弃**并报码**。"""
    rules, warnings = mail_rules.validate_rules([
        {"id": "r1", "name": "空", "senders": [], "keywords": []}])
    assert rules == []
    assert warnings.count("empty_rule") == 1


def test_whitespace_only_items_are_dropped_and_reported():
    """`["", "  "]` 里的空白串与空规则同罪。"""
    rules, warnings = mail_rules.validate_rules([
        {"id": "r1", "name": "空白", "senders": ["", "  "], "keywords": ["报告"]}])
    assert rules[0]["senders"] == []
    assert rules[0]["keywords"] == ["报告"]
    assert warnings.count("blank_item") == 1        # 去重：同一码只报一次


def test_bad_rules_are_dropped_and_reported_once():
    rules, warnings = mail_rules.validate_rules(
        ["nope", {"name": "无 id"}, {"id": "  "}, {"id": "r", "keywords": ["k"]}])
    assert [r["id"] for r in rules] == ["r"]
    assert warnings.count("bad_rule") == 1


def test_duplicate_rule_ids_are_dropped():
    """同 id 会让「勾选状态的键」撞车（键里含规则 id），必须只留第一条。"""
    rules, warnings = mail_rules.validate_rules([
        {"id": "r1", "keywords": ["a"]}, {"id": "r1", "keywords": ["b"]}])
    assert [r["keywords"] for r in rules] == [["a"]]
    assert warnings.count("bad_rule") == 1


def test_search_body_must_be_exactly_true():
    """`"yes"` / `1` 都不算 —— 正文那趟很贵，不能被真值语义悄悄打开。"""
    rules, _ = mail_rules.validate_rules([
        {"id": "r1", "keywords": ["k"], "search_body": "yes"}])
    assert rules[0]["search_body"] is False


def test_rule_name_falls_back_to_id():
    rules, _ = mail_rules.validate_rules([{"id": "r1", "keywords": ["k"]}])
    assert rules[0]["name"] == "r1"


# --- label_mails：匹配语义 ---

def test_keyword_matches_subject_case_insensitively():
    rules, _ = mail_rules.validate_rules([
        {"id": "r1", "name": "作业", "keywords": ["Lab Report"]}])
    assert mail_rules.label_mails(rules, [_mail(subject="LAB REPORT 2 due")]) == {"1": ["r1"]}


def test_keyword_does_not_see_body_unless_search_body():
    m = [_mail(subject="通知", body="请交实验报告")]
    off, _ = mail_rules.validate_rules([{"id": "r1", "keywords": ["实验报告"]}])
    assert mail_rules.label_mails(off, m) == {}
    on, _ = mail_rules.validate_rules(
        [{"id": "r1", "keywords": ["实验报告"], "search_body": True}])
    assert mail_rules.label_mails(on, m) == {"1": ["r1"]}


def test_sender_rule_matches_the_address_form():
    """规则写地址，就按地址匹配。

    **别把这句读成「和显示名无关」**——`sender_addr` 解不出地址时给的是**非地址**值
    （`From: Li Wei` → `"li"`、`From: Li:Wei` → `"wei"`、`From: Li <>` → `""`），
    所以按非地址值写的规则**也会**命中，见下一条。本用例名原先叫 `..._not_display_name`。
    """
    rules, _ = mail_rules.validate_rules([
        {"id": "r1", "name": "李教授", "senders": ["li.wei@cityu.edu.hk"]}])
    assert mail_rules.label_mails(rules, [_mail(addr="li.wei@cityu.edu.hk")]) == {"1": ["r1"]}
    assert mail_rules.label_mails(rules, [_mail(addr="other@cityu.edu.hk")]) == {}


def test_sender_rule_can_match_a_non_address_value():
    """`sender_addr` **不保证含 `@`**（Task 1 实测，生产路径）：`From: Li Wei` 给到 `"li"`。

    谁要是给 `label_mails` 加一条 `if "@" not in addr: continue` 的守卫，这条会红。
    **别再写「按教授中文名匹配」** —— 中文名到不了这里：带地址时本键是地址，
    不带地址的裸 8-bit 头在 `sender_name` 就先抛 TypeError（ledger 裁定 12）。
    本用例只钉一件事：不得假定本键含 `@`。
    """
    rules, _ = mail_rules.validate_rules([{"id": "r1", "senders": ["li"]}])
    assert mail_rules.label_mails(rules, [_mail(addr="li")]) == {"1": ["r1"]}


def test_sender_matches_a_bare_domain_suffix():
    """`@cityu.edu.hk` 这条规则要能收全校 —— 子串匹配天然支持，无需特判。"""
    rules, _ = mail_rules.validate_rules([{"id": "r1", "senders": ["@cityu.edu.hk"]}])
    assert mail_rules.label_mails(rules, [_mail(addr="a.b@cityu.edu.hk")]) == {"1": ["r1"]}


def test_sender_matches_case_insensitively():
    """地址侧的匹配同样大小写不敏感（规格：匹配前把待查文本也小写化）。

    本用例专钉 `label_mails` 里 `addr = ... .lower()` 那一步。上面几条 `test_sender_*`
    的 `addr` 夹具**全是小写**，删掉那一步它们一条都不会红（实测：15 条全绿）；
    规则侧的小写化由 `_clean_items` 负责，不靠本用例。
    """
    rules, _ = mail_rules.validate_rules([
        {"id": "r1", "name": "李教授", "senders": ["li.wei@cityu.edu.hk"]}])
    assert mail_rules.label_mails(
        rules, [_mail(addr="Li.Wei@CityU.EDU.HK")]) == {"1": ["r1"]}


def test_senders_and_keywords_are_or_within_one_rule():
    """一条规则内两个列表是**或**（见本任务「语义」一节的说明）。"""
    rules, _ = mail_rules.validate_rules([
        {"id": "r1", "senders": ["li@x.hk"], "keywords": ["报告"]}])
    assert mail_rules.label_mails(rules, [_mail(addr="li@x.hk")]) == {"1": ["r1"]}
    assert mail_rules.label_mails(rules, [_mail(subject="实验报告")]) == {"1": ["r1"]}
    assert mail_rules.label_mails(rules, [_mail(subject="别的")]) == {}


def test_multiple_rules_give_multiple_labels_and_order_does_not_matter():
    raw = [{"id": "r1", "name": "李教授", "senders": ["li@x.hk"]},
           {"id": "r2", "name": "作业", "keywords": ["报告"]}]
    m = [_mail(addr="li@x.hk", subject="实验报告")]
    rules, _ = mail_rules.validate_rules(raw)
    rev, _ = mail_rules.validate_rules(list(reversed(raw)))
    # 用**集合**比：不要求标签的排列顺序稳定，只要求命中的规则集合一致
    assert set(mail_rules.label_mails(rules, m)["1"]) == {"r1", "r2"}
    assert set(mail_rules.label_mails(rev, m)["1"]) == {"r1", "r2"}


def test_unmatched_mail_is_absent_from_the_result():
    rules, _ = mail_rules.validate_rules([{"id": "r1", "keywords": ["zzz"]}])
    assert mail_rules.label_mails(rules, [_mail(subject="别的")]) == {}


def test_mail_without_an_id_is_skipped():
    rules, _ = mail_rules.validate_rules([{"id": "r1", "keywords": ["k"]}])
    assert mail_rules.label_mails(rules, [{"subject": "k"}]) == {}


def test_non_list_matching_list_is_reported_rather_than_silently_dropped():
    """`senders` 传成字符串（而不是数组）⇒ 整条子句消失，必须报 `bad_rule`。

    缺键 / `None` 是「这个维度没写」，合法，**不报** —— 否则每条只用关键词的规则
    都会带一个假警告。
    """
    rules, warnings = mail_rules.validate_rules([
        {"id": "r1", "senders": "@cityu.edu.hk", "keywords": ["实验报告"]},
    ])
    assert warnings == ["bad_rule"]
    assert rules[0]["senders"] == []
    assert rules[0]["keywords"] == ["实验报告"]

    rules, warnings = mail_rules.validate_rules([{"id": "r2", "keywords": ["x"]}])
    assert warnings == []
    assert rules[0]["senders"] == []


def test_non_list_top_level_rules_is_reported_rather_than_silently_dropped():
    """`rules` 本身传成非数组 ⇒ **全部规则**消失、界面上「一封都没分类」且无解释。"""
    rules, warnings = mail_rules.validate_rules("oops")
    assert rules == []
    assert warnings == ["bad_rule"]
