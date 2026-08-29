"""通过 AppleScript 操作 macOS 日历与提醒事项。"""
from __future__ import annotations

import subprocess
from datetime import datetime

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
    out = _run('tell application "Calendar" to get name of every calendar')
    return [s.strip() for s in out.split(",")]


def list_reminder_lists() -> list[str]:
    out = _run('tell application "Reminders" to get name of every list')
    return [s.strip() for s in out.split(",")]


def add_calendar_event(calendar_name: str, title: str, start_iso: str, end_iso: str,
                       location: str, notes: str) -> None:
    props = '{summary:"' + _quote(title) + '", start date:startDate, end date:endDate'
    if location:
        props += ', location:"' + _quote(location) + '"'
    if notes:
        props += ', description:"' + _quote(notes) + '"'
    props += "}"
    script = (
        'tell application "Calendar"\n'
        + _build_date_script(start_iso, "startDate") + "\n"
        + _build_date_script(end_iso, "endDate") + "\n"
        'tell calendar "' + _quote(calendar_name) + '"\n'
        "make new event with properties " + props + "\n"
        "end tell\n"
        "end tell"
    )
    _run(script)


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
