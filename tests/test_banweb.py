"""Banweb 课表解析与事件规格的单元测试（不依赖浏览器）。"""
import sys
import threading
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


def test_enrich_meetings_tba_days_has_no_days_list():
    """days="TBA" 不能拆出 'T'（周二）——整串非全合法星期则不留 days_list。"""
    courses = [{"code": "CS1315", "section": "C03",
                "meetings": [{"time": "12:00 pm - 2:50 pm", "days": "TBA"}]}]
    out = banweb.enrich_meetings(courses)
    m = out[0]["meetings"][0]
    assert "days_list" not in m
    assert m["start_min"] == 720    # 时间仍成功解析，不受 days 影响
    assert m["end_min"] == 890


# ---------------- primary_instructor（课表主教授） ----------------

def test_primary_instructor_prefers_p_marker():
    """多个 meeting 里优先取带 (P) 主讲师标记的那个，并剥掉标记。"""
    course = {"meetings": [
        {"instr": "TA Bob"},
        {"instr": "Alice CHAN (P)"},
        {"instr": "TA Carol"},
    ]}
    assert banweb.primary_instructor(course) == "Alice CHAN"


def test_primary_instructor_falls_back_to_first_nonempty():
    """没有 (P) 标记 → 取第一个非空 instr（原样返回）。"""
    course = {"meetings": [{"instr": ""}, {"instr": "TA Bob"}, {"instr": "Dr. Eve"}]}
    assert banweb.primary_instructor(course) == "TA Bob"


def test_primary_instructor_empty_when_no_instructor():
    assert banweb.primary_instructor({"meetings": [{"instr": ""}, {"instr": ""}]}) == ""
    assert banweb.primary_instructor({"meetings": []}) == ""


def test_enrich_meetings_attaches_primary_instructor():
    """get_schedule 已调 enrich_meetings → 每个课程块带 primary_instructor，前端下拉直接取用。"""
    courses = [{"code": "CS1315", "section": "C01",
                "meetings": [{"time": "12:00 pm - 2:50 pm", "days": "F", "room": "MMW",
                              "range": "Aug 31, 2026 - Nov 28, 2026",
                              "instr": "Kenneth LEE (P)"}]}]
    out = banweb.enrich_meetings(courses)
    assert out[0]["primary_instructor"] == "Kenneth LEE"


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


# ---------------- 周课表简称地点（room_short） ----------------

WEEKLY_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "banweb_weekly.html"
WEEKLY_HTML = WEEKLY_FIXTURE.read_text(encoding="utf-8", errors="replace")


def test_parse_weekly_schedule_html_entries():
    """周课表矩阵解析：每格三行（CRN / 课程-分班 / 楼宇码 房间号），
    行内 <br> 分隔的文本片段不会粘连（如 "C01MMW"）。"""
    entries = banweb.parse_weekly_schedule_html(WEEKLY_HTML)
    assert len(entries) == 9
    by_crn = {e["crn"]: e for e in entries}
    assert by_crn["13018"] == {"crn": "13018", "code": "CS1315", "section": "C01",
                               "day": "F", "room_short": "MMW 2450"}
    assert by_crn["12087"] == {"crn": "12087", "code": "DSC1001", "section": "C01",
                               "day": "M", "room_short": "LI 3505"}
    assert by_crn["11511"] == {"crn": "11511", "code": "MA1508", "section": "CA1",
                               "day": "T", "room_short": "YEUNG LT-6"}
    assert by_crn["11709"] == {"crn": "11709", "code": "MA1508", "section": "TA1",
                               "day": "M", "room_short": "YEUNG LT-18"}
    assert by_crn["12128"] == {"crn": "12128", "code": "GE2401", "section": "T01",
                               "day": "F", "room_short": "YEUNG B5-207"}
    assert by_crn["14467"] == {"crn": "14467", "code": "GE1362", "section": "C01",
                               "day": "R", "room_short": "LI 1507"}


def test_parse_weekly_schedule_html_blank_cells_ignored():
    """空白格（&nbsp;）不产出条目，rowspan 时间槽也不重复计数。"""
    entries = banweb.parse_weekly_schedule_html(WEEKLY_HTML)
    assert len({e["crn"] for e in entries}) == len(entries)


