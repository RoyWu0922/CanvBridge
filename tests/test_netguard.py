"""守卫本身的自检（R69）。

`tests/conftest.py` 的 `_block_real_network` 是**全仓默认**的纪律：既拦又报。
它的两条契约在这里各钉一条 —— 拦得住外网 + 放得过回环。

## 本文件自己**绝不能**是一条出网路径

自检的意义是「守卫坏掉时它红」。如果它偏偏在**守卫已经坏掉**的状态下真的去连
公网，那它就从「检测器」变成了「泄漏源」—— 比没有自检更糟。所以这里的每一个
目标都是**保留地址**：

- `example.invalid` —— RFC 6761 / RFC 2606 保留的 `.invalid` 顶级域，
  **保证不可解析**，DNS 查了也拿不到地址；
- `192.0.2.1` —— RFC 5737 的 TEST-NET-1，**保留且永不在公网路由**。
  这一条刻意用**裸 IP**，为的是走「不经过 DNS 的那条路」（`connect` / `connect_ex`
  直接吃 IP）—— 那条路 DNS 层的守卫罩不到，值得单独钉住。

⚠️ 后来者请勿把上面任何一个换成「看起来更真实」的域名或 IP。它们之所以长这样，
就是因为**只有守卫坏掉时才会真的走到它们**。
"""
import socket

import pytest

from tests.conftest import BLOCKED, NetworkBlocked

#: RFC 5737 TEST-NET-1：保留、永不在公网路由。裸 IP ⇒ 走不经过 DNS 的那条路。
TEST_NET = "192.0.2.1"


def test_guard_blocks_an_external_host(allow_network_block):
    """非回环目标必须在 socket 层就被拦下（DNS 那一层，请求根本发不出去）。

    本用例**故意**制造被拦的尝试 ⇒ 显式请求 `allow_network_block`。
    没有那个 fixture 的话，本用例会因为「自己制造了一次出网尝试」在收尾被判红 ——
    这正是默认行为（拦截即失败）；这里退出的理由是「本用例就是来验证拦截的」。

    四个入口逐一钉：`getaddrinfo`（DNS）/ `create_connection`（requests 走这条）/
    `socket.connect`、`socket.connect_ex`（裸 IP、不经 DNS 的两条）。
    """
    # 入口 1：DNS。`.invalid` 是保留域，查了也解析不出来。
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo("example.invalid", 443)
    assert any("example.invalid" in line for line in BLOCKED)

    # 入口 2：`create_connection` —— requests/urllib3 的实际出口。
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("example.invalid", 443))

    # 入口 3：裸 socket 的 `connect`，目标用裸 IP ⇒ 完全不碰 DNS。
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.25)
        with pytest.raises(NetworkBlocked):
            s.connect((TEST_NET, 443))
    finally:
        s.close()

    # 入口 4：`connect_ex`。它**不抛异常、只返回 errno**，是最容易在替身里被漏掉
    # 的一条 —— 漏了它，一次真实外发就是一个 `errno` 而没有任何异常。
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # `settimeout` 只在**守卫坏掉**的那种状态里才有意义：那时最坏情况是
        # TEST-NET 静默丢包，超时把返回钉在 0.25s 内，而不是挂到 TCP 默认超时。
        s.settimeout(0.25)
        with pytest.raises(NetworkBlocked):
            s.connect_ex((TEST_NET, 443))
    finally:
        s.close()


def test_guard_lets_loopback_through():
    """回环必须照旧可用（`TestClient` 与任何本地监听都依赖它）—— 无需任何 opt-out。

    这里真的起一次 `getaddrinfo`：若守卫把回环一起拦了，本用例会红在
    `NetworkBlocked` 上，而不是红在「收尾发现被拦过」上（回环不进 `BLOCKED`）。
    """
    assert socket.getaddrinfo("127.0.0.1", 80)
    assert socket.getaddrinfo("localhost", 80)
    assert BLOCKED == []
