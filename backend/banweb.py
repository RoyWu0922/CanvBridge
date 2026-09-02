"""CityU AIMS (Banweb) 课表自动抓取。

会话策略（用户 2026-08-31 确认）：
- 账号密码存在本机钥匙串（backend/credentials.py），只存本地、不上传。
- 后端用系统 Chrome 打开一个常驻 **无头** 浏览器（独立 profile + CDP 端口）；
  检测到退登时用钥匙串里的账密自动填 Okta 表单登录，全程不弹窗口。
- 仅在自动登录失败（密码错/验证码等）或用户主动点「重新登录」时才临时
  以有头窗口打开，供手动完成。这反转了早期「程序绝不代登录、不接触凭证」
  的决策，属用户明确批准。
"""
from __future__ import annotations

import concurrent.futures
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

from . import credentials

try:
    from playwright.sync_api import sync_playwright
except Exception:  # 未装 Playwright 时不影响其余功能
    sync_playwright = None

LOGIN_HOST = "https://auth.cityu.edu.hk"
BANWEB = "https://banweb.cityu.edu.hk"
TERM_PAGE = BANWEB + "/pls/PROD/bwskfshd.P_CrseSchdDetl"
# 真实登录页：登出后 TERM_PAGE 只给「User Login」中转页（无表单），
# 真正的 Okta 登录表单在 P_WWWLogin（2026-08-31 实测标题 "City University of Hong Kong - Sign In"）
P_WWWLOGIN = BANWEB + "/pls/PROD/twgkpswd_cityu.P_WWWLogin"
# 周课表（Matrix Format）页：每格「楼宇码 房间号」，即前端课表块的简称地点来源
WEEKLY_PAGE = BANWEB + "/pls/PROD/hwsstmtbl_matrix_cityu.Show"
# 考试时间表（Site Map「Examination Timetable」实测）：只显示当前注册学期
EXAM_PAGE = BANWEB + "/pls/PROD/hwsrsett_cityu.P_DispSchd"
_EXAM_NOT_AVAILABLE = "currently not available"
_EXAM_COLUMN_KEYS = {
    "course": ("course", "subject", "课程"),
    "section": ("section", "sec", "分班"),
    "date": ("date", "日期"),
    "time": ("time", "时间"),
    "venue": ("venue", "building", "地点"),
    "room": ("room", "房间"),
    "seat": ("seat", "no.", "座位"),
}
PROFILE_DIR = Path.home() / ".cityu_aims_profile"
CDP_PORT = 9339
# Okta Sign-In Widget v2（auth.cityu.edu.hk，2026-08-31 实测）：两步登录
# 第一步输 EID（name=identifier）点 Next，第二步输密码（name=credentials.passcode）
_OKTA_IDENTIFIER = 'input[name="identifier"]'
_OKTA_PASSCODE = 'input[name="credentials.passcode"]'
_OKTA_SUBMIT = 'input[type="submit"], button[type="submit"], button[data-type="save"]'
_OKTA_ERROR = '[data-se="o-form-error-container"]'

_lock = threading.RLock()
_pw = None
_ctx = None
_page = None
_browser_headless: bool | None = None   # 当前浏览器是否无头（None=附着到外部实例）
_browser_executor: concurrent.futures.ThreadPoolExecutor | None = None


class BanwebError(RuntimeError):
    """AIMS 抓取相关错误（未登录 / 未安装 Playwright / 解析失败等）。"""


# ---------------- 纯解析（可单测） ----------------

_CAP_RE = re.compile(r"^(.*?)\s*-\s*([A-Z]{2,4}\s*\d{4})\s*-\s*([A-Z0-9]+)$")
_WEEKDAY = {"M": "Mon", "T": "Tue", "W": "Wed", "R": "Thu", "F": "Fri", "S": "Sat", "U": "Sun"}
_WEEKDAY_0 = {"M": 0, "T": 1, "W": 2, "R": 3, "F": 4, "S": 5, "U": 6}
_WEEKDAY_LETTER = {  # 周课表表头英文全名 → 单字母
    "Monday": "M", "Tuesday": "T", "Wednesday": "W", "Thursday": "R",
    "Friday": "F", "Saturday": "S", "Sunday": "U",
}
_WEEKLY_CELL_RE = re.compile(r"^(\d+)\s+([A-Z]{2,4}\d{4}-[A-Z0-9]+)\s+(.*)$")
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2}) (am|pm)\s*-\s*(\d{1,2}):(\d{2}) (am|pm)$")
_RANGE_RE = re.compile(r"^([A-Z][a-z]{2}) (\d{1,2}), (\d{4}) - ([A-Z][a-z]{2}) (\d{1,2}), (\d{4})$")


