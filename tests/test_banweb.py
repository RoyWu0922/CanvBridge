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


def test_is_login_page_detects_sso_logout():
    """会话失效后浏览器停在 CityU SSO 登出页（banids /ssomanager/）→ 判为未登录。"""
    assert banweb._is_login_page(
        "https://banids.cityu.edu.hk/ssomanager/ui/samlLogout.jsp") is True
    assert banweb._is_login_page("https://auth.cityu.edu.hk/app/banweb/x/") is True
    assert banweb._is_login_page(
        "https://banweb.cityu.edu.hk/pls/PROD/bwskfshd.P_CrseSchdDetl") is False


def test_require_logged_in_raises_on_sso_logout_page():
    """浏览器停在 samlLogout（URL/标题都非 "User Login"）→ 也必须判未登录并抛错。"""
    page = _FakePage("https://banids.cityu.edu.hk/ssomanager/ui/samlLogout.jsp",
                     "CityU AIMS Sign out")
    with pytest.raises(banweb.BanwebError):
        banweb._require_logged_in(page)


class _El:
    """query_selector 命中元素的替身（Playwright 元素句柄只需真值）。"""


class _QPPage:
    def __init__(self, has_term: bool, boom: bool = False):
        self._has, self._boom = has_term, boom
    def query_selector(self, sel):
        if self._boom:
            raise RuntimeError("context destroyed")
        return _El() if self._has and sel == "select[name=term_in]" else None


def test_term_ready_true_when_dropdown_present():
    assert banweb._term_ready(_QPPage(has_term=True)) is True


def test_term_ready_false_when_no_dropdown():
    assert banweb._term_ready(_QPPage(has_term=False)) is False


def test_term_ready_false_when_navigation_exception():
    assert banweb._term_ready(_QPPage(has_term=True, boom=True)) is False


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


# --- 邮件 tab 隔离（spec §5.1 约束 1）---

class _Page:
    def __init__(self, url):
        self.url = url


class _Ctx:
    def __init__(self, urls):
        self.pages = [_Page(u) for u in urls]
        self.new_pages = 0

    def new_page(self):
        self.new_pages += 1
        p = _Page("about:blank")
        self.pages.append(p)
        return p


def test_is_mail_url_matches_all_mail_hosts():
    """`MAIL_HOSTS` 现在是**三个**主机：两个 OWA 域名 + 邮箱登录中继。

    原名 `test_is_mail_url_matches_both_owa_hosts` 只断言前两个 —— 名字与内容
    双双停在「两个主机」的年代，第三个主机被漏在用例之外。
    """
    assert banweb._is_mail_url("https://outlook.cloud.microsoft/mail/")
    assert banweb._is_mail_url("https://outlook.office.com/mail/")
    assert banweb._is_mail_url("https://login.microsoftonline.com/common/oauth2/v2.0/authorize")
    assert not banweb._is_mail_url("https://banweb.cityu.edu.hk/x")
    assert not banweb._is_mail_url("")


def test_mail_hosts_stay_disjoint_from_aims_hosts():
    """`_find_page` 第一段循环里的邮件过滤之所以是「无操作」，全靠这条不变式：
    MAIL_HOSTS 与 {BANWEB, LOGIN_HOST} **前缀不相交**。日后加邮件域时若撞上 AIMS
    的域名，这条会立刻炸 —— 那时第一段循环的过滤不但会咬合，还会把 AIMS 登录页
    一起跳过。

    **两个方向缺一不可**，因为 `_is_mail_url` 是拿 MAIL_HOSTS 去 `startswith`
    页面 URL 的，两个方向都能让 AIMS 页被误判成邮件页：
      · `h.startswith(AIMS)` —— 邮件域更长。误把 Okta 的 auth.cityu.edu.hk 加进来，
        于是 AIMS 登录页自己变成「邮件页」。
      · `AIMS.startswith(h)` —— 邮件域更短。写了 `https://banweb` 这种半截前缀，
        效果一样。只钉前一个方向时，这一半出问题没有任何用例会红。
    这里也顺带钉住 LOGIN_HOST（Okta host）**不在** MAIL_HOSTS 里。"""
    assert not any(
        h.startswith(banweb.BANWEB) or h.startswith(banweb.LOGIN_HOST)
        for h in banweb.MAIL_HOSTS
    ), "MAIL_HOSTS 里有以 AIMS host 为前缀的项：AIMS 页会被判成邮件页"
    assert not any(
        banweb.BANWEB.startswith(h) or banweb.LOGIN_HOST.startswith(h)
        for h in banweb.MAIL_HOSTS
    ), "MAIL_HOSTS 里有 AIMS host 的前缀：AIMS 页会被判成邮件页"


