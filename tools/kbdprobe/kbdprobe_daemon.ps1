# kbdprobe_daemon.ps1 - 键盘探针常驻（全天记录）
# ASCII-only 注释区以外用中文说明；逻辑简单：起 python 探针，挂了就重启，日志写 daemon.log。
# 隐私：探针本身只写 VK 数字/时间戳/事件类型（见 kbdprobe.py 顶部红线）。
param(
    [string]$Root = "D:\workspace\桌面控制台\tools\kbdprobe",
    [int]$RestartDelaySec = 5
)

$ErrorActionPreference = "Continue"
$script = Join-Path $Root "kbdprobe.py"
$outDir = Join-Path $Root "samples\daily"
$log = Join-Path $Root "daemon.log"
$pidFile = Join-Path $Root "daemon.pid"

function Log($m) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Add-Content -Path $log -Value $line -Encoding utf8
}

if (-not (Test-Path $script)) { Log "missing $script"; exit 2 }
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$py = $null
foreach ($c in @("py", "python")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") { $py = $cmd.Source; break }
}
if (-not $py) { Log "no python"; exit 2 }

Set-Content -Path $pidFile -Value $PID -Encoding ascii
Log "daemon start pid=$PID py=$py out=$outDir"

while ($true) {
    try {
        $p = Start-Process -FilePath $py -ArgumentList @("`"$script`"", "--out", "`"$outDir`"") -PassThru -WindowStyle Hidden
        Log "probe started pid=$($p.Id)"
        Wait-Process -Id $p.Id -ErrorAction SilentlyContinue
        Log "probe exited code=$($p.ExitCode)"
    } catch {
        Log ("error: " + $_.Exception.Message)
    }
    Start-Sleep -Seconds $RestartDelaySec
}
