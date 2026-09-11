# 课程文件：文件夹结构与下载路径 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 下载落盘路径去掉 Canvas 课程根文件夹那一层；侧栏「文件」页与课程中心「文件」标签改为可展开的文件夹树；课程中心文件标签下方增加「还没下载」勾选清单 + 「下载所选」。

**Architecture:** 后端把「一条文件夹链」拆成两个路径函数 —— `build_folder_path`（新，**不含**课程根）与 `build_legacy_folder_path`（旧，**含**课程根，逐字复现改动前行为）。`plan_downloads` 同时算出两条路径，`saved` 与 `download_items` 的跳过判定都改成「新路径 OR 旧路径」，于是老用户磁盘上的旧布局不会被当成没下过。`/api/list_files` 把 `folders[]`（含 `position` 透传）一并回传，前端用它建树。

**Tech Stack:** Python 3 / FastAPI / pytest；前端是**无构建的四段经典 `<script>`**（`i18n.js → util.js → app.js → shell.js`，加载顺序硬性），没有 JS 单测框架。

**Spec:** `docs/superpowers/specs/2026-09-11-course-files-folders-design.md`

## Global Constraints

- **不引入构建步骤、不用 ES 模块。** 四个 `<script>` 共享同一个顶层作用域，加载顺序硬性为 `i18n.js → util.js → app.js → shell.js`。
- **不要在任一文件里新增与其余三个重名的顶层 `let`/`const`** —— 重复的顶层声明会让整页 `SyntaxError` 白屏。新增顶层声明前先 grep 其余三个文件。
- **绝不硬编码字符串 `"course files"`。** 判断课程根文件夹一律用结构判据 `parent_folder_id is None`。
- **不搬动用户磁盘上已有的文件。** 旧路径只用于「是否已下过」的判定，永不做迁移、重命名或删除。
- **不动** `#detailModal` 里的 Modules 列表、**不动** `files_downloader.module_dest()`、**不动** AIMS/Banweb 任何代码。
- **i18n：zh/en 必须成对新增**，`node tools/check_i18n_keys.mjs` 的死键基线是 **21**（允许持平或下降）。
- **`node tools/check_dom_ids.mjs` 基线是 HTML 共 124 个 id** —— 该门检查 JS 里每个 `$("...")` 引用都能在 `index.html` 找到对应 id。**动态渲染出来的元素不要用 `$()` 取**（见 Task 5 的说明）。
- **pytest 基线 228 passed。**
- **转义口径：`esc()` 只转义 `&` `<` `>`；`escAttr()` 才额外转义 `"`。** 任何 HTML **属性位**必须用 `escAttr`，用 `esc` 等于属性逃逸。
- **i18n 悬空引用扫描只认「`t` 紧跟一个引号字面量」这一种形状**，且**它连注释一起读** —— 所以 `t(cond ? "a" : "b")` 里的键查不到，必须写成两条独立的字面量调用；注释里也不能出现 `t` 加引号的字样。
- **后端错误约定**：出错也返回 HTTP 200 + JSON，判别看 `Content-Type === "application/json"`。
- **前端没有单测框架。** 前端任务的运行时验证由**控制方**（有 chrome-devtools MCP）在 dev server `127.0.0.1:8331` 上跑探针完成；实施子代理只负责跑静态门（`node --check` 拼接、i18n 门、DOM id 门）并把输出贴进报告。
- **macOS 没有 `timeout` 命令。** Python 一律用 `.venv/bin/python`。
- **本计划里的行号是 BASE 提交当时的快照，会漂移。** 靠前的任务增删行之后，靠后任务引用的行号会失准（例如 Task 1 在 `tests/test_files_downloader.py` 里把 8 行换成约 40 行，Task 2 引用的「第 61-85 行」随即失效）。**一律按函数名 / 测试名 / id 定位**，行号只用于快速找到大致位置 —— 找不到就按名字搜，别按行号硬数。

---

## 文件结构

| 文件 | 职责 | 本轮改动 |
|---|---|---|
| `backend/files_downloader.py` | 文件夹路径推导 + 下载规划与执行 | 改写路径函数、双路径判定 |
| `backend/canvas_client.py` | Canvas API 客户端 | folders 补抓 `position` |
| `backend/main.py` | FastAPI 端点 | `/api/list_files` 回传 folders；`/api/download_files` 透传 `legacy_path` |
| `frontend/app.js` | 主前端 | 建树/渲染 + 两个页面改用树 + 「还没下载」清单 |
| `frontend/app.css` | 样式 | 文件夹行与课程中心清单的样式 |
| `frontend/i18n.js` | 文案 | 新增 2 个键（**只在 Task 5 加**，理由见该任务） |
| `tests/test_files_downloader.py` | 下载器单测 | **修正 2 个既有用例** + 新增 6 个 |
| `tests/test_main.py` | 端点单测 | 新增 2 个用例 |

---

### Task 1: 后端 —— 文件夹链与新旧两条路径

**Files:**
- Modify: `backend/files_downloader.py:39-47`（`build_folder_path` 及其上方新增两个函数）
- Test: `tests/test_files_downloader.py:41-48`（修正既有用例）、文件末尾新增用例

**Interfaces:**
- Consumes: 无（起点任务）
- Produces:
  - `_folder_chain(folder_id, folders: list[dict]) -> list[dict]` —— 从课程根到 `folder_id` 的节点链，根在前；带环保护
  - `build_folder_path(folder_id, folders: list[dict]) -> str` —— **不含**课程根那一段；根下文件返回 `""`
  - `build_legacy_folder_path(folder_id, folders: list[dict]) -> str` —— **含**课程根那一段（复现改动前行为）

**为什么这两个既有用例必须改（不是「顺手改测试」）：** `tests/test_files_downloader.py` 的 `test_build_folder_path` 与 `test_plan_downloads_path_and_rename` 把**今天的路径形状**当成了期望值（`"Slides/Week 3"`、`.../Slides/Week 3/a.pdf`）。本轮要改的正是这个形状，所以它们**必然**变红。留着不改＝放弃测试；改成新形状＝让它们继续守住新的契约。计划要求在**同一个提交里**改掉，让「改语义」与「改期望」在评审时同时可见。

- [ ] **Step 1: 先确认文件夹链相关名字没有被占用**

Run: `grep -rn "_folder_chain\|build_legacy_folder_path" backend/ tests/`
Expected: 无输出（两个都是新名字）。

