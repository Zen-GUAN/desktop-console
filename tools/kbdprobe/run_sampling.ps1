# 键盘延迟探针 · 一键采样（采 60 秒 → 出报告 → 打开输出目录）
# 用法: powershell -ExecutionPolicy Bypass -File run_sampling.ps1 [-Seconds 60] [-NoOpen]
# 隐私: 只记录 VK 数字 / 时间戳 / 事件类型，绝不记录按键内容（详见 README.md）
param(
    [int]$Seconds = 60,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
chcp 65001 > $null

$dir = $PSScriptRoot
if (-not $dir) { $dir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$script = Join-Path $dir "kbdprobe.py"
if (-not (Test-Path $script)) {
    Write-Host "[错误] 找不到 $script" -ForegroundColor Red
    exit 2
}

# 找 Python（优先 py 启动器，避开 Windows Store 的 python3 假别名）
$py = $null
foreach ($c in @("py", "python", "python3")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") { $py = $cmd.Source; break }
}
if (-not $py) {
    Write-Host "[错误] 未找到 Python（需 3.8+，仅标准库）。请安装并加入 PATH。" -ForegroundColor Red
    exit 2
}

$hint = if ($Seconds -gt 0) { "$Seconds 秒" } else { "Ctrl+C 手动停止" }
Write-Host "=== 键盘延迟探针 · 采样 $hint ==="
Write-Host "隐私：只记录 VK 数字/时间戳/事件类型，绝不记录按键内容。"
Write-Host "期间请正常打字 / 游戏（想测粘滞就长按几个键），到点自动出报告。"
Write-Host ""

if ($Seconds -gt 0) {
    & $py $script --seconds $Seconds
} else {
    & $py $script
}
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Host "[错误] kbdprobe 退出码 $code（安装钩子失败或参数问题，见 README）" -ForegroundColor Red
    exit $code
}

$samples = Join-Path $dir "samples"
if (-not $NoOpen) {
    Start-Process explorer.exe -ArgumentList "`"$samples`""
}
Write-Host ""
Write-Host "CSV 与报告已写入: $samples"
