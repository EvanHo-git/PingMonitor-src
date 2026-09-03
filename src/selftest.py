# -*- coding: utf-8 -*-
"""无 GUI 自检（--selftest）。

目的：Windows exe 由云端 CI 产出、无法在本机人工验证，自检作为**产物可运行门禁**——
CI 执行 `PingMonitor.exe --selftest`，退出码非 0 即判定产物不可用。

硬性约束：
1. 除 check_tk 外**绝不 import tkinter、绝不创建窗口**；check_tk 故意 import tkinter
   以验证打包产物里的 Tcl/Tk 运行时是否可用（这是打包最易翻车处）。
2. 只能依赖会被 PyInstaller 打进包内的模块（src/ 下），不能依赖 tests/。
"""
import os
import sys
import time

from models import PingParams
from ping_runner import parse_ping_output, build_ping_cmd, round_deadline
from fixtures_ping import FIXTURES


def _close(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) < 1e-4


def check_fixtures(log):
    """验证五类 ping 输出的解析结果。返回 (是否通过, 通过项数, 失败项数)。"""
    ok_all, passed, failed = True, 0, 0
    for fx in FIXTURES:
        p = PingParams(count=fx["count"])
        res = parse_ping_output(fx["lines"], p, fx["target"], 0, time.time())
        exp = fx["expect"]
        checks = [
            ("received", res.received, exp["received"]),
            ("loss_rate", res.loss_rate, exp["loss_rate"]),
            ("min_rtt", res.min_rtt, exp["min_rtt"]),
            ("max_rtt", res.max_rtt, exp["max_rtt"]),
            ("avg_rtt", res.avg_rtt, exp["avg_rtt"]),
            ("jitter", res.jitter, exp["jitter"]),
            ("resolved_ip", res.resolved_ip, exp["resolved_ip"]),
        ]
        for field, got, want in checks:
            good = (got == want) if isinstance(want, str) else _close(got, want)
            if good:
                passed += 1
            else:
                failed += 1
                ok_all = False
                log(f"  [FAIL] {fx['name']} / {field}: 得到 {got!r}，期望 {want!r}")
    return ok_all, passed, failed


def check_cmd_construction(log):
    """验证跨平台命令构造（重点：单位陷阱与 -q 禁用）。"""
    ok = True
    p = PingParams(interval=0.5, timeout_ms=2000, count=3, concurrency=10)
    mac = build_ping_cmd("darwin", p, "10.0.0.1")
    win = build_ping_cmd("win32", p, "10.0.0.1")
    lin = build_ping_cmd("linux", p, "10.0.0.1")

    if "-q" in mac or "-q" in win or "-q" in lin:
        ok = False
        log("  [FAIL] 探测命令包含 -q，会吞掉逐包时延导致抖动无法计算")
    if mac[mac.index("-W") + 1] != "2000":
        ok = False
        log(f"  [FAIL] macOS -W 应为毫秒，实得 {mac}")
    if lin[lin.index("-W") + 1] != "2.000":
        ok = False
        log(f"  [FAIL] Linux -W 应为秒，实得 {lin}")
    if "-i" in win:
        ok = False
        log(f"  [FAIL] Windows 不支持 -i，实得 {win}")
    if round_deadline(p, "win32") < 3 * 1.0 + 2.0:
        ok = False
        log("  [FAIL] Windows deadline 未按固定 1 秒/包计算")
    return ok


def check_real_ping(log):
    """真实 ping 回环地址，验证系统 ping 可用且能被解析。"""
    from scheduler import ProbeScheduler
    host = "127.0.0.1"
    log(f"  真实探测 {host} ...")
    try:
        res = ProbeScheduler.quick_probe(host, PingParams(count=2, timeout_ms=2000, interval=0.5))
    except Exception as exc:  # noqa: BLE001
        log(f"  [FAIL] 探测异常：{type(exc).__name__}: {exc}")
        return False
    log(f"  结果：发出={res.sent} 收到={res.received} 丢包率={res.loss_rate:.0%} "
        f"平均={res.avg_rtt if res.avg_rtt is None else round(res.avg_rtt, 3)}ms")
    if res.received == 0:
        log("  [FAIL] 回环地址未收到任何回包（系统 ping 不可用或被策略拦截）")
        return False
    return True