- [ ] **Step 2: 写失败测试**

把 `tests/test_files_downloader.py` 中**现有的** `test_build_folder_path`（第 41-48 行）整段替换为：

```python
def test_build_folder_path():
    """新路径不含课程根文件夹那一段（Canvas 里那层 "course files"）。"""
    folders = [
        {"id": 1, "name": "course files", "parent_folder_id": None},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1},
    ]
    assert files_downloader.build_folder_path(2, folders) == "Week 3"
    assert files_downloader.build_folder_path(1, folders) == ""
    assert files_downloader.build_folder_path(None, folders) == ""
    assert files_downloader.build_folder_path(999, folders) == ""


def test_build_folder_path_nested_two_levels():
    folders = [
        {"id": 1, "name": "course files", "parent_folder_id": None},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1},
        {"id": 3, "name": "Lab", "parent_folder_id": 2},
    ]
    assert files_downloader.build_folder_path(3, folders) == "Week 3/Lab"


def test_build_folder_path_degrades_when_no_root_marker():
    """folders 里没有任何 parent_folder_id 为 None 的节点（Canvas 返回形态与预期不符）
    → 退回「保留全部段」＝本轮改动前的行为。宁可多一层，也不把真实文件夹名算丢。"""
    folders = [{"id": 2, "name": "Week 3", "parent_folder_id": 1}]  # 父 1 不在表里
    assert files_downloader.build_folder_path(2, folders) == "Week 3"


def test_build_folder_path_survives_parent_cycle():
    """父链成环不得死循环、不得抛；环被截断，名字不会无限累积。"""
    folders = [
        {"id": 1, "name": "A", "parent_folder_id": 2},
        {"id": 2, "name": "B", "parent_folder_id": 1},
    ]
    p = files_downloader.build_folder_path(1, folders)
    assert isinstance(p, str)
    assert p.count("A") <= 1 and p.count("B") <= 1


def test_build_legacy_folder_path_keeps_root_segment():
    """旧路径必须逐字复现改动前的形状（含课程根那一段）—— 兼容检测全靠它。"""
    folders = [
        {"id": 1, "name": "course files", "parent_folder_id": None},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1},
    ]
    assert files_downloader.build_legacy_folder_path(2, folders) == "course files/Week 3"
    assert files_downloader.build_legacy_folder_path(1, folders) == "course files"
    assert files_downloader.build_legacy_folder_path(999, folders) == ""
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_files_downloader.py -q -k "folder_path"`
Expected: FAIL —— `test_build_folder_path` 报 `assert 'course files/Week 3' == 'Week 3'`；`build_legacy_folder_path` 报 `AttributeError: module ... has no attribute`。

- [ ] **Step 4: 实现**

把 `backend/files_downloader.py` 第 39-47 行的 `build_folder_path` 整段替换为下面三块（`_folder_chain` 在前）：

```python
def _folder_chain(folder_id, folders: list[dict]) -> list[dict]:
    """从课程根到 folder_id 的文件夹节点链（根在前、folder_id 在后）。带环保护。

    链上首节点的 parent_folder_id 为 None 时，它就是课程根文件夹 —— 即 Canvas
    网页里显示为 "course files" 的那一层。用结构判据而不是比较名字字符串：硬编码
    名字会在 Canvas 改文案（或换语言）时静默失效 —— 表现是那层文件夹又冒出来，
    且不报任何错。folder_id 不在 folders 里（含 None）→ 返回空链。
    """
    by_id = {f["id"]: f for f in folders}
    chain: list[dict] = []
    seen: set = set()
    cur = by_id.get(folder_id)
    while cur is not None:
        cid = cur.get("id")
        if cid in seen:          # 父链成环 → 就地截断，不挂起
            break
        seen.add(cid)
        chain.append(cur)
        cur = by_id.get(cur.get("parent_folder_id"))
    chain.reverse()
    return chain


def build_folder_path(folder_id, folders: list[dict]) -> str:
    """返回 folder_id 的斜杠路径，**不含课程根文件夹**；课程根下的文件返回 ''。

    folders 里找不到 parent_folder_id 为 None 的节点时（Canvas 返回形态与预期不符），
    退回「保留全部段」——即本轮改动前的行为：宁可多一层，也不把真实文件夹名当根丢掉。
    """
    chain = _folder_chain(folder_id, folders)
    if chain and chain[0].get("parent_folder_id") is None:
        chain = chain[1:]
    return "/".join(_safe_name(f.get("name", "")) for f in chain)


def build_legacy_folder_path(folder_id, folders: list[dict]) -> str:
    """返回**含**课程根文件夹的旧路径 —— 复现本轮改动前的 build_folder_path 行为。

    只用于「这个文件是不是已经在旧位置下过了」的兼容判定（见 plan_downloads）。
    """
    return "/".join(_safe_name(f.get("name", "")) for f in _folder_chain(folder_id, folders))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_files_downloader.py -q -k "folder_path"`
Expected: PASS。

- [ ] **Step 6: 跑全量，确认只剩计划内的那一个用例在红**

Run: `.venv/bin/python -m pytest -q`
Expected: **1 failed** —— 只剩 `test_plan_downloads_path_and_rename`（它的 `dest_path` 期望值还是旧形状，由 Task 2 修正）。其余全绿。

- [ ] **Step 7: 提交**

```bash
git add backend/files_downloader.py tests/test_files_downloader.py
git commit -m "feat: 文件夹路径拆成新旧两条（新路径不含 Canvas 课程根那层）"
```

---

### Task 2: 后端 —— 双路径判定（`saved` / `legacy_path` / 下载跳过）

**Files:**
- Modify: `backend/files_downloader.py:50-75`（`plan_downloads`）、`:87-116`（`download_items`）
- Modify: `backend/main.py:85-87`（`DownloadRequest.items` 注释）、`:582`（planned 构造）
- Test: `tests/test_files_downloader.py:61-85`（修正既有用例）、文件末尾新增用例

**Interfaces:**
- Consumes: Task 1 的 `build_folder_path` / `build_legacy_folder_path`
- Produces:
  - `plan_downloads(...)` 的每条计划项新增 `"legacy_path": str` 字段；`saved` 语义变为「新路径存在 **或** 旧路径存在」
  - `download_items(...)` 在旧路径已存在时记为 `skipped` 且**不调用** `canvas_client.download_file`
  - `/api/download_files` 的 `req.items[]` 接受可选 `legacy_path`

