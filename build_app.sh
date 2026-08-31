#!/bin/bash
# CanvBridge macOS App 打包脚本。
# 用法：./build_app.sh
# 产物：dist/CanvBridge.app
set -euo pipefail
cd "$(dirname "$0")"

# PyInstaller 若缺就装（日常开发不强制装）
if ! .venv/bin/pyinstaller --version >/dev/null 2>&1; then
  .venv/bin/pip install pyinstaller
fi

# 图标缺失时提示（docs/icon.icns 用 iconutil 从 1024px PNG 生成）
if [ ! -f docs/icon.icns ]; then
  echo "缺少 docs/icon.icns（先准备 1024×1024 PNG，用 iconutil -c icns 生成）"
  exit 1
fi

.venv/bin/pyinstaller --clean --noconfirm \
  --name CanvBridge \
  --windowed \
  --icon "$(pwd)/docs/icon.icns" \
  --add-data "frontend:frontend" \
  --collect-all playwright \
  --hidden-import "uvicorn.logging" \
  --hidden-import "uvicorn.loops.auto" \
  --hidden-import "uvicorn.protocols.http.auto" \
  --hidden-import "uvicorn.protocols.websockets.auto" \
  --hidden-import "uvicorn.lifespan.on" \
  run_app.py

echo ""
echo "完成：dist/CanvBridge.app"
echo "双击运行（首次被 Gatekeeper 拦就右键→打开，或系统设置→隐私与安全性→仍要打开）"