class _TableParser(HTMLParser):
    """逐 <table> 收集 caption 与全部单元格行。"""

    def __init__(self):
        super().__init__()
        self.tables: list[dict] = []
        self.cur: dict | None = None
        self.intable = self.intd = self.incap = False
        self.cell: list[str] = []
        self.row: list[str] = []
        self.cap: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table" and not self.intable:
            self.intable = True
            self.cur = {"cap": "", "rows": []}
        if tag == "caption" and self.intable:
            self.incap = True
            self.cap = []
        if tag == "tr" and self.intable:
            self.row = []
        if tag in ("td", "th") and self.intable:
            self.intd = True
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.intd:
            self.intd = False
            self.row.append(" ".join("".join(self.cell).split()))
        if tag == "tr" and self.intable and self.row:
            self.cur["rows"].append(self.row)
        if tag == "caption" and self.incap:
            self.incap = False
            self.cur["cap"] = " ".join("".join(self.cap).split())
        if tag == "table" and self.intable:
            self.intable = False
            self.tables.append(self.cur)
            self.cur = None

    def handle_data(self, data):
        if self.incap:
            self.cap.append(data)
        if self.intd:
            self.cell.append(data)


def parse_schedule_html(html: str) -> list[dict]:
    """解析 Student Detail Schedule 页面，返回课程块列表。

    每块字段：course/code/section/crn/credits/meetings。
    meetings 字段：type/time/days/room/range/instr；无固定时间的课 meetings 为空列表。
    """
    parser = _TableParser()
    parser.feed(html)
    results: list[dict] = []
    cur: dict | None = None
    for t in parser.tables:
        m = _CAP_RE.match(t["cap"])
        if m:
            cur = {
                "course": m.group(1).strip(),
                "code": re.sub(r"\s", "", m.group(2)),
                "section": m.group(3).strip(),
                "crn": "",
                "credits": "",
                "meetings": [],
            }
            for r in t["rows"]:
                if len(r) >= 2 and r[0].startswith("CRN"):
                    cur["crn"] = r[1]
                elif len(r) >= 2 and r[0].startswith("Credits"):
                    cur["credits"] = r[1].strip()
            results.append(cur)
        elif cur and t["cap"] == "Scheduled Meeting Times":
            for r in t["rows"]:
                if len(r) == 7 and r[0] not in ("Type",):
                    cur["meetings"].append({
                        "type": r[0], "time": r[1], "days": r[2],
                        "room": r[3], "range": r[4], "instr": r[6],
                    })
    return results


class _WeeklyMatrixParser(HTMLParser):
    """解析周课表矩阵表（class=ctt-matrix）：每行一个时间槽，7 列对应周一到周日。

    与 _TableParser 不同：矩阵格内不同行（CRN / 课程-分班 / 楼宇 房间）由 <br>
    分隔，相邻文本片段之间本无空格，需用空格 join 片段后再折叠空白，否则会
    粘连成 "C01MMW"。空白格是 &nbsp;，HTMLParser 不产生文本 → 判为空串。
    """

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._in_matrix = False
        self._intd = False
        self._cell: list[str] = []
        self._row: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            cls = dict(attrs).get("class", "").split()
            if "ctt-matrix" in cls:
                self._in_matrix = True
            return
        if not self._in_matrix:
            return
        if tag in ("td", "th"):
            self._intd = True
            self._cell = []
        elif tag == "tr":
            self._row = []

    def handle_endtag(self, tag):
        if not self._in_matrix:
            return
        if tag in ("td", "th") and self._intd:
            self._intd = False
            self._row.append(" ".join(" ".join(self._cell).split()))
        elif tag == "tr" and self._row:
            self.rows.append(self._row)
        elif tag == "table":
            self._in_matrix = False

    def handle_data(self, data):
        if self._intd:
            self._cell.append(data)


def parse_weekly_schedule_html(html: str) -> list[dict]:
    """解析 AIMS 周课表（Matrix Format）页面，返回每个课程块的简称地点。

    每块字段：crn/code/section/day/room_short；day 为星期字母（M=周一）。
    矩阵格内容三行：CRN、COURSE-SEC、楼宇码 房间号（如 "MMW 2450"）——
    「楼宇码 房间号」正是前端课表块要展示的简称地点，按 crn + 星期挂到
    详情页对应 meeting 上。
    """
    p = _WeeklyMatrixParser()
    p.feed(html)
    out: list[dict] = []
    if not p.rows:
        return out
    day_cols: list[tuple[int, str]] = []
    for i, h in enumerate(p.rows[0]):
        if h in _WEEKDAY_LETTER:
            day_cols.append((i, _WEEKDAY_LETTER[h]))
    for row in p.rows[1:]:
        for ci, day in day_cols:
            cell = row[ci] if ci < len(row) else ""
            m = _WEEKLY_CELL_RE.match(cell)
            if not m:
                continue
            crn, cs, short = m.group(1), m.group(2), m.group(3).strip()
            code, section = cs.rsplit("-", 1)
            out.append({
                "crn": crn, "code": code, "section": section,
                "day": day, "room_short": short,
            })
    return out


def merge_room_short(courses: list[dict], weekly: list[dict]) -> list[dict]:
    """把周课表的简称地点挂到详情页对应 meeting 上（按 crn + 星期匹配）。

    找不到对应 meeting（周课表 term 与详情页不同、课程不在矩阵里）时跳过，
    前端回退显示完整地点。原地修改入参——courses 是 parse_schedule_html 的
    新产出，调用方随后 enrich_meetings 会逐层复制，无共享风险。
    """
    by_crn: dict[str, dict] = {}
    for c in courses:
        if c.get("crn"):
            by_crn[c["crn"]] = c
    for w in weekly:
        c = by_crn.get(w["crn"])
        if not c:
            continue
        for m in c.get("meetings", []):
            if w["day"] in m.get("days", ""):
                m["room_short"] = w["room_short"]
                break
    return courses