**为什么 `download_items` 也要改（只改 `saved` 不够）：** 若某文件只存在于旧路径，它在 UI 上已被标为「已保存」并默认不勾；但用户可以**手动勾上**它。此时 `download_items` 查的是**新**路径 → 不存在 → 真的再下一份，磁盘上出现新旧两份，正好违背 D1 的「不会白下一遍」。所以跳过判定必须同样看旧路径。

- [ ] **Step 1: 写失败测试**

把 `tests/test_files_downloader.py` 中**现有的** `test_plan_downloads_path_and_rename`（第 61-85 行）整段替换为：

```python
def test_plan_downloads_path_and_rename(tmp_path):
    files = [
        {"id": 1, "display_name": "a.pdf", "folder_id": 2},
        {"id": 2, "display_name": "a.pdf", "folder_id": 2},
        {"id": 3, "display_name": "b.pdf", "folder_id": None},
        {"id": 4, "display_name": "a.pdf", "folder_id": 2},
    ]
    folders = [
        {"id": 1, "name": "course files", "parent_folder_id": None},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1},
    ]
    planned = files_downloader.plan_downloads(str(tmp_path), "CS 101", files, folders)
    assert len(planned) == 4
    # 新路径：不含课程根文件夹那一层（真正落盘的位置）
    assert planned[0]["dest_path"] == str(tmp_path / "CS 101" / "Week 3" / "a.pdf")
    assert planned[1]["dest_path"] == str(tmp_path / "CS 101" / "Week 3" / "a_2.pdf")
    assert planned[2]["dest_path"] == str(tmp_path / "CS 101" / "b.pdf")
    assert planned[3]["dest_path"] == str(tmp_path / "CS 101" / "Week 3" / "a_3.pdf")
    # 旧路径：保留课程根那一层，供「是否已下过」判定
    assert planned[0]["legacy_path"] == \
        str(tmp_path / "CS 101" / "course files" / "Week 3" / "a.pdf")
    # 根下文件（folder_id 为 None）的**新旧路径相同** —— 它在旧布局里也是直接落在
    # 课程目录下；只有子文件夹里的文件才会多出 "course files" 那一层。
    # 别把它的 legacy_path 也期望成带前缀的。
    assert planned[2]["legacy_path"] == str(tmp_path / "CS 101" / "b.pdf")
    assert planned[2]["legacy_path"] == planned[2]["dest_path"]
    # 磁盘上已存在的目标文件应标记 saved=True，其余 False
    assert planned[0]["saved"] is False  # 规划时该文件还不存在
    dest0 = Path(planned[0]["dest_path"])
    dest0.parent.mkdir(parents=True, exist_ok=True)
    dest0.write_bytes(b"x")
    planned2 = files_downloader.plan_downloads(str(tmp_path), "CS 101", files, folders)
    assert planned2[0]["saved"] is True
    assert planned2[1]["saved"] is False


def test_plan_downloads_marks_saved_when_only_legacy_exists(tmp_path):
    """老用户磁盘上是旧布局：文件只存在于 legacy_path → 同样算已保存（不重下）。"""
    files = [{"id": 1, "display_name": "a.pdf", "folder_id": 2}]
    folders = [
        {"id": 1, "name": "course files", "parent_folder_id": None},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1},
    ]
    legacy = tmp_path / "CS 101" / "course files" / "Week 3" / "a.pdf"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"old")
    planned = files_downloader.plan_downloads(str(tmp_path), "CS 101", files, folders)
    assert planned[0]["saved"] is True
    assert not Path(planned[0]["dest_path"]).exists()   # 检测不得凭空创建新路径


def test_plan_downloads_legacy_equals_dest_when_no_folders(tmp_path):
    """folders 为空（含文件全在课程根）时两条路径重合，saved 仍按磁盘真实情况给。"""
    files = [{"id": 1, "display_name": "a.pdf", "folder_id": None}]
    planned = files_downloader.plan_downloads(str(tmp_path), "CS 101", files, [])
    assert planned[0]["dest_path"] == planned[0]["legacy_path"]
    assert planned[0]["saved"] is False


def test_download_items_skips_when_legacy_path_exists(monkeypatch, tmp_path):
    """只有旧路径存在时，手动勾选下载也不得再下一份到新路径（D1）。"""
    calls = []
    def ok(canvas_url, token, url, dest):
        calls.append(dest)
    monkeypatch.setattr(canvas_client, "download_file", ok)
    legacy = tmp_path / "CS 101" / "course files" / "a.pdf"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"old")
    files_by_id = {1: {"url": "http://x/f/1"}}
    planned = [{"file_id": 1, "dest_path": str(tmp_path / "CS 101" / "a.pdf"),
                "legacy_path": str(legacy)}]
    result = files_downloader.download_items("https://x", "tok", files_by_id, planned,
                                             str(tmp_path))
    assert result["ok"] is True
    assert result["downloaded"] == []
    assert result["skipped"] == [str(legacy)]
    assert calls == []                                    # 没有真的去下
    assert not (tmp_path / "CS 101" / "a.pdf").exists()   # 新路径没被创建
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_files_downloader.py -q`
Expected: FAIL —— `KeyError: 'legacy_path'`（`plan_downloads` 还没产出该字段）；`test_download_items_skips_when_legacy_path_exists` 报 `result["skipped"] == []`。

- [ ] **Step 3: 实现 `plan_downloads`**

把 `backend/files_downloader.py` 的 `plan_downloads` 整段替换为：

```python
def plan_downloads(download_dir: str, course_name: str, files: list[dict],
                   folders: list[dict]) -> list[dict]:
    """规划落盘目标；同名加 _N 后缀。

    dest_path 是不含课程根文件夹的新路径（真正落盘的位置）；legacy_path 是含课程根
    那一层的旧路径，只用于「是否已经下过」的判定 —— 老用户磁盘上是旧布局，不能因为
    换了布局就把它当成没下过、再下一份。
    saved = 新路径存在 **或** 旧路径存在（供前端显示「已保存」并默认不勾选）。
    """
    root = Path(download_dir).expanduser() / _safe_name(course_name)
    planned: list[dict] = []
    used: set[str] = set()
    for f in files:
        folder_path = build_folder_path(f.get("folder_id"), folders)
        legacy_folder = build_legacy_folder_path(f.get("folder_id"), folders)
        base = root / folder_path if folder_path else root
        display = _safe_name(f.get("display_name", "file"))
        dest = base / display
        stem = dest.stem
        suffix = dest.suffix
        counter = 2
        while str(dest) in used:
            dest = base / f"{stem}_{counter}{suffix}"
            counter += 1
        used.add(str(dest))
        legacy = root / legacy_folder / display if legacy_folder else root / display
        planned.append({
            "file_id": f["id"], "display_name": display, "dest_path": str(dest),
            "legacy_path": str(legacy),
            "saved": dest.exists() or legacy.exists(),
        })
    return planned
```

