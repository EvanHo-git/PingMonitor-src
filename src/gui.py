# -*- coding: utf-8 -*-
"""PingMonitor 主界面。

线程模型：探测线程只跑子进程与解析，结果经 queue 回传；
本模块所有 Tk 操作都在主线程完成（Tk 不是线程安全的）。
"""
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from collections import deque

import colors
from colors import STYLE_OF, semantic, disabled_colors
from chart import LatencyChart
from config import (IS_WIN, effective_interval, supports_custom_interval,
                    default_params, export_dir)
from models import MAX_POINTS, MAX_TARGETS, PingParams, TargetConfig, iso_time
import hostinfo
import persistence
import history as history_mod
from scheduler import ProbeScheduler
import toast as toast_mod

_EXPORT_MAX = MAX_POINTS * MAX_TARGETS   # 内存中保留的探测记录上限

# 按钮语义 -> 中文说明，用于 tooltip 与配色图例，保证"同色同义"
KIND_LABEL = {
    "primary": "主操作（启动类）",
    "secondary": "次操作（辅助/设置类）",
    "danger": "危险操作（终止/删除/清空类）",
    "export": "导出类（数据导出）",
}


class PingMonitorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.params = persistence.load_params()
        self.targets = persistence.load_targets()
        self.theme_name = persistence.load_theme("light")
        # 首次使用默认网格小图；之后沿用用户上次选择（见 on_view_change）
        self.view = persistence.load_view("grid")
        self.host_info = {"computer": "", "primary_ip": "", "nics": [],
                          "error": ""}
        self.records = deque(maxlen=_EXPORT_MAX)
        self.q = queue.Queue()
        self._host_q = queue.Queue()        # 本机信息：后台线程 -> 主线程
        self._host_pending = False
        self.scheduler = None
        self._last_draw = 0.0
        self._rows = {}
        # F1: 删除撤销栈（最近 5 次删除，每次保存 targets + 曲线 + 关联 records）
        self._undo_stack = []
        self._undo_max = 5
        self._current_toast = None
        # O5: 监测会话开始时间戳，用于导出文件名
        self._session_start = 0.0
        # F3: hostinfo 采集起始时间
        self._host_collect_at = 0.0
        # F2: 启动空状态控件
        self._empty_state = None
        # O6: 启动时恢复上轮会话的探测记录
        self._restore_history()

        self.title("批量 Ping 网络质量监测工具")
        self.geometry("1420x960")
        self.minsize(1180, 760)

        self._build_ui()
        self._apply_theme()
        self._sync_target_tree()
        self._restore_chart_from_records()
        self._update_status()
        self._refresh_empty_state()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)

    def _restore_history(self):
        """O6: 启动时从 history.jsonl 恢复上轮会话。"""
        records, start = history_mod.load_history()
        if not records:
            return
        for r in records:
            self.records.append(r)
        if start and self._session_start == 0.0:
            self._session_start = start

    def _restore_chart_from_records(self):
        """O6: 把恢复的 records 重新喂进 chart.series。"""
        if not self.records:
            return
        for r in self.records:
            self.chart.push(r)

    # ==================== 界面构建 ====================
    def _btn(self, parent, text, kind, command, width=None, tip=None):
        """按语义类别创建按钮——同色同类，含义全局一致。"""
        b = ttk.Button(parent, text=text, style=STYLE_OF[kind],
                       command=command, width=width)
        if tip:
            _Tooltip(b, f"{tip}\n【{KIND_LABEL[kind]}】")
        return b

    def _build_ui(self):
        # ---------- 第 1 行：监测控制 + 探测参数 ----------
        bar1 = ttk.Frame(self, padding=(10, 8, 10, 0))
        bar1.pack(fill="x", side="top")

        ctl = ttk.LabelFrame(bar1, text="监测控制", padding=(8, 6))
        ctl.pack(side="left")
        self.btn_start = self._btn(ctl, "开始监测", "primary", self.on_start, 10,
                                   "按当前目标与参数开始批量并发探测")
        self.btn_start.pack(side="left", padx=(0, 6))
        self.btn_pause = self._btn(ctl, "暂停", "secondary", self.on_pause, 7,
                                   "暂停/继续探测，已采集数据完整保留")
        self.btn_pause.pack(side="left", padx=(0, 6))
        self.btn_stop = self._btn(ctl, "停止", "danger", self.on_stop, 7,
                                  "结束本轮监测并释放全部探测进程")
        self.btn_stop.pack(side="left")
        self.btn_pause.configure(state="disabled")
        self.btn_stop.configure(state="disabled")

        pf = ttk.LabelFrame(bar1, text="探测参数", padding=(8, 6))
        pf.pack(side="left", padx=(10, 0))
        self.var_interval = tk.StringVar(value=f"{self.params.interval:g}")
        self.var_timeout = tk.StringVar(value=str(self.params.timeout_ms))
        self.var_count = tk.StringVar(value=str(self.params.count))
        self.var_conc = tk.StringVar(value=str(self.params.concurrency))

        self.ent_interval = self._param_entry(pf, "发包间隔", self.var_interval, 6, "秒",
                                              self.on_apply_params)
        self.ent_timeout = self._param_entry(pf, "超时时间", self.var_timeout, 6, "毫秒",
                                             self.on_apply_params)
        self.ent_count = self._param_entry(pf, "包数量", self.var_count, 4, "个/轮",
                                           self.on_apply_params)
        self.ent_conc = self._param_entry(pf, "并发数", self.var_conc, 4, "个",
                                          self.on_apply_params)
        # U5: 取消「应用参数」按钮 —— 参数变更即自动应用（Enter / 失焦 即提交）

        # ---------- 第 2 行：视图切换 + 数据导出 + 主题 ----------
        bar2 = ttk.Frame(self, padding=(10, 6))
        bar2.pack(fill="x", side="top")

        vf = ttk.LabelFrame(bar2, text="结果视图", padding=(8, 4))
        vf.pack(side="left")
        self.var_view = tk.StringVar(value=self.view)
        self.rb_overlay = ttk.Radiobutton(vf, text="单图多线（大图）", value="overlay",
                                          variable=self.var_view,
                                          command=self.on_view_change)
        self.rb_overlay.pack(side="left")
        self.rb_grid = ttk.Radiobutton(vf, text="网格小图", value="grid",
                                       variable=self.var_view,
                                       command=self.on_view_change)
        self.rb_grid.pack(side="left", padx=(10, 0))

        ef = ttk.LabelFrame(bar2, text="数据导出", padding=(8, 4))
        ef.pack(side="left", padx=(10, 0))
        self._btn(ef, "导出日志", "export", lambda: self.on_export("csv"), 10,
                  "导出为 CSV 日志，可直接用 Excel 打开分析").pack(side="left")
        self._btn(ef, "导出 JSON", "export", lambda: self.on_export("json"), 10,
                  "导出为 JSON，供脚本/程序二次分析").pack(side="left", padx=(6, 0))

        # O4: 主题按钮缩为图标 + tooltip
        self.btn_theme = self._btn(bar2, "🌙", "secondary", self.on_toggle_theme, 4,
                                   "在浅色与深色界面之间切换（选择会被记住）")
        self.btn_theme.pack(side="right", padx=(10, 0))

        # ---------- 本机网络信息（固定区域） ----------
        self._build_host_frame()

        # ---------- 主体 ----------
        main = ttk.Frame(self, padding=(10, 8, 10, 0))
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main, width=660)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)

        head = ttk.Frame(left)
        head.pack(fill="x", pady=(0, 6))
        ttk.Label(head, text="监测目标与实时指标",
                  font=("PingFang SC", 11, "bold")).pack(side="left")
        ttk.Label(head, text=f"（上限 {MAX_TARGETS} 个）").pack(side="left", padx=(4, 0))

        tf = ttk.Frame(left)
        tf.pack(fill="x", pady=(0, 6))
        self._btn(tf, "添加", "secondary", self.on_add_target, 7,
                  "新增监测目标（IP 或域名），新目标显示在列表顶部；"
                  "监测中也能添加，下一轮即生效").pack(side="left", padx=(0, 6))
        self._btn(tf, "删除", "danger", self.on_del_target, 7,
                  "移除选中目标（同时清除其曲线），删除前需二次确认").pack(side="left", padx=(0, 6))
        self._btn(tf, "导入 TXT/CSV", "secondary", self.on_import, 13,
                  "从文件批量导入监测目标").pack(side="left", padx=(0, 6))
        self._btn(tf, "清空记录", "danger", self.on_clear_records, 9,
                  "清空已采集的全部监测记录与曲线（不可恢复）").pack(side="left")

        cols = ("name", "ip", "cur", "avg", "min", "max", "loss", "jitter", "time")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=16)
        spec = [("目标", 132, "w"), ("解析IP", 100, "w"), ("当前(ms)", 62, "e"),
                ("平均(ms)", 62, "e"), ("最小(ms)", 62, "e"), ("最大(ms)", 62, "e"),
                ("丢包率", 58, "e"), ("抖动(ms)", 62, "e"), ("最近更新", 64, "center")]
        for (title, w, anc), key in zip(spec, cols):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=w, anchor=anc)
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        # O2: 右键菜单（macOS 习惯用 Control+Click，Windows/Linux 用 Button-3）
        self.tree.bind("<Button-3>", self.on_tree_right_click)
        self.tree.bind("<Control-Button-1>", self.on_tree_right_click)

        right = ttk.Frame(main)
        right.pack(side="left", fill="both", expand=True)
        self.chart = LatencyChart(right, theme_name=self.theme_name, view=self.view)
        # F2: 启动空状态（叠在右侧 chart 之上）
        self._build_empty_state(right)

        # ---------- 状态栏 ----------
        sb = ttk.Frame(self, padding=(10, 4))
        sb.pack(fill="x", side="bottom")
        # U1: 拆成两行：主状态 + 详细
        self.var_status = tk.StringVar(value="就绪")
        # U2: 状态用 tk.Label 以便动态切换背景色
        self.lbl_status = tk.Label(sb, textvariable=self.var_status,
                                   font=("PingFang SC", 10), padx=8, pady=2)
        self.lbl_status.pack(side="left")
        self.var_status_detail = tk.StringVar(value="")
        ttk.Label(sb, textvariable=self.var_status_detail,
                  font=("PingFang SC", 9)).pack(side="left", padx=(12, 0))
        self.var_hint = tk.StringVar(value="")
        ttk.Label(sb, textvariable=self.var_hint).pack(side="right", padx=(10, 0))
        self._build_legend(sb)

        self._refresh_platform_hint()
        self.on_refresh_host()          # 后台线程读取，不阻塞启动

    def _build_legend(self, parent):
        """U4: 折叠为「按钮配色 ▾」下拉，点击展开 4 个色块图例。"""
        t = colors.theme(self.theme_name)
        self._legend_chips = []
        self._legend_expanded = False
        box = ttk.Frame(parent)
        box.pack(side="right")
        ttk.Label(box, text="配色：").pack(side="left")
        self.btn_legend = ttk.Button(box, text="▾", width=3,
                                      command=self._toggle_legend)
        self.btn_legend.pack(side="left", padx=(2, 0))
        # 展开后的图例容器（默认隐藏）
        self._legend_popup = tk.Frame(self, bg=t["panel"],
                                      highlightbackground=t["border"],
                                      highlightthickness=1)
        for kind in ("primary", "secondary", "danger", "export"):
            bg, fg, _ = semantic(self.theme_name, kind)
            chip = tk.Label(self._legend_popup, text="  " + KIND_LABEL[kind].split("（")[0],
                            bg=bg, fg=fg, padx=8, pady=3,
                            font=("PingFang SC", 9))
            chip.pack(anchor="w", padx=8, pady=2)
            self._legend_chips.append((chip, kind))

    def _toggle_legend(self):
        if self._legend_expanded:
            self._legend_popup.place_forget()
            self.btn_legend.configure(text="▾")
            self._legend_expanded = False
        else:
            # 紧贴按钮下方
            x = self.btn_legend.winfo_rootx() - self.winfo_rootx()
            y = self.btn_legend.winfo_rooty() - self.winfo_rooty() + \
                self.btn_legend.winfo_height() + 2
            self._legend_popup.place(x=x, y=y)
            self.btn_legend.configure(text="▴")
            self._legend_expanded = True

    def _build_host_frame(self):
        f = ttk.LabelFrame(self, text="本机网络信息", padding=(10, 6))
        f.pack(fill="x", side="top", padx=10, pady=(6, 0))

        top = ttk.Frame(f)
        top.pack(fill="x")
        self.var_computer = tk.StringVar(value=f"计算机名：{hostinfo.UNKNOWN}")
        self.var_localip = tk.StringVar(value=f"本机 IP：{hostinfo.UNKNOWN}")
        self.var_localmac = tk.StringVar(value=f"MAC 地址：{hostinfo.UNKNOWN}")
        self.var_iface = tk.StringVar(value=f"主网卡：{hostinfo.UNKNOWN}")
        for v in (self.var_computer, self.var_localip,
                  self.var_localmac, self.var_iface):
            ttk.Label(top, textvariable=v,
                      font=("PingFang SC", 10)).pack(side="left", padx=(0, 20))
        self.var_host_state = tk.StringVar(value="正在读取网卡信息…")
        ttk.Label(top, textvariable=self.var_host_state).pack(side="left")
        self._btn(top, "刷新", "secondary", self.on_refresh_host, 6,
                  "重新读取本机网卡（切换网络/VPN 后使用）").pack(side="right")

        wrap = ttk.Frame(f)
        wrap.pack(fill="x", pady=(6, 0))
        cols = ("iface", "ip", "mac", "state")
        self.nic_tree = ttk.Treeview(wrap, columns=cols, show="headings", height=4)
        spec = [("网卡", 130, "w"), ("IPv4 地址", 130, "w"),
                ("MAC 地址", 150, "w"), ("状态", 86, "center")]
        for (title, w, anc), key in zip(spec, cols):
            self.nic_tree.heading(key, text=title)
            self.nic_tree.column(key, width=w, anchor=anc)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.nic_tree.yview)
        self.nic_tree.configure(yscrollcommand=vsb.set)
        self.nic_tree.pack(side="left", fill="x", expand=True)
        vsb.pack(side="right", fill="y")

    def _param_entry(self, parent, label, var, width, unit, on_submit=None):
        """U5: 参数输入框；Enter 或失焦即自动提交。"""
        f = ttk.Frame(parent)
        f.pack(side="left", padx=(0, 6))
        ttk.Label(f, text=label).pack(side="left")
        e = ttk.Entry(f, textvariable=var, width=width, justify="center")
        e.pack(side="left", padx=(3, 0))
        ttk.Label(f, text=unit).pack(side="left", padx=(2, 0))
        if on_submit:
            e.bind("<Return>", lambda _e, c=on_submit: (c(), "break")[1])
            e.bind("<FocusOut>", lambda _e: on_submit())
        return e

    def _build_empty_state(self, parent):
        """F2: 零目标时的引导插画。"""
        t = colors.theme(self.theme_name)
        self._empty_state = tk.Frame(parent, bg=t["bg"])
        tk.Label(self._empty_state, text="📡", bg=t["bg"], fg=t["muted"],
                 font=("PingFang SC", 48)).pack(pady=(40, 8))
        tk.Label(self._empty_state, text="还没有监测目标", bg=t["bg"], fg=t["fg"],
                 font=("PingFang SC", 16, "bold")).pack()
        tk.Label(self._empty_state, text="三步开始：① 添加目标  ② 设置参数  ③ 开始监测",
                 bg=t["bg"], fg=t["muted"],
                 font=("PingFang SC", 11)).pack(pady=(4, 16))
        ttk.Button(self._empty_state, text="添加第一个目标",
                   style="Primary.TButton",
                   command=self.on_add_target).pack()
        tk.Label(self._empty_state,
                 text="支持 IP（10.0.0.1）或域名（www.baidu.com），可后续批量导入 TXT/CSV。",
                 bg=t["bg"], fg=t["muted"],
                 font=("PingFang SC", 9)).pack(pady=(10, 0))

    def _refresh_empty_state(self):
        """F2: 根据目标数显示/隐藏空状态。"""
        if not hasattr(self, "_empty_state") or self._empty_state is None:
            return
        if not self.targets and not self.records:
            # 用 place 覆盖在 chart 区域上方
            self._empty_state.place(relx=0.5, rely=0.5, anchor="center",
                                    relwidth=1.0, relheight=1.0)
            self._empty_state.lift()
        else:
            self._empty_state.place_forget()

    def on_tree_right_click(self, event):
        """O2: 目标行右键菜单。"""
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
        sel = self.tree.selection()
        if not sel:
            return
        host = sel[0]
        # 解析 IP（树里 "—"，所以从 records 取最近一次 resolved_ip）
        ip = "—"
        for r in reversed(list(self.records)):
            if r.target == host and r.resolved_ip:
                ip = r.resolved_ip
                break
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="修改目标…", command=self.on_edit_target)
        menu.add_separator()
        menu.add_command(label=f"复制主机：{host}",
                         command=lambda h=host: self._copy_to_clipboard(h))
        menu.add_command(label=f"复制 IP：{ip}" if ip != "—" else "复制 IP：（尚未解析）",
                         command=lambda i=ip: self._copy_to_clipboard(i))
        menu.add_separator()
        is_hidden = host in self.chart.hidden
        menu.add_command(label=("显示曲线" if is_hidden else "隐藏曲线"),
                         command=lambda h=host: self.chart.toggle_series(h))
        menu.add_separator()
        menu.add_command(label="删除目标…", foreground="#B72E22",
                         command=self.on_del_target)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_to_clipboard(self, text: str):
        if not text or text == "—":
            return
        self.clipboard_clear()
        self.clipboard_append(text)

    def _refresh_platform_hint(self):
        if IS_WIN and not supports_custom_interval():
            self.ent_interval.configure(state="disabled")
            self.var_hint.set("Windows 系统 ping 不支持自定义包间隔，固定 1 秒/包")
        else:
            self.var_hint.set("")
        # O4: 主题按钮只用图标
        self.btn_theme.configure(text="🌙" if self.theme_name == "light" else "☀")

    # ==================== 本机网络信息 ====================
    def on_refresh_host(self):
        """F3: 异步读取本机信息，5s/10s 文字变化避免误判为卡死。"""
        if self._host_pending:
            return
        self._host_pending = True
        import time as _time
        self._host_collect_at = _time.time()
        self.var_host_state.set("正在读取网卡信息…")
        threading.Thread(target=self._collect_host_worker, daemon=True).start()
        # 5s/10s 进度提示
        self.after(5000, self._check_host_progress)
        self.after(10000, self._check_host_progress)

    def _check_host_progress(self):
        if not self._host_pending:
            return
        import time as _time
        elapsed = _time.time() - self._host_collect_at
        if elapsed >= 10:
            self.var_host_state.set("仍在读取（>10s）… 可手动点击「刷新」重试")
        elif elapsed >= 5:
            self.var_host_state.set("读取较慢（>5s）… 通常是 Windows 首次扫描慢")

    def _collect_host_worker(self):
        try:
            info = hostinfo.collect()
        except Exception as exc:  # noqa: BLE001  ——采集失败也必须回传占位
            info = {"computer": "", "primary_ip": "", "nics": [],
                    "error": f"{type(exc).__name__}: {exc}"}
        self._host_q.put(info)

    def _apply_host_info(self, info: dict):
        self._host_pending = False
        self.host_info = info
        s = hostinfo.summarize(info)
        self.var_computer.set(f"计算机名：{s['computer']}")
        self.var_localip.set(f"本机 IP：{s['ip']}")
        self.var_localmac.set(f"MAC 地址：{s['mac']}")
        self.var_iface.set(f"主网卡：{s['iface']}")

        for iid in self.nic_tree.get_children():
            self.nic_tree.delete(iid)
        nics = list(info.get("nics") or [])
        # 主网卡置顶，其次已连接，其余保持原有顺序
        nics.sort(key=lambda n: (not n.get("primary"), not n.get("up")))
        tags = colors.theme(self.theme_name)
        if not nics:
            self.nic_tree.insert("", "end", values=(
                hostinfo.UNKNOWN, "—", "—", "读取失败"))
            self.var_host_state.set(
                f"⚠ {info.get('error') or '未能读取网卡信息'}")
        else:
            for n in nics:
                state = "主网卡" if n.get("primary") else (
                    "已连接" if n.get("up") else "未连接")
                self.nic_tree.insert(
                    "", "end",
                    values=(n["name"], n["ipv4"] or "—", n["mac"] or "—", state),
                    tags=("primary",) if n.get("primary") else ())
            primary = next((n for n in nics if n.get("primary")), None)
            self.var_host_state.set(
                "" if primary else "⚠ 未识别到主网卡（可能已离线）")

    # ==================== 主题 ====================
    def _apply_theme(self):
        t = colors.theme(self.theme_name)
        self.configure(bg=t["bg"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")     # clam 在 Tk 8.6 / 9.0 上行为一致
        except tk.TclError:
            pass
        style.configure(".", background=t["bg"], foreground=t["fg"],
                        fieldbackground=t["panel"], bordercolor=t["border"],
                        troughcolor=t["bg"], font=("PingFang SC", 10))
        style.configure("TFrame", background=t["bg"])
        style.configure("TLabel", background=t["bg"], foreground=t["fg"])
        style.configure("TButton", background=t["panel"], foreground=t["fg"],
                        bordercolor=t["border"], padding=(8, 4))
        style.map("TButton", background=[("active", t["sel"]), ("disabled", t["bg"])])
        # 语义化按钮：同类同色，禁用态统一灰色
        dbg, dfg = disabled_colors(self.theme_name)
        for kind, style_name in STYLE_OF.items():
            bg, fg, active = semantic(self.theme_name, kind)
            style.configure(style_name, background=bg, foreground=fg,
                            bordercolor=bg, lightcolor=bg, darkcolor=bg,
                            padding=(8, 4), font=("PingFang SC", 10))
            style.map(style_name,
                      background=[("disabled", dbg), ("active", active),
                                  ("pressed", active)],
                      foreground=[("disabled", dfg), ("active", fg),
                                  ("pressed", fg)])
        style.configure("TLabelframe", background=t["bg"], bordercolor=t["border"])
        style.configure("TLabelframe.Label", background=t["bg"], foreground=t["muted"],
                        font=("PingFang SC", 9))
        style.configure("TRadiobutton", background=t["bg"], foreground=t["fg"])
        style.map("TRadiobutton", background=[("active", t["sel"])])
        style.configure("TEntry", fieldbackground=t["panel"], foreground=t["fg"],
                        insertcolor=t["fg"])
        style.configure("Treeview", background=t["panel"], foreground=t["fg"],
                        fieldbackground=t["panel"], bordercolor=t["border"])
        style.configure("Treeview.Heading", background=t["panel"], foreground=t["fg"],
                        bordercolor=t["border"])
        style.map("Treeview", background=[("selected", t["sel"])],
                  foreground=[("selected", t["fg"])])
        style.configure("TScrollbar", background=t["panel"], troughcolor=t["bg"],
                        bordercolor=t["border"])
        style.configure("TSeparator", background=t["border"])
        self.tree.tag_configure("bad", foreground=t["bad"])
        self.tree.tag_configure("warn", foreground=t["warn"])
        self.tree.tag_configure("ok", foreground=t["fg"])
        self.chart.set_theme(self.theme_name)
        self._refresh_platform_hint()
        # 图例与本机信息区跟随主题（U4：折叠图例的色块）
        if getattr(self, "_legend_chips", None):
            for chip, kind in self._legend_chips:
                bg, fg, _ = semantic(self.theme_name, kind)
                chip.configure(bg=bg, fg=fg)
        if getattr(self, "_legend_popup", None):
            t = colors.theme(self.theme_name)
            self._legend_popup.configure(bg=t["panel"])
        if getattr(self, "nic_tree", None):
            self.nic_tree.tag_configure("primary", foreground=t["accent"])

    def on_toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        persistence.save_theme(self.theme_name)
        self._apply_theme()

    # ==================== 目标管理 ====================
    def _sync_target_tree(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._rows = {}
        for t in self.targets:
            iid = t.host
            self._rows[iid] = t
            self.tree.insert("", "end", iid=iid,
                             values=(t.name, "—", "—", "—", "—", "—", "—", "—", "—"))
        # F2: 同步空状态可见性
        if hasattr(self, "_refresh_empty_state"):
            self._refresh_empty_state()

    def on_add_target(self):
        dlg = _TargetDialog(self, self.theme_name)
        self.wait_window(dlg.top)
        if not dlg.result:
            return
        host, name = dlg.result
        self._add_target(host, name)

    def _add_target(self, host: str, name: str):
        """添加目标的实质逻辑（与弹窗解耦，便于导入复用与自动化测试）。"""
        host = (host or "").strip()
        name = (name or "").strip()
        if not host:
            return False
        if any(t.host == host for t in self.targets):
            messagebox.showwarning("重复目标", f"目标 {host} 已在列表中。")
            return False
        if len(self.targets) >= MAX_TARGETS:
            messagebox.showwarning("已达上限", f"最多支持 {MAX_TARGETS} 个监测目标。")
            return False
        # 新目标插入列表顶部：最新添加的排在最前，自上而下即为添加顺序的倒序
        self.targets.insert(0, TargetConfig(name=name or host, host=host))
        persistence.save_targets(self.targets)
        self._sync_target_tree()
        self.tree.selection_set(host)
        self.tree.see(host)
        self._update_status()
        self._refresh_empty_state()
        if self.scheduler:
            # 运行中热添加：新目标立即提交探测循环，下一轮即纳入监测
            self.scheduler.set_targets(self.targets)
            if self.scheduler.running:
                self._show_toast(f"已添加 {name or host}，下一轮起纳入监测")
        return True

    def on_edit_target(self):
        """右键「修改目标」：复用同一个对话框，进入编辑模式。"""
        host = self._selected_host()
        if not host:
            messagebox.showinfo("提示", "请先在左侧列表中选择要修改的目标。")
            return
        target = next((t for t in self.targets if t.host == host), None)
        if target is None:
            return
        dlg = _TargetDialog(self, self.theme_name, title="修改监测目标",
                            initial=(target.host, target.name), ok_text="保存")
        self.wait_window(dlg.top)
        if not dlg.result:
            return
        new_host, new_name = dlg.result
        self._apply_target_edit(target, new_host, new_name or new_host)

    def _apply_target_edit(self, target, new_host: str, new_name: str):
        """保存修改；地址变化时迁移曲线、记录与调度器中的键。"""
        old_host = target.host
        new_host = (new_host or "").strip()
        if not new_host:
            return False
        if new_host != old_host and any(t.host == new_host for t in self.targets):
            messagebox.showwarning("重复目标", f"目标 {new_host} 已在列表中。")
            return False
        if new_host != old_host:
            # 地址变了：曲线、图例、历史记录、撤销快照都要跟着换 key
            self._migrate_target_data(old_host, new_host)
        # 就地修改：保持该目标在列表中的原有位置，不因修改而跳位
        target.host = new_host
        target.name = new_name
        persistence.save_targets(self.targets)
        self.chart._legend_sig = None
        self.chart._grid_sig = None
        self._sync_target_tree()
        self.tree.selection_set(new_host)
        self.tree.see(new_host)
        self.chart.redraw()
        self._update_status()
        if self.scheduler:
            self.scheduler.set_targets(self.targets)
        return True

    def _selected_host(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _migrate_target_data(self, old_host: str, new_host: str):
        """修改目标地址后，把曲线、图例、记录与撤销快照迁移到新 key。"""
        c = self.chart
        if old_host in c.series:
            c.series[new_host] = c.series.pop(old_host)
        if old_host in c.meta:
            c.meta[new_host] = c.meta.pop(old_host)
        if old_host in c.order:
            c.order[c.order.index(old_host)] = new_host
        if old_host in c.hidden:
            c.hidden.discard(old_host)
            c.hidden.add(new_host)
        for r in self.records:
            if r.target == old_host:
                r.target = new_host
                r.display = new_host if r.display == old_host else r.display
        for ev in self._undo_stack:
            if old_host in ev.get("series", {}):
                ev["series"][new_host] = ev["series"].pop(old_host)
            for t in ev.get("targets", []):
                if t.host == old_host:
                    t.host = new_host

    def on_del_target(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在左侧列表中选择要删除的目标。")
            return
        # F1: 收集要删除的目标与其关联的 chart series / records，供撤销恢复
        positions = [(i, t) for i, t in enumerate(self.targets) if t.host in sel]
        removed = [t for _, t in positions]
        if not removed:
            return
        # 先确认后删除：标准 messagebox 的按钮文字固定为「确定/取消」，
        # 达不到「确认删除 / 取消」的要求，故改用自定义对话框。
        names = "、".join(t.name or t.host for t in removed)
        if len(names) > 56:
            names = names[:53] + "…"
        if not self._confirm(
                "确认删除目标",
                f"即将删除 {len(removed)} 个监测目标：\n\n{names}\n\n"
                f"其历史曲线与已采集记录会一并清除。\n"
                f"（删除后 10 秒内可通过底部「撤销」恢复）"):
            return
        series_snapshot = {}
        for host in sel:
            if host in self.chart.series:
                series_snapshot[host] = list(self.chart.series[host])
        records_snapshot = [r for r in self.records if r.target in sel]
        # 执行删除
        self.targets = [t for t in self.targets if t.host not in sel]
        for host in sel:
            self.chart.series.pop(host, None)
            self.chart.meta.pop(host, None)
            if host in self.chart.order:
                self.chart.order.remove(host)
        # 同步 records
        self.records = deque(
            (r for r in self.records if r.target not in sel),
            maxlen=_EXPORT_MAX)
        persistence.save_targets(self.targets)
        self.chart._legend_sig = None
        self.chart._grid_sig = None
        self._sync_target_tree()
        self.chart.redraw()
        self._update_status()
        if self.scheduler:
            self.scheduler.set_targets(self.targets)
        # 压入撤销栈
        self._undo_stack.append({
            "targets": removed,
            "indexes": [i for i, _ in positions],   # 撤销时按原位置还原
            "series": series_snapshot,
            "records": records_snapshot,
        })
        if len(self._undo_stack) > self._undo_max:
            self._undo_stack.pop(0)
        # 显示 Toast
        self._show_toast(
            f"已删除 {len(removed)} 个目标",
            action_text="撤销",
            on_action=lambda ev=self._undo_stack[-1]: self._undo_delete(ev),
        )
        self._refresh_empty_state()

    def _undo_delete(self, event):
        """F1: 恢复被误删的目标、曲线与记录（按删除前的列表位置还原）。"""
        existing = {x.host for x in self.targets}
        indexes = event.get("indexes") or []
        for i, t in enumerate(event["targets"]):
            if t.host in existing:
                continue
            pos = indexes[i] if i < len(indexes) else len(self.targets)
            self.targets.insert(max(0, min(pos, len(self.targets))), t)
            existing.add(t.host)
        # 恢复 chart 序列（追加到原位置）
        for host, series_list in event["series"].items():
            from collections import deque as _dq
            self.chart.series[host] = _dq(series_list, maxlen=MAX_POINTS)
            if host not in self.chart.order:
                self.chart.order.append(host)
            if host not in self.chart.meta:
                idx = len(self.chart.meta)
                self.chart.meta[host] = {
                    "display": next((t.name for t in self.targets if t.host == host), host),
                    "idx": idx,
                }
        # 恢复 records
        for r in event["records"]:
            self.records.append(r)
        persistence.save_targets(self.targets)
        self.chart._legend_sig = None
        self.chart._grid_sig = None
        self._sync_target_tree()
        self.chart.redraw()
        self._update_status()
        if self.scheduler:
            self.scheduler.set_targets(self.targets)
        self._refresh_empty_state()

    def _confirm(self, title, message, ok_text="确认删除",
                 cancel_text="取消", ok_kind="danger") -> bool:
        """删除等危险操作的二次确认入口。

        独立成方法是为了可测试：自动化测试可覆盖本方法直接返回 True/False，
        避免自定义模态框在无人点击时把测试卡死。
        """
        return _ask_confirm(self, self.theme_name, title, message,
                            ok_text, cancel_text, ok_kind)

    def _show_toast(self, message, action_text=None, on_action=None):
        """F1: 显示底部 Toast；同时间只保留一个。"""
        if self._current_toast is not None:
            try:
                self._current_toast.dismiss()
            except Exception:  # noqa: BLE001
                pass
        self._current_toast = toast_mod.Toast(
            self, message,
            action_text=action_text, on_action=on_action, timeout_ms=10000,
        )

    def on_import(self):
        path = filedialog.askopenfilename(
            title="导入监测目标",
            filetypes=[("目标清单", "*.txt *.csv"), ("文本文件", "*.txt"),
                       ("CSV 文件", "*.csv"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            imported = persistence.import_targets(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导入失败", str(exc))
            return
        if not imported:
            messagebox.showwarning("无有效目标",
                                   "文件中未识别到有效的 IP 或域名。\n"
                                   "支持格式：每行一个地址，或「地址,显示名」。")
            return
        exist = {t.host for t in self.targets}
        fresh = []
        for t in imported:
            if t.host in exist:
                continue
            if len(self.targets) + len(fresh) >= MAX_TARGETS:
                break
            fresh.append(t)
            exist.add(t.host)
        added = len(fresh)
        # 与手动添加一致：新导入的目标置顶，且保持文件中的先后顺序
        self.targets = fresh + self.targets
        persistence.save_targets(self.targets)
        self._sync_target_tree()
        self._update_status()
        self._refresh_empty_state()
        if self.scheduler:
            self.scheduler.set_targets(self.targets)
        messagebox.showinfo("导入完成", f"成功导入 {added} 个目标（文件共 {len(imported)} 个）。")

    def on_clear_records(self):
        if not self.records:
            return
        if not messagebox.askyesno("确认", f"将清空内存中 {len(self.records)} 条监测记录与全部曲线，继续？"):
            return
        self.records.clear()
        self.chart.clear()
        self._sync_target_tree()
        self._update_status()
        self._refresh_empty_state()

    # ==================== 监测控制 ====================
    def _read_params(self) -> PingParams:
        try:
            p = PingParams(
                interval=float(self.var_interval.get()),
                timeout_ms=int(float(self.var_timeout.get())),
                count=int(float(self.var_count.get())),
                concurrency=int(float(self.var_conc.get())),
            )
        except (TypeError, ValueError):
            messagebox.showwarning("参数无效", "参数必须为数字，已恢复为默认值。")
            p = default_params()
        p.clamp()
        self.var_interval.set(f"{p.interval:g}")
        self.var_timeout.set(str(p.timeout_ms))
        self.var_count.set(str(p.count))
        self.var_conc.set(str(p.concurrency))
        return p

    def on_apply_params(self):
        p = self._read_params()
        self.params = p
        persistence.save_params(p)
        if self.scheduler:
            self.scheduler.set_params(p)
            if p.concurrency != (self.scheduler._executor._max_workers if self.scheduler._executor else p.concurrency):
                self._set_status_hint("并发数已保存，将在下次启动时生效")
        self._update_status()

    def on_start(self):
        if self.scheduler and self.scheduler.running:
            return
        if not self.targets:
            messagebox.showinfo("提示", "请先添加至少一个监测目标。")
            return
        self.params = self._read_params()
        persistence.save_params(self.params)
        import time as _time
        self._session_start = _time.time()   # O5: 记录本轮开始时间
        self.scheduler = ProbeScheduler(self.params, self.targets, self.q.put)
        try:
            self.scheduler.start()
        except ValueError as exc:
            messagebox.showwarning("无法启动", str(exc))
            self.scheduler = None
            return
        self.btn_start.configure(state="disabled")
        self.btn_pause.configure(state="normal")
        self.btn_stop.configure(state="normal")
        self._set_status_hint("")
        self._update_status()
        self.chart.set_paused(False)
        self._refresh_empty_state()

    def on_pause(self):
        if not self.scheduler:
            return
        paused = not self.scheduler.paused
        self.scheduler.set_paused(paused)
        self.btn_pause.configure(text="继续" if paused else "暂停")
        # F5: 通知 chart 冻结/解冻 X 轴
        self.chart.set_paused(paused)
        self._update_status()

    def on_stop(self):
        if self.scheduler:
            self.scheduler.stop()
            self.scheduler = None
        self.btn_start.configure(state="normal")
        self.btn_pause.configure(state="disabled", text="暂停")
        self.btn_stop.configure(state="disabled")
        self._update_status()

    def on_view_change(self):
        """切换视图并记住选择：下次启动监测时沿用（首次默认网格小图）。"""
        view = self.var_view.get()
        self.view = view
        persistence.save_view(view)
        self.chart.set_view(view)

    # ==================== 导出 ====================
    def on_export(self, kind: str):
        if not self.records:
            messagebox.showinfo("无可导出数据", "当前没有监测记录，请先开始监测。")
            return
        # O5: 优先使用监测会话开始时间命名
        default = persistence.default_export_name(kind, self._session_start or None)
        if kind == "csv":
            title, ftype, fmt_note = "导出监测日志", [("日志文件 (*.csv)", "*.csv")], "CSV 日志"
        else:
            title, ftype, fmt_note = "导出监测日志（JSON）", [("JSON 文件", "*.json")], "JSON 数据"
        path = filedialog.asksaveasfilename(
            title=title,
            initialdir=export_dir(),
            initialfile=os.path.basename(default),
            defaultextension="." + kind,
            filetypes=ftype)
        if not path:
            return
        # F7: 携带本机信息与会话开始时间
        host_summary = {
            "computer": self.host_info.get("computer", ""),
            "primary_ip": self.host_info.get("primary_ip", ""),
            "interfaces": [n.get("name") for n in self.host_info.get("nics", [])],
        }
        try:
            out = (persistence.export_csv(self.records, path, self._session_start or None)
                   if kind == "csv"
                   else persistence.export_json(self.records, path, host_summary,
                                                self._session_start or None))
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        messagebox.showinfo("导出完成",
                            f"已导出 {len(self.records)} 条监测日志（{fmt_note}）：\n{out}")

    # ==================== 轮询与刷新 ====================
    def _poll(self):
        # 本机信息：后台线程结果回主线程后再更新 Tk 控件
        try:
            self._apply_host_info(self._host_q.get_nowait())
        except queue.Empty:
            pass
        dirty = False
        while True:
            try:
                res = self.q.get_nowait()
            except queue.Empty:
                break
            self.records.append(res)
            self.chart.push(res)
            self._update_row(res)
            dirty = True
        if dirty:
            now = time.monotonic()
            if now - self._last_draw >= 0.25:      # 重绘节流 4fps
                self.chart.redraw()
                self._last_draw = now
            self._update_status()
        self.after(100, self._poll)

    def _update_row(self, res):
        iid = res.target
        if not self.tree.exists(iid):
            return
        tag = "bad" if res.loss_rate >= 0.2 else ("warn" if res.loss_rate > 0 else "ok")
        self.tree.item(iid, values=(
            res.display or res.target,
            res.resolved_ip or "—",
            "—" if res.avg_rtt is None else f"{res.avg_rtt:.1f}",
            "—" if res.avg_rtt is None else f"{res.avg_rtt:.1f}",
            "—" if res.min_rtt is None else f"{res.min_rtt:.1f}",
            "—" if res.max_rtt is None else f"{res.max_rtt:.1f}",
            f"{res.loss_rate:.0%}",
            "—" if res.jitter is None else f"{res.jitter:.2f}",
            iso_time(res.timestamp)[11:] or "—",
        ), tags=(tag,))

    def _set_status_hint(self, text):
        self.var_hint.set(text)

    def _update_status(self):
        n = len(self.targets)
        paused = bool(self.scheduler and self.scheduler.running and self.scheduler.paused)
        if self.scheduler and self.scheduler.running:
            state = "已暂停" if paused else "监测中"
        else:
            state = "已停止"
        per = effective_interval(self.params)
        cycle = self.params.count * per
        # U1: 主状态精简；详情行展示细节 + 启动/恢复时间
        self.var_status.set(f"状态：{state}    目标：{n}/{MAX_TARGETS}    记录：{len(self.records)} 条    周期：{cycle:.1f}秒/轮")
        # F4: 目标数接近上限时详情行高亮
        detail = f"内存保留：{MAX_POINTS} 点/目标    启动：{iso_time(self._session_start)[11:] or '—'}"
        self.var_status_detail.set(detail)
        # U2 / F4: 状态色
        t = colors.theme(self.theme_name)
        if paused:
            bg, fg = t["warn"], "#1F2328"
        elif n >= MAX_TARGETS:
            bg, fg = t["bad"], "#FFFFFF"
        elif n >= MAX_TARGETS - 2:
            bg, fg = "#FFE7C2", "#1F2328"   # 接近上限：浅橙
        else:
            bg, fg = t["panel"], t["fg"]
        try:
            self.lbl_status.configure(bg=bg, fg=fg)
        except tk.TclError:
            pass

    # ==================== 关闭 ====================
    def _on_close(self):
        # O6: 关闭时把当前 records 落盘，供下次启动恢复
        if self.records:
            history_mod.save_history(self.records, self._session_start or None)
        if self.scheduler:
            self.scheduler.stop(wait=False)
            self.scheduler = None
        persistence.save_targets(self.targets)
        persistence.save_params(self.params)
        persistence.save_theme(self.theme_name)
        self.destroy()


class _Tooltip:
    """轻量悬停提示：说明按钮作用，并标注其语义配色类别。"""

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide)

    def _show(self, _event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        name = self.theme_name()
        t = colors.theme(name)
        tk.Label(tw, text=self.text, justify="left", bg=t["panel"], fg=t["fg"],
                 relief="solid", borderwidth=1, padx=8, pady=5,
                 font=("PingFang SC", 9), wraplength=280).pack()

    def theme_name(self):
        top = self.widget.winfo_toplevel()
        return getattr(top, "theme_name", "light") or "light"

    def _hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class _ConfirmDialog:
    """通用确认对话框。

    为什么不用 messagebox：标准 messagebox 的按钮文字固定为「确定/取消」，
    无法按要求给出「确认删除」。
    为什么不用 tk.Button：macOS/Aqua 会忽略 bg 但保留 fg，
    白色文字压在浅色系统按钮上肉眼就是「空白按钮」；统一改用 ttk + 语义样式。
    """

    def __init__(self, parent, theme_name, title, message,
                 ok_text="确认删除", cancel_text="取消", ok_kind="danger"):
        t = colors.theme(theme_name)
        self.ok = False
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.configure(bg=t["bg"])
        self.top.transient(parent)
        self.top.grab_set()
        self.top.resizable(False, False)
        self.top.geometry("+%d+%d" % (parent.winfo_rootx() + 240,
                                      parent.winfo_rooty() + 200))

        frm = tk.Frame(self.top, bg=t["bg"], padx=18, pady=16)
        frm.pack(fill="both", expand=True)
        tk.Label(frm, text=message, bg=t["bg"], fg=t["fg"], justify="left",
                 font=("PingFang SC", 10),
                 wraplength=380).pack(anchor="w", pady=(0, 14))

        bf = tk.Frame(frm, bg=t["bg"])
        bf.pack(anchor="e")
        ttk.Button(bf, text=cancel_text, width=10,
                   style=STYLE_OF["secondary"],
                   command=self._cancel).pack(side="right", padx=(8, 0))
        ttk.Button(bf, text=ok_text, width=10,
                   style=STYLE_OF[ok_kind],
                   command=self._ok).pack(side="right")

        # 危险操作：不绑定 <Return>，避免误敲回车直接执行
        self.top.bind("<Escape>", lambda e: self._cancel())
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self.top.focus_set()

    def _ok(self):
        self.ok = True
        self.top.destroy()

    def _cancel(self):
        self.ok = False
        self.top.destroy()


def _ask_confirm(parent, theme_name, title, message,
                 ok_text="确认删除", cancel_text="取消", ok_kind="danger") -> bool:
    dlg = _ConfirmDialog(parent, theme_name, title, message,
                         ok_text, cancel_text, ok_kind)
    parent.wait_window(dlg.top)
    return dlg.ok


class _TargetDialog:
    """添加 / 修改目标对话框：地址 + 可选显示名。

    按钮一律用 ttk + 语义样式——此前用 tk.Button(bg=主色, fg=白色) 时，
    macOS(Aqua) 主题下 bg 被系统忽略、fg 仍然生效，
    于是白色文字压在浅灰系统按钮上，看起来就是「确认按钮空白」。
    """

    def __init__(self, parent, theme_name, title="添加监测目标",
                 initial=None, ok_text="确定"):
        t = colors.theme(theme_name)
        self.result = None
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.configure(bg=t["bg"])
        self.top.transient(parent)
        self.top.grab_set()
        self.top.resizable(False, False)
        self.top.geometry("+%d+%d" % (parent.winfo_rootx() + 260, parent.winfo_rooty() + 200))

        frm = tk.Frame(self.top, bg=t["bg"], padx=16, pady=14)
        frm.pack(fill="both", expand=True)
        tk.Label(frm, text="IP 地址或域名 *", bg=t["bg"], fg=t["fg"],
                 font=("PingFang SC", 10)).grid(row=0, column=0, sticky="w")
        self.e_host = tk.Entry(frm, width=32, bg=t["panel"], fg=t["fg"],
                               insertbackground=t["fg"], relief="solid")
        self.e_host.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 8))
        tk.Label(frm, text="显示名（可选，如「北京核心交换机」）", bg=t["bg"], fg=t["fg"],
                 font=("PingFang SC", 10)).grid(row=2, column=0, sticky="w")
        self.e_name = tk.Entry(frm, width=32, bg=t["panel"], fg=t["fg"],
                               insertbackground=t["fg"], relief="solid")
        self.e_name.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(3, 12))

        bf = tk.Frame(frm, bg=t["bg"])
        bf.grid(row=4, column=0, columnspan=2, sticky="e")
        ttk.Button(bf, text="取消", width=9, style=STYLE_OF["secondary"],
                   command=self._cancel).pack(side="right", padx=(8, 0))
        ttk.Button(bf, text=ok_text, width=9, style=STYLE_OF["primary"],
                   command=self._ok).pack(side="right")

        if initial:
            self.e_host.insert(0, initial[0] or "")
            self.e_name.insert(0, initial[1] or "")
            self.e_host.select_range(0, "end")

        self.e_host.bind("<Return>", lambda e: self._ok())
        self.e_name.bind("<Return>", lambda e: self._ok())
        self.top.bind("<Escape>", lambda e: self._cancel())
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self.e_host.focus_set()

    def _ok(self):
        host = self.e_host.get().strip()
        if not host:
            return
        self.result = (host, self.e_name.get().strip())
        self.top.destroy()

    def _cancel(self):
        self.result = None
        self.top.destroy()
