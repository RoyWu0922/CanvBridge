# CanvBridge

把你的 CityU 学期日程收进一个本地页面：**AIMS (Banweb) 课表 / 考试 + Canvas 课程 / 作业 / 公告 / 文件**，一键同步到 **Apple 日历 / 提醒事项**，并提供 AI 总结。

Campus course assistant — one local page that pulls your AIMS timetable & exams and your Canvas courses together, syncs them into Apple Calendar / Reminders, and summarizes announcements & syllabi with an LLM.

> 完全本地运行：服务只监听 `127.0.0.1`，凭据按「存本机钥匙串 / 浏览器本地」的最低权限模型处理（详见 [安全模型](#凭据与安全模型)）。

---

## 目录

- [功能](#功能)
- [环境要求](#环境要求)
- [安装与启动（源码运行）](#安装与启动源码运行)
- [配置：API 与 Token](#配置api-与-token)
- [凭据与安全模型](#凭据与安全模型)
- [AIMS 自动登录说明](#aims-自动登录说明)
- [Apple 日历 / 提醒与 macOS 权限](#apple-日历--提醒与-macos-权限)
- [课程文件下载](#课程文件下载)
- [测试](#测试)
- [打包桌面 App（可选）](#打包桌面-app可选)
- [项目结构](#项目结构)
- [常见问题](#常见问题)

---

## 功能

1. **课表 / 考试（AIMS, Banweb）**
   - 用 macOS 钥匙串里的 EID + 密码自动完成 CityU Okta 两步登录（headless Chrome，默认不弹窗）
   - 拉取课表与考试时间表 → 前端**周视图**预览 → 按标题去重写入 Apple 日历：
     - 课程：**每周重复事件**，范围到课程结束日期；可指定忽略某门课
     - 考试：一次性事件；单门课也可叠加显示
   - 「重新登录 / Sign in again」**一律先走无头自动登录**，只有失败（未存凭据 / 密码错误 / 后端忙）才弹出可手输的 Chrome 窗口兜底
2. **课程详情**：点课表某门课的 ⓘ → 课程代码 / CRN / 学分 / 学期节奏 / 每节讲师、Canvas 链接、Syllabus + **AI 总结**（可一键把总结里识别出的日期写入日历 / 提醒）
3. **作业标注**：Canvas 未截止作业按截止日期在周视图琥珀色标注，点击直达提交页
4. **公告 / 总结**：按时间段与课程筛选拉取公告，LLM 生成结构化总结（摘要 + 日程 + 截止提醒）
5. **课程文件下载**：Canvas Files → 按「课程 / 原文件夹结构」分类存到本机（默认 `~/Downloads/Canvas课程文件`，可用系统文件夹选择框改）
6. **界面**：中英 i18n、跟随系统 / 手动 浅深色、单页无刷新

## 环境要求

- **macOS 12+**（写日历 / 提醒走 AppleScript，凭据走钥匙串）
- **Python 3.10+**（源码运行时）
- **已安装 Google Chrome**（Playwright 以 `channel="chrome"` 驱动系统 Chrome 做 AIMS 登录）
- **CityU 账号**：AIMS EID + 密码、Canvas API Token
- LLM 服务（可选，用于 AI 总结）：任意 OpenAI 兼容接口

## 安装与启动（源码运行）

```bash
cd School_Calendar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run_app.py          # 起服务(127.0.0.1:8331)，等服务就绪后自动打开浏览器
```

或手动起服务再访问：

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8331
# 浏览器打开 http://127.0.0.1:8331
```

> 依赖仅 `fastapi / uvicorn[standard] / requests / httpx / pytest / playwright`（见 `requirements.txt`）。
> AIMS 登录用的是**系统安装的 Google Chrome**，无需 `playwright install` 下载自带浏览器；若提示找不到 Chrome，先安装 Google Chrome。

首次使用：右上「设置」按下面的表填好 → 写日历/提醒时放行 macOS 权限 → 完事。

## 配置：API 与 Token

所有设置都在页面右上角的「设置」里，无需改任何配置文件。下表是每个字段的含义与**存储位置**。

| 设置字段 | 填什么 | 存在哪 |
|---|---|---|
| **Canvas 实例 URL** | 学校 Canvas 地址，如 `https://cityu.instructure.com`（去掉末尾 `/`） | 浏览器 localStorage |
| **Canvas API Token** | Canvas：`Account → Settings → Approved Integrations → New Access Token` 生成的 Token | 浏览器 localStorage |
| **AIMS 账号 (CityUHK EID)** | 你的 CityU 登录 ID（如 `cs123456`） | **macOS 钥匙串** |
| **AIMS 密码** | CityU 账号密码 | **macOS 钥匙串**（仅存本机，不返回前端） |
| **LLM Base URL** | OpenAI 兼容接口根地址（可选，见下） | 浏览器 localStorage |
| **LLM API Key** | 模型服务商签发的 Key（可选） | 浏览器 localStorage |
| **LLM 模型名** | 如 `gpt-4o-mini` / `deepseek-chat`（可选） | 浏览器 localStorage |
| **下载目录** | 默认 `~/Downloads/Canvas课程文件`；可用原生文件夹选择框改 | 浏览器 localStorage |
| **主题** | 跟随系统 / 浅色 / 深色 | 浏览器 localStorage |

### Canvas Token

Token 只用于**读取**：课程列表、Syllabus、讲师、Modules、作业、文件、公告、待办与日历事件。建议创建时只授予所需作用域（read-only），不要用生产级全权限 Key。

### LLM（可选，用于 AI 总结 / Syllabus 提取）

接口走 OpenAI 兼容的 `POST {base_url}/chat/completions`。三个常用示例：

- **DeepSeek**（默认推荐，中文总结质量好）：
  - Base URL：`https://api.deepseek.com`
  - API Key：在 <https://platform.deepseek.com> 创建
  - 模型名：填 `deepseek-chat` 即可——后端检测到官方 `api.deepseek.com` 主机会自动把已退役的旧名迁移到 `deepseek-v4-flash`，并显式关闭思考模式保证结构化 JSON 稳定
- **OpenAI**：Base URL `https://api.openai.com/v1`，模型如 `gpt-4o-mini`
- **OpenRouter / 其它网关**：Base URL 填网关地址（如 `https://openrouter.ai/api/v1`），模型名原样透传，不会被 DeepSeek 的特殊逻辑改写

不填 LLM：公告 / Syllabus 页不生成 AI 总结，其余功能不受影响。

## 凭据与安全模型

- **监听边界**：服务固定监听 `127.0.0.1:8331`，未开 CORS——外部网页无法调用本机 API。
- **Canvas Token / LLM Key**：保存在**浏览器 localStorage**（明文，仅存于 `http://127.0.0.1:8331` 这个 origin）。每个 API 请求由前端带上、回环发给本机后端；后端**不落盘、不记日志、不返回**。换浏览器 / 清数据需重填。
- **AIMS 密码**：只存 **macOS 钥匙串**（`security add-generic-password`，service=`cityu_aims_login`），不存在任何明文文件、不进 Git。后端仅在自动登录时读出、直接填入 CityU Okta 表单；除提交给 CityU 官方登录页外不出本机，前端拿到的只有脱敏的账号名。
- 不写任何第三方云；同步目标是**你本机的** Apple 日历 / 提醒事项。

> 提示：localStorage 里的 Token 会被同 origin 下恶意脚本读到，但因服务只在回环且未开 CORS，风险局限于本机浏览器本身。介意可每次用完在设置里清掉。

## AIMS 自动登录说明

- 后端用 Playwright 驱动系统 Chrome，常驻独立 profile `~/.cityu_aims_profile`（CDP 端口 9339），登录态存该 profile，重复抓取不重复登录。
- 状态条会按 3s 轮询登录态：已退登且钥匙串有凭据 → **自动无头重登**；点「Sign in again」同样**先走无头自动登录**，仅当它失败才弹出手动登录窗口。
- Okta 两步表单（identifier → passcode）会自动填写；登录后回到学期页并等 `term_in` 下拉就绪才算成功。
- **关于卡死自愈**：所有浏览器操作跑在单个 worker 线程上（Playwright 绑定线程所致）。若某次页面操作卡住，后续请求会排队；后端有 180 秒看门狗会自动重建浏览器并恢复。期间前端可能短暂转圈，属设计内的自愈窗口；仍卡超过 3 分钟可重启 `run_app.py`。

## Apple 日历 / 提醒与 macOS 权限

- 写入通过 `osascript`（AppleScript）调用本机「日历」App：课程写入指定**日历**（按名称选）、提醒写入指定**提醒列表**。
- 课表写入按事件**标题查重**：已存在的课自动跳过、只补缺失；每节写成「每周重复事件」（RFC 2445 `FREQ=WEEKLY`），结束于课程截止日期。
- **首次写入会弹 macOS 授权**：若失败，到「系统设置 → 隐私与安全性 → 日历 / 提醒事项 / 文件与文件夹」，勾选你的**运行终端**或 **CanvBridge.app**。选下载目录时同样需放行「文件夹」。

## 课程文件下载

- 「文件」页列出所选课程的 Canvas 文件，勾选后批量下载。
- 落盘结构：`下载目录/<课程名>/<原文件夹路径>/<文件>`（Module 文件归到 `下载目录/<课程>/<模块名>/`）。
- 重名自动加 `_2/_3…`，已存在的文件跳过。

## 测试

```bash
source .venv/bin/activate
python -m pytest          # 全量：backend 单测 + API 冒烟（不触网）
```

## 打包桌面 App（可选）

```bash
./build_app.sh            # PyInstaller：打包 backend + frontend + playwright → dist/CanvBridge.app
```

- 产物双击即用：自动起服务并打开 `http://127.0.0.1:8331`。
- App 未做 Apple 公证，首次打开被 Gatekeeper 拦截属正常：右键 → 打开（或系统设置 → 隐私与安全性 → 仍要打开）。完整指引见 [`打开步骤.md`](./打开步骤.md)。
- 若报「已损坏」：终端执行 `xattr -cr /Applications/CanvBridge.app`。

## 项目结构

```
backend/             FastAPI 后端
  main.py            路由 / 前端静态托管
  canvas_client.py   Canvas REST 客户端（分页、只读）
  banweb.py          Playwright 驱动 AIMS/Banweb + Okta 登录（无头/有头、单线程看门狗）
  llm_client.py      OpenAI 兼容 LLM 客户端（结构化 JSON 提取，DeepSeek 特殊处理）
  files_downloader.py 课程文件下载
  apple_script.py    写 Apple 日历 / 提醒（osascript）
  credentials.py     AIMS 凭据 → macOS 钥匙串
frontend/            无框架单页（index.html / app.js / i18n.js / app.css）
tests/               pytest 单测
run_app.py           源码入口：起服务 + 自动开浏览器
CanvBridge.spec      PyInstaller 打包描述
build_app.sh         打包脚本
```

## 常见问题

**Q：自动登录 / 抓课表时一直转圈或没反应？**
单线程浏览器执行器若被某个页面操作卡住，会阻塞后续请求最多 180 秒，之后看门狗自动重建并恢复。可稍等 1–3 分钟，或直接重启 `run_app.py`。

**Q：报「未安装 Playwright / 找不到 Chrome」？**
确认 `pip install -r requirements.txt` 成功、并已安装 Google Chrome。

**Q：写日历 / 提醒失败？**
到「系统设置 → 隐私与安全性 → 日历 / 提醒事项」，把运行终端（或 CanvBridge.app）勾上。

**Q：换了浏览器 / 清了缓存，课程不见了？**
Canvas Token 等在 localStorage，清数据即失效，重新在设置里填即可；AIMS 密码仍在钥匙串，不受影响。

**Q：打包的 App 打不开 / 显示未验证开发者？**
见 [`打开步骤.md`](./打开步骤.md)；`已损坏` 用 `xattr -cr` 修复。

**Q：这是要联网上传我的数据吗？**
不会。同步目标是你本机的 Apple 日历；仅在你触发抓取/总结时，代码直连 CityU（AIMS/Canvas）与你配置的 LLM。所有 token/密码不出本机（LLM Key 只发给你自己配的 LLM 服务）。
