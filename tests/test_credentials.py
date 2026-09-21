"""AIMS 凭据钥匙串存储的单元测试（mock security CLI，不碰真实钥匙串）。"""
import subprocess
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
    # 与 Gmail 读路径同款：假 `_run` 只按子命令分流、从不看 `-s`，不钉住 service 就
    # 拦不住「AIMS 那条读去查了别的 service」的回归。查账号那次是唯一不带 -w 的 find。
    find = next(a for a in calls
                if a[1] == "find-generic-password" and "-w" not in a)
    assert find[find.index("-s") + 1] == credentials.SERVICE


def test_get_credentials_parses_acct_from_stdout(monkeypatch):
    """macOS 25+ 把属性打到 stdout（stderr 为空）——实测缺这个会读到空账号。"""
    calls = _patch(monkeypatch, acct="sc123456", acct_stream="stdout", pw="s3cret")
    assert credentials.get_credentials() == ("sc123456", "s3cret")
    assert credentials.get_username() == "sc123456"


def test_get_username_from_stdout(monkeypatch):
    """get_username 同样兼容 stdout 属性。"""
    calls = _patch(monkeypatch, acct="sc123456", acct_stream="stdout")
    assert credentials.get_username() == "sc123456"
    # 回显路径同样要钉 service：它是设置页地址框上一次看到的值的唯一来源，
    # 查错 service 会回显成另一套凭据的账号，而假 `_run` 从不看 `-s`、拦不住。
    find = next(a for a in calls
                if a[1] == "find-generic-password" and "-w" not in a)
    assert find[find.index("-s") + 1] == credentials.SERVICE


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


# --- Gmail 版（同一套逻辑，换 service）---

def test_gmail_service_name_is_distinct():
    """两个 service 必须不同 —— 否则 Gmail 密码会覆盖 AIMS 密码。"""
    assert credentials.GMAIL_SERVICE != credentials.SERVICE
    assert credentials.GMAIL_SERVICE == "cityu_gmail_imap"


def test_save_gmail_uses_gmail_service(monkeypatch):
    """service 用的是 Gmail 那个，不是 AIMS 的。"""
    calls = _patch(monkeypatch)
    credentials.save_gmail("me@gmail.com", "app-pw-16")
    add = _add_call(calls)
    assert add[add.index("-s") + 1] == credentials.GMAIL_SERVICE
    assert add[add.index("-a") + 1] == "me@gmail.com"
    assert add[add.index("-w") + 1] == "app-pw-16"


def test_save_gmail_rejects_empty(monkeypatch):
    calls = _patch(monkeypatch)
    with pytest.raises(credentials.CredentialsError):
        credentials.save_gmail("", "pw")
    with pytest.raises(credentials.CredentialsError):
        credentials.save_gmail("me@gmail.com", "")
    assert not any(a[1] == "add-generic-password" for a in calls)


def test_save_gmail_replaces_old_address(monkeypatch):
    """与 AIMS 同款 M2 修复：换地址先删旧项。"""
    calls = _patch(monkeypatch, acct="old@gmail.com")
    credentials.save_gmail("new@gmail.com", "pw")
    dele = next(a for a in calls if a[1] == "delete-generic-password")
    assert dele[dele.index("-s") + 1] == credentials.GMAIL_SERVICE
    assert dele[dele.index("-a") + 1] == "old@gmail.com"


def test_get_gmail_returns_pair(monkeypatch):
    calls = _patch(monkeypatch, acct="me@gmail.com", pw="app-pw-16")
    assert credentials.get_gmail() == ("me@gmail.com", "app-pw-16")
    # service 必须钉住。`_patch` 的假 `_run` 只按 security 的子命令分流、**从不看 `-s`**
    # ⇒ 把 `get_gmail` 里的 `_load(GMAIL_SERVICE)` 误写成 `_load(SERVICE)`（拿 **AIMS**
    # 那条钥匙串记录去登 Gmail：学校账号密码会作为 LOGIN 参数发给 imap.gmail.com，
    # 再被翻成「请检查地址与应用专用密码」把用户引向错误因由）时，上面那行断言照样绿。
    # 查账号那次是**唯一**不带 `-w` 的 find，用 `"-w" not in a` 唯一定位它。
    find = next(a for a in calls
                if a[1] == "find-generic-password" and "-w" not in a)
    assert find[find.index("-s") + 1] == credentials.GMAIL_SERVICE


