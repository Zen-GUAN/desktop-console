# 外接显示器亮度控制（DDC/CI），不依赖 Windows 个性化设置，未激活系统同样可用
#
# 用法：
#   .\brightness.ps1 -Get                 查看当前亮度
#   .\brightness.ps1 -Set 45              设为 45%
#   .\brightness.ps1 -Step +10            调亮 10
#   .\brightness.ps1 -Step -10            调暗 10
#   .\brightness.ps1 -Set 30 -Monitor 1   指定第 1 个显示器（多屏时用）

param(
    [switch]$Get,
    [int]$Set = -1,
    [int]$Step = 0,
    [int]$Monitor = 0
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public class Dxva2Bright {
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
"@

$null = [Dxva2Bright]::Enumerate()

# 收集所有物理显示器
$phys = @()
foreach ($hMon in [Dxva2Bright]::Handles) {
    $cnt = 0
    if (-not [Dxva2Bright]::GetNumberOfPhysicalMonitorsFromHMONITOR($hMon, [ref]$cnt)) { continue }
    if ($cnt -eq 0) { continue }
    $buf = New-Object 'Dxva2Bright+PHYSICAL_MONITOR[]' $cnt
    if (-not [Dxva2Bright]::GetPhysicalMonitorsFromHMONITOR($hMon, $cnt, $buf)) { continue }
    for ($i = 0; $i -lt $cnt; $i++) {
        $phys += [PSCustomObject]@{
            Handle = $buf[$i].hPhysicalMonitor
            Desc   = (-join $buf[$i].szPhysicalMonitorDescription).Trim([char]0).Trim()
        }
    }
}

if ($phys.Count -eq 0) { Write-Error "没有找到可控制的显示器"; exit 1 }
if ($Monitor -ge $phys.Count) { Write-Error "显示器索引 $Monitor 超出范围（共 $($phys.Count) 个）"; exit 1 }

$target = $phys[$Monitor]
$min = 0; $cur = 0; $max = 0
if (-not [Dxva2Bright]::GetMonitorBrightness($target.Handle, [ref]$min, [ref]$cur, [ref]$max)) {
    Write-Error "该显示器不支持 DDC/CI 亮度控制，或显示器菜单里未开启 DDC/CI"
    exit 1
}
$span = $max - $min
function ToPct([int]$v) { if ($span -le 0) { return 0 } [math]::Round(($v - $min) * 100.0 / $span) }
function FromPct([int]$p) { if ($span -le 0) { return $min } [int]([math]::Round($p * $span / 100.0)) + $min }

if ($Get -or ($Set -lt 0 -and $Step -eq 0)) {
    Write-Output ("显示器[{0}] {1}  亮度 {2}%  (原始 {3}, 范围 {4}-{5})" -f $Monitor, $target.Desc, (ToPct $cur), $cur, $min, $max)
    exit 0
}

$newPct = if ($Set -ge 0) {
    [Math]::Max(0, [Math]::Min(100, $Set))
} else {
    [Math]::Max(0, [Math]::Min(100, (ToPct $cur) + $Step))
}

$newRaw = [uint32](FromPct $newPct)
if (-not [Dxva2Bright]::SetMonitorBrightness($target.Handle, $newRaw)) {
    Write-Error "设置失败（显示器可能拒绝该值，或 DDC/CI 被关闭）"
    exit 1
}

Start-Sleep -Milliseconds 150
$null = [Dxva2Bright]::GetMonitorBrightness($target.Handle, [ref]$min, [ref]$cur, [ref]$max)
Write-Output ("亮度 → {0}%  (原始 {1})" -f (ToPct $cur), $cur)
