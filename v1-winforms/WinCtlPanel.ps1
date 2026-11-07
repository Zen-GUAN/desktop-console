# ============================================================
#  WinCtlPanel —— 桌面浮窗控制面板
#  用途：Windows 未激活导致「个性化」设置面板被锁时，直接走
#        DDC/CI（亮度）+ 注册表/SPI（外观）把常用个性化功能做回来。
#  纪律：不碰激活本身、不改系统文件、不装服务、不写 HKLM
#        （仅「锁屏图片」一项在用户明确同意后经 UAC 提升写策略键）。
#  编码：本文件必须保存为 UTF-8 with BOM（PowerShell 5.1 硬要求）。
# ============================================================
param([switch]$SelfTest)

$ErrorActionPreference = 'Stop'

# ---------- 路径 ----------
$script:DataDir   = Join-Path $env:APPDATA 'WinCtlPanel'
$script:BackupFile = Join-Path $DataDir 'backup.json'
$script:UiFile     = Join-Path $DataDir 'ui.json'
$script:ScriptPath = $MyInvocation.MyCommand.Path

if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir -Force | Out-Null }

# ---------- 注册表路径 ----------
$script:PTheme = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
$script:PAdv   = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# ============================================================
#  P/Invoke：DDC/CI 部分（原样搬运自已实测的 brightness.ps1）
# ============================================================
Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Collections.Generic;
using System.Windows.Forms;

public class Dxva2 {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int left, top, right, bottom; }

    [StructLayout(LayoutKind.Sequential)]
    public struct PHYSICAL_MONITOR {
        public IntPtr hPhysicalMonitor;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 128)]
        public char[] szPhysicalMonitorDescription;
    }

    public delegate bool MonitorEnumProc(IntPtr hMonitor, IntPtr hdc, ref RECT rect, IntPtr data);

    [DllImport("user32.dll")]
    public static extern bool EnumDisplayMonitors(IntPtr hdc, IntPtr clip, MonitorEnumProc proc, IntPtr data);
    [DllImport("dxva2.dll", SetLastError = true)]
    public static extern bool GetNumberOfPhysicalMonitorsFromHMONITOR(IntPtr hMonitor, out uint count);
    [DllImport("dxva2.dll", SetLastError = true)]
    public static extern bool GetPhysicalMonitorsFromHMONITOR(IntPtr hMonitor, uint count, [Out] PHYSICAL_MONITOR[] monitors);
    [DllImport("dxva2.dll", SetLastError = true)]
    public static extern bool GetMonitorBrightness(IntPtr hMonitor, out uint min, out uint cur, out uint max);
    [DllImport("dxva2.dll", SetLastError = true)]
    public static extern bool SetMonitorBrightness(IntPtr hMonitor, uint value);
    [DllImport("dxva2.dll", SetLastError = true)]
    public static extern bool DestroyPhysicalMonitor(IntPtr hMonitor);

    public static System.Collections.Generic.List<IntPtr> Handles = new System.Collections.Generic.List<IntPtr>();
    private static bool Callback(IntPtr hMonitor, IntPtr hdc, ref RECT rect, IntPtr data) {
        Handles.Add(hMonitor); return true;
    }
    public static int Enumerate() {
        Handles.Clear();
        return EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, new MonitorEnumProc(Callback), IntPtr.Zero) ? Handles.Count : -1;
    }
}

public class W2 {
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern bool SystemParametersInfo(uint uiAction, uint uiParam, StringBuilder pvParam, uint fWinIni);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern bool SystemParametersInfo(uint uiAction, uint uiParam, string pvParam, uint fWinIni);
    [DllImport("user32.dll")]
    public static extern bool ReleaseCapture();
    [DllImport("user32.dll")]
    public static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint msg, IntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out IntPtr result);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool UnregisterHotKey(IntPtr hWnd, int id);
}

// 自绘无边框窗体：只为了能收到 WM_HOTKEY 全局热键消息
public class PanelForm : Form {
    public event EventHandler<int> HotKey;
    protected override void WndProc(ref Message m) {
        if (m.Msg == 0x0312) {                      // WM_HOTKEY
            EventHandler<int> h = HotKey;
            if (h != null) h(this, m.WParam.ToInt32());
        }
        base.WndProc(ref m);
    }
}
"@ -ReferencedAssemblies @('System.Windows.Forms', 'System.Drawing')

# ============================================================
#  通用函数
# ============================================================

function ConvertTo-ABGR {
    # RGB(三个 0-255) → Windows 注册表 AccentColor 的 ABGR 32 位整数
    param([int]$R, [int]$G, [int]$B)
    return [int](0xFF000000 -bor (($B -band 0xFF) -shl 16) -bor (($G -band 0xFF) -shl 8) -bor ($R -band 0xFF))
}

function Get-RegValue {
    param([string]$Path, [string]$Name, $Default)
    try {
        $v = Get-ItemProperty -Path $Path -Name $Name -ErrorAction Stop
        return $v.$Name
    } catch { return $Default }
}

function Set-RegValue {
    param([string]$Path, [string]$Name, [int]$Value)
    if (-not (Test-Path $Path)) { $null = New-Item -Path $Path -Force }
    Set-ItemProperty -Path $Path -Name $Name -Value $Value -Type DWord
}

function Broadcast-Settings {
    # 广播 WM_SETTINGCHANGE。主题/强调色用 ImmersiveColorSet，其他用空参数。
    param([string]$Param = '')
    $res = [IntPtr]::Zero
    $null = [W2]::SendMessageTimeout([IntPtr]0xFFFF, 0x001A, [IntPtr]::Zero, $Param, 0x0002, 1000, [ref]$res)
}

# ---------- 备份（合并语义：同一键只记最早的值） ----------
$script:BackupKeysSeen = @{}

function Add-Backup {
    param([string]$Key, $Value)
    if ($script:BackupKeysSeen.ContainsKey($Key)) { return }
    $data = $null
    if (Test-Path $script:BackupFile) {
        try { $data = Get-Content $script:BackupFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $data = $null }
    }
    if ($null -eq $data) {
        $data = New-Object psobject -Property @{
            created = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
            values  = (New-Object psobject)
        }
    }
    if ($data.values.PSObject.Properties[$Key]) {
        # 文件里已有（可能是上次会话留的），以文件里的为准
        $script:BackupKeysSeen[$Key] = $true
        return
    }
    $data.values | Add-Member -MemberType NoteProperty -Name $Key -Value $Value -Force
    $data | ConvertTo-Json -Depth 5 | Out-File -FilePath $script:BackupFile -Encoding UTF8
    $script:BackupKeysSeen[$Key] = $true
}

