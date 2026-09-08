"""OpenAI 兼容 chat completions 客户端，用于公告总结与日程提取。"""
from __future__ import annotations

import json
import re
from typing import Any

import requests

_SYSTEM = (
    "You extract structured schedule information from course announcements. "
    "Respond only with the requested JSON object, never with markdown."
)

_SYSTEM_TEXT = (
    "You are a helpful academic assistant. Respond with concise plain text, "
    "never markdown, never JSON."
)

def _schema_instructions(language: str) -> str:
    lang = "Chinese" if language == "zh" else "English"
    return (
        'Produce a JSON object with EXACTLY this structure:\n'
        '{\n'
        '  "course_name": "<course_name>",\n'
        f'  "summaries": ["<{lang} summary of the 1st announcement>", '
        '"<{lang} summary of the 2nd announcement>", ...],\n'
        '  "calendar_events": [{"title": "...", "start": "YYYY-MM-DDTHH:MM:SS", '
        '"end": "YYYY-MM-DDTHH:MM:00", "location": "...", "notes": "..."}],\n'
        '  "reminders": [{"title": "...", "due_date": "YYYY-MM-DDTHH:MM:SS", "notes": "..."}]\n'
        '}\n'
        'Rules:\n'
        f'- summaries: write in {lang}; an ARRAY with EXACTLY ONE entry per announcement, '
        'in the same order the announcements are listed above (entry 1 ↔ announcement [1], '
        'entry 2 ↔ announcement [2], and so on). Each entry summarizes ONLY its own '
        'announcement in a few sentences.\n'
        '- calendar_events: ONLY items with a concrete date/time (a class, review session, '
        'exam, office hours). If only a date is given, use 23:59:00 as the end time. '
        'Location in English if mentioned, else "".\n'
        '- reminders: ONLY deadlines/due dates without a start/end period. due_date is the '
        'deadline; default to 23:59:00 if only a date is given.\n'
        '- Titles and notes in English, verbatim from the announcements where possible.\n'
        '- Return [] for calendar_events or reminders if there are none. Never invent events.\n'
        'Return ONLY the JSON object.'
    )


def _build_prompt(course_name: str, announcements: list[dict], language: str = "zh") -> str:
    lines = [
        f'You are an academic assistant. Below are announcements from the Canvas course named "{course_name}".',
        "",
    ]
    for i, a in enumerate(announcements, 1):
        lines.append(f"[{i}] {a.get('posted_at', '')} — {a.get('title', '')}")
        lines.append(a.get("message", ""))
        lines.append("")
    lines.append(_schema_instructions(language))
    return "\n".join(lines)


# DeepSeek 第一方 API。官方 2026-07-24 停用旧模型名 deepseek-chat / deepseek-reasoner，
# 需改叫 deepseek-v4-flash；V4 思考模式默认开启会让 temperature 失效、json_object 不稳
# （可空内容），结构化提取宜显式关闭。第三方网关（OpenRouter 等）目录名不在其列，原样保留。
_DEEPSEEK_HOST = "api.deepseek.com"
_LEGACY_DS_MODELS = {"deepseek-chat", "deepseek-reasoner"}


def _is_deepseek(base_url: str) -> bool:
    return _DEEPSEEK_HOST in (base_url or "").lower()


def _effective_model(base_url: str, model: str) -> str:
    model = (model or "").strip()
    if _is_deepseek(base_url) and model in _LEGACY_DS_MODELS:
        return "deepseek-v4-flash"
    return model


def _call_chat(base_url: str, api_key: str, model: str, prompt: str,
               json_mode: bool = True) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    model = _effective_model(base_url, model)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM if json_mode else _SYSTEM_TEXT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if _is_deepseek(base_url):
        # 显式关掉思考，保持旧 deepseek-chat 的非思考语义，采样参数与 json_object 才可靠。
        payload["thinking"] = {"type": "disabled"}
    resp = requests.post(
        url, json=payload,
        headers={"Authorization": f"Bearer {api_key}"}, timeout=120,
    )
    if not resp.ok:
        # 把上游真实原因带进报错：400 多为模型别名被退役 / 参数不被支持，正文有说明
        body = (resp.text or "")[:400]
        raise requests.HTTPError(f"{resp.status_code} {resp.reason} from {url} — {body}")
    resp.raise_for_status()
    data = resp.json()
    if not data.get("choices"):
        raise ValueError("LLM 响应无 choices")
    return data["choices"][0]["message"].get("content") or ""


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _per_summaries(announcements: list[dict]) -> list[str]:
    """逐条回退摘要：每条公告一行「标题: 原文摘录」，无公告给占位。"""
    out = [f"- {a.get('title', '')}: {a.get('message', '')[:300]}" for a in announcements]
    return out or ["(无公告)"]