def test_is_mail_url_covers_mail_login_relay_host():
    """邮箱登录中继（login.microsoftonline.com）也必须算邮件域。

    OWA 冷启动要经它跳转，SSO 期间邮件 tab 就停在这个 host 上；漏掉它，
    `_find_page` 的兜底循环便会把这个 tab 交给 AIMS，再被 goto(TERM_PAGE)
    导航走 —— 这正是本任务要消灭的那个 bug，只是换了个窗口期。
    """
    assert banweb._is_mail_url("https://login.microsoftonline.com/common/oauth2/v2.0/authorize")
    assert banweb._is_mail_url("https://outlook.office.com/mail/")


def test_find_page_never_returns_a_mail_login_relay_tab():
    ctx = _Ctx(["https://login.microsoftonline.com/common/oauth2/v2.0/authorize"])
    assert banweb._find_page(ctx) is None
    assert ctx.new_pages == 0


def test_find_page_prefers_aims_page_over_mail_tab():
    ctx = _Ctx(["https://outlook.cloud.microsoft/mail/",
                "https://banweb.cityu.edu.hk/twkwkmgr.P_DispMyWeek"])
    assert banweb._find_page(ctx).url.startswith(banweb.BANWEB)


def test_find_page_never_returns_a_mail_tab():
    """只剩邮件 tab 时必须返回 None —— 认领它会让 AIMS 把邮件页导航走。"""
    ctx = _Ctx(["https://outlook.cloud.microsoft/mail/"])
    assert banweb._find_page(ctx) is None
    assert ctx.new_pages == 0        # 且绝不能自己开页


def test_find_page_still_falls_back_to_unknown_blank_page():
    """非邮件域的非 AIMS 页仍照旧兜底 —— 现有行为不能变。"""
    ctx = _Ctx(["about:blank"])
    assert banweb._find_page(ctx).url == "about:blank"


def test_find_page_returns_none_when_no_pages():
    assert banweb._find_page(_Ctx([])) is None


# --- 轮询（launch=False）不得优雅退出 Chrome（spec §3.2/§11）---

class _Browser:
    """browser 替身：只记 close() 被调了几次。"""

    def __init__(self, ctx):
        self.contexts = [ctx]
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class _Driver:
    """Playwright 驱动替身：connect_over_cdp 一律交回给定 browser 替身。"""

    def __init__(self, browser):
        self.chromium = self
        self._browser = browser

    def connect_over_cdp(self, url):
        return self._browser


def _poll(monkeypatch, ctx):
    """用替身驱动 `_ensure_browser(launch=False)`，返回 browser 替身。"""
    browser = _Browser(ctx)
    monkeypatch.setattr(banweb, "_pw", _Driver(browser))
    monkeypatch.setattr(banweb, "_page", None)
    monkeypatch.setattr(banweb, "_ctx", None)
    with pytest.raises(banweb.BanwebError):
        banweb._ensure_browser(launch=False)
    return browser


def test_poll_with_only_mail_tab_must_not_close_browser(monkeypatch):
    """轮询碰到「有页面、但没有 AIMS 页（只剩邮件 tab）」时**绝不能** close。

    `browser.close()` 是优雅退出 Chrome —— spec §3.2/§11 明确把它列为会**丢
    Okta 会话**的动作，而且它还会顺带关掉用户刚打开的邮件 tab。排除邮件 tab
    之前，这条 close 尾只可能出现在「零页面」；排除之后「有页面但没 AIMS 页」
    也会落到这里，于是轮询就能把会话关掉。只对真正的零页面保留 close。
    """
    ctx = _Ctx(["https://outlook.cloud.microsoft/mail/"])
    browser = _poll(monkeypatch, ctx)
    assert browser.close_calls == 0
    assert ctx.new_pages == 0        # 顺带：轮询绝不弹窗


def test_poll_with_truly_empty_page_list_still_disconnects(monkeypatch):
    """零页面那条老路径保持不变：干净断开连接，再走 needs_login。"""
    browser = _poll(monkeypatch, _Ctx([]))
    assert browser.close_calls == 1


# --- 有头 → 无头 的对称切换（抓取前把「手动登录留下的可见窗口」换掉）---
#
# `_browser_headless` 的三个取值各有含义，切换判据**只认 False**：
#   True  = 我们自己开的无头窗口
#   False = 我们自己开的**有头**窗口（手动登录兜底留下的）
#   None  = 附着在**外部**实例上 —— 可能是用户自己的 Chrome，绝不能杀