# ---------- 亮度 ----------
function Get-PhysMonitors {
    $list = @()
    $n = [Dxva2]::Enumerate()
    if ($n -le 0) { return ,$list }
    foreach ($hMon in [Dxva2]::Handles) {
        $cnt = [uint32]0
        if (-not [Dxva2]::GetNumberOfPhysicalMonitorsFromHMONITOR($hMon, [ref]$cnt)) { continue }
        if ($cnt -eq 0) { continue }
        $buf = New-Object 'Dxva2+PHYSICAL_MONITOR[]' $cnt
        if (-not [Dxva2]::GetPhysicalMonitorsFromHMONITOR($hMon, $cnt, $buf)) { continue }
        for ($i = 0; $i -lt $cnt; $i++) {
            $desc = (((-join $buf[$i].szPhysicalMonitorDescription)) -replace ([string][char]0), '').Trim()
            if (-not $desc) { $desc = "显示器 $($list.Count + 1)" }
            $list += [pscustomobject]@{ Handle = $buf[$i].hPhysicalMonitor; Desc = $desc }
        }
    }
    return ,$list
}

function Get-BrightnessInfo {
    param($Handle)
    $min = [uint32]0; $cur = [uint32]0; $max = [uint32]0
    if (-not [Dxva2]::GetMonitorBrightness($Handle, [ref]$min, [ref]$cur, [ref]$max)) { return $null }
    return [pscustomobject]@{ Min = $min; Cur = $cur; Max = $max }
}

# ---------- 壁纸 ----------
function Get-Wallpaper {
    $sb = New-Object System.Text.StringBuilder 1024
    $null = [W2]::SystemParametersInfo(0x0073, 1024, $sb, 0)   # SPI_GETDESKWALLPAPER
    return $sb.ToString()
}

function Set-WallpaperFile {
    param([string]$Path)
    Add-Backup 'wallpaper' (Get-Wallpaper)
    $ok = [W2]::SystemParametersInfo(0x0014, 0, $Path, 0x03)   # SPI_SETDESKWALLPAPER | UPDATEINI | SENDCHANGE
    if (-not $ok) { throw '系统拒绝了壁纸设置（SystemParametersInfo 返回失败），请确认图片格式是 jpg/png/bmp。' }
    Broadcast-Settings ''
}

# ---------- 深色 / 浅色 ----------
function Set-ThemeMode {
    param([bool]$Dark)
    Add-Backup 'appsLight'    (Get-RegValue $script:PTheme 'AppsUseLightTheme' 1)
    Add-Backup 'sysLight'     (Get-RegValue $script:PTheme 'SystemUsesLightTheme' 1)
    $lightVal = 1; if ($Dark) { $lightVal = 0 }
    Set-RegValue $script:PTheme 'AppsUseLightTheme'    $lightVal
    Set-RegValue $script:PTheme 'SystemUsesLightTheme' $lightVal
    Broadcast-Settings 'ImmersiveColorSet'
}

function Get-ThemeIsDark {
    return ((Get-RegValue $script:PTheme 'AppsUseLightTheme' 1) -eq 0)
}

# ---------- 强调色 ----------
function Set-AccentColor {
    param([int]$R, [int]$G, [int]$B)
    Add-Backup 'accent'         (Get-RegValue $script:PTheme 'AccentColor' 0)
    Add-Backup 'colorPrevalence'(Get-RegValue $script:PTheme 'ColorPrevalence' 0)
    Set-RegValue $script:PTheme 'AccentColor'      (ConvertTo-ABGR $R $G $B)
    Set-RegValue $script:PTheme 'ColorPrevalence'  1
    Broadcast-Settings 'ImmersiveColorSet'
}

# ---------- 任务栏对齐 ----------
function Set-TaskbarAlign {
    param([int]$Value)   # 0=左, 1=居中
    Add-Backup 'taskbarAl' (Get-RegValue $script:PAdv 'TaskbarAl' 1)
    Set-RegValue $script:PAdv 'TaskbarAl' $Value
    Broadcast-Settings ''
}

# ---------- 一键还原 ----------
function ConvertTo-RegInt($v) {
    # JSON 反序列化可能得到 Int64（如 4281550495），DWord 需要 Int32（补码）
    $n = [int64]$v
    if ($n -gt [int]::MaxValue) { $n = $n - 4294967296 }
    return [int]$n
}

function Restore-All {
    if (-not (Test-Path $script:BackupFile)) { return $false }
    $data = Get-Content $script:BackupFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $mons = $null
    foreach ($p in $data.values.PSObject.Properties) {
        try {
            switch -Regex ($p.Name) {
                '^wallpaper$'     { $null = [W2]::SystemParametersInfo(0x0014, 0, [string]$p.Value, 0x03) }
                '^appsLight$'     { Set-RegValue $script:PTheme 'AppsUseLightTheme'    (ConvertTo-RegInt $p.Value) }
                '^sysLight$'      { Set-RegValue $script:PTheme 'SystemUsesLightTheme' (ConvertTo-RegInt $p.Value) }
                '^accent$'        { Set-RegValue $script:PTheme 'AccentColor'          (ConvertTo-RegInt $p.Value) }
                '^colorPrevalence$'{ Set-RegValue $script:PTheme 'ColorPrevalence'     (ConvertTo-RegInt $p.Value) }
                '^taskbarAl$'     { Set-RegValue $script:PAdv  'TaskbarAl'             (ConvertTo-RegInt $p.Value) }
                '^bright:(\d+)$'  {
                    $idx = [int]$Matches[1]
                    if ($null -eq $mons) { $mons = Get-PhysMonitors }
                    if ($idx -lt $mons.Count) {
                        $parts = [string]$p.Value -split ';'
                        if ($parts.Count -ge 3) {
                            $null = [Dxva2]::SetMonitorBrightness($mons[$idx].Handle, [uint32]$parts[1])
                        }
                    }
                }
            }
        } catch { }
    }
    Broadcast-Settings 'ImmersiveColorSet'
    Broadcast-Settings ''
    Remove-Item $script:BackupFile -Force -ErrorAction SilentlyContinue
    $script:BackupKeysSeen = @{}
    return $true
}

