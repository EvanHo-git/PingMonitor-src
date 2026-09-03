# -*- coding: utf-8 -*-
"""跨平台 ping 执行与输出解析。

设计要点（均有实测依据）：
1. 禁用 `ping -q`：安静模式会吞掉逐包 `time=` 行，导致抖动与逐包时延无法计算。
2. 不解析 summary 行：中英文/各 locale 格式差异大，丢包率与统计值一律自算。
3. 单位陷阱：macOS `-W` 是毫秒、Linux `-W` 是秒、Windows `-w` 是毫秒，在此层抹平。
4. macOS `-t` 实测为**总超时秒**（非 TTL），用作优雅截止；Windows 无总超时参数，
   由 Python 侧 subprocess 超时强制 kill 兜底。
"""
import re
import subprocess
import sys
import time

from config import effective_interval, supports_custom_interval
from models import PingParams, ProbeResult, TargetConfig, SUB_1MS_VALUE

# 逐包回显：macOS/Linux "time=0.076 ms"；Windows 英文 "time=1ms"/"time<1ms"；
#           Windows 中文 "时间=12ms"/"时间<1ms"
RTT_RE = re.compile(r"(?:time|时间)\s*([=<>])\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)

# 超时 / 不可达行（覆盖中英文）
TIMEOUT_RE = re.compile(
    r"(Request\s+(?:timeout|timed\s+out)|请求\s*超时|"
    r"Destination\s+(?:Host\s+)?Unreachable|无法访问|General\s+failure|"
    r"expired\s+in\s+transit|传输失败)",
    re.IGNORECASE,
)

# 真实 IP：Unix 用 "PING host (ip)"，Windows 用 "[ip]"
HOST_RE_UNIX = re.compile(r"PING\s+\S+\s*\(([^)]+)\)", re.IGNORECASE)
HOST_RE_WIN = re.compile(r"\[([0-9]{1,3}(?:\.[0-9]{1,3}){3}|[0-9A-Fa-f:]{2,})\]")


def build_ping_cmd(platform: str, p: PingParams, host: str) -> list:
    """按平台构造 ping 命令（argv 列表，不使用 shell）。"""
    if platform == "win32":
        # Windows：间隔固定 1s 无法指定；-w 单位毫秒
        return ["ping", "-n", str(p.count), "-w", str(int(p.timeout_ms)), host]

    if platform == "darwin":
        # macOS/BSD：-W 毫秒；-t 总超时秒（实测有效）
        total = int(p.count * effective_interval(p, platform)) + int(p.timeout_ms / 1000) + 3
        return [
            "ping", "-n", "-c", str(p.count),
            "-i", f"{effective_interval(p, platform):.3f}",
            "-W", str(int(p.timeout_ms)),
            "-t", str(total),
            host,
        ]

    # Linux iputils：-W 单位是秒（浮点）
    return [
        "ping", "-n", "-c", str(p.count),
        "-i", f"{effective_interval(p, platform):.3f}",
        "-W", f"{p.timeout_ms / 1000:.3f}",
        host,
    ]


def round_deadline(p: PingParams, platform: str = None) -> float:
    """单轮探测的硬截止（秒）。Windows 无总超时参数，此值是唯一保障。"""
    platform = platform or sys.platform
    iv = effective_interval(p, platform)
    return float(p.count) * iv + (p.timeout_ms / 1000.0) + 2.0


def _decode(data: bytes) -> str:
    """按 UTF-8 → GBK → latin-1 顺序解码 ping 输出。

    Windows 中文环境 ping 输出为 GBK，英文环境为 ASCII；直接按 UTF-8 解码会炸。
    """
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def parse_ping_output(lines, p: PingParams, target: TargetConfig,
                      round_index: int = 0, timestamp: float = None) -> ProbeResult:
    """逐行解析 ping 输出，自算丢包率与统计值（不依赖 summary 行）。"""
    samples = []
    resolved_ip = ""

    for line in lines:
        if not resolved_ip:
            m = HOST_RE_UNIX.search(line)
            if m:
                resolved_ip = m.group(1)
            else:
                m = HOST_RE_WIN.search(line)
                if m:
                    resolved_ip = m.group(1)
        if TIMEOUT_RE.search(line):
            continue
        m = RTT_RE.search(line)
        if m:
            op, raw = m.group(1), float(m.group(2))
            # Windows 在时延 <1ms 时输出 "time<1ms"，按约定取半值
            samples.append(raw / 2.0 if op == "<" else raw)

    sent = int(p.count)
    received = len(samples)
    loss_rate = 0.0 if sent <= 0 else max(0.0, min(1.0, (sent - received) / sent))

    res = ProbeResult(
        target=target.host,
        display=target.name or target.host,
        round_index=round_index,
        timestamp=timestamp if timestamp is not None else time.time(),
        sent=sent,
        received=received,
        loss_rate=loss_rate,
        resolved_ip=resolved_ip,
        samples=samples,
    )
    if received:
        res.min_rtt = min(samples)
        res.max_rtt = max(samples)
        res.avg_rtt = sum(samples) / received
        if received >= 2:
            res.jitter = sum(abs(samples[i] - samples[i - 1]) for i in range(1, received)) / (received - 1)
    return res


def run_probe(p: PingParams, target: TargetConfig, round_index: int = 0,
              platform: str = None, proc_registry=None) -> ProbeResult:
    """执行一轮探测。proc_registry 为可选集合，用于登记 Popen 以便 stop() 时强杀。"""
    platform = platform or sys.platform
    cmd = build_ping_cmd(platform, p, target.host)
    deadline = round_deadline(p, platform)
    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    if platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = None
    try:
        proc = subprocess.Popen(cmd, **kwargs)
        if proc_registry is not None:
            proc_registry.add(proc)
        out_bytes, _ = proc.communicate(timeout=deadline)
        text = _decode(out_bytes or b"")
        if proc_registry is not None:
            proc_registry.discard(proc)
        return parse_ping_output(text.splitlines(), p, target, round_index)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            out_bytes, _ = proc.communicate()
            text = _decode(out_bytes or b"")
        except Exception:
            text = ""
        finally:
            if proc_registry is not None and proc is not None:
                proc_registry.discard(proc)
        res = parse_ping_output(text.splitlines(), p, target, round_index)
        res.success = False
        res.error = f"探测超时（>{deadline:.1f}s）"
        return res
    except FileNotFoundError:
        res = parse_ping_output([], p, target, round_index)
        res.success = False
        res.error = "未找到系统 ping 命令"
        return res
    except Exception as exc:  # noqa: BLE001 - 探测线程不允许抛异常
        res = parse_ping_output([], p, target, round_index)
        res.success = False
        res.error = f"{type(exc).__name__}: {exc}"
        return res
