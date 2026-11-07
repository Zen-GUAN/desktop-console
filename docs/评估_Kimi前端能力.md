# SCNet + Kimi-K3.1-Pro 前端能力评估（2026-11-05）

> 任务：用 Web 技术重写桌面浮窗控制面板的 UI。原始诉求："那个浮窗太丑了"。
> 结果：**界面确实好看了，但后端有 3 个 bug，其中一个致命。**

## 一、结论先行

| 维度 | 评价 |
|---|---|
| **视觉设计** | **明显优于原版 WinForms**。卡片式布局、淡紫主题、圆角、留白得当，有"产品感"而不是"工程师界面" |
| **代码组织** | 清晰。ui（html/css/js 三件）+ server（Server/Native 两层）分离，职责明确 |
| **能跑吗** | **修了 3 个 bug 才跑起来**。原始交付开箱即崩 |
| **功能完整度** | 6 项功能界面全有，但亮度检测误报"不支持" |

**总评：前端强，后端弱。** 它写 HTML/CSS 的水平明显高于写 PowerShell 服务端的水平。

## 二、产出了什么

```
D:\zcodefile\_tools\WinCtlPanel2\
  ui\index.html     12.6 KB
  ui\style.css      16.5 KB
  ui\app.js         18.6 KB
  server\Server.ps1 26.6 KB   ← 3 个 bug 都在这里
  server\Native.ps1  6.4 KB   ← P/Invoke 层，写得对
  启动.cmd
```

架构：本地 HttpListener（随机端口）+ Edge `--app` 窗口，前端纯 HTML/CSS/JS 无构建链，符合任务书要求。

## 三、修的 3 个 bug

### Bug 1：Edge 路径变量写错（致命，开箱即崩）

```powershell
# 错：PowerShell 解析成 $env:ProgramFiles + 字面量 "(x86)"
"$env:ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe"
# 实际展开成 "C:\Program Files(x86)\Microsoft\..." ← 少了反斜杠，路径不存在

# 对：
"${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
```

后果：抛「没有找到 Microsoft Edge」，而 Edge 其实装着。**这个错误极隐蔽**——字符串看着完全正常。

顺带加了 Chrome 兜底（万一 Edge 真没有）。

### Bug 2：`ConcurrentQueue.Dequeue()` 不存在（致命，一有请求就崩）

```powershell
# 错：ConcurrentQueue 只有 TryDequeue，没有 Dequeue
$ctx = $script:ContextQueue.Dequeue()

# 对：
$ctx = $null
if(-not $script:ContextQueue.TryDequeue([ref]$ctx)){ break }
```

后果：弹 .NET 未处理异常框「不包含名为 Dequeue 的方法」，整个面板直接死。**这是把普通 Queue 的 API 套到了并发队列上**。

### Bug 3：亮度检测误报"不支持"（功能缺陷，未修）

现象：界面亮度卡片显示红色「不支持 / 此显示器未开启 DDC/CI」，但实测这台显示器**支持**。

证据：把它的 `Native.ps1` 单独跑一遍——

```
Enumerate 返回: 1
GetNumberOfPhysicalMonitors: ok=True count=1
GetPhysicalMonitors: ok=True
GetMonitorBrightness: ok=True min=0 cur=23 max=100   ← 完全正常
```

**原生层是对的**，问题在 `Server.ps1` 的 `Get-PhysMonitors` / `Get-BrightnessInfo` 组装环节
（`$script:Monitors` 里的 Handle 没能传到 `GetMonitorBrightness`）。
同一个文件里，`Get-BrightnessInfo` 直接 return $null，UI 就据此显示"不支持"——
**没有区分「显示器真的不支持」和「我们调用失败了」**，这是错误处理设计问题，不只是 bug。

## 四、值得肯定的地方

1. **视觉做对了**：卡片分组（亮度/个性化/壁纸）、状态徽章（本地就绪绿点）、底部"就绪·所有改动均已备份"
2. **还原机制齐全**：底部「一键还原 有改动可还原」，说明它实现了备份
3. **安全考虑了**：API 有 Origin 校验（我用 curl 直接打返回 403，说明不是裸奔）
4. **自适应**：窗口 380×600，内容按卡片滚动

## 五、给下次派活的启示

如果还要让它做类似的事，任务书里要补三条：

1. **"交付前必须自己启动一遍并截图"** —— 它显然没跑过，否则 Edge 路径和 Dequeue 立刻就暴露
2. **"错误处理要区分「不支持」和「调用失败」"** —— 这是它第 3 个 bug 的根因
3. **"PowerShell 里 `$env:ProgramFiles(x86)` 必须写成 `${env:ProgramFiles(x86)}`"** —— 直接把坑写在任务书里

## 六、当前状态

面板**已修好并在运行**（PID 见进程表，端口 58493，窗口标题「WinCtl · 桌面控制台」）。

已知缺陷：亮度卡片仍显示"不支持"（Bug 3 未修），其余 5 项功能界面就绪。
