# 桌面控制台 · 交接文档（AGENT_HANDOFF）

> 最后更新：2026-11-07
> 状态：**独立 exe 已完成（2026-11-07，路线 A）**：`v3-exe\dist\桌面控制台.exe`（16.1MB 单文件，后端全实测通过，UI 层复验待做），同日新增 **360 式悬浮球**（功能自选，见 `v3-exe\README.md`）。v1/v2 保留为回退，v2 兼作 v3 的 UI 源。
> exe 的构建/使用/已知说明见 `v3-exe\README.md`；§5 保留打包路线的原始论证（路线 A 已实施）。

---

## 一、这是什么东西

Windows 11 **未激活**，所以「设置 → 个性化」整类被锁（背景/颜色/主题/动态光效/锁屏/开始/任务栏/字体全灰）。

这个项目用 Web 技术把那 6 项功能重新提供出来，做成一个 380×600 的桌面小浮窗。

| 功能 | v1（WinForms） | v2（Web） |
|---|---|---|
| 深色/浅色模式 | ✓ | ✓ |
| 强调色 | ✓ | ✓ |
| 任务栏对齐 | ✓ | ✓ |
| 桌面壁纸 | ✓ | ✓ |
| 一键还原（备份回滚） | ✓ | ✓ |
| 屏幕亮度 DDC/CI | ✓ | ✓（2026-11-07 修复，UI 待复验） |

**v1 丑但全通；v2 好看，亮度 bug 已修（根因在后端，见 §六 bug 1），两版并存现为回退与现行关系。**

---

## 二、目录结构

```
D:\workspace\桌面控制台\
  AGENT_HANDOFF.md          ← 本文件
  README.md                 ← 项目说明
  PROJECT.md                ← 项目专属知识（分工/根因结论/坑，2026-11-07 建）
  环境与依赖.md              ← 运行需要什么
  docs\
    任务书_桌面浮窗控制面板.md   原始需求（11-04）
    任务书_浮窗UI重写.md        v2 的任务书（11-05）
    评估_Kimi前端能力.md        v2 生成方的能力评估 + 3 个 bug 记录
  v1-winforms\              ← 原版，PowerShell + WinForms，48KB 单文件
    WinCtlPanel.ps1 49KB
    启动.cmd
    README.md
    测试记录.md             9 项验收 PASS
  v2-web\                   ← 现行版，Kimi-K3.1-Pro 生成 + 人工修 2 个 bug
    ui\index.html  12.6KB
    ui\style.css   16.5KB
    ui\app.js      18.6KB
    server\Server.ps1   28KB   HttpListener + 6 项操作
    server\Native.ps1   6.4KB  P/Invoke（DDC/CI、壁纸、广播）
    启动.cmd
    README.md
    诊断_亮度.log
    server\Server.ps1.bak-before-fix
  v3-exe\                   ← 独立 exe（2026-11-07 产出，路线 A：pywebview+PyInstaller）
    app.py                  Python 宿主：HTTP 服务 + 全部 API（对齐 Server.ps1）
    winapi.py               ctypes 绑定（DDC/CI、壁纸、托盘、热键、对话框、提权）
    build.py / gen_icon.py / version_info.txt / icon.ico
    dist\桌面控制台.exe     16.1MB 单文件
    README.md
  tools\                    ← 独立可用的亮度工具（不受 v2 的 bug 影响）
    brightness.ps1   4.5KB   -Get / -Set N / -Step ±N
    亮度调高.cmd
    亮度调低.cmd
```

---

## 三、v2 怎么跑（当前形态）

```
双击 v2-web\启动.cmd
  → 隐藏启动 PowerShell 跑 server\Server.ps1
  → 随机端口起 HttpListener
  → Edge --app 模式开 380×600 无边框窗口
```

退出：关窗口。**但设计上的"浏览器退出 → 服务自杀"这条自愈在 2026-11-07 实测失效了**（窗口关了，server 进程还在），见 §6 待修 bug 2。

---

## 四、架构要点（改代码前必读）

- **API 有 Origin 校验**：不带 `Origin: http://127.0.0.1:<port>` 的请求返回 403。所以不能直接 curl 调试，要么从页面里发，要么临时放宽校验。
- **端口是随机分配**（TcpListener 探测），不是固定值。要拿端口就查 Edge 命令行里的 `--app=http://127.0.0.1:PORT/`。
- **所有改动前先备份**：`Add-Backup` 把原值写进去，底部「一键还原」按备份回滚。这是它比手工改注册表强的地方。
- **`$env:ProgramFiles(x86)` 必须写 `${env:ProgramFiles(x86)}`** —— v2 生成时踩过，见 AGENTS.md 技术红线 2。

---

## 五、目标：做成 exe

