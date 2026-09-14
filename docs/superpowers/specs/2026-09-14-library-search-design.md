# 图书馆检索（CityU / JULAC Primo）设计

**日期**：2026-09-14
**状态**：待实施
**可行性验证**：2026-09-14 已对线上实例实测通过（文中标注「实测」的均为真跑出来的数字，不是文档抄的）

## 1. 目标

用户用**任意语言的自然语言**描述要找的文献，系统把它转成**结构化检索式**，在**本馆馆藏**与**外部数据库**两处自动检索，**分两块展示**，每条给出可点的链接。

用户可**先选一个学科领域（area）**收窄范围；**也可不选**，由检索式自行判断。

## 2. 非目标（本阶段明确不做）

- 不做全文下载、不做文献管理（引文导出/EndNote 集成）
- 不做借阅/预约等账户操作
- 不做全量收割或定时批量抓取（见 §9 合规边界）
- 第一阶段不做无头登录（登录整体见 §7 分阶段）

## 3. 背景：后端是 Ex Libris Primo（实测结论）

CityU 图书馆检索后端为 **Ex Libris Primo**，托管在 **JULAC**（香港高校图书馆联盟）。前端自身的公开 REST 端点**无需任何凭据**。

> **本节的参数细节是实测踩出来的，实施时直接照用，不要重新推导。**

### 3.1 端点与参数

```
GET https://julac-cuh.primo.exlibrisgroup.com/primaws/rest/pub/pnxs
```

固定项：`vid=852JULAC_CUH:CUH`、`tab=Everything`、`sort=rank`、`lang=en`、
`blendFacetsSeparately=false`、`getMore=0`、`refEntryActive=false`、`rtaLinks=true`、
`skipDelivery=Y`（本阶段不需要 delivery）、`limit`、`offset`、`q`、`scope`。

三个坑（每一个都实测过）：

| 现象 | 原因 |
|---|---|
| 0 命中 | `vid` 必须写**带冒号的全限定形式** `852JULAC_CUH:CUH`；只写 `852JULAC_CUH` 静默返回 0 |
| 0 命中 | **不要传 `inst` 参数**，传了就 0 |
| HTTP 400 + 空 body | `scope` **必填**；漏掉时不是 JSON 错误页，很难看出原因 |

### 3.2 四个 scope（实测数字，查询词 `transformer architecture`）

| scope | 含义 | 命中 |
|---|---|---|
| `MyInstitution` | 仅本馆馆藏/纸本 | 15,417 |
| `MyInst_and_CI` | 本馆 + Primo Central 中央索引 | 78,932（980 + 77,952） |
| `CentralIndex` | 纯中央索引（文章级） | 77,952 |
| `DiscoveryNetwork` | JULAC 联盟全库 | 80,023（2,071 + 77,952） |

**本设计只用 `MyInstitution`（馆藏块）与 `CentralIndex`（外部数据库块）两个 scope，并发两次请求**，而不是查 `MyInst_and_CI` 再在前端拆分——省一次往返，且天然分块、不会串。

### 3.3 facet 过滤（`qInclude`，不是 `facet`）

- 过滤参数是 **`qInclude`**，反向是 `qExclude`。传 `facet=` **无效**（实测：命中数不变）。
- **`rtype` 的值是复数**：`databases` 对，`database` 返回 0。同理 `books` / `journals` / `articles` / `conference_proceedings` / `dissertations` / `book_chapters` / `preprint`。

已实测可用的过滤：

| 用途 | 参数 | 实测 |
|---|---|---|
| 限定来源数据库 | `qInclude=domain,exact,<数据库名>` | 2,071,906 → 40,609（`Publicly Available Content Database`） |
| 限定资源类型 | `qInclude=rtype,exact,articles` | — |
| 限定数据库记录 | `qInclude=rtype,exact,databases` | 本馆共 **66** 个 |

### 3.4 结果里的关键 facet

`domain`（来源数据库/平台，如 ScienceDirect、ProQuest、Factiva、IngentaConnect、MEDLINE）、
`jtitle`（期刊名）、`topic`、`rtype`、`lang`、`creationdate`、
`tlevel`（`open_access` / `peer_reviewed` / `online_resources`）、
`attribute`（`review_article` / `preprint` / `retracted_publication` / `retraction_notice`）、
`newrecords`（`90 days back` / `30 days back` / `07 days back`）。

`control.sourceid`（如 `jstor_cross`）、`search.sourceid` 给稳定的来源库代码。

**中文检索可用**（实测「深度学习」→ 381 条）。

### 3.5 本阶段拿不到的东西