def _to_24(h: str, mm: str, ap: str) -> tuple[int, int]:
    h, mm = int(h), int(mm)
    if ap == "am" and h == 12:
        h = 0
    elif ap == "pm" and h != 12:
        h += 12
    return h, mm


def parse_time_range(s: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """'12:00 pm - 2:50 pm' → ((12,0),(14,50))。"""
    m = _TIME_RE.match(s.strip())
    if not m:
        raise BanwebError(f"无法解析时间: {s!r}")
    sh, sm, sap, eh, em, eap = m.groups()
    return _to_24(sh, sm, sap), _to_24(eh, em, eap)


def parse_date_range(s: str) -> tuple[datetime, datetime]:
    """'Aug 31, 2026 - Nov 28, 2026' → (datetime, datetime)。"""
    m = _RANGE_RE.match(s.strip())
    if not m:
        raise BanwebError(f"无法解析日期范围: {s!r}")
    d0 = datetime(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
    d1 = datetime(int(m.group(6)), _MONTHS[m.group(4)], int(m.group(5)))
    return d0, d1


def _split_exam_time(s: str) -> tuple[str, str]:
    """"14:30 - 17:30" / "2:30 pm - 5:30 pm" → ("14:30","17:30")；解析失败返回 (s,"")。"""
    s = s.strip()
    if not s:
        return "", ""
    m = re.match(r"(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})$", s)
    if m:
        return (f"{int(m.group(1)):02d}:{m.group(2)}",
                f"{int(m.group(3)):02d}:{m.group(4)}")
    try:
        (sh, sm), (eh, em) = parse_time_range(s)   # 12h "2:30 pm - 5:30 pm"
        return f"{sh:02d}:{sm:02d}", f"{eh:02d}:{em:02d}"
    except BanwebError:
        return s, ""


def _parse_exam_date(s: str) -> str:
    """"2026-12-15" / "15/12/2026" / "15 Dec 2026" → "YYYY-MM-DD"；失败返回 "". """
    s = s.strip()
    if not s:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    m = re.fullmatch(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})", s)
    if m:
        month = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                 "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
        mon = month.get(m.group(2).capitalize()[:3])
        if mon:
            return f"{m.group(3)}-{mon:02d}-{int(m.group(1)):02d}"
    return ""


def _map_exam_columns(rows: list[list[str]]) -> dict | None:
    """按表头关键词把列名映射到列下标；无 date 也无 time 列 → None。"""
    if not rows:
        return None
    header = [h.lower() for h in rows[0]]
    mapping: dict[str, int] = {}
    for field, keys in _EXAM_COLUMN_KEYS.items():
        for i, h in enumerate(header):
            if any(k in h for k in keys):
                mapping[field] = i
                break
    if "date" not in mapping and "time" not in mapping:
        return None
    return mapping


def _row_to_exam(row: list[str], cols: dict) -> dict | None:
    def _cell(field: str) -> str:
        i = cols.get(field)
        return row[i].strip() if i is not None and i < len(row) else ""

    course = _cell("course")
    if not course and not _cell("date") and not _cell("time"):
        return None
    m = re.search(r"[A-Z]+\d{4}", course.upper())
    code = m.group(0) if m else re.sub(r"\s", "", course)
    start, end = _split_exam_time(_cell("time"))
    return {
        "course": course,
        "code": code,
        "section": _cell("section"),
        "date": _parse_exam_date(_cell("date")),
        "start": start,
        "end": end,
        "room": _cell("room") or _cell("venue"),
        "seat": _cell("seat"),
    }


def parse_exam_html(html: str) -> list[dict]:
    """解析考试时间表页面，返回考试块列表。

    含 "currently not available" → []。否则遍历 _TableParser 的表，找表头含
    date/time 关键词的数据表，按列映射解析每行（不依赖列序）。
    """
    if _EXAM_NOT_AVAILABLE in html:
        return []
    parser = _TableParser()
    parser.feed(html)
    for t in parser.tables:
        cols = _map_exam_columns(t["rows"])
        if not cols:
            continue
        out = []
        for row in t["rows"][1:]:
            rec = _row_to_exam(row, cols)
            if rec:
                out.append(rec)
        return out
    return []


def first_occurrence(d0: datetime, day_letter: str) -> datetime:
    """返回 >= d0 的第一个指定星期几（M=周一）。"""
    diff = (_WEEKDAY_0[day_letter] - d0.weekday()) % 7
    return d0 + timedelta(days=diff)


def build_event_specs(courses: list[dict], selected: set[str]) -> list[dict]:
    """把选中的课程块转成每周重复事件规格。

    selected 元素形如 'CS1315:C01'。无固定时间的课（meetings 为空）不会产出规格。
    规格字段：key('CS1315:C01:F')/block('CS1315:C01')/title/start/end/until/location/notes。
    """
    specs: list[dict] = []
    for c in courses:
        block = f"{c['code']}:{c['section']}"
        if block not in selected:
            continue
        multi = len(c["meetings"]) > 1 or any(len(m.get("days", "")) > 1 for m in c["meetings"])
        for m in c["meetings"]:
            try:
                (sh, sm), (eh, em) = parse_time_range(m["time"])
                d0, d1 = parse_date_range(m["range"])
            except BanwebError:
                continue  # 单节解析失败只跳过，不拖垮整批
            room = (m.get("room") or "").strip()
            instr = re.sub(r"\s*\([^)]*\)\s*$", "", m.get("instr") or "").strip()
            for day in m.get("days", ""):
                if day not in _WEEKDAY:
                    continue
                first = first_occurrence(d0, day)
                wd = _WEEKDAY[day]
                if multi:
                    title = f"{c['code']} {c['section']} {wd} · {c['course']}"
                else:
                    title = f"{c['code']} {c['section']} · {c['course']}"
                specs.append({
                    "key": f"{block}:{day}",
                    "block": block,
                    "title": title,
                    "start": first.replace(hour=sh, minute=sm).strftime("%Y-%m-%dT%H:%M:%S"),
                    "end": first.replace(hour=eh, minute=em).strftime("%Y-%m-%dT%H:%M:%S"),
                    "until": d1.strftime("%Y-%m-%d"),
                    "location": room,
                    "notes": instr,
                })
    return specs


def primary_instructor(course: dict) -> str:
    """取课程块的主讲师（显示用，前端筛选下拉直接取用）。

    遍历 meetings 的 instr：优先取含 (P) 主讲师标记的第一个并剥掉标记；
    无 (P) 则取第一个非空 instr。无则返回空串。
    """
    for m in course.get("meetings", []):
        instr = (m.get("instr") or "").strip()
        if instr and "(P)" in instr:
            return re.sub(r"\s*\(P\)\s*$", "", instr).strip()
    for m in course.get("meetings", []):
        instr = (m.get("instr") or "").strip()
        if instr:
            return instr
    return ""


def enrich_meetings(courses: list[dict]) -> list[dict]:
    """为每个 meeting 追加 start_min/end_min/days_list 供前端日历落位。

    原 time/days/range 字段不变。无固定时间（time 解析失败）的 meeting 不追加定位字段。
    不修改入参：每个 course 与 meeting 都复制一份。
    """
    out: list[dict] = []
    for c in courses:
        c2 = dict(c)
        meetings = []
        for m in c.get("meetings", []):
            m2 = dict(m)
            try:
                (sh, sm), (eh, em) = parse_time_range(m["time"])
                m2["start_min"] = sh * 60 + sm
                m2["end_min"] = eh * 60 + em
                days = m.get("days", "")
                if days and all(ch in _WEEKDAY for ch in days):
                    m2["days_list"] = list(days)
            except BanwebError:
                pass
            meetings.append(m2)
        c2["meetings"] = meetings
        c2["primary_instructor"] = primary_instructor(c)
        out.append(c2)
    return out


# ---------------- 课表同步（删旧建新） ----------------

_WEEKDAY_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"]


def _fmt_range(ev: dict) -> str:
    """把事件 start/end 格式化成 'Fri 12:00-14:50' 供结果展示。"""
    st = datetime.fromisoformat(ev["start"])
    en = datetime.fromisoformat(ev["end"])
    return f"{_WEEKDAY_FULL[st.weekday()][:3]} " \
           f"{st.strftime('%H:%M')}-{en.strftime('%H:%M')}"


def reconcile_block(specs: list[dict], existing: list[dict]) -> dict:
    """对比一个课程块的新规格与日历现有事件，算出保留/编辑/新建/隐藏。

    specs：build_event_specs 中同一 block 的所有规格（含 title/start/end）
    existing：apple_script.find_events 按 "{code} {section}" 前缀读回的事件
              （每条含 summary/start/end，ISO 格式）
    匹配 key = (星期, 开始 HH:MM, 结束 HH:MM, 标题)：
      key 全等          → exists（跳过保留）
      标题相同但时间/天变 → update（原地编辑旧事件到新时间，old 是它要编辑的旧事件）
      时间相同但标题变    → update（原地改标题，old 同理）
      以上都没有        → create（新建）
    旧事件没有任何新规格对应 → remove（隐藏，把重复截止改到过去）
    返回：
      create: 需新建的规格
      update: 需原地编辑的 [{spec, old, old_time}]，old_time 为旧时间展示串
      exists: 已存在、跳过保留的规格
      remove: 需隐藏的旧事件 summary 列表
      removed: remove 的长度
    """
    def _key(wd, sh, sm, eh, em, title):
        return (wd, f"{sh:02d}:{sm:02d}", f"{eh:02d}:{em:02d}", title)

    def _time_key(st, en):
        return (st.strftime("%A"), f"{st.hour:02d}:{st.minute:02d}",
                f"{en.hour:02d}:{en.minute:02d}")

    spec_keys: dict[tuple, dict] = {}
    for sp in specs:
        st = datetime.fromisoformat(sp["start"])
        en = datetime.fromisoformat(sp["end"])
        spec_keys[_key(st.strftime("%A"), st.hour, st.minute,
                       en.hour, en.minute, sp["title"])] = sp

    old_by_key: dict[tuple, dict] = {}
    old_by_title: dict[str, list[dict]] = {}
    old_by_time: dict[tuple, list[dict]] = {}
    for ev in existing:
        st = datetime.fromisoformat(ev["start"])
        en = datetime.fromisoformat(ev["end"])
        old_by_key[_key(st.strftime("%A"), st.hour, st.minute,
                        en.hour, en.minute, ev["summary"])] = ev
        old_by_title.setdefault(ev["summary"], []).append(ev)
        old_by_time.setdefault(_time_key(st, en), []).append(ev)

    used: set[int] = set()   # 已被 update 认领的旧事件 id
    exists, update, create = [], [], []
    for key, sp in spec_keys.items():
        if key in old_by_key:
            used.add(id(old_by_key[key]))
            exists.append(sp)
            continue
        old_ev = next((ev for ev in old_by_title.get(sp["title"], [])
                       if id(ev) not in used), None)
        if old_ev is None:  # 时间变了但标题没变 → 按时间找（改名场景）
            st = datetime.fromisoformat(sp["start"])
            en = datetime.fromisoformat(sp["end"])
            old_ev = next((ev for ev in old_by_time.get(_time_key(st, en), [])
                           if id(ev) not in used), None)
        if old_ev is not None:
            used.add(id(old_ev))
            update.append({"spec": sp, "old": old_ev,
                           "old_time": _fmt_range(old_ev)})
        else:
            create.append(sp)

    remove = list(dict.fromkeys(
        ev["summary"] for k, ev in old_by_key.items() if id(ev) not in used))
    return {"create": create, "update": update, "exists": exists,
            "remove": remove, "removed": len(remove)}


# ---------------- 浏览器与会话 ----------------

def _is_login_page(url: str) -> bool:
    return url.startswith(LOGIN_HOST) or "twgkpswd" in url


def _is_embedded_login(page) -> bool:
    """Banner 退登后会在 banweb URL 内嵌 "User Login" 页（标题即 User Login），
    URL 不变但实际已退登，需靠页面内容识别。"""
    try:
        return "User Login" in page.title()
    except Exception:
        return False


def _require_logged_in(page) -> None:
    if _is_login_page(page.url) or _is_embedded_login(page):
        raise BanwebError("尚未登录 AIMS：请在新打开的浏览器窗口登录后再试")


def _find_page(ctx) -> object:
    for p in ctx.pages:
        if p.url.startswith(BANWEB) or p.url.startswith(LOGIN_HOST):
            return p
    return ctx.pages[0] if ctx.pages else ctx.new_page()


def _on_browser_thread(fn):
    """在专属线程上运行 fn，返回其返回值。

    Playwright sync API 的 driver 绑定在第一次 start() 它的线程上；FastAPI 的
    sync 端点跑在线程池里，换线程再调用会抛 "Cannot switch to a different
    thread"（实测 launch_persistent_context 必炸）。用单 worker 的线程池把
    所有浏览器操作钉在一个线程上。
    """
    global _browser_executor
    if _browser_executor is None:
        _browser_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="banweb-browser")
    return _browser_executor.submit(fn).result(timeout=180)


