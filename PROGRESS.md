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

## 新增（2026-11-20）

- **键盘延迟探针 `tools\kbdprobe\`**（按任务书 2026-11-19 交付）：LL 键盘钩子量化「迟滞/粘滞」，单文件纯标准库，无需管理员。隐私红线=只记 VK 数字/时间戳/事件类型/派生指标（源码 grep 自审零调用）。自测 39 项断言 PASS；SendInput 注入端到端实测通过（含长按带 repeat→不算粘滞的分类验证）。踩坑已修：① `CallNextHookEx` 必须显式 argtypes，否则 64 位 l_param 高位为 1 → 回调 OverflowError 返回 0 = 静默拦截按键（红线）；② `GetTickCount64` 需 restype=c_ulonglong（否则开机 >24.8 天截断）；③ powercfg 管道输出编码随控制台 CP 变化，须字节流 utf-8→mbcs 依次解码；④ 本机 USB 选择性暂停 GUID 是 `48e6b7a6-50f5-4782-a5d4-53bb8f07e226`（SUB_USB=2a737441），**当前值=启用**——屋主键盘问题排查第一条线索。判读方法见 `tools\kbdprobe\README.md` §三；真实 60s 采样待屋主自行跑 `run_sampling.ps1`。
