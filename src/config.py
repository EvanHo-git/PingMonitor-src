# -*- coding: utf-8 -*-
"""平台判定、默认参数、用户数据目录与 frozen 资源定位。"""
import os
import sys

from models import PingParams

APP_ID = "com.evan.pingmonitor"
APP_NAME = "PingMonitor"

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WIN and not IS_MAC

# Windows 系统 ping 不支持自定义包间隔，固定 1 秒/包
WIN_FIXED_INTERVAL = 1.0


def default_params() -> PingParams:
    return PingParams(interval=0.5, timeout_ms=2000, count=3, concurrency=10)


def effective_interval(p: PingParams, platform: str = None) -> float:
    """返回该平台下实际生效的包间隔（Windows 恒为 1.0 秒）。"""
    platform = platform or sys.platform
    return WIN_FIXED_INTERVAL if platform == "win32" else float(p.interval)


def supports_custom_interval(platform: str = None) -> bool:
    platform = platform or sys.platform
    return platform != "win32"


def user_data_dir() -> str:
    """配置与导出目录。

    在 PyInstaller frozen 模式下**绝不写入 .app 包内**（会破坏签名且可能只读），
    一律落到操作系统约定的用户目录。
    """
    if IS_MAC:
        base = os.path.expanduser("~/Library/Application Support")
    elif IS_WIN:
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(base, APP_ID)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = os.path.join(os.path.expanduser("~"), "." + APP_ID)
        os.makedirs(path, exist_ok=True)
    return path


def export_dir() -> str:
    path = os.path.join(user_data_dir(), "exports")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = user_data_dir()
    return path


def _bundle_root() -> str:
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def resource_dir(sub: str = "") -> str:
    base = _bundle_root()
    return os.path.join(base, sub) if sub else base


def setup_tcl_env() -> None:
    """定位 Tcl/Tk 运行时，必须在创建 Tk 窗口之前调用。

    - 打包态（frozen）：用 bundle 内自带的 tcl9.0/tk9.0（PyInstaller 已收集）。
    - 开发态（venv）：venv 自身不含 Tcl/Tk，回退到创建该 venv 的基础 Python
      安装目录（sys.base_prefix/lib/tcl9.0），否则会报 "can't find init.tcl"。
    """
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(_bundle_root())               # _MEIPASS / .app/Contents/MacOS
    else:
        base = getattr(sys, "base_prefix", None) or sys.prefix
        roots.append(os.path.join(base, "lib"))    # 基础 Python 的 Tcl/Tk
        roots.append(os.path.join(sys.prefix, "lib"))

    candidates = [
        ("TCL_LIBRARY", "tcl9.0"),
        ("TK_LIBRARY", "tk9.0"),
        ("TCL_LIBRARY", "tcl8.6"),
        ("TK_LIBRARY", "tk8.6"),
        ("TCL_LIBRARY", "tcl"),
        ("TK_LIBRARY", "tk"),
    ]
    for root in roots:
        for var, sub in candidates:
            candidate = os.path.join(root, sub)
            if os.path.isdir(candidate):
                os.environ.setdefault(var, candidate)
