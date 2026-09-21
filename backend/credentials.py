"""本地安全存储的凭据（macOS 钥匙串），目前有两套。

- AIMS 登录凭据（service=cityu_aims_login）：应用代为自动登录 AIMS，因此需要保存
  账号密码。密码只存本机钥匙串，不落盘为明文文件，也绝不返回给前端；后端在
  auto_login 时读取后直接填入 Okta 表单。这与早期「程序绝不代用户登录、不接触任
  何凭证」的决策相反，属用户明确批准的反转。
- Gmail 取信凭据（service=cityu_gmail_imap）：学校邮箱转发到 Gmail 后，用 Gmail
  地址 + 应用专用密码走 IMAP 读信，取代原先的浏览器截获 OWA token 路线。

两组凭据共用同一套内部逻辑（_find_account / _save / _load / _delete），只以
service 名区分，公开函数各自传入自己的 service。**刻意不复制一份**：`_save` 里
「换账号先删旧项」是踩出来的修复（M2），复制会导致两份副本漂移，而漂移的是安全相
关的删除逻辑。

新增 Gmail 这组凭据是 spec §9（安全与合规边界）的**显式修订**：旧 §9 写着「不新增
任何凭据使用」，现改写为「不新增任何登录流程；新增一个凭据：Gmail 应用专用密码」。
§9 的其余条目（不得明文落盘、不得回传前端、不得出现在日志/报告/提交信息里，钥匙串
为唯一许可存放处）一条不减，对两套 service 同等生效。
"""
from __future__ import annotations

import re
import subprocess

SERVICE = "cityu_aims_login"
GMAIL_SERVICE = "cityu_gmail_imap"


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


def _find_account(service: str = SERVICE) -> str:
    """查 service 当前命中的账号（首个匹配）；未存过返回空串。"""
    r = _run(["security", "find-generic-password", "-s", service])
    if r.returncode != 0:
        return ""
    return _parse_acct(r)


def _save(service: str, username: str, password: str, empty_msg: str) -> None:
    """把账号密码写入钥匙串（-U 已存在则覆盖）。

    改账号前先把旧账号的项删掉，避免 service 下残留两条、后续读/删命中歧义
    （M2：换了用户名后钥匙串里新旧各一条，App 却一直读到旧账号）。
    """
    if not username or not password:
        raise CredentialsError(empty_msg)
    old = _find_account(service)
    if old and old != username:
        _run(["security", "delete-generic-password", "-s", service, "-a", old])
    r = _run(["security", "add-generic-password", "-s", service,
              "-a", username, "-w", password, "-U"])
    if r.returncode != 0:
        raise CredentialsError("保存到钥匙串失败：" +
                               (r.stderr.strip() or r.stdout.strip() or "未知错误"))


def _load(service: str) -> tuple[str, str] | None:
    """返回 (username, password)；未存过或已删除则返回 None。

    密码按「读到的账号」配对查询（find ... -a <username> -w），防止 service 下
    有多条时把账号 A 的密码读成账号 B 的。
    """
    username = _find_account(service)
    if not username:
        return None
    r = _run(["security", "find-generic-password", "-s", service, "-a", username, "-w"])
    if r.returncode != 0:
        return None
    password = r.stdout.rstrip("\n")
    return (username, password) if password else None


def _delete(service: str) -> bool:
    """删除已存凭据。返回是否真的有被删的项。

    先查出当前账号再按 -a 删，确保删的是同一条、不会误删其它 service 的项。
    """
    username = _find_account(service)
    if not username:
        return False
    r = _run(["security", "delete-generic-password", "-s", service, "-a", username])
    return r.returncode == 0


# --- AIMS ---

def save_credentials(username: str, password: str) -> None:
    _save(SERVICE, username, password, "AIMS 账号和密码都不能为空")


def get_credentials() -> tuple[str, str] | None:
    return _load(SERVICE)


def get_username() -> str:
    """只取账号（不读密码），供前端设置页回显。未存过返回空串。"""
    return _find_account(SERVICE)


def delete_credentials() -> bool:
    return _delete(SERVICE)


# --- Gmail（学校邮箱转发目标）---

def save_gmail(address: str, app_password: str) -> None:
    """存 Gmail 地址与应用专用密码。密码由用户在设置页输入，绝不落盘为文件。"""
    _save(GMAIL_SERVICE, address, app_password, "Gmail 地址和应用专用密码都不能为空")


def get_gmail() -> tuple[str, str] | None:
    """返回 (地址, 应用专用密码)；未存过返回 None。

    密码去掉**全部空白字符**后再返回：Google 把应用专用密码显示成
    「abcd efgh ijkl mnop」——16 位、用空格分成四组——用户手抄或手动选中复制时
    会把这些空格一起带进来。带空格的密码送去 IMAP 认证必然
    AUTHENTICATIONFAILED，而下游会把它翻成「请检查地址与应用专用密码（需已开启
    两步验证）」，把用户引向完全错误的因由。

    归一化收口在**读取侧**而非 save_gmail：用户可以照 Task 1 的前置条件自己用
    `security add-generic-password` 直接写钥匙串，那条路径绕过 save_gmail，只在
    写入侧做就会漏。地址原样返回，不在这里动（地址里的空白归端点处理）。
    """
    creds = _load(GMAIL_SERVICE)
    if creds is None:
        return None
    address, app_password = creds
    return (address, "".join(app_password.split()))


def get_gmail_address() -> str:
    """只取地址（不读密码），供前端设置页回显。"""
    return _find_account(GMAIL_SERVICE)


def delete_gmail() -> bool:
    return _delete(GMAIL_SERVICE)
