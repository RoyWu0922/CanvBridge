# 邮件规则分类与待办 / 时间表 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户用自定义规则（教授邮箱 / 课程关键词）把学校邮件分类，产出一页「待办清单 + 时间表」，与既有的日历写入路径完全分开。

**Architecture:** 分类 100% 由后端**纯函数**规则引擎决定（规则随请求体传，后端无状态、不加持久化）；AI 只保留一件窄活 —— 从正文里抽待办与日期，且按需手动触发。前端新增一页，规则存 localStorage。

**Tech Stack:** FastAPI + Pydantic（后端）、原生四段 classic script（前端，无构建）、pytest / node 自检脚本。

**Spec:** `docs/superpowers/specs/2026-09-24-mail-rules-todo-design.md`

## Global Constraints

- **分类只由规则决定。** AI 不参与分类；`/api/mail/summarize` 与邮件页的 AI 分组视图**原样保留、一个字不改**。
- **所有错误一律 HTTP 200 + `{ok:false, error}`。** 前端 `frontend/util.js:26` 的 `api()` 不看状态码，直接把响应 `r.json()` 交给调用方；FastAPI 的 422 体是合法 JSON，那条「响应无法解析」的 catch 根本走不到。
- **警告以「码」跨语言传递。** 后端只出码（如 `empty_rule`），前端按 `t("mailtodo.warn." + code)` 取双语句子。**绝不出句子** —— 英文界面会冒中文。
- **邮箱只读。** 沿用 `mail_client` 的 EXAMINE + `BODY.PEEK` 保证，不发信、不改已读、不移动、不删除。
- **不动 `todo` 页与 `schedule` 页。**
- **测试绝不发真实网络请求、绝不调真实 LLM。** 后端与自检一律打桩（这花用户的真钱，按阻断级缺陷对待）。
- **`app.js` 的 TDZ 纪律：** 被 `applyLang()` 读到的状态，必须在 `frontend/app.js:2459` 那次顶层 `applyLang()` **之前**声明。判据是「这个函数读了哪些顶层 `let`，那些声明在**最早**调用时点之前吗」—— 不只是新增状态会踩，给既有函数加一个更早的调用时点同样致命。
- **i18n 每个键 zh / en 各写一次。** `tools/check_i18n_keys.mjs` 会拦「只写一侧」「引用了不存在的键」「死键数超出基线 `DEAD_BASELINE = 21`」。
- **命令一律用绝对解释器路径** `.venv/bin/python -m pytest`（zsh 下 `python` 不存在）。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `backend/mail_client.py` | 取信层；`header_item` 组装对外契约条目 | 改（加 `sender_addr`） |
| `backend/mail_rules.py` | **规则引擎**：校验规则、给邮件打标签。无 I/O 的纯函数 | 新建 |
| `backend/llm_client.py` | 模型调用层；新增抽取用的 prompt / schema / 归一 | 改（追加） |
| `backend/main.py` | 路由层；两个新端点 + 请求模型 | 改（追加） |
| `frontend/shell.js` | 页面注册表 `PAGES` / `PAGE_INIT` | 改（各加一项） |
| `frontend/index.html` | 导航按钮 + 页面容器 | 改（各加一处） |
| `frontend/i18n.js` | 双语字典 + `applyLang()` 重渲钩子 | 改（加键 + 加一行钩子） |
| `frontend/app.js` | 页面状态与渲染 | 改（状态声明 + 若干函数） |
| `tools/check_i18n_keys.mjs` | i18n 自检；动态家族白名单 | 改（`DYNAMIC_PREFIXES` 加前缀） |
| `tools/check_mailtodo_frontend.mjs` | **新页自检**：守住「勾选键 = 指纹而非 UID」 | 新建 |
| `tests/test_mail_rules.py` | 规则引擎用例 | 新建 |
| `tests/test_mail_client.py` | 契约键用例 | 改（5 处键集合断言 + 3 处名字/文案残留） |
| `tests/test_main.py` | 两个新端点用例 | 改（追加 + 3 处残留） |
| `tests/test_llm_client.py` | 新归一的用例 | 改（追加） |

**为什么不新开一个前端 js 文件：** 前端是四段 classic script（`shell.js` / `util.js` / `i18n.js` / `app.js`），加载顺序有硬性约束，`i18n.js` 的 `applyLang()` 会经钩子回调 `app.js` 里的渲染函数。加第五个文件要动 `index.html` 的 script 标签与加载序，正是本项目白屏事故的产地。既有每个页面都在 `app.js` 里，照旧。

---

### Task 1: `sender_addr` 进邮件契约

**Files:**
- Modify: `backend/mail_client.py:63`（`_CONTRACT_KEYS`）、`:187-208`（`header_item`）、`:422`、`:454`
- Test: `tests/test_mail_client.py:309`、`:335`、`:712-726`
- Test: `tests/test_main.py:1817-1819`、`:2034`、`:2043`

**Interfaces:**
- Consumes: 无（第一个任务）
- Produces: 邮件条目多一个键 `sender_addr: str` —— 恒为**小写地址**（非显示名），From 缺失时为 `""`。`_CONTRACT_KEYS` 变为 5 元组。**后续所有任务的规则匹配都吃这个键。**

**背景（为什么必须做）：** `/api/mail/list` 的 `sender` 是**显示名优先、退回地址**（`mail_client.py:90` `sender_name`：`return name or addr`），通常**根本不含邮箱地址**。用户按「教授邮箱」写的规则会一条都匹配不上，且**完全静默**。

- [ ] **Step 1: 先改测试，把契约钉到 5 个键**

`tests/test_mail_client.py` 里改名并改断言。

**（pre-flight 实测补正，2026-09-24）** 改 `header_item` 会让**文件里全部 5 处键集合断言**变红，不只下面列的那两条。另外 3 处**不在原锚点内**，必须一并改，否则 Step 4 的「全文件 PASS」根本达不到：

| 位置 | 所属用例 | 处理 |
|---|---|---|
| `tests/test_mail_client.py:226` | `test_list_mail_returns_contract_items`（`def` 在 `:216`） | 集合里补 `"sender_addr"` |
| `tests/test_mail_client.py:295` | `test_list_mail_returns_exactly_contract_keys`（`def` 在 `:286`） | 同上 |
| `tests/test_mail_client.py:334` | `test_do_list_applies_select_scope`（`def` 在 `:298`） | 同上。**注意 `:334` 是断言行、`:335` 是文案行 —— 相邻但两回事，只改 `:335` 会漏** |

下面两条现在就会红，因为它们要求恰好 4 个键：

```python
def test_header_item_shape_exactly_five_keys():
    """键集合白名单 —— is_read 已删除、sender_addr 已新增，多一个少一个都要红。"""
    m = _msg(subject="作业", sender="老师 <t@cityu.edu.hk>",
             date="Thu, 17 Sep 2026 16:31:00 +0800")
    it = mail_client.header_item("42", m)
    assert set(it) == {"id", "subject", "sender", "sender_addr", "received"}
    assert it["id"] == "42"
    assert it["subject"] == "作业"
    assert it["sender"] == "老师"
    assert it["sender_addr"] == "t@cityu.edu.hk"
    assert it["received"] == "2026-09-17T08:31:00+00:00"


def test_header_item_survives_missing_headers():
    it = mail_client.header_item("7", _msg())
    assert it == {"id": "7", "subject": "", "sender": "", "sender_addr": "",
                  "received": ""}


def test_contract_keys_is_exactly_the_five_public_keys():
    """白名单少一个键 = `_do_list` 在 `:455` 把它丢掉 = 前端永远匹配不上（静默）。
    用集合相等而不是 `in` —— `in` 在**多**键时照绿。"""
    assert set(mail_client._CONTRACT_KEYS) == {
        "id", "subject", "sender", "sender_addr", "received"}
```

再加一条**字段级**用例（这条才是 `sender_addr` 存在的理由）：

```python
def test_header_item_sender_addr_is_the_address_not_the_display_name():
    """`sender` 显示名优先、**不含**地址，规则要按邮箱匹配就必须有本键。
    大写域名必须被压平，否则用户按 `li.wei@cityu.edu.hk` 写的小写规则会静默匹配不上。"""
    m = _msg(subject="作业", sender="Li Wei <Li.Wei@CityU.EDU.HK>",
             date="Thu, 17 Sep 2026 16:31:00 +0800")
    it = mail_client.header_item("42", m)
    assert it["sender"] == "Li Wei"                     # 显示名优先，且不含地址
    assert it["sender_addr"] == "li.wei@cityu.edu.hk"   # 地址、小写


def test_header_item_sender_addr_is_empty_without_a_from_header():
    """`parseaddr(None)[1]` 实测返回 `''`、**不抛**（Task 1 探针，Python 3.11）。
    照 `sender_name` 的写法保持一致即可，不必额外兜底。"""
    assert mail_client.header_item("7", _msg())["sender_addr"] == ""
```

- [ ] **Step 2: 跑测试，确认红的正是预期那几条**

Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q -k "header_item or contract_keys"`
Expected: FAIL —— `test_header_item_shape_exactly_five_keys`、`test_header_item_survives_missing_headers`、`test_contract_keys_is_exactly_the_five_public_keys`、`test_header_item_sender_addr_is_the_address_not_the_display_name` 四条红（`KeyError: 'sender_addr'` / 集合不等），其余绿。**若红的条数或名字与此不符，先查清再往下走。**

- [ ] **Step 3: 改实现**

`backend/mail_client.py:63`：

```python
#: 对外契约的键集合（spec §3.5）。`_do_list` 返回前**按它放行**。
#: 白名单而不是黑名单：黑名单在新增内部键时静默泄漏，而泄漏的正是「不在契约里」的东西。
#: `sender_addr` 与 `sender` 并列：后者显示名优先、通常**不含**地址，规则匹配要的是地址。
_CONTRACT_KEYS = ("id", "subject", "sender", "sender_addr", "received")
```

`backend/mail_client.py:203-208`（`header_item` 的 return）：

```python
    return {
        "id": uid,
        "subject": decode_mime_header(msg.get("Subject")),
        "sender": sender_name(msg.get("From")),
        # 恒为**地址**（小写、去空白），供规则匹配用；不是显示名。
        # `parseaddr` 不是新依赖：`sender_name` 内部已在用，`_addr_domain` 是同族先例 ——
        # 这是把「已经解出来又被丢掉」的东西留下。
        "sender_addr": parseaddr(msg.get("From"))[1].strip().lower(),
        "received": _iso(received_dt(msg) or internaldate),
    }
