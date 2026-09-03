# -*- coding: utf-8 -*-
"""数据模型：探测参数、目标配置、探测结果。

仅依赖标准库，供 core / ui / 打包产物共用。
"""
from dataclasses import dataclass, field, asdict
from typing import Optional, List


MAX_TARGETS = 20
MAX_POINTS = 1800          # 每目标保留的最新采样点数（≈30 分钟 @1.5s/点）
SUB_1MS_VALUE = 0.5        # Windows 输出 time<1ms 时的约定取值（毫秒）


@dataclass
class PingParams:
    interval: float = 0.5      # 发包间隔(秒)：同一轮内相邻包的发送间隔；Windows 固定 1s
    timeout_ms: int = 2000     # 单包等待回复上限(毫秒)
    count: int = 3             # 每轮发包数
    concurrency: int = 10      # 并发探测目标数（线程池大小）

    def clamp(self) -> "PingParams":
        self.interval = min(60.0, max(0.1, float(self.interval)))
        self.timeout_ms = int(min(10000, max(100, int(self.timeout_ms))))
        self.count = int(min(20, max(1, int(self.count))))
        self.concurrency = int(min(MAX_TARGETS, max(1, int(self.concurrency))))
        return self

    def as_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "PingParams":
        p = PingParams()
        if isinstance(d, dict):
            for k in ("interval", "timeout_ms", "count", "concurrency"):
                if k in d:
                    try:
                        setattr(p, k, type(getattr(p, k))(d[k]))
                    except (TypeError, ValueError):
                        pass
        return p.clamp()


@dataclass
class TargetConfig:
    name: str                  # 显示名（如 "北京核心交换机"）
    host: str                  # 主机/IP（如 "10.1.1.1" 或 "www.jd.com"）
    enabled: bool = True

    def as_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TargetConfig":
        return TargetConfig(
            name=str(d.get("name") or d.get("host") or "").strip(),
            host=str(d.get("host") or "").strip(),
            enabled=bool(d.get("enabled", True)),
        )


@dataclass
class ProbeResult:
    target: str                              # TargetConfig.host（聚合键）
    display: str = ""                        # TargetConfig.name（展示用）
    round_index: int = 0                     # 第几轮探测（从 0 递增）
    timestamp: float = 0.0                   # 本轮结束的 epoch 秒
    sent: int = 0                            # 发出包数（= PingParams.count）
    received: int = 0                        # 解析到的回显数
    loss_rate: float = 0.0                   # (sent-received)/sent，0..1
    min_rtt: Optional[float] = None          # ms；received==0 时为 None
    avg_rtt: Optional[float] = None          # ms
    max_rtt: Optional[float] = None          # ms
    jitter: Optional[float] = None           # MAD = mean(|rtt[i]-rtt[i-1]|) ms；样本<2 为 None
    samples: List[float] = field(default_factory=list)   # 原始 RTT 序列(ms)
    resolved_ip: str = ""                    # 从 PING 头行提取的真实 IP
    success: bool = True                     # 进程是否未超时/未异常
    error: str = ""                          # 失败原因（超时、命令缺失等）

    def to_row(self) -> dict:
        """供 CSV / JSON 导出的扁平字典。"""
        d = asdict(self)
        d["samples"] = ";".join(f"{x:.3f}" for x in self.samples)
        d["timestamp_iso"] = iso_time(self.timestamp)
        return d

    @property
    def ok(self) -> bool:
        """本轮是否至少收到一个回包。"""
        return self.received > 0


def iso_time(ts: float) -> str:
    import time as _time
    if not ts:
        return ""
    return _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(ts))