def _target_closed(exc: Exception) -> bool:
    """Playwright 报「Target page, context or browser has been closed」的错误特征。

    常见于用户手动关掉了抓取窗口、或 CDP 会话中断（后端重启/休眠等）。
    """
    msg = str(exc)
    return "has been closed" in msg or "TargetClosedError" in msg


def _reset_browser() -> None:
    """清空页面与浏览器句柄；保留 Playwright 驱动实例 _pw 以便复用。

    不能把 _pw 也置空：否则下次会 sync_playwright().start() 新实例，而旧实例
    未 stop() 会触发 "using Playwright Sync API inside the asyncio loop"。
    同一个驱动实例可以反复 connect_over_cdp / launch_persistent_context。
    """
    global _pw, _ctx, _page
    _ctx, _page = None, None


def _retry_once(fn):
    """把目标已关闭错误当临时故障：重置浏览器后整体重试一次。

    Playwright 在浏览器被关掉的瞬间，部分属性（如 .url）仍返回旧缓存不报错，
    直到真正的 CDP 操作（eval/select）才抛 TargetClosedError。单靠 _ensure_browser
    的存活探测不够，必须在整条操作链上兜底。
    """
    try:
        return fn()
    except Exception as exc:
        if not _target_closed(exc):
            raise
        _reset_browser()
        return fn()