- [ ] **Step 4: 实现 `download_items` 的旧路径跳过**

把 `backend/files_downloader.py` 中 `download_items` 的 `try:` 块整段替换为：

```python
        try:
            if dest.exists():
                skipped.append(str(dest))
                continue
            # 旧布局（含课程根那层）里已有同一文件 → 同样算「已下过」：跳过，不重下、
            # 也不搬动（D1）。legacy_path 由客户端回传、同样经 confine_dest 重新锚定；
            # 它只影响「跳不跳」，写不出下载目录、也覆盖不了任何文件。
            legacy = (confine_dest(download_dir, item["legacy_path"])
                      if item.get("legacy_path") else None)
            if legacy is not None and legacy.exists():
                skipped.append(str(legacy))
                continue
            canvas_client.download_file(canvas_url, token, info["url"], str(dest))
            downloaded.append(str(dest))
        except Exception as exc:  # 单文件失败不影响整体
            failed.append({"file_id": item["file_id"], "error": str(exc)})
```

- [ ] **Step 5: 在 `main.py` 透传 `legacy_path`**

把 `backend/main.py` 第 87 行的注释改为：

```python
    items: list[dict]  # [{course_id, file_id, dest_path, legacy_path}]
```

把第 582 行的 `planned` 构造替换为：

```python
    planned = [{"file_id": i["file_id"], "dest_path": i["dest_path"],
                "legacy_path": i.get("legacy_path", "")} for i in req.items]
```

（用 `.get` 而不是 `[]`：老前端或手工调用不带该字段时必须仍能工作。）

- [ ] **Step 6: 跑全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: `235 passed`。算式：基线 228 + Task 1 净增 4（1 个既有用例被换成 5 个）+ 本任务净增 3（1 个既有用例被换成 4 个）= 235。**若有计划外的既有用例转红，说明改动越界了，停下来查。**

- [ ] **Step 7: 提交**

```bash
git add backend/files_downloader.py backend/main.py tests/test_files_downloader.py
git commit -m "feat: 文件「已下过」按新旧两条路径判定，旧布局不重下也不搬动"
```

---

### Task 3: 后端 —— `/api/list_files` 回传 folders（含 position）

**Files:**
- Modify: `backend/canvas_client.py:511-515`（folders 映射）
- Modify: `backend/main.py:549-568`（成功分支增加 `folders`；三条错误分支补 `folders: []`）
- Test: `tests/test_main.py`（文件末尾新增用例）

**Interfaces:**
- Consumes: 无
- Produces: `/api/list_files` 每门课的结果多一个 `folders` 数组，元素形如 `{"id": int, "name": str, "parent_folder_id": int|None, "position": int|None}`；`files[]` 元素增加 `legacy_path`

**前端依赖这个字段才能建树。** 此前 `folders` 只在服务端用来算 `path`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_main.py` 末尾追加：

```python
def test_list_files_returns_folders_with_position(monkeypatch):
    """folders 必须回传给前端（建树用），并透传 position（Canvas 不一定给，取不到即 None）。"""
    files = [{"id": 9, "display_name": "a.pdf", "folder_id": 2,
              "content_type": "application/pdf", "size": 1, "url": "http://x/f/9"}]
    folders = [
        {"id": 1, "name": "course files", "parent_folder_id": None, "position": 1},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1, "position": None},
    ]
    monkeypatch.setattr(canvas_client, "list_courses", lambda u, t: [{"id": 5, "name": "CS 101"}])
    monkeypatch.setattr(canvas_client, "get_course_files", lambda u, t, cid: (files, folders))
    r = client.post("/api/list_files", json={"canvas_url": "https://x", "canvas_token": "t",
                                             "course_ids": [5], "download_dir": "/tmp/dl"})
    body = r.json()
    assert body["ok"] is True
    got = body["courses"][0]
    assert [f["name"] for f in got["folders"]] == ["course files", "Week 3"]
    assert got["folders"][0]["parent_folder_id"] is None
    assert got["folders"][1]["position"] is None
    # 路径语义随之改变：不含课程根那一段
    assert got["files"][0]["path"] == "Week 3"
    # 落盘目标也不含那一段
    assert got["files"][0]["dest_path"].endswith("CS 101/Week 3/a.pdf")
    # 旧路径随 files 一并回传，供前端原样转交给下载端点
    assert got["files"][0]["legacy_path"].endswith("CS 101/course files/Week 3/a.pdf")