# ============================================================
#  自测模式：-SelfTest
# ============================================================
if ($SelfTest) {
    $fail = 0
    function Test([string]$Name, [bool]$Ok, [string]$Detail) {
        $tag = 'PASS'; if (-not $Ok) { $tag = 'FAIL'; $script:fail++ }
        Write-Output ("[{0}] {1}  {2}" -f $tag, $Name, $Detail)
    }

    # 1. 自身编码：UTF-8 with BOM
    $bytes = [System.IO.File]::ReadAllBytes($script:ScriptPath)
    $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    Test '脚本编码 UTF-8 BOM' $hasBom ("前3字节: {0:X2} {1:X2} {2:X2}" -f $bytes[0], $bytes[1], $bytes[2])

    # 2. ABGR 转换
    $vRed   = ConvertTo-ABGR 255 0 0     # 期望 0xFF0000FF = -16776961
    $vBlue  = ConvertTo-ABGR 0 0 255     # 期望 0xFFFF0000 = -65536
    $vWhite = ConvertTo-ABGR 255 255 255 # 期望 0xFFFFFFFF = -1
    Test 'ABGR转换 红'   ($vRed   -eq -16776961) ("RGB(255,0,0)→{0}" -f $vRed)
    Test 'ABGR转换 蓝'   ($vBlue  -eq -65536)    ("RGB(0,0,255)→{0}" -f $vBlue)
    Test 'ABGR转换 白'   ($vWhite -eq -1)        ("RGB(255,255,255)→{0}" -f $vWhite)

    # 3. 注册表写/读/还原（TaskbarAl）
    $orig = Get-RegValue $script:PAdv 'TaskbarAl' 1
    Set-RegValue $script:PAdv 'TaskbarAl' 0
    $w = Get-RegValue $script:PAdv 'TaskbarAl' -1
    Set-RegValue $script:PAdv 'TaskbarAl' ([int]$orig)
    $r = Get-RegValue $script:PAdv 'TaskbarAl' -1
    Test '注册表写读还原' (($w -eq 0) -and ($r -eq [int]$orig)) "原=$orig 写后=$w 还原后=$r"

    # 4. 当前壁纸可读
    $wp = Get-Wallpaper
    Test '读当前壁纸' ($wp.Length -gt 0) $wp

    # 5. 枚举显示器 + DDC/CI 读亮度
    $mons = Get-PhysMonitors
    Test '枚举物理显示器' ($mons.Count -gt 0) ("共 {0} 个" -f $mons.Count)
    $ddcOk = $false; $ddcDetail = '无显示器'
    if ($mons.Count -gt 0) {
        $b = Get-BrightnessInfo $mons[0].Handle
        if ($null -ne $b) { $ddcOk = $true; $ddcDetail = ("{0}: {1}/{2}/{3} (min/cur/max)" -f $mons[0].Desc, $b.Min, $b.Cur, $b.Max) }
        else { $ddcDetail = $mons[0].Desc + ' 不支持 DDC/CI' }
    }
    Test 'DDC/CI 读亮度' $ddcOk $ddcDetail

    # 6. DDC 失败路径：零句柄 → 返回 null → 人话错误
    $bad = Get-BrightnessInfo ([IntPtr]::Zero)
    Test 'DDC失败返回可识别' ($null -eq $bad) '零句柄返回 null，UI 层据此显示人话错误'

    # 7. 备份 JSON 往返
    $tmp = Join-Path $env:TEMP ("wcp_test_{0}.json" -f (Get-Random))
    $old = $script:BackupFile; $oldKeys = $script:BackupKeysSeen
    $script:BackupFile = $tmp; $script:BackupKeysSeen = @{}
    Add-Backup 'taskbarAl' 1
    Add-Backup 'taskbarAl' 0      # 第二次写应被忽略
    $j = Get-Content $tmp -Raw -Encoding UTF8 | ConvertFrom-Json
    $roundtrip = ([int]$j.values.taskbarAl -eq 1)
    Remove-Item $tmp -Force
    $script:BackupFile = $old; $script:BackupKeysSeen = $oldKeys
    Test '备份JSON往返+合并语义' $roundtrip '同键二次写被忽略'

    Write-Output ''
    if ($fail -eq 0) { Write-Output '自测全部通过'; exit 0 }
    else { Write-Output ("自测失败 {0} 项" -f $fail); exit 1 }
}

# ============================================================
#  单实例互斥
# ============================================================
$script:createdNew = $false
$script:Mutex = New-Object System.Threading.Mutex($true, 'Local\WinCtlPanel_SingleInstance', [ref]$script:createdNew)
if (-not $script:createdNew) {
    [System.Windows.Forms.MessageBox]::Show('WinCtlPanel 已经在运行了（看看系统托盘）。', 'WinCtlPanel',
        [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
    $script:Mutex.Dispose()
    exit 0
}

# ============================================================
#  UI 构建
# ============================================================
$ErrorActionPreference = 'Continue'

function ConvertFrom-Hex([string]$hex) {
    return [System.Drawing.ColorTranslator]::FromHtml($hex)
}

$script:statusTimer = $null

# ---- 窗体 ----
$form = New-Object PanelForm
$form.Text = '控制面板'
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$form.StartPosition  = [System.Windows.Forms.FormStartPosition]::Manual
$form.Size = New-Object System.Drawing.Size(396, 640)
$form.MinimumSize = New-Object System.Drawing.Size(340, 480)
$form.BackColor = ConvertFrom-Hex '#F3F4F6'
$form.Opacity = 0.85
$form.ShowInTaskbar = $false
$form.TopMost = $false
$form.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9)
$form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::Font

# 位置记忆
$uiLoc = $null; $uiSize = $null; $uiTop = $false
if (Test-Path $script:UiFile) {
    try {
        $ui = Get-Content $script:UiFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($ui.x -ne $null -and $ui.y -ne $null) { $uiLoc = New-Object System.Drawing.Point([int]$ui.x, [int]$ui.y) }
        if ($ui.w -ne $null -and $ui.h -ne $null) { $uiSize = New-Object System.Drawing.Size([int]$ui.w, [int]$ui.h) }
        $uiTop = [bool]$ui.topmost
    } catch { }
}
$work = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
if ($null -ne $uiLoc) {
    # 夹在工作区里，防止屏幕拔了以后找不到窗
    $x = [Math]::Max($work.Left, [Math]::Min($uiLoc.X, $work.Right  - 100))
    $y = [Math]::Max($work.Top,  [Math]::Min($uiLoc.Y, $work.Bottom - 100))
    $form.Location = New-Object System.Drawing.Point($x, $y)
} else {
    $form.Location = New-Object System.Drawing.Point(($work.Right - 400), ($work.Top + 80))
}
if ($null -ne $uiSize) { $form.Size = $uiSize }
$form.TopMost = $uiTop

# ---- 自绘标题栏 ----
$titleBar = New-Object System.Windows.Forms.Panel
$titleBar.Dock = [System.Windows.Forms.DockStyle]::Top
$titleBar.Height = 34
$titleBar.BackColor = ConvertFrom-Hex '#2F6FED'
$form.Controls.Add($titleBar)

$lbTitle = New-Object System.Windows.Forms.Label
$lbTitle.Text = '  控制面板'
$lbTitle.ForeColor = [System.Drawing.Color]::White
$lbTitle.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9.5, [System.Drawing.FontStyle]::Bold)
$lbTitle.AutoSize = $true
$lbTitle.Location = New-Object System.Drawing.Point(0, 8)
$titleBar.Controls.Add($lbTitle)

$btnTop = New-Object System.Windows.Forms.Button
$btnTop.Text = '置顶'
$btnTop.Size = New-Object System.Drawing.Size(44, 24)
$btnTop.Location = New-Object System.Drawing.Point(($form.Width - 3 * 48 - 8), 5)
$btnTop.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnTop.FlatAppearance.BorderSize = 0
$btnTop.BackColor = ConvertFrom-Hex '#1E5AD6'
$btnTop.ForeColor = [System.Drawing.Color]::White
$btnTop.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnTop.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$titleBar.Controls.Add($btnTop)

$btnMin = New-Object System.Windows.Forms.Button
$btnMin.Text = '─'
$btnMin.Size = $btnTop.Size
$btnMin.Location = New-Object System.Drawing.Point(($form.Width - 2 * 48 - 8), 5)
$btnMin.FlatStyle = $btnTop.FlatStyle
$btnMin.FlatAppearance.BorderSize = 0
$btnMin.BackColor = $btnTop.BackColor
$btnMin.ForeColor = [System.Drawing.Color]::White
$btnMin.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnMin.Anchor = $btnTop.Anchor
$titleBar.Controls.Add($btnMin)