def _is_driver_error(exc: Exception) -> bool:
    """Playwright driver（底层 node 进程）崩掉的错误特征。"""
    return "Connection closed while reading from the driver" in str(exc)


def _restart_driver() -> None:
    """driver 崩了时整机重启：先 stop 旧驱动，再 start 新实例。

    必须先 stop 再 start：旧实例没停就新建会触发
    "using Playwright Sync API inside the asyncio loop"。
    """
    global _pw
    try:
        _pw.stop()
    except Exception:
        pass
    _pw = sync_playwright().start()


def _kill_zombie_chrome() -> None:
    """清掉占住 CDP 端口的僵死 Chrome，为重新开窗腾地方。

    macOS 上用户关掉登录窗口后，Chrome 主进程可能仍常驻（还占着 9339），且
    命令行里不一定带 profile 路径，按路径 pkill 会漏。双管齐下：
    1) 按端口 lsof 拿 PID 强杀（不依赖命令行匹配）；
    2) 再按 profile 路径 pkill -9 兜底（清理只占 profile 锁、没占端口的进程）。
    只在该端口已连不上（connect_over_cdp 失败）时才会走到这里，不会误杀活窗口。
    """
    try:
        out = subprocess.run(
            ["lsof", "-nP", "-tiTCP", f"{CDP_PORT}", "-sTCP:LISTEN"],
            capture_output=True, text=True, check=False)
        for pid in out.stdout.split():
            subprocess.run(["kill", "-9", pid], check=False)
    except Exception:
        pass
    try:
        subprocess.run(["pkill", "-9", "-f", str(PROFILE_DIR)], check=False)
    except Exception:
        pass
    # 等端口真正释放（最多 ~3 秒），否则立刻 launch 仍可能撞上残留
    for _ in range(30):
        try:
            out = subprocess.run(
                ["lsof", "-nP", "-i", f":{CDP_PORT}"],
                capture_output=True, text=True, check=False)
            if not out.stdout.strip():
                return
        except Exception:
            return
        time.sleep(0.1)


