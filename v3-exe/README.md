# 桌面控制台 v3 — 独立 exe

> 2026-11-07 按交接文档 §5 路线 A 完成：**Python(pywebview) 宿主 + 内嵌 WebView2 + PyInstaller**。
> 产物：`dist\桌面控制台.exe`（16.1 MB 单文件，双击即用，零安装）。

## 使用

```
双击 dist\桌面控制台.exe
  → 随机端口起本地 HTTP 服务（127.0.0.1）
  → WebView2 窗口加载打包在 exe 内的 v2-web\ui
  → 托盘图标：左键显示面板 / 右键菜单（显示、置顶、悬浮球设置、退出）
  → 全局热键：Ctrl+Alt+↑/↓ 亮度，Ctrl+Alt+D 深浅色
  → 悬浮球（360 式）：屏幕右缘中部，56 逻辑像素
```

关窗口 = 退出进程（v2 的「关窗后 server 残留」问题在同进程架构下自然消失）。

## 悬浮球（2026-11-07 新增，同日重构为 morph 单实体）

- **交互模型**：悬停球体 → **球 morph 成小面板**（圆角从正圆插值到面板、约 145ms），球被面板顶替——任一时刻只有一个实体；鼠标离开/点空白/执行动作 → 面板缩回成球
- **面板内容**：顶部亮度滑块（显示当前百分比，拖动实时调节全部显示器）+ 功能行（按设置顺序，悬停整卡高亮，点击执行）；交互卡片与拖动区（面板底色/把手凹槽/卡片缝隙）视觉分区
- **拖动**：拖球体或拖面板（含卡片行，位移 >4px 判定为拖）皆可移动；**松手靠边 → 缩回贴边成半个；松手在中间 → 球在面板下方保持展开**
- **功能自选**：托盘右键 → 「悬浮球」子菜单勾选，或主面板「更多工具 → 悬浮球设置」页（勾选+排序可视化）。可选 9 项，默认 5 项
- **实现要点**：纯后端（ctypes 分层窗口 + UpdateLayeredWindow），前端零改动；球线程整体 `SetThreadDpiAwarenessContext(PMv2)` 全程物理像素；拖动/缩放/面板拖动均为异步轮询（GetCursorPos + GetAsyncKeyState，绕开 WebView2 capture）；异常落 `%TEMP%\WinCtlPanel3-ball.log`
- **关闭悬浮球**：托盘子菜单取消「启用悬浮球」即可；配置即时落盘

## 开机自启

- 托盘右键勾选「开机自启」→ 写入 HKCU Run 键（`--minimized` 静默启动：开机仅球+托盘，主面板点球/托盘唤回，窗口闪现 1-2 秒后自动隐藏）
- 取消勾选即删除 Run 键；设置即时生效，重启电脑验证

## 与 v2 的关系

- **前端 `ui\` 与 v2 完全同一份**（构建时从 `..\v2-web\ui` 打包进 exe）——前端归 Kimi，本目录不碰它
- Python 宿主保留了本地 HTTP 层（而非 postMessage 直连），就是为了**前端零改动**
- 后端 API 与 `Server.ps1` 逐条对齐（同样的路由、参数、中文错误文案、首写备份语义）
- 备份文件与 v2 共用 `%APPDATA%\WinCtlPanel\backup.json`，两个版本可互相还原对方做的改动
- v2 修过的亮度 JSON 形状 bug 在 Python 版天然不存在，且 selftest 有形状回归项盯着

## 构建与测试

```
python build.py        # 缺依赖自动用清华镜像安装；产出 dist\桌面控制台.exe
python app.py --selftest          # 后端自检 7 项
python app.py --no-window --port 18779   # 无窗口模式，curl 调 API
```

2026-11-07 实测：未冻结 selftest 7/7 ✓；无窗口 API 全绿（state 平铺 / 静态资源 / 预览 / 错误路径 / exit）✓；
冻结后 selftest 7/7 ✓、无窗口 API ✓、GUI 冒烟（窗口创建、热键注册、POST exit 1 秒退出）✓。

## 部署到其他电脑（通用性）

单文件拷走即用（无需安装、无需管理员）。目标机条件与适配情况：

| 条件 | 说明 |
|---|---|
| Windows 10 1607+ / Windows 11 | 热键与悬浮球依赖的 API 自 1607 起可用 |
| WebView2 Runtime | Win11 自带；Win10 装新版 Edge 即有。**缺失时启动会弹窗引导到官方下载页**，不会静默失败 |
| .NET Framework 4.6.2+ | Win10 1709+ / Win11 自带 |

- **换机诊断**：`桌面控制台.exe --selftest` → 弹窗 + `%TEMP%\WinCtlPanel3-selftest.log`，12 项检查含系统版本/WebView2/.NET/DDC/CI/备份语义/球渲染
- **未激活适配**：全部功能只写 HKCU 注册表与用户态 API，不碰需要激活的界面，不需要管理员
- **已知差异**：任务栏对齐（TaskbarAl）仅 Win11 生效，Win10 写注册表无变化（selftest 会提示）；显示器不支持 DDC/CI 时亮度卡片如实显示「不支持」；无显示器（RDP 断开等）时显示「未找到可控制的显示器」
- **DPI**：悬浮球线程独立 PMv2 感知，100%–200% 缩放下视觉尺寸一致；主面板为逻辑像素，各缩放下 380×600 不变

## 已知说明

- **无签名，首次运行 SmartScreen 会提示「未知发布者」**——点「更多信息 → 仍要运行」，正常现象
- 若 WebView2 窗口起不来（日志报 `E_ABORT`/`Failed to delete user data folder`），多为上次**强杀进程**留下的 msedgewebview2 僵尸锁住了数据目录——结束那些僵尸（或重启）再开即可；正常关闭不会残留（系统的 6 个 msedgewebview2 属于 Windows Shell，勿动）
- **锁屏图片**功能运行时会弹 UAC（写 HKLM 组策略必须提权，与 v1/v2 行为一致）；其余功能全都不需要管理员
- **开机自启未做**——要加的话参考 `yuancheng` 项目的 Startup + VBS 方案（工作区 AGENTS.md 有验证记录）
- 图标是 `gen_icon.py` 按前端 favicon 复刻的纯代码生成品；想换图标就替换 `icon.ico` 重新 build
- `build\` 与 `*.spec` 是 PyInstaller 过程产物，可删，下次 build 自动重建

## 文件

```
app.py             HTTP 服务 + 全部 API 处理器 + 状态组装 + main（后端核心）
winapi.py          ctypes 绑定：DDC/CI、壁纸、注册表、托盘、热键、对话框、提权、悬浮球
verify_ball_settings.py  悬浮球设置页 headless 验收（playwright + 系统 Edge，自起自停后端，零窗口）
verify_layout.py         布局适配 headless 验收（340×480 / 1200×900 / 380×600 三档视口几何）
gen_icon.py        生成 icon.ico（纯 stdlib 画素）
version_info.txt   PyInstaller 版本资源（产品名/版本号）
build.py           一键构建（装依赖 → 图标 → PyInstaller）
icon.ico           生成的图标
```
