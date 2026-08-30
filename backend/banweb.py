"""CityU AIMS (Banweb) 课表自动抓取。

会话策略（用户已确认）：
- 程序绝不代用户登录、不接触任何凭证。
- 后端用系统 Chrome 打开一个常驻有头窗口（独立 profile + CDP 端口）；
  退登时用户在该窗口手动登录一次，watchdog 检测到回到 banweb 后自动继续。
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

try:
    from playwright.sync_api import sync_playwright
except Exception:  # 未装 Playwright 时不影响其余功能
    sync_playwright = None

LOGIN_HOST = "https://auth.cityu.edu.hk"
BANWEB = "https://banweb.cityu.edu.hk"
TERM_PAGE = BANWEB + "/pls/PROD/bwskfshd.P_CrseSchdDetl"
PROFILE_DIR = Path.home() / ".cityu_aims_profile"
CDP_PORT = 9339

_lock = threading.RLock()
_pw = None
_ctx = None
_page = None
_browser_executor: concurrent.futures.ThreadPoolExecutor | None = None


class BanwebError(RuntimeError):
    """AIMS 抓取相关错误（未登录 / 未安装 Playwright / 解析失败等）。"""


# ---------------- 纯解析（可单测） ----------------

_CAP_RE = re.compile(r"^(.*?)\s*-\s*([A-Z]{2,4}\s*\d{4})\s*-\s*([A-Z0-9]+)$")
_WEEKDAY = {"M": "Mon", "T": "Tue", "W": "Wed", "R": "Thu", "F": "Fri", "S": "Sat", "U": "Sun"}
_WEEKDAY_0 = {"M": 0, "T": 1, "W": 2, "R": 3, "F": 4, "S": 5, "U": 6}
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


def _ensure_browser(launch: bool = True):
    """返回一个可用的浏览器页面。

    launch=False 供状态轮询用：**绝不自己弹窗**。浏览器已关时抛 BanwebError，
    由调用方判为 needs_login，等用户点「重新登录」再开窗。
    launch=True 供用户主动操作（重新登录/抓课表）用：确保窗口真的打开。
    """
    global _pw, _ctx, _page
    with _lock:
        if sync_playwright is None:
            raise BanwebError("未安装 Playwright，无法抓取课表（pip install playwright）")
        if _page is not None:
            try:
                # 真存活探测：做一次 CDP 往返。is_closed() 对已断开的连接可能仍返回
                # False，导致拿旧对象当活窗口用（open_login 因此"成功"却没开窗）。
                _page.evaluate("1")
                return _page
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
        # 3) 启动常驻有头 Chrome（独立 profile，避免碰系统主 profile）
        try:
            _ctx = _launch_persistent()
        except Exception as exc:
            if _is_driver_error(exc):
                # driver 被残留连接打崩 → 重启驱动后再试一次
                _reset_browser()
                _restart_driver()
                try:
                    _ctx = _launch_persistent()
                except Exception as exc2:
                    raise BanwebError(
                        "无法打开浏览器。若旧窗口还在运行，请先关闭它再重试。") from exc2
            else:
                raise BanwebError(
                    "无法打开浏览器。若旧窗口还在运行，请先关闭它再重试。") from exc
        _page = _ctx.pages[0] if _ctx.pages else _ctx.new_page()
        try:
            _page.goto(TERM_PAGE, timeout=60000, wait_until="domcontentloaded")
        except Exception:
            pass  # 未登录会跳转到 Okta，属正常
        return _page


def _launch_persistent():
    """用当前 driver 启动常驻有头 Chrome（独立 profile）。"""
    return _pw.chromium.launch_persistent_context(
        str(PROFILE_DIR), channel="chrome", headless=False,
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
    """确保登录窗口打开并置前，让用户手动登录（程序不代登、不接触凭证）。

    若退登，导航课表页会自动落到 Okta 登录页（或 Banner 内嵌登录页）。
    """
    def _run() -> None:
        with _lock:
            page = _ensure_browser(launch=True)
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
    """登录后选择学期并抓取课表，返回课程块列表。"""
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
            return parse_schedule_html(page.content())
    return _on_browser_thread(lambda: _retry_once(_run))