该端点**不返回 `delivery` 块**——索书号、馆藏位置、在架状态、订阅电子资源全文链接**都拿不到**。`/primaws/rest/priv/...` 前缀的端点与 delivery 均需登录（见 §7）。

## 4. 学科领域（area）

图书馆自带分类，**9 个一级、约 30 个二级，每个有稳定 code**（`comp`=Computer Science、`law`、`busgen`、`econ`、`maths`、`phy`、`psy`、`edu`、`comm`…）。

每个 area 的页面（`ereslist/index.php?type=db&format=subj&subj=comp`）**直接给出该领域的数据库清单**。

> **⚠️ 这套分类没有 API。** `app3.lib.cityu.edu.hk/ereslist/` 是老 PHP 站，实测 `format=json` / `output=json` **都是假的**（照样返回 `text/html`），只能解析 HTML。

**因此 area→数据库 的映射必须抓一次、缓存到本地**，搜索时不重复抓取。这是全设计里唯一脆弱的依赖，**必须与主链路解耦**：缓存缺失或解析失败时，降级为「不做库级收窄」，搜索本身照常工作，**不得整个功能失败**。

## 5. 架构

### 5.1 组件

| 文件 | 职责 | 新增/改动 |
|---|---|---|
| `backend/library_client.py` | Primo 客户端：检索、分页、facet 解析、结果归一化 | 新增 |
| `backend/library_areas.py` | area 分类的抓取、解析、缓存、降级 | 新增 |
| `backend/library_query.py` | 自然语言 → 结构化检索式（走 `llm_client` 的调用方式） | 新增 |
| `backend/library_login.py` | Primo SAML 无头登录 → JWT（**第二阶段**） | 新增 |
| `backend/main.py` | 新端点（见 §5.3） | 改 |
| `frontend/index.html` | 新一级导航项 + `#page-library` | 改 |
| `frontend/app.js` | 图书馆页逻辑 | 改 |
| `frontend/i18n.js` | 新增 key，**zh/en 两侧必须同步** | 改 |
| `frontend/app.css` | 结果双栏样式 | 改 |

### 5.2 数据流

```
① 用户选 area（可选，留空则交给检索式判断）
② 用户输入任意语言的自然语言
③ LLM → 结构化检索式 JSON
④ 编译成 Primo 参数
⑤ 并发两个 scope：MyInstitution（馆藏）/ CentralIndex（外部数据库）
⑥ 归一化 → 前端双栏
```

### 5.3 端点

**第一阶段**

```
POST /api/library/areas   → {ok, areas:[{code,name,parent?}], cached:bool}
POST /api/library/search  → {ok, parsed:{...}, local:{items,count}, external:{items,count}, warnings[]}
```

`/api/library/search` 请求体：`{text, area?, language, limit?, filters?}`。
响应回带 `parsed`，供前端把「我理解你要找的是…」原样显示给用户。

**第二阶段**（形状对齐 `banweb` 那套）

```
POST /api/library/status     → {ok, status: opening|needs_login|logged_in|error}
POST /api/library/login      → 用钥匙串凭据走无头登录
POST /api/library/open_login → 开真窗口手动登录（兜底）
```

## 6. 检索式契约

LLM 的产物是**结构化 JSON，不是 Primo 查询串**。理由：可校验、可原样回显给用户、单字段出错时能降级而不是整个查询崩掉。

```json
{
  "keywords": ["machine learning", "education"],
  "synonyms": ["ML", "learning analytics"],
  "year_from": 2020, "year_to": null,
  "rtype": ["articles", "conference_proceedings"],
  "language": null,
  "open_access_only": false,
  "peer_reviewed_only": true,
  "exclude_retracted": true,
  "areas": ["comp"],
  "explanation": "找 2020 年后的机器学习在教育领域的同行评审论文"
}
```

编译规则：

- `keywords + synonyms` → `q=any,contains,...`
- `rtype` → `qInclude=rtype,exact,<值>`（**复数形式**）
- `areas` → 该 area 的数据库清单 → `qInclude=domain,exact,<库名>,...`（已实测可用）
- `year_from/to` → `qInclude=creationdate,exact,<年>`，逐年一条（`qInclude` 语法已实测；区间形式的专用参数未验，**不作为默认**，验通后可换成区间优化，属性能改进不属功能）

**只在「外部数据库块」生效的过滤**（`tlevel` / `attribute` 是 Primo Central 的 facet，本馆馆藏记录没有这两个维度，套到 `MyInstitution` 上会得到 0 或静默失效）：

- `peer_reviewed_only` → `qInclude=tlevel,exact,peer_reviewed`
- `open_access_only` → `qInclude=tlevel,exact,open_access`
- `exclude_retracted` → `qExclude=attribute,exact,retracted_publication`

