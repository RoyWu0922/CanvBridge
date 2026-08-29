# Canvas 课程助手 设计文档

日期：2026-08-29
状态：已批准（用户确认设计）

## 1. 目标与背景

构建一个本地运行的课程信息助手，帮助用户从学校 Canvas 系统中提取课程信息并自动整理到 Apple 生态：

1. 登录学校 Canvas（API Token），读取每个课程在给定时间段内的公告，提取有用信息并生成中文总结。
2. 从总结中提取有具体时间/地点的日程，写入 Apple 日历（可选择目标日历）。
3. 从总结中提取只有截止日期（DDL）的待办，写入 Apple 提醒事项（可选择目标列表）。
4. 下载课程 Files（文件标签页）里的全部文件，按「科目 → 原文件夹结构」分类存到本地（可指定目录）。
5. 总结/提取通过配置的 OpenAI 兼容 LLM API（DeepSeek / Kimi / 通义 / 智谱 / Ollama 等）实现。

### 关键约束

- **运行形态**：本地后端 + 网页前端。浏览器访问 `localhost` 操作；API key 留在本机，不发往外部。
- 目标系统：macOS（依赖 AppleScript 操作日历/提醒、本地文件系统）。

## 2. 已确认的决策

| 决策点 | 结论 |
|---|---|
| 运行形态 | 本地后端（Python 3 + FastAPI）+ 网页前端（单页 HTML/JS） |
| Canvas 认证 | Bearer API Token（用户在校 Canvas 设置生成），非账号密码 |
| LLM 接口 | OpenAI 兼容（base_url + api_key + model），兼容主流国内/国外模型 |
| 公告语言 | 英文 |
| 日历/提醒内容语言 | 英文（从公告原文提取，不翻译） |
| 中文总结 | 中文，方便用户阅读 |
| 界面语言 | 中文 |
| 时间段选择 | 预设：本周 / 本月 / 自定义日期范围 |
| 分流规则 | 有具体时间段+地点 → 日历事件；只有截止 DDL → 提醒事项 |
| 文件下载范围 | 课程 Files 标签页全部文件（非仅公告附件） |
| 文件分类方式 A | `下载目录/科目名/<Canvas 原文件夹路径>/<文件名>`（保留结构） |

## 3. 架构

```
浏览器 (localhost:8000)  ──HTTP/JSON──▶  本地后端 (Python 3 + FastAPI, uvicorn)
                                              │
      ├── canvas_client.py      Canvas REST API（课程/公告/文件/文件夹）
      ├── llm_client.py         OpenAI 兼容接口
      ├── apple_script.py       AppleScript → Calendar + Reminders
      └── files_downloader.py   下载文件 → 下载目录/科目/原文件夹/文件名
```

- 后端只服务本机（`127.0.0.1`），无跨域问题（前端同源）。
- 配置（Canvas token、LLM key、下载目录、目标日历/提醒列表）存于浏览器 `localStorage`，每次请求随 body 传给后端，后端不持久化（内存态，进程重启即失）。

## 4. 目录结构

```
School_Calendar/
  backend/
    main.py             # FastAPI 入口 + 全部路由
    canvas_client.py    # Canvas API 封装（课程/公告/文件/文件夹/下载）
    llm_client.py       # OpenAI 兼容 LLM 封装（结构化 JSON 提取）
    apple_script.py     # AppleScript 封装（列出/创建日历事件、提醒事项）
    files_downloader.py # 文件下载 + 文件夹结构重建
    config.py           # 请求级配置校验与透传（不落盘）
  frontend/
    index.html          # 单页前端（内联 CSS/JS）
  requirements.txt
  README.md
  docs/superpowers/specs/2026-08-29-canvas-calendar-design.md
```

## 5. 前端设计（中文单页）

### 5.1 设置面板（可折叠）

| 字段 | 说明 |
|---|---|
| Canvas 实例 URL | 如 `https://xxx.instructure.com` |
| Canvas API Token | Bearer token |
| LLM Base URL | OpenAI 兼容地址，如 `https://api.deepseek.com/v1` |
| LLM API Key | 密钥 |
| LLM 模型名 | 如 `deepseek-chat` |
| 下载目录 | 默认 `~/Downloads/Canvas课程文件`，可改 |
| 目标日历 | 下拉，从 `osascript 列出日历` 填充 |
| 目标提醒列表 | 下拉，从 `osascript 列出提醒列表` 填充 |

设置存 `localStorage`，可「测试连接」（调后端拉课程列表验证 token）。

### 5.2 主面板

- 时间段选择：本周 / 本月 / 自定义（日期输入）
- 课程选择：从后端拉课程列表，可勾选（默认全选）
- 「同步并总结」按钮 → 调后端拉公告 → LLM 总结 → 展示

### 5.3 结果区（分区展示）

1. **中文总结卡片**：按课程分组，可折叠。
2. **日历事件**：每个事件显示标题/时间/地点（英文），可勾选，选择目标日历，点「写入日历」。
3. **提醒事项**：每条显示标题/截止时间（英文），可勾选，选择目标列表，点「写入提醒」。
4. **文件列表**：每个课程展示其 Files 文件（含文件夹路径），可勾选课程/文件、可按类型过滤，点「下载所选」。

每个动作独立执行并反馈状态（成功/失败/条数）。

## 6. 后端 API 设计

所有接口 `POST /api/...`，body 携带操作所需配置（token 等来自前端）。前缀 `/api`。