class _LivePage:
    """**活**页面替身：`_ensure_browser` 的存活探测是 `_page.evaluate("1")`。

    不能用现成的 `_Page`：它没有 `evaluate`，探测会抛异常 → 走
    `_reset_browser()` 那条分支，于是**根本测不到切换逻辑**（四个用例会
    因为「反正都 reset 了」而全绿）。这是个必须避开的说谎形状。
    """

    def __init__(self, url="https://banweb.cityu.edu.hk/x"):
        self.url = url

    def evaluate(self, expr):
        return 1


def _switch_probe(monkeypatch, current, requested):
    """在当前状态 `current` 下请求 `requested`，返回 (返回的页, kill 次数)。

    杀进程用替身记账 —— 真实 `_kill_zombie_chrome` 会 pkill 掉占 9339 的
    Chrome，测试里**绝不能**真跑。
    """
    killed = []
    monkeypatch.setattr(banweb, "_kill_zombie_chrome", lambda: killed.append(1))
    monkeypatch.setattr(banweb, "_reset_browser",
                        lambda: (monkeypatch.setattr(banweb, "_ctx", None),
                                 monkeypatch.setattr(banweb, "_page", None)))
    page = _LivePage()
    ctx = _Ctx(["https://banweb.cityu.edu.hk/twkwkmgr.P_DispMyWeek"])
    ctx.pages[0] = page
    monkeypatch.setattr(banweb, "_pw", _Driver(_Browser(ctx)))
    monkeypatch.setattr(banweb, "_page", page)
    monkeypatch.setattr(banweb, "_ctx", ctx)
    monkeypatch.setattr(banweb, "_browser_headless", current)
    got = banweb._ensure_browser(launch=True, headless=requested)
    return got, len(killed)


def test_ensure_browser_switches_our_headed_window_to_headless(monkeypatch):
    """核心：请求无头、当前是**我们自己开的有头**窗口 → 拆掉重建无头。

    没有这一支，第 889 行的 `headless or ...` 会短路为真、无条件复用现成页面 ——
    于是用户只要点过**一次**「重新登录」，那个可见窗口就被此后**每一次**抓取
    复用，再也回不到无头。切换必须走真实拆除：只 `_reset_browser()` 不够，
    无头 Chrome 还活着时 `connect_over_cdp` 会连回它、复用旧窗口。
    """
    page, killed = _switch_probe(monkeypatch, current=False, requested=True)
    assert killed == 1, "有头→无头必须真的杀进程重启，否则窗口不会消失"
    assert page is not None


def test_ensure_browser_never_kills_an_attached_external_instance(monkeypatch):
    """**这条守的是用户自己的 Chrome。**

    `_browser_headless is None` 表示我们**附着在外部实例**上 —— 那个 Chrome
    不是我们启动的，可能是用户正在用的一个（端口 9339）。切换判据若写成
    `_browser_headless is not True`（把 None 一并卷进来），就会在附着状态下
    也去 pkill，把用户的浏览器杀掉。
    """
    page, killed = _switch_probe(monkeypatch, current=None, requested=True)
    assert killed == 0, "附着在外部实例上时绝不能杀 —— 那可能不是我们的 Chrome"
    assert page is not None


def test_ensure_browser_reuses_headless_without_teardown(monkeypatch):
    """已经无头、又请求无头 → 直接用，不做无谓的重启（重启 = 丢会话）。"""
    _, killed = _switch_probe(monkeypatch, current=True, requested=True)
    assert killed == 0


def test_ensure_browser_keeps_our_headed_window_when_headed_requested(monkeypatch):
    """已经是有头、又请求有头（手动登录）→ 窗口保持不动。"""
    _, killed = _switch_probe(monkeypatch, current=False, requested=False)
    assert killed == 0


def test_ensure_browser_still_switches_headless_to_headed(monkeypatch):
    """**原有方向不许被这次改动弄坏**：请求有头、当前无头 → 拆掉换有头。

    这是「重新登录」按钮开窗所依赖的那一支（老 bug：报「已打开」却无窗）。
    """
    _, killed = _switch_probe(monkeypatch, current=True, requested=False)
    assert killed == 1


# ---------------- _fetch_page：切换之后把会话补回来 ----------------
#
# 「有头 → 无头」的切换会重建浏览器（= 丢会话），所以抓取路径必须在切换后静默重登
# 一次，否则用户每点一次抓取就掉一次线。下面五个用例各钉这条链上的一环。