def _ensure_browser(launch: bool = True, headless: bool = True):
    """返回一个可用的浏览器页面。

    launch=False 供状态轮询用：**绝不自己弹窗**。浏览器已关时抛 BanwebError，
    由调用方判为 needs_login，等自动登录或用户点「重新登录」再开。
    launch=True 供主动操作（自动登录/抓课表/手动登录）用：确保浏览器真的打开。
    headless=False 供手动登录用：若当前是无头浏览器，先关掉换有头窗口，
    让用户能看见登录页。
    """
    global _pw, _ctx, _page, _browser_headless
    with _lock:
        if sync_playwright is None:
            raise BanwebError("未安装 Playwright，无法抓取课表（pip install playwright）")
        if _page is not None:
            try:
                # 真存活探测：做一次 CDP 往返。is_closed() 对已断开的连接可能仍返回
                # False，导致拿旧对象当活窗口用（open_login 因此"成功"却没开窗）。
                _page.evaluate("1")
                if headless or _browser_headless is not True:
                    # 请求无头（默认）或当前已是有头窗口 → 直接用
                    return _page
                # 请求有头但当前是无头 → 关掉无头，走下方重建有头窗口
                _reset_browser()
            except Exception:
                _reset_browser()
        if _pw is None:
            _pw = sync_playwright().start()
        # 1) 附着仍在运行的实例（登录态得以保留）
        try:
            browser = _pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            try:
                if browser.contexts:
                    _ctx = browser.contexts[0]
                    if _ctx.pages:
                        # 已有窗口（抓取页 / 登录页 / 残留空白页）→ 直接用
                        _page = _find_page(_ctx)
                        return _page
                    if launch:
                        # 用户主动操作且进程还活着但没窗口 → 开个新标签页当窗口
                        _page = _ctx.new_page()
                        return _page
                    # 轮询（launch=False）：进程在但没窗口 → 视为未开窗，
                    # 绝不自己弹窗。干净断开连接后交给下方 needs_login 分支。
                    try:
                        browser.close()
                    except Exception:
                        pass
            except Exception:
                # 附着了但用不上（如窗口刚被用户关掉）→ 先干净断开 driver 里的
                # 连接再走清理。否则直接 kill 底层 Chrome 会打崩 driver，后续
                # 所有 launch 都报 "Connection closed while reading from the driver"。
                try:
                    browser.close()
                except Exception:
                    pass
        except Exception:
            pass
        if not launch:
            raise BanwebError("AIMS 登录窗口未打开，请点「重新登录」打开后再试")
        # 2) 可能残留占住端口的僵死 Chrome（macOS 关窗后进程常驻）→ 清掉再开
        _kill_zombie_chrome()
        # 3) 启动常驻 Chrome（独立 profile，避免碰系统主 profile）；默认无头
        try:
            _ctx = _launch_persistent(headless=headless)
        except Exception as exc:
            if _is_driver_error(exc):
                # driver 被残留连接打崩 → 重启驱动后再试一次
                _reset_browser()
                _restart_driver()
                try:
                    _ctx = _launch_persistent(headless=headless)
                except Exception as exc2:
                    raise BanwebError(
                        "无法打开浏览器。若旧窗口还在运行，请先关闭它再重试。") from exc2
            else:
                raise BanwebError(
                    "无法打开浏览器。若旧窗口还在运行，请先关闭它再重试。") from exc
        _page = _ctx.pages[0] if _ctx.pages else _ctx.new_page()
        _browser_headless = headless
        try:
            _page.goto(TERM_PAGE, timeout=60000, wait_until="domcontentloaded")
        except Exception:
            pass  # 未登录会跳转到 Okta，属正常
        return _page


