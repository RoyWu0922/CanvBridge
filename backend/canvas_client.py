"""Canvas REST API 客户端（本应用只用读操作）。"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests

USER_AGENT = "canvas-calendar-helper/1.0"


class CanvasError(RuntimeError):
    """Canvas 返回错误时抛给上层展示。"""


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}


def _next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        section = part.split(";")
        if len(section) < 2:
            continue
        url = section[0].strip().strip("<>")
        rel = ""
        for attr in section[1:]:
            if "rel=" in attr:
                rel = attr.split("=", 1)[1].strip().strip('"')
        if rel == "next":
            return url
    return None


def _paginate(session: requests.Session, url: str, params: dict[str, Any],
              token: str) -> list[dict]:
    """沿 Canvas 的 Link 头翻页，收集所有结果。"""
    results: list[dict] = []
    next_url: str | None = url
    current_params: dict[str, Any] = params
    while next_url:
        resp = session.get(next_url, params=current_params, headers=_headers(token))
        if resp.status_code == 401:
            raise CanvasError("Canvas token 无效或已过期 (HTTP 401)")
        if resp.status_code == 403:
            raise CanvasError("没有权限访问该资源 (HTTP 403)")
        resp.raise_for_status()
        results.extend(resp.json())
        next_url = _next_link(resp.headers.get("Link", ""))
        current_params = {}  # Link 头里的 next URL 已带全部 query 参数
    return results


def list_courses(canvas_url: str, token: str) -> list[dict]:
    """返回用户当前在修的课程 [{"id": int, "name": str}]。"""
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        data = _paginate(
            s, f"{base}/api/v1/courses",
            {"enrollment_state": "active", "per_page": 100}, token,
        )
    return [
        {"id": c["id"], "name": c.get("name", f"Course {c['id']}")}
        for c in data
    ]


class _TextExtractor(HTMLParser):
    """抽取 HTML 文本，块级标签处换行。"""

    _BLOCK = {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html or "")
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def get_announcements(canvas_url: str, token: str, course_ids: list[int],
                      start_date: str, end_date: str) -> dict[int, list[dict]]:
    """按课程分组返回公告；无公告的课程不在结果中出现。

    message 已剥离 HTML。日期格式 YYYY-MM-DD。
    """
    base = canvas_url.rstrip("/")
    params = {
        "context_codes[]": [f"course_{cid}" for cid in course_ids],
        "start_date": start_date,
        "end_date": end_date,
        "per_page": 100,
    }
    with requests.Session() as s:
        data = _paginate(s, f"{base}/api/v1/announcements", params, token)
    grouped: dict[int, list[dict]] = {}
    for item in data:
        match = re.fullmatch(r"course_(\d+)", item.get("context_code", ""))
        if not match:
            continue
        cid = int(match.group(1))
        grouped.setdefault(cid, []).append({
            "id": item.get("id"),
            "title": item.get("title", "(untitled)"),
            "message": strip_html(item.get("message", "")),
            "posted_at": item.get("posted_at", ""),
        })
    return grouped
