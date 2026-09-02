"""Canvas REST API 客户端（本应用只用读操作）。"""
from __future__ import annotations

import re
from datetime import datetime, timezone  # 放到文件顶部现有 import 区
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


def list_courses(canvas_url: str, token: str, include_scores: bool = False) -> list[dict]:
    """返回用户当前在修的课程 [{"id", "name", "course_code"}]。

    course_code 是 Canvas/SIS 里的课程代码（如 "CS1315A"），前端用它把
    Banweb 课表与 Canvas 课程按「字母简称 + 4 位数字」对齐（忽略 a/c 后缀）。
    enrollment_state 用 current_and_invited 而不是 active：新加入的课程在
    学生接受邀请前 enrollment 状态是 invited/invitation_pending，只查 active
    会把这类课程静默漏掉（Canvas 页面上能看到 7 门、这里只返回 6 门）。
    current_and_invited = 当前学期 active + invited 的选课，不含已结业课程。
    include_scores=True 时请求带 include[]=enrollments&include[]=total_scores，
    每课附加 current_score / final_score（数字或 None，课程未给分时为 None）。
    """
    base = canvas_url.rstrip("/")
    params = {"enrollment_state": "current_and_invited", "per_page": 100}
    if include_scores:
        params["include[]"] = ["enrollments", "total_scores"]
    with requests.Session() as s:
        data = _paginate(s, f"{base}/api/v1/courses", params, token)
    out = []
    for c in data:
        item = {
            "id": c["id"],
            "name": c.get("name", f"Course {c['id']}"),
            "course_code": c.get("course_code", "") or "",
        }
        if include_scores:
            item["current_score"] = _course_score(c, "current")
            item["final_score"] = _course_score(c, "final")
        out.append(item)
    return out


def _course_score(course: dict, kind: str):
    """从 enrollments[0].grades 或 total_scores 取当前/期末分数，缺失返回 None。

    include[]=enrollments 返回 enrollments[].grades.{current,final}_score；
    include[]=total_scores 返回 total_scores（list）的 computed_{current,final}_score。
    两者都缺（课程未给分）→ None。
    """
    grades = (course.get("enrollments") or [None])[0]
    if grades:
        g = grades.get("grades") or {}
        v = g.get("current_score" if kind == "current" else "final_score")
        if v is not None:
            return v
    totals = course.get("total_scores") or []
    if isinstance(totals, dict):
        totals = [totals]
    for t in totals:
        v = t.get("computed_current_score" if kind == "current" else "computed_final_score")
        if v is not None:
            return v
    return None


def get_course(canvas_url: str, token: str, course_id: int) -> dict:
    """返回单课程详情 {id, name, syllabus_text, teachers}。

    syllabus_body 用 strip_html 转纯文本（前端只渲染纯文本，不碰原始 HTML）。
    teachers 取 TeacherEnrollment + TaEnrollment 的 user 名字。
    syllabus 缺失（空课程）返回空串；认证错误抛 CanvasError。
    """
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        resp = s.get(
            f"{base}/api/v1/courses/{course_id}",
            params={"include[]": "syllabus_body"}, headers=_headers(token), timeout=30,
        )
        if resp.status_code == 401:
            raise CanvasError("Canvas token 无效或已过期 (HTTP 401)")
        if resp.status_code == 403:
            raise CanvasError("没有权限访问该资源 (HTTP 403)")
        resp.raise_for_status()
        course = resp.json()
        teachers = _paginate(
            s, f"{base}/api/v1/courses/{course_id}/users",
            {"enrollment_type[]": ["TeacherEnrollment", "TaEnrollment"], "per_page": 100},
            token,
        )
    return {
        "id": course.get("id", course_id),
        "name": course.get("name", f"Course {course_id}"),
        "syllabus_text": strip_html(course.get("syllabus_body", "")),
        "teachers": [t.get("name", "") for t in teachers if t.get("name")],
    }


def get_assignments(canvas_url: str, token: str, course_id: int) -> list[dict]:
    """返回未截止作业 [{id, name, due_at, points_possible, html_url}]。

    无截止日期（due_at 为空）保留（详情展示用，不上日历）；
    due_at 在未来保留；已过或无法解析的丢弃。html_url 缺失时拼兜底 URL。
    """
    base = canvas_url.rstrip("/")
    now = datetime.now(timezone.utc)
    with requests.Session() as s:
        data = _paginate(
            s, f"{base}/api/v1/courses/{course_id}/assignments",
            {"per_page": 100}, token,
        )
    out = []
    for a in data:
        due = a.get("due_at")
        if due:
            try:
                due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
            except ValueError:
                continue
            if due_dt <= now:
                continue
        out.append({
            "id": a.get("id"),
            "name": a.get("name", "(untitled)"),
            "due_at": due or "",
            "points_possible": a.get("points_possible"),
            "html_url": a.get("html_url")
                or f"{base}/courses/{course_id}/assignments/{a.get('id')}",
        })
    return out


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


