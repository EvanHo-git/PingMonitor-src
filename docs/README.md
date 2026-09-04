# PingMonitor —— 批量 Ping 网络质量监测工具

面向网络管理员的批量 Ping 探测与实时质量监测桌面工具。零第三方运行时依赖，双击即运行。

- **macOS 版**：`PingMonitor.app`
- **Windows 版**：`PingMonitor.exe`
---

## 一、功能

| # | 功能 | 说明 |
|---|------|------|
| 1 | 批量并发探测 | 支持最多 **20 个** 域名 / IP 目标并发 Ping |
| 2 | 参数可调 | 发包间隔、超时时间、包数量、并发数可在界面设置 |
| 3 | 实时趋势图 | 两种视图可切换：**单图多线叠加**（横向对比）+ **网格小图**（每目标一张迷你趋势图） |
| 4 | 关键指标记录 | 每次探测记录：时间戳、目标、平均/最大/最小时延、丢包率、抖动 |
| 5 | 结构化导出 | 完整监测日志一键导出 **CSV + JSON** 双格式 |
| 6 | 目标管理 | 手动添加 / TXT/CSV 批量导入 / 清单与参数持久化（下次打开自动恢复） |
| 7 | 主题切换 | 浅色为主 + 可切换深色，选择持久化 |

---

## 二、指标口径定义

| 指标 | 口径 | 说明 |
|------|------|------|
| 时延（min/avg/max） | 逐包 `time=` 解析求极值与均值 | 禁用 `-q`（安静模式），否则逐包时延被吞、抖动算不出 |
| **丢包率** | `(1 − 收到包数 / 发出包数) × 100%`，本地自算 | 不解析系统 summary 行，规避 macOS/Linux/Windows 中英文输出差异 |
| **抖动** | 相邻两次 RTT 差的绝对值取平均 | 工程常用口径，比标准差更敏感于瞬时毛刺 |
| `time<1ms`（Windows） | 记为 `0.5ms` | Windows 在时延 <1ms 时输出 `time<1ms`（小于号而非等号），需特殊解析 |

> 口径在导出 CSV 的 `metrics_note` 列与 JSON 的 `metrics_note` 字段中同步标注，便于汇报时溯源。

---

## 三、平台能力差异（重要）

| 项 | macOS / Linux | Windows |
|----|---------------|---------|
| 自定义包间隔 | 支持（默认 0.5s） | **不支持，固定 1 秒/包**（Windows 系统 ping 无 `-i` 参数） |
| 单包超时 | `-W` 毫秒（macOS）/ 秒（Linux） | 由 `-w` 毫秒近似 |
| 探测权限 | 普通用户即可 | 普通用户即可（非原始套接字，免管理员） |

> Windows 因包间隔固定 1 秒，20 目标全量刷新约 6 秒；macOS 约 3 秒。界面已对 Windows 用户明确标注该限制。

---

## 四、目录结构

```
ping-monitor/
├── src/
│   ├── main.py            # 入口：--selftest / --version / 启动 GUI
│   ├── models.py          # 数据结构：PingParams / TargetConfig / ProbeResult
│   ├── config.py          # 平台判定、默认参数、用户目录、frozen 下 Tcl/Tk 定位
│   ├── ping_runner.py     # 跨平台 ping 命令构造 + 输出解析（四种格式正则）
│   ├── scheduler.py       # 并发探测调度（线程池 + Queue 回传主线程）
│   ├── persistence.py     # 目标清单/参数/主题持久化 + CSV/JSON 导出
│   ├── selftest.py        # --selftest 无 GUI 自检（CI 产物门禁）
│   ├── colors.py          # 20 目标双主题配色（浅色/深色，色盲友好）
│   ├── chart.py           # Canvas 自绘趋势图（双视图渲染器）
│   └── gui.py             # 主界面（布局/参数/目标管理/导入导出/主题）
├── tests/
│   ├── fixtures_ping.py   # 五类 ping 输出样例（macOS/Linux/Win中/Win英/超时）
│   ├── test_parse.py      # 解析层单元测试（48 项断言）
│   └── test_gui_smoke.py  # GUI 集成测试（启动/探测/导出/主题）
├── build/
│   ├── build_macos.sh     # macOS 打包脚本（.app + ad-hoc 签名 + 自检）
│   ├── build_windows.py   # Windows 打包脚本（--onefile，被 CI 与本机共用）
│   └── requirements-build.txt
├── build_windows.bat      # Windows 本机一键构建（创建 venv → 装依赖 → 构建）
├── .github/workflows/
│   └── build.yml          # Windows 云端构建流水线（产出 exe artifact）
└── docs/
    ├── 首次打开说明.md     # macOS 未签名 .app 首次打开步骤
    └── Windows使用说明.md  # Windows exe 使用与放行说明
```

---
