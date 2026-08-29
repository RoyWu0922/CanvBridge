"""FastAPI 应用：Canvas 课程助手后端。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import apple_script, canvas_client, files_downloader, llm_client

app = FastAPI(title="Canvas 课程助手")

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "index.html"


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
            req.location, req.notes)
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
