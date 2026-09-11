# 课程文件：文件夹结构与下载路径

**日期**：2026-09-11
**状态**：待用户评审
**分支**：实施时自当前 `main` 新建（本 spec 本身提交在 `main` 上）

---

## 1. 背景与问题

用户提出三条：

1. 课程文件下载下来时，**不需要再多一层 `course files` 文件夹**。
2. **任意显示文件的页**都要跟 Canvas 内的结构一样，**有文件夹**。
3. 在**单独课程页**（课程中心 → 某门课 → 文件标签），**文件夹结构底下再显示一列「还没下载过的文件」**。

### 问题 1 的根因（已定位）

`course files` **不是本仓代码里的字面量** —— 全仓 grep 无命中。它是 **Canvas 自己的课程根文件夹名**。

`backend/files_downloader.py` 的 `build_folder_path` 从文件所在文件夹一路向上走到 `parent_folder_id` 为 `None` 为止，**把根文件夹的名字也当成一个路径段拼了进去**，于是：

```
下载目录/<课程名>/course files/<Week 1>/<文件>
                  ^^^^^^^^^^^ 这一层
```

注意该函数的 docstring 写的是「**课程根目录返回 `''`**」—— 代码行为与它自己的 docstring 不符。本次改动是把代码改到与 docstring 一致的方向。

### 问题 2、3 的现状

- `backend/canvas_client.py` 的 `get_course_files()` **已经取回了 folders**，返回 `(files, folders)`。
- 但 `backend/main.py` 的 `/api/list_files` **只回传 `files[]`**，folders 仅用于服务端算 `path` 字符串与 `dest_path`。
- 因此前端**拿不到文件夹结构**：`frontend/app.js` 的 `renderFiles`（侧栏文件页）与 `renderHubFiles`（课程中心文件标签）都是**平铺列表**，把 `path` 当一行小字显示。

**结论：要显示文件夹，必须先让后端把 folders 回传。**

---

## 2. 目标与非目标

### 目标

- 下载落盘路径去掉 `course files` 层，变成 `下载目录/<课程名>/<文件夹路径>/<文件名>`。
- 侧栏「文件」页 与 课程中心「文件」标签，都用**可展开的树**呈现 Canvas 的文件夹结构。
- 课程中心「文件」标签在树下方增加「还没下载」勾选清单 + 「下载所选」按钮。

### 非目标（本轮明确不做）

- **不动**课程详情弹层 `#detailModal` 里的 Modules 列表 —— Modules 在 Canvas 里是另一个概念、本就没有文件夹层级，且它「页面 vs 文件二选一」的交互是上一轮刚定的。
- **不动**模块下载 `module_dest()` —— 它按模块名落盘，与 folders 无关。
- **不搬动**用户磁盘上已有的文件（见 D1）。
- **不动** AIMS / Banweb 侧。

---

## 3. 已定决策

| # | 决策 | 出处 |
|---|---|---|
| **D1** | **旧文件新旧路径都认**：`saved` 检测同时看新路径与旧路径（`<课程名>/course files/…`）；**新下载只写新路径**；旧路径已存在时**跳过、不重下、不搬动**。 | 用户选「新旧路径都认（推荐）」 |
| **D2** | 文件夹 UI 用**可展开的树**（缩进 + caret），与侧栏课程卡片现有交互同源。 | 用户选「可展开的树」 |
| **D3** | 课程页「还没下载」清单**可勾选**，配一个「下载所选」按钮。 | 用户选「可直接勾选下载」 |
| **D4** | 文件夹数据由**后端回传 `folders` 数组**（含 `position`），而非前端从 `path` 反推。 | 用户选 A |

### D4 的理由（备查）

前端从 `path` 反推也能建树、且零 API 改动，但**空文件夹会看不见**（Canvas 里建了文件夹还没放文件的情形会凭空消失）、顺序只能按路径字母排。用户的要求是「跟 canvas 内的结构一样」，而**空文件夹与顺序也是结构的一部分**。

### D2 的补充

范围是**侧栏「文件」页 + 课程中心「文件」标签**两处。课程详情弹层的 Modules 列表不算「显示文件的页」（见非目标）。

