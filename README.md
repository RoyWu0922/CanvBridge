# CanvBridge

Campus course assistant — pulls your university timetable and Canvas into one place: week-view schedule, syllabus &amp; assignment details, and Apple Calendar sync.

从学校 AIMS (Banweb) 课表 + Canvas 拉取课程数据，统一在一个本地页面：周视图课表、课程详情（syllabus / 作业 / 教授 / 学分 / 学期节奏）、公告总结、文件下载，并同步到 Apple 日历。

## 功能

1. **课表（AIMS / Banweb）**：自动抓取课表 → 周视图日历预览 → 按标题查重，把缺失的课以**每周重复事件**写入 Apple 日历；无固定时间的课灰显标注
2. **自动登录**：EID + 密码存入 macOS 钥匙串（`cityu_aims_login`），headless Chrome 自动完成 Okta 两步登录，无需弹窗
3. **课程详情**：点课表任意课的 ⓘ → 课程代码/CRN/学分、学期节奏（共几周/还剩几周）、每节讲师（主讲标注）、Canvas 首页/文件/作业链接、syllabus + AI 总结
4. **作业 due 标注**：Canvas 未截止作业在课表周视图对应日期琥珀色标注，点击直达提交页
5. **公告总结**：按时间段拉取所选课程公告，LLM 生成中文总结，按课程筛选
6. **课程文件下载**：下载 Canvas Files → 按「科目/原文件夹结构」分类存到本地（下载目录可用原生文件夹选择框选取）
7. **i18n**：界面中英切换，跟随系统深浅色

## 快速开始

```bash
cd School_Calendar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>，在「设置」填写：

- Canvas 实例 URL（如 `https://xxx.instructure.com`）与 API Token（Canvas → Account → Settings → Approved Integrations → New Access Token）
- AIMS 登录 EID 与密码（存 macOS 钥匙串，不进 git、不返回前端）
- LLM Base URL / API Key / 模型名（OpenAI 兼容格式，可选，用于公告总结 / syllabus 总结）

## 课表（AIMS / Banweb）

「课表」tab 自动抓取，流程：

1. 首次点开：填写一次 EID + 密码，存入钥匙串；之后自动登录
2. 选学期 → 抓取课表 → 周视图预览（无固定时间的课灰显标注，不可写入）
3. 选目标日历 → 写入：按事件标题查重，**已存在的课自动跳过，只补缺失**；每节写入「每周重复事件」到课程结束日期
4. 预览结果保存在浏览器 localStorage，刷新/切换 tab 不丢

要求本机已安装 Google Chrome。

## 权限

首次写入日历/提醒时，macOS 会弹出授权。若失败，到「系统设置 → 隐私与安全性 → 日历 / 提醒事项」勾选你的终端或运行环境。

## 安全

- Canvas Token 与 LLM API key 只保存在浏览器 localStorage 与后端内存；AIMS 密码只存 macOS 钥匙串
- 服务仅监听 127.0.0.1
- 「课表」写入已自动按标题查重，只补缺失

## 测试

```bash
python -m pytest
```