$btnClose = New-Object System.Windows.Forms.Button
$btnClose.Text = '✕'
$btnClose.Size = $btnTop.Size
$btnClose.Location = New-Object System.Drawing.Point(($form.Width - 1 * 48 - 8), 5)
$btnClose.FlatStyle = $btnTop.FlatStyle
$btnClose.FlatAppearance.BorderSize = 0
$btnClose.BackColor = $btnTop.BackColor
$btnClose.ForeColor = [System.Drawing.Color]::White
$btnClose.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnClose.Anchor = $btnTop.Anchor
$titleBar.Controls.Add($btnClose)

# 标题栏拖动
$titleBar.Add_MouseDown({
    param($s, $e)
    if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
        $null = [W2]::ReleaseCapture()
        $null = [W2]::SendMessage($form.Handle, 0xA1, [IntPtr]0x2, [IntPtr]0)
    }
})
$lbTitle.Add_MouseDown({
    param($s, $e)
    if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
        $null = [W2]::ReleaseCapture()
        $null = [W2]::SendMessage($form.Handle, 0xA1, [IntPtr]0x2, [IntPtr]0)
    }
})

# ---- 内容区 ----
$content = New-Object System.Windows.Forms.Panel
$content.Dock = [System.Windows.Forms.DockStyle]::Fill
$content.AutoScroll = $true
$content.Padding = New-Object System.Windows.Forms.Padding(14, 8, 14, 30)
$form.Controls.Add($content)

$script:Status = New-Object System.Windows.Forms.Label
$script:Status.Dock = [System.Windows.Forms.DockStyle]::Bottom
$script:Status.Height = 26
$script:Status.TextAlign = [System.Drawing.ContentAlignment]::MiddleLeft
$script:Status.Padding = New-Object System.Windows.Forms.Padding(10, 0, 4, 0)
$script:Status.ForeColor = ConvertFrom-Hex '#666666'
$script:Status.BackColor = ConvertFrom-Hex '#E9EBF0'
$script:Status.Text = '就绪'

function Show-Status {
    param([string]$Text, [bool]$IsError = $false)
    $script:Status.Text = $Text
    if ($IsError) { $script:Status.ForeColor = ConvertFrom-Hex '#C42B1C' }
    else          { $script:Status.ForeColor = ConvertFrom-Hex '#666666' }
}

# ---- 亮度（每屏一行 + 200ms 防抖） ----
$script:Monitors = @()
$script:BrightInfos = @()
$script:BrightBars = @()
$script:BrightLabels = @()
$script:PendingBright = @{}

try { $script:Monitors = Get-PhysMonitors } catch { $script:Monitors = @() }

$y = 10
$lbSec = New-Object System.Windows.Forms.Label
$lbSec.Text = '亮度（DDC/CI）'
$lbSec.ForeColor = ConvertFrom-Hex '#444444'
$lbSec.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9, [System.Drawing.FontStyle]::Bold)
$lbSec.AutoSize = $true
$lbSec.Location = New-Object System.Drawing.Point(14, $y)
$content.Controls.Add($lbSec)
$y += 22

if ($script:Monitors.Count -eq 0) {
    $lbNoMon = New-Object System.Windows.Forms.Label
    $lbNoMon.Text = '没有找到可控制的显示器。'
    $lbNoMon.ForeColor = ConvertFrom-Hex '#C42B1C'
    $lbNoMon.AutoSize = $true
    $lbNoMon.Location = New-Object System.Drawing.Point(22, $y)
    $content.Controls.Add($lbNoMon)
    $y += 24
} else {
    for ($i = 0; $i -lt $script:Monitors.Count; $i++) {
        $info = Get-BrightnessInfo $script:Monitors[$i].Handle
        $script:BrightInfos += $info
        $desc = $script:Monitors[$i].Desc
        if ($desc.Length -gt 14) { $desc = $desc.Substring(0, 13) + '…' }

        $lb = New-Object System.Windows.Forms.Label
        $lb.Text = $desc
        $lb.AutoSize = $false
        $lb.Size = New-Object System.Drawing.Size(92, 22)
        $lb.Location = New-Object System.Drawing.Point(18, ($y + 6))
        $content.Controls.Add($lb)

        $bar = New-Object System.Windows.Forms.TrackBar
        $bar.Minimum = 0; $bar.Maximum = 100
        $bar.TickStyle = [System.Windows.Forms.TickStyle]::None
        $bar.Size = New-Object System.Drawing.Size(160, 28)
        $bar.Location = New-Object System.Drawing.Point(114, ($y + 2))
        $bar.SmallChange = 1; $bar.LargeChange = 5
        $content.Controls.Add($bar)
        $idx = $i
        if ($null -eq $info) {
            $bar.Enabled = $false
            $val = New-Object System.Windows.Forms.Label
            $val.Text = '不支持'
            $val.ForeColor = ConvertFrom-Hex '#C42B1C'
        } else {
            $span = [int]($info.Max - $info.Min)
            $pct = 0
            if ($span -gt 0) { $pct = [Math]::Round(([int]$info.Cur - [int]$info.Min) * 100.0 / $span) }
            $bar.Value = [Math]::Max(0, [Math]::Min(100, $pct))
            $val = New-Object System.Windows.Forms.Label
            $val.Text = ('{0}% ({1})' -f $pct, $info.Cur)
        }
        $val.AutoSize = $false
        $val.Size = New-Object System.Drawing.Size(80, 22)
        $val.Location = New-Object System.Drawing.Point(278, ($y + 6))
        $content.Controls.Add($val)

        # 注意：先赋初值再挂事件，避免初始化触发 ValueChanged 时数组还没就位
        $bar.Add_ValueChanged({
            param($s, $e)
            $script:PendingBright[$idx] = $s.Value
            if ($script:BrightLabels.Count -gt $idx) { $script:BrightLabels[$idx].Text = [string]$s.Value + '%' }
            if ($null -ne $script:BrightTimer -and -not $script:BrightTimer.Enabled) { $script:BrightTimer.Start() }
        })
        $bar.Add_MouseUp({
            param($s, $e)
            # 松开鼠标立即下发，不等防抖
            if ($script:PendingBright.Count -gt 0) { Invoke-ApplyBrightNow }
        })

        $script:BrightBars += $bar
        $script:BrightLabels += $val
        $y += 36
    }
}

# 防抖定时器：拖动过程中只更新数字，200ms 静默后才下发 DDC 命令
$script:BrightTimer = New-Object System.Windows.Forms.Timer
$script:BrightTimer.Interval = 200