def _launch_persistent(headless: bool = True):
    """用当前 driver 启动常驻 Chrome（默认无头，独立 profile）。"""
    return _pw.chromium.launch_persistent_context(
        str(PROFILE_DIR), channel="chrome", headless=headless,
        viewport={"width": 1280, "height": 900},
        args=[f"--remote-debugging-port={CDP_PORT}"])


def _judge_status(url: str, title: str | None) -> str:
    """根据当前 URL 与标题判定登录态。

    - 退登后的 Banner 内嵌登录页：URL 仍是 banweb、但 title 是 "User Login"。
    - title 读不到（页面还在跳转）时不能当已登录：宁可报过渡态 opening，
      让前端继续轮询，也不要误判 logged_in（会停掉轮询、隐藏登录按钮）。
    """
    if _is_login_page(url):
        return "needs_login"
    if title and "User Login" in title:
        return "needs_login"
    if url.startswith(BANWEB):
        return "logged_in" if title is not None else "opening"
    return "opening"


def get_status() -> dict:
    """返回 {ok, status}；status ∈ opening / needs_login / logged_in / error。"""
    def _run() -> dict:
        try:
            with _lock:
                # launch=False：轮询绝不自己弹窗；窗口被用户关掉时视为 needs_login
                page = _ensure_browser(launch=False)
                # 页面若正导航，title() 会抛错 → 先等加载稳定
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
                url = page.url
                title = None
                try:
                    title = page.title()
                except Exception:
                    pass  # 导航中读不到标题：交给 _judge_status 兜底
        except BanwebError as exc:
            if "Playwright" in str(exc):
                return {"ok": False, "status": "error", "error": str(exc)}
            # 浏览器未开（用户关了窗口）→ 需要重新打开登录窗口
            return {"ok": True, "status": "needs_login"}
        except Exception as exc:
            if _target_closed(exc):
                raise  # 目标关闭：交给 _retry_once 整体重建后重试
            return {"ok": False, "status": "error", "error": str(exc)}
        return {"ok": True, "status": _judge_status(url, title)}
    return _on_browser_thread(lambda: _retry_once(_run))


def open_login() -> None:
    """确保登录窗口打开并置前，让用户手动登录（自动登录失败时的兜底）。

    无头浏览器在运行时会临时换有头窗口；若退登，导航课表页会自动落到
    Okta 登录页（或 Banner 内嵌登录页）。
    """
    def _run() -> None:
        with _lock:
            page = _ensure_browser(launch=True, headless=False)
            try:
                page.goto(TERM_PAGE, timeout=30000, wait_until="domcontentloaded")
            except Exception:
                pass  # 未登录会跳转 Okta，属正常
            try:
                page.bring_to_front()
            except Exception:
                pass
            # 验证窗口真的可用：若目标已关（evaluate 抛 TargetClosedError），
            # 交给 _retry_once 整体重建后重试，而不是"成功"却没开窗。
            page.evaluate("1")
    _on_browser_thread(lambda: _retry_once(_run))


# ---------------- 自动登录（填 Okta 表单） ----------------

