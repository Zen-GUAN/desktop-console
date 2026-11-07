# WinCtlPanel2 · 桌面控制台（Web 版）

> 2026-11-05 由 SCNet + Kimi-K3.1-Pro 生成的界面层，本机接手修复后可用。
> 原版（WinForms）在 `..\WinCtlPanel\`，**保留作回退**。

## 这是什么

Windows 未激活时「设置 → 个性化」整类被锁。这个面板用 Web 技术把那 6 项功能提供出来，
界面比 WinForms 版明显好看（卡片式、淡紫主题、圆角、状态徽章）。

## 怎么跑

```
双击 启动.cmd
```

它做两件事：起本地 HttpListener（随机端口）+ 用 Edge `--app` 模式开一个 380×600 的无边框窗口。
关窗口即退（server 会检测到浏览器退出并自杀）。

依赖：PowerShell 5.1、Edge 或 Chrome（脚本会自己找）、无任何 pip/npm 依赖。

## 功能与状态

| 功能 | 状态 | 说明 |
|---|---|---|
| 深色/浅色模式 | ✅ 可用 | 改 `AppsUseLightTheme` + 广播 `ImmersiveColorSet` |
| 强调色 | ✅ 可用 | `AccentColor`（ABGR）+ `ColorPrevalence` |
| 任务栏对齐 | ✅ 可用 | `TaskbarAl` 0=左 1=居中，需重启 explorer 生效 |
| 桌面壁纸 | ✅ 可用 | `SystemParametersInfo` SPI_SETDESKWALLPAPER |
| 一键还原 | ✅ 可用 | 所有改动前备份，底部按钮回退 |
| **屏幕亮度** | ❌ **不可用** | 界面显示"不支持 / 此显示器未开启 DDC/CI"，但**实际支持**，见下 |

## 已知缺陷：亮度显示"不支持"（**根因已定位：前端问题，不是后端**）

**2026-11-06 02:00 诊断结论**：在服务进程内加日志后实测——

```
=== 2026-11-06 01:59:32 ===
Monitors.Count = 1
  [0] type=PSCustomObject Handle=1 Desc=Generic PnP Monitor
    GetMonitorBrightness ok=True min=0 cur=23 max=100 Win32Err=0
```

**后端调用完全成功**（ok=True，Win32Err=0，读到 23/100）。
所以"不支持"不是探测失败，而是**前端渲染的状态不对**——可能是首次崩溃时缓存了旧状态，
或 `app.js` 里对 `supported` 字段的判定写反/取错字段。

**排查建议（下次从这里开始，别再从后端查）**：

1. 浏览器开发者工具看 `/api/brightness` 的真实返回 JSON，确认 `supported` 是否为 true
2. 若 API 返回 true 而 UI 显示不支持 → 问题在 `ui\app.js` 的渲染逻辑
3. Edge 的 `--user-data-dir` 是持久的，旧的 localStorage 可能残留 → 清掉
   `%LOCALAPPDATA%\WinCtlPanel2\EdgeProfile` 再试

**临时替代**：`_tools\brightness.ps1 -Set 45` 命令行可用。

---

## 历史排查记录（保留，避免重复劳动）

**现象**：亮度卡片红字「不支持」，滑块不可用。

**已排除的可能**（都实测过）：

1. 显示器不支持 → **排除**。单独跑它的 `Native.ps1`：
   ```
   GetMonitorBrightness: ok=True min=0 cur=23 max=100
   ```
2. 嵌套数组 → **排除**。`return ,$list` 会造成 `$m[0]` 类型是 `Object[]`，
   但把这个链条原样复制出来跑，`Get-BrightnessInfo` 仍返回 `Min=0 Cur=23 Max=100`
3. 浏览器缓存 → **排除**。F5 刷新后仍显示不支持

**已修的相关问题**：

- `return ,$list` + `@()` 造成嵌套数组 → 改成 `return $list`
- `Get-BrightnessInfo` 加了 Win32 错误码诊断（`$script:LastBrightErr`），
  用来区分「真不支持」和「调用失败」——**但这个诊断值目前没被暴露到 API 里**

**下一步建议**：把 `$script:LastBrightErr` 和 `$script:LastBrightHandle` 塞进 `/api/brightness`
的返回体，看服务进程里 `GetMonitorBrightness` 到底返回什么错误码（推测 1168 ERROR_NOT_FOUND
或句柄失效）。在此之前，**亮度请用原版的命令行工具**：

```powershell
D:\zcodefile\_tools\brightness.ps1 -Get
D:\zcodefile\_tools\brightness.ps1 -Set 45
D:\zcodefile\_tools\brightness.ps1 -Step -10
```

## 接口安全

API 有 Origin 校验：不带 `Origin: http://127.0.0.1:<port>` 的请求返回 403。
所以不能直接 curl 调试——要么从页面里发，要么先临时放宽校验。

## 修过的 3 个 bug（SCNet 原始交付开箱即崩）

| # | Bug | 修法 |
|---|---|---|
| 1 | `"$env:ProgramFiles(x86)\..."` 少 `${}`，展开成 `C:\Program Files(x86)\...` | 改成 `${env:ProgramFiles(x86)}`，并加 Chrome 兜底 |
| 2 | `ConcurrentQueue.Dequeue()` 不存在（只有 `TryDequeue`） | 改成 `TryDequeue([ref]$ctx)` |
| 3 | （未修，见上）亮度检测误报 | — |

## 目录

```
ui\index.html      12.6 KB   三卡布局
ui\style.css       16.5 KB   淡紫主题
ui\app.js          18.6 KB   与后端交互
server\Server.ps1  26.6 KB   HttpListener + 6 项操作
server\Native.ps1   6.4 KB   P/Invoke（DDC/CI、壁纸、广播）
启动.cmd
评估_Kimi前端能力.md          对 Kimi-K3.1-Pro 的评测记录
Server.ps1.bak-before-fix     修 Bug 1 前的备份
```

## 不碰激活

本工具**只绕过设置面板的 UI 限制**，不改授权状态、不做任何破解/激活。
底层注册表与系统 API 本来就可写，未激活锁的只是那个设置界面。
