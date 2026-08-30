import subprocess

import pytest

from backend import apple_script


def test_quote_escapes():
    assert apple_script._quote('say "hi"') == 'say \\"hi\\"'
    assert apple_script._quote("a\nb") == "a b"
    assert apple_script._quote("a\rb") == "a b"


def test_build_date_script_sets_components():
    script = apple_script._build_date_script("2026-08-31T14:00:00", "d")
    assert "set year of d to 2026" in script
    assert "set month of d to August" in script
    assert "set day of d to 31" in script
    assert "set hours of d to 14" in script
    assert "set minutes of d to 0" in script


def test_run_raises_on_nonzero(monkeypatch):
    class _P:
        returncode = 1
        stdout = ""
        stderr = "not allowed"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())
    with pytest.raises(apple_script.AppleScriptError):
        apple_script._run("tell application \"Calendar\"")


def test_list_calendars_splits(monkeypatch):
    """逐行返回并保留原名（含尾随空格，如 "Study "）。"""
    class _P:
        returncode = 0
        stdout = "Study \n家庭\nWork"
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())
    assert apple_script.list_calendars() == ["Study ", "家庭", "Work"]


def test_list_reminder_lists_splits(monkeypatch):
    class _P:
        returncode = 0
        stdout = "A\nB\nC"
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())
    assert apple_script.list_reminder_lists() == ["A", "B", "C"]


def test_add_event_with_alert(monkeypatch):
    captured = {}
    def fake_run(script):
        captured["script"] = script
    monkeypatch.setattr(apple_script, "_run", fake_run)
    apple_script.add_calendar_event("Study", "Quiz", "2026-08-31T14:00:00",
                                    "2026-08-31T15:00:00", "", "", alert_minutes=15)
    s = captured["script"]
    assert "set newEvent to make new event with properties" in s
    assert "make new display alarm at end of newEvent with properties {trigger interval:-15}" in s


def test_add_event_no_alert(monkeypatch):
    captured = {}
    def fake_run(script):
        captured["script"] = script
    monkeypatch.setattr(apple_script, "_run", fake_run)
    apple_script.add_calendar_event("Study", "Quiz", "2026-08-31T14:00:00",
                                    "2026-08-31T15:00:00", "", "")
    assert "display alarm" not in captured["script"]