| 方法 | 路径 | 作用 | 请求体要点 | 响应 |
|---|---|---|---|---|
| POST | `/api/test_connection` | 验证 Canvas token | canvas_url, canvas_token | `{ok, courses:[{id,name}]}` |
| POST | `/api/courses` | 拉课程列表 | canvas_url, canvas_token | `{courses:[{id,name}]}` |
| POST | `/api/calendars` | 列出日历 | — | `{calendars:[name,...]}` |
| POST | `/api/reminder_lists` | 列出提醒列表 | — | `{lists:[name,...]}` |
| POST | `/api/sync_announcements` | 拉公告+LLM总结 | canvas 配置, llm 配置, course_ids, start_date, end_date | `{courses:[{name, summary_cn, events, reminders}]}` |
| POST | `/api/add_calendar_event` | 写日历事件 | calendar_name, title, start, end, location, notes | `{ok}` |
| POST | `/api/add_reminder` | 写提醒 | list_name, title, due_date, notes | `{ok}` |
| POST | `/api/list_files` | 列出课程文件+文件夹 | canvas 配置, course_ids | `{courses:[{id,name,files:[{id,display_name,path,content_type,size}]}]}` |
| POST | `/api/download_files` | 下载所选文件 | canvas 配置, download_dir, files:[{course_name,file_id,display_name,path}] | `{ok, downloaded:[...], failed:[...]}` |

### 6.1 后端内部行为

- **Canvas 客户端**：封装 `requests`。课程列表 `GET /api/v1/courses?enrollment_state=active`；公告 `GET /api/v1/announcements?context_codes[]=course_N&start_date=&end_date=`（返回 DiscussionTopic 对象，含 `title`/`message` HTML/`posted_at`）；文件 `GET /api/v1/courses/:id/files?per_page=100` 翻页 + `GET /api/v1/courses/:id/folders`；下载 `GET 文件.url` 带 Bearer。
- **公告消息清洗**：`message` 为 HTML，用简单正则/HTMLParser 剥离标签得纯文本送 LLM。
- **分页**：读取 `Link` 响应头翻页，直至取完。
- **LLM 客户端**：`POST {base_url}/chat/completions`，带 `Authorization: Bearer key`，`response_format` 尽量用 JSON（不支持则 prompt 约束 + 容错解析）。输出 schema 见 §7。
- **AppleScript**：`subprocess.run(["osascript", "-e", ...])`。
  - 列出日历：`tell application "Calendar" to get name of every calendar`
  - 创建事件：`tell application "Calendar" to tell calendar "<name>" to make new event with properties {summary:"...", start date:<date>, end date:<date>, location:"..."}`
  - 列出提醒列表：`tell application "Reminders" to get name of every list`
  - 创建提醒：`tell application "Reminders" to tell list "<name>" to make new reminder with properties {name:"...", due date:<date>, body:"..."}`
  - AppleScript 日期：用 `date "2026-08-30 14:00:00"` 字符串形式（按本机区域解析）。
- **文件下载**：用 `files_downloader`，按 `下载目录/科目名/原文件夹路径/display_name` 落盘，文件名冲突时追加序号。下载失败记录 `failed`。

## 7. LLM 输出 Schema（严格 JSON）

对每门课程一份：

```json
{
  "course_name": "CS 101",
  "summary_cn": "本周公告要点（中文）：……",
  "calendar_events": [
    {
      "title": "Quiz 1 Review Session",
      "start": "2026-08-31T14:00:00",
      "end": "2026-08-31T15:30:00",
      "location": "Room A101",
      "notes": "Bring calculator"
    }
  ],
  "reminders": [
    {
      "title": "Homework 3 due",
      "due_date": "2026-09-02T23:59:00",
      "notes": "Submit via Canvas"
    }
  ]
}
```

- `calendar_events`：仅当公告明确给出开始/结束时间（或单点时长）的日程。
- `reminders`：仅截止/DDL 类（无具体起止时间），`due_date` 为截止时刻。
- 标题/notes 保持英文原文；`summary_cn` 中文。
- LLM 未识别任何时间时返回空数组，不臆造。
- 后端解析失败时：重试一次；仍失败则返回 `summary_cn` 原始内容 + 空数组并标记 warning。

## 8. 错误处理

- **Canvas 认证失败**（401）→ 明确提示「Token 无效或已过期」。
- **LLM 调用失败** → 保留公告原文作 `summary_cn`，提示「总结失败，已展示原文」。
- **AppleScript 写失败** → 返回 stderr 供前端提示（多为系统权限未授权）。
- **下载失败** → 记录具体文件名与原因，不中断其余文件。
- 所有接口返回统一结构：`{ok: bool, error?: string, ...}`。

## 9. 安全与隐私

- 服务只监听 `127.0.0.1`，不对外网暴露。
- Token/API key 只存浏览器 localStorage 与后端内存，不写磁盘（除非用户显式要求持久化——本期不做）。
- README 明确提醒：不要将 token 提交到 git（`backend` 不写 token；`.gitignore` 含 `.env`、localStorage 无关）。

## 10. 测试策略

- 单元测试：`canvas_client` 消息清洗（HTML→文本）；`files_downloader` 路径拼接与冲突改名；LLM 响应解析容错。
- 集成（可选，需真 token）：`test_connection` 拉课程。
- 手动验证：前端全流程（同步→总结→写日历→写提醒→下载）。
- 测试框架：`pytest`。

## 11. 范围外（本期不做）

- 公告附件的独立下载（本期文件下载仅针对课程 Files 标签页）。
- 事件/提醒的编辑界面（本期可勾选；编辑列为增强）。
- 定时自动同步。
- 日历/提醒去重（重复点击可能产生重复事件——本期在 UI 提示风险）。
- API key 持久化存储（本期仅 localStorage）。

## 12. 依赖

```
fastapi
uvicorn
requests
pytest
```

Python 3.9+，仅 macOS。
