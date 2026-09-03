"""FastAPI 应用：Canvas 课程助手后端。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import apple_script, banweb, canvas_client, credentials, files_downloader, llm_client

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
    items: list[dict]  # [{course_id, file_id, dest_path}]


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
        return {"ok": True, "course": course}
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
        summary = llm_client.summarize_syllabus(
            req.llm_base_url, req.llm_api_key, req.llm_model,
            course["name"], course["syllabus_text"], language=req.language)
        return {"ok": True, "summary": summary}
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
                    "path": files_downloader.build_folder_path(f.get("folder_id"), folders),
                    "content_type": f["content_type"], "size": f["size"],
                    "dest_path": by_id.get(f["id"], {}).get("dest_path", ""),
                    "saved": bool(by_id.get(f["id"], {}).get("saved")),
                } for f in files],
            })
        except canvas_client.CanvasError as exc:
            # Canvas 对文件区为空 / 未对学生开放的课程返回 403。这里按「暂无文件」处理，
            # 前端灰色弱提示，不再把它当权限错误红字吓人。真正的异常照旧走 error。
            msg = str(exc)
            if "HTTP 403" in msg:
                results.append({"course_id": cid, "name": name, "files": [], "no_files": True})
            else:
                results.append({"course_id": cid, "name": name, "files": [], "error": msg})
        except Exception as exc:
            results.append({"course_id": cid, "name": name, "files": [], "error": str(exc)})
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
    planned = [{"file_id": i["file_id"], "dest_path": i["dest_path"]} for i in req.items]
    return files_downloader.download_items(req.canvas_url, req.canvas_token, files_by_id, planned)


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