function Invoke-ApplyBrightNow {
    $script:BrightTimer.Stop()
    foreach ($key in @($script:PendingBright.Keys)) {
        $i = $key
        $pct = [int]$script:PendingBright[$key]
        $info = $script:BrightInfos[$i]
        if ($null -eq $info) { continue }
        $span = [int]($info.Max - $info.Min)
        $raw = $info.Min
        if ($span -gt 0) { $raw = [uint32]([Math]::Round($pct * $span / 100.0) + [int]$info.Min) }
        Add-Backup ("bright:{0}" -f $i) ('{0};{1};{2}' -f $info.Min, $info.Cur, $info.Max)
        $ok = $false
        try { $ok = [Dxva2]::SetMonitorBrightness($script:Monitors[$i].Handle, $raw) } catch { $ok = $false }
        if (-not $ok) {
            Show-Status ("显示器[{0}] {1}：设置失败。你的显示器可能不支持 DDC/CI，或在显示器菜单里关闭了它。" -f $i, $script:Monitors[$i].Desc) $true
        } else {
            $back = Get-BrightnessInfo $script:Monitors[$i].Handle
            $nowPct = $pct
            if ($null -ne $back -and $span -gt 0) { $nowPct = [Math]::Round(([int]$back.Cur - [int]$info.Min) * 100.0 / $span) }
            Show-Status ("显示器[{0}] 亮度 → {1}%（原始 {2}）" -f $i, $nowPct, $back.Cur)
        }
    }
    $script:PendingBright.Clear()
}

$script:BrightTimer.Add_Tick({ Invoke-ApplyBrightNow })

# ---- 外观：深色/浅色 ----
$y += 8
$lbSec = New-Object System.Windows.Forms.Label
$lbSec.Text = '外观'
$lbSec.ForeColor = ConvertFrom-Hex '#444444'
$lbSec.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9, [System.Drawing.FontStyle]::Bold)
$lbSec.AutoSize = $true
$lbSec.Location = New-Object System.Drawing.Point(14, $y)
$content.Controls.Add($lbSec)
$y += 22

$btnDark = New-Object System.Windows.Forms.Button
$btnDark.Text = '深色'
$btnDark.Size = New-Object System.Drawing.Size(72, 26)
$btnDark.Location = New-Object System.Drawing.Point(18, $y)
$btnDark.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnDark.Cursor = [System.Windows.Forms.Cursors]::Hand
$content.Controls.Add($btnDark)

$btnLight = New-Object System.Windows.Forms.Button
$btnLight.Text = '浅色'
$btnLight.Size = New-Object System.Drawing.Size(72, 26)
$btnLight.Location = New-Object System.Drawing.Point(96, $y)
$btnLight.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnLight.Cursor = [System.Windows.Forms.Cursors]::Hand
$content.Controls.Add($btnLight)

function Update-ThemeButtons {
    if (Get-ThemeIsDark) {
        $btnDark.BackColor = ConvertFrom-Hex '#2F6FED'; $btnDark.ForeColor = [System.Drawing.Color]::White
        $btnLight.BackColor = [System.Drawing.Color]::White; $btnLight.ForeColor = [System.Drawing.Color]::Black
    } else {
        $btnDark.BackColor = [System.Drawing.Color]::White; $btnDark.ForeColor = [System.Drawing.Color]::Black
        $btnLight.BackColor = ConvertFrom-Hex '#2F6FED'; $btnLight.ForeColor = [System.Drawing.Color]::White
    }
}
$btnDark.Add_Click({
    try { Set-ThemeMode $true;  Update-ThemeButtons; Show-Status '已切换到深色模式（应用/开始菜单立刻生效）' }
    catch { Show-Status ('切换深色失败：' + $_.Exception.Message) $true }
})
$btnLight.Add_Click({
    try { Set-ThemeMode $false; Update-ThemeButtons; Show-Status '已切换到浅色模式' }
    catch { Show-Status ('切换浅色失败：' + $_.Exception.Message) $true }
})
Update-ThemeButtons
$y += 34

# ---- 强调色 ----
$lbSec = New-Object System.Windows.Forms.Label
$lbSec.Text = '强调色'
$lbSec.ForeColor = ConvertFrom-Hex '#444444'
$lbSec.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9, [System.Drawing.FontStyle]::Bold)
$lbSec.AutoSize = $true
$lbSec.Location = New-Object System.Drawing.Point(14, $y)
$content.Controls.Add($lbSec)
$y += 22

$accentPresets = @('#0078D7', '#CA5010', '#8764B8', '#107C10', '#C42B1C', '#038387')
$x = 18
foreach ($hex in $accentPresets) {
    $sw = New-Object System.Windows.Forms.Button
    $sw.Size = New-Object System.Drawing.Size(26, 26)
    $sw.Location = New-Object System.Drawing.Point($x, $y)
    $sw.BackColor = ConvertFrom-Hex $hex
    $sw.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $sw.FlatAppearance.BorderColor = ConvertFrom-Hex '#AAAAAA'
    $sw.Cursor = [System.Windows.Forms.Cursors]::Hand
    $hexVal = $hex
    $sw.Add_Click({
        try {
            $c = [System.Drawing.ColorTranslator]::FromHtml($hexVal)
            Set-AccentColor $c.R $c.G $c.B
            Show-Status ('强调色 → ' + $hexVal)
        } catch { Show-Status ('设置强调色失败：' + $_.Exception.Message) $true }
    })
    $content.Controls.Add($sw)
    $x += 32
}
$btnPick = New-Object System.Windows.Forms.Button
$btnPick.Text = '取色…'
$btnPick.Size = New-Object System.Drawing.Size(72, 26)
$btnPick.Location = New-Object System.Drawing.Point(($x + 6), ($y - 0))
$btnPick.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnPick.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnPick.Add_Click({
    try {
        $dlg = New-Object System.Windows.Forms.ColorDialog
        $dlg.FullOpen = $true
        if ($dlg.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
            Set-AccentColor $dlg.Color.R $dlg.Color.G $dlg.Color.B
            Show-Status ('强调色 → RGB(' + $dlg.Color.R + ',' + $dlg.Color.G + ',' + $dlg.Color.B + ')')
        }
    } catch { Show-Status ('取色失败：' + $_.Exception.Message) $true }
})
$content.Controls.Add($btnPick)
$y += 34

# ---- 任务栏对齐 ----
$lbSec = New-Object System.Windows.Forms.Label
$lbSec.Text = '任务栏'
$lbSec.ForeColor = ConvertFrom-Hex '#444444'
$lbSec.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9, [System.Drawing.FontStyle]::Bold)
$lbSec.AutoSize = $true
$lbSec.Location = New-Object System.Drawing.Point(14, $y)
$content.Controls.Add($lbSec)
$y += 22

$btnCenter = New-Object System.Windows.Forms.Button
$btnCenter.Text = '居中'
$btnCenter.Size = New-Object System.Drawing.Size(72, 26)
$btnCenter.Location = New-Object System.Drawing.Point(18, $y)
$btnCenter.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnCenter.Cursor = [System.Windows.Forms.Cursors]::Hand
$content.Controls.Add($btnCenter)

$btnLeft = New-Object System.Windows.Forms.Button
$btnLeft.Text = '左对齐'
$btnLeft.Size = New-Object System.Drawing.Size(72, 26)
$btnLeft.Location = New-Object System.Drawing.Point(96, $y)
$btnLeft.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnLeft.Cursor = [System.Windows.Forms.Cursors]::Hand
$content.Controls.Add($btnLeft)

