# -*- coding: utf-8 -*-
"""PingMonitor 主界面。

线程模型：探测线程只跑子进程与解析，结果经 queue 回传；
本模块所有 Tk 操作都在主线程完成（Tk 不是线程安全的）。
"""
import os
import queue
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from collections import deque

import colors
from chart import LatencyChart
from config import (IS_WIN, effective_interval, supports_custom_interval,
                    default_params, export_dir)
from models import MAX_POINTS, MAX_TARGETS, PingParams, TargetConfig, iso_time
import persistence
from scheduler import ProbeScheduler

_EXPORT_MAX = MAX_POINTS * MAX_TARGETS   # 内存中保留的探测记录上限


class PingMonitorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.params = persistence.load_params()
        self.targets = persistence.load_targets()
        self.theme_name = persistence.load_theme("light")
        self.records = deque(maxlen=_EXPORT_MAX)
        self.q = queue.Queue()
        self.scheduler = None
        self._last_draw = 0.0
        self._rows = {}

        self.title(f"批量 Ping 网络质量监测工具")
        self.geometry("1340x860")
        self.minsize(1100, 700)

        self._build_ui()
        self._apply_theme()
        self._sync_target_tree()
        self._update_status()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)

    # ==================== 界面构建 ====================
    def _build_ui(self):
        # ---- 工具条 ----
        bar = ttk.Frame(self, padding=(10, 8))
        bar.pack(fill="x", side="top")

        runf = ttk.Frame(bar)
        runf.pack(side="left")
        self.btn_start = ttk.Button(runf, text="开始监测", width=10, command=self.on_start)
        self.btn_start.pack(side="left", padx=(0, 4))
        self.btn_pause = ttk.Button(runf, text="暂停", width=7,
                                    command=self.on_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=(0, 4))
        self.btn_stop = ttk.Button(runf, text="停止", width=7,
                                   command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(0, 12))

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=(0, 10))

        pf = ttk.Frame(bar)
        pf.pack(side="left")
        self.var_interval = tk.StringVar(value=f"{self.params.interval:g}")
        self.var_timeout = tk.StringVar(value=str(self.params.timeout_ms))
        self.var_count = tk.StringVar(value=str(self.params.count))
        self.var_conc = tk.StringVar(value=str(self.params.concurrency))

        self.ent_interval = self._param_entry(pf, "发包间隔", self.var_interval, 6, "秒")
        self.ent_timeout = self._param_entry(pf, "超时时间", self.var_timeout, 6, "毫秒")
        self.ent_count = self._param_entry(pf, "包数量", self.var_count, 4, "个/轮")
        self.ent_conc = self._param_entry(pf, "并发数", self.var_conc, 4, "个")

        ttk.Button(pf, text="应用参数", width=9, command=self.on_apply_params).pack(side="left", padx=(6, 0))

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)

        vf = ttk.Frame(bar)
        vf.pack(side="left")
        ttk.Label(vf, text="视图：").pack(side="left")
        self.var_view = tk.StringVar(value="overlay")
        ttk.Radiobutton(vf, text="单图多线叠加", value="overlay",
                        variable=self.var_view, command=self.on_view_change).pack(side="left")
        ttk.Radiobutton(vf, text="网格小图", value="grid",
                        variable=self.var_view, command=self.on_view_change).pack(side="left")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)

        ef = ttk.Frame(bar)
        ef.pack(side="left")
        ttk.Button(ef, text="导出 CSV", width=10, command=lambda: self.on_export("csv")).pack(side="left", padx=(0, 4))
        ttk.Button(ef, text="导出 JSON", width=10, command=lambda: self.on_export("json")).pack(side="left", padx=(0, 4))
        self.btn_theme = ttk.Button(ef, text="", width=8, command=self.on_toggle_theme)
        self.btn_theme.pack(side="left", padx=(8, 0))

        # ---- 主体 ----
        main = ttk.Frame(self, padding=(10, 0, 10, 6))
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main, width=628)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)

        head = ttk.Frame(left)
        head.pack(fill="x", pady=(0, 6))
        ttk.Label(head, text="监测目标与实时指标", font=("PingFang SC", 11, "bold")).pack(side="left")
        ttk.Label(head, text=f"（上限 {MAX_TARGETS} 个）").pack(side="left", padx=(4, 0))

        tf = ttk.Frame(left)
        tf.pack(fill="x", pady=(0, 6))
        ttk.Button(tf, text="添加", width=7, command=self.on_add_target).pack(side="left", padx=(0, 4))
        ttk.Button(tf, text="删除", width=7, command=self.on_del_target).pack(side="left", padx=(0, 4))
        ttk.Button(tf, text="导入 TXT/CSV", width=13, command=self.on_import).pack(side="left", padx=(0, 4))
        ttk.Button(tf, text="清空记录", width=9, command=self.on_clear_records).pack(side="left")

        cols = ("name", "ip", "cur", "avg", "min", "max", "loss", "jitter", "time")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=26)
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

        right = ttk.Frame(main)
        right.pack(side="left", fill="both", expand=True)
        self.chart = LatencyChart(right, theme_name=self.theme_name, view="overlay")

        # ---- 状态栏 ----
        self.var_status = tk.StringVar(value="就绪")
        sb = ttk.Frame(self, padding=(10, 4))
        sb.pack(fill="x", side="bottom")
        ttk.Label(sb, textvariable=self.var_status).pack(side="left")
        self.var_hint = tk.StringVar(value="")
        ttk.Label(sb, textvariable=self.var_hint).pack(side="right")

        self._refresh_platform_hint()

    def _param_entry(self, parent, label, var, width, unit):
        f = ttk.Frame(parent)
        f.pack(side="left", padx=(0, 6))
        ttk.Label(f, text=label).pack(side="left")
        e = ttk.Entry(f, textvariable=var, width=width, justify="center")
        e.pack(side="left", padx=(3, 0))
        ttk.Label(f, text=unit).pack(side="left", padx=(2, 0))
        return e

    def _refresh_platform_hint(self):
        if IS_WIN and not supports_custom_interval():
            self.ent_interval.configure(state="disabled")
            self.var_hint.set("Windows 系统 ping 不支持自定义包间隔，固定 1 秒/包")
        else:
            self.var_hint.set("")
        self.btn_theme.configure(text="切换深色" if self.theme_name == "light" else "切换浅色")

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

    def on_add_target(self):
        dlg = _TargetDialog(self, self.theme_name)
        self.wait_window(dlg.top)
        if not dlg.result:
            return
        host, name = dlg.result
        if any(t.host == host for t in self.targets):
            messagebox.showwarning("重复目标", f"目标 {host} 已在列表中。")
            return
        if len(self.targets) >= MAX_TARGETS:
            messagebox.showwarning("已达上限", f"最多支持 {MAX_TARGETS} 个监测目标。")
            return
        self.targets.append(TargetConfig(name=name or host, host=host))
        persistence.save_targets(self.targets)
        self._sync_target_tree()
        self._update_status()
        if self.scheduler:
            self.scheduler.set_targets(self.targets)

    def on_del_target(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在左侧列表中选择要删除的目标。")
            return
        for host in sel:
            self.targets = [t for t in self.targets if t.host != host]
            self.chart.series.pop(host, None)
            self.chart.meta.pop(host, None)
            if host in self.chart.order:
                self.chart.order.remove(host)
        persistence.save_targets(self.targets)
        self.chart._legend_sig = None
        self.chart._grid_sig = None
        self._sync_target_tree()
        self.chart.redraw()
        self._update_status()
        if self.scheduler:
            self.scheduler.set_targets(self.targets)

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
        added = 0
        for t in imported:
            if t.host in exist:
                continue
            if len(self.targets) >= MAX_TARGETS:
                break
            self.targets.append(t)
            exist.add(t.host)
            added += 1
        persistence.save_targets(self.targets)
        self._sync_target_tree()
        self._update_status()
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

    def on_pause(self):
        if not self.scheduler:
            return
        paused = not self.scheduler.paused
        self.scheduler.set_paused(paused)
        self.btn_pause.configure(text="继续" if paused else "暂停")
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
        self.chart.set_view(self.var_view.get())

    # ==================== 导出 ====================
    def on_export(self, kind: str):
        if not self.records:
            messagebox.showinfo("无可导出数据", "当前没有监测记录，请先开始监测。")
            return
        default = persistence.default_export_name(kind)
        path = filedialog.asksaveasfilename(
            title="导出监测日志",
            initialdir=export_dir(),
            initialfile=os.path.basename(default),
            defaultextension="." + kind,
            filetypes=[("CSV 文件", "*.csv")] if kind == "csv" else [("JSON 文件", "*.json")])
        if not path:
            return
        try:
            out = (persistence.export_csv(self.records, path) if kind == "csv"
                   else persistence.export_json(self.records, path))
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        messagebox.showinfo("导出完成", f"已导出 {len(self.records)} 条记录：\n{out}")

    # ==================== 轮询与刷新 ====================
    def _poll(self):
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
        if self.scheduler and self.scheduler.running:
            state = "已暂停" if self.scheduler.paused else "监测中"
        else:
            state = "已停止"
        per = effective_interval(self.params)
        cycle = self.params.count * per
        self.var_status.set(
            f"状态：{state}    目标：{n}/{MAX_TARGETS}    记录：{len(self.records)} 条    "
            f"单目标采样周期：约 {cycle:.1f} 秒（Windows 约 {self.params.count * 1.0:.1f} 秒）    "
            f"内存保留：{MAX_POINTS} 点/目标")

    # ==================== 关闭 ====================
    def _on_close(self):
        if self.scheduler:
            self.scheduler.stop(wait=False)
            self.scheduler = None
        persistence.save_targets(self.targets)
        persistence.save_params(self.params)
        persistence.save_theme(self.theme_name)
        self.destroy()


class _TargetDialog:
    """添加目标对话框：地址 + 可选显示名。"""

    def __init__(self, parent, theme_name):
        t = colors.theme(theme_name)
        self.result = None
        self.top = tk.Toplevel(parent)
        self.top.title("添加监测目标")
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
        tk.Button(bf, text="取消", width=8, bg=t["panel"], fg=t["fg"],
                  relief="solid", command=self._cancel).pack(side="right", padx=(8, 0))
        tk.Button(bf, text="确定", width=8, bg=t["accent"], fg=t["accent_fg"],
                  relief="solid", command=self._ok).pack(side="right")

        self.e_host.bind("<Return>", lambda e: self._ok())
        self.e_name.bind("<Return>", lambda e: self._ok())
        self.top.bind("<Escape>", lambda e: self._cancel())
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