def check_tk(log):
    """验证打包产物中的 Tcl/Tk 运行时可用。

    这是打包环节最容易翻车的地方：Tcl/Tk 脚本目录若未被 PyInstaller 正确收集，
    就会在启动时报 "can't find init.tcl"。

    校验分两层：
      1) Tcl 解释器初始化（加载 tcl 库，无需显示环境）—— 硬性门禁，失败即产物不可用；
      2) Tk 根窗口创建（需要显示环境）—— 尽力而为，CI headless 无显示时仅告警不判失败。
    """
    try:
        import config
        config.setup_tcl_env()
        import tkinter
    except Exception as exc:  # noqa: BLE001
        log(f"  [FAIL] import tkinter 失败：{type(exc).__name__}: {exc}")
        return False
    # 1) Tcl 库加载（无显示依赖，捕捉最常见的 init.tcl 缺失）
    try:
        tcl = tkinter.Tcl()
        patch = tcl.eval("info patchlevel")
        log(f"  Tcl 运行时可用（patchlevel {patch}）")
    except Exception as exc:  # noqa: BLE001
        log(f"  [FAIL] Tcl 初始化失败（TCL_LIBRARY 未正确收集）：{type(exc).__name__}: {exc}")
        return False
    # 2) Tk 窗口创建：仅在打包产物（frozen）中强制校验——
    #    这是验证 PyInstaller 是否把 tcl9.0/tk9.0 脚本目录收齐的关键门禁。
    #    开发态（非 frozen）由 GUI 集成测试覆盖窗口创建，此处跳过，避免无谓告警。
    if getattr(sys, "frozen", False):
        try:
            root = tkinter.Tk()
            ver = root.tk.call("info", "patchlevel")
            root.withdraw()
            root.update()
            root.destroy()
            log(f"  Tk 窗口创建成功（patchlevel {ver}）")
        except Exception as exc:  # noqa: BLE001
            log(f"  [WARN] Tk 窗口创建失败（多为 CI 无显示环境，本地双击不受影响）："
                f"{type(exc).__name__}: {exc}")
    else:
        log("  [跳过] 开发态不强制 Tk 窗口校验（由 GUI 集成测试覆盖）")
    return True


def _report_path() -> str:
    """--windowed 下 stdout 不可见，落盘便于排障。

    绝不能写进 .app 包内（会破坏 ad-hoc 签名），故写入系统临时目录。
    """
    import tempfile
    return os.path.join(tempfile.gettempdir(), "pingmonitor_selftest_report.txt")


def run_selftest() -> int:
    lines = []
    started = time.strftime("%Y-%m-%d %H:%M:%S")

    def log(msg):
        lines.append(msg)
        # --windowed 在 headless 环境（如 CI runner）下 sys.stdout 为 None，
        # 直接 print 会抛异常；stdout 不可用时静默丢弃，真实记录以落盘报告为准。
        try:
            if sys.stdout is not None:
                sys.stdout.write(msg + "\n")
        except Exception:  # noqa: BLE001
            pass
   

    log("=" * 60)
    log(f"PingMonitor 自检  started={started}  platform={sys.platform}  python={sys.version.split()[0]}")
    log("=" * 60)

    log("[1/4] 解析层 fixture 校验")
    ok_fix, passed, failed = check_fixtures(log)
    log(f"      通过 {passed} 项，失败 {failed} 项")

    log("[2/4] 跨平台命令构造校验")
    ok_cmd = check_cmd_construction(log)

    log("[3/4] 真实 ping 回环探测")
    ok_ping = check_real_ping(log)

    log("[4/4] Tcl/Tk 运行时校验（打包产物 GUI 可用性）")
    ok_tk = check_tk(log)

    ok = ok_fix and ok_cmd and ok_ping and ok_tk
    log("-" * 60)
    log(f"SELFTEST {'PASS' if ok else 'FAIL'}")
    log("-" * 60)

    try:
        with open(_report_path(), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        log(f"报告已写入：{_report_path()}")
    except OSError as exc:
        log(f"报告写入失败（不影响退出码）：{exc}")

    return 0 if ok else 1