$btnCenter.Add_Click({
    try {
        Set-TaskbarAlign 1
        Show-Status '任务栏 → 居中（若没变，到「高级」里点重启资源管理器）'
    } catch { Show-Status ('任务栏设置失败：' + $_.Exception.Message) $true }
})
$btnLeft.Add_Click({
    try {
        Set-TaskbarAlign 0
        Show-Status '任务栏 → 左对齐（若没变，到「高级」里点重启资源管理器）'
    } catch { Show-Status ('任务栏设置失败：' + $_.Exception.Message) $true }
})
$y += 34

# ---- 壁纸 ----
$lbSec = New-Object System.Windows.Forms.Label
$lbSec.Text = '壁纸'
$lbSec.ForeColor = ConvertFrom-Hex '#444444'
$lbSec.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9, [System.Drawing.FontStyle]::Bold)
$lbSec.AutoSize = $true
$lbSec.Location = New-Object System.Drawing.Point(14, $y)
$content.Controls.Add($lbSec)

$wpNow = ''
try { $wpNow = Get-Wallpaper } catch { }
$lbWp = New-Object System.Windows.Forms.Label
$lbWp.Text = '当前：' + $(if ($wpNow.Length -gt 38) { '…' + $wpNow.Substring($wpNow.Length - 36) } elseif ($wpNow) { $wpNow } else { '（未知）' })
$lbWp.ForeColor = ConvertFrom-Hex '#888888'
$lbWp.AutoSize = $false
$lbWp.Size = New-Object System.Drawing.Size(330, 18)
$lbWp.Location = New-Object System.Drawing.Point(18, ($y + 20))
$content.Controls.Add($lbWp)
$y += 42

$btnWpPick = New-Object System.Windows.Forms.Button
$btnWpPick.Text = '选择图片…'
$btnWpPick.Size = New-Object System.Drawing.Size(88, 26)
$btnWpPick.Location = New-Object System.Drawing.Point(18, $y)
$btnWpPick.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnWpPick.Cursor = [System.Windows.Forms.Cursors]::Hand
$content.Controls.Add($btnWpPick)

$btnWpRot = New-Object System.Windows.Forms.Button
$btnWpRot.Text = '轮换：关'
$btnWpRot.Size = New-Object System.Drawing.Size(88, 26)
$btnWpRot.Location = New-Object System.Drawing.Point(112, $y)
$btnWpRot.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnWpRot.Cursor = [System.Windows.Forms.Cursors]::Hand
$content.Controls.Add($btnWpRot)

$btnWpRestore = New-Object System.Windows.Forms.Button
$btnWpRestore.Text = '还原壁纸'
$btnWpRestore.Size = New-Object System.Drawing.Size(88, 26)
$btnWpRestore.Location = New-Object System.Drawing.Point(206, $y)
$btnWpRestore.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnWpRestore.Cursor = [System.Windows.Forms.Cursors]::Hand
$content.Controls.Add($btnWpRestore)

$btnWpPick.Add_Click({
    try {
        $dlg = New-Object System.Windows.Forms.OpenFileDialog
        $dlg.Filter = '图片 (*.jpg;*.jpeg;*.png;*.bmp)|*.jpg;*.jpeg;*.png;*.bmp'
        if ($dlg.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
            Set-WallpaperFile $dlg.FileName
            $lbWp.Text = '当前：' + $dlg.FileName
            Show-Status '壁纸已更换'
        }
    } catch { Show-Status ('换壁纸失败：' + $_.Exception.Message) $true }
})
$btnWpRestore.Add_Click({
    try {
        $bp = ''
        if (Test-Path $script:BackupFile) {
            $d = Get-Content $script:BackupFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($d.values.PSObject.Properties['wallpaper']) { $bp = [string]$d.values.wallpaper }
        }
        if ($bp) {
            $null = [W2]::SystemParametersInfo(0x0014, 0, $bp, 0x03)
            $lbWp.Text = '当前：' + $(if ($bp.Length -gt 38) { '…' + $bp.Substring($bp.Length - 36) } else { $bp })
            Show-Status '壁纸已还原'
        } else {
            Show-Status '没有记录过原壁纸（本次会话还没换过壁纸）' $true
        }
    } catch { Show-Status ('还原壁纸失败：' + $_.Exception.Message) $true }
})

# 轮换
$script:RotateTimer = New-Object System.Windows.Forms.Timer
$script:RotateTimer.Interval = 600000   # 10 分钟
$script:RotateFolder = ''
$btnWpRot.Add_Click({
    if ($script:RotateTimer.Enabled) {
        $script:RotateTimer.Stop()
        $btnWpRot.Text = '轮换：关'
        Show-Status '壁纸轮换已关闭'
    } else {
        $fbd = New-Object System.Windows.Forms.FolderBrowserDialog
        $fbd.Description = '选择壁纸轮换文件夹'
        if ($fbd.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
            $script:RotateFolder = $fbd.SelectedPath
            $script:RotateTimer.Start()
            $btnWpRot.Text = '轮换：开'
            Show-Status ('壁纸轮换已开启（每 10 分钟，来源 ' + $script:RotateFolder + '）')
        }
    }
})
$script:RotateTimer.Add_Tick({
    try {
        $files = @(Get-ChildItem -Path $script:RotateFolder -Include *.jpg, *.jpeg, *.png, *.bmp -File -Recurse -ErrorAction SilentlyContinue)
        if ($files.Count -eq 0) { Show-Status '轮换文件夹里没有图片' $true; return }
        $f = $files[(Get-Random -Maximum $files.Count)].FullName
        Set-WallpaperFile $f
        $lbWp.Text = '当前：' + $(if ($f.Length -gt 38) { '…' + $f.Substring($f.Length - 36) } else { $f })
    } catch { Show-Status ('轮换失败：' + $_.Exception.Message) $true }
})
$y += 36

# ---- 一键还原 ----
$btnRestore = New-Object System.Windows.Forms.Button
$btnRestore.Text = '⟲  一键还原（回到打开工具前的状态）'
$btnRestore.Size = New-Object System.Drawing.Size(344, 32)
$btnRestore.Location = New-Object System.Drawing.Point(18, $y)
$btnRestore.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnRestore.BackColor = ConvertFrom-Hex '#E7F0FF'
$btnRestore.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnRestore.Add_Click({
    try {
        $done = Restore-All
        if ($done) {
            Update-ThemeButtons
            Show-Status '已全部还原'
        } else {
            Show-Status '没有需要还原的改动'
        }
    } catch { Show-Status ('还原失败：' + $_.Exception.Message) $true }
})
$content.Controls.Add($btnRestore)
$y += 42

# ---- 高级（折叠） ----
$lnkAdv = New-Object System.Windows.Forms.LinkLabel
$lnkAdv.Text = '高级 ▸ 主题 / 锁屏 / 重启资源管理器'
$lnkAdv.LinkBehavior = [System.Windows.Forms.LinkBehavior]::HoverUnderline
$lnkAdv.AutoSize = $true
$lnkAdv.Location = New-Object System.Drawing.Point(14, $y)
$content.Controls.Add($lnkAdv)
$y += 24

