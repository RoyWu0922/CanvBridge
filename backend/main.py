"""FastAPI 应用：Canvas 课程助手后端。"""
from __future__ import annotations

import itertools
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (apple_script, banweb, canvas_client, credentials, files_downloader,
               library_areas, library_client, library_query, llm_client, mail_client)

app = FastAPI(title="CanvBridge")

# 前端目录定位：PyInstaller 打包后 __file__ 指向解压目录，须用 _MEIPASS；
# 源码运行时（_MEIPASS 不存在）回退到仓库内的 frontend。
_BUNDLE_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
FRONTEND_DIR = _BUNDLE_BASE / "frontend"
FRONTEND = FRONTEND_DIR / "index.html"

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# 开发缓存策略：/static 一律 no-cache（每次重新校验，改了前端文件立即生效）。
# 浏览器对没有 Cache-Control 的静态资源会按 Last-Modified 做启发式缓存，
# 改完前端仍显示旧版（旧 i18n 键、旧链接色）就是它导致的。
@app.middleware("http")
async def _static_no_cache(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class CanvasConfig(BaseModel):
    canvas_url: str
    canvas_token: str


class LLMConfig(BaseModel):
    llm_base_url: str
    llm_api_key: str
    llm_model: str


class MailListRequest(BaseModel):
    days: int = 7


class MailSummaryRequest(LLMConfig):
    days: int = 7
    language: str = "zh"


class SyncRequest(CanvasConfig):
    course_ids: list[int]
    start_date: str
    end_date: str


class SummarizeAnnouncementsRequest(LLMConfig):
    canvas_url: str
    canvas_token: str
    course_id: int
    course_name: str
    announcements: list[dict]
    language: str = "zh"


class AddEventRequest(BaseModel):
    calendar_name: str
    title: str
    start: str
    end: str
    location: str = ""
    notes: str = ""
    alert_minutes: int | None = None


class AddReminderRequest(BaseModel):
    list_name: str
    title: str
    due_date: str
    notes: str = ""


class ListFilesRequest(CanvasConfig):
    course_ids: list[int]
    download_dir: str


class DownloadRequest(CanvasConfig):
    download_dir: str  # 落盘锚点：每条 dest_path 都会被重新锚定到该目录内
    items: list[dict]  # [{course_id, file_id, dest_path, legacy_path}]


class DownloadModuleItemRequest(CanvasConfig):
    download_dir: str
    course_id: int
    course_name: str = ""
    module_name: str = ""
    file_id: int


class ModuleFileStreamRequest(CanvasConfig):
    course_id: int
    file_id: int


class BanwebScheduleRequest(BaseModel):
    term: str


class BanwebWriteRequest(BaseModel):
    calendar_name: str
    courses: list[dict]   # 前端预览的课程块（原样传回，由后端转事件规格）
    selected: list[str]   # 勾选的 "code:section" 键
    alert_minutes: int | None = None


class AimsCredentials(BaseModel):
    username: str
    password: str


class GmailCredentials(BaseModel):
    address: str
    app_password: str


class CourseDetailRequest(BaseModel):
    canvas_url: str
    canvas_token: str
    course_id: int


class AssignmentsRequest(BaseModel):
    canvas_url: str
    canvas_token: str
    course_ids: list[int]


class GradesRequest(CanvasConfig):
    course_ids: list[int]


class CalendarEventsRequest(CanvasConfig):
    course_ids: list[int]
    start_date: str
    end_date: str


class CoursesRequest(CanvasConfig):
    """批量端点：一次拉多门课的同一类内容。"""
    course_ids: list[int]


class PageBodyRequest(CourseDetailRequest):
    """单个 Page 的正文。page_url 是 Canvas 的页面 slug（如 "course-home"）。"""
    page_url: str


class PlannerRequest(CanvasConfig):
    """Planner 聚合流。无课程维度 —— Canvas 按当前用户的全部选课聚合。"""
    start_date: str
    end_date: str


class WriteCanvasEventsRequest(BaseModel):
    calendar_name: str
    items: list[dict]   # [{title, start, end, location, notes}]
    alert_minutes: int | None = None


class WriteExamsRequest(BaseModel):
    calendar_name: str
    exams: list[dict]   # [{course, code, section, date, start, end, room, seat}]
    alert_minutes: int | None = None


class SummarizeSyllabusRequest(BaseModel):
    canvas_url: str
    canvas_token: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    course_id: int
    language: str = "zh"


class LibrarySearchRequest(LLMConfig):
    text: str = ""
    area: str | None = None
    page: int = 1
    # 第 1 页不带 parsed（后端调 LLM 解析）；第 2 页起由前端原样回传，
    # 后端**不再调 LLM** —— 否则每页的检索式都可能不同，结果集中途漂移。
    parsed: dict | None = None


@app.get("/")
def index():
    return FileResponse(FRONTEND)


@app.post("/api/test_connection")
def test_connection(req: CanvasConfig):
    try:
        return {"ok": True, "courses": canvas_client.list_courses(req.canvas_url, req.canvas_token)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/courses")
def courses(req: CanvasConfig):
    try:
        return {"ok": True, "courses": canvas_client.list_courses(req.canvas_url, req.canvas_token)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/calendars")
def calendars():
    try:
        return {"ok": True, "calendars": apple_script.list_calendars()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/reminder_lists")
def reminder_lists():
    try:
        return {"ok": True, "lists": apple_script.list_reminder_lists()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/pick_dir")
def pick_dir():
    """弹 macOS 原生文件夹选择框，返回所选 POSIX 路径。

    用户取消 → {"ok": False, "cancelled": True}（前端当无事发生）。
    """
    try:
        return {"ok": True, "path": apple_script.pick_folder()}
    except apple_script.AppleScriptError as exc:
        msg = str(exc).lower()
        if "cancel" in msg or "-128" in msg:
            return {"ok": False, "cancelled": True}
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/sync_announcements")
def sync(req: SyncRequest):
    """只同步原始公告，不做 AI 总结（总结由前端逐课调 /api/summarize_course）。"""
    try:
        announcements = canvas_client.get_announcements(
            req.canvas_url, req.canvas_token, req.course_ids,
            req.start_date, req.end_date)
        courses = canvas_client.list_courses(req.canvas_url, req.canvas_token)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    name_by_id = {c["id"]: c["name"] for c in courses}
    results = []
    for cid in req.course_ids:
        results.append({
            "course_id": cid,
            "course_name": name_by_id.get(cid, f"Course {cid}"),
            "announcements": announcements.get(cid, []),
        })
    return {"ok": True, "courses": results}


@app.post("/api/summarize_course")
def summarize_course(req: SummarizeAnnouncementsRequest):
    """对单门课程做 AI 总结（前端把已同步的原始公告传回）。"""
    try:
        r = llm_client.extract_course_summary(
            req.llm_base_url, req.llm_api_key, req.llm_model,
            req.course_name, req.announcements, language=req.language)
        r["course_id"] = req.course_id
        return {"ok": True, **r}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/course_detail")
def course_detail(req: CourseDetailRequest):
    try:
        course = canvas_client.get_course(req.canvas_url, req.canvas_token, req.course_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    # modules 属增强信息：拉取失败只报 modules_error，不拖垮详情展示
    modules, modules_error = None, None
    try:
        modules = canvas_client.get_modules(req.canvas_url, req.canvas_token, req.course_id)
    except Exception as exc:
        modules_error = str(exc)
    return {"ok": True, "course": course, "modules": modules, "modules_error": modules_error}


@app.post("/api/quizzes")
def quizzes(req: CoursesRequest):
    """逐课程拉测验。未启用测验工具的课程返回 404（实测约一半课程如此），
    按「该课无测验」静默处理，不进 errors —— 那是常态，不是故障。"""
    by_course: dict[int, list] = {}
    errors: dict[int, str] = {}
    for cid in req.course_ids:
        try:
            by_course[cid] = canvas_client.get_quizzes(req.canvas_url,
                                                       req.canvas_token, cid)
        except canvas_client.CanvasToolDisabled:
            by_course[cid] = []
        except Exception as exc:
            by_course[cid] = []
            errors[cid] = str(exc)
    return {"ok": True, "by_course": by_course, "errors": errors}


@app.post("/api/discussions")
def discussions(req: CoursesRequest):
    """逐课程拉讨论区帖子。错误隔离口径与 /api/quizzes 一致。"""
    by_course: dict[int, list] = {}
    errors: dict[int, str] = {}
    for cid in req.course_ids:
        try:
            by_course[cid] = canvas_client.get_discussion_topics(
                req.canvas_url, req.canvas_token, cid)
        except canvas_client.CanvasToolDisabled:
            by_course[cid] = []
        except Exception as exc:
            by_course[cid] = []
            errors[cid] = str(exc)
    return {"ok": True, "by_course": by_course, "errors": errors}


@app.post("/api/pages")
def pages(req: CourseDetailRequest):
    """单课程 Pages 列表（不含正文）。未启用 Pages 工具 → 空列表，不算错误。"""
    try:
        return {"ok": True, "pages": canvas_client.get_pages(
            req.canvas_url, req.canvas_token, req.course_id)}
    except canvas_client.CanvasToolDisabled:
        return {"ok": True, "pages": []}
    except Exception as exc:
        return {"ok": False, "pages": [], "error": str(exc)}


@app.post("/api/page_body")
def page_body(req: PageBodyRequest):
    """单个 Page 正文。按需拉取 —— 列表阶段不带头文，避免 N+1。"""
    try:
        return {"ok": True, "page": canvas_client.get_page_body(
            req.canvas_url, req.canvas_token, req.course_id, req.page_url)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/planner")
def planner(req: PlannerRequest):
    """Canvas Planner 聚合流。不做日期过滤（见 canvas_client.get_planner_items）。"""
    try:
        return {"ok": True, "items": canvas_client.get_planner_items(
            req.canvas_url, req.canvas_token, req.start_date, req.end_date)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/assignments")
def assignments(req: AssignmentsRequest):
    """逐课程拉未截止作业；单门失败进 errors（by_course 里该门为 []），不拖垮整批。"""
    by_course: dict[int, list] = {}
    errors: dict[int, str] = {}
    for cid in req.course_ids:
        try:
            by_course[cid] = canvas_client.get_assignments(
                req.canvas_url, req.canvas_token, cid)
        except Exception as exc:
            by_course[cid] = []
            errors[cid] = str(exc)
    return {"ok": True, "by_course": by_course, "errors": errors}


@app.post("/api/grades")
def grades(req: GradesRequest):
    """课程总评 + 作业明细；单门作业失败进 errors（该门 assignments=[]），不拖垮整批。"""
    try:
        courses = canvas_client.list_courses(req.canvas_url, req.canvas_token, include_scores=True)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    by_id = {c["id"]: c for c in courses}
    results = []
    errors = {}
    for cid in req.course_ids:
        c = by_id.get(cid)
        if c is None:
            results.append({"course_id": cid, "course_name": f"Course {cid}",
                            "current_score": None, "final_score": None, "assignments": []})
            continue
        try:
            asg = canvas_client.get_assignments_full(req.canvas_url, req.canvas_token, cid)
        except Exception as exc:
            asg = []
            errors[cid] = str(exc)
        results.append({
            "course_id": cid, "course_name": c["name"],
            "current_score": c.get("current_score"), "final_score": c.get("final_score"),
            "assignments": asg,
        })
    return {"ok": True, "courses": results, "errors": errors}


@app.post("/api/todo")
def todo(req: CanvasConfig):
    try:
        return {"ok": True, "items": canvas_client.get_todo(req.canvas_url, req.canvas_token)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/calendar_events")
def calendar_events(req: CalendarEventsRequest):
    try:
        events = canvas_client.get_calendar_events(
            req.canvas_url, req.canvas_token, req.course_ids,
            req.start_date, req.end_date)
        return {"ok": True, "events": events}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _to_local_naive(iso: str) -> str:
    """带时区/Z 后缀的 ISO → 本地无时区 ISO（秒归零）；纯本地时间原样返回。"""
    iso = (iso or "").strip()
    if not iso:
        return iso
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if dt.tzinfo is None:
        return iso
    return dt.astimezone().strftime("%Y-%m-%dT%H:%M:00")


def _write_one_off(calendar_name: str, items: list[dict], alert_minutes: int | None) -> dict:
    """共享一次性事件写入：按 标题+开始时间 去重（Task 6 写考试复用）。

    items: [{title, start, end, location, notes}]。find_events 按标题前缀读回既有
    事件，比较 (summary==标题 且 start==开始时间) 判已存在→跳过。返回
    {items:[{title,status,error?}], created, exists, errors}。
    """
    created = exists = errors = 0
    out = []
    for item in items:
        title = (item.get("title") or "").strip()
        start = _to_local_naive(item.get("start"))
        end = _to_local_naive(item.get("end")) or start
        if not title or not start:
            errors += 1
            out.append({"title": title or (item.get("title") or ""), "status": "error",
                        "error": "缺标题或开始时间"})
            continue
        try:
            existing = apple_script.find_events(calendar_name, title)
            if any(ev["summary"] == title and ev["start"] == start for ev in existing):
                exists += 1
                out.append({"title": title, "status": "exists"})
                continue
            apple_script.add_calendar_event(
                calendar_name, title, start, end,
                item.get("location") or "", item.get("notes") or "", alert_minutes)
            created += 1
            out.append({"title": title, "status": "created"})
        except Exception as exc:
            errors += 1
            out.append({"title": title, "status": "error", "error": str(exc)})
    return {"items": out, "created": created, "exists": exists, "errors": errors}


@app.post("/api/write_canvas_events")
def write_canvas_events(req: WriteCanvasEventsRequest):
    try:
        res = _write_one_off(req.calendar_name, req.items, req.alert_minutes)
        return {"ok": True, **res}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _exam_to_event(exam: dict) -> dict:
    """考试块 → 一次性事件规格 {title, start, end, location, notes}。"""
    code = (exam.get("code") or exam.get("course") or "Exam").strip()
    title = f"{code} 考试"
    date = (exam.get("date") or "").strip()
    start = (exam.get("start") or "").strip()
    end = (exam.get("end") or "").strip()
    start_iso = f"{date}T{start}:00" if date and start else ""
    end_iso = f"{date}T{end}:00" if date and end else start_iso
    loc = " ".join(x for x in (exam.get("room"), exam.get("seat")) if x).strip()
    return {"title": title, "start": start_iso, "end": end_iso,
            "location": loc, "notes": exam.get("course") or ""}


@app.post("/api/banweb/exams")
def banweb_exams():
    try:
        term_label, exams = banweb.get_exams()
        return {"ok": True, "term_label": term_label, "exams": exams}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/banweb/write_exams")
def banweb_write_exams(req: WriteExamsRequest):
    try:
        items = [_exam_to_event(exam) for exam in req.exams]
        res = _write_one_off(req.calendar_name, items, req.alert_minutes)
        return {"ok": True, **res}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/summarize_syllabus")
def summarize_syllabus(req: SummarizeSyllabusRequest):
    try:
        course = canvas_client.get_course(req.canvas_url, req.canvas_token, req.course_id)
        result = llm_client.summarize_syllabus(
            req.llm_base_url, req.llm_api_key, req.llm_model,
            course["name"], course["syllabus_text"], language=req.language)
        return {"ok": True, **result}   # summary + calendar_events + reminders
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/add_calendar_event")
def add_event(req: AddEventRequest):
    try:
        apple_script.add_calendar_event(
            req.calendar_name, req.title, req.start, req.end,
            req.location, req.notes, req.alert_minutes)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/add_reminder")
def add_reminder(req: AddReminderRequest):
    try:
        apple_script.add_reminder(req.list_name, req.title, req.due_date, req.notes)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/list_files")
def list_files(req: ListFilesRequest):
    try:
        courses = canvas_client.list_courses(req.canvas_url, req.canvas_token)
    except Exception:
        courses = []
    name_by_id = {c["id"]: c["name"] for c in courses}
    results = []
    for cid in req.course_ids:
        name = name_by_id.get(cid, f"Course {cid}")
        try:
            files, folders = canvas_client.get_course_files(req.canvas_url, req.canvas_token, cid)
            planned = files_downloader.plan_downloads(req.download_dir, name, files, folders)
            by_id = {p["file_id"]: p for p in planned}
            results.append({
                "course_id": cid, "name": name,
                "files": [{
                    "file_id": f["id"], "display_name": f["display_name"],
                    # folder_id 前端建树要用（把文件挂到对应文件夹节点）。
                    # Canvas 不回该字段时取 None —— 前端按「挂根层」兜底（spec §5.1）。
                    "folder_id": f.get("folder_id"),
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
        except canvas_client.CanvasError as exc:
            # Canvas 对文件区为空 / 未对学生开放的课程返回 403。这里按「暂无文件」处理，
            # 前端灰色弱提示，不再把它当权限错误红字吓人。真正的异常照旧走 error。
            msg = str(exc)
            if "HTTP 403" in msg:
                results.append({"course_id": cid, "name": name, "files": [], "folders": [],
                                "no_files": True})
            else:
                results.append({"course_id": cid, "name": name, "files": [], "folders": [],
                                "error": msg})
        except Exception as exc:
            results.append({"course_id": cid, "name": name, "files": [], "folders": [],
                            "error": str(exc)})
    return {"ok": True, "courses": results}


@app.post("/api/download_files")
def download_files(req: DownloadRequest):
    files_by_id: dict[int, dict] = {}
    for item in req.items:
        try:
            info = canvas_client.get_file(req.canvas_url, req.canvas_token,
                                          item["course_id"], item["file_id"])
            files_by_id[item["file_id"]] = info
        except Exception:
            continue
    planned = [{"file_id": i["file_id"], "dest_path": i["dest_path"],
                "legacy_path": i.get("legacy_path", "")} for i in req.items]
    return files_downloader.download_items(req.canvas_url, req.canvas_token,
                                           files_by_id, planned, req.download_dir)


@app.post("/api/download_module_item")
def download_module_item(req: DownloadModuleItemRequest):
    """下载单个模块 File 条目到 下载目录/课程/模块/文件名；已存在则跳过。

    模块 File 条目的 file_id 即 Canvas 文件 id：先 get_file 拿到真实文件名与
    下载地址，再经下载目录规划目标路径。返回 dest_path 与是否命中已存在。
    """
    try:
        info = canvas_client.get_file(req.canvas_url, req.canvas_token,
                                      req.course_id, req.file_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    display = (info.get("display_name") or info.get("filename")
               or f"file_{req.file_id}")
    course_name = req.course_name or f"Course {req.course_id}"
    dest = files_downloader.module_dest(req.download_dir, course_name,
                                        req.module_name, display)
    if dest.exists():
        return {"ok": True, "saved": True, "dest_path": str(dest)}
    try:
        if not info.get("url"):
            return {"ok": False, "error": "缺少下载地址"}
        canvas_client.download_file(req.canvas_url, req.canvas_token,
                                    info["url"], str(dest))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "saved": False, "dest_path": str(dest)}


@app.post("/api/module_file_stream")
def module_file_stream(req: ModuleFileStreamRequest):
    """以 inline 方式把模块 File 条目本体回传给前端（内联预览，不触发下载）。

    Canvas 文件对象自带的 url 是带 download_frd 的下载直链——浏览器新标签打开会
    被 Canvas 判定为附件下载，无法内联渲染。这里由后端用 token 流式取回文件字节，
    以文件真实 content-type 回传；前端 fetch→blob 后让浏览器内置 PDF 查看器渲染。
    失败路径一律回 JSON {ok:false,error}。
    """
    try:
        info = canvas_client.get_file(req.canvas_url, req.canvas_token,
                                      req.course_id, req.file_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    info = info or {}
    url = info.get("url") or ""
    if not url:
        return {"ok": False, "error": "缺少下载地址"}
    content_type = info.get("content-type") or "application/octet-stream"
    low_name = ((info.get("display_name") or info.get("filename") or "")).lower()
    if content_type == "application/octet-stream" and low_name.endswith(".pdf"):
        content_type = "application/pdf"
    try:
        it = canvas_client.stream_file(req.canvas_url, req.canvas_token, url)
        first = next(it)                 # 提前触发连接/鉴权，失败在发响应头前暴露
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return StreamingResponse(itertools.chain([first], it), media_type=content_type)


@app.post("/api/banweb/status")
def banweb_status():
    """AIMS 登录态（opening / needs_login / logged_in / error）。

    任何异常都返回 {ok:false, error} JSON，而不是裸 500：前端轮询靠 status 的
    ok 字段走兜底，裸 500 只会让界面停留在上一次的状态。
    """
    try:
        return banweb.get_status()
    except Exception as exc:
        return {"ok": False, "status": "error", "error": str(exc)}


@app.post("/api/banweb/terms")
def banweb_terms():
    try:
        return {"ok": True, "terms": banweb.list_terms()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/banweb/open_login")
def banweb_open_login():
    """重新打开 AIMS 登录窗口并置前，供用户手动登录（自动登录失败的兜底）。"""
    try:
        banweb.open_login()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/banweb/credentials")
def banweb_save_credentials(req: AimsCredentials):
    """把 AIMS 账号密码存入本机钥匙串（只存本地，不返回密码）。"""
    try:
        credentials.save_credentials(req.username.strip(), req.password)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/banweb/credentials/status")
def banweb_credentials_status():
    """返回是否已存 AIMS 凭据 + 账号（永不返回密码）。"""
    try:
        username = credentials.get_username()
        return {"ok": True, "has_credentials": bool(username), "username": username}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.delete("/api/banweb/credentials")
def banweb_delete_credentials():
    """清除已存的 AIMS 账号密码。"""
    try:
        credentials.delete_credentials()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/banweb/auto_login")
def banweb_auto_login():
    """用钥匙串里已存的账号密码自动登录 AIMS（全程无窗口）。"""
    try:
        status = banweb.auto_login_from_stored()
        return {"ok": True, "status": status}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/banweb/schedule")
def banweb_schedule(req: BanwebScheduleRequest):
    try:
        return {"ok": True, "term": req.term, "courses": banweb.get_schedule(req.term)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/banweb/write_calendar")
def banweb_write(req: BanwebWriteRequest):
    """自动同步课表：检测已改时间的旧事件，删除后写入新时间。

    按课程块分组，每块用 find_events 读回现有事件，reconcile_block 对比
    (星期, 开始, 结束, 标题) 四元组：全等 → 保留；任一不同 → 先删旧再建新。
    只在选中日历、限当前课程块前缀内操作，不会误删别的课程事件。
    """
    try:
        specs = banweb.build_event_specs(req.courses, set(req.selected))
        if not specs:
            return {"ok": True, "items": [], "created": 0, "exists": 0,
                    "updated": 0, "removed": 0, "errors": 0}
        blocks: dict[str, list[dict]] = {}
        for sp in specs:
            blocks.setdefault(sp["block"], []).append(sp)
        items: list[dict] = []
        created = exists = updated = removed = errors = 0
        for block, block_specs in blocks.items():
            prefix = block.replace(":", " ")
            try:
                existing = apple_script.find_events(req.calendar_name, prefix)
            except Exception as exc:
                for sp in block_specs:
                    items.append({"key": sp["key"], "title": sp["title"],
                                  "status": "error", "error": str(exc)})
                errors += len(block_specs)
                continue
            dec = banweb.reconcile_block(block_specs, existing)
            removed += dec["removed"]
            # 整节取消的旧事件 → 隐藏（把重复截止改到过去）；AppleScript 无法真删重复系列
            for summary in dec["remove"]:
                try:
                    apple_script.hide_recurring_event(req.calendar_name, summary)
                except Exception as exc:
                    items.append({"key": block, "title": summary,
                                  "status": "error", "error": str(exc)})
                    errors += 1
            for sp in dec["create"]:
                try:
                    apple_script.add_recurring_event(
                        req.calendar_name, sp["title"], sp["start"], sp["end"],
                        sp["until"], sp["location"], sp["notes"], req.alert_minutes)
                    items.append({"key": sp["key"], "title": sp["title"], "status": "created"})
                    created += 1
                except Exception as exc:
                    items.append({"key": sp["key"], "title": sp["title"], "status": "error",
                                  "error": str(exc)})
                    errors += 1
            for upd in dec["update"]:
                sp = upd["spec"]
                try:
                    # 时间/天/标题变了 → 原地编辑旧事件，保留事件身份
                    apple_script.edit_recurring_event(
                        req.calendar_name, upd["old"]["summary"], sp["title"],
                        sp["start"], sp["end"], sp["until"])
                    items.append({"key": sp["key"], "title": sp["title"], "status": "updated",
                                  "old_time": upd["old_time"]})
                    updated += 1
                except Exception as exc:
                    items.append({"key": sp["key"], "title": sp["title"], "status": "error",
                                  "error": str(exc)})
                    errors += 1
            for sp in dec["exists"]:
                items.append({"key": sp["key"], "title": sp["title"], "status": "exists"})
                exists += 1
        return {"ok": True, "items": items, "created": created, "exists": exists,
                "updated": updated, "removed": removed, "errors": errors}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------- 图书馆检索（Ex Libris Primo，全程零登录） ----------

# `fut.result()` 的**兜底**超时（不是正常路径的一部分）。
#
# 取值依据：门内两栏既不取 `_query_lock` 也不过节流，所以一次**合法**的 `search()`
# 唯一能阻塞的地方就是那一次 HTTP 调用，而它自己的超时是
# `library_client.HTTP_TIMEOUT`（30s）。这里取 1.5 倍 —— 严格大于客户端自身的请求
# 超时，因此正常路径永远不会被它打断；而任何**意料外**的阻塞（例如将来有人在门内
# 用 `inside_query=False` 调用，worker 卡在 `_query_lock` 上）都会在 45 秒内变成
# 一次 200 + JSON 的错误返回，而不是把请求永久挂住。
_RESULT_TIMEOUT = library_client.HTTP_TIMEOUT * 1.5


@app.post("/api/library/areas")
def library_areas_endpoint():
    """学科领域清单。**拿不到不是错误**：available=False，前端整个不渲染选择器。"""
    try:
        # 抓取是「查询之外的上游调用」：套同一个门外纪律，免得冷缓存时与正在进行的
        # 检索的两栏并发（spec §9 只允许同一次查询的两栏并发）。
        with library_client.out_of_query():
            return {"ok": True, **library_areas.load_areas()}
    except Exception as exc:          # 本模块内部已尽力兜底，这里是最后一道
        return {"ok": True, "available": False, "areas": [], "error": str(exc)}


@app.post("/api/library/search")
def library_search(req: LibrarySearchRequest):
    """馆藏 + 外部数据库两块检索。全程零登录。"""
    if req.page < 1 or req.page > library_client.MAX_PAGES:
        return {"ok": False, "error": f"页码越界：{req.page}"}

    warnings: list[str] = []
    # **F-2：这两次「端点边界的取词表」调用原先都在任何 `try` 之外。** 畸形缓存
    # （合法 JSON、非空 list、但条目不是 `{"code","name"}`）会让 `load_areas` 抛错，
    # 一路冒成 HTTP 500 裸栈 —— 违反 Global Constraints「后端错误一律返回 200 + JSON」。
    # 现在各自兜住、退化成「拿不到」：area 见 spec §4.3「拿不到就当没有……
    # **绝不让它拖垮检索**」，domain 见 §4.2「选库是增强，不是主干」。
    # 注意这不是新增降级语义：两条链各自的**正常**失败（网络挂、页面改版）本来就已经
    # 被 `load_areas` / `list_domains` 自己兜成空值，这里补的是「意料外形状」那一层。
    try:
        domains = library_client.list_domains()
    except Exception:
        domains = []
    # area 词表走的是 `library_areas` 自己那条 `requests` 路径（不经过 `_get`），
    # 所以必须**显式**套上同一个门外纪律：冷缓存（官网抓不到时不会写缓存 ⇒ 每次
    # 检索都重抓）时它是一次真实的上游调用，不套就会与下面门内的两栏并发
    # —— 复审实测上游在飞峰值 3，spec §9 只允许同一次查询的两栏并发。
    try:
        with library_client.out_of_query():
            area_catalog = library_areas.load_areas()
        area_names = {a["code"]: a["name"] for a in area_catalog["areas"]}
    except Exception:
        area_names = {}

    if req.parsed is not None:
        # 翻页路径：不碰 LLM（spec §5.3）。parsed 是客户端可传的输入，必须归一化。
        parsed = library_query.validate_parsed(req.parsed, known_domains=domains)
        if not parsed["keywords"] and not parsed["synonyms"]:
            return {"ok": False, "error": "parsed 缺少 keywords"}
    else:
        if not (req.text or "").strip():
            return {"ok": False, "error": "缺少 text 或 parsed"}
        parsed, warnings = library_query.parse_natural(
            req.llm_base_url, req.llm_api_key, req.llm_model, req.text,
            area=req.area, area_names=area_names, domains=domains)
        # **不回填 `req.area` 进 `parsed["areas"]`**（spec §6）。area 只是提示词，
        # 代码拦不住 LLM 无视它；规格给的补偿正是「`parsed.areas` 随 `parsed` 回显，
        # 用户能立刻看见 LLM 有没有照做」——回填会让回显恒等于「照做了」，
        # 把补偿机制归零。详见 `library_query.parse_natural` 的同类说明。

    q = library_query.build_q(parsed)
    blocks = {}
    # **不用 `with ThreadPoolExecutor(...)`**：那个上下文管理器的 `__exit__` 等价于
    # `shutdown(wait=True)`，会把「worker 被意料外卡住」这件事重新变成挂死 ——
    # 连 `fut.result(timeout=)` 刚转出来的 200 + JSON 也会卡在 `__exit__` 里发不出去。
    pool = ThreadPoolExecutor(max_workers=2)
    stuck = False                 # True ⇔ 有 worker 没在 `_RESULT_TIMEOUT` 内返回
    try:
        # 一次「查询」= 一次 /api/library/search 请求 = 一个节流单位（spec §9：
        # 「相邻翻页请求之间 ≥1s」「不同查询之间串行」）。整段查询期间持门，
        # 别的查询被挡在门外并等到间隔满足。
        with library_client.query_scope():
            # **两个 scope 并发**（spec §3.2「并发两次请求」、§5.2 ⑤、§9「只允许
            # 两个 scope 并发」）—— 全项目唯一的一处并发，也只限同一次查询的两栏。
            # 每个 scope 各用各的 requests.Session：search(session=None) 每次自建，
            # Session 不是线程安全的，共享会把连接池搞坏。
            # `inside_query=True` 是**显式声明的事实**（不是让库去猜锁状态）：
            # 这两次调用在门内、本就该并发，不走逐调用节流；门外的调用
            # （list_domains 的探针、load_areas 的抓取）则相反 —— 它们会排队等这次
            # 查询做完。库层把 `inside_query` 定义成**必传**参数：漏传会在 worker 里
            # 立刻抛 TypeError（不是永久死锁），由下面的 `fut.result()` 带出来。
            futures = {}
            for scope, key in ((library_client.SCOPE_LOCAL, "local"),
                               (library_client.SCOPE_EXTERNAL, "external")):
                include, exclude = library_query.compile_filters(parsed, scope)
                # 窄化警告：**互斥**取值在同一 facet 里只能发一个（端点本没有
                # 「同 facet OR」；可叠加的取值不受此限，见 `compile_filters`），
                # 被丢掉的那个值必须**说出来** —— 回显里 `parsed.rtype` 仍是两个，
                # 不说用户就会以为两个都筛了。两个 scope 各调一次会报出同一个码，
                # 按码去重（前端也是按码查 `library.warn.<code>`）。
                for code in library_query.narrowing_warnings(parsed, scope):
                    if code not in warnings:
                        warnings.append(code)
                futures[key] = pool.submit(
                    library_client.search, scope, q,
                    q_include=include, q_exclude=exclude, page=req.page,
                    inside_query=True)
            # 按 scope 顺序取回（submit 顺序），失败的那个把异常原样抛出来。
            for key, fut in futures.items():
                # 显式超时 = **纵深防御**，不是正常路径的一部分（正常路径下 worker
                # 最久也就跑一次 `HTTP_TIMEOUT`，见 `_RESULT_TIMEOUT`）。
                try:
                    blocks[key] = fut.result(timeout=_RESULT_TIMEOUT)
                except FuturesTimeoutError:
                    stuck = True
                    raise library_client.LibraryError(
                        f"图书馆检索超时：{key} 栏超过 {_RESULT_TIMEOUT:.0f} 秒未返回")
    except Exception as exc:
        # 错误一律 200 + JSON（本仓既有约定）。**不回带 parsed**：前端在 !ok 分支里
        # 会把 libraryState.parsed 清空（app.js 的 runLibrarySearch），带回去没有任何
        # 消费者 —— 旧注释说的「否则前端第 2 页起就回传不了了」不成立。
        return {"ok": False, "error": str(exc), "warnings": warnings}
    finally:
        # 正常路径（含「worker 自己抛错」的路径）：两个 worker 都已有结论，
        # `wait=True` 立即返回，与用 `with` 时行为一致 —— 门要等到两栏都结束才放开。
        # 只有超时那条路 `wait=False`：卡住的那个 worker 不能拦着响应发出去。
        # （如实登记代价：那个 worker 线程会游离到它自己醒来为止，期间它仍可能占着
        # 上游连接；这是「挂死」与「一次可恢复的错误返回」之间刻意选的后者。）
        pool.shutdown(wait=not stuck)

    return {"ok": True, "parsed": parsed, "warnings": warnings, **blocks}


@app.post("/api/mail/gmail/credentials")
def gmail_save_credentials(req: GmailCredentials):
    """把 Gmail 地址与应用专用密码存入本机钥匙串（只存本地，不返回密码）。

    密码由用户在此输入，绝不经过对话/文件/日志 —— spec §6.4。
    """
    try:
        credentials.save_gmail(req.address.strip(), req.app_password)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/mail/gmail/credentials/status")
def gmail_credentials_status():
    """返回是否已存 Gmail 凭据 + 地址（永不返回密码）。"""
    try:
        address = credentials.get_gmail_address()
        return {"ok": True, "has_credentials": bool(address), "address": address}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.delete("/api/mail/gmail/credentials")
def gmail_delete_credentials():
    """清除已存的 Gmail 地址与应用专用密码。"""
    try:
        credentials.delete_gmail()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/mail/list")
def mail_list(req: MailListRequest = MailListRequest()):
    """列出最近 N 天的学校邮箱邮件。只读，不调 LLM、不改已读状态。

    `req` 带默认实例（而非无默认）—— FastAPI 据此把 body 判为**可选**。前端
    `util.js` 的 `api()` 在 `body` 为 `undefined` 时不发 body，若无默认则被
    FastAPI 挡成 422，拿不到本端点契约里的 `{ok: ...}`。
    """
    try:
        mails = mail_client.list_mail(req.days)
        return {"ok": True, "count": len(mails), "mails": mails}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/mail/summarize")
def mail_summarize(req: MailSummaryRequest):
    """取信 + AI 两级归类/摘要/DDL。取信仍是纯读，不改已读状态。

    失败路径**只多报一个** `reason` 键（成功路径的形状一个字节没变）：单靠 `error`
    这一条通道，前端无法区分「取信阶段失败」与「AI 阶段炸了」，只能一律说
    「AI 总结失败」—— 取信走 Gmail IMAP，认证失败时该做的是去设置页重填应用专用密码
    （spec §11），而用户会被指去查 API key。
    """
    phase = "mail"
    try:
        mails = mail_client.list_mail(req.days)
        if not mails:
            return {"ok": True, "mails": [], "groups": [], "warnings": []}
        bodies = mail_client.fetch_bodies(mails)
        # index 是前端把 AI 结果对回原文的锚，必须是连续整数，不能靠标题匹配
        for i, m in enumerate(mails):
            m["index"] = i
            m["body"] = bodies.get(m["id"], "")
        phase = "llm"
        r = llm_client.summarize_mail(
            req.llm_base_url, req.llm_api_key, req.llm_model, mails,
            language=req.language)
        return {"ok": True, "mails": mails, **r}
    except Exception as exc:
        return {"ok": False, "reason": phase, "error": str(exc)}