def _wait_for_any(page, selectors: list[str], timeout: float = 15000) -> str | None:
    """轮询 page.wait_for_selector，任一选择器命中即返回它；超时返回 None。

    用 wait_for_selector（自带超时）而不是 query_selector：Banner 的「User Login」
    中转页 frame 可能永远停在加载态，query_selector 会无限阻塞（实测卡 120s+），
    而 wait_for_selector 每次最多等 3s，整体预算 = timeout，绝不可能挂死。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for s in selectors:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            try:
                page.wait_for_selector(s, timeout=min(remaining, 3000),
                                       state="visible")
                return s
            except Exception:
                pass
    return None


def _extract_okta_error(page) -> str:
    """从 Okta 错误容器 [data-se=o-form-error-container] 提取可见错误文本。"""
    try:
        parts = page.eval_on_selector_all(
            _OKTA_ERROR,
            "els => els.map(e => e.textContent.trim()).filter(Boolean)")
    except Exception:
        return ""
    return " ".join(" ".join(p.split()) for p in parts)


def _raise_login_error(page, prefix: str = "AIMS 自动登录失败") -> None:
    """抛出带 Okta 错误信息的 BanwebError；无错误信息时给通用提示。"""
    msg = _extract_okta_error(page)
    detail = f"：{msg}" if msg else "（页面未跳回课表，可能有验证码或账号问题）"
    raise BanwebError(prefix + detail)


def auto_login(username: str, password: str) -> str:
    """用账号密码自动登录 AIMS（Okta 两步表单：EID → Next → 密码）。

    全程无头、不 bring_to_front。已是登录态直接返回；成功返回 'logged_in'；
    失败抛 BanwebError（带 Okta 错误信息）。
    """
    def _run() -> str:
        with _lock:
            page = _ensure_browser(launch=True, headless=True)
            try:
                page.goto(TERM_PAGE, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_load_state("load", timeout=30000)
            except Exception:
                pass  # 未登录会跳到 Okta / 中转页，属正常
            if not (_is_login_page(page.url) or _is_embedded_login(page)):
                return "logged_in"  # 已是登录态（附着了有会话的浏览器/外部实例）
            # 中转页：TERM_PAGE 上只有标题 "User Login"、无表单。真正的 Okta
            # 登录页在 P_WWWLogin，先导航过去再走两步表单。
            if page.url.startswith(BANWEB) and not page.url.startswith(LOGIN_HOST):
                try:
                    page.goto(P_WWWLOGIN, timeout=30000, wait_until="domcontentloaded")
                    # 等 Okta widget 真正加载完，否则 fill 的值会被初始化清掉
                    page.wait_for_load_state("load", timeout=30000)
                except Exception:
                    pass
            # 第一步：填 EID → Next
            if _wait_for_any(page, [_OKTA_IDENTIFIER, _OKTA_ERROR], 15000) != _OKTA_IDENTIFIER:
                _raise_login_error(page)
            page.fill(_OKTA_IDENTIFIER, username)
            page.click(_OKTA_SUBMIT, timeout=10000)
            # 第二步：等密码框出现；出现错误（用户名无效/验证码）则报错
            if _wait_for_any(page, [_OKTA_PASSCODE, _OKTA_ERROR], 15000) != _OKTA_PASSCODE:
                _raise_login_error(page)
            page.fill(_OKTA_PASSCODE, password)
            page.click(_OKTA_SUBMIT, timeout=10000)
            # 提交后：等待跳出 Okta 域（成功会重定向回 banweb）
            try:
                page.wait_for_url(lambda u: not _is_login_page(u),
                                  timeout=20000, wait_until="domcontentloaded")
                page.wait_for_load_state("load", timeout=30000)
            except Exception:
                _raise_login_error(page)  # 密码错误等：Okta 页面上的错误
            _require_logged_in(page)
            return "logged_in"
    return _on_browser_thread(lambda: _retry_once(_run))


def auto_login_from_stored() -> str:
    """从钥匙串读账号密码并自动登录。返回状态串；无凭据时抛 BanwebError。"""
    creds = credentials.get_credentials()
    if not creds:
        raise BanwebError("尚未保存 AIMS 账号密码，请先在设置中填写")
    username, password = creds
    return auto_login(username, password)


def _goto_term_page(page) -> None:
    try:
        page.goto(TERM_PAGE, timeout=60000, wait_until="domcontentloaded")
    except Exception:
        pass
    _require_logged_in(page)


def list_terms() -> list[dict]:
    """登录后返回 [{value, label}] 学期列表。"""
    def _run() -> list[dict]:
        with _lock:
            page = _ensure_browser()
            _goto_term_page(page)
            opts = page.eval_on_selector_all(
                "select[name=term_in] option",
                "els => els.map(e => ({value: e.value, label: e.textContent.trim()}))")
            return [o for o in opts if o["value"]]
    return _on_browser_thread(lambda: _retry_once(_run))


def get_schedule(term: str) -> list[dict]:
    """登录后选择学期并抓取课表，返回课程块列表。

    详情页之后会再抓一次周课表（Matrix Format）拿每块的简称地点
    （如 "MMW 2450"），按 crn + 星期挂到对应 meeting 的 room_short；
    周课表抓取/解析失败时静默回退，不阻塞课表展示（前端回退完整地点）。
    """
    def _run() -> list[dict]:
        with _lock:
            page = _ensure_browser()
            _goto_term_page(page)
            try:
                page.select_option("select[name=term_in]", term)
                page.click("form[action*='CrseSchdDetl'] input[type=submit]", timeout=10000)
                page.wait_for_load_state("load", timeout=60000)
            except Exception as exc:
                if _target_closed(exc):
                    raise  # 窗口被关：放行给 _retry_once 整体重试
                raise BanwebError("抓取课表失败（可能登录已失效）：" + str(exc)) from exc
            _require_logged_in(page)
            courses = parse_schedule_html(page.content())
            try:
                page.goto(WEEKLY_PAGE, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_load_state("load", timeout=30000)
                _require_logged_in(page)
                weekly = parse_weekly_schedule_html(page.content())
                merge_room_short(courses, weekly)
            except Exception:
                pass  # 周课表简称抓取失败不阻塞主流程
            return enrich_meetings(courses)
    return _on_browser_thread(lambda: _retry_once(_run))


def get_exams() -> tuple[str, list[dict]]:
    """抓取当前注册学期的考试时间表。返回 (term_label, exams)。

    term_label 取页面标题行 "Student Examination Timetable (Semester A 2026/27)"
    括号内的学期名；无考试时 exams=[]。失败抛 BanwebError。
    """
    def _run() -> tuple[str, list[dict]]:
        with _lock:
            page = _ensure_browser()
            try:
                page.goto(EXAM_PAGE, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_load_state("load", timeout=30000)
            except Exception as exc:
                if _target_closed(exc):
                    raise
                raise BanwebError("抓取考试时间表失败（可能登录已失效）：" + str(exc)) from exc
            _require_logged_in(page)
            content = page.content()
            m = re.search(r"Student Examination Timetable\s*\(([^)]+)\)", content)
            return (m.group(1) if m else ""), parse_exam_html(content)
    return _on_browser_thread(lambda: _retry_once(_run))