$advPanel = New-Object System.Windows.Forms.Panel
$advPanel.Size = New-Object System.Drawing.Size(344, 92)
$advPanel.Location = New-Object System.Drawing.Point(14, $y)
$advPanel.Visible = $false
$content.Controls.Add($advPanel)

$btnTheme = New-Object System.Windows.Forms.Button
$btnTheme.Text = '应用 .theme 主题文件…（实验性）'
$btnTheme.Size = New-Object System.Drawing.Size(340, 26)
$btnTheme.Location = New-Object System.Drawing.Point(0, 0)
$btnTheme.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnTheme.Cursor = [System.Windows.Forms.Cursors]::Hand
$advPanel.Controls.Add($btnTheme)

$btnLock = New-Object System.Windows.Forms.Button
$btnLock.Text = '设置锁屏图片…（需要管理员，会弹 UAC）'
$btnLock.Size = New-Object System.Drawing.Size(340, 26)
$btnLock.Location = New-Object System.Drawing.Point(0, 32)
$btnLock.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnLock.Cursor = [System.Windows.Forms.Cursors]::Hand
$advPanel.Controls.Add($btnLock)

$btnRestartExp = New-Object System.Windows.Forms.Button
$btnRestartExp.Text = '重启资源管理器（让任务栏对齐立即生效）'
$btnRestartExp.Size = New-Object System.Drawing.Size(340, 26)
$btnRestartExp.Location = New-Object System.Drawing.Point(0, 64)
$btnRestartExp.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnRestartExp.Cursor = [System.Windows.Forms.Cursors]::Hand
$advPanel.Controls.Add($btnRestartExp)

$lnkAdv.Add_Click({
    $advPanel.Visible = -not $advPanel.Visible
    if ($advPanel.Visible) { $lnkAdv.Text = '高级 ▾ 主题 / 锁屏 / 重启资源管理器' }
    else { $lnkAdv.Text = '高级 ▸ 主题 / 锁屏 / 重启资源管理器' }
})

$btnTheme.Add_Click({
    try {
        $dlg = New-Object System.Windows.Forms.OpenFileDialog
        $dlg.Filter = '主题文件 (*.theme)|*.theme'
        $dlg.InitialDirectory = "$env:LOCALAPPDATA\Microsoft\Windows\Themes"
        if ($dlg.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
            Show-Status '正在调用系统主题引擎…'
            Start-Process rundll32.exe -ArgumentList ('shell32.dll,Control_RunDLL desk.cpl desk,@Themes /Action:OpenTheme /file:"' + $dlg.FileName + '"')
            Show-Status '已提交系统主题引擎（实验性，弹出的预览窗口里点保存即可）'
        }
    } catch { Show-Status ('应用主题失败：' + $_.Exception.Message) $true }
})

$btnLock.Add_Click({
    try {
        $r = [System.Windows.Forms.MessageBox]::Show(
            "锁屏图片走的是组策略键（HKLM），需要管理员权限。`n点「确定」后会弹出 UAC 提升窗口，请手动确认。`n`n还原方法：删除 HKLM\SOFTWARE\Policies\Microsoft\Windows\Personalization 下的 LockScreenImage / LockScreenImageStatus。",
            '需要管理员权限', [System.Windows.Forms.MessageBoxButtons]::OKCancel, [System.Windows.Forms.MessageBoxIcon]::Warning)
        if ($r -ne [System.Windows.Forms.DialogResult]::OK) { return }
        $dlg = New-Object System.Windows.Forms.OpenFileDialog
        $dlg.Filter = '图片 (*.jpg;*.jpeg;*.png;*.bmp)|*.jpg;*.jpeg;*.png;*.bmp'
        if ($dlg.ShowDialog($form) -ne [System.Windows.Forms.DialogResult]::OK) { return }
        $img = $dlg.FileName.Replace("'", "''")
        $cmd = "New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization' -Force | Out-Null; " +
               "Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization' -Name 'LockScreenImage' -Value '$img'; " +
               "Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization' -Name 'LockScreenImageStatus' -Value 1 -Type DWord"
        $proc = Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile', '-Command', $cmd -PassThru -ErrorAction Stop
        $proc.WaitForExit(60000) | Out-Null
        if ($proc.ExitCode -eq 0) { Show-Status '锁屏图片已设置（Win+L 可查看）' }
        else { Show-Status '锁屏设置未完成（UAC 被取消或失败）' $true }
    } catch { Show-Status ('锁屏设置失败：' + $_.Exception.Message) $true }
})

$btnRestartExp.Add_Click({
    try {
        $r = [System.Windows.Forms.MessageBox]::Show('重启资源管理器会关闭并重开任务栏（已打开的资源管理器窗口也会关闭）。继续吗？',
            '重启资源管理器', [System.Windows.Forms.MessageBoxButtons]::OKCancel, [System.Windows.Forms.MessageBoxIcon]::Question)
        if ($r -ne [System.Windows.Forms.DialogResult]::OK) { return }
        Stop-Process -Name explorer -Force
        Start-Sleep -Milliseconds 1200
        if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) { Start-Process explorer }
        Show-Status '资源管理器已重启'
    } catch { Show-Status ('重启资源管理器失败：' + $_.Exception.Message) $true }
})

# ---- 右下角拖拽调大小 ----
$grip = New-Object System.Windows.Forms.Label
$grip.Text = '◢'
$grip.ForeColor = ConvertFrom-Hex '#999999'
$grip.Size = New-Object System.Drawing.Size(20, 20)
$grip.Cursor = [System.Windows.Forms.Cursors]::SizeNWSE
$grip.Anchor = [System.Windows.Forms.AnchorStyles]::Bottom -bor [System.Windows.Forms.AnchorStyles]::Right
$grip.Location = New-Object System.Drawing.Point(($form.Width - 24), ($form.Height - 46))
$form.Controls.Add($grip)
$grip.BringToFront()

$script:resizing = $false
$script:rsStart = $null
$script:rsSize = $null
$grip.Add_MouseDown({
    param($s, $e)
    if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
        $script:resizing = $true
        $script:rsStart = [System.Windows.Forms.Cursor]::Position
        $script:rsSize = $form.Size
    }
})
$grip.Add_MouseMove({
    param($s, $e)
    if ($script:resizing) {
        $p = [System.Windows.Forms.Cursor]::Position
        $w = $script:rsSize.Width  + ($p.X - $script:rsStart.X)
        $h = $script:rsSize.Height + ($p.Y - $script:rsStart.Y)
        if ($w -ge $form.MinimumSize.Width) { $form.Width = $w }
        if ($h -ge $form.MinimumSize.Height) { $form.Height = $h }
    }
})
$grip.Add_MouseUp({ $script:resizing = $false })

$form.Controls.Add($script:Status)
$script:Status.BringToFront()

