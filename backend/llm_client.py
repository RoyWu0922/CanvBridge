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

def _schema_instructions(language: str) -> str:
    lang = "Chinese" if language == "zh" else "English"
    return (
        'Produce a JSON object with EXACTLY this structure:\n'
        '{\n'
        '  "course_name": "<course_name>",\n'
        f'  "summary": "<{lang} summary>",\n'
        '  "calendar_events": [{"title": "...", "start": "YYYY-MM-DDTHH:MM:SS", '
        '"end": "YYYY-MM-DDTHH:MM:00", "location": "...", "notes": "..."}],\n'
        '  "reminders": [{"title": "...", "due_date": "YYYY-MM-DDTHH:MM:SS", "notes": "..."}]\n'
        '}\n'
        'Rules:\n'
        f'- summary: write in {lang}, covering the key points in a few sentences.\n'
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


def _call_chat(base_url: str, api_key: str, model: str, prompt: str) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(
        url, json=payload,
        headers={"Authorization": f"Bearer {api_key}"}, timeout=120,
    )
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


def extract_course_summary(base_url: str, api_key: str, model: str,
                           course_name: str, announcements: list[dict],
                           language: str = "zh") -> dict:
    """返回 {course_name, summary, calendar_events, reminders}。

    输出无法解析时（重试一次后）回退到公告原文，并附 warning 标记。
    """
    fallback = {
        "course_name": course_name,
        "summary": "\n".join(
            f"- {a.get('title', '')}: {a.get('message', '')[:300]}" for a in announcements
        ) or "(无公告)",
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
            parsed.setdefault("summary", "")
            parsed.setdefault("calendar_events", [])
            parsed.setdefault("reminders", [])
            return parsed
        except (requests.RequestException, ValueError, KeyError, TypeError, RuntimeError):
            continue
    return fallback