def test_list_files_403_still_returns_folders_key(monkeypatch):
    """空文件夹区/未开放 → no_files，且 folders 键存在（形状统一，前端少一种分支）。"""
    def boom(u, t, cid):
        raise canvas_client.CanvasError("HTTP 403")
    monkeypatch.setattr(canvas_client, "list_courses", lambda u, t: [{"id": 5, "name": "CS 101"}])
    monkeypatch.setattr(canvas_client, "get_course_files", boom)
    r = client.post("/api/list_files", json={"canvas_url": "https://x", "canvas_token": "t",
                                             "course_ids": [5], "download_dir": "/tmp/dl"})
    got = r.json()["courses"][0]
    assert got["no_files"] is True
    assert got["folders"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_main.py -q -k list_files`
Expected: FAIL —— `KeyError: 'folders'`。

- [ ] **Step 3: 实现 `canvas_client` 的 folders 映射**

把 `backend/canvas_client.py` 第 511-515 行的 `folders = [...]` 替换为：

```python
    folders = [{
        "id": fo["id"],
        "name": fo.get("name", ""),
        "parent_folder_id": fo.get("parent_folder_id"),
        # position 供前端按 Canvas 的顺序排文件夹。Canvas 不一定回这个字段，
        # 取不到就是 None —— 前端按「有 position 就用它排，没有就按名字排」容缺。
        "position": fo.get("position"),
    } for fo in folders_data]
```

- [ ] **Step 4: 实现 `main.py` 的回传**

把 `backend/main.py` 第 549-558 行成功分支的 `results.append({...})` 替换为：

```python
            results.append({
                "course_id": cid, "name": name,
                "files": [{
                    "file_id": f["id"], "display_name": f["display_name"],
                    "path": files_downloader.build_folder_path(f.get("folder_id"), folders),
                    "content_type": f["content_type"], "size": f["size"],
                    "dest_path": by_id.get(f["id"], {}).get("dest_path", ""),
                    "legacy_path": by_id.get(f["id"], {}).get("legacy_path", ""),
                    "saved": bool(by_id.get(f["id"], {}).get("saved")),
                } for f in files],
                "folders": [{
                    "id": fo["id"], "name": fo.get("name", ""),
                    "parent_folder_id": fo.get("parent_folder_id"),
                    "position": fo.get("position"),
                } for fo in folders],
            })
```

（注意 `files[]` 这里同时加上了 `legacy_path` —— 前端 Task 4 要把它原样回传给 `/api/download_files`。）

把下面三条错误分支（第 564、566、568 行）的字典各加上 `"folders": []`：

```python
                results.append({"course_id": cid, "name": name, "files": [], "folders": [],
                                "no_files": True})
            else:
                results.append({"course_id": cid, "name": name, "files": [], "folders": [],
                                "error": msg})
        except Exception as exc:
            results.append({"course_id": cid, "name": name, "files": [], "folders": [],
                            "error": str(exc)})
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest -q`
Expected: `237 passed`（Task 2 后的 235 + 本任务 2 个新用例）。

- [ ] **Step 6: 提交**

```bash
git add backend/canvas_client.py backend/main.py tests/test_main.py
git commit -m "feat: /api/list_files 回传 folders（含 position 透传）与 legacy_path"
```

---

### Task 4: 前端 —— 建树函数 + 侧栏「文件」页改文件夹树

**Files:**
- Modify: `frontend/app.js:1245-1278`（`renderFiles` 及其上方新增建树函数）、`:1312-1324`（caret 委托加文件夹分支）、`:1343`（下载时回传 `legacy_path`）
- Modify: `frontend/app.css`（文件末尾追加文件夹行样式）

**Interfaces:**
- Consumes: Task 3 的 `courses[].folders[]` 与 `files[].legacy_path`
- Produces:
  - `buildFileTree(files, folders) -> { folders: [node], files: [file] }`，节点形如 `{ id, name, position, folders: [], files: [] }`
  - `treeRows(node, depth, expanded, fkey, fileRow) -> string`（HTML）
  - `const expandedFolders = new Set()`（顶层，键为 `"<courseId>:<folderId>"`）
  - `parentChainCycles(nodes, n) -> bool`、`sortTree(folders, files) -> void`

**本任务不加任何 i18n 键** —— 文件夹行复用已有的 `files.expand` / `files.collapse`。新增键一律放到 Task 5（它们的唯一消费者），否则这两个键会立刻变成死键、把 i18n 门的死键数顶到 23 > 基线 21。

- [ ] **Step 1: 确认新顶层声明不重名**

Run: `grep -rn "expandedFolders\|buildFileTree\|treeRows\|sortTree\|parentChainCycles" frontend/`
Expected: 无输出。**若 `expandedFolders` 命中任何一处，换名字再继续** —— 重复的顶层 `let`/`const` 会让整页白屏。

- [ ] **Step 2: 加入建树与渲染函数**

在 `frontend/app.js` 第 1245 行（`const expandedFiles = new Set();`）**下方**插入：

```js
/* 文件夹展开状态（键为 "<courseId>:<folderId>"）。与 expandedFiles 分开：
   那个管课程卡片的展开，这个管卡片内部文件夹的展开。 */
const expandedFolders = new Set();
/* 把 /api/list_files 的 files[] 与 folders[] 合成一棵树。
   返回 { folders: 根层文件夹节点, files: 挂在根层的散文件 }；节点形如
   { id, name, position, folders:[], files:[] }。

   folders 可能是 undefined —— 后端在 no_files 与 error 两条分支上不带这个键
   （虽然 main.py 现在补了 folders: []，这里仍按容缺写：那两类课程今天显示的
   是一句弱提示，不能因为建树整块白掉）。 */
function buildFileTree(files, folders){
  const list = Array.isArray(folders) ? folders : [];
  const nodes = new Map();
  list.forEach(fo => nodes.set(fo.id, {
    id: fo.id, name: fo.name || "", position: fo.position, parent: fo.parent_folder_id,
    folders: [], files: [],
  }));
  const roots = [], loose = [];
  nodes.forEach(n => {
    const p = nodes.get(n.parent);
    /* 自指或父链回环的节点一律提到根层：否则两个节点互为 children 会让排序与
       渲染无限递归。宁可丢掉这层（本就无意义），也不能挂起。 */
    if (p && p !== n && !parentChainCycles(nodes, n)) p.folders.push(n); else roots.push(n);
  });
  (files || []).forEach(f => {
    const n = nodes.get(f.folder_id);
    (n ? n.files : loose).push(f);        // 找不到所属文件夹 → 挂根层，不丢文件
  });
  sortTree(roots, loose);
  return { folders: roots, files: loose };
}
/* n 的父链上是否回环到 n 自身。seen 保证最多走一遍，绝不会无限循环。 */
function parentChainCycles(nodes, n){
  const seen = new Set();
  let cur = nodes.get(n.parent);
  while (cur){
    if (cur === n) return true;
    if (seen.has(cur)) return false;      // 撞进别人的环 → 这条链到此为止
    seen.add(cur);
    cur = nodes.get(cur.parent);
  }
  return false;
}
/* 文件夹按 Canvas 的 position 升序；取不到 position 的排在有值的后面，彼此按名字。
   文件按 display_name —— 我们没抓文件的 position，按名字排每次渲染顺序一致，
   比依赖 API 返回顺序这种隐含约定好。 */
function sortTree(folders, files){
  const pos = n => (n.position === null || n.position === undefined) ? Infinity : n.position;
  folders.sort((a, b) => (pos(a) - pos(b)) || a.name.localeCompare(b.name));
  files.sort((a, b) => String(a.display_name || "").localeCompare(String(b.display_name || "")));
  folders.forEach(n => sortTree(n.folders, n.files));
}
/* 递归渲染一棵文件夹树。fileRow(f, depth) 由调用方给，负责生成文件行的 HTML
   （侧栏带勾选框、课程中心不带）。fkey(folderId) 生成展开状态的键。
   传入 { folders, files } 形状的伪根即可 —— buildFileTree 的返回值就是这个形状。 */
function treeRows(node, depth, expanded, fkey, fileRow){
  const dirs = (node.folders || []).map(ch => {
    const k = fkey(ch.id);
    const open = expanded.has(k);
    const caret = open ? t("files.collapse") : t("files.expand");
    return `
      <div class="folder-row" style="padding-left:${depth * 14}px">
        <button type="button" class="fcaret" data-fkey="${escAttr(k)}" aria-expanded="${open}" title="${escAttr(caret)}">▸</button>
        <span class="folder-name">${esc(ch.name)}</span>
      </div>
      <div class="fchildren"${open ? "" : " hidden"}>${treeRows(ch, depth + 1, expanded, fkey, fileRow)}</div>`;
  }).join("");
  const rows = (node.files || []).map(f => fileRow(f, depth)).join("");
  return dirs + rows;
}
```

- [ ] **Step 3: 把 `renderFiles` 改成树**

在 `frontend/app.js` 的 `renderFiles` 里，把第 1250-1256 行的 `shown` 计算替换为：

```js
  const shown = fileCourses
    .filter(c => !courseSel || c.name === courseSel)
    .map(c => ({ ...c, _orig: fileCourses.indexOf(c),
                 _empty:(c.files||[]).length===0 && (c.folders||[]).length===0,
                 files:(c.files||[]).filter(f=>{
      if(!filter) return true;
      return (f.content_type||"").toLowerCase().includes(filter)
          || (f.display_name||"").toLowerCase().endsWith("."+filter);
    })}));
```

把第 1262-1275 行 `.map((c)=>{ ... })` 里的 `const cfs=c.files||[];` 与其下的 `rows` 替换为：

```js
    const cfs=c.files||[];
    const folders=c.folders||[];
    /* 文件行保留原有的 .fl 勾选框与 file-open 直链（updateSelectAllBtn 与
       refreshCourseChecks 是按 .fl 全局/按卡片统计的，文件夹行绝不能带 .fl，
       否则文件夹会被算成一个文件）。 */
    const fileRow=(f, depth)=>`
        <div class="item" style="padding-left:${depth*14}px"><input type="checkbox" class="fl" data-ci="${c._orig}" data-fi="${f.file_id}" ${f.saved?"":"checked"}>
          <div><div class="item-title"><a href="#" class="file-open" data-ci="${c._orig}" data-fid="${escAttr(f.file_id)}" data-name="${escAttr(f.display_name)}" title="${escAttr(t("file.open"))}">${esc(f.display_name)}</a> <span class="muted">（${esc(f.content_type)}）</span>${f.saved?` <span class="file-saved">${esc(t("files.saved"))}</span>`:""}</div>
          <div class="file-path">${esc(f.path||"/")}</div></div></div>`;
    const tree=buildFileTree(cfs, folders);
    const rows=treeRows(tree, 0, expandedFolders, (fid)=>`${c.course_id}:${fid}`, fileRow);
    const has = cfs.length>0 || folders.length>0;
```

（`has` 必须把 folders 算进去 —— 否则一个只有空文件夹的课程会连展开按钮都不出现，违反「空文件夹也要看得见」。）

- [ ] **Step 4: 加文件夹 caret 的委托分支**

在 `frontend/app.js` 第 1312 行 `const ct=e.target.closest(".caret");` 这一分支**之前**插入：

```js
  const fc=e.target.closest(".fcaret");
  if(fc){
    const k=fc.dataset.fkey;
    const kids=fc.closest(".folder-row").nextElementSibling;   // 紧邻的 .fchildren
    const wasOpen = kids && !kids.hidden;
    if(kids) kids.hidden = wasOpen;
    fc.setAttribute("aria-expanded", String(!wasOpen));
    fc.title = wasOpen ? t("files.expand") : t("files.collapse");
    if(wasOpen) expandedFolders.delete(k); else expandedFolders.add(k);
    return;
  }
```

- [ ] **Step 5: 下载时回传 `legacy_path`**

把 `frontend/app.js` 第 1343 行的 `return { course_id:c.course_id, file_id:f.file_id, dest_path:f.dest_path }; });` 替换为：

```js
    return { course_id:c.course_id, file_id:f.file_id,
             dest_path:f.dest_path, legacy_path:f.legacy_path||"" }; });
