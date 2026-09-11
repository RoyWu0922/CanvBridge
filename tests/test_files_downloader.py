from pathlib import Path

from backend import canvas_client, files_downloader


def test_safe_name_strips_path():
    assert files_downloader._safe_name("../a/b.pdf") == "_a_b.pdf"
    assert files_downloader._safe_name(".hidden") == "hidden"
    assert files_downloader._safe_name("") == "_"


def test_confine_dest_anchors_under_root(tmp_path):
    """根目录内的绝对路径原样锚回（保留空格与子目录结构）。"""
    root = tmp_path / "DL"
    dest = files_downloader.confine_dest(str(root), str(root / "CS 101" / "a.pdf"))
    assert dest == root / "CS 101" / "a.pdf"


def test_confine_dest_rejects_path_escaping_root(tmp_path):
    """逃出下载根的绝对路径 → None（H1：客户端改 dest_path 也写不出去）。"""
    root = tmp_path / "DL"
    assert files_downloader.confine_dest(str(root), str(tmp_path / "escape.pdf")) is None
    assert files_downloader.confine_dest(str(root), str(tmp_path / "other" / "x.pdf")) is None


def test_confine_dest_needs_absolute_root(tmp_path):
    assert files_downloader.confine_dest("", str(tmp_path / "a.pdf")) is None
    assert files_downloader.confine_dest("relative/dir", str(tmp_path / "a.pdf")) is None


def test_confine_dest_neutralizes_traversal(tmp_path):
    """相对路径里的 ../ 与前导点被净化掉，最终仍落在根目录内。"""
    root = tmp_path / "DL"
    dest = files_downloader.confine_dest(str(root), "../x/.hidden/evil.pdf")
    assert dest is not None
    assert ".." not in dest.parts
    assert str(dest).startswith(str(root))
    assert dest.name == "evil.pdf"


def test_build_folder_path():
    """新路径不含课程根文件夹那一段（Canvas 里那层 "course files"）。"""
    folders = [
        {"id": 1, "name": "course files", "parent_folder_id": None},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1},
    ]
    assert files_downloader.build_folder_path(2, folders) == "Week 3"
    assert files_downloader.build_folder_path(1, folders) == ""
    assert files_downloader.build_folder_path(None, folders) == ""
    assert files_downloader.build_folder_path(999, folders) == ""


def test_build_folder_path_nested_two_levels():
    folders = [
        {"id": 1, "name": "course files", "parent_folder_id": None},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1},
        {"id": 3, "name": "Lab", "parent_folder_id": 2},
    ]
    assert files_downloader.build_folder_path(3, folders) == "Week 3/Lab"


def test_build_folder_path_degrades_when_no_root_marker():
    """folders 里没有任何 parent_folder_id 为 None 的节点（Canvas 返回形态与预期不符）
    → 退回「保留全部段」＝本轮改动前的行为。宁可多一层，也不把真实文件夹名算丢。"""
    folders = [{"id": 2, "name": "Week 3", "parent_folder_id": 1}]  # 父 1 不在表里
    assert files_downloader.build_folder_path(2, folders) == "Week 3"


def test_build_folder_path_survives_parent_cycle():
    """父链成环不得死循环、不得抛；环被截断，名字不会无限累积。"""
    folders = [
        {"id": 1, "name": "A", "parent_folder_id": 2},
        {"id": 2, "name": "B", "parent_folder_id": 1},
    ]
    p = files_downloader.build_folder_path(1, folders)
    assert isinstance(p, str)
    assert p.count("A") <= 1 and p.count("B") <= 1


def test_build_legacy_folder_path_keeps_root_segment():
    """旧路径必须逐字复现改动前的形状（含课程根那一段）—— 兼容检测全靠它。"""
    folders = [
        {"id": 1, "name": "course files", "parent_folder_id": None},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1},
    ]
    assert files_downloader.build_legacy_folder_path(2, folders) == "course files/Week 3"
    assert files_downloader.build_legacy_folder_path(1, folders) == "course files"
    assert files_downloader.build_legacy_folder_path(999, folders) == ""


def test_module_dest_builds_path(tmp_path):
    """module_dest = 下载目录/课程/模块/文件名；名称净化 / 与前导点，无模块名则省略一层。"""
    dest = files_downloader.module_dest(str(tmp_path), "CS 101", "Week 1", "syllabus.pdf")
    assert dest == tmp_path / "CS 101" / "Week 1" / "syllabus.pdf"
    assert files_downloader.module_dest(str(tmp_path), "CS 101", "", "syllabus.pdf") == \
        tmp_path / "CS 101" / "syllabus.pdf"
    assert files_downloader.module_dest(str(tmp_path), "A/B", "../Week", "x.pdf") == \
        tmp_path / "A_B" / "_Week" / "x.pdf"


