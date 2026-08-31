#!/usr/bin/env python3
"""CanvBridge 桌面启动入口：起本地服务并自动打开浏览器。

源码运行时：python run_app.py
PyInstaller 打包后：双击 .app 即执行本模块。
"""
import threading
import time
import webbrowser

import uvicorn

from backend.main import app

HOST = "127.0.0.1"
PORT = 8331


def _open_browser() -> None:
    # 等服务真正起来再开浏览器，避免打开时 502
    deadline = time.time() + 8
    url = f"http://{HOST}:{PORT}"
    while time.time() < deadline:
        try:
            import socket

            with socket.create_connection((HOST, PORT), timeout=0.3):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.3)


if __name__ == "__main__":
    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