```

- [ ] **Step 6: 加样式**

在 `frontend/app.css` 文件末尾追加：

```css
/* 文件夹树（侧栏文件页 + 课程中心文件标签共用）。缩进由 JS 的内联 padding-left 给，
   这里只管行本身的排版与 caret 的展开态。 */
.folder-row { display:flex; align-items:center; gap:6px; padding:5px 0; }
.folder-row .fcaret { border:0; background:none; cursor:pointer; color:var(--muted);
  font-size:14px; line-height:1; padding:0; }
.folder-row .fcaret:hover { color:var(--ink); }
.folder-row .fcaret[aria-expanded="true"] { transform:rotate(90deg); }
.folder-name { font-size:13px; font-weight:600; }
.fchildren[hidden] { display:none; }
```

- [ ] **Step 7: 跑静态门**

```bash
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/concat-check.js && node --check /tmp/concat-check.js && echo "SYNTAX OK"
node tools/check_i18n_keys.mjs
node tools/check_dom_ids.mjs
```

Expected: `SYNTAX OK`；`死键 21 个（基线 21）`；`HTML 共 124 个 id`。**死键数若变成 23，说明 i18n 键被提前加进来了，回退到 Task 5 再加。**

- [ ] **Step 8: 提交**

```bash
git add frontend/app.js frontend/app.css
git commit -m "feat: 侧栏文件页改为可展开的文件夹树（与 Canvas 结构一致）"
```

- [ ] **Step 9: 控制方跑运行时探针（本步骤由控制方执行，不在实施子代理范围内）**

在 dev server（`127.0.0.1:8331`）上用 chrome-devtools MCP 的 `evaluate_script` 执行探针，**裸函数表达式、不能包 IIFE**。被测函数全是真身，只有 `api` 被打桩。断言：

1. `buildFileTree` 对合成数据把根文件夹排除在路径外（`folder_id` 指向子文件夹的文件落在 `Week 3` 节点下）。
2. `position` 缺失时按名字排；有 `position` 时按 position 排。
3. `folders` 传 `undefined` 时不抛，全部文件挂根层。
4. `parent_folder_id` 成环时不挂起、不死循环。
5. `renderFiles()` 之后 `#filesArea` 的 HTML 里出现 `.folder-row` 与 `.fchildren`，且 `.fl` 的数量等于文件数（文件夹行没带 `.fl`）。

