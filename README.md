# 桌面控制台

> Windows 11 未激活时，「设置 → 个性化」整类被锁。这个项目把那 6 项功能做成一个 380×600 的桌面小浮窗。
> **详细交接见 [AGENT_HANDOFF.md](AGENT_HANDOFF.md)**（含"做成 exe"的路线与取舍）。

## 两个版本 → 三个版本

| 版本 | 形态 | 功能 | 状态 |
|---|---|---|---|
| `v1-winforms\` | PowerShell + WinForms 单文件 49KB | 6/6 全通 | 丑，回退用 |
| `v2-web\` | HTML/CSS/JS + PowerShell 后端 + Edge `--app` | 6/6（亮度 2026-11-07 修复） | **v3 的 UI 源**，可独立跑 |
| `v3-exe\` | Python(pywebview) 宿主 + 内嵌 WebView2，**16.1MB 单 exe** | 6/6（后端全实测） | **现行分发版** |

## 怎么跑

```
双击 v2-web\启动.cmd
```

关窗口即退（**但 2026-11-07 实测这个自愈失效了**，server 进程会残留，见交接文档 §六 bug 2）。

## 独立 exe：已完成

**2026-11-07 按路线 A（Python + pywebview + PyInstaller）打包完成**：[v3-exe\README.md](v3-exe/README.md)
产物 `v3-exe\dist\桌面控制台.exe`（16.1MB 单文件，双击即用，后端 selftest + API + GUI 冒烟全实测通过）。
三条路线的原始论证保留在 [AGENT_HANDOFF.md §5](AGENT_HANDOFF.md)；亮度 bug 根因（后端 JSON 形状）见 [PROJECT.md](PROJECT.md)。
剩两件小事：UI 层肉眼复验一次亮度滑杆；开机自启是否要做待定。

## 目录

```
PROJECT.md     项目专属知识（分工 / bug 根因 / 坑）
docs\          任务书 ×2 + 生成方能力评估 + 修复记录_亮度不支持误报
v1-winforms\   原版（PowerShell + WinForms，9 项测试 PASS）
v2-web\        UI 源 + 可独立跑（ui\ 前端=Kimi 管 + server\ 后端=ZCode 管）
v3-exe\        独立 exe（app.py/winapi.py 后端 + build.py 打包）
tools\         独立亮度工具（不受 v2 的 bug 影响，可直接用）
```