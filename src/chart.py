# -*- coding: utf-8 -*-
"""Canvas 自绘趋势图：单图多线叠加 与 网格小图 两种视图。

性能策略（20 目标 × 长时间运行）：
  - 每目标 deque(maxlen=MAX_POINTS) 环形缓冲，内存恒定；
  - 每帧先 delete('all') 再重画，旧图元交给 Tk 回收，杜绝 create_line 累积泄漏；
  - 按"绘制点数预算"自适应降采样：曲线越多，单条抽得越稀，每像素最多 1 段线；
  - 重绘频率由调用方节流（GUI 层 250ms）。
"""
import tkinter as tk
from tkinter import ttk
from collections import deque

import colors
from models import MAX_POINTS

GRID_COLS = 4
CELL_W = 224
CELL_H = 118
PLOT_H = 70

_FONT = "PingFang SC" if __import__("sys").platform == "darwin" else "Microsoft YaHei"
_MONO = "SF Mono" if __import__("sys").platform == "darwin" else "Consolas"


def _nice_max(v: float) -> float:
    """把 Y 轴上界取整到 1/2/5 × 10^n，避免刻度数字乱码。"""
    if v <= 0:
        return 10.0
    import math
    exp = math.floor(math.log10(v))
    base = 10 ** exp
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * base:
            return m * base
    return 10 * base