def _fetch_probe(monkeypatch, current, *, flag=False, login_error=None):
    """在浏览器状态 `current` 下跑 `_fetch_page`，返回 (返回的页, kill 次数, 登录调用表)。

    钥匙串与 `_login_with` 双双替身：测试**绝不**碰真钥匙串、绝不做真登录。
    """
    killed, logins = [], []
    monkeypatch.setattr(banweb, "_kill_zombie_chrome", lambda: killed.append(1))
    monkeypatch.setattr(banweb, "_reset_browser",
                        lambda: (monkeypatch.setattr(banweb, "_ctx", None),
                                 monkeypatch.setattr(banweb, "_page", None)))
    monkeypatch.setattr(banweb.credentials, "get_credentials", lambda: ("eid", "pw"))

    def _fake_login(username, password):
        logins.append((username, password))
        if login_error is not None:
            raise login_error
        return "logged_in"

    monkeypatch.setattr(banweb, "_login_with", _fake_login)
    page = _LivePage()
    ctx = _Ctx(["https://banweb.cityu.edu.hk/twkwkmgr.P_DispMyWeek"])
    ctx.pages[0] = page
    monkeypatch.setattr(banweb, "_pw", _Driver(_Browser(ctx)))
    monkeypatch.setattr(banweb, "_page", page)
    monkeypatch.setattr(banweb, "_ctx", ctx)
    monkeypatch.setattr(banweb, "_browser_headless", current)
    monkeypatch.setattr(banweb, "_manual_login_required", flag)
    return banweb._fetch_page(), len(killed), logins


def test_fetch_page_relogins_after_reclaiming_the_headless_browser(monkeypatch):
    """核心：把可见窗口收回无头之后，必须**静默补一次登录**。

    不补的话用户点一次抓取就掉线（切换重建了浏览器、会话没了），而当初正是自动
    登录失败才被迫开的有头窗口 —— 等于每次抓取都罚一次手动登录。
    """
    _, killed, logins = _fetch_probe(monkeypatch, current=False)
    assert killed == 1                                # 确实切换了（前提成立）
    assert logins == [("eid", "pw")]                  # 用钥匙串里的凭据补登了一次
    assert banweb._manual_login_required is False      # 成功 → 不进手动登录态


def test_fetch_page_does_not_login_when_the_browser_is_already_headless(monkeypatch):
    """本来就在无头状态 → 没发生切换，也就不该有任何登录动作（不打扰、不耗时）。"""
    _, killed, logins = _fetch_probe(monkeypatch, current=True)
    assert killed == 0
    assert logins == []


def test_fetch_page_marks_manual_login_required_when_the_silent_relogin_fails(monkeypatch):
    """补登失败（需要 MFA / 没存凭据）→ 原样抛错，并**记下手动登录态**。

    记这一笔是为了下一个用例：不记的话每次抓取都会再拆一次用户的窗口。
    """
    with pytest.raises(banweb.BanwebError, match="需要 MFA"):
        _fetch_probe(monkeypatch, current=False,
                     login_error=banweb.BanwebError("需要 MFA 验证"))
    assert banweb._manual_login_required is True


def test_fetch_page_never_tears_down_the_window_when_manual_login_is_required(monkeypatch):
    """**抖动护栏**：一旦自动登录不可用，可见窗口就是唯一进得去的路 → 绝不拆它。

    这条钉的是一个**顺序**错误：`_ensure_browser` 的拆除发生在它内部，若先调它、
    再判标志位，标志位就形同虚设 —— 用户的窗口照样每次抓取被拆一次，而且拆完还
    登不回去（正是当初置位的原因），变成「点一次抓取、重登一次」的无底洞。
    """
    page, killed, logins = _fetch_probe(monkeypatch, current=False, flag=True)
    assert killed == 0          # ← 顺序错了这里会是 1
    assert logins == []         # 已知自动登录没用，不重复试
    assert page is not None     # 用户手上那个窗口还能用


def test_fetch_page_never_switches_an_attached_external_instance(monkeypatch):
    """附着在**外部**实例上（可能是用户自己开的 Chrome）→ 抓取路径也不许动它。

    与 `_ensure_browser` 的守卫同源：`_browser_headless is None` 意味着那个 Chrome
    不是我们启动的，杀它等于杀掉用户的浏览器。
    """
    _, killed, logins = _fetch_probe(monkeypatch, current=None)
    assert killed == 0
    assert logins == []