---

### Task 5: 前端 —— 课程中心「文件」标签：树 + 「还没下载」清单 + 下载所选

**Files:**
- Modify: `frontend/app.js:2404-2432`（`renderHubFiles`）、`:2350-2355` 之后（新增委托与辅助函数）
- Modify: `frontend/app.css`（追加清单样式）
- Modify: `frontend/i18n.js`（新增 2 个键 × zh/en 各一处：`files.undownloaded` 与 `files.root`）

**Interfaces:**
- Consumes: Task 4 的 `buildFileTree` / `treeRows` / `expandedFolders`；Task 3 的 `folders[]`；Task 2 的 `/api/download_files` 的 `legacy_path`
- Produces: `hubSelectedFiles(cid) -> [{course_id, file_id, dest_path, legacy_path}]`、`hubSyncDownloadBtn() -> void`

**两个必须遵守的约定：**

1. **清单勾选框用 `.hfl`，绝不能用 `.fl`。** 侧栏的 `updateSelectAllBtn()` 与 `refreshCourseChecks()` 是按 `document.querySelectorAll(".fl")` **全局**统计的，而 `#filesArea` 与 `#hubPanel` **同时存在于 DOM**（页面只是切换显示）。用 `.fl` 会让课程中心的勾选被算进侧栏的「全选」判断。
2. **「下载所选」按钮不要用 `id`。** `tools/check_dom_ids.mjs` 要求 JS 里每个 `$("...")` 都能在 `index.html` 找到对应 id，而这个按钮是 `renderHubFiles` 每次重渲出来的、静态 HTML 里没有。所以用 **class** `.hub-dl-go` + `#hubPanel` 上的事件委托来取它 —— **不要为了取它而新加一个 id，也不要绕过门禁**。

- [ ] **Step 1: 加 i18n 键（zh 与 en 各一处，必须成对）**

在 `frontend/i18n.js` 的中文段 `"files.saved": "已保存",`（第 85 行）下方插入：

```js
    "files.undownloaded": "还没下载",
    "files.root": "根目录",
```

在英文段 `"files.saved": "Saved",` 下方插入对应两行：

```js
    "files.undownloaded": "Not downloaded",
    "files.root": "root",
```

（「下载所选」按钮**复用已有的 `btn.download`** —— 它已经通过 `index.html:173` 的 `data-i18n` 被引用，不是死键，重复使用不改变任何计数。）

- [ ] **Step 2: 改写 `renderHubFiles`**

把 `frontend/app.js` 第 2404-2432 行的 `renderHubFiles` 整段替换为：

```js
/* 文件子标签。只读 fileCourses 缓存，不触发网络（刷新由 hubRefreshCurrent 统一发起）。 */
function renderHubFiles(cid){
  const box = $("hubPanel");
  if (!box) return;
  const c = (fileCourses || []).find(x => x.course_id === cid);
  if (!c){                                   // 全局缓存里根本没有这门课 → 尚未加载
    box.innerHTML = `<div class="muted">${t("hub.tab_empty.files_unloaded")}</div>`;
    return;
  }
  if (c.error){ box.innerHTML = `<div class="muted">${esc(c.error)}</div>`; return; }
  const files = c.files || [];
  const folders = c.folders || [];
  if (!files.length && !folders.length){
    // 后端在 Canvas 对空文件区返回 403 时会带 no_files:true（backend/main.py 的 list_files）——
    // 那种「确实为空 / 未对学生开放」用侧栏文件页的同一句措辞，比笼统的 hub.tab_empty.files 准。
    // 两条分支各写一遍取词调用、而不是把它塞进三元表达式：check_i18n_keys.mjs 的悬空引用扫描
    // 只认「t 后面紧跟一个引号字面量」这种形状，写成条件表达式后那两个键就查不到定义了。
    // （同理，本注释里也不能出现 t 加引号的字样 —— 那个扫描读的是原文，连注释一起读。）
    box.innerHTML = c.no_files
      ? `<div class="muted">${t("files.no_files")}</div>`
      : `<div class="muted">${t("hub.tab_empty.files")}</div>`;
    return;
  }
  // 上段：文件夹树，文件行不带勾选框（点文件名仍是 openFileSmart 直开）
  const row = (f, depth) => `
    <div class="item" style="padding-left:${depth*14}px">
      <div>
        <div class="item-title"><a href="#" class="file-open" data-cid="${cid}" data-fid="${escAttr(f.file_id)}" data-name="${escAttr(f.display_name)}" title="${escAttr(t("file.open"))}">${esc(f.display_name)}</a> <span class="muted">（${esc(f.content_type || "")}）</span>${f.saved ? ` <span class="file-saved">${esc(t("files.saved"))}</span>` : ""}</div>
        <div class="file-path">${esc(f.path || "/")}</div>
      </div>
    </div>`;
  const tree = buildFileTree(files, folders);
  const treeHtml = treeRows(tree, 0, expandedFolders, (fid)=>`${cid}:${fid}`, row);
  // 下段：还没下载的，可勾选下载
  const missing = files.filter(f => !f.saved);
  const panel = missing.length ? `
    <div class="hub-dl-sep"></div>
    <div class="sub-label">${t("files.undownloaded")} (${missing.length})</div>
    <div class="hub-dl-list">${missing.map(f => `
      <label class="hub-dl-row"><input type="checkbox" class="hfl" data-fid="${escAttr(f.file_id)}">
        <span class="hub-dl-name">${esc(f.display_name)}</span>
        <span class="hub-dl-path">${esc(f.path || t("files.root"))}</span></label>`).join("")}</div>
    <div class="hub-dl-go-wrap"><button type="button" class="btn btn-primary hub-dl-go" disabled>${esc(t("btn.download"))}</button></div>` : "";
  box.innerHTML = treeHtml + panel;
}
```

