# -*- coding: utf-8 -*-
"""持久化：目标清单 / 探测参数 / 主题 读写，以及 CSV、JSON 导出与目标导入。

配置文件一律写入操作系统约定的用户目录，frozen 模式下绝不写进 .app 包内。
"""
import csv
import json
import os
import time

from config import export_dir, user_data_dir
from models import PingParams, ProbeResult, TargetConfig, MAX_TARGETS

CSV_HEADER = [
    "时间戳", "目标", "显示名", "解析IP", "轮次", "发出", "收到", "丢包率",
    "最小时延(ms)", "平均时延(ms)", "最大时延(ms)", "抖动(ms)", "逐包时延(ms)", "状态",
]

CSV_NOTE = (
    "口径说明：丢包率=(发出-收到)/发出（自算，不解析系统 summary）；"
    "抖动=相邻 RTT 差绝对值均值(MAD)；Windows 输出 time<1ms 时按 0.5ms 计。"
)


# ---------------- 目标清单 ----------------
def targets_path() -> str:
    return os.path.join(user_data_dir(), "targets.json")


def params_path() -> str:
    return os.path.join(user_data_dir(), "params.json")


def theme_path() -> str:
    return os.path.join(user_data_dir(), "theme.json")


def load_targets():
    path = targets_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        t = TargetConfig.from_dict(item)
        if t.host:
            out.append(t)
        if len(out) >= MAX_TARGETS:
            break
    return out


def save_targets(targets):
    _atomic_write(targets_path(),
                  json.dumps([t.as_dict() for t in targets], ensure_ascii=False, indent=2))


def load_params() -> PingParams:
    path = params_path()
    if not os.path.isfile(path):
        return PingParams()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return PingParams.from_dict(json.load(f))
    except (OSError, ValueError):
        return PingParams()


def save_params(p: PingParams):
    _atomic_write(params_path(), json.dumps(p.as_dict(), ensure_ascii=False, indent=2))


def load_theme(default: str = "light") -> str:
    path = theme_path()
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            v = json.load(f).get("theme", default)
        return v if v in ("light", "dark") else default
    except (OSError, ValueError):
        return default


def save_theme(theme: str):
    _atomic_write(theme_path(), json.dumps({"theme": theme}, ensure_ascii=False, indent=2))


def _atomic_write(path: str, text: str):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# ---------------- 目标导入 ----------------
def import_targets(path: str):
    """从 TXT / CSV 导入目标。

    支持格式：
      - 每行一个 host
      - "host,显示名" 或 "显示名,host"（自动识别含 IP/域名的那一列）
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    ext = os.path.splitext(path)[1].lower()
    out, seen = [], set()
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        if ext == ".csv":
            rows = list(csv.reader(f))
        else:
            rows = [line.strip().split(",") for line in f if line.strip()]
    for row in rows:
        row = [c.strip() for c in row if c.strip()]
        if not row:
            continue
        host, name = None, None
        for cell in row:
            if _looks_like_host(cell):
                host = cell
            elif name is None:
                name = cell
        if not host:
            continue
        if host in seen:
            continue
        seen.add(host)
        out.append(TargetConfig(name=name or host, host=host))
        if len(out) >= MAX_TARGETS:
            break
    return out


def _looks_like_host(s: str) -> bool:
    if not s or len(s) > 253:
        return False
    if " " in s or "/" in s:
        return False
    # IPv4
    parts = s.split(".")
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return True
    # 域名（含中文域名不处理，仅接受 ASCII 域名）
    if "." in s and all(c.isalnum() or c in ".-_" for c in s):
        return True
    return s.lower() in ("localhost", "::1")


# ---------------- 导出 ----------------
def default_export_name(ext: str) -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(export_dir(), f"ping_{stamp}.{ext}")


def _fmt(v):
    return "" if v is None else f"{v:.3f}"


def export_csv(results, path: str = None) -> str:
    path = path or default_export_name("csv")
    # utf-8-sig：Excel 打开中文不乱码
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["# " + CSV_NOTE])
        w.writerow(CSV_HEADER)
        for r in results:
            w.writerow([
                __import__("models").iso_time(r.timestamp), r.target, r.display, r.resolved_ip,
                r.round_index, r.sent, r.received, f"{r.loss_rate:.4f}",
                _fmt(r.min_rtt), _fmt(r.avg_rtt), _fmt(r.max_rtt), _fmt(r.jitter),
                ";".join(f"{x:.3f}" for x in r.samples),
                "正常" if r.ok else ("失败:" + r.error if r.error else "全丢包"),
            ])
    return path


def export_json(results, path: str = None) -> str:
    path = path or default_export_name("json")
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "metrics_note": CSV_NOTE,
        "count": len(results),
        "records": [r.to_row() for r in results],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
