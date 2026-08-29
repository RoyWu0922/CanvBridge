# Canvas 课程助手

从学校 Canvas 读取课程公告，用 LLM 总结，并把日程写入 Apple 日历、待办写入提醒事项，同时下载课程 Files 到本地。

## 功能

1. 连接 Canvas（API Token），按时间段拉取所选课程公告并生成中文总结
2. 从公告提取有具体时间的日程 → 写入 Apple 日历（可选日历）
3. 从公告提取截止 DDL → 写入 Apple 提醒事项（可选列表）
4. 下载课程 Files → 按「科目/原文件夹结构」分类存到本地
5. 总结/提取走 OpenAI 兼容 LLM（DeepSeek/Kimi/通义/智谱/Ollama）

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
- LLM Base URL / API Key / 模型名（OpenAI 兼容格式）

## 权限

首次写入日历/提醒时，macOS 会弹出授权。若失败，到「系统设置 → 隐私与安全性 → 日历 / 提醒事项」勾选你的终端或运行环境。

## 安全

- Token 与 API key 只保存在浏览器 localStorage 与后端内存，不写磁盘、不入 git
- 服务仅监听 127.0.0.1
- 注意：重复点击「写入」会创建重复的日历事件/提醒

## 测试

```bash
python -m pytest
```