def test_get_gmail_strips_whitespace_in_app_password(monkeypatch):
    """Google 把应用专用密码按四组显示（中间是空格），用户会带空格粘贴进来。

    带空格的密码送去 IMAP 认证必然 AUTHENTICATIONFAILED，而那个错误会被翻成
    「请检查地址与应用专用密码」，把用户引向错误的因由 —— 读出来必须是连写的
    16 位。地址不受影响。
    """
    _patch(monkeypatch, acct="me@gmail.com", pw="abcd efgh ijkl mnop")
    assert credentials.get_gmail() == ("me@gmail.com", "abcdefghijklmnop")


def test_get_gmail_none_when_missing(monkeypatch):
    _patch(monkeypatch, acct_rc=44)
    assert credentials.get_gmail() is None


def test_gmail_address_is_read_from_gmail_service(monkeypatch):
    """get_gmail_address 查的是 Gmail 的 service。"""
    calls = _patch(monkeypatch, acct="me@gmail.com")
    assert credentials.get_gmail_address() == "me@gmail.com"
    find = next(a for a in calls if a[1] == "find-generic-password" and "-w" not in a)
    assert find[find.index("-s") + 1] == credentials.GMAIL_SERVICE


def test_delete_gmail_scopes_account(monkeypatch):
    calls = _patch(monkeypatch, acct="me@gmail.com")
    assert credentials.delete_gmail() is True
    dele = next(a for a in calls if a[1] == "delete-generic-password")
    assert dele[dele.index("-s") + 1] == credentials.GMAIL_SERVICE
    assert dele[dele.index("-a") + 1] == "me@gmail.com"


def test_aims_save_still_uses_aims_service(monkeypatch):
    """参数化之后 AIMS 那条路没被带偏。"""
    calls = _patch(monkeypatch)
    credentials.save_credentials("sc123456", "s3cret")
    add = _add_call(calls)
    assert add[add.index("-s") + 1] == credentials.SERVICE


# --- 凭据绝不回传前端（超时路径）---

def test_save_gmail_timeout_does_not_leak_password(monkeypatch):
    """钥匙串命令超时时，CredentialsError 消息里绝不能出现明文密码。

    机制（这条用例钉的就是它）：`_save` 的 argv 自带明文密码
    （`add-generic-password … -w <password>`），而 `subprocess.TimeoutExpired.__str__`
    会把**整个 argv** 渲染进消息 —— 实测形态是
    `Command '[…, '-w', '<password>', …]' timed out after 10 seconds`。
    钥匙串被锁 / 弹不出授权框时 `security` 会一直阻塞到 10s 超时，于是
    `_run` 的 except 分支若写 `{exc}`，密码就顺着 CredentialsError 上行；而端点是
    `return {"ok": False, "error": str(exc)}`（main.py 的 AIMS 与 Gmail 两处），
    密码最终回传到浏览器 —— 违反 spec 「凭据绝不回传前端」。

    **这里替身的是 `subprocess.run` 本身**（不是 `_run`），走的才是真实的那条 except。
    假 run 按子命令分流：查账号那步返回「没找到」，**add 那步才抛 TimeoutExpired**
    —— 否则超时会在不含密码的 find argv 上先炸，用例会在错误的分支上变绿。
    """
    leaked = "abcd efgh ijkl mnop"          # 合成值：Google 应用专用密码的显示形态
    seen: list[list[str]] = []

    def fake_run(args, **kw):
        seen.append(list(args))
        if args[1] == "add-generic-password":
            # 与真 subprocess 同形：cmd 是完整 argv，__str__ 会把它整个渲染出来
            raise subprocess.TimeoutExpired(cmd=list(args), timeout=10)
        # 查账号：未存过（security 找不到项时返回 44）
        return _R(44, stderr="SecKeychainSearchCopyNext: not found\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(credentials.CredentialsError) as ei:
        credentials.save_gmail("me@gmail.com", leaked)

    msg = str(ei.value)
    assert leaked not in msg, f"密码泄漏进异常消息：{msg}"
    assert "TimeoutExpired" in msg, f"没走超时分支（消息里无类型名）：{msg}"

    # 反空转：证明「被检对象」确实是被那个超时抛出的，而不是别的错误路径满足了上面两条。
    assert isinstance(ei.value.__cause__, subprocess.TimeoutExpired)   # from exc 链在
    add_args = next(a for a in seen if a[1] == "add-generic-password")
    assert leaked in add_args          # 那次超时的 argv 里**真的**带着密码
    # 威胁不是假想：被丢弃的那个字符串里，密码本来就是存在的 —— 即修复前
    # `{exc}` 一定会泄漏。少了这一条，上面那个 not in 可能只是在守一个空气球
    # （比如 Python 哪天不再把 argv 渲染进 __str__，用例就该重新审视而不是继续装绿）。
    assert leaked in str(ei.value.__cause__)
