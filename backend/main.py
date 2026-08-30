"""FastAPI 应用：Canvas 课程助手后端。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import apple_script, banweb, canvas_client, files_downloader, llm_client

app = FastAPI(title="Canvas 课程助手")

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
FRONTEND = FRONTEND_DIR / "index.html"

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


class CanvasConfig(BaseModel):
    canvas_url: str
    canvas_token: str


class LLMConfig(BaseModel):
    llm_base_url: str
    llm_api_key: str
    llm_model: str


class SyncRequest(CanvasConfig, LLMConfig):
    course_ids: list[int]
    start_date: str
    end_date: str


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


@app.post("/api/sync_announcements")
def sync(req: SyncRequest):
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
        anns = announcements.get(cid, [])
        results.append(llm_client.extract_course_summary(
            req.llm_base_url, req.llm_api_key, req.llm_model,
            name_by_id.get(cid, f"Course {cid}"), anns))
    return {"ok": True, "courses": results}


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
            dest_by_id = {p["file_id"]: p["dest_path"] for p in planned}
            results.append({
                "course_id": cid, "name": name,
                "files": [{
                    "file_id": f["id"], "display_name": f["display_name"],
                    "path": files_downloader.build_folder_path(f.get("folder_id"), folders),
                    "content_type": f["content_type"], "size": f["size"],
                    "dest_path": dest_by_id.get(f["id"], ""),
                } for f in files],
            })
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
    """AIMS 登录态（opening / needs_login / logged_in / error）。"""
    return banweb.get_status()


@app.post("/api/banweb/terms")
def banweb_terms():
    try:
        return {"ok": True, "terms": banweb.list_terms()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/banweb/open_login")
def banweb_open_login():
    """重新打开 AIMS 登录窗口并置前，供用户手动登录。"""
    try:
        banweb.open_login()
        return {"ok": True}
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
