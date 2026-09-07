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
        # 热添加支持：_started 记录已提交过探测循环的 host；
        # _gen_of 记录每个 host 当前有效的循环代次，代次失效的循环会自行退出
        self._started = set()
        self._gen_of = {}
        self._gen = 0
        self._gen_lock = threading.Lock()

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
        # 线程数必须覆盖全部目标：每个循环长期占用一个线程，
        # 若只按 concurrency 建池，多出的目标会一直排队、永远不被探测。
        workers = max(int(self.params.concurrency), len(enabled))
        self._executor = ThreadPoolExecutor(max_workers=workers)
        self._started = set()
        self._gen_of = {}
        self._futures = []
        for t in enabled:
            self._started.add(t.host)
            self._gen_of[t.host] = self._gen
            self._futures.append(self._executor.submit(self._loop, t, self._gen))

    def stop(self, wait: bool = True):
        self.running = False
        self._pause.clear()
        self._stop.set()
        self._kill_all()
        if self._executor is not None:
            self._executor.shutdown(wait=wait, cancel_futures=True)
            self._executor = None
        self._futures = []
        self._started = set()
        self._gen_of = {}

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
        """更新目标清单（支持运行中热添加）。

        - 新增目标：立即提交探测循环，下一轮即纳入监测；
        - 移除/停用的目标：由 _loop 每轮的 _is_active() 自检退出，不再产生数据；
        - 目标数超过线程池容量时：重建线程池并整体重新提交（旧循环按代次失效退出）。
        """
        self.targets = list(targets)
        if not self.running or self._executor is None:
            return
        enabled = [t for t in self.targets if t.enabled]
        alive = {t.host for t in enabled}
        # 已移除的目标允许重新加入：清掉其"已启动"标记
        self._started &= alive
        added = [t for t in enabled if t.host not in self._started]
        if len(enabled) > self._executor._max_workers:
            self._rebuild_executor(enabled, len(enabled))
            return
        if not added:
            return          # 纯删除：无需提交，靠 _loop 自检回收线程
        for t in added:
            gen = self._next_gen()
            self._started.add(t.host)
            self._gen_of[t.host] = gen
            self._futures.append(self._executor.submit(self._loop, t, gen))

    # ---------- 内部 ----------
    def _next_gen(self) -> int:
        with self._gen_lock:
            self._gen += 1
            return self._gen

    def _rebuild_executor(self, enabled, workers: int):
        """扩容重建线程池：旧池中的循环因代次失效，会在本轮探测后自行退出。"""
        old = self._executor
        self._executor = ThreadPoolExecutor(max_workers=workers)
        self._futures = []
        self._started = set()
        self._gen_of = {}
        for t in enabled:
            gen = self._next_gen()
            self._started.add(t.host)
            self._gen_of[t.host] = gen
            self._futures.append(self._executor.submit(self._loop, t, gen))
        if old is not None:
            # 旧池不再接收新任务；其线程跑完当前探测即退出，不阻塞调用方
            threading.Thread(target=old.shutdown, kwargs={"wait": False},
                             daemon=True).start()
    def _kill_all(self):
        with self._proc_lock:
            procs = list(self._procs)
            self._procs.clear()
        for proc in procs:
            try:
                proc.kill()
            except Exception:
                pass

    def _is_active(self, host: str, gen: int) -> bool:
        """本循环是否仍然有效：代次未变，且目标仍在清单中并处于启用状态。"""
        if self._stop.is_set():
            return False
        if self._gen_of.get(host) != gen:
            return False
        return any(t.host == host and t.enabled for t in self.targets)

    def _loop(self, target: TargetConfig, gen: int):
        host = target.host
        idx = 1
        while not self._stop.is_set():
            if not self._is_active(host, gen):
                break                       # 目标被删除/停用/重建 → 回收线程
            if self._pause.is_set():
                if self._stop.wait(0.2):
                    break
                continue
            res = run_probe(self.params, target, idx,
                            platform=self.platform, proc_registry=self._procs)
            # 探测期间目标可能已被删除或重建：失效循环的结果丢弃，避免脏数据
            if self._stop.is_set() or not self._is_active(host, gen):
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