---

## 4. 后端设计

文件：`backend/files_downloader.py`、`backend/canvas_client.py`、`backend/main.py`

### 4.1 文件夹链与两条路径

把今天「走一遍链、把根也算进去」的单一函数，拆成**一条链 + 两个路径函数**。

```python
def _folder_chain(folder_id, folders: list[dict]) -> list[dict]:
    """从课程根到 folder_id 的文件夹节点链（根在前）。带环保护。

    链上第一个节点若 parent_folder_id 为 None，那它就是课程根文件夹
    —— 即 Canvas 里显示为 "course files" 的那一层。
    """
```

```python
def build_folder_path(folder_id, folders: list[dict]) -> str:
    """新路径：**不含**课程根文件夹。课程根下的文件返回 ''。"""
    chain = _folder_chain(folder_id, folders)
    if chain and chain[0].get("parent_folder_id") is None:
        chain = chain[1:]
    return "/".join(_safe_name(f.get("name", "")) for f in chain)


def build_legacy_folder_path(folder_id, folders: list[dict]) -> str:
    """旧路径：**含**课程根文件夹（复现本轮改动前的行为，供 D1 的兼容检测用）。"""
    return "/".join(_safe_name(f.get("name", "")) for f in _folder_chain(folder_id, folders))
```

**为什么用 `parent_folder_id is None` 而不是比较名字字符串**：硬编码 `"course files"` 会在 Canvas 改文案（或换成别的语言）时**静默失效** —— 表现为那层文件夹又冒出来，且没有任何报错。用结构判据则与文案无关。

**降级行为**：若 `folders` 里找不到任何 `parent_folder_id` 为 `None` 的节点（Canvas 返回形态与预期不符），则 `build_folder_path` 保留全部段 —— 即**退回今天的行为**，表现为「那层还在」，而不是把路径算错丢文件。这条降级要有测试固定住。

### 4.2 `plan_downloads` 同时算两条路径

`files_downloader.py` 的 `plan_downloads`。每条计划项增加一个字段：

```python
{
  "file_id": ..., "display_name": ...,
  "dest_path": str(new_dest),          # 新路径 —— 真正落盘的位置
  "legacy_path": str(legacy_dest),     # 旧路径 —— 只用于「是否已下过」的判定
  "saved": new_dest.exists() or legacy_dest.exists(),
}
```

`saved` 的语义由「新路径存在」改为「**新路径存在 或 旧路径存在**」。

### 4.3 `download_items` 跳过旧路径已有

`files_downloader.py` 的 `download_items`。D1 只说「不重复下载」，但**只改 `saved` 达不到这个效果**：若某文件仅存在于旧路径，用户在 UI 上手动勾选它下载，`download_items` 查的是**新**路径 → 不存在 → 真的再下一份，磁盘上出现新旧两份。

所以 `download_items` 的判定改为：

- 新路径存在 → `skipped`（今天的行为）
- 否则旧路径存在 → `skipped`（新增）
- 都不存在 → 下载

**`legacy_path` 由客户端回传**（前端从 `/api/list_files` 手里就有）。这里**不引入新的信任边界**：

- 新路径本来就已经是客户端回传的（`/api/download_files` 的 `req.items[].dest_path`），今天靠 `confine_dest()`（`files_downloader.py`）重新锚定到 `download_dir` 内来兜底。
- `legacy_path` 走**同一个** `confine_dest()` 处理。它能造成的最坏后果是「误判为已有 → 少下一个文件」，**不会写到下载目录之外**，也不会覆盖任何文件（跳过路径不写盘）。
- 备选方案是后端按课程重新拉一次 folders 自行推算，代价是每个下载请求多若干次 Canvas API 调用。对一个本地单用户工具，不值得。

### 4.4 `canvas_client.get_course_files` 补抓 `position`

`canvas_client.py` 的 folders 列表推导里增加 `"position": fo.get("position")`。