```

- [ ] **Step 4: 跑测试，确认全绿**

Run: `.venv/bin/python -m pytest tests/test_mail_client.py -q`
Expected: PASS（全文件）—— **前提是 Step 1 那张补正表里的三处断言也改了**；漏一处这里就会红，而那红不是本任务引入的缺陷，是计划漏写。

- [ ] **Step 5: 清掉「四个键」家族的同族残留（改一处 ≠ 改一类）**

全仓活代码里共 9 处提到「四个键」，逐处按下列口径处理（`docs/` 下的旧计划与 `.superpowers/` 的历史报告**不动** —— 那是已完成任务的留档）：

| 位置 | 处理 |
|---|---|
| `backend/mail_client.py:422` | 注释「`header_item` 的契约恰好四个键」→ 改「五个键」 |
| `backend/mail_client.py:454` | 注释「只放行对外契约的四个键」→ 改「五个键」 |
| `backend/mail_client.py:455` | 代码本身不动（它按 `_CONTRACT_KEYS` 迭代，自动跟随） |
| `tests/test_mail_client.py:309` | docstring 里的用例名 → 改成新名 `test_header_item_shape_exactly_five_keys` |
| `tests/test_mail_client.py:335` | 「对外契约恰好四个键」→ 改「五个键」 |
| `tests/test_mail_client.py:712` | 旧用例名的 `def` 行本身也要改名 —— 只改 `:309` 与 `:1818` 里的引用会留下一个**指向不存在名字**的引用 |
| `tests/test_main.py:1817-1819` | 注释里的键序列与**指名引用**的用例名一起改（键序列补 `sender_addr`，用例名换新名） |
| `tests/test_main.py:2034` | 「响应只有这四个键」→ 改「这五个键」 |

- [ ] **Step 6: 更新 `tests/test_main.py` 里的夹具，让它们继续描述真实契约**

`/api/mail/summarize` 与 `/api/mail/list` 的夹具是 monkeypatch 的裸字典，加键后**不会变红**，但它描述的不再是真实契约 —— 这种「夹具与实现静默分家」正是日后漏键不被发现的产地。

**（pre-flight 实测补正，2026-09-24）** 这类 4 键字面量在 `tests/test_main.py` 里有 **9 处、分属 8 个用例**，不是「两处」：

| 用例 | 字面量位置 | 承重与否 |
|---|---|---|
| `test_mail_summarize_joins_bodies_and_groups` | `:2010` | **承重** —— 它喂的正是 `:2043` 那条断言，不补则该断言变红 |
| `test_mail_list_returns_mails` | `:1809` | 描述性 |
| `test_mail_list_tolerates_bodyless_request` | `:1843` | 描述性 |
| `test_mail_summarize_indexes_are_contiguous` | `:2058`、`:2059` | 描述性 |
| `test_mail_summarize_returns_mails_too` | `:2081` | 描述性 |
| `test_mail_summarize_fetch_bodies_failure_reports_mail_phase` | `:2111` | 描述性 |
| `test_mail_summarize_llm_failure_reports_llm_phase` | `:2125` | 描述性 |
| `test_mail_summarize_missing_body_falls_back_to_empty` | `:2158`、`:2159` | 描述性 |

**9 处全部补 `"sender_addr": ""`**（`:2010` 那处按真实契约补地址亦可）—— 只补「两处」会让另外 7 处继续描述一个已经不存在的契约，而本步骤存在的理由恰恰是消掉这一类。并把 `:2043` 的断言补上该键：

```python
    assert all(set(m) == {"id", "subject", "sender", "sender_addr", "received",
                          "index", "body"}
               for m in body["mails"])
```

- [ ] **Step 7: 跑全量，确认没有别的用例被打到**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS（`tests/` 全量）

- [ ] **Step 8: 提交**

```bash
git add backend/mail_client.py tests/test_mail_client.py tests/test_main.py
git commit -m "feat(mail): 条目新增 sender_addr（地址，供规则匹配）"
```

---

### Task 2: `backend/mail_rules.py` 规则引擎

**Files:**
- Create: `backend/mail_rules.py`
- Test: `tests/test_mail_rules.py`

**Interfaces:**
- Consumes: Task 1 的 `sender_addr` 键。
- Produces:
  - `WARNING_CODES: tuple[str, ...]` = `("bad_rule", "empty_rule", "blank_item")`
  - `validate_rules(raw) -> tuple[list[dict], list[str]]` —— 规则每项形如 `{"id": str, "name": str, "senders": list[str], "keywords": list[str], "search_body": bool}`，`senders`/`keywords` 里的串**已小写化、已去空白**；第二项是**去重后**的警告码。
  - `label_mails(rules: list[dict], mails: list[dict]) -> dict[str, list[str]]` —— `{邮件 id: [规则 id, ...]}`，无命中的邮件**不出现在字典里**。

**语义（本任务定死，后续任务按此消费）：**
- 匹配一律**大小写不敏感子串**（不分词）。匹配前把待查文本也小写化。
- `senders` 匹配 `sender_addr`；`keywords` 匹配**主题**，`search_body` 为真时**连正文一起**（`主题 + "\n" + 正文`）。
- **一条规则内，`senders` 与 `keywords` 是「或」** —— 任一列表命中即该规则命中。
  ⚠️ **规格未明、此处定为「或」**（规格 §2 的表只写了每个字段各自匹配什么，没写两者之间是且还是或）。理由：编辑器的两类输入是「按教授」「按关键词」两种独立用法，填了两边的人要的是「这位教授的**或**提到这事的」；「且」会让填了两边的规则大面积静默不命中。**若你要「且」，在实现前驳回。**
- 规则之间**无顺序**：命中几条带几个标签，结果与规则顺序无关。

- [ ] **Step 1: 写失败的用例**

创建 `tests/test_mail_rules.py`：

```python
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


def test_sender_matches_address_not_display_name():
    rules, _ = mail_rules.validate_rules([
        {"id": "r1", "name": "李教授", "senders": ["li.wei@cityu.edu.hk"]}])
    assert mail_rules.label_mails(rules, [_mail(addr="li.wei@cityu.edu.hk")]) == {"1": ["r1"]}
    assert mail_rules.label_mails(rules, [_mail(addr="other@cityu.edu.hk")]) == {}


def test_sender_matches_a_bare_domain_suffix():
    """`@cityu.edu.hk` 这条规则要能收全校 —— 子串匹配天然支持，无需特判。"""
    rules, _ = mail_rules.validate_rules([{"id": "r1", "senders": ["@cityu.edu.hk"]}])
    assert mail_rules.label_mails(rules, [_mail(addr="a.b@cityu.edu.hk")]) == {"1": ["r1"]}


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
```

- [ ] **Step 2: 跑测试，确认红的是「模块不存在」**

Run: `.venv/bin/python -m pytest tests/test_mail_rules.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'backend.mail_rules'`（整文件收集失败）。

> ⚠️ 这条红**不证明任何一条断言承重** —— 模块不存在会掩盖其后全部断言。Step 4 之后必须**逐条**确认每条断言真的跑过（`-v` 看条数），别把「模块建好就全绿」当成验证过了。

- [ ] **Step 3: 写实现**

创建 `backend/mail_rules.py`：

```python
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
```

- [ ] **Step 4: 跑测试，逐条确认都真的执行了**

Run: `.venv/bin/python -m pytest tests/test_mail_rules.py -v`
Expected: PASS —— **14 条**全绿，且 `-v` 列表里每条用例名都在。若条数少于 14，先查是哪条没被收集。

- [ ] **Step 5: 变异检验（证明关键守卫承重）**

变异 A —— 去掉空规则守卫：把 `if not senders and not keywords:` 改成 `if False:`
Run: `.venv/bin/python -m pytest tests/test_mail_rules.py -q -k empty`
Expected: **FAIL** —— `test_empty_rule_is_dropped_and_reported` 红。跑完**改回**。

变异 B —— 把正文开关改成恒开（`hay = subject + ("\n" + body if r["search_body"] else "")` → `hay = subject + "\n" + body`）：
Run: `.venv/bin/python -m pytest tests/test_mail_rules.py -q -k body`
Expected: **FAIL** —— `test_keyword_does_not_see_body_unless_search_body` 红。跑完**改回**。

变异 C —— 把 `senders` 改成匹配显示名（`addr` → `str(m.get("sender") or "")`）：
Run: `.venv/bin/python -m pytest tests/test_mail_rules.py -q -k sender`
Expected: **FAIL** —— 三条 `sender` 用例里有红。跑完**改回**。

> 每次变异后必须**清掉 `.pyc`**（`.venv/bin/python -B` 只防写不防读，判据是 `(mtime, size)`，同秒等长重写会跑上一次的代码）：
> `find . -name '__pycache__' -prune -exec rm -rf {} +`
> 并确认三个变异都**改回了**（提交前 `git diff --stat backend/mail_rules.py` 应为空）。

- [ ] **Step 6: 提交**

```bash
git add backend/mail_rules.py tests/test_mail_rules.py
git commit -m "feat(mail): 规则引擎纯函数（校验 + 打标签）"
```

---

### Task 3: `POST /api/mail/classify`

**Files:**
- Modify: `backend/main.py`（import 块 `:16-17`；追加请求模型与端点）
- Test: `tests/test_main.py`（追加）

**Interfaces:**
- Consumes: `mail_rules.validate_rules` / `label_mails`（Task 2）、`mail_client.list_mail` / `fetch_bodies`、Task 1 的 `sender_addr`。
- Produces: `POST /api/mail/classify`，body `{days?: int = 14, rules?: list}`，响应
  `{ok: true, count: int, mails: [{id, subject, sender, sender_addr, received, labels: [str]}], warnings: [str]}`
  或 `{ok: false, error: str}`。

- [ ] **Step 1: 写失败的用例**

追加到 `tests/test_main.py`（照 `:1807` 既有的 `monkeypatch.setattr(mail_client, ...)` 写法）：

```python
# --- 邮件规则分类 ---

def _mt_mails():
    return [
        {"id": "A", "subject": "实验报告", "sender": "李老師",
         "sender_addr": "li.wei@cityu.edu.hk", "received": "2026-09-17T08:31:00Z"},
        {"id": "B", "subject": "校历", "sender": "Registry",
         "sender_addr": "reg@cityu.edu.hk", "received": "2026-09-16T08:31:00Z"},
    ]


def test_mail_classify_labels_by_rules(monkeypatch):
    monkeypatch.setattr(mail_client, "list_mail", lambda days: _mt_mails())
    body = TestClient(main.app).post("/api/mail/classify", json={
        "days": 7,
        "rules": [{"id": "r1", "name": "李教授", "senders": ["li.wei@cityu.edu.hk"]}]}).json()
    assert body["ok"] is True
    assert body["count"] == 2
    assert body["warnings"] == []
    # 逐条邮件的键集合：顶层白名单管不到元素，而 `mails` 正是前端的回指表。
    # 用 `==` 而不是 `<=` —— 子集断言在**少一个键**时照样绿。
    assert all(set(m) == {"id", "subject", "sender", "sender_addr", "received",
                          "labels"} for m in body["mails"])
    assert body["mails"][0]["labels"] == ["r1"]
    assert body["mails"][1]["labels"] == []
    assert set(body) == {"ok", "count", "mails", "warnings"}


def test_mail_classify_defaults_to_fourteen_days(monkeypatch):
    """待办跨周，邮件页的 7 天会挡住下周的事。形状必须是 int（见既有 `days` 口径）。"""
    seen = {}
    monkeypatch.setattr(mail_client, "list_mail",
                        lambda days: (seen.setdefault("d", days), [])[1])
    TestClient(main.app).post("/api/mail/classify", json={}).json()
    assert seen["d"] == 14


def test_mail_classify_only_fetches_bodies_when_a_rule_needs_them(monkeypatch):
    """默认路径**不碰正文**：30 封要 3 秒（5 封一批、批间 0.5s 节流）。"""
    monkeypatch.setattr(mail_client, "list_mail", lambda days: _mt_mails())
    called = []
    monkeypatch.setattr(mail_client, "fetch_bodies",
                        lambda items: (called.append(items), {})[1])
    TestClient(main.app).post("/api/mail/classify", json={
        "rules": [{"id": "r1", "senders": ["li.wei@cityu.edu.hk"]}]}).json()
    assert called == []
    TestClient(main.app).post("/api/mail/classify", json={
        "rules": [{"id": "r1", "keywords": ["x"], "search_body": True}]}).json()
    assert len(called) == 1


def test_mail_classify_fetches_bodies_for_all_mails_not_only_unmatched(monkeypatch):
    """**多标签下不能跳过已命中的邮件取正文**：一封已被「李教授」命中的信仍可能再命中
    一条只在正文出现的关键词规则，跳过即静默漏标签。"""
    monkeypatch.setattr(mail_client, "list_mail", lambda days: _mt_mails())
    sent = []
    monkeypatch.setattr(mail_client, "fetch_bodies",
                        lambda items: (sent.append([m["id"] for m in items]),
                                       {"A": "补充：实验报告提交", "B": ""})[1])
    body = TestClient(main.app).post("/api/mail/classify", json={"rules": [
        {"id": "r1", "senders": ["li.wei@cityu.edu.hk"]},
        {"id": "r2", "keywords": ["实验报告"], "search_body": True}]}).json()
    assert sent == [["A", "B"]]           # 两封都要取，不能只取 B
    assert set(body["mails"][0]["labels"]) == {"r1", "r2"}


