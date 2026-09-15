"""守卫本身的自检（R69）。

`tests/conftest.py` 的 `_block_real_network` 是**全仓默认**的纪律：既拦又报。
它的两条契约在这里各钉一条 —— 拦得住外网 + 放得过回环。
"""
import socket

import pytest

from tests.conftest import BLOCKED, NetworkBlocked


def test_guard_blocks_an_external_host(allow_network_block):
    """非回环主机名必须在 socket 层就被拦下（DNS 那一层，请求根本发不出去）。

    本用例**故意**制造一次被拦的尝试 ⇒ 显式请求 `allow_network_block`。
    没有那个 fixture 的话，本用例会因为「自己制造了一次出网尝试」在收尾被判红 ——
    这正是默认行为（拦截即失败）；这里退出的理由是「本用例就是来验证拦截的」。
    """
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo("example.invalid", 443)
    assert any("example.invalid" in line for line in BLOCKED)
    # 拦下 ≠ 放行：连 DNS 都没有真的发出去（异常在解析前抛出）。
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("example.invalid", 443))
    with pytest.raises(NetworkBlocked):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("93.184.216.34", 443))


def test_guard_lets_loopback_through():
    """回环必须照旧可用（`TestClient` 与任何本地监听都依赖它）—— 无需任何 opt-out。

    这里真的起一次 `getaddrinfo`：若守卫把回环一起拦了，本用例会红在
    `NetworkBlocked` 上，而不是红在「收尾发现被拦过」上（回环不进 `BLOCKED`）。
    """
    assert socket.getaddrinfo("127.0.0.1", 80)
    assert socket.getaddrinfo("localhost", 80)
    assert BLOCKED == []
