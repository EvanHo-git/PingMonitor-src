# -*- coding: utf-8 -*-
"""探测调度器：线程池 + 每目标探测循环 + 生命周期管理。

线程安全约束（Tk 单线程）：工作线程只负责跑子进程与解析，
结果通过 on_result 回调交给调用方（GUI 层再入 queue），
**绝不在工作线程触碰任何 Tk 对象**。
"""
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from models import PingParams, TargetConfig
from ping_runner import run_probe


class ProbeScheduler:
    def __init__(self, params: PingParams, targets, on_result, platform: str = None):
        self.params = params
        self.targets = list(targets)
        self.on_result = on_result
        self.platform = platform or sys.platform
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._procs = set()
        self._proc_lock = threading.Lock()
        self._executor = None
        self._futures = []
        self.running = False

    # ---------- 生命周期 ----------
    def start(self):
        if self.running:
            return
        enabled = [t for t in self.targets if t.enabled]
        if not enabled:
            raise ValueError("没有启用的探测目标")
        self._stop.clear()
        self._pause.clear()
        self.running = True
        self._executor = ThreadPoolExecutor(max_workers=self.params.concurrency)
        self._futures = [self._executor.submit(self._loop, t) for t in enabled]

    def stop(self, wait: bool = True):
        self.running = False
        self._pause.clear()
        self._stop.set()
        self._kill_all()
        if self._executor is not None:
            self._executor.shutdown(wait=wait, cancel_futures=True)
            self._executor = None
        self._futures = []

    def set_paused(self, paused: bool):
        if paused:
            self._pause.set()
        else:
            self._pause.clear()

    @property
    def paused(self) -> bool:
        return self._pause.is_set()

    def set_params(self, params: PingParams):
        """运行中改参：下一轮生效，不打断当前轮。"""
        self.params = params

    def set_targets(self, targets):
        self.targets = list(targets)

    # ---------- 内部 ----------
    def _kill_all(self):
        with self._proc_lock:
            procs = list(self._procs)
            self._procs.clear()
        for proc in procs:
            try:
                proc.kill()
            except Exception:
                pass

    def _loop(self, target: TargetConfig):
        idx = 1
        while not self._stop.is_set():
            if self._pause.is_set():
                if self._stop.wait(0.2):
                    break
                continue
            res = run_probe(self.params, target, idx,
                            platform=self.platform, proc_registry=self._procs)
            if self._stop.is_set():
                break
            try:
                self.on_result(res)
            except Exception:
                pass
            idx += 1
            # 轮次之间短暂退让，保证 停止/暂停 可及时响应
            if self._stop.wait(0.05):
                break

    # ---------- 便捷静态方法（供 --selftest 使用） ----------
    @staticmethod
    def quick_probe(host: str, params: PingParams = None, platform: str = None) -> object:
        """同步执行单轮探测，返回 ProbeResult。"""
        from models import TargetConfig
        p = (params or PingParams(count=2, timeout_ms=2000, interval=0.5))
        return run_probe(p, TargetConfig(name=host, host=host), 1, platform=platform)


def wait_briefly(seconds: float):
    time.sleep(seconds)
