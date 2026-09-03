# -*- coding: utf-8 -*-
"""配色与主题（零第三方依赖，colorsys 生成，确定性可复现）。

20 条曲线无法做到完美色盲友好，故：
  1. 前 8 个目标强制使用 Okabe-Ito 色盲安全色；
  2. 每 5 条轮换一种虚线样式；
  3. 网格小图（每目标独立小图 + 数值标签）作为色盲友好的主视图。
"""
import colorsys


def _palette(s, v, n=20):
    return [
        "#%02X%02X%02X" % tuple(int(c * 255) for c in colorsys.hsv_to_rgb(i / n, s, v))
        for i in range(n)
    ]


OKABE_ITO = ["#000000", "#E69F00", "#56B4E9", "#009E73",
             "#F0E442", "#0072B2", "#D55E00", "#CC79A7"]

LIGHT_SERIES = _palette(0.62, 0.62)
DARK_SERIES = _palette(0.85, 0.90)

# 每 5 条曲线轮换一种线型，提升可辨识度
DASH_STYLES = [(), (4, 2), (8, 3, 2, 3), (2, 4), (6, 2)]

THEMES = {
    "light": {
        "bg": "#F4F5F7",
        "panel": "#FFFFFF",
        "fg": "#1F2328",
        "muted": "#6B7280",
        "border": "#DDE1E6",
        "grid": "#E6E9ED",
        "axis": "#98A1AB",
        "canvas_bg": "#FFFFFF",
        "accent": "#2563EB",
        "accent_fg": "#FFFFFF",
        "ok": "#009E73",
        "warn": "#C47F00",
        "bad": "#D55E00",
        "sel": "#DCE7FB",
    },
    "dark": {
        "bg": "#1C1F24",
        "panel": "#23272E",
        "fg": "#E6E8EB",
        "muted": "#98A1AB",
        "border": "#333A43",
        "grid": "#2E343D",
        "axis": "#5A636D",
        "canvas_bg": "#191C21",
        "accent": "#4FA8E0",
        "accent_fg": "#0E1116",
        "ok": "#4FD3A8",
        "warn": "#F5B85C",
        "bad": "#F57E33",
        "sel": "#2C3A4A",
    },
}


def theme(name: str) -> dict:
    return THEMES.get(name, THEMES["light"])


def color_for(idx: int, theme_name: str = "light") -> str:
    """按序号取曲线颜色：前 8 个使用色盲安全的 Okabe-Ito 配色。"""
    if idx < len(OKABE_ITO):
        base = OKABE_ITO[idx]
        if theme_name == "dark" and base == "#000000":
            return "#E6E8EB"      # 纯黑在深底不可见，替换为浅灰
        return base
    return (LIGHT_SERIES if theme_name == "light" else DARK_SERIES)[idx]


def dash_for(idx: int):
    return DASH_STYLES[idx % len(DASH_STYLES)]


def loss_color(rate: float, theme_name: str = "light") -> str:
    t = theme(theme_name)
    if rate <= 0:
        return t["ok"]
    if rate < 0.2:
        return t["warn"]
    return t["bad"]
