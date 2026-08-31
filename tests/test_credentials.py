"""AIMS 凭据钥匙串存储的单元测试（mock security CLI，不碰真实钥匙串）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import credentials


class _R:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


def test_save_credentials_passes_cli_args(monkeypatch):
    """add-generic-password 参数正确：service 固定、账号密码到位、-U 覆盖。"""
    calls = []
    monkeypatch.setattr(credentials, "_run",
                        lambda args, **kw: calls.append(args) or _R())
    credentials.save_credentials("sc123456", "s3cret")
    args = calls[0]
    assert args[0] == "security"
    assert args[1] == "add-generic-password"
    assert args[3] == credentials.SERVICE
    assert args[5] == "sc123456"   # -a 账号
    assert args[7] == "s3cret"     # -w 密码
    assert "-U" in args


def test_save_credentials_rejects_empty(monkeypatch):
    """空账号/密码不调用 CLI，直接抛 CredentialsError。"""
    calls = []
    monkeypatch.setattr(credentials, "_run",
                        lambda args, **kw: calls.append(args) or _R())
    with pytest.raises(credentials.CredentialsError):
        credentials.save_credentials("", "x")
    with pytest.raises(credentials.CredentialsError):
        credentials.save_credentials("u", "")
    assert calls == []


def test_save_credentials_raises_on_cli_error(monkeypatch):
    monkeypatch.setattr(credentials, "_run",
                        lambda args, **kw: _R(1, stderr="denied"))
    with pytest.raises(credentials.CredentialsError) as ei:
        credentials.save_credentials("u", "p")
    assert "denied" in str(ei.value)


def test_get_credentials_none_when_missing(monkeypatch):
    """security 返回非 0（未找到）→ None。"""
    monkeypatch.setattr(credentials, "_run", lambda args, **kw: _R(44))
    assert credentials.get_credentials() is None


def test_get_credentials_parses_username_and_password(monkeypatch):
    """从 stderr 的 "acct"<blob> 取账号、-w 取密码（rstrip 掉换行）。"""
    def fake(args, **kw):
        if "-w" in args:
            return _R(0, stdout="s3cret\n")
        return _R(0, stderr='keychain: ".../login.keychain-db"\n'
                            '    "acct"<blob>="sc123456"\n'
                            '    "svce"<blob>="cityu_aims_login"\n')
    monkeypatch.setattr(credentials, "_run", fake)
    assert credentials.get_credentials() == ("sc123456", "s3cret")


def test_get_credentials_parses_acct_from_stdout(monkeypatch):
    """macOS 25+ 把属性打到 stdout（stderr 为空）——实测缺这个会读到空账号。"""
    def fake(args, **kw):
        if "-w" in args:
            return _R(0, stdout="s3cret\n")
        return _R(0, stdout='keychain: ".../login.keychain-db"\n'
                            '    "acct"<blob>="sc123456"\n'
                            '    "svce"<blob>="cityu_aims_login"\n')
    monkeypatch.setattr(credentials, "_run", fake)
    assert credentials.get_credentials() == ("sc123456", "s3cret")
    assert credentials.get_username() == "sc123456"


def test_get_username_from_stdout(monkeypatch):
    """get_username 同样兼容 stdout 属性。"""
    monkeypatch.setattr(credentials, "_run",
                        lambda args, **kw: _R(0, stdout='    "acct"<blob>="sc123456"\n'))
    assert credentials.get_username() == "sc123456"


def test_get_credentials_none_when_password_missing(monkeypatch):
    def fake(args, **kw):
        if "-w" in args:
            return _R(44)
        return _R(0, stderr='"acct"<blob>="sc123456"\n')
    monkeypatch.setattr(credentials, "_run", fake)
    assert credentials.get_credentials() is None


def test_get_username_empty_when_missing(monkeypatch):
    monkeypatch.setattr(credentials, "_run", lambda args, **kw: _R(44))
    assert credentials.get_username() == ""


def test_delete_credentials(monkeypatch):
    monkeypatch.setattr(credentials, "_run", lambda args, **kw: _R(0))
    assert credentials.delete_credentials() is True


def test_delete_credentials_not_found(monkeypatch):
    monkeypatch.setattr(credentials, "_run", lambda args, **kw: _R(44))
    assert credentials.delete_credentials() is False