class LatencyChart:
    def __init__(self, parent, theme_name: str = "light", view: str = "overlay"):
        self.parent = parent
        self.theme_name = theme_name
        self.view = view
        self.series = {}          # host -> deque([(ts, avg_or_None, loss_rate)])
        self.meta = {}            # host -> {'display': str, 'idx': int}
        self.order = []
        self.hidden = set()
        self.cells = {}           # 网格视图：host -> (canvas, info_label)
        self.container = ttk.Frame(parent)
        self.container.pack(fill="both", expand=True)
        self._legend_sig = None
        self._grid_sig = None
        self._build()

    # ---------------- 公共接口 ----------------
    @property
    def widget(self):
        return self.container

    def push(self, res):
        host = res.target
        if host not in self.series:
            self.series[host] = deque(maxlen=MAX_POINTS)
            self.meta[host] = {"display": res.display or host, "idx": len(self.order)}
            self.order.append(host)
        self.meta[host]["display"] = res.display or host
        self.series[host].append((res.timestamp, res.avg_rtt, res.loss_rate))

    def clear(self):
        self.series.clear()
        self.meta.clear()
        self.order.clear()
        self._legend_sig = None
        self._grid_sig = None
        self.redraw()

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        self._legend_sig = None
        self._grid_sig = None
        self._build()

    def set_view(self, view: str):
        if view == self.view:
            return
        self.view = view
        self._legend_sig = None
        self._grid_sig = None
        self._build()

    def toggle_series(self, host: str):
        if host in self.hidden:
            self.hidden.discard(host)
        else:
            self.hidden.add(host)
        self._refresh_legend_style()
        self.redraw()

    def visible_hosts(self):
        return [h for h in self.order if h not in self.hidden]

    # ---------------- 构建 ----------------
    def _build(self):
        for child in self.container.winfo_children():
            child.destroy()
        self.cells = {}
        t = colors.theme(self.theme_name)
        if self.view == "grid":
            self._build_grid(t)
        else:
            self._build_overlay(t)
        self.redraw()

    def _build_overlay(self, t):
        self.canvas = tk.Canvas(self.container, highlightthickness=0,
                                bg=t["canvas_bg"], height=340)
        self.canvas.pack(fill="both", expand=True, padx=(8, 8), pady=(8, 0))
        self.legend = tk.Frame(self.container, bg=t["bg"])
        self.legend.pack(fill="x", padx=8, pady=(4, 8))
        self.legend_items = {}

    def _build_grid(self, t):
        outer = tk.Frame(self.container, bg=t["bg"])
        outer.pack(fill="both", expand=True)
        self._scroll = tk.Canvas(outer, highlightthickness=0, bg=t["bg"])
        self._sb = ttk.Scrollbar(outer, orient="vertical", command=self._scroll.yview)
        self._inner = tk.Frame(self._scroll, bg=t["bg"])
        self._win_id = self._scroll.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>", self._on_inner_configure)
        self._scroll.bind("<Configure>", self._on_scroll_configure)
        self._scroll.configure(yscrollcommand=self._sb.set)
        self._scroll.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        self._sb.pack(side="right", fill="y", pady=8, padx=(0, 8))

    def _on_inner_configure(self, event=None):
        self._scroll.configure(scrollregion=self._scroll.bbox("all"))

    def _on_scroll_configure(self, event=None):
        if hasattr(self, "_win_id"):
            self._scroll.itemconfigure(self._win_id, width=event.width)

    # ---------------- 重绘 ----------------
    def redraw(self):
        if self.view == "grid":
            self._draw_grid()
        else:
            self._ensure_legend()
            self._draw_overlay()

    def _draw_overlay(self):
        c = self.canvas
        t = colors.theme(self.theme_name)
        c.delete("all")
        w = max(240, c.winfo_width())
        h = max(140, c.winfo_height())
        ml, mr, mt, mb = 64, 18, 16, 30
        pw, ph = w - ml - mr, h - mt - mb
        c.configure(bg=t["canvas_bg"])
        if pw <= 20 or ph <= 20:
            return

        visible = [(h_, self.series[h_]) for h_ in self.order
                   if h_ not in self.hidden and self.series.get(h_)]
        if not visible:
            c.create_text(w / 2, h / 2, text="点击「开始监测」后显示时延趋势",
                          fill=t["muted"], font=(_FONT, 12))
            return

        t0 = min(pts[0][0] for _, pts in visible)
        t1 = max(pts[-1][0] for _, pts in visible)
        if t1 - t0 < 1.0:
            t1 = t0 + 1.0
        vals = [v for _, pts in visible for (_, v, _) in pts if v is not None]
        ymax = _nice_max(max(10.0, (max(vals) if vals else 10.0) * 1.25))

        def X(ts):
            return ml + (ts - t0) / (t1 - t0) * pw

        def Y(v):
            return mt + ph - (min(v, ymax) / ymax) * ph

        # 横向网格 + Y 轴刻度
        for i in range(5):
            yv = ymax * i / 4
            y = Y(yv)
            c.create_line(ml, y, ml + pw, y, fill=t["grid"])
            c.create_text(ml - 8, y, text=f"{yv:.0f}", anchor="e",
                          fill=t["axis"], font=(_MONO, 9))
        c.create_text(ml - 8, mt - 6, text="ms", anchor="e",
                      fill=t["axis"], font=(_MONO, 8))

        # 纵向网格 + 时间刻度
        import time as _time
        for i in range(6):
            x = ml + pw * i / 5
            c.create_line(x, mt, x, mt + ph, fill=t["grid"])
            ts = t0 + (t1 - t0) * i / 5
            c.create_text(x, mt + ph + 12,
                          text=_time.strftime("%H:%M:%S", _time.localtime(ts)),
                          fill=t["axis"], font=(_MONO, 9))
        c.create_rectangle(ml, mt, ml + pw, mt + ph, outline=t["border"])

        # 自适应降采样：曲线越多，单条抽得越稀
        n_series = max(1, len(visible))
        per_series = max(60, min(900, 6000 // n_series))

        for host, pts in visible:
            idx = self.meta[host]["idx"]
            col = colors.color_for(idx, self.theme_name)
            dash = colors.dash_for(idx)
            n = len(pts)
            step = max(1, n // per_series)
            idxs = list(range(0, n, step))
            if idxs[-1] != n - 1:
                idxs.append(n - 1)

            seg, loss_x = [], []
            for i in idxs:
                ts, v, loss = pts[i]
                x = X(ts)
                if v is None:
                    loss_x.append(x)
                else:
                    seg.append(x)
                    seg.append(Y(v))
            if len(seg) >= 4:
                c.create_line(*seg, fill=col, width=1.6, dash=dash, tags="trace")
            # 全丢包的采样点在底部画红竖线
            for x in loss_x:
                c.create_line(x, mt + ph - 5, x, mt + ph, fill=t["bad"], width=2, tags="trace")

    def _ensure_legend(self):
        sig = tuple(self.order)
        if sig == self._legend_sig:
            return
        self._legend_sig = sig
        t = colors.theme(self.theme_name)
        for child in self.legend.winfo_children():
            child.destroy()
        self.legend_items = {}
        if not self.order:
            tk.Label(self.legend, text="尚未添加监测目标", bg=t["bg"], fg=t["muted"],
                     font=(_FONT, 10)).pack(anchor="w")
            return
        wrap = tk.Frame(self.legend, bg=t["bg"])
        wrap.pack(fill="x")
        for i, host in enumerate(self.order):
            item = tk.Frame(wrap, bg=t["bg"])
            item.grid(row=i // 5, column=i % 5, sticky="w", padx=(0, 14), pady=1)
            sw = tk.Label(item, text="    ", bg=colors.color_for(self.meta[host]["idx"], self.theme_name))
            sw.pack(side="left")
            name = self.meta[host]["display"]
            if len(name) > 14:
                name = name[:13] + "…"
            txt = tk.Label(item, text=name, bg=t["bg"], fg=t["fg"], font=(_FONT, 10), cursor="hand2")
            txt.pack(side="left")
            for wgt in (item, sw, txt):
                wgt.bind("<Button-1>", lambda e, h=host: self.toggle_series(h))
            self.legend_items[host] = (sw, txt)
        self._refresh_legend_style()

    def _refresh_legend_style(self):
        t = colors.theme(self.theme_name)
        for host, (sw, txt) in getattr(self, "legend_items", {}).items():
            if host in self.hidden:
                sw.configure(bg=t["border"])
                txt.configure(fg=t["muted"])
            else:
                sw.configure(bg=colors.color_for(self.meta[host]["idx"], self.theme_name))
                txt.configure(fg=t["fg"])

    # ---------------- 网格小图 ----------------
    def _draw_grid(self):
        t = colors.theme(self.theme_name)
        hosts = [h for h in self.order if h in self.series]
        sig = tuple(hosts)
        if sig != self._grid_sig:
            self._grid_sig = sig
            self._rebuild_cells(t, hosts)
        for host in hosts:
            cv, info = self.cells.get(host, (None, None))
            if cv is None:
                continue
            self._draw_cell(host, cv, info, t)

    def _rebuild_cells(self, t, hosts):
        for child in self._inner.winfo_children():
            child.destroy()
        self.cells = {}
        if not hosts:
            tk.Label(self._inner, text="尚未添加监测目标", bg=t["bg"], fg=t["muted"],
                     font=(_FONT, 12)).pack(pady=40)
            self._scroll.configure(scrollregion=self._scroll.bbox("all"))
            return
        for i, host in enumerate(hosts):
            cell = tk.Frame(self._inner, bg=t["panel"], width=CELL_W, height=CELL_H,
                            highlightbackground=t["border"], highlightthickness=1)
            cell.grid(row=i // GRID_COLS, column=i % GRID_COLS, padx=5, pady=5, sticky="nw")
            cell.grid_propagate(False)
            head = tk.Frame(cell, bg=t["panel"])
            head.pack(fill="x", padx=8, pady=(6, 0))
            sw = tk.Label(head, text="  ", bg=colors.color_for(self.meta[host]["idx"], self.theme_name))
            sw.pack(side="left")
            name = self.meta[host]["display"]
            if len(name) > 16:
                name = name[:15] + "…"
            tk.Label(head, text=name, bg=t["panel"], fg=t["fg"],
                     font=(_FONT, 10, "bold"), anchor="w").pack(side="left")
            cv = tk.Canvas(cell, width=CELL_W - 18, height=PLOT_H,
                           bg=t["canvas_bg"], highlightthickness=0)
            cv.pack(padx=8, pady=(4, 0))
            info = tk.Label(cell, text="等待数据…", bg=t["panel"], fg=t["muted"],
                            font=(_MONO, 9), anchor="w")
            info.pack(fill="x", padx=8, pady=(2, 4))
            self.cells[host] = (cv, info)
        self._scroll.configure(scrollregion=self._scroll.bbox("all"))

    def _draw_cell(self, host, cv, info, t):
        pts = self.series.get(host) or []
        cv.delete("all")
        w = CELL_W - 18
        h = PLOT_H
        if not pts:
            return
        vals = [v for (_, v, _) in pts if v is not None]
        ymax = _nice_max(max(5.0, (max(vals) if vals else 5.0) * 1.25))
        n = len(pts)
        step = max(1, n // max(30, w))
        idxs = list(range(0, n, step))
        if idxs[-1] != n - 1:
            idxs.append(n - 1)

        seg, loss_x = [], []
        for i in idxs:
            ts, v, loss = pts[i]
            x = 2 + (i / max(1, n - 1)) * (w - 4)
            if v is None:
                loss_x.append(x)
            else:
                seg.append(x)
                seg.append(h - 4 - (min(v, ymax) / ymax) * (h - 12))
        col = colors.color_for(self.meta[host]["idx"], self.theme_name)
        cv.create_line(2, h - 4, w - 2, h - 4, fill=t["grid"])
        if len(seg) >= 4:
            cv.create_line(*seg, fill=col, width=1.5)
        for x in loss_x:
            cv.create_line(x, h - 8, x, h - 3, fill=t["bad"], width=2)

        last = pts[-1]
        last_v, last_loss = last[1], last[2]
        txt = f"当前 {('--' if last_v is None else f'{last_v:.1f}')} ms   丢包 {last_loss:.0%}   上限 {ymax:.0f} ms"
        info.configure(text=txt, fg=colors.loss_color(last_loss, self.theme_name))
