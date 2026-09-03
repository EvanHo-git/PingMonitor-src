# -*- coding: utf-8 -*-
"""GUI 集成测试：真实创建窗口、跑几轮探测、验证图表图元、切视图、切主题、导出。

运行：/Users/evan/.workbuddy/binaries/python/envs/default/bin/python tests/test_gui_smoke.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from models import TargetConfig  # noqa: E402
import persistence  # noqa: E402

_failed = []
_passed = 0


def check(cond, msg):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(msg)


def main():
    import config
    config.setup_tcl_env()  # 开发态 venv 需定位 Tcl/Tk，必须在创建 Tk 窗口前调用

    from gui import PingMonitorApp

    app = PingMonitorApp()
    targets = [
        TargetConfig(name="本机回环", host="127.0.0.1"),
        TargetConfig(name="黑洞地址", host="10.255.255.1"),
        TargetConfig(name="京东门户", host="www.jd.com"),
        TargetConfig(name="外网DNS", host="223.5.5.5"),
    ]
    app.targets = targets
    app.params.interval = 0.5
    app.params.count = 3
    app.params.timeout_ms = 1500
    app.params.concurrency = 4
    app._sync_target_tree()
    app.on_start()

    state = {"stage": 0}

    # ---------- 阶段 1：叠加视图渲染 ----------
    def stage1():
        state["stage"] = 1
        c = app.chart.canvas
        items = c.find_all()
        traces = c.find_withtag("trace")
        check(len(app.records) >= 4, f"采集记录过少：{len(app.records)}")
        check(len(traces) >= len(targets), f"叠加视图曲线数 {len(traces)} < 目标数 {len(targets)}")
        check(len(items) > 20, f"叠加视图图元过少：{len(items)}（网格/坐标轴未绘制？）")
        check(len(app.chart.legend_items) == len(targets), "图例数量与目标数不符")
        # 黑洞地址应判为 100% 丢包
        black = [r for r in app.records if r.target == "10.255.255.1"]
        check(bool(black) and all(r.loss_rate == 1.0 for r in black),
              "黑洞地址未判为 100% 丢包")
        jd = [r for r in app.records if r.target == "www.jd.com" and r.resolved_ip]
        check(bool(jd), "域名未解析出真实 IP")

    # ---------- 阶段 2：网格视图 ----------
    def stage2():
        state["stage"] = 2
        app.var_view.set("grid")
        app.on_view_change()
        app.update()
        check(len(app.chart.cells) == len(targets),
              f"网格小图数量 {len(app.chart.cells)} != 目标数 {len(targets)}")
        drawn = 0
        for host, (cv, info) in app.chart.cells.items():
            if len(cv.find_all()) >= 2:
                drawn += 1
        check(drawn >= len(targets) - 1, f"网格小图中只有 {drawn} 个画出了内容")

    # ---------- 阶段 3：深色主题 + 导出 ----------
    def stage3():
        state["stage"] = 3
        # 主题是持久化的，上一轮测试可能留下深色，故断言"发生翻转"而非固定方向
        before = app.theme_name
        app.on_toggle_theme()
        app.update()
        after = app.theme_name
        check(after != before, f"主题未发生切换（{before} → {after}）")
        check(persistence.load_theme() == after, f"主题未持久化（磁盘为 {persistence.load_theme()}）")

    def finish():
        app.on_stop()
        check(len(app.records) > 0, "无监测记录")
        csv_path = persistence.export_csv(app.records)
        json_path = persistence.export_json(app.records)
        check(os.path.getsize(csv_path) > 100, "CSV 导出为空")
        check(os.path.getsize(json_path) > 100, "JSON 导出为空")
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            head = f.readline()
        check("口径说明" in head, "CSV 缺少口径说明行")

        print(f"采集记录：{len(app.records)} 条")
        for r in list(app.records)[-4:]:
            print(f"  {r.display:<8} 收到={r.received}/{r.sent} 丢包={r.loss_rate:>4.0%} "
                  f"平均={r.avg_rtt if r.avg_rtt is None else round(r.avg_rtt, 2)}ms "
                  f"抖动={r.jitter if r.jitter is None else round(r.jitter, 2)}ms "
                  f"ip={r.resolved_ip or '-'}")
        print(f"CSV ：{csv_path}")
        print(f"JSON：{json_path}")
        for f in _failed:
            print("  FAIL:", f)
        print(f"GUI 集成测试：通过 {_passed} 项，失败 {len(_failed)} 项")
        print("RESULT:", "PASS" if not _failed else "FAIL")
        app.destroy()
        global _ok
        _ok = not _failed

    app.after(7000, stage1)
    app.after(9000, stage2)
    app.after(12000, stage3)
    app.after(14500, finish)
    app.mainloop()


_ok = False

if __name__ == "__main__":
    main()
    sys.exit(0 if _ok else 1)