def test_mail_classify_passes_rule_warning_codes_through(monkeypatch):
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [])
    body = TestClient(main.app).post("/api/mail/classify", json={
        "rules": [{"id": "r1", "senders": [], "keywords": []}]}).json()
    assert body["ok"] is True
    assert body["warnings"] == ["empty_rule"]


def test_mail_classify_tolerates_bodyless_request(monkeypatch):
    monkeypatch.setattr(mail_client, "list_mail", lambda days: [])
    body = TestClient(main.app).post("/api/mail/classify").json()
    assert body["ok"] is True


def test_mail_classify_failure_is_200_json(monkeypatch):
    def boom(days):
        raise mail_client.MailError("邮箱会话已失效")
    monkeypatch.setattr(mail_client, "list_mail", boom)
    r = TestClient(main.app).post("/api/mail/classify", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "邮箱会话已失效" in body["error"]
```

- [ ] **Step 2: 跑测试，确认红的正是这 7 条**

Run: `.venv/bin/python -m pytest tests/test_main.py -q -k mail_classify`
Expected: FAIL —— 7 条全红（`404`，路由不存在）。

- [ ] **Step 3: 写实现**

`backend/main.py` 的 import 块（`:16-17`）加 `mail_rules`（保持字母序）：

```python
from . import (apple_script, banweb, canvas_client, credentials, files_downloader,
               library_areas, library_client, library_query, llm_client, mail_client,
               mail_rules)
```

请求模型（放在 `MailSummaryRequest` 附近）：

```python
class MailClassifyRequest(BaseModel):
    #: 待办跨周，默认 14 天（邮件页默认 7 天，会挡住下周的事）。
    days: int = 14
    #: 规则来自前端 localStorage，本端点**不持有**它们。`Any` + 由 mail_rules 收敛：
    #: 用具体类型会让畸形规则抛 422，而 422 体是合法 JSON、走不到 api() 那条 catch。
    rules: Any = None
```

端点（放在 `mail_summarize` 之后）：

```python
@app.post("/api/mail/classify")
def mail_classify(req: MailClassifyRequest = MailClassifyRequest()):
    """按用户规则给邮件打标签。**零 AI、零成本、可复现** —— 分类不经模型。

    正文那趟的代价：只要有**任意一条**规则 `search_body` 为真，就给窗口内**所有**
    邮件取正文。曾考虑「已命中的跳过取正文」——**那是错的**：多标签下，一封已被
    「李教授」命中的邮件仍可能再命中一条只在正文出现的关键词规则，跳过即**静默漏标签**。
    所以宁可慢也不漏。**默认关闭**，默认路径不碰正文。
    """
    try:
        mails = mail_client.list_mail(req.days)
        rules, warnings = mail_rules.validate_rules(req.rules)
        if mails and any(r["search_body"] for r in rules):
            bodies = mail_client.fetch_bodies(mails)
            mails = [{**m, "body": bodies.get(m["id"], "")} for m in mails]
        labels = mail_rules.label_mails(rules, mails)
        out = [{"id": m["id"], "subject": m["subject"], "sender": m["sender"],
                "sender_addr": m["sender_addr"], "received": m["received"],
                "labels": labels.get(m["id"], [])} for m in mails]
        return {"ok": True, "count": len(out), "mails": out, "warnings": warnings}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
```

- [ ] **Step 4: 跑测试，确认全绿**

Run: `.venv/bin/python -m pytest tests/test_main.py -q -k mail_classify`
Expected: PASS（7 条）

- [ ] **Step 5: 跑全量**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add backend/main.py tests/test_main.py
git commit -m "feat(mail): /api/mail/classify 规则分类端点"
```

---

### Task 4: `llm_client.extract_todos`（抽取专用的 prompt / schema / 归一）

**Files:**
- Modify: `backend/llm_client.py`（追加在 `summarize_mail` 之后）
- Test: `tests/test_llm_client.py`（追加）

**Interfaces:**
- Consumes: 无（纯 LLM 层）
- Produces: `extract_todos(base_url, api_key, model, mails, language="zh") -> dict`，返回
  `{"items": [{"mail_index": int, "summary": str, "reminders": [dict], "calendar_events": [dict]}], "warnings": [str], "dropped": int}`。
  `warnings` 是**码**（`ai_unusable` / `ai_dropped`），不是句子 —— 与 Task 2 同一套跨语言契约。
  `mails` 每项须含 `index`（整数、连续）、`subject`、`sender`、`received`、`body`。

**与 `summarize_mail` 的分工：** 新写聚焦的 prompt/schema，**不套用** `_MAIL_SCHEMA_ZH/_EN` ——
(a) 它强制的「课程 → 类型」两级归类正是本设计要丢掉的东西，白花 token；
(b) 它的「每封必须被归类」「一封只能出现一次」是**分组**约束，套到抽取上会平白多出失败模式。
**但复用它的两个已验证零件**：`_call_chat(..., json_mode=True)` 的两轮重试写法、以及
「越界 `mail_index` 一律丢弃、绝不把 AI 编的内容挂到真实邮件上」这条归一纪律。

- [ ] **Step 1: 写失败的用例**

追加到 `tests/test_llm_client.py`：

```python
# --- extract_todos：抽取路径 ---

def _ex_mail(idx=0, mid="A", subject="作业", body="下周五前交实验报告"):
    return {"index": idx, "id": mid, "subject": subject, "sender": "李老師",
            "received": "2026-09-17T08:31:00Z", "body": body}


def test_extract_todos_normalizes_items(monkeypatch):
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: json.dumps({
        "items": [{"mail_index": 0, "summary": "交实验报告",
                   "reminders": [{"title": "交实验报告", "due_date": "2026-09-25T23:59:00",
                                  "notes": ""}],
                   "calendar_events": []}]}))
    r = llm_client.extract_todos("u", "k", "m", [_ex_mail()])
    assert r["warnings"] == []
    assert r["dropped"] == 0
    assert r["items"][0]["mail_index"] == 0
    assert r["items"][0]["summary"] == "交实验报告"
    assert r["items"][0]["reminders"][0]["title"] == "交实验报告"


def test_extract_todos_drops_out_of_range_index(monkeypatch):
    """**绝不能把 AI 编的内容挂到真实邮件上** —— 与 _normalize_groups 同一条纪律。"""
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: json.dumps({
        "items": [{"mail_index": 7, "summary": "编的", "reminders": [], "calendar_events": []},
                  {"mail_index": 0, "summary": "真的", "reminders": [], "calendar_events": []}]}))
    r = llm_client.extract_todos("u", "k", "m", [_ex_mail()])
    assert [it["summary"] for it in r["items"]] == ["真的"]
    assert r["dropped"] == 1
    assert "ai_dropped" in r["warnings"]


def test_extract_todos_drops_boolean_index(monkeypatch):
    """`True` 是 `int` 的子类 —— 不显式挡掉就会被当成下标 1。"""
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: json.dumps({
        "items": [{"mail_index": True, "summary": "x", "reminders": [], "calendar_events": []}]}))
    r = llm_client.extract_todos("u", "k", "m", [_ex_mail(0), _ex_mail(1)])
    assert r["items"] == []
    assert r["dropped"] == 1


def test_extract_todos_unusable_structure_is_reported(monkeypatch):
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: json.dumps({"nope": 1}))
    r = llm_client.extract_todos("u", "k", "m", [_ex_mail()])
    assert r["items"] == []
    assert r["warnings"] == ["ai_unusable"]


def test_extract_todos_falls_back_to_excerpt_when_summary_is_blank(monkeypatch):
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: json.dumps({
        "items": [{"mail_index": 0, "summary": "   ", "reminders": [], "calendar_events": []}]}))
    r = llm_client.extract_todos("u", "k", "m", [_ex_mail()])
    assert r["items"][0]["summary"].startswith("下周五前交实验报告")


def test_extract_todos_non_list_fields_become_empty_arrays(monkeypatch):
    """形状坏掉时给空数组，绝不把字符串塞进渲染层。"""
    monkeypatch.setattr(llm_client, "_call_chat", lambda *a, **k: json.dumps({
        "items": [{"mail_index": 0, "summary": "s", "reminders": "oops",
                   "calendar_events": [1, {"title": "ok"}]}]}))
    r = llm_client.extract_todos("u", "k", "m", [_ex_mail()])
    assert r["items"][0]["reminders"] == []
    assert r["items"][0]["calendar_events"] == [{"title": "ok"}]


def test_extract_todos_empty_input_skips_the_model(monkeypatch):
    called = []
    monkeypatch.setattr(llm_client, "_call_chat",
                        lambda *a, **k: (called.append(1), "{}")[1])
    r = llm_client.extract_todos("u", "k", "m", [])
    assert called == []
    assert r == {"items": [], "warnings": [], "dropped": 0}


def test_extract_todos_retries_once_then_reports(monkeypatch):
    """两轮都抛 requests 异常 ⇒ 报「结构不可用」，不是「无法解析」。"""
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise requests.RequestException("network down")

    monkeypatch.setattr(llm_client, "_call_chat", boom)
    r = llm_client.extract_todos("u", "k", "m", [_ex_mail()])
    assert len(calls) == 2
    assert r["items"] == []
    assert r["warnings"] == ["ai_unusable"]
```

> 文件顶部已有的 `import json` / `import requests` / `from backend import llm_client` 若缺哪个就补哪个 —— 按该文件当下的既有 import 写，别新造。

- [ ] **Step 2: 跑测试，确认红的是「函数不存在」**

Run: `.venv/bin/python -m pytest tests/test_llm_client.py -q -k extract`
Expected: FAIL（`AttributeError: module 'backend.llm_client' has no attribute 'extract_todos'`）

> 同样地，这条红**不证明任何断言承重**。Step 4 要用 `-v` 数条数，确认 8 条都真的跑了。

- [ ] **Step 3: 写实现**

追加到 `backend/llm_client.py`：

```python
# ---------------------------------------------------------------------------
# 抽取路径：只抽「待办 + 带日期的日程」，不做任何分组
# ---------------------------------------------------------------------------

_TODOS_SCHEMA_ZH = """只输出如下 JSON，不要任何解释、不要 markdown 代码块：
{"items":[{"mail_index":<输入里的邮件编号，整数>,
  "summary":"一句话中文摘要",
  "reminders":[{"title":"","due_date":"YYYY-MM-DDTHH:MM:SS","notes":""}],
  "calendar_events":[{"title":"","start":"YYYY-MM-DDTHH:MM:SS","end":"YYYY-MM-DDTHH:MM:SS","location":"","notes":""}]}]}
规则：
- 邮件里**没有**明确截止时间时，calendar_events 与 reminders 都留空数组，绝不臆造日期。
- 只列**要做的事**。纯告知类邮件（讲座宣传、系统维护）没有待办就留空，可以不出现在 items 里。
- 每封输入邮件最多出现一次。"""

_TODOS_SCHEMA_EN = """Output only the following JSON, no prose, no markdown fences:
{"items":[{"mail_index":<the mail number from the input, integer>,
  "summary":"one-sentence summary",
  "reminders":[{"title":"","due_date":"YYYY-MM-DDTHH:MM:SS","notes":""}],
  "calendar_events":[{"title":"","start":"YYYY-MM-DDTHH:MM:SS","end":"YYYY-MM-DDTHH:MM:SS","location":"","notes":""}]}]}
Rules:
- If a mail states **no** explicit deadline, leave calendar_events and reminders empty. Never invent dates.
- List only **action items**. Purely informational mail may be omitted entirely.
- Each input mail appears at most once."""


def _build_extract_prompt(mails: list[dict], language: str = "zh") -> str:
    if language == "en":
        head = "Extract action items and dated events from the mails below.\n"
        schema = _TODOS_SCHEMA_EN
    else:
        head = "从下面的邮件里抽出待办事项与带日期的日程。\n"
        schema = _TODOS_SCHEMA_ZH
    parts = [head, schema, "\n---\n"]
    for m in mails:
        parts.append(
            f"### 邮件 #{m['index']}\n"
            f"主题：{m.get('subject', '')}\n"
            f"发件人：{m.get('sender', '')}\n"
            f"时间：{m.get('received', '')}\n"
            f"正文：\n{m.get('body', '')}\n\n")
    return "".join(parts)


def _keep_dicts(v) -> list:
    """只留对象元素；非列表一律回空数组。

    形状坏掉时给**空**，绝不把字符串塞进渲染层 —— 前端对它做 `.map()`，
    一个字符串会让整页白屏而不是少几行。
    """
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _normalize_extract(data: dict, mails: list[dict], language: str) -> dict:
    """收敛抽取结果。越界 / 非整数 mail_index 一律丢 —— 绝不能把 AI 编的内容
    挂到真实邮件上（与 `_normalize_groups` 同一条纪律）。

    警告出**码**不出句子：前端按 `mailtodo.warn.<code>` 取双语，出句子会让英文界面冒中文。
    """
    items_in = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items_in, list):
        return {"items": [], "warnings": ["ai_unusable"], "dropped": 0}
    out: list[dict] = []
    dropped = 0
    for it in items_in:
        if not isinstance(it, dict):
            continue
        idx = it.get("mail_index")
        # `isinstance(True, int)` 为真 ⇒ 布尔必须**显式**挡在前面，否则被当成下标 1
        if isinstance(idx, bool) or not isinstance(idx, int) or not (0 <= idx < len(mails)):
            dropped += 1
            continue
        summary = it.get("summary")
        out.append({
            "mail_index": idx,
            "summary": summary if isinstance(summary, str) and summary.strip()
                       else _excerpt(mails[idx]),
            "reminders": _keep_dicts(it.get("reminders")),
            "calendar_events": _keep_dicts(it.get("calendar_events")),
        })
    warnings = ["ai_dropped"] if dropped else []
    return {"items": out, "warnings": warnings, "dropped": dropped}


def extract_todos(base_url: str, api_key: str, model: str,
                  mails: list[dict], language: str = "zh") -> dict:
    """从邮件正文里抽出待办与带日期的日程。**这是本设计里 AI 唯一的活。**

    `mails` 每项：{index, subject, sender, received, body}。
    返回 {"items": [...], "warnings": [码], "dropped": int}；整体失败时 items 为空、
    warnings 有话说，**绝不静默降级**。
    """
    if not mails:
        return {"items": [], "warnings": [], "dropped": 0}
    trimmed = trim_bodies(mails)
    prompt = _build_extract_prompt(trimmed, language)
    data = None           # 首次就抛异常时，循环后的 isinstance 守卫要有值可查
    for _attempt in range(2):
        try:
            content = _call_chat(base_url, api_key, model, prompt, json_mode=True)
            data = _parse_json(content)
            if not isinstance(data, dict):
                raise ValueError("LLM 返回非 JSON 对象")
            break
        except (requests.RequestException, ValueError, KeyError, TypeError, RuntimeError):
            # 与 `summarize_mail` 同一写法：值正确性上冗余（下一轮赋值会覆盖），
            # 与后面那行 isinstance 守卫合起来才防住 UnboundLocalError
            data = None
            continue
    if not isinstance(data, dict):
        return {"items": [], "warnings": ["ai_unusable"], "dropped": 0}
    return _normalize_extract(data, trimmed, language)
```

> `trim_bodies` / `_parse_json` / `_excerpt` 都是该文件**已有**的辅助函数（`summarize_mail` 在用）。落笔前先在该文件里核一遍这三个名字确实存在；若某个名字不同，按文件里的实际名字写，别照抄本块。

- [ ] **Step 4: 跑测试，逐条确认**

Run: `.venv/bin/python -m pytest tests/test_llm_client.py -q -k extract -v`
Expected: PASS —— **8 条**全绿。

- [ ] **Step 5: 变异检验**

变异 A —— 去掉布尔守卫（`isinstance(idx, bool) or` 删掉）：
Run: `.venv/bin/python -m pytest tests/test_llm_client.py -q -k boolean`
Expected: **FAIL**。跑完改回。

变异 B —— 把越界丢弃改成「不过滤」（`not (0 <= idx < len(mails))` → `False`）：
Run: `.venv/bin/python -m pytest tests/test_llm_client.py -q -k range`
Expected: **FAIL**（`IndexError` 或断言不等）。跑完改回。

> 每次变异后清 `.pyc`：`find . -name '__pycache__' -prune -exec rm -rf {} +`

- [ ] **Step 6: 提交**

```bash
git add backend/llm_client.py tests/test_llm_client.py
git commit -m "feat(mail): extract_todos 抽取专用的 prompt/schema/归一"
```

---

### Task 5: `POST /api/mail/extract`

**Files:**
- Modify: `backend/main.py`（追加请求模型与端点）
- Test: `tests/test_main.py`（追加）

**Interfaces:**
- Consumes: `llm_client.extract_todos`（Task 4）、`mail_client.list_mail` / `fetch_bodies`。
- Produces: `POST /api/mail/extract`，body
  `{llm_base_url, llm_api_key, llm_model, ids: [str], days?: int = 14, language?: str = "zh"}`，响应
  `{ok: true, items: [{id, mail_index, summary, reminders, calendar_events}], warnings: [str], dropped: int, missing: int}`
  或 `{ok: false, reason: str, error: str}`。

⚠️ **规格偏离（已论证）：** 规格 §3 的 extract body 列表里**没有 `days`**。实现时必须补上 ——
否则后端无从知道要在哪个窗口里找回这些 id，只能另定一个固定窗口，而那会让两个端点的窗口
**静默漂移**（分类按 14 天给 id，抽取按 7 天找 ⇒ 找不到就少了一周的待办，且不报错）。
前端把分类时用的同一个值透传过来。

- [ ] **Step 1: 写失败的用例**

```python
def test_mail_extract_maps_indices_back_to_mail_ids(monkeypatch):
    monkeypatch.setattr(mail_client, "list_mail", lambda days: _mt_mails())
    monkeypatch.setattr(mail_client, "fetch_bodies",
                        lambda items: {"A": "下周五前交实验报告", "B": ""})
    seen = {}

    def fake_extract(base, key, model, mails, language="zh"):
        seen["mails"] = mails
        return {"items": [{"mail_index": 0, "summary": "交报告",
                           "reminders": [{"title": "交报告", "due_date": "2026-09-25T23:59:00",
                                          "notes": ""}],
                           "calendar_events": []}],
                "warnings": [], "dropped": 0}

    monkeypatch.setattr(llm_client, "extract_todos", fake_extract)
    body = TestClient(main.app).post("/api/mail/extract", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "ids": ["A"], "days": 7, "language": "zh"}).json()
    assert body["ok"] is True
    # index 是回指锚，必须从 0 连续编号
    assert [m["index"] for m in seen["mails"]] == [0]
    assert seen["mails"][0]["body"] == "下周五前交实验报告"
    # 前端只认 **id**，不认下标
    assert body["items"][0]["id"] == "A"
    assert body["items"][0]["mail_index"] == 0
    assert set(body) == {"ok", "items", "warnings", "dropped", "missing"}


def test_mail_extract_reports_ids_that_were_not_found(monkeypatch):
    """要了 3 个 id、窗口里只有 1 个 ⇒ 必须说出来，不能少给两条还答 ok。
    多标签/缓存下这是真实情形：缓存里的邮件可能已被挤出窗口。"""
    monkeypatch.setattr(mail_client, "list_mail", lambda days: _mt_mails())
    monkeypatch.setattr(mail_client, "fetch_bodies", lambda items: {"A": "x"})
    monkeypatch.setattr(llm_client, "extract_todos",
                        lambda *a, **k: {"items": [], "warnings": [], "dropped": 0})
    body = TestClient(main.app).post("/api/mail/extract", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "ids": ["A", "ZZ", "YY"]}).json()
    assert body["ok"] is True
    assert body["missing"] == 2


def test_mail_extract_skips_the_model_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(mail_client, "list_mail", lambda days: _mt_mails())
    called = []
    monkeypatch.setattr(llm_client, "extract_todos",
                        lambda *a, **k: (called.append(1), {})[1])
    body = TestClient(main.app).post("/api/mail/extract", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
        "ids": ["ZZ"]}).json()
    assert called == []
    assert body["items"] == []
    assert body["missing"] == 1


def test_mail_extract_rejects_a_missing_or_bad_ids(monkeypatch):
    for bad in [None, [], "A", ["A", 3], ["A", "  "], [""]]:
        body = TestClient(main.app).post("/api/mail/extract", json={
            "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m",
            "ids": bad}).json()
        assert body["ok"] is False, bad
        assert "ids" in body["error"]


def test_mail_extract_reports_the_mail_phase(monkeypatch):
    def boom(days):
        raise mail_client.MailError("邮箱会话已失效")
    monkeypatch.setattr(mail_client, "list_mail", boom)
    r = TestClient(main.app).post("/api/mail/extract", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "ids": ["A"]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == "mail"


def test_mail_extract_llm_failure_reports_llm_phase(monkeypatch):
    monkeypatch.setattr(mail_client, "list_mail", lambda days: _mt_mails())
    monkeypatch.setattr(mail_client, "fetch_bodies", lambda items: {"A": "x"})

    def boom(*a, **k):
        raise RuntimeError("模型炸了")
    monkeypatch.setattr(llm_client, "extract_todos", boom)
    body = TestClient(main.app).post("/api/mail/extract", json={
        "llm_base_url": "u", "llm_api_key": "k", "llm_model": "m", "ids": ["A"]}).json()
    assert body["ok"] is False
    assert body["reason"] == "llm"
```

- [ ] **Step 2: 跑测试，确认红的是这 6 条（404）**

Run: `.venv/bin/python -m pytest tests/test_main.py -q -k mail_extract`
Expected: FAIL —— 6 条全红（路由不存在）。

- [ ] **Step 3: 写实现**

请求模型：

```python
class MailExtractRequest(LLMConfig):
    #: 要抽取的邮件 id（来自 /api/mail/classify 的响应）。
    ids: Any = None
    #: **与分类用同一个窗口**。规格 §3 的 body 列表里没有它，实现时补上：不给窗口
    #: 后端就只能另定一个固定值，那会让两个端点的窗口静默漂移（分类按 14 天给 id、
    #: 抽取按 7 天找 ⇒ 少一周的待办且不报错）。
    days: int = 14
    language: str = "zh"


def _mail_id_list(v) -> list[str] | None:
    """请求里的邮件 id 列表：必须是非空数组、每项都是非空**字符串**。

    不做任何隐式转换（`3` 不变成 `"3"`）—— 与既有的 `_mail_uid` / `_mail_index` 同一口径。
    返回 `None` 表示形状不合法。
    """
    if not isinstance(v, list) or not v:
        return None
    out: list[str] = []
    for x in v:
        if not isinstance(x, str) or not x.strip():
            return None
        out.append(x.strip())
    return out
```

端点：

```python
@app.post("/api/mail/extract")
def mail_extract(req: MailExtractRequest):
    """AI 只做一件窄活：从**指定邮件**的正文里抽出待办与日期。取信仍是纯读。

    分类不经过这里（那是 `/api/mail/classify` 的规则引擎），故本端点不需要 rules。
    未在窗口里找到的 id 由 `missing` 报出来 —— 少给几条还答 ok 是静默丢件。
    """
    ids = _mail_id_list(req.ids)
    if ids is None:
        return {"ok": False, "error": "邮件编号列表（ids）必须是非空数字串数组"}
    phase = "mail"
    try:
        mails = mail_client.list_mail(req.days)
        want = set(ids)
        sel = [m for m in mails if m["id"] in want]
        missing = len(want) - len(sel)
        if not sel:
            # 一封都没找到：不调模型（那是真金白银），但**要说出来**
            return {"ok": True, "items": [], "warnings": [], "dropped": 0,
                    "missing": missing}
        bodies = mail_client.fetch_bodies(sel)
        for i, m in enumerate(sel):
            m["index"] = i
            m["body"] = bodies.get(m["id"], "")
        phase = "llm"
        r = llm_client.extract_todos(req.llm_base_url, req.llm_api_key, req.llm_model,
                                     sel, language=req.language)
        # `mail_index` 是 `sel` 的下标（llm_client 已保证它在范围内）→ 换成真实 id。
        # 前端只认 id：下标是这一趟的临时编号，刷新后毫无意义，拿它当缓存键会让
        # 勾选状态在下次刷新时挂到**另一封邮件**上。
        items = [{**it, "id": sel[it["mail_index"]]["id"]} for it in r["items"]]
        return {"ok": True, "items": items, "warnings": r["warnings"],
                "dropped": r["dropped"], "missing": missing}
    except Exception as exc:
        # `reason` 区分取信阶段与 AI 阶段（同 /api/mail/summarize）：认证失败该去设置页
        # 重填应用专用密码，报成「AI 失败」会把用户指向查 API key。
        return {"ok": False, "reason": phase, "error": str(exc)}
```

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_main.py -q -k mail_extract`
Expected: PASS（6 条）

- [ ] **Step 5: 跑全量**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add backend/main.py tests/test_main.py
git commit -m "feat(mail): /api/mail/extract 端点（AI 只抽待办与日期）"
```

---

### Task 6: 前端接入壳层 + 空壳渲染

**Files:**
- Modify: `frontend/shell.js:5`、`:11-21`
- Modify: `frontend/index.html:40`（导航）、`:257`（页面容器）
- Modify: `frontend/i18n.js`（`nav.mailtodo` 两处、页面键、`:896` 附近的 `applyLang()` 钩子）
- Modify: `frontend/app.js:2459` **之前**（状态声明）、文件末尾（函数）

**Interfaces:**
- Consumes: 无后端依赖（本任务只做空壳）
- Produces: 函数 `initMailTodoTab()`、`renderMailTodo()`；状态
  `mtDays`（`= 14`，由工具条的天数选择改）、`MT_RULES_KEY`（`= "sc_mailRules"`）、
  `MT_DONE_KEY`（`= "sc_mailDone"`）、
  `mtState`（对象）、`mtRules`（数组）。**后续三个任务都在这套状态上长。**

**这个任务最要紧的纪律：** 页面**能被打开、不白屏**。三处必须同步（漏一处就静默不显示或整页白屏）：

- [ ] **Step 1: 三个注册点各加一项**

`frontend/shell.js:5`：

```js
const PAGES = ["home","announce","discuss","schedule","todo","files","grades","courses","library","mail","mailtodo","settings"];
```

`frontend/shell.js:11-21` 的 `PAGE_INIT`（照既有缩进）：

```js
  mail:     () => initMailTab(),
  mailtodo: () => initMailTodoTab(),
};
```

`frontend/index.html:40` 之后（`nav-gap` 在 41 行、settings 固定在最后）：

```html
      <button class="nav-item" data-page="mailtodo"><span data-i18n="nav.mailtodo"></span></button>
```

- [ ] **Step 2: 加页面容器**

`frontend/index.html:257`（`#page-mail` 结束）与 `:259`（`#page-settings` 开始）之间插入：

```html
    <div id="page-mailtodo" class="page" hidden>
      <div class="card">
        <div class="mail-toolbar">
          <button class="btn btn-primary" id="mtRefresh" data-i18n="mailtodo.refresh"></button>
          <button class="btn" id="mtExtract" data-i18n="mailtodo.extract"></button>
          <button class="btn" id="mtRules" data-i18n="mailtodo.rules"></button>
          <label class="mt-inline"><span data-i18n="mailtodo.days"></span>
            <select class="inp" id="mtDaysSel">
              <option value="7">7</option>
              <option value="14" selected>14</option>
              <option value="30">30</option>
            </select></label>
          <span class="mail-meta" id="mtMeta"></span>
        </div>
        <div class="mail-error" id="mtWarn" hidden></div>
      </div>
      <div class="card" id="mtRulePanel" hidden>
        <div id="mtRuleList"></div>
        <div class="mail-toolbar">
          <button class="btn" id="mtRuleAdd" data-i18n="mailtodo.rule_add"></button>
          <button class="btn" id="mtRuleExport" data-i18n="mailtodo.rule_export"></button>
          <button class="btn" id="mtRuleImport" data-i18n="mailtodo.rule_import"></button>
          <button class="btn btn-primary" id="mtRuleSave" data-i18n="mailtodo.rule_save"></button>
        </div>
        <div class="mail-meta" id="mtRuleHint"></div>
      </div>
      <div class="card">
        <h3 data-i18n="mailtodo.todo_title"></h3>
        <div id="mtTodo"></div>
      </div>
      <div class="card">
        <h3 data-i18n="mailtodo.sched_title"></h3>
        <div id="mtSchedule"></div>
      </div>
    </div>
```

CSS 不需要改：`app.css` 里没有任何 `#page-*` 选择器，页面显隐靠 `.page[hidden]`。

- [ ] **Step 3: 加 i18n 键（zh / en 各一遍）**

zh 块 —— `nav.mailtodo` 插在 `frontend/i18n.js:12` 的 `"nav.mail": "邮件",` **之后**；页面键追加在 zh 块末尾（`:425` 的 `"library.warn.year_narrowed"` 之后）。

```js
    "nav.mailtodo": "邮件待办",
```

```js
    "mailtodo.refresh": "刷新分类",
    "mailtodo.extract": "AI 抽取待办与日期",
    "mailtodo.rules": "分类规则…",
    "mailtodo.days": "统计范围（天）",
    "mailtodo.todo_title": "待办清单",
    "mailtodo.sched_title": "时间表",
    "mailtodo.empty": "最近 {n} 天没有匹配的邮件",
    "mailtodo.count": "{n} 封邮件 · {m} 条待办",
    "mailtodo.unmatched": "未命中",
    "mailtodo.load_failed": "分类失败：",
    "mailtodo.extract_failed": "AI 抽取失败：",
    "mailtodo.extracting": "正在读取正文并抽取…",
    "mailtodo.body_reading": "正在读取正文…",
    "mailtodo.need_classify": "请先点「刷新分类」",
    "mailtodo.extract_none": "这次没有抽出待办或日期",
    "mailtodo.rule_add": "新增规则",
    "mailtodo.rule_export": "导出 JSON",
    "mailtodo.rule_import": "导入 JSON",
    "mailtodo.rule_save": "保存规则",
    "mailtodo.rule_name": "名称",
    "mailtodo.rule_senders": "发件人（每行一个，可只写 @域名）",
    "mailtodo.rule_keywords": "关键词（每行一个）",
    "mailtodo.rule_body": "连正文一起搜（较慢）",
    "mailtodo.rule_del": "删除",
    "mailtodo.rule_saved": "已保存 {n} 条规则",
    "mailtodo.rule_bad": "每条规则要有名称，且发件人、关键词至少填一个",
    "mailtodo.rule_import_failed": "导入失败：不是规则数组",
    "mailtodo.warn.bad_rule": "有规则格式不对（缺 id 或 id 重复），已忽略",
    "mailtodo.warn.empty_rule": "有规则既没填发件人也没填关键词，会匹配全部邮件，已忽略",
    "mailtodo.warn.blank_item": "有规则里的发件人/关键词是空白，已忽略",
    "mailtodo.warn.ai_unusable": "AI 返回的结构不可用，这次没抽出待办",
    "mailtodo.warn.ai_dropped": "AI 有 {n} 条结果指向了不存在的邮件，已丢弃",
    "mailtodo.warn.ids_missing": "有 {n} 封邮件已不在当前时间窗口内，未抽取",
```

en 块 —— `nav.mailtodo` 插在 `frontend/i18n.js:437` 的 `"nav.mail": "Mail",` **之后**；页面键追加在 en 块末尾（`:850` 的 `"library.warn.year_narrowed"` 之后）。

```js
    "nav.mailtodo": "Mail to-dos",
```

```js
    "mailtodo.refresh": "Refresh",
    "mailtodo.extract": "AI: extract to-dos & dates",
    "mailtodo.rules": "Rules…",
    "mailtodo.days": "Window (days)",
    "mailtodo.todo_title": "To-do list",
    "mailtodo.sched_title": "Schedule",
    "mailtodo.empty": "No matching mail in the last {n} days",
    "mailtodo.count": "{n} messages · {m} to-dos",
    "mailtodo.unmatched": "Unmatched",
    "mailtodo.load_failed": "Classification failed: ",
    "mailtodo.extract_failed": "AI extraction failed: ",
    "mailtodo.extracting": "Reading bodies and extracting…",
    "mailtodo.body_reading": "Reading message bodies…",
    "mailtodo.need_classify": "Run \"Refresh\" first",
    "mailtodo.extract_none": "Nothing was extracted this time",
    "mailtodo.rule_add": "Add rule",
    "mailtodo.rule_export": "Export JSON",
    "mailtodo.rule_import": "Import JSON",
    "mailtodo.rule_save": "Save rules",
    "mailtodo.rule_name": "Name",
    "mailtodo.rule_senders": "Senders (one per line; a bare @domain works)",
    "mailtodo.rule_keywords": "Keywords (one per line)",
    "mailtodo.rule_body": "Also search message bodies (slower)",
    "mailtodo.rule_del": "Delete",
    "mailtodo.rule_saved": "Saved {n} rule(s)",
    "mailtodo.rule_bad": "Every rule needs a name and at least one sender or keyword",
    "mailtodo.rule_import_failed": "Import failed: not an array of rules",
    "mailtodo.warn.bad_rule": "Some rules were malformed (missing or duplicate id) and were ignored",
    "mailtodo.warn.empty_rule": "A rule with no senders and no keywords would match every mail — ignored",
    "mailtodo.warn.blank_item": "Some senders/keywords were blank and were ignored",
    "mailtodo.warn.ai_unusable": "The AI returned an unusable structure; nothing was extracted",
    "mailtodo.warn.ai_dropped": "{n} AI result(s) pointed at nonexistent mail and were dropped",
    "mailtodo.warn.ids_missing": "{n} message(s) are no longer in the current window and were not extracted",
```

- [ ] **Step 4: 加 `applyLang()` 重渲钩子**

`frontend/i18n.js:896` 的 `if (typeof renderMail === "function") renderMail();` **之后**：

```js
  if (typeof renderMailTodo === "function") renderMailTodo();
```

- [ ] **Step 5: 声明状态（必须在 `app.js:2459` 那次顶层 `applyLang()` 之前）**

在 `frontend/app.js:2425` 的 `let mailState = ...` **之后**、`:2459` 的 `applyLang();` **之前**插入：

```js
/* 邮件规则分类页的状态。**声明在这里、而不是文件末尾那段页面代码里**：与上面的
   `mailState` 同一个道理 —— 本文件顶层的 `applyLang()`（就在下面几行）会经 i18n.js
   的钩子调用 renderMailTodo()，而它要读这些顶层 `let`。压在文件末尾就是
   「读到还在 TDZ 的 let」⇒ **整页白屏**，而 `typeof` 守卫照旧为真、`node --check` 也查不出。
   判据是「这个函数读了哪些顶层 let，那些声明在**最早**调用时点之前吗」—— 不只是
   **新增**状态会踩：给既有函数加一个更早的调用时点同样致命。 */
/* 窗口天数。**`let` 而不是 `const`**：工具条上的天数选择要能改它。
   待办跨周，默认 14 天比邮件页的 7 天长。
   **仍然必须声明在这段早声明区里** —— renderMailTodo() 会读它取空态文案，
   而 renderMailTodo() 在下面几行那次顶层 applyLang() 里就会被调用到。 */
let mtDays = 14;
const MT_RULES_KEY = "sc_mailRules";
const MT_DONE_KEY  = "sc_mailDone";
let mtRules = [];                                 // 规则（localStorage 的镜像）
let mtState = { mails: [], labels: {}, loading: false, error: "", warnings: [],
                items: {}, extracting: false, extractWarnings: [], missing: 0 };
```

- [ ] **Step 6: 加两个函数（文件末尾、邮件那段之后）**

```js
/* ---------- 邮件规则分类（待办 / 时间表） ---------- */
/* 页面状态 `mtState` / `mtRules` 声明在文件上部（`applyLang()` 那一段、`mailState` 旁边）
   —— 理由同上：applyLang() 会经 i18n.js 的钩子调用 renderMailTodo()。 */

function initMailTodoTab(){
  if(mtState.mails.length || mtState.loading){ renderMailTodo(); return; }
  return loadMailTodo();
}

function renderMailTodo(){
  const box = $("mtTodo");
  if(!box) return;
  const meta = $("mtMeta");
  if(meta){
    /* 错误卡与计数说的是两件事，同时出现就是骗人 —— 与 renderMail 同一条纪律：
       载入失败时 mails 仍是上一次的结果，于是「共 5 封」会挂在错误卡旁边。 */
    meta.textContent = (mtState.loading || mtState.error || !mtState.mails.length) ? ""
      : t("mailtodo.count", { n: mtState.mails.length, m: mtTodoCount() });
  }
  renderMailTodoWarnings();
  const sched = $("mtSchedule");
  if(sched) sched.innerHTML = mtScheduleHtml();
  if(mtState.error){
    box.innerHTML = `<div class="card mail-error">${esc(mtState.error)}</div>`;
    return;
  }
  if(mtState.loading){
    box.innerHTML = `<div class="card mail-empty">…</div>`;
    return;
  }
  if(!mtState.mails.length){
    box.innerHTML = `<div class="card mail-empty">${esc(t("mailtodo.empty", { n: mtDays }))}</div>`;
    return;
  }
  box.innerHTML = mtTodoHtml();
}

function mtTodoCount(){ return 0; }              // Task 8 填真值

function renderMailTodoWarnings(){
  const el = $("mtWarn");
  if(!el) return;
  const codes = [...(mtState.warnings || []), ...(mtState.extractWarnings || [])];
  const msgs = codes.map(w => t("mailtodo.warn." + w, { n: mtState.missing }));
  el.hidden = !msgs.length;
  el.textContent = msgs.join(" · ");
}

function mtTodoHtml(){                           // Task 8 填真值
  return `<div class="card mail-empty">${esc(t("mailtodo.need_classify"))}</div>`;
}

function mtScheduleHtml(){                       // Task 9 填真值
  return "";
}

async function loadMailTodo(){                   // Task 8 接后端
  return true;
}

/* 天数选择：改了立刻重算。**坏值回落到 14**，绝不把 NaN/空串原样传给后端
   （后端 `days: int` 收到 NaN 会抛 422，而 422 体是合法 JSON、前端那条 catch 拦不到）。 */
$("mtDaysSel").addEventListener("change", e => {
  const n = Number(e.target.value);
  mtDays = Number.isFinite(n) && n > 0 ? n : 14;
  loadMailTodo();
});
```

> 占位函数共 **4 个**（`mtTodoCount` / `mtTodoHtml` / `mtScheduleHtml` / `loadMailTodo`），后续任务**逐个替换**，不是追加。

- [ ] **Step 7: 三个自检全跑，确认接线没错**

Run:
```bash
node tools/check_dom_ids.mjs
node tools/check_i18n_keys.mjs
node tools/check_mail_frontend.mjs
```
Expected:
- `check_dom_ids.mjs`：`✓ 所有 $("id") 引用都能在 index.html 中找到`
- `check_i18n_keys.mjs`：**会红** —— 新增的 `mailtodo.warn.*` 判为死键（它们只经 `t("mailtodo.warn." + w)` 引用）。这是**预期内的红**，Task 10 把它们加进 `DYNAMIC_PREFIXES` 才好。**其余失败项（双语键集不一致、悬空引用）必须为零**；若还有别的红，先修掉再往下。
- `check_mail_frontend.mjs`：`✓` 全绿（本任务没碰邮件页，它必须一条不红 —— 若红了说明撞到了既有契约）

- [ ] **Step 8: 手动验一次「不白屏」**

Run: `.venv/bin/python run_app.py --browser`
Expected: 侧栏出现「邮件待办」；点它能切进去、看到工具条与两块空壳；**浏览器控制台无报错**；把语言切到 English 再切回来，两块标题与按钮文案跟着变（证明 `applyLang()` 钩子接上了）。验完关掉进程。

- [ ] **Step 9: 提交**

```bash
git add frontend/shell.js frontend/index.html frontend/i18n.js frontend/app.js
git commit -m "feat(mailtodo): 新页接入壳层与空壳渲染"
```

---

### Task 7: 规则编辑器

**Files:**
- Modify: `frontend/app.js`（在 Task 6 那段里追加）
- Test: `tools/check_mailtodo_frontend.mjs`（Task 10 建；本任务先手动验）

**Interfaces:**
- Consumes: Task 6 的 `mtRules` / `MT_RULES_KEY`。
- Produces:
  - `mtLoadRules()` / `mtSaveRules(rules)` —— 读写 localStorage，**解析失败回空数组**（坏 JSON 不能炸页面）
  - `mtRuleValid(r)` —— 名称为空、或两个匹配列表都空 ⇒ `false`
  - `mtRenderRules()` / `mtReadRulesFromPanel()` —— 渲染面板、把面板内容读回 `mtRules`
  - 规则形状与 Task 2 的 `validate_rules` 输入**逐字一致**：`{id, name, senders: [str], keywords: [str], search_body: bool}`

- [ ] **Step 1: 写实现**

追加到 Task 6 那段之后：

```js
/* ---- 规则：localStorage <-> 编辑器 ---- */

/* 读规则。**解析失败一律回空数组**：localStorage 里的坏 JSON 不能让整页炸掉，
   那与「规则丢了」是同一件事，而且用户可以重新导入。 */
function mtLoadRules(){
  try{
    const raw = localStorage.getItem(MT_RULES_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  }catch(e){
    return [];
  }
}

function mtSaveRules(rules){
  localStorage.setItem(MT_RULES_KEY, JSON.stringify(rules));
}

/* 名称必填，且发件人 / 关键词至少一个非空。两者都空 = 匹配**全部**邮件
   （子串匹配下空串被任何串包含），后端也会丢弃并报 empty_rule —— 但**这里就得拦住**：
   让用户存下一条注定被丢的规则，是最糟的那种「看起来正常」。 */
function mtRuleValid(r){
  if(!r || !String(r.name || "").trim()) return false;
  const has = xs => Array.isArray(xs) && xs.some(x => String(x || "").trim());
  return has(r.senders) || has(r.keywords);
}

function mtNewRule(){
  return { id: "r-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 7),
           name: "", senders: [], keywords: [], search_body: false };
}

/* 一个多行文本框 <-> 一个字符串数组。读写都用它，免得两侧的空行口径分家。 */
function mtLinesToArr(v){ return String(v || "").split("\n").map(s => s.trim()).filter(Boolean); }
function mtArrToLines(a){ return (Array.isArray(a) ? a : []).join("\n"); }

function mtRenderRules(){
  const box = $("mtRuleList");
  if(!box) return;
  box.innerHTML = mtRules.map((r, i) => `
    <div class="card mt-rule" data-ri="${i}">
      <label class="field"><span>${esc(t("mailtodo.rule_name"))}</span>
        <input class="inp" data-f="name" value="${escAttr(r.name || "")}"></label>
      <label class="field"><span>${esc(t("mailtodo.rule_senders"))}</span>
        <textarea class="inp" data-f="senders" rows="2">${esc(mtArrToLines(r.senders))}</textarea></label>
      <label class="field"><span>${esc(t("mailtodo.rule_keywords"))}</span>
        <textarea class="inp" data-f="keywords" rows="2">${esc(mtArrToLines(r.keywords))}</textarea></label>
      <label class="field mt-inline"><input type="checkbox" data-f="search_body"${r.search_body ? " checked" : ""}>
        <span>${esc(t("mailtodo.rule_body"))}</span></label>
      <button class="btn" data-del="${i}">${esc(t("mailtodo.rule_del"))}</button>
    </div>`).join("");
  const hint = $("mtRuleHint");
  if(hint) hint.textContent = mtRules.every(mtRuleValid) ? "" : t("mailtodo.rule_bad");
  const save = $("mtRuleSave");
  if(save) save.disabled = !mtRules.every(mtRuleValid);   // 空规则**存不下**
}

/* 从面板读回规则（保存与增删前都要走这一步，别让编辑中的内容被静默丢弃）。 */
function mtReadRulesFromPanel(){
  $$("#mtRuleList .mt-rule").forEach(el => {
    const i = Number(el.dataset.ri);
    if(!mtRules[i]) return;
    const val = f => { const n = el.querySelector(`[data-f="${f}"]`); return n ? n.value : ""; };
    mtRules[i].name = val("name").trim();
    mtRules[i].senders = mtLinesToArr(val("senders"));
    mtRules[i].keywords = mtLinesToArr(val("keywords"));
    const cb = el.querySelector('[data-f="search_body"]');
    mtRules[i].search_body = !!(cb && cb.checked);
  });
  return mtRules;
}

$("mtRules").addEventListener("click", () => {
  const p = $("mtRulePanel");
  p.hidden = !p.hidden;
  if(!p.hidden) mtRenderRules();
});

$("mtRuleAdd").addEventListener("click", () => {
  mtReadRulesFromPanel();
  mtRules.push(mtNewRule());
  mtRenderRules();
});

$("mtRuleList").addEventListener("click", e => {
  const b = e.target.closest("[data-del]");
  if(!b) return;
  mtReadRulesFromPanel();
  mtRules.splice(Number(b.dataset.del), 1);
  mtRenderRules();
});

$("mtRuleSave").addEventListener("click", () => {
  mtReadRulesFromPanel();
  if(!mtRules.every(mtRuleValid)){ mtRenderRules(); setStatus(t("mailtodo.rule_bad"), "err"); return; }
  mtSaveRules(mtRules);
  setStatus(t("mailtodo.rule_saved", { n: mtRules.length }), "ok");
  loadMailTodo();                       // 规则改了立刻重算分类
});

/* 导出 / 导入：给「规则在 localStorage、换浏览器就没了」兜底（spec §4）。 */
$("mtRuleExport").addEventListener("click", () => {
  mtReadRulesFromPanel();
  const blob = new Blob([JSON.stringify(mtRules, null, 2)], {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "mail-rules.json";
  a.click();
  URL.revokeObjectURL(a.href);
});

$("mtRuleImport").addEventListener("click", () => {
  const inp = document.createElement("input");
  inp.type = "file"; inp.accept = "application/json,.json";
  inp.addEventListener("change", () => {
    const f = inp.files && inp.files[0];
    if(!f) return;
    const fr = new FileReader();
    fr.addEventListener("load", () => {
      let arr = null;
      try{ arr = JSON.parse(String(fr.result)); }catch(e){ arr = null; }
      /* 不是数组就**明确报错**，不静默吞掉 —— 导错文件却看起来没反应最气人。 */
      if(!Array.isArray(arr)){ setStatus(t("mailtodo.rule_import_failed"), "err"); return; }
      const rid = x => (x && typeof x.id === "string") ? x.id : "";
      /* 合并而不是覆盖：导入的是「加几条」，把用户手写的规则顶掉是破坏性的。
         同 id 视作同一条，用导入的那份覆盖（本地 id 由 mtNewRule 生成，不会撞）。 */
      const byId = new Map(mtRules.map(r => [r.id, r]));
      arr.forEach(r => { if(rid(r)) byId.set(r.id, r); });
      mtRules = [...byId.values()];
      mtSaveRules(mtRules);
      mtRenderRules();
    });
    fr.readAsText(f);
  });
  inp.click();
});
```

> `escAttr` 若该文件里没有，就用既有的 HTML 转义函数（落笔前核一遍名字）；`esc` / `setStatus` / `$$` / `$` / `t` 都是既有函数。

- [ ] **Step 2: 手动验**

Run: `.venv/bin/python run_app.py --browser`
Expected:
1. 点「分类规则…」展开面板；点「新增规则」出现一条空规则；**「保存规则」此时是禁用的**，且提示行显示「每条规则要有名称，且发件人、关键词至少填一个」。
2. 填名称 + 一行关键词 ⇒ 保存按钮变可用；点保存 ⇒ 状态条「已保存 1 条规则」。
3. 刷新页面（F5）⇒ 规则还在（localStorage 生效）。
4. 「导出 JSON」下载出 `mail-rules.json`；删掉规则、再「导入 JSON」选刚才那个文件 ⇒ 规则回来。

- [ ] **Step 3: 提交**

```bash
git add frontend/app.js
git commit -m "feat(mailtodo): 规则编辑器（localStorage + 导出/导入 + 空规则禁用）"
```

---

### Task 8: 分类请求 + 待办清单（多标签 + 稳定键 + 勾选）

**Files:**
- Modify: `frontend/app.js`（替换 Task 6 的三个占位：`mtTodoCount` / `mtTodoHtml` / `loadMailTodo`）

**Interfaces:**
- Consumes: `POST /api/mail/classify`（Task 3）、`mtRules`（Task 7）
- Produces:
  - `mtFingerprint(m) -> str` —— 邮件指纹（`主题 + 发件人 + 收信时间`）
  - `mtDoneKey(fp, i) -> str` —— 勾选状态的键 = 指纹 + 条目下标
  - `mtDoneSet()` / `mtSetDone(key, on)` —— 读写 localStorage（键 `sc_mailDone`）
  - `loadMailTodo()` / `mtTodoHtml()` / `mtTodoCount()` —— 真实现

**两条不可退让的约束：**

1. **勾选状态的键必须是指纹 + 条目下标，不能是 IMAP UID。** UID 会随 UIDVALIDITY 变，
   服务器重整后缓存与勾选状态**整批失效**。改成 UID 是一个必须能被变异杀掉的错误。
2. **条目下标：同一封邮件的不同待办要能独立勾选。** 用邮件指纹单独当键会让勾掉一条连带勾掉另一条。

- [ ] **Step 1: 写实现（替换 Task 6 的三个占位函数）**

```js
/* 邮件指纹：主题 + 发件人 + 收信时间 拼成的**稳定字符串键**。
   **不是密码学哈希** —— 它只当 localStorage 的键，不需要抗碰撞，也不需要
   `crypto.subtle`（那是异步的、还要 secure context，为一把键引入它不划算）。
   **更不能用 IMAP UID**：UID 会随 UIDVALIDITY 变，服务器重整后缓存与勾选状态会
   整批失效 —— 用户看到的是「我勾过的全没了」。 */
/* 分隔符**必须非空、且不会出现在字段内容里**。写成空串是最隐蔽的错法：
   `{主题:"ab", 发件人:"c"}` 与 `{主题:"a", 发件人:"bc"}` 会拼出同一个键，
   勾选状态于是挂到**另一封邮件**上 —— 界面上看不出任何异常。
   这里用的两个控制字符在邮件头里不可能出现。
   **必须写成转义序列**：把控制字符本身落进源码，它在编辑器与 diff 里都不可见、
   grep 不到，任何一次复制粘贴或工具链改写都可能把它吃掉。 */
const MT_SEP1 = "\u0001", MT_SEP2 = "\u0002";
function mtFingerprint(m){
  return [String(m.subject || ""), String(m.sender || ""), String(m.received || "")].join(MT_SEP1);
}
/* 勾选状态的键 = 邮件指纹 + **条目下标**。同一封邮件抽出两条待办时，
   勾掉一条**不能**连带勾掉另一条 —— 只用指纹当键就会犯这个错。 */
function mtDoneKey(fp, i){ return fp + MT_SEP2 + String(i); }

function mtDoneSet(){
  try{
    const raw = localStorage.getItem(MT_DONE_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(arr) ? arr : []);
  }catch(e){ return new Set(); }
}
function mtSetDone(key, on){
  const s = mtDoneSet();
  if(on) s.add(key); else s.delete(key);
  localStorage.setItem(MT_DONE_KEY, JSON.stringify([...s]));
}

async function loadMailTodo(){
  if(mtState.loading) return false;
  mtState.loading = true; mtState.error = ""; mtState.warnings = [];
  renderMailTodo();
  /* 规则里只要有 search_body，后端就要给**所有**邮件取正文（30 封约 3 秒）。
     先说清楚在等什么，否则用户以为卡死了。 */
  const reading = mtRules.some(r => r.search_body === true);
  if(reading && $("mtMeta")) $("mtMeta").textContent = t("mailtodo.body_reading");
  const r = await api("mail/classify", { days: mtDays, rules: mtRules });
  mtState.loading = false;
  if(r.ok !== true){
    mtState.error = (r.error || t("mailtodo.load_failed"));
    renderMailTodo();
    return false;
  }
  mtState.mails = r.mails || [];
  mtState.labels = {};
  mtState.mails.forEach(m => { mtState.labels[m.id] = m.labels || []; });
  mtState.warnings = r.warnings || [];
  /* `items` 的键换成**指纹**：抽取那一趟回来的是 id，而下标只在那一趟有效。
     指纹跨刷新稳定，拿它当键勾选状态才不会挂到别的邮件上。 */
  const fpById = {};
  mtState.mails.forEach(m => { fpById[m.id] = mtFingerprint(m); });
  const remapped = {};
  Object.entries(mtState.items || {}).forEach(([mid, v]) => {
    if(fpById[mid]) remapped[fpById[mid]] = v;
  });
  mtState.items = remapped;
  renderMailTodo();
  return true;
}

function mtTodoCount(){
  return Object.values(mtState.items || {})
    .reduce((n, it) => n + ((it.reminders || []).length), 0);
}

/* 待办清单：按分类分组。**一封邮件命中 N 个分类，就在 N 个组下各出现一次**，
   但勾选状态共用同一把键（勾一处、各处同步）—— 见 spec §4「多标签邮件在清单里怎么摆」。
   真正的伤害（同一件事被当三件事、勾了一处忘另一处）由共享键消掉；重复的**行**
   只是分组视图的必然结果。 */
function mtTodoHtml(){
  const groups = new Map();                     // 分类名 -> [行]
  const done = mtDoneSet();
  mtState.mails.forEach(m => {
    const fp = mtFingerprint(m);
    const it = mtState.items[fp];
    const rows = (it && it.reminders) || [];
    if(!rows.length) return;                    // 没有待办的邮件不进清单（它可能只在时间表里）
    const labels = mtState.labels[m.id] || [];
    const names = labels.length
      ? labels.map(rid => (mtRules.find(r => r.id === rid) || {}).name || rid)
      : [t("mailtodo.unmatched")];
    names.forEach(n => {
      if(!groups.has(n)) groups.set(n, []);
      rows.forEach((rem, i) => {
        const key = mtDoneKey(fp, i);
        groups.get(n).push(`
          <div class="mt-row">
            <label class="mt-inline">
              <input type="checkbox" data-done="${escAttr(key)}"${done.has(key) ? " checked" : ""}>
              <span>${esc(rem.title || it.summary || "")}</span>
            </label>
            <em>${esc(mtFmtWhen(rem.due_date))}</em>
            <em class="muted">${esc(mtWhoSent(m))}</em>
          </div>`);
      });
    });
  });
  if(!groups.size) return `<div class="card mail-empty">${esc(t("mailtodo.need_classify"))}</div>`;
  return [...groups.entries()].map(([name, rows]) =>
    `<div class="card"><h4>${esc(name)}</h4>${rows.join("")}</div>`).join("");
}

function mtWhoSent(m){
  /* 显示名优先（与邮件页一致）；地址**不上屏** —— 它只用于匹配。 */
  return String(m.sender || m.sender_addr || "");
}

function mtFmtWhen(iso){
  if(!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : d.toLocaleString();
}

/* 勾选：事件委托。读**行上写的键**，绝不按下标或标题反推 —— 那类错位是静默的。 */
$("mtTodo").addEventListener("change", e => {
  const cb = e.target.closest("[data-done]");
  if(!cb) return;
  mtSetDone(cb.dataset.done, cb.checked);
  renderMailTodo();                             // 多标签下同一把键的其它行要同步
});

$("mtRefresh").addEventListener("click", () => {
  withBusy(t("mailtodo.refresh"), $("mtRefresh"), loadMailTodo);
});
```

- [ ] **Step 2: 手动验（用真实邮箱，只读）**

Run: `.venv/bin/python run_app.py --browser`
Expected:
1. 编辑器里建一条「李教授」规则（填一个**真实教授的邮箱地址**），刷新分类 ⇒ 这位教授的信被归到该组。
2. 再建一条关键词规则（如「实验报告」），让**同一封邮件同时命中两条** ⇒ 它在该邮件在两个组下**各出现一次**；勾其中一个组下的复选框 ⇒ **另一个组下同一行也跟着变勾**。
3. F5 刷新 ⇒ 勾选状态还在。
4. 建一条**只在正文里出现**的关键词规则 + 勾「连正文一起搜」⇒ 状态行先显示「正在读取正文…」，随后能命中。
5. 建一条空规则（只填名字）⇒ 保存按钮**禁用**。

- [ ] **Step 3: 跑邮件页自检，确认没碰到既有契约**

Run: `node tools/check_mail_frontend.mjs`
Expected: `✓` 全绿（`renderMail` 那条链一个字没动）

- [ ] **Step 4: 提交**

```bash
git add frontend/app.js
git commit -m "feat(mailtodo): 待办清单（多标签 + 指纹勾选键）"
```

---

### Task 9: 时间表（AI 抽取 + 合并排序）

**Files:**
- Modify: `frontend/app.js`（替换 Task 6 的 `mtScheduleHtml` 占位，并追加 `mtExtract`）

**Interfaces:**
- Consumes: `POST /api/mail/extract`（Task 5）、`mtState.mails` / `mtState.labels`（Task 8）、`mtFingerprint`
- Produces: `mtExtract()`、`mtScheduleRows()`、`mtScheduleHtml()`

**两块各自放什么（沿用 spec §4）：**
- **待办清单** = `reminders`（Task 8 已做）
- **时间表** = `calendar_events`（按 `start`）与 `reminders`（按 `due_date`）**合并**后按时间从近到远排。**只有抽得出日期的条目能进**；抽不出日期的只留在上方清单里，不会在时间表里凭空占位。

- [ ] **Step 1: 写实现**

```js
/* AI 抽取：**按需触发**（一个按钮），不自动跑 —— 否则每开一次页烧一次 token。
   只对**还没抽过**的邮件发请求（`mtState.items` 里已有的跳过）。 */
async function mtExtract(){
  if(mtState.extracting) return;
  if(!mtState.mails.length){ setStatus(t("mailtodo.need_classify"), "err"); return; }
  const s = settings();
  if(!s.llm_base_url || !s.llm_api_key || !s.llm_model){
    setStatus(t("mailtodo.extract_failed") + t("settings.need_llm"), "err");
    return;
  }
  const have = new Set(Object.keys(mtState.items || {}));
  const todo = mtState.mails.filter(m => !have.has(mtFingerprint(m)));
  if(!todo.length){ renderMailTodo(); return; }
  mtState.extracting = true;
  renderMailTodo();
  try{
    const r = await api("mail/extract", {
      llm_base_url: s.llm_base_url, llm_api_key: s.llm_api_key, llm_model: s.llm_model,
      ids: todo.map(m => m.id), days: mtDays, language: lang() });
    if(r.ok !== true){
      setStatus(t("mailtodo.extract_failed") + (r.error || ""), "err");
      return;
    }
    const fpById = {};
    mtState.mails.forEach(m => { fpById[m.id] = mtFingerprint(m); });
    (r.items || []).forEach(it => {
      const fp = fpById[it.id];
      if(fp) mtState.items[fp] = it;             // 键是**指纹**，不是 id（见 Task 8）
    });
    const dropped = Number(r.dropped || 0);
    const missing = Number(r.missing || 0);
    mtState.missing = missing;
    mtState.extractWarnings = (r.warnings || []).slice();
    if(missing && !mtState.extractWarnings.includes("ids_missing")){
      mtState.extractWarnings.push("ids_missing");   // 少给几条还答 ok 是静默丢件
    }
    if(!Object.keys(mtState.items).length && !dropped && !missing){
      setStatus(t("mailtodo.extract_none"), "info");
    }
  }catch(e){
    setStatus(t("mailtodo.extract_failed") + String(e && e.message || e), "err");
  }finally{
    mtState.extracting = false;
    renderMailTodo();
  }
}

/* 时间表：calendar_events（按 start）+ reminders（按 due_date）**合并**后按时间升序。
   **只有抽得出日期的能进** —— 抽不出日期的只留在上方清单，不在这里凭空占位。 */
function mtScheduleRows(){
  const rows = [];
  mtState.mails.forEach(m => {
    const it = mtState.items[mtFingerprint(m)];
    if(!it) return;
    (it.calendar_events || []).forEach(ev => {
      if(!ev.start) return;
      rows.push({ when: ev.start, title: ev.title || it.summary || "", who: mtWhoSent(m) });
    });
    (it.reminders || []).forEach(rem => {
      if(!rem.due_date) return;
      rows.push({ when: rem.due_date, title: rem.title || it.summary || "", who: mtWhoSent(m) });
    });
  });
  /* 解不出的日期排到最后（而不是当成「现在」混进最前面）—— 把坏值排到眼前是骗人。 */
  const at = s => { const d = new Date(s); return isNaN(d) ? Infinity : d.getTime(); };
  return rows.sort((a, b) => at(a.when) - at(b.when));
}

function mtScheduleHtml(){
  const rows = mtScheduleRows();
  if(!rows.length){
    return `<div class="card mail-empty">${esc(t("mailtodo.extract_none"))}</div>`;
  }
  return rows.map(r => `
    <div class="mt-row">
      <b>${esc(mtFmtWhen(r.when))}</b>
      <span>${esc(r.title)}</span>
      <em class="muted">${esc(r.who)}</em>
    </div>`).join("");
}

$("mtExtract").addEventListener("click", () => {
  withBusy(t("mailtodo.extracting"), $("mtExtract"), mtExtract);
});
```

> `settings()` 与语言取值函数（`lang()` 或别的名字）都以该文件**当下的既有写法**为准 —— 落笔前核一遍；`withBusy` / `setStatus` / `api` 用法照 `loadMail` 那段。

- [ ] **Step 2: 手动验**

Run: `.venv/bin/python run_app.py --browser`
Expected:
1. 先在设置页填好 LLM 三项。点「AI 抽取待办与日期」⇒ 遮罩显示「正在读取正文并抽取…」，随后下方「时间表」按日期从近到远列出条目。
2. 再点一次 ⇒ **不再发请求**（已抽过的都跳过了）—— 可在浏览器网络面板确认只有第一次有 `mail/extract`。
3. 找一封**没有明确截止时间**的信 ⇒ 它的待办只出现在上方清单里，**不出现在时间表**。
4. 一封都抽不出时 ⇒ 状态条提示「这次没有抽出待办或日期」，不静默。

- [ ] **Step 3: 提交**

```bash
git add frontend/app.js
git commit -m "feat(mailtodo): 时间表（AI 按需抽取 + 合并排序）"
```

---

### Task 10: 自检收口

**Files:**
- Modify: `tools/check_i18n_keys.mjs:74`
- Create: `tools/check_mailtodo_frontend.mjs`

**Interfaces:**
- Consumes: Task 6-9 的前端实现。
- Produces: `node tools/check_mailtodo_frontend.mjs` 与变异开关 `--mutate-key-to-uid`、`--mutate-drop-index`。

**为什么新开一个自检脚本、而不是并进 `check_mail_frontend.mjs`（spec §4 的「未决」项，此处定案）：**
`check_mail_frontend.mjs` 的 DOM 替身是按**邮件页的 id 集合**装配的，且它的 `fetch` 桩只放行少数几条端点
（`:660` 附近 `if (!(u in ROUTED)) throw`）—— 新页要打 `/api/mail/classify` 与 `/api/mail/extract`，直接并进去
会让那个桩把新页的请求**当错误抛出**。它的变异开关还是一份**逐条实测过红集**的固定清单（`:44-66`），
往里加段会动到那份契约。抽公共壳要先重构一份 1101 行、已被验证的脚本 —— 那是独立的一笔工作，
不该塞进本特性。**所以新开一个，只守这一页最要紧的那条契约。**

- [ ] **Step 1: 把 `mailtodo.warn.` 加进动态家族白名单**

`tools/check_i18n_keys.mjs:74`：

```js
const DYNAMIC_PREFIXES = ["wd.", "library.warn.", "mailtodo.warn."];
```

并更新它上面那段注释（`:70-73`），说明 `mailtodo.warn.` 是同一形态的第三个家族：

```js
/* 动态家族前缀：源码里存在 "wd." 这样的引号字面量，键由它拼出来
   （app.js 的 t("wd."+d)）。这类键无法被字面量扫描判活，只能按前缀整体放行。
   `library.warn.` 是同一形态：app.js 的 t("library.warn."+w) 把后端回的**警告码**
   翻成句子（警告必须是码而不是句子，否则英文界面会冒中文），所以整族按前缀放行。
   `mailtodo.warn.` 是第三个：app.js 的 t("mailtodo.warn."+w, {n}) 同一形态。 */
```

- [ ] **Step 2: 跑 i18n 自检**

Run: `node tools/check_i18n_keys.mjs`
Expected: `✓ I18N.zh / I18N.en 键集一致`、`✓ 被引用的键全部有定义`、`✓ 死键 N 个（基线 21，允许持平或下降）`。
**若死键涨到 21 以上**：说明有键写了没人用 —— 逐条查是哪个，要么用起来要么删掉，**不要上调基线**。

- [ ] **Step 3: 写新页自检**

创建 `tools/check_mailtodo_frontend.mjs`。它的装配照抄 `check_mail_frontend.mjs` 的做法：最小 DOM 替身 + 逐文件 `runInContext`（保持浏览器的 `<script>` 边界，**不拼成一个脚本**）+ `fetch` 桩 + 变异锚点必须恰好命中一次。**夹具全部合成**：邮箱用 RFC 2606 保留域（`s0@example.invalid`），不含任何真实邮件内容或 token。

要守的断言（**最小集，只守承重的那条**）：

| 编号 | 断言 |
|---|---|
| S1 | `mtFingerprint` 对同一封邮件两次调用返回相同串（稳定） |
| S2 | 主题/发件人/收信时间任一不同 ⇒ 指纹不同（不是只取其中一个字段） |
| S3 | `mtDoneKey(fp,0) !== mtDoneKey(fp,1)` —— 同一封邮件的两条待办**不能共用键** |
| S4 | 勾选第 0 条、重渲后第 1 条**仍未勾**（勾一条不连带） |
| S5 | 一封邮件命中两个分类 ⇒ 两处各一行；勾其中一处 ⇒ **两处都变勾**（共享键） |
| S6 | 抽不出日期的 reminder **不进** `mtScheduleRows()` |
| S7 | `mtScheduleRows()` 的结果按时间升序 |
| S8 | `mtLoadRules()` 在 localStorage 是坏 JSON 时返回 `[]`、**不抛** |
| S9 | 空规则（名称为空 / 两个列表都空）⇒ `mtRuleValid` 为 `false` |

变异开关：

- `--mutate-key-to-uid` —— 把勾选键从 `mtDoneKey(mtFingerprint(m), i)` 换成 `mtDoneKey(m.id, i)` 那一类「用 UID 当键」的写法。
  **这是本页最该被杀掉的变异**：UID 随 UIDVALIDITY 变，勾选状态会在服务器重整后整批失效。
- `--mutate-drop-index` —— `mtDoneKey(fp, i)` 去掉下标（`return fp`）。应杀 S3 S4。

锚点必须**恰好命中一次**，命中 0 次或多次、或替换后与原文一字不差，一律直接抛错退出（照 `check_mail_frontend.mjs` 里既有的那套写法）。

- [ ] **Step 4: 跑新自检与两个变异**

Run:
```bash
node tools/check_mailtodo_frontend.mjs
node tools/check_mailtodo_frontend.mjs --mutate-key-to-uid
node tools/check_mailtodo_frontend.mjs --mutate-drop-index
```
Expected: 第一条全绿（`✓`）；**后两条必须红**，且红的正好是上面表里对应的那几条。若变异下照绿，说明那些断言**不承重**，回去改断言而不是改变异。

- [ ] **Step 5: 三个自检 + 全量后端测试一起跑**

Run:
```bash
node tools/check_dom_ids.mjs
node tools/check_i18n_keys.mjs
node tools/check_mail_frontend.mjs
node tools/check_mailtodo_frontend.mjs
.venv/bin/python -m pytest -q
```
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add tools/check_i18n_keys.mjs tools/check_mailtodo_frontend.mjs
git commit -m "test(mailtodo): 新页自检（勾选键必须是指纹而非 UID）+ i18n 动态前缀"
```

---

## 完成标准

- 后端：`.venv/bin/python -m pytest -q` 全绿。
- 前端：四个自检脚本全绿；`--mutate-key-to-uid` 与 `--mutate-drop-index` 都能杀出红。
- 端到端（真实邮箱，只读）：建一条教授邮箱规则 ⇒ 分类正确；同一封命中两类的邮件在两处各出现一次、勾一处两处同步；F5 后勾选还在；AI 抽取按需触发、第二次点不重复请求。
- 未触碰：`todo` 页、`schedule` 页、邮件页的 AI 分组视图、`/api/mail/summarize`。

## 已知的规格偏离（实现时已论证，供复审）

1. **`/api/mail/extract` 的 body 多了 `days`**（Task 5）。规格 §3 的列表里没有它；不给窗口后端就只能另定一个固定值，两个端点的窗口会**静默漂移**。
2. **一条规则内 `senders` 与 `keywords` 是「或」**（Task 2）。规格 §2 的表只写了各字段匹配什么，没写两者之间是且还是或。
3. **多标签邮件在清单里各组各出现一次**（Task 8）—— 这一条规格里已显式说明并请用户确认过。