def test_merge_room_short_attaches_by_crn_and_day():
    """周课表简称按 crn + 星期挂到详情页对应 meeting；完整地点仍保留。"""
    courses = banweb.parse_schedule_html(HTML)
    weekly = banweb.parse_weekly_schedule_html(WEEKLY_HTML)
    banweb.merge_room_short(courses, weekly)
    def mt(code, section, day):
        c = [x for x in courses
             if x["code"] == code and x["section"] == section][0]
        return next(m for m in c["meetings"] if day in m.get("days", ""))
    assert mt("CS1315", "C01", "F")["room_short"] == "MMW 2450"
    assert mt("DSC1001", "C01", "M")["room_short"] == "LI 3505"
    assert mt("MA1508", "CA1", "T")["room_short"] == "YEUNG LT-6"
    assert mt("MA1508", "TA1", "M")["room_short"] == "YEUNG LT-18"
    assert mt("CS1315", "L02", "T")["room_short"] == "LI 4208"
    assert mt("GE1362", "T01", "R")["room_short"] == "LI 1507"
    cs = [x for x in courses if x["code"] == "CS1315" and x["section"] == "C01"][0]
    assert cs["meetings"][0]["room"] == "Mong Man Wai Building 2450"


def test_merge_room_short_keeps_full_room_when_no_match():
    """周课表里没有的课程（或 term 不同）→ 不加 room_short、不报错。"""
    courses = banweb.parse_schedule_html(HTML)
    banweb.merge_room_short(courses, [])  # 空周课表
    for c in courses:
        for m in c["meetings"]:
            assert "room_short" not in m


def test_enrich_meetings_preserves_room_short():
    """room_short 经 enrich_meetings 逐层复制后仍在 meeting 上（get_schedule 路径）。"""
    courses = [{"code": "CS1315", "section": "C01",
                "meetings": [{"time": "12:00 pm - 2:50 pm", "days": "F",
                              "room": "Mong Man Wai Building 2450",
                              "room_short": "MMW 2450",
                              "range": "Aug 31, 2026 - Nov 28, 2026",
                              "instr": "Kenneth LEE (P)"}]}]
    out = banweb.enrich_meetings(courses)
    m = out[0]["meetings"][0]
    assert m["room_short"] == "MMW 2450"
    assert m["room"] == "Mong Man Wai Building 2450"
    assert m["start_min"] == 720
    assert m["days_list"] == ["F"]


# ---------------- 自动登录（Okta 表单辅助） ----------------

class _FakeWaitPage:
    """模拟 page.wait_for_selector：present 集合里的选择器命中即返回，否则抛超时。"""
    def __init__(self, present):
        self.present = present
    def wait_for_selector(self, sel, timeout=3000, state="visible"):
        if sel in self.present:
            return object()
        raise TimeoutError(f"waiting for selector {sel}")


class _BoomWaitPage:
    def wait_for_selector(self, sel, timeout=3000, state="visible"):
        raise RuntimeError("Target page, context or browser has been closed")


def test_wait_for_any_returns_first_match():
    p = _FakeWaitPage({"a", "b"})
    assert banweb._wait_for_any(p, ["a", "b"], timeout=1) == "a"


def test_wait_for_any_timeout_returns_none():
    p = _FakeWaitPage(set())
    assert banweb._wait_for_any(p, ["a"], timeout=0.05) is None


def test_wait_for_any_swallows_navigation_errors():
    """导航中 wait_for_selector 抛错（目标关闭/页面切换）→ 吞掉继续轮询，超时返回 None。"""
    assert banweb._wait_for_any(_BoomWaitPage(), ["a"], timeout=0.05) is None


class _FakeEvalPage:
    def __init__(self, parts):
        self.parts = parts
        self.sel = None
    def eval_on_selector_all(self, sel, js):
        self.sel = sel
        return self.parts


def test_extract_okta_error_joins_and_collapses_whitespace():
    p = _FakeEvalPage([" Invalid  credentials ", " Try again "])
    assert banweb._extract_okta_error(p) == "Invalid credentials Try again"
    assert p.sel == banweb._OKTA_ERROR   # 用的是错误容器选择器


def test_extract_okta_error_empty_list():
    assert banweb._extract_okta_error(_FakeEvalPage([])) == ""


def test_extract_okta_error_on_js_exception():
    class Boom:
        def eval_on_selector_all(self, *a):
            raise RuntimeError("closed")
    assert banweb._extract_okta_error(Boom()) == ""


def test_raise_login_error_includes_okta_message():
    p = _FakeEvalPage(["We couldn't verify that email and password."])
    with pytest.raises(banweb.BanwebError) as ei:
        banweb._raise_login_error(p)
    assert "We couldn't verify" in str(ei.value)


def test_raise_login_error_no_message_gives_generic_hint():
    with pytest.raises(banweb.BanwebError) as ei:
        banweb._raise_login_error(_FakeEvalPage([]))
    assert "未跳回课表" in str(ei.value)