def test_plan_downloads_path_and_rename(tmp_path):
    files = [
        {"id": 1, "display_name": "a.pdf", "folder_id": 2},
        {"id": 2, "display_name": "a.pdf", "folder_id": 2},
        {"id": 3, "display_name": "b.pdf", "folder_id": None},
        {"id": 4, "display_name": "a.pdf", "folder_id": 2},
    ]
    folders = [
        {"id": 1, "name": "Slides", "parent_folder_id": None},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1},
    ]
    planned = files_downloader.plan_downloads(str(tmp_path), "CS 101", files, folders)
    assert len(planned) == 4
    assert planned[0]["dest_path"] == str(tmp_path / "CS 101" / "Slides" / "Week 3" / "a.pdf")
    assert planned[1]["dest_path"] == str(tmp_path / "CS 101" / "Slides" / "Week 3" / "a_2.pdf")
    assert planned[2]["dest_path"] == str(tmp_path / "CS 101" / "b.pdf")
    assert planned[3]["dest_path"] == str(tmp_path / "CS 101" / "Slides" / "Week 3" / "a_3.pdf")
    # 磁盘上已存在的目标文件应标记 saved=True，其余 False
    assert planned[0]["saved"] is False  # 规划时该文件还不存在
    dest0 = Path(planned[0]["dest_path"])
    dest0.parent.mkdir(parents=True, exist_ok=True)
    dest0.write_bytes(b"x")
    planned2 = files_downloader.plan_downloads(str(tmp_path), "CS 101", files, folders)
    assert planned2[0]["saved"] is True
    assert planned2[1]["saved"] is False


def test_download_items_reports_failure(monkeypatch, tmp_path):
    def boom(canvas_url, token, url, dest):
        raise RuntimeError("network")
    monkeypatch.setattr(canvas_client, "download_file", boom)
    files_by_id = {1: {"url": "http://x/f/1", "display_name": "a.pdf"}}
    planned = [{"file_id": 1, "dest_path": str(tmp_path / "a.pdf")},
               {"file_id": 99, "dest_path": str(tmp_path / "miss.pdf")}]
    result = files_downloader.download_items("https://x", "tok", files_by_id, planned,
                                             str(tmp_path))
    assert result["ok"] is False
    assert result["downloaded"] == []
    assert len(result["failed"]) == 2


def test_download_items_success(monkeypatch, tmp_path):
    calls = []
    def ok(canvas_url, token, url, dest):
        calls.append(dest)
    monkeypatch.setattr(canvas_client, "download_file", ok)
    files_by_id = {1: {"url": "http://x/f/1"}}
    planned = [{"file_id": 1, "dest_path": str(tmp_path / "a.pdf")}]
    result = files_downloader.download_items("https://x", "tok", files_by_id, planned,
                                             str(tmp_path))
    assert result["ok"] is True
    assert result["downloaded"] == [str(tmp_path / "a.pdf")]


def test_download_items_skips_existing(monkeypatch, tmp_path):
    calls = []
    def ok(canvas_url, token, url, dest):
        calls.append(dest)
    monkeypatch.setattr(canvas_client, "download_file", ok)
    existing = tmp_path / "a.pdf"
    existing.write_bytes(b"old")
    files_by_id = {1: {"url": "http://x/f/1"}, 2: {"url": "http://x/f/2"}}
    planned = [
        {"file_id": 1, "dest_path": str(existing)},
        {"file_id": 2, "dest_path": str(tmp_path / "b.pdf")},
    ]
    result = files_downloader.download_items("https://x", "tok", files_by_id, planned,
                                             str(tmp_path))
    assert result["ok"] is True
    assert result["skipped"] == [str(existing)]  # 已存在 → 跳过不覆盖
    assert result["downloaded"] == [str(tmp_path / "b.pdf")]
    assert calls == [str(tmp_path / "b.pdf")]    # download_file 只被调用一次
    assert existing.read_bytes() == b"old"       # 原文件未被覆盖


def test_download_items_rejects_path_outside_root(monkeypatch, tmp_path):
    """dest_path 被改成下载根之外 → 拒绝下载，download_file 不被调用（H1）。"""
    calls = []
    def ok(canvas_url, token, url, dest):
        calls.append(dest)
    monkeypatch.setattr(canvas_client, "download_file", ok)
    root = tmp_path / "DL"
    files_by_id = {1: {"url": "http://x/f/1"}}
    planned = [{"file_id": 1, "dest_path": str(tmp_path / "escape.pdf")}]
    result = files_downloader.download_items("https://x", "tok", files_by_id, planned,
                                             str(root))
    assert result["ok"] is False
    assert result["downloaded"] == []
    assert result["failed"][0]["file_id"] == 1
    assert "已拒绝" in result["failed"][0]["error"]
    assert calls == []
