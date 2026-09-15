"""全仓测试的默认纪律：**不允许真实出网**。

## 为什么拦在 socket 层，而不是拦 `library_client`

这是本文件存在的**唯一理由**，后来者请勿把它挪层（R69 的原始记录）。

本仓有**两条互不相干**的 HTTP 路径：

1. `library_client._get()` —— Primo 检索（走 `_http_get`，可以被单独替身）；
2. `library_areas.load_areas()` —— CityU 官网的 `db_subject.php` 学科词表。它走的是
   **`library_areas` 自己那条 `requests` 路径，不经过 `_get`、不经过 `library_client`
   的任何共享出口**（`backend/main.py` 里那句「area 词表走的是 `library_areas` 自己
   那条 `requests` 路径（不经过 `_get`）」说的就是这件事）。

⇒ **只拦 `library_client` 的守卫会给出虚假的安心感，比没有守卫更糟**：第二类出网
照旧发生，而你以为已经罩住了。socket 层是**唯一**能同时罩住两条路径的位置 —— 无论
谁、走哪条路，最后都要过 DNS 与 `connect`。

## 已被这条守卫挡住的两起真实失守（都发生在变异检验期间）

- Task 8 修复轮 3 §8.6：变异 M6a 首次运行时，用例真的对
  `julac-cuh.primo.exlibrisgroup.com` 发出一次未登录 GET。
- `~/.canvbridge/library_areas.json`（1444 字节、**28 条真实 CityU 学科**、
  `fetched_at` 15:35:37）—— 只可能来自对 CityU 官网的真实抓取。

两起的机制完全相同：`tests/test_main.py` 的 `_patch_library_boundaries` 替身了
`_MIN_INTERVAL` / `parse_natural` / `list_domains`，**唯独不替身 `load_areas`**；
而变异检验恰恰就是「摘掉替身看用例红不红」⇒ 摘掉那次 `load_areas` 替身，端点就会
真的去抓 CityU 的 area 页。旁证：同目录**没有** `library_domains.json`（= 那次运行里
`list_domains` 被替身了、`load_areas` 没有），与上面的机制逐字吻合。

## 被拦到时**抛异常**，不静默返回空

静默返回会把「测试其实在打网络」变成「测试悄悄拿到空数据」—— 后者更难发现。
异常会在用例里炸开，指向本文件的说明。

## 放行

- **回环**（`127.0.0.0/8` / `localhost` / `::1` / `0.0.0.0` / `::`）—— 否则会打死本仓
  既有的 `TestClient` 用法（`fastapi.testclient` 在某些配置下真的会走回环 socket）。
- **AF_UNIX**（本机 unix domain socket，不出网）。

## 覆盖的入口

`socket.getaddrinfo`（DNS —— 拦在这里请求根本发不出去）、`socket.create_connection`、
`socket.socket.connect`、`socket.socket.connect_ex`。四条都拦，是因为
`requests`/`urllib3` 走 `create_connection`（内部先 `getaddrinfo` 再 `sock.connect`），
而直连的代码可能只碰其中一条；只拦 DNS 会漏掉「直接给 IP 就没有 DNS」的形态。
"""
from __future__ import annotations

import socket

import pytest


class NetworkBlocked(RuntimeError):
    """测试期间任何真实出网尝试都会撞到这里（见本文件顶部说明）。"""


#: 每一次被拦下的出网尝试都会记在这里（`"<入口>: <目标>"` 一行）。
#: 干净态下它必须是空的 —— 这正是本守卫「在位且真的没漏」的判据。
BLOCKED: list[str] = []

#: 明文放行的主机（回环 / 未指定）。`127.0.0.0/8` 另按前缀放行。
_LOOPBACK_HOSTS = {"localhost", "::1", "0.0.0.0", "::", ""}


def _host_of(address):
    """从 `connect(addr)` 的 addr 里取出主机名；取不到就返回 None。"""
    if isinstance(address, (tuple, list)):
        return address[0] if address else None
    return address


def _is_loopback(host) -> bool:
    if host is None:
        return True                     # 没有主机名（AF_UNIX 之类）→ 放行
    if isinstance(host, bytes):
        try:
            host = host.decode("utf-8")
        except Exception:
            return False
    if not isinstance(host, str):
        return False
    h = host.strip().lower()
    return h in _LOOPBACK_HOSTS or h.startswith("127.")


def _deny(entry_point: str, target):
    BLOCKED.append(f"{entry_point}: {target!r}")
    raise NetworkBlocked(
        f"测试禁止真实出网，但代码试图 {entry_point} → {target!r}。\n"
        f"请用合成 fixture + monkeypatch 替身（范式见 tests/test_canvas_client.py 的 "
        f"_Resp/_Session）。\n"
        f"注意：本守卫挂在 socket 层是刻意的 —— library_areas 走的是它自己那条 "
        f"requests 路径，只拦 library_client 会漏掉它。详见 tests/conftest.py 顶部。")


@pytest.fixture(autouse=True)
def _block_real_network(monkeypatch):
    """autouse：整仓每个用例默认出不了网。需要真回环的用例无需做任何事（已放行）。"""
    real_getaddrinfo = socket.getaddrinfo
    real_create_connection = socket.create_connection
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guard_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if family == socket.AF_UNIX or _is_loopback(host):
            return real_getaddrinfo(host, port, family, type, proto, flags)
        _deny("socket.getaddrinfo", (host, port))

    def guard_create_connection(address, *args, **kwargs):
        host = _host_of(address)
        # `address` 是 str/bytes ⇒ unix domain socket 路径，放行。
        if isinstance(address, (str, bytes)) or _is_loopback(host):
            return real_create_connection(address, *args, **kwargs)
        _deny("socket.create_connection", address)

    def guard_connect(self, address):
        if getattr(self, "family", None) == socket.AF_UNIX or _is_loopback(_host_of(address)):
            return real_connect(self, address)
        _deny("socket.socket.connect", address)

    def guard_connect_ex(self, address):
        if getattr(self, "family", None) == socket.AF_UNIX or _is_loopback(_host_of(address)):
            return real_connect_ex(self, address)
        _deny("socket.socket.connect_ex", address)

    monkeypatch.setattr(socket, "getaddrinfo", guard_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", guard_create_connection)
    monkeypatch.setattr(socket.socket, "connect", guard_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guard_connect_ex)
    yield