def get_course_files(canvas_url: str, token: str, course_id: int) -> tuple[list[dict], list[dict]]:
    """返回 (files, folders) 供下载规划使用。"""
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        files_data = _paginate(
            s, f"{base}/api/v1/courses/{course_id}/files",
            {"per_page": 100}, token,
        )
        folders_data = _paginate(
            s, f"{base}/api/v1/courses/{course_id}/folders",
            {"per_page": 100}, token,
        )
    files = [{
        "id": f["id"],
        "display_name": f.get("display_name", f.get("filename", "file")),
        "folder_id": f.get("folder_id"),
        "content_type": f.get("content-type", ""),
        "size": f.get("size", 0),
        "url": f.get("url", ""),
    } for f in files_data]
    folders = [{
        "id": fo["id"],
        "name": fo.get("name", ""),
        "parent_folder_id": fo.get("parent_folder_id"),
    } for fo in folders_data]
    return files, folders


def get_file(canvas_url: str, token: str, course_id: int, file_id: int) -> dict:
    base = canvas_url.rstrip("/")
    resp = requests.get(
        f"{base}/api/v1/courses/{course_id}/files/{file_id}",
        headers=_headers(token), timeout=30,
    )
    if resp.status_code == 401:
        raise CanvasError("Canvas token 无效或已过期 (HTTP 401)")
    resp.raise_for_status()
    return resp.json()


def download_file(canvas_url: str, token: str, file_url: str, dest_path: str) -> None:
    """流式下载 file_url 到 dest_path（自动建父目录）。"""
    dest = Path(dest_path)
    resp = requests.get(file_url, headers=_headers(token), stream=True, timeout=60)
    if resp.status_code == 401:
        raise CanvasError("Canvas token 无效或已过期 (HTTP 401)")
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                fh.write(chunk)


def get_assignments_full(canvas_url: str, token: str, course_id: int) -> list[dict]:
    """返回全部作业（含已截止）与提交分数 [{id, name, due_at, points_possible, html_url, score, submitted}]。

    include[]=submission 让每作业带 submission 对象；score 取 submission.score
    （无提交 None），submitted = 有 submitted_at。成绩明细用，不筛未来。
    """
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        data = _paginate(
            s, f"{base}/api/v1/courses/{course_id}/assignments",
            {"per_page": 100, "include[]": "submission"}, token,
        )
    out = []
    for a in data:
        sub = a.get("submission") or {}
        out.append({
            "id": a.get("id"),
            "name": a.get("name", "(untitled)"),
            "due_at": a.get("due_at") or "",
            "points_possible": a.get("points_possible"),
            "html_url": a.get("html_url")
                or f"{base}/courses/{course_id}/assignments/{a.get('id')}",
            "score": sub.get("score"),
            "submitted": bool(sub.get("submitted_at")),
        })
    return out


def get_todo(canvas_url: str, token: str) -> list[dict]:
    """返回归一化待办 [{id, type, title, course_id, course_name, due_at, html_url, points_possible, overdue}]。

    调 /api/v1/users/self/todo（含已过期项，无需课程参数）。due_at 解析失败按无截止
    处理；overdue = 有截止且早于现在。按 due_at 升序，无截止排最后。
    """
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        data = _paginate(s, f"{base}/api/v1/users/self/todo", {"per_page": 100}, token)
    now = datetime.now(timezone.utc)
    out = []
    for item in data:
        asg = item.get("assignment") or {}
        if not asg:
            continue
        due_raw = asg.get("due_at")
        due_dt = None
        if due_raw:
            try:
                due_dt = datetime.fromisoformat(due_raw.replace("Z", "+00:00"))
            except ValueError:
                due_dt = None
        out.append({
            "id": asg.get("id"),
            "type": item.get("type") or "Assignment",
            "title": asg.get("name", "(untitled)"),
            "course_id": asg.get("course_id"),
            "course_name": item.get("context_name") or "",
            "due_at": due_raw or "",
            "html_url": asg.get("html_url") or "",
            "points_possible": asg.get("points_possible"),
            "overdue": bool(due_dt and due_dt < now),
        })
    out.sort(key=lambda x: (x["due_at"] == "", x["due_at"]))
    return out


def get_calendar_events(canvas_url: str, token: str, course_ids: list[int],
                        start_date: str, end_date: str) -> list[dict]:
    """按课程拉日历事件，只保留 type=="event" 的一次性事件。

    返回 [{id, title, course_id, start_at, end_at, location_name, html_url}]。
    type=="assignment" 的日历事件（作业截止在日历上的展示）与待办重复，排除。
    """
    base = canvas_url.rstrip("/")
    with requests.Session() as s:
        data = _paginate(
            s, f"{base}/api/v1/calendar_events",
            {"context_codes[]": [f"course_{cid}" for cid in course_ids],
             "start_date": start_date, "end_date": end_date, "per_page": 100}, token,
        )
    out = []
    for ev in data:
        if (ev.get("type") or "") != "event":
            continue
        match = re.fullmatch(r"course_(\d+)", ev.get("context_code") or "")
        out.append({
            "id": ev.get("id"),
            "title": ev.get("title", "(untitled)"),
            "course_id": int(match.group(1)) if match else None,
            "start_at": ev.get("start_at") or "",
            "end_at": ev.get("end_at") or ev.get("start_at") or "",
            "location_name": ev.get("location_name") or "",
            "html_url": ev.get("html_url") or "",
        })
    out.sort(key=lambda x: x["start_at"])
    return out
