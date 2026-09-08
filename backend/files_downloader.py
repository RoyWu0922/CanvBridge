"""文件下载规划与执行（保留 Canvas 文件夹结构）。"""
from __future__ import annotations

from pathlib import Path

from . import canvas_client


def _safe_name(name: str) -> str:
    name = name.replace("/", "_").replace("\\", "_").lstrip(".")
    return name or "_"


def confine_dest(download_dir: str, dest_path: str) -> Path | None:
    """把客户端回传的目标路径重新锚定到下载根目录内（H1）。

    真实的 dest_path 由 plan_downloads 在同一个 download_dir 下生成；但下载
    端点收到的是客户端原样回传，不能信任。这里重新约束：
    - 绝对路径必须落在 download_dir 之内，否则拒绝（返回 None）
    - 相对根目录的每一段重新 _safe_name（去分隔符 / 前导点），把 "../" 等
      遍历成分中和掉，杜绝写到根目录之外
    拒绝的项一律不落盘，由调用方记为失败。
    """
    root = Path(download_dir or "").expanduser()
    if not str(root) or not root.is_absolute():
        return None
    p = Path(dest_path).expanduser()
    try:
        rel = p.relative_to(root) if p.is_absolute() else p
    except ValueError:  # 绝对路径逃出下载根 → 拒绝
        return None
    # relative_to 成功后 rel 已不含逃出 root 的 ..，再把每段净化一遍兜底
    parts = [_safe_name(part) for part in rel.parts if part not in ("", ".")]
    if not parts:
        return None
    return root.joinpath(*parts)


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
    """规划 dest_path = 下载目录/科目/原文件夹路径/文件名；同名加 _N 后缀。

    saved 表示目标路径在磁盘上已存在（供前端显示「已保存」并默认不勾选）。
    """
    root = Path(download_dir).expanduser() / _safe_name(course_name)
    planned: list[dict] = []
    used: set[str] = set()
    for f in files:
        folder_path = build_folder_path(f.get("folder_id"), folders)
        base = root / folder_path if folder_path else root
        display = _safe_name(f.get("display_name", "file"))
        dest = base / display
        stem = dest.stem
        suffix = dest.suffix
        counter = 2
        while str(dest) in used:
            dest = base / f"{stem}_{counter}{suffix}"
            counter += 1
        used.add(str(dest))
        planned.append({
            "file_id": f["id"], "display_name": display, "dest_path": str(dest),
            "saved": dest.exists(),
        })
    return planned


def module_dest(download_dir: str, course_name: str, module_name: str,
                display_name: str) -> Path:
    """模块文件下载目标 = 下载目录/课程/模块/文件名（沿用 _safe_name 净化）。"""
    root = Path(download_dir).expanduser() / _safe_name(course_name)
    if module_name:
        root = root / _safe_name(module_name)
    return root / _safe_name(display_name)


def download_items(canvas_url: str, token: str, files_by_id: dict[int, dict],
                   planned: list[dict], download_dir: str) -> dict:
    """逐文件下载；已存在的目标文件跳过，单个失败不中断其余。

    download_dir 是落盘锚点：每条 dest_path 都先经 confine_dest 重新锚定到该
    目录内（不信任客户端回传的绝对路径），逃出或无法锚定的一律拒绝下载。
    """
    downloaded: list[str] = []
    skipped: list[str] = []
    failed: list[dict] = []
    for item in planned:
        info = files_by_id.get(item["file_id"])
        if not info or not info.get("url"):
            failed.append({"file_id": item["file_id"], "error": "缺少下载地址"})
            continue
        dest = confine_dest(download_dir, item["dest_path"])
        if dest is None:
            failed.append({"file_id": item["file_id"],
                           "error": f"目标路径不在下载目录内，已拒绝：{item['dest_path']}"})
            continue
        try:
            if dest.exists():
                skipped.append(str(dest))
                continue
            canvas_client.download_file(canvas_url, token, info["url"], str(dest))
            downloaded.append(str(dest))
        except Exception as exc:  # 单文件失败不影响整体
            failed.append({"file_id": item["file_id"], "error": str(exc)})
    return {"ok": len(failed) == 0, "downloaded": downloaded, "skipped": skipped,
            "failed": failed}