# ---------------- 考试时间表解析（AIMS hwsrsett_cityu.P_DispSchd） ----------------

def test_parse_exam_html_not_available():
    html = ("<html><body>Student Examination Timetable (Semester A 2026/27)"
            " ... currently not available.</body></html>")
    assert banweb.parse_exam_html(html) == []


EXAM_FIXTURE = """<table>
<tr><td>Course</td><td>Section</td><td>Date</td><td>Time</td><td>Venue</td><td>Room</td><td>Seat No.</td></tr>
<tr><td>CS2315</td><td>C01</td><td>2026-12-15</td><td>14:30 - 17:30</td><td>AC1</td><td>LT-17</td><td>23</td></tr>
<tr><td>MA2000</td><td>B01</td><td>2026-12-17</td><td>09:00 - 12:00</td><td>AC2</td><td>LT-4</td><td>11</td></tr>
</table>"""


def test_parse_exam_html_parses_table():
    out = banweb.parse_exam_html(EXAM_FIXTURE)
    assert len(out) == 2
    e = out[0]
    assert e["code"] == "CS2315"
    assert e["section"] == "C01"
    assert e["date"] == "2026-12-15"
    assert e["start"] == "14:30"
    assert e["end"] == "17:30"
    assert e["room"] == "LT-17"
    assert e["seat"] == "23"


def test_parse_exam_html_12h_time():
    html = EXAM_FIXTURE.replace("14:30 - 17:30", "2:30 pm - 5:30 pm")
    out = banweb.parse_exam_html(html)
    assert out[0]["start"] == "14:30"
    assert out[0]["end"] == "17:30"


def test_parse_exam_html_bad_date_kept():
    html = EXAM_FIXTURE.replace("2026-12-15", "15 Dec 2026")
    out = banweb.parse_exam_html(html)
    assert out[0]["date"] == "2026-12-15"


def test_parse_exam_html_no_recognized_headers():
    html = "<table><tr><td>Foo</td><td>Bar</td></tr><tr><td>a</td><td>b</td></tr></table>"
    assert banweb.parse_exam_html(html) == []


def test_split_exam_time_24h():
    assert banweb._split_exam_time("14:30 - 17:30") == ("14:30", "17:30")


def test_parse_exam_date_formats():
    assert banweb._parse_exam_date("2026-12-15") == "2026-12-15"
    assert banweb._parse_exam_date("15/12/2026") == "2026-12-15"
    assert banweb._parse_exam_date("15 Dec 2026") == "2026-12-15"
    assert banweb._parse_exam_date("") == ""


# ---------------- 浏览器单线程 executor 看门狗 ----------------

def test_on_browser_thread_normal_op_returns_result():
    """健康 executor 上普通操作照常返回结果（回归保护）。"""
    assert banweb._on_browser_thread(lambda: 42) == 42


def test_on_browser_thread_recovers_after_stuck_worker(monkeypatch):
    """单线程 worker 被永久阻塞后，看门狗应弃用旧 executor、重建并重试成功。

    模拟故障：先用一个永远不返回的任务占住唯一的 worker 线程；随后任何新任务
    都会排队超时。期望：_on_browser_thread 检测到超时 → 整体恢复 → 在全新的
    executor 上重试并成功返回，而不是把 TimeoutError 抛给调用方。
    """
    monkeypatch.setattr(banweb, "_kill_zombie_chrome", lambda: None)
    monkeypatch.setattr(banweb, "BROWSER_OP_TIMEOUT", 0.5)
    released = threading.Event()

    def blocker():
        released.wait()  # 模拟对半死连接永久阻塞的浏览器调用

    ex0 = banweb._browser_executor_instance()
    ex0.submit(blocker)
    try:
        assert banweb._on_browser_thread(lambda: 42) == 42
        # 恢复后 executor 必须是全新实例（旧卡死 executor 已被弃用）
        assert banweb._browser_executor is not ex0
    finally:
        released.set()  # 让卡死线程退出，避免泄漏


def test_recover_browser_swaps_executor_lock_and_refs(monkeypatch):
    """恢复原语应弃用旧 executor、清空浏览器句柄，并换一把新锁。"""
    monkeypatch.setattr(banweb, "_kill_zombie_chrome", lambda: None)
    banweb._ctx = object()
    banweb._page = object()
    ex0 = banweb._browser_executor_instance()
    lock0 = banweb._lock
    banweb._recover_browser_subprocess()
    assert banweb._browser_executor is not ex0
    assert banweb._lock is not lock0
    assert banweb._ctx is None and banweb._page is None
