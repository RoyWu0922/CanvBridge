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


def _patch(monkeypatch, *, acct="", acct_rc=0, acct_stream="stderr",
           pw="", pw_rc=0, add_rc=0, add_err="", del_rc=0):
    """按 security 子命令路由的假 _run，记录所有调用参数。

    - 无 -a/-w 的 find 查账号 → 返回 acct（属性打到 stderr 或 stdout）
    - 带 -w 的 find 查密码
    - add-generic-password / delete-generic-password 分别返回 add_rc / del_rc
    """
    calls = []

    def fake(args, **kw):
        calls.append(args)
        cmd = args[1]
        if cmd == "find-generic-password":
            if "-w" in args:
                return _R(pw_rc, stdout=pw + "\n")
            payload = f'"acct"<blob>="{acct}"\n'
            if acct_stream == "stdout":
                return _R(acct_rc, stdout=payload)
            return _R(acct_rc, stderr=payload)
        if cmd == "add-generic-password":
            return _R(add_rc, stderr=add_err)
        if cmd == "delete-generic-password":
            return _R(del_rc)
        return _R(0)

    monkeypatch.setattr(credentials, "_run", fake)
    return calls


def _add_call(calls):
    return next(a for a in calls if a[1] == "add-generic-password")


def test_save_credentials_passes_cli_args(monkeypatch):
    """add-generic-password 参数正确：service 固定、账号密码到位、-U 覆盖。"""
    calls = _patch(monkeypatch)  # 未存过 → 不需先删旧账号
    credentials.save_credentials("sc123456", "s3cret")
    args = _add_call(calls)
    assert args[0] == "security"
    assert args[1] == "add-generic-password"
    assert args[3] == credentials.SERVICE
    assert args[5] == "sc123456"   # -a 账号
    assert args[7] == "s3cret"     # -w 密码
    assert "-U" in args
    assert all(a[1] != "delete-generic-password" for a in calls)


def test_save_credentials_replaces_old_account(monkeypatch):
    """换了用户名：先把旧账号的项删掉，再存新账号，避免残留两条（M2）。"""
    calls = _patch(monkeypatch, acct="olduser")
    credentials.save_credentials("newuser", "p")
    dels = [a for a in calls if a[1] == "delete-generic-password"]
    assert len(dels) == 1
    assert "-a" in dels[0] and dels[0][dels[0].index("-a") + 1] == "olduser"
    add = _add_call(calls)
    assert add[add.index("-a") + 1] == "newuser"
    assert calls.index(dels[0]) < calls.index(add)  # 先删后加


def test_save_credentials_same_account_no_delete(monkeypatch):
    """仍是同一账号 → 只 -U 覆盖，不删。"""
    calls = _patch(monkeypatch, acct="u")
    credentials.save_credentials("u", "p")
    assert all(a[1] != "delete-generic-password" for a in calls)
    assert "-U" in _add_call(calls)


def test_save_credentials_rejects_empty(monkeypatch):
    """空账号/密码不调用 CLI，直接抛 CredentialsError。"""
    calls = _patch(monkeypatch)
    with pytest.raises(credentials.CredentialsError):
        credentials.save_credentials("", "x")
    with pytest.raises(credentials.CredentialsError):
        credentials.save_credentials("u", "")
    assert calls == []


def test_save_credentials_raises_on_cli_error(monkeypatch):
    calls = _patch(monkeypatch, acct_rc=44, add_rc=1, add_err="denied")
    with pytest.raises(credentials.CredentialsError) as ei:
        credentials.save_credentials("u", "p")
    assert "denied" in str(ei.value)


def test_get_credentials_none_when_missing(monkeypatch):
    """security 返回非 0（未找到）→ None。"""
    calls = _patch(monkeypatch, acct_rc=44)
    assert credentials.get_credentials() is None
    assert len(calls) == 1  # 账号都查不到就不去读密码


def test_get_credentials_parses_username_and_password(monkeypatch):
    """从 "acct"<blob> 取账号；密码按该账号配对查询（带 -a）。"""
    calls = _patch(monkeypatch, acct="sc123456", pw="s3cret")
    assert credentials.get_credentials() == ("sc123456", "s3cret")
    pw_call = next(a for a in calls if "-w" in a)
    assert pw_call[pw_call.index("-a") + 1] == "sc123456"   # -a 与账号一致


def test_get_credentials_parses_acct_from_stdout(monkeypatch):
    """macOS 25+ 把属性打到 stdout（stderr 为空）——实测缺这个会读到空账号。"""
    calls = _patch(monkeypatch, acct="sc123456", acct_stream="stdout", pw="s3cret")
    assert credentials.get_credentials() == ("sc123456", "s3cret")
    assert credentials.get_username() == "sc123456"


def test_get_username_from_stdout(monkeypatch):
    """get_username 同样兼容 stdout 属性。"""
    calls = _patch(monkeypatch, acct="sc123456", acct_stream="stdout")
    assert credentials.get_username() == "sc123456"


def test_get_credentials_none_when_password_missing(monkeypatch):
    calls = _patch(monkeypatch, acct="sc123456", pw_rc=44)
    assert credentials.get_credentials() is None


def test_get_username_empty_when_missing(monkeypatch):
    calls = _patch(monkeypatch, acct_rc=44)
    assert credentials.get_username() == ""


def test_delete_credentials_scopes_account(monkeypatch):
    """删除按当前账号带 -a，只删这一条。"""
    calls = _patch(monkeypatch, acct="sc123456")
    assert credentials.delete_credentials() is True
    dels = [a for a in calls if a[1] == "delete-generic-password"]
    assert len(dels) == 1
    assert dels[0][dels[0].index("-a") + 1] == "sc123456"


def test_delete_credentials_not_found(monkeypatch):
    """没有账号可删 → False，且不再尝试 delete。"""
    calls = _patch(monkeypatch, acct_rc=44)
    assert credentials.delete_credentials() is False
    assert all(a[1] != "delete-generic-password" for a in calls)
