"""通过 AppleScript 操作 macOS 日历与提醒事项。"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta

_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


class AppleScriptError(RuntimeError):
    """osascript 失败时抛出（多为系统权限未授权）。"""


def _run(script: str) -> str:
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AppleScriptError(proc.stderr.strip() or f"osascript failed ({proc.returncode})")
    return proc.stdout.strip()


def _quote(s: str) -> str:
    """把字符串安全嵌入 AppleScript 双引号字面量。"""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def _build_date_script(iso: str, var: str) -> str:
    """生成把 var 设为指定时刻的 AppleScript（按组件赋值，避免本地化问题）。"""
    dt = datetime.fromisoformat(iso)
    return (
        f"set {var} to current date\n"
        f"set year of {var} to {dt.year}\n"
        f"set month of {var} to {_MONTHS[dt.month - 1]}\n"
        f"set day of {var} to {dt.day}\n"
        f"set hours of {var} to {dt.hour}\n"
        f"set minutes of {var} to {dt.minute}\n"
        f"set seconds of {var} to {dt.second}"
    )


def list_calendars() -> list[str]:
    # 逐行返回，保留原名——日历名可能含尾随空格（如 "Study "），
    # strip 会导致写入时 calendar "Study" 找不到真实日历。
    script = (
        'tell application "Calendar"\n'
        'set _out to ""\n'
        'repeat with _c in every calendar\n'
        'set _out to _out & (name of _c) & linefeed\n'
        'end repeat\n'
        'return _out\n'
        'end tell'
    )
    return [ln for ln in _run(script).split("\n") if ln]


def list_reminder_lists() -> list[str]:
    script = (
        'tell application "Reminders"\n'
        'set _out to ""\n'
        'repeat with _c in every list\n'
        'set _out to _out & (name of _c) & linefeed\n'
        'end repeat\n'
        'return _out\n'
        'end tell'
    )
    return [ln for ln in _run(script).split("\n") if ln]


def add_calendar_event(calendar_name: str, title: str, start_iso: str, end_iso: str,
                       location: str, notes: str, alert_minutes: int | None = None) -> None:
    props = '{summary:"' + _quote(title) + '", start date:startDate, end date:endDate'
    if location:
        props += ', location:"' + _quote(location) + '"'
    if notes:
        props += ', description:"' + _quote(notes) + '"'
    props += "}"
    lines = [
        'tell application "Calendar"',
        _build_date_script(start_iso, "startDate"),
        _build_date_script(end_iso, "endDate"),
        'tell calendar "' + _quote(calendar_name) + '"',
        "set newEvent to make new event with properties " + props,
    ]
    if alert_minutes:
        lines.append(
            f"make new display alarm at end of newEvent with properties "
            f"{{trigger interval:{-alert_minutes}}}")
    lines += ["end tell", "end tell"]
    _run("\n".join(lines))


def event_exists(calendar_name: str, title: str) -> bool:
    """按标题精确判断某日历里是否已存在事件（用于写入前去重）。"""
    script = (
        'tell application "Calendar"\n'
        'set _n to count of (every event of calendar "'
        + _quote(calendar_name) + '" whose summary is "'
        + _quote(title) + '")\n'
        "return _n as text\n"
        "end tell"
    )
    return int(_run(script).strip()) > 0


def find_events(calendar_name: str, summary_prefix: str) -> list[dict]:
    """返回某日历里 summary 以 summary_prefix 开头的所有事件（跳过已隐藏的）。

    每条含 summary / start / end（ISO 格式）。字段先承接到变量再拼进字符串，
    避免在表达式里直接 as text 报 -1700（如 (weekday of _st) as text 拼接失败）。
    recurrence 读取用 try 包裹：非重复事件读不到时置空串。
    已隐藏事件（重复截止 UNTIL 在过去）会被跳过，避免幽灵复活干扰同步。
    """
    script = (
        'tell application "Calendar"\n'
        'set _out to ""\n'
        'tell calendar "' + _quote(calendar_name) + '"\n'
        'repeat with _e in (every event whose summary starts with "'
        + _quote(summary_prefix) + '")\n'
        'set _st to start date of _e\n'
        'set _en to end date of _e\n'
        'set _mo to (month of _st) as integer\n'
        'set _moe to (month of _en) as integer\n'
        'set _r to ""\n'
        'try\n'
        'set _r to recurrence of _e as text\n'
        'end try\n'
        'set _out to _out & (summary of _e) & "\\t"'
        ' & ((year of _st) as text) & "\\t" & (_mo as text) & "\\t"'
        ' & ((day of _st) as text) & "\\t" & ((hours of _st) as text) & "\\t"'
        ' & ((minutes of _st) as text) & "\\t"'
        ' & ((year of _en) as text) & "\\t" & (_moe as text) & "\\t"'
        ' & ((day of _en) as text) & "\\t" & ((hours of _en) as text) & "\\t"'
        ' & ((minutes of _en) as text) & "\\t" & _r & linefeed\n'
        'end repeat\n'
        'end tell\n'
        'return _out\n'
        'end tell'
    )
    today = datetime.now().date()
    events: list[dict] = []
    for line in _run(script).split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 12:
            continue
        summary = parts[0]
        y, mo, d, h, mi, ye, moe, de, he, mie = (int(p) for p in parts[1:11])
        match = re.search(r"UNTIL=(\d{8})", parts[11])
        if match and datetime.strptime(match.group(1), "%Y%m%d").date() < today:
            continue  # 系列已结束（被隐藏）→ 不参与同步
        events.append({
            "summary": summary,
            "start": f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:00",
            "end": f"{ye:04d}-{moe:02d}-{de:02d}T{he:02d}:{mie:02d}:00",
        })
    return events


def edit_recurring_event(calendar_name: str, old_summary: str, new_summary: str,
                         start_iso: str, end_iso: str, until_date: str) -> None:
    """把日历里 summary 等于 old_summary 的第一个重复事件原地改成新时间/标题。

    实测 Calendar 的脚本删除对重复系列静默失效（连 save 也不持久化），所以“改时间”
    用编辑而非删旧建新：事件身份保留、系列随新开始日期平移。
    改 start/end 必须先设结束、再设开始——直接设开始会拿新开始去比旧的结束
    校验 "The start date must be before the end date"。收尾把重复截止设为 until_date。
    """
    lines = [
        'tell application "Calendar"',
        'tell calendar "' + _quote(calendar_name) + '"',
        'set _e to first event whose summary is "' + _quote(old_summary) + '"',
    ]
    if new_summary != old_summary:
        lines.append('set summary of _e to "' + _quote(new_summary) + '"')
    # 先把结束挪到“新开始 + 365 天”（必然晚于旧开始，校验通过），再设开始，最后收尾
    probe = (datetime.fromisoformat(start_iso) + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S")
    lines.append(_build_date_script(probe, "probeDate"))
    lines.append(_build_date_script(start_iso, "newStart"))
    lines.append(_build_date_script(end_iso, "newEnd"))
    lines.append("set end date of _e to probeDate")
    lines.append("set start date of _e to newStart")
    lines.append("set end date of _e to newEnd")
    until_s = str(until_date)[:10].replace("-", "") + "T235959"
    lines.append(f'set recurrence of _e to "FREQ=WEEKLY;INTERVAL=1;UNTIL={until_s}"')
    lines += ["end tell", "end tell"]
    _run("\n".join(lines))


def hide_recurring_event(calendar_name: str, summary: str) -> None:
    """把某日历里 summary 精确匹配的所有重复事件隐藏（整节取消时用）。

    把重复截止改到过去，系列不再有未来发生项。原因同 edit：AppleScript 无法
    真删重复系列。隐藏后 find_events 会按 UNTIL 跳过它，同步保持幂等。
    """
    script = (
        'tell application "Calendar"\n'
        'tell calendar "' + _quote(calendar_name) + '"\n'
        'repeat with _e in (every event whose summary is "' + _quote(summary) + '")\n'
        'set recurrence of _e to "FREQ=WEEKLY;INTERVAL=1;UNTIL=20200101T000000"\n'
        'end repeat\n'
        'end tell\n'
        'end tell'
    )
    _run(script)


def add_recurring_event(calendar_name: str, title: str, start_iso: str, end_iso: str,
                        until_date: str, location: str, notes: str,
                        alert_minutes: int | None = None) -> None:
    """创建每周重复事件，重复到 until_date（YYYY-MM-DD）当日结束。

    recurrence 在本机 Calendar 词典中是 RFC 2445 文本属性。
    """
    props = '{summary:"' + _quote(title) + '", start date:startDate, end date:endDate'
    if location:
        props += ', location:"' + _quote(location) + '"'
    if notes:
        props += ', description:"' + _quote(notes) + '"'
    props += "}"
    until_s = str(until_date)[:10].replace("-", "") + "T235959"
    lines = [
        'tell application "Calendar"',
        _build_date_script(start_iso, "startDate"),
        _build_date_script(end_iso, "endDate"),
        'tell calendar "' + _quote(calendar_name) + '"',
        "set newEvent to make new event with properties " + props,
        f'set recurrence of newEvent to "FREQ=WEEKLY;INTERVAL=1;UNTIL={until_s}"',
    ]
    if alert_minutes:
        lines.append(
            f"make new display alarm at end of newEvent with properties "
            f"{{trigger interval:{-alert_minutes}}}")
    lines += ["end tell", "end tell"]
    _run("\n".join(lines))


def add_reminder(list_name: str, title: str, due_iso: str, notes: str) -> None:
    props = '{name:"' + _quote(title) + '", due date:dueDate'
    if notes:
        props += ', body:"' + _quote(notes) + '"'
    props += "}"
    script = (
        'tell application "Reminders"\n'
        + _build_date_script(due_iso, "dueDate") + "\n"
        'tell list "' + _quote(list_name) + '"\n'
        "make new reminder with properties " + props + "\n"
        "end tell\n"
        "end tell"
    )
    _run(script)


def pick_folder() -> str:
    """弹 macOS 原生文件夹选择框，返回 POSIX 路径（无尾随斜杠）。

    用户取消时 osascript 返回非 0（错误 -128），抛 AppleScriptError。
    """
    return _run("POSIX path of (choose folder)")