**即：这两个 scope 的请求参数不是同一套。** `MyInstitution` 只吃 `q` / `rtype` / `creationdate` / `domain`；`CentralIndex` 全吃。编译函数必须按 scope 分流出两份参数，不能编一份发两处。

**降级**：LLM 调用失败或返回非法 JSON 时，退化为「把用户原文直接当 `q` 的关键词」并置 `warnings`，**不得整个搜索失败**。

## 7. 分阶段：登录是增强，不是主干

**检索整条链零登录**——两块结果、每条的记录页链接，第一阶段即可交付。登录只多拿两样：**全文直链**、**馆藏可用性**（索书号/在架状态）。

**第一阶段**：零登录。自然语言 → 检索式 → 双栏结果 + 每条 **Primo 记录页链接**。全文由用户点进 Primo 自行登录（学校正常用法）。

> **本次实施计划只覆盖第一阶段。** 第二阶段的四项风险没有一项验证过，把它们和已验证的第一阶段写进同一个计划，等于让计划一半的内容建立在猜测上。第二阶段在 §7 的四项风险逐条验掉之后，**另起一份 spec**。

**第二阶段**：复用 `backend/credentials.py` 的**钥匙串**（与 AIMS 同一套 CityU 凭据）走 Primo SAML 登录，换到 **JWT**（存内存、不落盘），用它调 `/primaws/rest/priv/...` 取 delivery。失败退回开真窗口手动登。

> **不能照搬** `banweb.auto_login_from_stored()` 的代码。那是 banweb 自己的 Okta 表单流（`P_WWWLOGIN` 页的 `name=identifier` / `credentials.passcode`）。Primo 是 SAML 重定向 + postMessage 回传 `event.data.jwt`，形状不同，**只能复用凭据存储与「无头 HTTP 走完整流程」这个思路**。

**第二阶段的四项未验证风险**（任一条不成立都要重做或放弃）：

1. JULAC 的 SAML profile 是否接 CityU 同一个 Okta
2. 是否需要 MFA
3. JWT 能调通哪些 `priv` 端点
4. SAML 重定向能否在无头会话里走完

## 8. 测试策略

- **不打真实网络**。测试用**合成的 Primo JSON 形状**做 fixture（本仓既有 `tests/test_canvas_client.py` 就是这个模式）。
- `library_client`：参数拼装（尤其 vid/inst/scope 三个坑、rtype 复数、qInclude 语法）、分页、结果归一化、facet 解析、异常路径。
- `library_areas`：HTML 解析、缓存命中、**抓取失败时必须降级而不是抛错**。
- `library_query`：JSON 解析、**非法/残缺 JSON 的降级路径**、编译成 params 的映射。
- `main.py`：端点契约（成功 / 上游报错 / 降级三态），错误一律 200 + JSON（本仓既有约定，判别字段是 `Content-Type`）。
- **新测试必须做变异检验**（临时改坏实现，确认测试真的失败），本仓既有要求。

## 9. 合规与边界

Primo Central 是**授权内容**。本工具是**个人只读检索**：用户手动触发、结果限量（`limit` 有上限）、**不做定时/批量收割**、不做全量分页拉取。实施时限速，不做并发轰炸。

## 10. 本仓既有约束（实施必须遵守）

- **无构建、无 JS 工具链**：`frontend/index.html:354-357` 四个经典 `<script>`，顺序 `i18n.js → util.js → app.js → shell.js`。
- 新增顶层 `let`/`const` 前先 grep 其余三文件（重复即 `SyntaxError` 白屏）。**顶层 `function` 重名 `node --check` 查不出来**，必须手工 grep。
- i18n：新增 key **必须 zh/en 两侧同步**（当前各 **359** 键，317 个被引用）。死键基线 **21**，不得上升。
- HTML id 基线 **124**（新增 id 没问题，但所有 `$("id")` 必须能解析到）。
- 门禁：`pytest`、`node tools/check_i18n_keys.mjs`、`node tools/check_dom_ids.mjs`、四文件按加载顺序拼接 `node --check`。

## 11. 实施时需现场验证的点

1. **日期区间参数**：`creationdate` 的区间过滤写法（facet 值是年份桶，区间语法未验）。
2. **记录页链接的构造**：从记录的 `recordid` / `@id` 拼 Primo permalink（`discovery/fulldisplay?docid=...&vid=...`）需实测确认格式。
3. **`domain` 过滤的多个库**：`qInclude` 传多个 `domain,exact,` 时的语义（OR / AND）。
4. **分页上限**：`limit` 的最大值与深分页行为。

以上四条都不影响架构，属参数细节，实施第一步验掉即可。
