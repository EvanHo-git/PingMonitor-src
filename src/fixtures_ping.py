# -*- coding: utf-8 -*-
"""跨平台 ping 输出样本与期望值（解析层测试与打包后自检共用）。

放在 src/ 下而非 tests/ 下，是因为 PyInstaller 不会打包 tests/，
而 --selftest 自检必须能在打包产物内部验证解析逻辑。
"""
from models import TargetConfig

T = TargetConfig(name="测试目标", host="testhost")


def _t(host, name=""):
    return TargetConfig(name=name or host, host=host)


FIXTURES = [
    {
        "name": "macOS 回环成功（本机实测原文）",
        "platform": "darwin",
        "count": 3,
        "target": _t("127.0.0.1", "回环"),
        "lines": [
            "PING 127.0.0.1 (127.0.0.1): 56 data bytes",
            "64 bytes from 127.0.0.1: icmp_seq=0 ttl=64 time=0.076 ms",
            "64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time=0.185 ms",
            "64 bytes from 127.0.0.1: icmp_seq=2 ttl=64 time=0.269 ms",
            "",
            "--- 127.0.0.1 ping statistics ---",
            "3 packets transmitted, 3 packets received, 0.0% packet loss",
            "round-trip min/avg/max/stddev = 0.076/0.177/0.269/0.079 ms",
        ],
        "expect": {
            "received": 3, "loss_rate": 0.0, "min_rtt": 0.076, "max_rtt": 0.269,
            "avg_rtt": (0.076 + 0.185 + 0.269) / 3,
            "jitter": (0.109 + 0.084) / 2,
            "resolved_ip": "127.0.0.1",
        },
    },
    {
        "name": "macOS 不可达全丢包（本机实测原文）",
        "platform": "darwin",
        "count": 3,
        "target": _t("10.255.255.1", "黑洞地址"),
        "lines": [
            "PING 10.255.255.1 (10.255.255.1): 56 data bytes",
            "Request timeout for icmp_seq 0",
            "Request timeout for icmp_seq 1",
            "",
            "--- 10.255.255.1 ping statistics ---",
            "3 packets transmitted, 0 packets received, 100.0% packet loss",
        ],
        "expect": {
            "received": 0, "loss_rate": 1.0, "min_rtt": None, "max_rtt": None,
            "avg_rtt": None, "jitter": None, "resolved_ip": "10.255.255.1",
        },
    },
    {
        "name": "Linux iputils 正常",
        "platform": "linux",
        "count": 3,
        "target": _t("10.0.0.1", "汇聚交换机"),
        "lines": [
            "PING 10.0.0.1 (10.0.0.1) 56(84) bytes of data.",
            "64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=12.3 ms",
            "64 bytes from 10.0.0.1: icmp_seq=2 ttl=64 time=8.1 ms",
            "64 bytes from 10.0.0.1: icmp_seq=3 ttl=64 time=20.5 ms",
            "",
            "--- 10.0.0.1 ping statistics ---",
            "3 packets transmitted, 3 received, 0% packet loss, time 2003ms",
            "rtt min/avg/max/mdev = 8.100/13.633/20.500/5.100 ms",
        ],
        "expect": {
            "received": 3, "loss_rate": 0.0, "min_rtt": 8.1, "max_rtt": 20.5,
            "avg_rtt": (12.3 + 8.1 + 20.5) / 3,
            "jitter": (4.2 + 12.4) / 2,
            "resolved_ip": "10.0.0.1",
        },
    },
    {
        "name": "Windows 中文（含 time<1ms 与请求超时）",
        "platform": "win32",
        "count": 4,
        "target": _t("www.jd.com", "京东门户"),
        "lines": [
            "",
            "正在 Ping www.jd.com [27.36.125.193] 具有 32 字节的数据:",
            "来自 27.36.125.193 的回复: 字节=32 时间=12ms TTL=51",
            "来自 27.36.125.193 的回复: 字节=32 时间<1ms TTL=51",
            "请求超时。",
            "来自 27.36.125.193 的回复: 字节=32 时间=15ms TTL=51",
            "",
            "27.36.125.193 的 Ping 统计信息:",
            "    数据包: 已发送 = 4，已接收 = 3，丢失 = 1 (25% 丢失)，",
            "往返行程的估计时间(以毫秒为单位):",
            "    最短 = 0ms，最长 = 15ms，平均 = 9ms",
        ],
        "expect": {
            "received": 3, "loss_rate": 0.25, "min_rtt": 0.5, "max_rtt": 15.0,
            "avg_rtt": (12.0 + 0.5 + 15.0) / 3,
            "jitter": (11.5 + 14.5) / 2,
            "resolved_ip": "27.36.125.193",
        },
    },
    {
        "name": "Windows 英文全丢包",
        "platform": "win32",
        "count": 4,
        "target": _t("10.255.255.1", "黑洞地址"),
        "lines": [
            "",
            "Pinging 10.255.255.1 with 32 bytes of data:",
            "Request timed out.",
            "Request timed out.",
            "Request timed out.",
            "Request timed out.",
            "",
            "Ping statistics for 10.255.255.1:",
            "    Packets: Sent = 4, Received = 0, Lost = 4 (100% loss)",
        ],
        "expect": {
            "received": 0, "loss_rate": 1.0, "min_rtt": None, "max_rtt": None,
            "avg_rtt": None, "jitter": None, "resolved_ip": "",
        },
    },
]
