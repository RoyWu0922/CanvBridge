from backend import canvas_client, files_downloader


def test_safe_name_strips_path():
    assert files_downloader._safe_name("../a/b.pdf") == "_a_b.pdf"
    assert files_downloader._safe_name(".hidden") == "hidden"
    assert files_downloader._safe_name("") == "_"


def test_build_folder_path():
    folders = [
        {"id": 1, "name": "Slides", "parent_folder_id": None},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1},
    ]
    assert files_downloader.build_folder_path(2, folders) == "Slides/Week 3"
    assert files_downloader.build_folder_path(1, folders) == "Slides"
    assert files_downloader.build_folder_path(999, folders) == ""


def test_plan_downloads_path_and_rename(tmp_path):
    files = [
        {"id": 1, "display_name": "a.pdf", "folder_id": 2},
        {"id": 2, "display_name": "a.pdf", "folder_id": 2},
        {"id": 3, "display_name": "b.pdf", "folder_id": None},
    ]
    folders = [
        {"id": 1, "name": "Slides", "parent_folder_id": None},
        {"id": 2, "name": "Week 3", "parent_folder_id": 1},
    ]
    planned = files_downloader.plan_downloads(str(tmp_path), "CS 101", files, folders)
    assert len(planned) == 3
    assert planned[0]["dest_path"] == str(tmp_path / "CS 101" / "Slides" / "Week 3" / "a.pdf")
    assert planned[1]["dest_path"] == str(tmp_path / "CS 101" / "Slides" / "Week 3" / "a_2.pdf")
    assert planned[2]["dest_path"] == str(tmp_path / "CS 101" / "b.pdf")


def test_download_items_reports_failure(monkeypatch):
    def boom(canvas_url, token, url, dest):
        raise RuntimeError("network")
    monkeypatch.setattr(canvas_client, "download_file", boom)
    files_by_id = {1: {"url": "http://x/f/1", "display_name": "a.pdf"}}
    planned = [{"file_id": 1, "dest_path": "/tmp/a.pdf"}, {"file_id": 99, "dest_path": "/tmp/miss.pdf"}]
    result = files_downloader.download_items("https://x", "tok", files_by_id, planned)
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
    result = files_downloader.download_items("https://x", "tok", files_by_id, planned)
    assert result["ok"] is True
    assert result["downloaded"] == [str(tmp_path / "a.pdf")]
