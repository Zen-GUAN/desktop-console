# 桌面控制台 · PROGRESS.md（项目进度）

> 2026-11-07 按约定建档。项目知识见 [PROJECT.md](PROJECT.md)；完整交接以 `AGENT_HANDOFF.md` 为准（本文件不复制，防双源）。

## 快照（2026-11-07 收工态）

- **v3 独立 exe 完整交付并经用户实测通过**：`v3-exe\dist\桌面控制台.exe`（16.2MB 单文件）
  - 6/6 功能 + 360 式悬浮球（morph 动画 / 亮度滑块 / 面板可拖动 / 把手分区）+ 无边框主窗 + 窗口几何记忆
  - 开机自启（HKCU Run + --minimized 静默启动，托盘「开机自启」可开关）
  - 通用性：WebView2 预检 / .NET 自检 / 换机诊断 selftest 15 项
- **已开源**：github.com/Zen-GUAN/desktop-console（MIT，Release v3.0.0 带 exe）
- 回归资产：`v3-exe\verify_ball_settings.py`（16 项）+ `v3-exe\verify_layout.py`（12 项）+ selftest 15 项

## 已完成（全部验收通过）

1. 亮度「不支持」误报修复（根因：后端 monitors 双层嵌套，见 `docs\修复记录_亮度不支持误报.md`）
2. 悬浮球（morph/滑块/面板拖动/贴边收起）+ 设置页（Kimi 前端，headless 16/16）
3. 布局适配（Kimi 前端，headless 12/12）
4. 通用性加固 + 无边框化 + 关闭竞态修复（KeyError('master')）+ 开机自启

## 剩余（全部可选）

- 代码签名（消除 SmartScreen 提示）
- 悬停 morph 时长/面板配色等手感参数微调（均为常数，反馈即调）