def extract_course_summary(base_url: str, api_key: str, model: str,
                           course_name: str, announcements: list[dict],
                           language: str = "zh") -> dict:
    """返回 {course_name, summaries, calendar_events, reminders}。

    summaries 与 announcements 逐条一一对应（顺序相同）；calendar_events/reminders
    仍按整门课聚合。输出无法解析时（重试一次后）回退到公告原文摘录并附 warning。
    """
    fallback = {
        "course_name": course_name,
        "summaries": _per_summaries(announcements),
        "calendar_events": [],
        "reminders": [],
        "warning": "总结失败，已展示公告原文",
    }
    prompt = _build_prompt(course_name, announcements, language)
    for _attempt in range(2):
        try:
            content = _call_chat(base_url, api_key, model, prompt)
            parsed = _parse_json(content)
            if not isinstance(parsed, dict):
                raise ValueError("LLM 返回非 JSON 对象")
            parsed.setdefault("course_name", course_name)
            parsed.setdefault("calendar_events", [])
            parsed.setdefault("reminders", [])
            # summaries 需与公告对齐：模型漏条/非字符串时用原文摘录补齐
            base = _per_summaries(announcements)
            got = parsed.get("summaries")
            if isinstance(got, list) and announcements:
                parsed["summaries"] = [
                    got[i].strip() if (i < len(got) and isinstance(got[i], str) and got[i].strip())
                    else base[i]
                    for i in range(len(announcements))
                ]
            else:
                parsed["summaries"] = base
            return parsed
        except (requests.RequestException, ValueError, KeyError, TypeError, RuntimeError):
            continue
    return fallback


_MAX_SYLLABUS = 20000


def _syllabus_schema(language: str) -> str:
    """syllabus 提取的结构化指令：要点总结 + 可写日历的日程/截止事项。"""
    lang = "Chinese" if language == "zh" else "English"
    return (
        'Produce a JSON object with EXACTLY this structure:\n'
        '{\n'
        f'  "summary": "<concise {lang} bullet-point summary>",\n'
        '  "calendar_events": [{"title": "...", "start": "YYYY-MM-DDTHH:MM:SS", '
        '"end": "YYYY-MM-DDTHH:MM:00", "location": "...", "notes": "..."}],\n'
        '  "reminders": [{"title": "...", "due_date": "YYYY-MM-DDTHH:MM:SS", "notes": "..."}]\n'
        '}\n'
        'Rules:\n'
        f'- summary: in {lang}, as concise bullet points. Cover course objectives, grading '
        'scheme, key deadlines and assessments, and anything a student must know.\n'
        '- calendar_events: ONLY items with a concrete date/time or a single dated event '
        '(exam dates, special sessions, reading-week holidays). If only a date is given, '
        'use 09:00:00 as start and 23:59:00 as end. Location if mentioned, else "".\n'
        '- reminders: ONLY deadlines and due dates without a start/end period (assignment '
        'due dates, registration or add/drop deadlines). due_date is the deadline; default '
        'to 23:59:00 if only a date is given.\n'
        '- Titles and notes verbatim from the syllabus where possible.\n'
        '- Return [] for calendar_events or reminders if there are none. Never invent dates '
        'that are not in the syllabus.\n'
        'Return ONLY the JSON object.'
    )


def summarize_syllabus(base_url: str, api_key: str, model: str,
                       course_name: str, syllabus_text: str,
                       language: str = "zh") -> dict:
    """返回结构化结果 {summary, calendar_events, reminders}。

    summary 为要点总结（目标语言）；calendar_events/reminders 是从 syllabus
    提取的可写日历事项。syllabus 过长截断防 token 超限；输出需能解析为 JSON，
    失败重试一次，仍失败抛异常（由端点转 ok:false，不静默降级成原文）。
    """
    if len(syllabus_text) > _MAX_SYLLABUS:
        syllabus_text = syllabus_text[:_MAX_SYLLABUS] + "\n…(已截断)"
    prompt = (
        f'You are an academic assistant. Below is the syllabus for "{course_name}". '
        f"Extract a summary and any dated schedule items exactly as instructed.\n\n"
        f"Syllabus:\n{syllabus_text}\n\n"
        + _syllabus_schema(language)
    )
    last_err: Exception | None = None
    for _attempt in range(2):
        try:
            content = _call_chat(base_url, api_key, model, prompt, json_mode=True)
            parsed = _parse_json(content)
            if not isinstance(parsed, dict):
                raise ValueError("LLM 返回非 JSON 对象")
            return {
                "summary": parsed.get("summary", ""),
                "calendar_events": parsed.get("calendar_events") or [],
                "reminders": parsed.get("reminders") or [],
            }
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"Syllabus 总结失败: {last_err}") from last_err
