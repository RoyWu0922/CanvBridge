#!/usr/bin/env python3
"""CanvBridge 桌面启动入口。

默认形态：起本地服务（127.0.0.1:8331）+ pywebview 内嵌原生窗口（WKWebView）渲染前端，
不再借用系统浏览器的标签页。外链/新窗口由桥（js_api.openExternal）交回系统默认浏览器。

退回「开浏览器」模式：python run_app.py --browser 或设环境变量 CANVBRIDGE_WEBVIEW=0
（服务仍只监听 127.0.0.1，凭据仅存本机，安全模型不变）。

源码运行：python run_app.py
PyInstaller 打包后：双击 .app 即执行本模块。
"""
import os
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from backend.main import app

HOST = "127.0.0.1"
PORT = 8331
URL = f"http://{HOST}:{PORT}"


def _port_open() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.3):
            return True
    except OSError:
        return False


def _wait_ready(deadline: float = 10.0) -> bool:
    """等服务真正起来（打开窗口 / 浏览器时避免 502）。"""
    end = time.time() + deadline
    while time.time() < end:
        if _port_open():
            return True
        time.sleep(0.3)
    return False


def _open_browser() -> None:
    if not _wait_ready(8.0):
        return
    webbrowser.open(URL)


# ---- webview 桥：窗口内 JS 调用的对象（方法名即暴露给 window.pywebview.api 的名字）----
class _ShellApi:
    """只暴露白名单动作；凭据等敏感信息不经过这里。"""

    def openExternal(self, url: str):
        """前端外链（Canvas 等）交给系统默认浏览器。只放行 http/https。"""
        if isinstance(url, str) and url.lower().startswith(("http://", "https://")):
            webbrowser.open(url)
        return True


def run_webview() -> None:
    holder: dict = {}

    def _serve() -> None:
        cfg = uvicorn.Config(app, host=HOST, port=PORT, log_level="info")
        server = uvicorn.Server(cfg)
        holder["server"] = server
        try:
            server.run()
        except Exception:  # 绑定失败等：不崩溃，让窗口侧收尾
            pass

    threading.Thread(target=_serve, daemon=True).start()

    if not _wait_ready():
        sys.stderr.write("本地服务未能启动，请检查端口占用或日志。\n")
        sys.exit(1)

    import webview  # 惰性导入：浏览器退回模式不依赖 pywebview

    webview.create_window(
        "CanvBridge",
        URL,
        js_api=_ShellApi(),
        width=1280,
        height=900,
        min_size=(980, 680),
    )
    webview.start()
    # 窗口全关 → 停服退出
    server = holder.get("server")
    if server is not None:
        server.should_exit = True


def main() -> None:
    use_browser = os.environ.get("CANVBRIDGE_WEBVIEW", "1") == "0" or "--browser" in sys.argv
    if use_browser:
        threading.Thread(target=_open_browser, daemon=True).start()
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    else:
        run_webview()


if __name__ == "__main__":
    main()