> **✅ 已完成（2026-11-07，路线 A）**：`v3-exe\dist\桌面控制台.exe`（16.1MB，selftest 7/7、无窗口 API、GUI 冒烟全过）。
> 与原方案的一处偏离：Python 宿主**保留了本地 HTTP 层**（而非 postMessage 直连）——为了让前端 `app.js` 零改动（前端归 Kimi，ui\ 一字未动）。原始论证保留如下。

### 5.1 要解决的核心矛盾

现在这套是 **PowerShell 后端 + 外部浏览器**，不是一个独立应用。做成 exe 的本质是把这两块都装进一个进程/一个文件里：

| 部分 | 现在 | 打包后要变成 |
|---|---|---|
| UI | Edge `--app` 开 index.html | 内嵌 WebView2（或 Electron 自带 Chromium） |
| 后端 | PowerShell HttpListener（HTTP + 轮询） | 同进程内调用（不需要 HTTP 了），或保留本地 HTTP |
| 系统操作 | PowerShell P/Invoke | 用目标语言重写 P/Invoke（逻辑可照搬 `Native.ps1`） |

**关键判断：HTTP 层可以扔掉。** 现在用 HTTP 是因为 UI 在浏览器里；装进 WebView2 后，前端可以直接 `window.chrome.webview.postMessage()` 与宿主通信，比 HTTP 干净得多。

### 5.2 本机已具备的条件（2026-11-07 实测）

```
node        ✓ 已装
python      ✓ 已装（3.12）
WebView2    ✓ 已装 156.0.4314.58   ← 关键：不用额外装运行时
Edge        ✓ 156.0.4314.58
dotnet      ✗ 未装
rust        ✗ 未装
pyinstaller ✗ 未装（pip 可装）
```

### 5.3 三条路线与取舍

#### 路线 A（推荐）：Python + pywebview + PyInstaller

```
pip install pywebview pyinstaller
→ Python 起 WebView2 窗口，加载 v2-web\ui\*
→ 6 项操作用 ctypes 重写（照搬 Native.ps1 的 P/Invoke 声明）
→ pyinstaller --onefile --windowed 出单 exe
```

- **优点**：Python 已有；WebView2 已有；纯 Python 写系统调用（ctypes）比想象中简单，`Native.ps1` 里的 DllImport 声明几乎能一对一翻译；产出约 **30–50MB** 单文件
- **缺点**：PyInstaller 单文件启动有 1–2 秒解压延迟；无签名 exe 可能被 SmartScreen 拦一次
- **工作量估计**：2–3 小时（6 项操作重写 + 窗口 + 打包）

#### 路线 B：Electron

```
npm i -D electron electron-builder
→ main.js 起 BrowserWindow，loadFile(v2-web/ui/index.html)
→ 后端用 Node 写（DDC/CI 需 native 模块，或干脆 child_process 调 tools\brightness.ps1 兜底）
```

- **优点**：前端零改动，UI 直接可用；生态成熟
- **缺点**：**体积 150MB+**；DDC/CI 在 Node 里没有现成模块，要么写 node-gyp 原生模块（重），要么用 `child_process` 调 PowerShell（那就没脱离 PowerShell）；打包配置比 Python 麻烦
- **工作量估计**：3–4 小时

#### 路线 C：C# + WebView2

```
装 .NET SDK（约 200MB）
→ WinForms/WPF 宿主 + Microsoft.Web.WebView2 控件
→ 6 项操作直接用 C# P/Invoke（最贴近 Win32）
→ dotnet publish -r win-x64 -p:PublishSingleFile=true --self-contained false
```

- **优点**：**产出最小（5–10MB，框架依赖）**；原生集成最好；任务栏/壁纸这类操作在 C# 里最顺
- **缺点**：要先装 .NET SDK（占 200MB，只为打包）；WPF/WinForms 写 UI 不如 HTML 灵活——但这里 UI 已经是 HTML，正好用 WebView2 塞进去，所以这个缺点不成立
- **工作量估计**：2–3 小时（装 SDK 约 20 分钟，其余与路线 A 相当）

### 5.4 我的建议

**先 A，不成就 C。**

- A 的依赖全部现成，改动集中在「把 PowerShell 的 6 项操作翻成 Python」，风险可控
- 如果发现 Python 的 ctypes 在某些操作上别扭（比如窗口广播 `SendMessageTimeout`），再转 C
- **不建议 B**：为了一个 380×600 的小浮窗付出 150MB 体积和原生模块复杂度，不划算

### 5.5 打包前必须先解决的三件事

**① 亮度检测 bug（✅ 已解除，2026-11-07）**