# ---- 标题栏按钮行为 ----
$btnTop.Add_Click({
    $form.TopMost = -not $form.TopMost
    if ($form.TopMost) { $btnTop.BackColor = ConvertFrom-Hex '#FFB900' }
    else { $btnTop.BackColor = ConvertFrom-Hex '#1E5AD6' }
    Show-Status ($(if ($form.TopMost) { '已置顶' } else { '已取消置顶' }))
})
$btnMin.Add_Click({ $form.Hide() })
$btnClose.Add_Click({ $form.Hide() })   # ✕ 也是收到托盘；真正退出走托盘右键

if ($form.TopMost) { $btnTop.BackColor = ConvertFrom-Hex '#FFB900' }

# ---- 托盘图标 ----
$trayBmp = New-Object System.Drawing.Bitmap 16, 16
$g = [System.Drawing.Graphics]::FromImage($trayBmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.Clear([System.Drawing.Color]::Transparent)
$g.FillEllipse((New-Object System.Drawing.SolidBrush(ConvertFrom-Hex '#2F6FED')), 1, 1, 14, 14)
$g.FillEllipse([System.Drawing.Brushes]::White, 5, 5, 6, 6)
$g.Dispose()
$trayIcon = New-Object System.Windows.Forms.NotifyIcon
$trayIcon.Icon = [System.Drawing.Icon]::FromHandle($trayBmp.GetHicon())
$trayIcon.Text = 'WinCtlPanel 控制面板'
$trayIcon.Visible = $true

$trayMenu = New-Object System.Windows.Forms.ContextMenuStrip
$miShow = $trayMenu.Items.Add('显示面板')
$miTop  = $trayMenu.Items.Add('置顶')
$miTop.CheckOnClick = $true
$null = $trayMenu.Items.Add('-')
$miExit = $trayMenu.Items.Add('退出（不还原改动）')
$trayIcon.ContextMenuStrip = $trayMenu
$miShow.Add_Click({ $form.Show(); $form.Activate() })
$miTop.Add_Click({ $form.TopMost = $miTop.Checked; $btnTop.BackColor = $(if ($form.TopMost) { ConvertFrom-Hex '#FFB900' } else { ConvertFrom-Hex '#1E5AD6' }) })
$miExit.Add_Click({ $script:realExit = $true; $form.Close() })
$trayIcon.Add_MouseClick({
    param($s, $e)
    if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) { $form.Show(); $form.Activate() }
})

# ---- 全局热键 ----
# id 1: Ctrl+Alt+Up 亮度+5   id 2: Ctrl+Alt+Down 亮度-5   id 3: Ctrl+Alt+D 切深色
$null = $form.Handle   # 强制创建句柄
$MOD = [uint32]3        # MOD_CONTROL | MOD_ALT
$hkOk1 = [W2]::RegisterHotKey($form.Handle, 1, $MOD, 0x26)
$hkOk2 = [W2]::RegisterHotKey($form.Handle, 2, $MOD, 0x28)
$hkOk3 = [W2]::RegisterHotKey($form.Handle, 3, $MOD, 0x44)
if (-not ($hkOk1 -and $hkOk2 -and $hkOk3)) {
    Show-Status '部分全局热键注册失败（可能被其他软件占用），浮窗内功能不受影响' $true
}
$form.Add_HotKey({
    param($s, $id)
    try {
        if ($id -eq 1 -or $id -eq 2) {
            $step = 5; if ($id -eq 2) { $step = -5 }
            $mons = $script:Monitors
            for ($i = 0; $i -lt $mons.Count; $i++) {
                $info = Get-BrightnessInfo $mons[$i].Handle
                if ($null -eq $info) { continue }
                $span = [int]($info.Max - $info.Min)
                if ($span -le 0) { continue }
                $pct = [Math]::Round(([int]$info.Cur - [int]$info.Min) * 100.0 / $span) + $step
                $pct = [Math]::Max(0, [Math]::Min(100, $pct))
                $raw = [uint32]([Math]::Round($pct * $span / 100.0) + [int]$info.Min)
                $null = [Dxva2]::SetMonitorBrightness($mons[$i].Handle, $raw)
                if ($i -lt $script:BrightBars.Count) {
                    $script:BrightBars[$i].Value = $pct
                    $script:BrightLabels[$i].Text = [string]$pct + '%'
                }
            }
            Show-Status ('热键：全部显示器亮度 ' + $(if ($step -gt 0) { '+5' } else { '-5' }))
        } elseif ($id -eq 3) {
            Set-ThemeMode (-not (Get-ThemeIsDark))
            Update-ThemeButtons
            Show-Status '热键：已切换深色/浅色'
        }
    } catch { Show-Status ('热键处理失败：' + $_.Exception.Message) $true }
})

# ---- 启动时：检测上次未还原的改动 ----
if (Test-Path $script:BackupFile) {
    $r = [System.Windows.Forms.MessageBox]::Show(
        "检测到上次有未还原的改动（backup.json）。`n`n是 → 立即还原到最初的原始值`n否 → 保留记录，本次「一键还原」仍会还原它们",
        '发现未还原的改动', [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question)
    if ($r -eq [System.Windows.Forms.DialogResult]::Yes) {
        $null = Restore-All
        Update-ThemeButtons
        Show-Status '已还原上次的改动'
    } else {
        Show-Status '保留了上次的备份记录，可用「一键还原」回退'
        # 把文件里已有的键记入内存集合，避免被本次会话的新值覆盖
        try {
            $d = Get-Content $script:BackupFile -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($p in $d.values.PSObject.Properties) { $script:BackupKeysSeen[$p.Name] = $true }
        } catch { }
    }
}

# ---- 退出与位置记忆 ----
function Save-UiState {
    $o = [pscustomobject]@{
        x = $form.Location.X; y = $form.Location.Y
        w = $form.Width; h = $form.Height
        topmost = $form.TopMost
    }
    $o | ConvertTo-Json | Out-File -FilePath $script:UiFile -Encoding UTF8
}

$script:realExit = $false
$form.Add_FormClosing({
    param($s, $e)
    # 只有用户点 ✕/─ 才收到托盘；系统关机等其余关闭原因都走正常退出并保存位置
    if (-not $script:realExit -and $e.CloseReason -eq [System.Windows.Forms.CloseReason]::UserClosing) {
        $e.Cancel = $true; $form.Hide(); return
    }
    Save-UiState
    $null = [W2]::UnregisterHotKey($form.Handle, 1)
    $null = [W2]::UnregisterHotKey($form.Handle, 2)
    $null = [W2]::UnregisterHotKey($form.Handle, 3)
    $trayIcon.Visible = $false
    $trayIcon.Dispose()
    foreach ($m in $script:Monitors) {
        try { $null = [Dxva2]::DestroyPhysicalMonitor($m.Handle) } catch { }
    }
    $script:Mutex.Dispose()
})

# ---- 运行 ----
# AutoScroll 面板在 DPI 缩放下可能带着初始滚动量出现，把亮度节卷出可视区，显式归零
$content.AutoScrollPosition = New-Object System.Drawing.Point(0, 0)
$form.Add_Shown({ $content.AutoScrollPosition = New-Object System.Drawing.Point(0, 0) })
$form.Show()
$form.Activate()
[System.Windows.Forms.Application]::Run($form)
