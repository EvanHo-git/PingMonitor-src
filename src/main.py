# -*- coding: utf-8 -*-
"""PingMonitor 入口。

启动顺序至关重要：
  1. 先处理 --selftest（绝不 import tkinter）；
  2. 再设置 frozen 模式下的 Tcl/Tk 路径（必须在 import tkinter 之前）；
  3. 最后启动 GUI。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VERSION = "1.0.0"


def _print_usage():
    print(f"PingMonitor {VERSION}")
    print("用法：")
    print("  PingMonitor              启动图形界面")
    print("  PingMonitor --selftest   无界面自检（供打包产物验证，退出码反映成败）")
    print("  PingMonitor --version    显示版本")


def main():
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        _print_usage()
        return 0
    if "--version" in args:
        print(VERSION)
        return 0

    # 自检分支：绝不导入 tkinter、绝不创建窗口
    if "--selftest" in args:
        from selftest import run_selftest
        return run_selftest()

    import config
    config.setup_tcl_env()

    from gui import PingMonitorApp
    app = PingMonitorApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
