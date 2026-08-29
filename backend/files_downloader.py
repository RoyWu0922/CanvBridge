"""文件下载规划与执行（保留 Canvas 文件夹结构）。"""
from __future__ import annotations

from pathlib import Path

from . import canvas_client


def _safe_name(name: str) -> str:
    name = name.replace("/", "_").replace("\\", "_").lstrip(".")
    return name or "_"


def build_folder_path(folder_id, folders: list[dict]) -> str:
    """返回 folder_id 的斜杠路径；课程根目录返回 ''。"""
    by_id = {f["id"]: f for f in folders}
    parts: list[str] = []
    cur = by_id.get(folder_id)
    while cur is not None:
        parts.append(_safe_name(cur.get("name", "")))
        cur = by_id.get(cur.get("parent_folder_id"))
    return "/".join(reversed(parts))


def plan_downloads(download_dir: str, course_name: str, files: list[dict],
                   folders: list[dict]) -> list[dict]:
    """规划 dest_path = 下载目录/科目/原文件夹路径/文件名；同名加 _N 后缀。"""
    root = Path(download_dir).expanduser() / _safe_name(course_name)
    planned: list[dict] = []
    used: set[str] = set()
    for f in files:
        folder_path = build_folder_path(f.get("folder_id"), folders)
        base = root / folder_path if folder_path else root
        display = _safe_name(f.get("display_name", "file"))
        dest = base / display
        counter = 2
        while str(dest) in used:
            dest = base / f"{dest.stem}_{counter}{dest.suffix}"
            counter += 1
        used.add(str(dest))
        planned.append({"file_id": f["id"], "display_name": display, "dest_path": str(dest)})
    return planned


def download_items(canvas_url: str, token: str, files_by_id: dict[int, dict],
                   planned: list[dict]) -> dict:
    """逐文件下载；单个失败不中断其余。"""
    downloaded: list[str] = []
    failed: list[dict] = []
    for item in planned:
        info = files_by_id.get(item["file_id"])
        if not info or not info.get("url"):
            failed.append({"file_id": item["file_id"], "error": "缺少下载地址"})
            continue
        try:
            canvas_client.download_file(canvas_url, token, info["url"], item["dest_path"])
            downloaded.append(item["dest_path"])
        except Exception as exc:  # 单文件失败不影响整体
            failed.append({"file_id": item["file_id"], "error": str(exc)})
    return {"ok": len(failed) == 0, "downloaded": downloaded, "failed": failed}
