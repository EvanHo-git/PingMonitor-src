# -*- coding: utf-8 -*-
"""解析层单元测试（无需 GUI，本机直接运行）。

运行：/Users/evan/.workbuddy/binaries/python/envs/default/bin/python tests/test_parse.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from models import PingParams  # noqa: E402
from ping_runner import build_ping_cmd, parse_ping_output, round_deadline  # noqa: E402
from fixtures_ping import FIXTURES  # noqa: E402

EPS = 1e-6
_passed = 0
_failed = []


def _check(cond, msg):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(msg)


def _close(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) < 1e-4


def test_parse_fixtures():
    for fx in FIXTURES:
        p = PingParams(count=fx["count"])
        res = parse_ping_output(fx["lines"], p, fx["target"], 0, 1700000000.0)
        exp = fx["expect"]
        tag = fx["name"]
        _check(res.received == exp["received"], f"{tag}: received {res.received} != {exp['received']}")
        _check(_close(res.loss_rate, exp["loss_rate"]), f"{tag}: loss {res.loss_rate} != {exp['loss_rate']}")
        _check(_close(res.min_rtt, exp["min_rtt"]), f"{tag}: min {res.min_rtt} != {exp['min_rtt']}")
        _check(_close(res.max_rtt, exp["max_rtt"]), f"{tag}: max {res.max_rtt} != {exp['max_rtt']}")
        _check(_close(res.avg_rtt, exp["avg_rtt"]), f"{tag}: avg {res.avg_rtt} != {exp['avg_rtt']}")
        _check(_close(res.jitter, exp["jitter"]), f"{tag}: jitter {res.jitter} != {exp['jitter']}")
        _check(res.resolved_ip == exp["resolved_ip"], f"{tag}: ip {res.resolved_ip!r} != {exp['resolved_ip']!r}")


def test_cmd_construction():
    p = PingParams(interval=0.5, timeout_ms=2000, count=3, concurrency=10)
    mac = build_ping_cmd("darwin", p, "10.0.0.1")
    _check("-c" in mac and "3" in mac, f"macOS 缺包数: {mac}")
    _check("-W" in mac and "2000" in mac, f"macOS -W 应为毫秒: {mac}")
    _check("-t" in mac, f"macOS 应带 -t 总超时: {mac}")
    _check(mac[-1] == "10.0.0.1", f"macOS 目标应在末尾: {mac}")
    _check("-q" not in mac, "探测命令不得包含 -q（会吞掉逐包时延）")

    win = build_ping_cmd("win32", p, "10.0.0.1")
    _check("-n" in win and "3" in win, f"Windows 缺包数: {win}")
    _check("-w" in win and "2000" in win, f"Windows -w 应为毫秒: {win}")
    _check("-i" not in win, f"Windows 不支持 -i，不应出现: {win}")

    lin = build_ping_cmd("linux", p, "10.0.0.1")
    idx = lin.index("-W")
    _check(lin[idx + 1] == "2.000", f"Linux -W 应为秒: {lin}")


def test_deadline():
    p = PingParams(interval=0.5, timeout_ms=2000, count=3, concurrency=10)
    _check(round_deadline(p, "darwin") >= 3 * 0.5 + 2.0, "macOS deadline 过短")
    # Windows 固定 1s/包，deadline 必须按 1s 算而非用户填的 0.5s
    dw = round_deadline(p, "win32")
    _check(dw >= 3 * 1.0 + 2.0, f"Windows deadline 未按固定 1s 计算: {dw}")


def test_jitter_edge():
    p = PingParams(count=1)
    res = parse_ping_output(["64 bytes from 1.1.1.1: icmp_seq=0 ttl=64 time=5.0 ms"], p,
                            FIXTURES[0]["target"])
    _check(res.jitter is None, "单样本时抖动应为 None")
    _check(res.received == 1 and res.avg_rtt == 5.0, "单样本统计错误")


def run_all() -> bool:
    global _passed, _failed
    _passed, _failed = 0, []
    test_parse_fixtures()
    test_cmd_construction()
    test_deadline()
    test_jitter_edge()
    for f in _failed:
        print("  FAIL:", f)
    return not _failed


if __name__ == "__main__":
    ok = run_all()
    print(f"解析测试：通过 {_passed} 项，失败 {len(_failed)} 项")
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
