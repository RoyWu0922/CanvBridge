"""AIMS 登录凭据的本地安全存储（macOS 钥匙串）。

安全模型（用户 2026-08-31 确认）：应用现在代为自动登录 AIMS，因此需要保存
账号密码。密码只存本机钥匙串（service=cityu_aims_login），不落盘为明文文件，
也绝不返回给前端；后端在 auto_login 时读取后直接填入 Okta 表单。这与早期
「程序绝不代用户登录、不接触任何凭证」的决策相反，属用户明确批准的反转。
"""
from __future__ import annotations

import re
import subprocess

SERVICE = "cityu_aims_login"


class CredentialsError(RuntimeError):
    """钥匙串读写失败。"""


def _run(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10)
    except Exception as exc:
        raise CredentialsError(f"钥匙串命令执行失败：{exc}") from exc


def _parse_acct(r) -> str:
    """从 find-generic-password 输出里取账号。不同 macOS 版本属性打印到
    stdout（Darwin 25+）或 stderr（旧版），两个流都解析，兼容两者。"""
    for stream in (r.stdout, r.stderr):
        m = re.search(r'"acct"<blob>="([^"]*)"', stream)
        if m:
            return m.group(1)
    return ""


def _find_account() -> str:
    """查 service 当前命中的账号（首个匹配）；未存过返回空串。"""
    r = _run(["security", "find-generic-password", "-s", SERVICE])
    if r.returncode != 0:
        return ""
    return _parse_acct(r)


def save_credentials(username: str, password: str) -> None:
    """把账号密码写入钥匙串（-U 已存在则覆盖）。

    改账号前先把旧账号的项删掉，避免 service 下残留两条、后续读/删命中歧义
    （M2：换了用户名后钥匙串里新旧各一条，App 却一直读到旧账号）。
    """
    if not username or not password:
        raise CredentialsError("AIMS 账号和密码都不能为空")
    old = _find_account()
    if old and old != username:
        _run(["security", "delete-generic-password", "-s", SERVICE, "-a", old])
    r = _run(["security", "add-generic-password", "-s", SERVICE,
              "-a", username, "-w", password, "-U"])
    if r.returncode != 0:
        raise CredentialsError("保存到钥匙串失败：" +
                               (r.stderr.strip() or r.stdout.strip() or "未知错误"))


def get_credentials() -> tuple[str, str] | None:
    """返回 (username, password)；未存过或已删除则返回 None。

    密码按「读到的账号」配对查询（find ... -a <username> -w），防止 service 下
    有多条时把账号 A 的密码读成账号 B 的。
    """
    username = _find_account()
    if not username:
        return None
    r = _run(["security", "find-generic-password", "-s", SERVICE, "-a", username, "-w"])
    if r.returncode != 0:
        return None
    password = r.stdout.rstrip("\n")
    return (username, password) if password else None


def get_username() -> str:
    """只取账号（不读密码），供前端设置页回显。未存过返回空串。"""
    return _find_account()


def delete_credentials() -> bool:
    """删除已存凭据。返回是否真的有被删的项。

    先查出当前账号再按 -a 删，确保删的是同一条、不会误删其它 service 的项。
    """
    username = _find_account()
    if not username:
        return False
    r = _run(["security", "delete-generic-password", "-s", SERVICE, "-a", username])
    return r.returncode == 0