**不赌它一定在**：Canvas 的 folders 返回里 `position` 是否为常见字段，本机令牌已失效（`api("courses")` 返回 HTTP 401）、**无法实测**。因此前端排序规则写成「有 `position` 就按它排，没有就按名字排」，后端只做透传（`fo.get("position")`，取不到即 `None`）。

### 4.5 `/api/list_files` 回传 folders

`main.py` 的 `/api/list_files`。每门课的结果里增加：

```python
"folders": [{"id": fo["id"], "name": fo["name"],
             "parent_folder_id": fo["parent_folder_id"],
             "position": fo.get("position")} for fo in folders],
```

同时该课程 `files[]` 里每项的 `path` 字段语义**随之改变**（从 `course files/Week 1` 变成 `Week 1`）—— 因为它用的就是 `build_folder_path`。前端不再把它当主要展示（树已经表达了结构），但**「还没下载」清单要拿它做来源标注**（如 `Week 1` / 空则显示根目录）。

---

## 5. 前端设计

文件：`frontend/app.js`、`frontend/app.css`、`frontend/i18n.js`、`frontend/index.html`

### 5.1 共用的建树函数（新增）

```js
function buildFileTree(files, folders){ /* → { folders:[node], files:[node] } 递归 */ }
```

- 输入：该课的 `files[]` 与 `folders[]`。
- 输出：以课程为根的树；根层同时含子文件夹与直接挂在根下的文件。
- **排序**：文件夹之间按 `position` 升序（缺失者排在有值者之后、彼此按 `name` 排）；文件之间按 `display_name` 排。
  - 文件不按 Canvas 的 `position` 排 —— 我们没抓文件的 `position`，按名字排是**可预测**的（每次渲染顺序一致），这比「按 API 返回顺序」这种隐含依赖好。这是**有意的简化**，不是遗漏。
- **环保护**：`parent_folder_id` 若形成环，不得死循环。
- 兜底一：`folders` 里找不到某文件的 `folder_id` 时，该文件挂到课程根层（**不丢文件**）。
- 兜底二：**`folders` 可能是 `undefined`**。`/api/list_files` 在 `no_files`（Canvas 返 403）与 `error` 两条分支上只回 `files: []`、**根本不带 `folders` 键**。建树函数必须把 `folders` 缺省当空数组处理、正常返回一棵只有文件的树，**不能抛** —— 否则这两类课程的文件标签会整块白掉（今天它们显示的是一句弱提示）。

### 5.2 侧栏「文件」页 `renderFiles`

课程卡片内由「平铺文件行」改为「文件夹树 + 文件行」：

- 文件夹行：缩进 + caret（沿用现有 `.caret` 交互），**不带勾选框**。
- 文件行：缩进更深，**保留现有 `.fl` 勾选框**与 `file-open` 直链 —— `updateSelectAllBtn()` / `refreshCourseChecks()` 仍按 `.fl` 统计，不受影响。
  - **文件夹行不得带 `.fl`**，否则全选/统计会把文件夹算成文件。
  - 勾选默认值**不用新写**：`app.js:1265` 今天就是 `${f.saved?"":"checked"}` —— 未保存的默认勾上、已保存的默认不勾。`saved` 一旦按 4.2 改成双路径判定，验收第 2 条「旧路径已下过的默认不勾」自动成立。
- 展开状态新增 `expandedFolders`（`Set<"<courseId>:<folderId>">`），刷新重渲时保留。现有 `expandedFiles`（按课程）继续管课程卡片的展开。

### 5.3 课程中心「文件」标签 `renderHubFiles`

分上下两段：

**上段：文件夹树** —— 与 5.2 同一套渲染，但**不带勾选框**（这一段的文件行仍是今天的行为：`file-open` 直链打开源文件 / Canvas 页面）。

**下段：「还没下载」清单**

- 分隔标题：`还没下载 (N)`；N 为 0 时整段不显示。
- 每条：勾选框 + 文件名 + 来源标注（该文件的 `path`，空则显示根目录文案）。
- 按钮「下载所选」：未勾选任何项时禁用。
- 点击后：收集勾选项的 `{course_id, file_id, dest_path, legacy_path}` + `download_dir`，调**现有的** `/api/download_files`（本来就收这个形状）。
- 完成后重跑 `hubRefreshFiles(cid)` 刷新 `saved` 与清单。

