#!/bin/bash
# -*- coding: utf-8 -*-
# macOS 打包脚本：PyInstaller 构建 .app + ad-hoc 签名 + 打包后自检
#
# 用法：cd ping-monitor && bash build/build_macos.sh
#
# 关键背景：本机 Python 3.13.12 是**非 framework 构建**，Tcl/Tk 9.0 以
#   lib/libtcl9.0.dylib、lib/libtcl9tk9.0.dylib、lib/tcl9.0/、lib/tk9.0/
# 平铺存在。且 lib/tk9.0/pkgIndex.tcl 用相对路径 ../libtcl9tk9.0.dylib 加载 Tk，
# 因此这四者必须**共置于 bundle 根目录**，否则启动时报 "can't find init.tcl"。

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PY="/Users/evan/.workbuddy/binaries/python/envs/default/bin/python"
TCL_LIB="/Users/evan/.workbuddy/binaries/python/versions/3.13.12/lib/tcl9.0"
TK_LIB="/Users/evan/.workbuddy/binaries/python/versions/3.13.12/lib/tk9.0"
PY_LIB="/Users/evan/.workbuddy/binaries/python/versions/3.13.12/lib"

echo "==> [1/5] 清理上次构建产物"
rm -rf build/PingMonitor dist/PingMonitor.app dist/PingMonitor

echo "==> [2/5] PyInstaller 打包"
"$PY" -m PyInstaller \
  --windowed \
  --name PingMonitor \
  --osx-bundle-identifier com.evan.pingmonitor \
  --target-arch arm64 \
  --hidden-import tkinter \
  --hidden-import tkinter.ttk \
  --add-binary "${PY_LIB}/libtcl9.0.dylib:." \
  --add-binary "${PY_LIB}/libtcl9tk9.0.dylib:." \
  --add-data   "${TCL_LIB}:./tcl9.0" \
  --add-data   "${TK_LIB}:./tk9.0" \
  --noconfirm \
  src/main.py

echo "==> [3/5] 校验 Tcl/Tk 资源是否就位"
APP="dist/PingMonitor.app"
MACOS_DIR="${APP}/Contents/MacOS"
# PyInstaller 6 的 BUNDLE 布局：可执行文件在 Contents/MacOS，
# 二进制与数据分别落在 Contents/Frameworks 与 Contents/Resources
missing=0
for d in Frameworks Resources; do
  for f in libtcl9.0.dylib libtcl9tk9.0.dylib tcl9.0 tk9.0; do
    if [ ! -e "${APP}/Contents/${d}/${f}" ]; then
      echo "!! 缺少 ${APP}/Contents/${d}/${f}"
      missing=1
    fi
  done
done
if [ "${missing}" -ne 0 ]; then
  echo "!! Tcl/Tk 资源未收齐 —— 请改用显式 spec：pyinstaller build/macos.spec"
  exit 2
fi
echo "    Tcl/Tk 资源就位（Frameworks + Resources）"

echo "==> [4/5] ad-hoc 代码签名（本机无开发者证书，仅能自签名）"
codesign --force --deep --sign - "${APP}"
codesign -vvv "${APP}" 2>&1 | tail -2

echo "==> [5/5] 打包产物自检（--selftest）"
"${MACOS_DIR}/PingMonitor" --selftest
RC=$?
if [ $RC -ne 0 ]; then
  echo "!! 自检失败，产物不可用"
  exit $RC
fi

echo
echo "==> 构建完成：${APP}"
du -sh "${APP}"
