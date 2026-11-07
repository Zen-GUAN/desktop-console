# 桌面控制台 · PROJECT.md（项目专属知识）

> 2026-11-07 建立。原计划自 `~/.agents/AGENTS.md` 迁入，**经查该文件没有本项目内容**（通篇是 NAS/VPS/Netlify/代理等通用基建），故本文件内容来自工作区 `D:\workspace\AGENTS.md` 项目名录小节 + 本日取证沉淀；原小节已瘦身为指针。
> 分层：通用基建 → 各 AGENTS.md；运行与交接全貌 → `AGENT_HANDOFF.md`；本文件 → 本项目专属的分工、根因结论、坑。

## 分工（2026-11-07 用户定，长期有效）

- **ZCode 只负责后端**：`v2-web\server\Server.ps1`、`v2-web\server\Native.ps1`、`tools\`、日后的打包宿主
- **前端归 Kimi**：`v2-web\ui\index.html / style.css / app.js`（由 Kimi-K3.1-Pro 生成，历史评估见 `docs\评估_Kimi前端能力.md`）
- ZCode **不直接改前端文件**；需要前端改动时 → 钉死后端证据 + 写任务书交 Kimi

## 这是什么

Windows 11 未激活，「设置 → 个性化」整类被锁 → 把 6 项功能（深浅色/强调色/任务栏对齐/壁纸/一键还原/亮度 DDC-CI）重做成 380×600 桌面小浮窗。

- `v1-winforms\`：PowerShell + WinForms 单文件，6/6 全通但丑（回退方案 + P/Invoke 参考实现）
- `v2-web\`：HTML/CSS/JS 前端 + PowerShell HttpListener 后端 + Edge `--app` 窗口（**v3 的 UI 源**）
- `v3-exe\`：**独立 exe（2026-11-07 产出，现行分发版）**——Python(pywebview) 宿主 + 内嵌 WebView2，16.1MB 单文件，后端 selftest/API/GUI 冒烟全实测通过，见 `v3-exe\README.md`。同日新增 **360 式悬浮球**（ctypes 分层窗口，拖动/贴边收起/悬停弹出，功能经托盘子菜单自选，配置 `%APPDATA%\WinCtlPanel2\ball.json`，球线程 PMv2 全物理像素避 DPI 虚拟化坑）

## 亮度「不支持」误报 —— 根因与修复（2026-11-07，已推翻旧结论）

- **旧结论错误**：此前记录「后端正常、是前端问题、别查后端（白花两轮）」。真相：后端**取值**确实正常（`GetMonitorBrightness ok=True cur=23`），但 **JSON 形状坏了**——还是后端 bug。
- **根因**：`Server.ps1` 的 `Get-MonitorState` 结尾 `return ,$result`（逗号包裹防展开）+ 调用方 `@(Get-MonitorState)` → `/api/state` 输出 `"monitors":[[{…}]]` 双层嵌套。前端 `selectMonitor` 取 `state.monitors[0]` 拿到的是**内层数组**，`.supported` 为 undefined（falsy）→ 忠实渲染出「不支持 / 此显示器未开放 DDC/CI」（app.js:107-112，前端无错）。
- **修复**（后端三处 + 验证）：`return ,$result` → `return $result`；同类隐患 `Get-PhysMonitors` 失败路径 `return ,$list` → `return $list`；SelfTest 增加「状态 JSON 形状」回归项；修掉「无外链资源」检查对 SVG xmlns 的误报。详见 `docs\修复记录_亮度不支持误报.md`。
- **验证**：修复后 `/api/state` 实测 `"monitors":[{…supported:true, percent:23…}]` 平铺 ✓；SelfTest 17/18（唯一 FAIL「零句柄返回 null」是本机驱动连句柄 0 都接受的硬件特例，非缺陷）。
- **前端零改动**。UI 层复验（开面板拖滑杆）待下次打开面板时确认。

## 本项目坑（跨会话必读）

- **API 校验**：所有 `/api/*` 需 `X-WinCtl-Token`（GET `/` 从 `meta[name=winctl-token]` 提取）；Origin 头**有才校验** → curl 带 token 可直接调，不必开浏览器。
- **无窗口调试姿势**：`-SelfTest`（跑自检即退出）、`-NoBrowser -Port N`（不起 Edge）。
- **杀服务别信 netstat 的 PID**：HttpListener 走 http.sys，LISTENING 显示 PID=4（内核）——要用 CIM 按命令行找真实 powershell 进程再杀。
- **单实例互斥体** `Local\WinCtlPanel2_SingleInstance`：起服务前确认无旧实例（冲突会弹 MessageBox 挂住脚本）。
- **编码红线**：`.ps1` UTF-8 带 BOM；`.cmd` 纯 ASCII（工作区 AGENTS.md 技术红线 1/8）。
- 窗口关闭后 server 进程不退（bug#2，UiTimer 的 `HasExited` 检测失效）——打包成同进程后自然消失，现形态暂不修。
- `tools\brightness.ps1` 独立可用，不受任何版本 bug 影响。
- **球线程坐标纪律**：`WM_MOUSEMOVE` 的 lparam 是**客户区坐标**（相对球左上角），拖动定位必须用 `GetCursorPos` 屏幕坐标——混用会导致球瞬移（2026-11-07 拖动手感异常的根因）；面板文本绘制注意 `DrawTextW` 在 user32，`SetBkMode`/`SetTextColor` 在 gdi32——**原型与调用点必须同侧**（这次劈叉只炸在冻结 GUI 路径，selftest 补了 panel-paint-apis + panel-paint-smoke 端到端冒烟拦住）；窗口斜向缩放用 `WM_SYSCOMMAND SC_SIZE|WMSZ_BOTTOMRIGHT`，别用合成的 WM_NCLBUTTONDOWN。
- **pywebview 退出/隐藏坑**：退出只投 `WM_CLOSE`，**勿与 `window.destroy()` 混用**（双关闭竞态 → on_close 重入 KeyError('master') → WinForms 错误框）；`hidden=True` + frameless 会让 WebView2 控制器初始化 E_ABORT（--minimized 用"正常创建 + loaded 后隐藏"绕开）；GUI 线程事件里的异常走 WinForms 自带错误框、不进 Python 日志（异常钩子 + stderr 重定向已落 `%TEMP%\WinCtlPanel3-stderr.log`）。
- 诊断日志：`%TEMP%\WinCtlPanel2-diag.log`（每次启动追加显示器枚举与亮度探测结果）；球/面板异常落 `%TEMP%\WinCtlPanel3-ball.log`，全局异常落 `%TEMP%\WinCtlPanel3-stderr.log`（用户报"没字/没反应/报错"先看这三个）。

## 开源仓库（2026-11-07 上线）

- **GitHub**：https://github.com/Zen-GUAN/desktop-console （公开，MIT License；最新 3aea92d+，随文档提交持续推送）
- Release：https://github.com/Zen-GUAN/desktop-console/releases/tag/v3.0.0 （含 `DesktopConsole-v3.0.0.exe`，16.1MB）
- **坑：GitHub 资产名不支持非 ASCII**（中文上传会被归一成 default.exe，PATCH 中文名也无效）→ 发布二进制用 ASCII 名
- 推送走 SSH 隧道代理（`_tools\proxy\proxy_on.ps1`，git 已配 github.com → 127.0.0.1:3128）

## 下一步（收工态：全部完成）

- ~~UI 层复验~~ ✓ 用户实测通过（2026-11-07）
- ~~开机自启~~ ✓ HKCU Run + `--minimized` 静默启动，托盘可开关
- 可选（未排期）：代码签名（消除 SmartScreen 提示）；morph 时长/面板配色手感参数微调

## 文件地图

```
AGENT_HANDOFF.md   交接全貌（架构要点/bug 清单/exe 路线论证）
环境与依赖.md       运行依赖与自检命令
docs\              任务书×2 + 评估_Kimi前端能力 + 修复记录_亮度不支持误报
v1-winforms\       回退版（6/6，9 项测试 PASS）
v2-web\            UI 源 + 可独立跑（ui\ 前端=Kimi 管 / server\ 后端=ZCode 管）
v3-exe\            独立 exe（现行分发版；app.py/winapi.py 后端 + build.py 打包）
tools\             独立亮度工具
```