- [ ] **Step 3: 加勾选收集与按钮态同步**

紧跟 `frontend/app.js` 第 2355 行（`.file-open` 委托那段之后）插入：

```js
/* 课程中心「还没下载」清单的勾选收集。勾选框是 .hfl 而不是 .fl —— 侧栏的
   updateSelectAllBtn/refreshCourseChecks 按 document.querySelectorAll(".fl") 全局
   统计，而 #filesArea 与 #hubPanel 同时在 DOM 里，用 .fl 会把这里的勾选算进侧栏。 */
function hubSelectedFiles(cid){
  const c = (fileCourses || []).find(x => x.course_id === cid);
  if (!c) return [];
  return [...document.querySelectorAll("#hubPanel .hfl:checked")].map(i => {
    const f = (c.files || []).find(x => x.file_id === Number(i.dataset.fid));
    return f ? { course_id: cid, file_id: f.file_id,
                 dest_path: f.dest_path, legacy_path: f.legacy_path || "" } : null;
  }).filter(Boolean);
}
/* 「下载所选」的可用态：一个都没勾就禁用。按钮每次重渲都是新的，所以按 class 取，
   不用 id —— 动态渲染的元素在 index.html 里没有对应 id，用 $() 取会被 check_dom_ids 拦。 */
function hubSyncDownloadBtn(){
  const btn = document.querySelector("#hubPanel .hub-dl-go");
  if (btn) btn.disabled = hubSelectedFiles(hubCid).length === 0;
}
$("hubPanel").addEventListener("change", (e) => {
  if (e.target.classList && e.target.classList.contains("hfl")) hubSyncDownloadBtn();
});
/* 课程中心的下载走与侧栏同一个端点。逐条发（端点一次只收一批 items，逐条发好报进度），
   完成后重渲本标签 —— saved 会从后端刷新回来，清单条目随之消失、计数随之减少。 */
$("hubPanel").addEventListener("click", async (e) => {
  const btn = e.target.closest(".hub-dl-go");
  if (!btn) return;
  const items = hubSelectedFiles(hubCid);
  if (!items.length) return;
  const s = settings();
  btn.disabled = true;
  const failed = [];
  try {
    for (const it of items) {
      const r = await api("download_files",
        { ...s, download_dir: downloadDir(), items: [it] });
      if (r.ok !== true) failed.push({ file_id: it.file_id, error: r.error || "" });
      else failed.push(...(r.failed || []));
    }
    const doneMsg = t("status.download_done",
      { a: items.length - failed.length, b: failed.length, s: 0 });
    setStatus(doneMsg, failed.length === 0 ? "ok" : "err");
    await hubRefreshFiles(hubCid);
  } finally {
    hubSyncDownloadBtn();
  }
});
```

- [ ] **Step 4: 加清单样式**

在 `frontend/app.css` 文件末尾追加：

```css
/* 课程中心「还没下载」清单 */
.hub-dl-sep { border-top:1px dashed var(--border); margin:16px 0 10px; }
.hub-dl-list { display:flex; flex-direction:column; }
.hub-dl-row { display:flex; gap:8px; align-items:center; padding:5px 0;
  border-top:1px dashed var(--border); cursor:pointer; }
.hub-dl-row input { accent-color:var(--accent); }
.hub-dl-name { font-size:13.5px; font-weight:500; }
.hub-dl-path { color:var(--muted); font-size:12px; margin-left:auto; }
.hub-dl-go-wrap { margin-top:10px; }
```

- [ ] **Step 5: 确认 `status.download_done` 的占位符与调用一致**

Run: `grep -n 'status.download_done' frontend/i18n.js`
Expected: 恰好两行 —— 第 200 行中文 `"下载完成：成功 {a}，跳过 {s}，失败 {b}"`、第 554 行英文 `"Download done: {a} ok, {s} skipped, {b} failed"`。占位符名就是 `a` / `s` / `b`，与 Step 3 传的 `{a, b, s}` 一致（占位符按名字替换，书写顺序不影响）。侧栏 `btnDownloadFiles` 的处理器（`frontend/app.js:1361`）传的是同一个形状，可作为对照。

- [ ] **Step 6: 跑静态门**

```bash
cat frontend/i18n.js frontend/util.js frontend/app.js frontend/shell.js > /tmp/concat-check.js && node --check /tmp/concat-check.js && echo "SYNTAX OK"
node tools/check_i18n_keys.mjs
node tools/check_dom_ids.mjs
```

Expected: `SYNTAX OK`；`键集一致（各 359 键）`、`死键 21 个（基线 21）`；`HTML 共 124 个 id`。

- [ ] **Step 7: 确认后端零改动、全量测试仍绿**

Run: `git diff --stat HEAD~1 -- backend/ tests/`
Expected: 空输出。

Run: `.venv/bin/python -m pytest -q`
Expected: `237 passed`（后端自 Task 3 起未再改动，应仍是 237）。

- [ ] **Step 8: 提交**

```bash
git add frontend/app.js frontend/app.css frontend/i18n.js
git commit -m "feat: 课程中心文件标签加文件夹树与「还没下载」勾选下载"
```

- [ ] **Step 9: 控制方跑运行时探针（本步骤由控制方执行）**

同 Task 4 的方式，额外断言：

1. 合成一门课（含 1 个已保存、2 个未保存文件）后调 `renderHubFiles(cid)`，`#hubPanel` 里出现 `还没下载 (2)`。
2. `#hubPanel` 里的勾选框数量为 2，且**全是 `.hfl`**，`.fl` 在 `#hubPanel` 里数量为 0。
3. 勾一个之后 `.hub-dl-go` 的 `disabled` 变 false；不勾则 true。
4. 全部文件已保存时，清单整段不出现（`hub-dl-sep` 与 `.hub-dl-go` 都不在）。
5. `#filesArea` 里的 `.fl` 计数不受 `#hubPanel` 勾选影响（两页同时存在时的隔离）。

---

## 收尾

全部 5 个任务完成、终审通过后：

1. 全量门禁复跑：`.venv/bin/python -m pytest -q`、三个 node 门、四文件拼接 `node --check`。
2. 交用户人工验收（spec §8 的五条）——注意 `saved` 语义变了是**一次性的用户可见改动**，第 1、2 条必须真跑一次。
3. 走 `superpowers:finishing-a-development-branch` 的分支处置菜单（须用户点头）。
