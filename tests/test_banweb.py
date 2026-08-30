"""Banweb 课表解析与事件规格的单元测试（不依赖浏览器）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import banweb

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "banweb_schedule.html"
HTML = FIXTURE.read_text(encoding="utf-8", errors="replace")


def test_parse_schedule_html_blocks():
    courses = banweb.parse_schedule_html(HTML)
    assert len(courses) == 11
    assert len({c["code"] for c in courses}) == 6
    codes = {c["code"] for c in courses}
    assert {"CS1315", "DSC1001", "GE1362", "GE1401", "GE2401", "MA1508"} <= codes


def test_parse_schedule_html_course_fields():
    courses = banweb.parse_schedule_html(HTML)
    cs = [c for c in courses if c["code"] == "CS1315" and c["section"] == "C01"][0]
    assert cs["course"] == "Introduction to Comp Programme"
    assert cs["crn"] == "13018"
    assert cs["credits"].startswith("3")
    assert len(cs["meetings"]) == 1
    mt = cs["meetings"][0]
    assert mt["time"] == "12:00 pm - 2:50 pm"
    assert mt["days"] == "F"
    assert mt["room"] == "Mong Man Wai Building 2450"
    assert mt["range"] == "Aug 31, 2026 - Nov 28, 2026"
    assert "LEE" in mt["instr"]


def test_parse_schedule_html_no_meeting_block():
    """无固定时间的课（英语作文 A01）meetings 为空但仍保留在结果里。"""
    courses = banweb.parse_schedule_html(HTML)
    a01 = [c for c in courses if c["code"] == "GE1401" and c["section"] == "A01"][0]
    assert a01["meetings"] == []


def test_parse_time_range():
    assert banweb.parse_time_range("12:00 pm - 2:50 pm") == ((12, 0), (14, 50))
    assert banweb.parse_time_range("9:00 am - 11:50 am") == ((9, 0), (11, 50))
    assert banweb.parse_time_range("12:00 am - 12:30 am") == ((0, 0), (0, 30))
    with pytest.raises(banweb.BanwebError):
        banweb.parse_time_range("bad time")


def test_parse_date_range():
    d0, d1 = banweb.parse_date_range("Aug 31, 2026 - Nov 28, 2026")
    assert d0.isoformat() == "2026-08-31T00:00:00"
    assert d1.isoformat() == "2026-11-28T00:00:00"


def test_first_occurrence():
    d0, _ = banweb.parse_date_range("Aug 31, 2026 - Nov 28, 2026")  # 周一的周一
    assert banweb.first_occurrence(d0, "M").isoformat() == "2026-08-31T00:00:00"
    assert banweb.first_occurrence(d0, "F").isoformat() == "2026-09-04T00:00:00"
    assert banweb.first_occurrence(d0, "R").isoformat() == "2026-09-03T00:00:00"


def test_build_event_specs_single_day():
    courses = banweb.parse_schedule_html(HTML)
    specs = banweb.build_event_specs(courses, {"CS1315:C01"})
    assert len(specs) == 1
    sp = specs[0]
    assert sp["key"] == "CS1315:C01:F"
    assert sp["title"] == "CS1315 C01 · Introduction to Comp Programme"
    assert sp["start"] == "2026-09-04T12:00:00"   # 首个周五
    assert sp["end"] == "2026-09-04T14:50:00"
    assert sp["until"] == "2026-11-28"
    assert sp["location"] == "Mong Man Wai Building 2450"
    assert "LEE" in sp["notes"]
    assert "(P)" not in sp["notes"]              # (P) 主讲师标记被剥掉


def test_build_event_specs_skips_no_meeting():
    courses = banweb.parse_schedule_html(HTML)
    # 选一门无固定时间的课 → 不产出任何规格
    assert banweb.build_event_specs(courses, {"GE1401:A01"}) == []


def test_build_event_specs_multi_day():
    courses = [{
        "code": "X1234", "section": "A01", "course": "Multi Day",
        "meetings": [{
            "time": "9:00 am - 10:00 am", "days": "MWF", "room": "R101",
            "range": "Sep 01, 2026 - Nov 30, 2026", "instr": "Alice BOB (P)",
        }],
    }]
    specs = banweb.build_event_specs(courses, {"X1234:A01"})
    assert len(specs) == 3
    assert {s["key"] for s in specs} == {"X1234:A01:M", "X1234:A01:W", "X1234:A01:F"}
    assert specs[0]["start"] == "2026-09-07T09:00:00"   # 首个周一
    assert specs[2]["start"] == "2026-09-04T09:00:00"   # 首个周五
    assert "Mon" in specs[0]["title"]                   # 多天课标题带星期，避免互相撞名


def test_build_event_specs_unselected_ignored():
    courses = banweb.parse_schedule_html(HTML)
    assert banweb.build_event_specs(courses, set()) == []


# ---------------- enrich_meetings（前端日历定位字段） ----------------

def test_enrich_meetings_parses_time_and_days():
    courses = [{"code": "CS1315", "section": "C01",
                "meetings": [{"time": "12:00 pm - 2:50 pm", "days": "MWF",
                              "room": "MMW 2450", "range": "Aug 31, 2026 - Nov 28, 2026",
                              "instr": "Kenneth LEE"}]}]
    out = banweb.enrich_meetings(courses)
    m = out[0]["meetings"][0]
    assert m["start_min"] == 720
    assert m["end_min"] == 890
    assert m["days_list"] == ["M", "W", "F"]
    assert m["days"] == "MWF"          # 原字段保留


def test_enrich_meetings_skips_unparseable_time():
    courses = [{"code": "CS1315", "section": "C02",
                "meetings": [{"type": "Lecture", "time": "", "days": ""}]}]
    out = banweb.enrich_meetings(courses)
    assert "start_min" not in out[0]["meetings"][0]


# ---------------- reconcile_block（课表变更同步） ----------------

def _mc(code, section, course, meetings):
    return {"code": code, "section": section, "course": course, "meetings": meetings}


def _single_meeting(time, days):
    return {"time": time, "days": days, "room": "R1",
            "range": "Sep 01, 2026 - Nov 30, 2026", "instr": "Alice BOB (P)"}


def test_reconcile_identical_is_exists():
    """四元组全等 → exists，不建不改不隐藏。"""
    courses = [_mc("CS1315", "C01", "Intro", [_single_meeting("12:00 pm - 2:50 pm", "F")])]
    specs = banweb.build_event_specs(courses, {"CS1315:C01"})
    sp = specs[0]
    existing = [{"summary": sp["title"], "start": sp["start"], "end": sp["end"]}]
    dec = banweb.reconcile_block(specs, existing)
    assert [s["title"] for s in dec["exists"]] == [sp["title"]]
    assert dec["create"] == [] and dec["update"] == [] and dec["remove"] == []
    assert dec["removed"] == 0


def test_reconcile_time_changed_is_update():
    """标题不变、时间/天变了 → 原地编辑旧事件（update 带 old 与旧时间）。"""
    courses = [_mc("CS1315", "C01", "Intro", [_single_meeting("12:00 pm - 2:50 pm", "F")])]
    specs = banweb.build_event_specs(courses, {"CS1315:C01"})
    sp = specs[0]
    old = [{"summary": sp["title"], "start": "2026-09-04T10:00:00",
            "end": "2026-09-04T11:50:00"}]
    dec = banweb.reconcile_block(specs, old)
    assert len(dec["update"]) == 1
    assert dec["update"][0]["old_time"] == "Fri 10:00-11:50"
    assert dec["update"][0]["old"] == old[0]   # 要编辑的旧事件
    assert dec["create"] == [] and dec["exists"] == []
    assert dec["remove"] == [] and dec["removed"] == 0


def test_reconcile_new_block_is_create():
    """日历里该块没有旧事件 → 全部 create。"""
    courses = [_mc("CS1315", "C01", "Intro", [_single_meeting("12:00 pm - 2:50 pm", "F")])]
    specs = banweb.build_event_specs(courses, {"CS1315:C01"})
    dec = banweb.reconcile_block(specs, [])
    assert len(dec["create"]) == 1
    assert dec["update"] == [] and dec["exists"] == [] and dec["remove"] == []
    assert dec["removed"] == 0


def test_reconcile_day_changed_with_day_letter_title():
    """多天块（标题带星期）：新增周三节 → create；反向取消周三 → remove。"""
    courses = [_mc("X1234", "A01", "Multi Day", [_single_meeting("9:00 am - 10:00 am", "M"),
                                                 _single_meeting("9:00 am - 10:00 am", "W")])]
    specs = banweb.build_event_specs(courses, {"X1234:A01"})
    wed = next(s for s in specs if s["key"].endswith(":W"))
    mon = next(s for s in specs if s["key"].endswith(":M"))
    # 旧日历只有周一那节；教授加了周三那节 → 周三无同标题/同时段旧事件 → create
    existing = [{"summary": mon["title"], "start": mon["start"], "end": mon["end"]}]
    dec = banweb.reconcile_block(specs, existing)
    assert len(dec["exists"]) == 1
    assert len(dec["create"]) == 1
    assert dec["create"][0]["title"] == wed["title"]
    assert dec["update"] == [] and dec["remove"] == [] and dec["removed"] == 0
    # 反向：新规格只剩周一（周三被取消）→ 周三旧事件纯隐藏 removed
    dec2 = banweb.reconcile_block(specs[:1], [{"summary": m["title"], "start": m["start"],
                                               "end": m["end"]} for m in specs])
    assert len(dec2["exists"]) == 1
    assert dec2["remove"] == [wed["title"]]
    assert dec2["removed"] == 1


def test_reconcile_rename_same_time_is_update():
    """课程名改了但时间不变 → 按同时段配对，原地改标题（update）。"""
    courses = [_mc("CS1315", "C01", "Intro", [_single_meeting("12:00 pm - 2:50 pm", "F")])]
    specs = banweb.build_event_specs(courses, {"CS1315:C01"})
    sp = specs[0]
    existing = [{"summary": "CS1315 C01 · Old Name", "start": "2026-09-04T12:00:00",
                 "end": "2026-09-04T14:50:00"}]
    dec = banweb.reconcile_block(specs, existing)
    assert dec["create"] == []
    assert len(dec["update"]) == 1                     # 课程名变 → 原地改标题
    assert dec["update"][0]["old"]["summary"] == "CS1315 C01 · Old Name"
    assert dec["update"][0]["old_time"] == "Fri 12:00-14:50"
    assert dec["remove"] == [] and dec["removed"] == 0


class _FakePage:
    def __init__(self, url, title):
        self.url, self._title = url, title   # url 是属性（与 Playwright page.url 一致）
    def title(self):
        return self._title


def test_require_logged_in_detects_embedded_login():
    """Banner 退登后 URL 仍是 banweb，但标题为 User Login → 应判为未登录。"""
    page = _FakePage("https://banweb.cityu.edu.hk/pls/PROD/bwskfshd.P_CrseSchdDetl",
                     "User Login")
    assert banweb._is_embedded_login(page) is True
    with pytest.raises(banweb.BanwebError):
        banweb._require_logged_in(page)


def test_require_logged_in_passes_normal_schedule_page():
    page = _FakePage("https://banweb.cityu.edu.hk/pls/PROD/bwskfshd.P_CrseSchdDetl",
                     "Course Schedule")
    assert banweb._is_embedded_login(page) is False
    banweb._require_logged_in(page)  # 不应抛异常


def test_is_driver_error():
    """driver（node 子进程）崩溃的特征识别：区别于目标关闭。"""
    assert banweb._is_driver_error(RuntimeError(
        "BrowserType.launch_persistent_context: "
        "Connection closed while reading from the driver")) is True
    assert banweb._is_driver_error(RuntimeError(
        "Target page, context or browser has been closed")) is False
    assert banweb._is_driver_error(RuntimeError("boom")) is False


def test_target_closed_detection():
    assert banweb._target_closed(
        RuntimeError("Target page, context or browser has been closed")) is True
    assert banweb._target_closed(RuntimeError("TargetClosedError")) is True
    assert banweb._target_closed(RuntimeError("boom")) is False
    assert banweb._target_closed(banweb.BanwebError("尚未登录")) is False


def test_judge_status_embedded_login_title_wins():
    """URL 是 banweb 但标题是 User Login（退登内嵌页）→ needs_login。"""
    url = "https://banweb.cityu.edu.hk/pls/PROD/bwskfshd.P_CrseSchdDetl"
    assert banweb._judge_status(url, "User Login") == "needs_login"


def test_judge_status_okta_login():
    assert banweb._judge_status("https://auth.cityu.edu.hk/login", "Okta") == "needs_login"


def test_judge_status_logged_in():
    url = "https://banweb.cityu.edu.hk/pls/PROD/bwskfshd.P_CrseSchdDetl"
    assert banweb._judge_status(url, "Course Schedule") == "logged_in"


def test_judge_status_title_unreadable_is_not_logged_in():
    """导航中读不到标题 → 不能误判 logged_in，报过渡态 opening。"""
    url = "https://banweb.cityu.edu.hk/pls/PROD/bwskfshd.P_CrseSchdDetl"
    assert banweb._judge_status(url, None) == "opening"


def test_judge_status_opening():
    assert banweb._judge_status("about:blank", "New Tab") == "opening"


def test_retry_once_recovers_after_target_closed():
    """目标已关闭 → 重置浏览器 → 整体重试一次；重试成功返回结果。"""
    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Target page, context or browser has been closed")
        return "ok"
    assert banweb._retry_once(flaky) == "ok"
    assert calls["n"] == 2


def test_retry_once_passes_other_errors():
    """非目标关闭错误直接抛出，不重试。"""
    calls = {"n": 0}
    def boom():
        calls["n"] += 1
        raise ValueError("boom")
    with pytest.raises(ValueError):
        banweb._retry_once(boom)
    assert calls["n"] == 1
