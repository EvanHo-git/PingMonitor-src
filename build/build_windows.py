#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 构建脚本（GitHub Actions 与本地 Windows 共用）。

产出 dist/PingMonitor.exe（--onefile，双击即运行，终端零第三方依赖）。
依赖：官方 CPython 3.13（自带 Tcl/Tk 8.6）。

用法：
  python build/build_windows.py          # 在装有 Python 3.13 的环境直接构建
CI 中由 .github/workflows/build.yml 调用同一脚本，保证本地与云端产物一致。
"""
import os
import shutil
import subprocess
import sys
import sysconfig

# GitHub Windows runner 默认控制台编码为 cp1252，无法编码中文 print，
# 会在脚本启动首条中文打印处炸出 UnicodeEncodeError 导致构建判定失败。
# 重配置为 utf-8 是跨平台最稳的做法（macOS 本就是 utf-8，无副作用）。
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, ".."))
APP_NAME = "PingMonitor"


def find_tcltk():
    """定位官方 CPython 的 Tcl/Tk 8.6 资源目录（Windows 布局）。"""
    prefix = sysconfig.get_config_var("prefix")
    tcl_root = os.path.join(prefix, "tcl")
    dlls = os.path.join(prefix, "DLLs")
    tcl_data = os.path.join(tcl_root, "tcl8.6") if os.path.isdir(os.path.join(tcl_root, "tcl8.6")) else None
    tk_data = os.path.join(tcl_root, "tk8.6") if os.path.isdir(os.path.join(tcl_root, "tk8.6")) else None
    dll_dir = dlls if os.path.isdir(dlls) else None
    return tcl_data, tk_data, dll_dir


def main():
    os.chdir(PROJECT)
    py = sys.executable
    tcl_data, tk_data, dll_dir = find_tcltk()

    print("==> [1/4] 定位 Tcl/Tk 资源")
    if tcl_data:
        print("    tcl:", tcl_data)
    if tk_data:
        print("    tk :", tk_data)
    if dll_dir:
        print("    dll:", dll_dir)
    if not (tcl_data and tk_data):
        print("    [WARN] 未找到 tcl8.6/tk8.6，回退到 PyInstaller 自动收集（官方 CPython 通常可行）")

    print("==> [2/4] PyInstaller 打包（--onefile）")
    cmd = [
        py, "-m", "PyInstaller",
        "--windowed",
        "--onefile",
        "--name", APP_NAME,
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.ttk",
        "--noconfirm",
        "src/main.py",
    ]
    # Windows 用分号作 --add-data / --add-binary 的路径分隔符
    if tcl_data:
        cmd += ["--add-data", f"{tcl_data};tcl8.6"]
    if tk_data:
        cmd += ["--add-data", f"{tk_data};tk8.6"]
    if dll_dir:
        for dll in ("tcl86t.dll", "tk86t.dll"):
            p = os.path.join(dll_dir, dll)
            if os.path.isfile(p):
                cmd += ["--add-binary", f"{p};."]
    rc = subprocess.call(cmd)
    if rc != 0:
        sys.exit(rc)

    exe = os.path.join(PROJECT, "dist", APP_NAME + ".exe")
    if not os.path.isfile(exe):
        print("!! 未生成 exe")
        sys.exit(3)

    print("==> [3/4] 打包产物自检（--selftest）")
    rc = subprocess.call([exe, "--selftest"])
    if rc != 0:
        print("!! 自检失败，产物不可用")
        sys.exit(rc)

    print("==> [4/4] 构建完成")
    print("    ", exe)
    print(f"    体积 {os.path.getsize(exe) / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