class _LoggedInPage(_LivePage):
    """**已登录**的页面替身：让 `_login_with` 走快路径（term 下拉已在）。

    只需要 goto / wait_for_load_state / query_selector —— 快路径在 `_term_ready`
    为真时立刻返回，不碰 Okta 表单，所以不必替身整个表单流程。
    """

    def goto(self, url, **kw):
        pass

    def wait_for_load_state(self, state, **kw):
        pass

    def query_selector(self, selector):
        return object() if selector == "select[name=term_in]" else None


def test_login_with_clears_manual_login_required_on_success(monkeypatch):
    """登录成功 → 撤掉「只走手动登录」的标记（自愈）。

    置位后若永不复位，用户即使补好了凭据、或后来勾了「信任此设备」，也再回不到
    无头自动登录 —— 那个可见窗口会一直留着。这是 `_login_with` 里**唯一**的复位
    出口，两条成功路径（会话仍在的快路径 / 走完 Okta 表单）共用它。
    """
    monkeypatch.setattr(banweb, "_manual_login_required", True)
    monkeypatch.setattr(banweb, "_kill_zombie_chrome",
                        lambda: pytest.fail("这条路径不该拆窗口"))
    page = _LoggedInPage()
    ctx = _Ctx(["https://banweb.cityu.edu.hk/x"])
    ctx.pages[0] = page
    monkeypatch.setattr(banweb, "_pw", _Driver(_Browser(ctx)))
    monkeypatch.setattr(banweb, "_page", page)
    monkeypatch.setattr(banweb, "_ctx", ctx)
    monkeypatch.setattr(banweb, "_browser_headless", True)
    assert banweb._login_with("eid", "pw") == "logged_in"
    assert banweb._manual_login_required is False


class _LoginFailedPage(_LivePage):
    """登录**失败**的页面替身：没有 term 下拉，Okta 表单那步直接报错。"""

    def goto(self, url, **kw):
        pass

    def wait_for_load_state(self, state, **kw):
        pass

    def query_selector(self, selector):
        return None      # 学期下拉不在 → 未登录


def test_login_with_keeps_manual_login_required_when_login_fails(monkeypatch):
    """登录**失败**时不许复位 —— 复位方向搞反会让护栏漏掉一轮。

    这个复位点必须只在成功那条出口上。若写成「失败也复位」（如 try/except 里复位、
    或把复位放进 finally），`_fetch_page` 自己的 `except` 会把标志位再置回去，
    所以单看抓取路径**看不出来**；真正漏的是另一个调用方 `auto_login`（前端
    「重新登录」按钮走的无头自动登录）—— 那里失败后标志位被抹成 False，用户下次
    抓取又会把窗口拆一次并登不回去，白白多挨一轮。
    """
    monkeypatch.setattr(banweb, "_manual_login_required", True)
    monkeypatch.setattr(banweb, "_kill_zombie_chrome",
                        lambda: pytest.fail("这条路径不该拆窗口"))
    # 收窄到浏览器边界：让 Okta 表单那步立刻判为出错（不替身整个表单流程，
    # 也不用真等 15s 轮询）。
    monkeypatch.setattr(banweb, "_wait_for_any",
                        lambda page, selectors, timeout=15000: banweb._OKTA_ERROR)
    monkeypatch.setattr(banweb, "_extract_okta_error", lambda page: "账号被锁")
    page = _LoginFailedPage()
    ctx = _Ctx(["https://banweb.cityu.edu.hk/x"])
    ctx.pages[0] = page
    monkeypatch.setattr(banweb, "_pw", _Driver(_Browser(ctx)))
    monkeypatch.setattr(banweb, "_page", page)
    monkeypatch.setattr(banweb, "_ctx", ctx)
    monkeypatch.setattr(banweb, "_browser_headless", True)
    with pytest.raises(banweb.BanwebError):
        banweb._login_with("eid", "pw")
    assert banweb._manual_login_required is True


def test_require_logged_in_message_points_at_the_relogin_button(monkeypatch):
    """未登录的文案必须指向**真会发生的事**：先试无头自动登录，失败才开窗。

    原文案「请在新打开的浏览器窗口登录后再试」在无头自动登录这条路上是**假的** ——
    那个窗口只有在用户点了「重新登录」、且自动登录也失败之后才会出现；照着它等，
    用户会一直等一个不会来的窗口。
    """
    page = _Page("https://auth.cityu.edu.hk/login")
    with pytest.raises(banweb.BanwebError) as exc:
        banweb._require_logged_in(page)
    msg = str(exc.value)
    assert "重新登录" in msg              # 肯定式：指对了入口
    assert "新打开的浏览器窗口" not in msg  # 不再无条件承诺一个窗口
