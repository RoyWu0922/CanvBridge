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


def save_credentials(username: str, password: str) -> None:
    """把账号密码写入钥匙串（-U 已存在则覆盖）。"""
    if not username or not password:
        raise CredentialsError("AIMS 账号和密码都不能为空")
    r = _run(["security", "add-generic-password", "-s", SERVICE,
              "-a", username, "-w", password, "-U"])
    if r.returncode != 0:
        raise CredentialsError("保存到钥匙串失败：" +
                               (r.stderr.strip() or r.stdout.strip() or "未知错误"))


def _parse_acct(r) -> str:
    """从 find-generic-password 输出里取账号。不同 macOS 版本属性打印到
    stdout（Darwin 25+）或 stderr（旧版），两个流都解析，兼容两者。"""
    for stream in (r.stdout, r.stderr):
        m = re.search(r'"acct"<blob>="([^"]*)"', stream)
        if m:
            return m.group(1)
    return ""


def get_credentials() -> tuple[str, str] | None:
    """返回 (username, password)；未存过或已删除则返回 None。"""
    r = _run(["security", "find-generic-password", "-s", SERVICE])
    if r.returncode != 0:
        return None
    username = _parse_acct(r)
    r2 = _run(["security", "find-generic-password", "-s", SERVICE, "-w"])
    if r2.returncode != 0:
        return None
    password = r2.stdout.rstrip("\n")
    return (username, password) if password else None


def get_username() -> str:
    """只取账号（不读密码），供前端设置页回显。未存过返回空串。"""
    r = _run(["security", "find-generic-password", "-s", SERVICE])
    if r.returncode != 0:
        return ""
    return _parse_acct(r)


def delete_credentials() -> bool:
    """删除已存凭据。返回是否真的有被删的项。"""
    r = _run(["security", "delete-generic-password", "-s", SERVICE])
    return r.returncode == 0
