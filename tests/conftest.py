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

## 但**只拦还不够**：拦截日志非空 ⇒ 用例判红

抛出会被上层的降级 `except` 吞掉：`library_areas.load_areas()` 自己就写着
「任何问题一律当没有」，`library_client.list_domains()` 同理，F-2 之后端点那两处
也各自有兜底。于是**一次真实外发**会以「`BLOCKED` 里多一行 + 用例照旧全绿」的形态
出现 —— 拦是拦住了，**但它是静默的**。R69 之所以存在，正是因为这条约束
**在同一天内静默失守过两次**（见上）。所以每个用例结束时
`_block_real_network` 会**断言 `BLOCKED` 为空**，失败消息里带入口、目标与用例 id。

默认即「拦截即失败」；需要**故意**制造一次被拦尝试的用例（验证守卫本身的那些）
必须显式请求 `allow_network_block` fixture —— 见该 fixture 的说明。

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
#: **拦截本身还不够**：光拦的话，被拦下的那次尝试会被上层的降级 `except` 吞掉，
#: 于是「一次真实外发」= 一条无人断言的日志 + **一个绿色的用例**。所以本列表
#: 每个用例结束时会被断言为空 —— 见 `_block_real_network` 的收尾。
BLOCKED: list[str] = []

#: 「拦截即失败」的**按用例**退出开关。只有显式请求了 `allow_network_block`
#: fixture 的用例才会进来（用于验证守卫本身的用例）；没有全局开关。
_OPT_OUT: set[str] = set()

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


def _blocked_failure_message(nodeid: str) -> str:
    """拦截日志非空时的失败说明 —— 必须**自足**：入口、目标、出事的用例都在里面。"""
    entries = "\n".join(f"    - {line}" for line in BLOCKED)
    return (
        f"[netguard] 用例 {nodeid} 期间发生了 {len(BLOCKED)} 次**真实出网尝试**"
        f"（已被 socket 层拦下，没有真的发出去）：\n{entries}\n"
        f"  拦住它们不等于没事：这次尝试**被上层的降级 except 吞掉了**，所以用例照样绿 ——\n"
        f"  这正是本条断言存在的理由（R69：这个约束曾在同一天内**静默**失守两次）。\n"
        f"  请改用合成 fixture + monkeypatch 替身（范式见 tests/test_canvas_client.py 的 _Resp/_Session）。\n"
        f"  ⚠️ 注意 library_areas 走的是它自己那条 requests 路径（不经过 library_client._get），\n"
        f"     替身要打在**它自己**的出口上（见 tests/test_library_areas.py 的 _Session）。\n"
        f"  如果本用例是**故意**要验证守卫本身，请显式请求 `allow_network_block` fixture。"
    )


@pytest.fixture
def allow_network_block(_block_real_network):
    """**显式的、按用例的**「拦截即失败」退出开关。

    只有「验证守卫本身、故意制造一次被拦尝试」的用例才该请求它 —— 默认（不请求）
    一律是拦截即失败。刻意做成 **fixture 参数**而不是全局开关：它必须出现在
    **用到它的那个用例的签名里**，读代码的人一眼就能看见「这个用例知道自己在碰网络」。

    形参 `_block_real_network` 是**故意的**：它把顺序钉死 —— 守卫先建立、后收尾，
    于是守卫的收尾一定发生在**本 fixture 之后**，也就读得到下面那个标志。
    （曾经在本地 `yield` 之后 `discard`，结果本 fixture 先收尾、标志先被清掉，
    守卫收尾读不到 ⇒ 自检用例被自己判红。这条依赖就是那个 bug 的修复。）
    """
    _OPT_OUT.add("allow")
    yield
    # 不在这里 discard：守卫的收尾要**先读**这个标志、读完再清（见 `_block_real_network`）。


@pytest.fixture(autouse=True)
def _block_real_network(request, monkeypatch):
    """autouse：整仓每个用例默认出不了网。需要真回环的用例无需做任何事（已放行）。

    **收尾会断言「本用例一次都没被拦过」** —— 这条断言才是 R69 的要点：光拦不报的话，
    历史上那两次失守会以「日志里多一行 + 用例全绿」的形态原样复现。
    """
    BLOCKED.clear()
    _OPT_OUT.clear()      # autouse 一定先于 `allow_network_block` 建立，此时它还没登记
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
    allowed = "allow" in _OPT_OUT
    _OPT_OUT.clear()      # 清掉本用例的退出标志，绝不泄漏到下一条用例
    if BLOCKED and not allowed:
        # `pytrace=False`：失败点在收尾、不在用例体内，栈回溯只会指错地方；
        # 消息本身已经把入口 / 目标 / 用例 id 全写清楚了。
        pytest.fail(_blocked_failure_message(request.node.nodeid), pytrace=False)