已定位并修复，**根因在后端**（旧「是前端问题」的结论有误，勿再引用）：`Server.ps1` 的 `Get-MonitorState` 结尾 `return ,$result`（逗号包裹防展开）+ 调用方 `@(Get-MonitorState)`，使 `/api/state` 输出 `"monitors":[[{…}]]` 双层嵌套；前端 `state.monitors[0]` 取到内层数组，`supported` 为 undefined → 渲染「不支持」。修复后实测 JSON 平铺且 `supported:true`，**前端零改动**。

完整证据链、改动清单与复现命令见 `docs\修复记录_亮度不支持误报.md`。仅剩 UI 层复验（开面板拖滑杆）待做。

**② 窗口关闭后服务不退（体验缺陷）**

`Server.ps1` 里有个 `UiTimer` 检查 `$script:EdgeProcess.HasExited`，本意是浏览器退出就自杀。2026-11-07 实测失效：窗口关了，`Server.ps1` 进程仍在。

打包后这个问题自然消失（同进程），但如果要在现形态下修，检查 `Start-Process -PassThru` 拿到的进程句柄是否真的对应 Edge 主进程。

**③ 编码**

- 所有 `.ps1` **UTF-8 with BOM**（无 BOM 会被 PowerShell 5.1 按 GBK 读，中文注释吞换行）
- 所有 `.cmd` **纯 ASCII**（cmd 没有 BOM 概念，中文注释必崩）
- 详见 `D:\workspace\AGENTS.md` 技术红线 1 和 8

### 5.6 打包时的具体清单

- [x] 图标：`v3-exe\icon.ico`（`gen_icon.py` 纯代码复刻前端 favicon，多尺寸）
- [x] 版本信息：`v3-exe\version_info.txt`（产品名「桌面控制台」、版本 3.0.0.0）
- [ ] 启动位置：开机自启走 Startup 文件夹还是注册表 Run？（参考 `yuancheng` 项目用 Startup + VBS 的方案，已验证能活）——**未做，待用户决定**
- [x] **单实例**：互斥体 `Local\WinCtlPanel3_SingleInstance`（与 v2 的互斥体独立，两版可并存）
- [x] **不要求管理员**：PyInstaller 默认 manifest 即 asInvoker；仅「锁屏图片」功能运行时按需弹 UAC（与 v1/v2 一致）
- [x] 卸载/还原：备份共用 `%APPDATA%\WinCtlPanel\backup.json`，与 v2 互相兼容（v2 的 PowerShell 还原也能读 Python 写入的备份）
- [x] 无签名 exe 第一次运行会被 SmartScreen 提示「未知发布者」，正常现象，已在 `v3-exe\README.md` 写清

---

## 六、已知 bug 清单

| # | 问题 | 严重度 | 位置 |
|---|---|---|---|
| 1 | ~~亮度卡片误报"不支持"~~ **已修复（2026-11-07）**：根因是后端 `/api/state` 的 monitors 双层嵌套（`return ,$result` + `@()`），旧「前端问题」结论有误 | 已解决 | `v2-web\server\Server.ps1` 的 `Get-MonitorState`（详见 `docs\修复记录_亮度不支持误报.md`） |
| 2 | 窗口关闭后 server 进程不退 | 低（残留进程） | `v2-web\server\Server.ps1` 的 UiTimer 逻辑（**v3 同进程架构下已消除**：关窗即全退，另有 8 秒硬兜底防僵尸；v2 现形态不修） |
| 3 | v2 的 `.cmd`/`.ps1` 曾因中文注释崩溃 | 已修 | 见 §5.5 ③ |

v2 里另外修好的两个 bug（生成方留下的）记录在 `docs\评估_Kimi前端能力.md`：
- `"$env:ProgramFiles(x86)\..."` 少 `${}` → 报"找不到 Edge"而 Edge 装着
- `ConcurrentQueue.Dequeue()` 不存在（只有 `TryDequeue`）→ 一有请求就崩

---

## 七、v1 为什么留着

v1 是 PowerShell + WinForms 单文件（49KB），**功能 6/6 全通、9 项测试 PASS**（见 `v1-winforms\测试记录.md`）。

留着它的理由：
1. v2 亮度坏着，v1 的亮度是好的 → 可以作为「亮度怎么调」的参考实现
2. v2 打包失败时是可靠回退
3. 它的 P/Invoke 代码（DDC/CI、SystemParametersInfo、注册表操作）比 v2 的更早验证过

**但 v1 的界面太丑**（这是当初重写 v2 的原因），所以不要走回头路把 v1 当终点。

---

## 八、相关但不在本项目

- **亮度命令行工具**：`tools\brightness.ps1`（这个独立可用，不受 v2 bug 影响）
- **系统未激活的处理**：`D:\workspace\AGENTS.md` §项目名录里没有单独的"系统"项目，相关约束散在本文件 §一
- **开机自启的可靠方案**：参考 `D:\workspace\yuancheng\`（Startup + VBS + 守卫，2026-11-07 验证通过）