### 5.4 i18n

新增键（zh/en **必须成对**，`tools/check_i18n_keys.mjs` 会拦）：至少

- 「还没下载」标题
- 「下载所选」按钮
- 根目录标注

**注意**：`check_i18n_keys.mjs` 当前基线是**死键 21 个**。新增键若真被引用则死键数不变；若新增后无人引用会被算作死键，导致门禁失败 —— 届时要么真接上，要么显式上调基线并说明理由。

### 5.5 DOM id

新增的 id（若有）要同步 `tools/check_dom_ids.mjs`，该门当前是「HTML 共 124 个 id」。

---

## 6. 测试

**本轮要新增 pytest 用例**（上一轮的「不新增用例」是本轮不继承的约束 —— 本轮引入了新的路径判定逻辑，且它是**会静默出错**的那一类）。

`tests/` 下新增，用**合成的 Canvas 形状**（本机无法调真实 API）：

- `build_folder_path`：根下文件 → `''`；一层子文件夹 → `Week 1`；两层 → `Week 1/Lab`。
- `build_legacy_folder_path`：同输入返回 `course files` 前缀的旧形状（**固定住今天的兼容路径**）。
- **降级**：`folders` 中无 `parent_folder_id is None` 的节点时，`build_folder_path` 退回含根的名字。
- 环保护：`parent_folder_id` 成环时不挂起、不抛。
- `plan_downloads`：`saved` 在「仅新存在」「仅旧存在」「都不存在」「都存在」四种下的取值。
- `download_items`：旧路径存在时记为 `skipped` 且**不调用下载**。
- `path` 字段语义变更后，`list_files` 返回值里不再带 `course files` 前缀。

---

## 7. 风险与代价

| 风险 | 说明 | 处置 |
|---|---|---|
| **路径语义变更是一次性、不可逆的用户可见改动** | 已有的 `saved` 判定依据变了 | D1 的双路径检测覆盖；旧文件保持原样不搬 |
| **Canvas 根文件夹名硬编码** | 会随 Canvas 改文案静默失效 | **不硬编码**，用 `parent_folder_id is None` 结构判据（4.1） |
| **降级行为写错会把文件路径算丢** | 找不到根时若「丢掉第一段」，会把真实文件夹名吃掉 | 降级为**保留全部段**（退回今天行为），并有测试固定（4.1、6） |
| **`position` 字段未经实测** | 令牌失效，无法验证 Canvas 是否返回 | 后端只透传、前端容缺（4.4） |
| **旧路径跳过依赖客户端回传 `legacy_path`** | 引入一个客户端可控的判据 | 复用既有 `confine_dest()` 锚定；最坏后果是少下一个文件，不会写出目录、不会覆盖（4.3） |
| **树形渲染替换平铺列表** | 侧栏文件页的勾选/全选统计依赖 `.fl` | 保留 `.fl` 在文件行上，统计逻辑不改（5.2） |
| **死键基线可能变** | 新增 i18n 键 | 见 5.4 |

---

## 8. 验收

**机器可验**（本轮门禁）：

- `pytest` 全绿，且**新增的用例数可数**
- `node tools/check_i18n_keys.mjs`：键集 zh/en 一致、引用有定义、死键不超过基线
- `node tools/check_dom_ids.mjs`
- 四文件按加载顺序拼接 `node --check`

**需用户人工验收**（子代理无浏览器）：

1. 下载一个此前没下过的文件 → 落盘路径**没有** `course files` 这一层。
2. 一个**此前下过**（在旧路径）的文件 → 显示「已保存」、勾选框默认不勾，手动勾选后下载**不会**产生第二份。
3. 侧栏「文件」页与课程中心「文件」标签都能展开/收起文件夹，结构与 Canvas 网页上看到的一致（含**空文件夹**与**顺序**）。
4. 课程中心「文件」标签下方出现「还没下载 (N)」，勾选后「下载所选」能下下来，完成后该清单条目消失、计数减少。
5. 切中/英，上述新增文案都跟着变。
