# ============================================================
#  注册 Windows 定时任务：每天自动抓取 + 推送
#
#  在管理员 PowerShell 里运行一次即可：
#      powershell -ExecutionPolicy Bypass -File scripts\setup-task.ps1
#
#  带参数：
#      -Time "09:00"        每天执行时间（默认 09:00）
#      -TaskName "..."      任务名
#      -Hourly              每小时执行一次（从下一个整点开始）
#      -Unregister          删除任务
#
#  为什么用 PowerShell 而不是 schtasks 命令行：
#  中文路径、引号嵌套、StartIn（工作目录）这三样在 schtasks 里都容易出错。
#  这里用 Register-ScheduledTask 显式设置，避免路径被拆错。
# ============================================================

param(
    [string]$Time = "09:00",
    [string]$TaskName = "Arena Leaderboard Fetch",
    [switch]$Hourly,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatchPath = Join-Path $ScriptDir "run-daily.bat"

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "已删除任务: $TaskName" -ForegroundColor Green
    } else {
        Write-Host "任务不存在: $TaskName" -ForegroundColor Yellow
    }
    exit 0
}

if (-not (Test-Path $BatchPath)) {
    Write-Host "找不到 $BatchPath" -ForegroundColor Red
    exit 1
}

# Prefer the project environment installed by install-deps.ps1.
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $VenvPython) {
    $PythonPath = $VenvPython
} else {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
    if (-not $py) {
        Write-Host "找不到 Python。请安装 Python 3.10+，再运行 scripts\install-deps.ps1。" -ForegroundColor Red
        exit 1
    }
    $PythonPath = $py.Source
}
Write-Host "Python: $PythonPath"

$Action = New-ScheduledTaskAction `
    -Execute $BatchPath `
    -WorkingDirectory $ScriptDir

if ($Hourly) {
    $NextHour = (Get-Date).Date.AddHours((Get-Date).Hour + 1)
    $Trigger = New-ScheduledTaskTrigger `
        -Once `
        -At $NextHour `
        -RepetitionInterval (New-TimeSpan -Hours 1) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
} else {
    # 每天固定时间触发（默认 09:00，可以用 -Time 修改）
    $Trigger = New-ScheduledTaskTrigger -Daily -At $Time
}
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew

# -Interactive：必须在**用户会话**里跑。
# 抓取要启动真实 Firefox 窗口过 Cloudflare，SYSTEM 账户没有桌面会话，
# 浏览器起不来或指纹异常。所以用交互式，并设成登录后可用。
$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "任务已存在，先删除旧的..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "arena.ai 榜单抓取并推送（本机 IP 过 Cloudflare）" | Out-Null

Write-Host ""
Write-Host "✓ 已注册任务: $TaskName" -ForegroundColor Green
if ($Hourly) {
    Write-Host "  每小时执行一次（从 $NextHour 开始）"
} else {
    Write-Host "  每天 $Time 执行"
}
Write-Host "  脚本: $BatchPath"
Write-Host ""
Write-Host "常用命令:"
Write-Host "  立即测试运行:  Start-ScheduledTask -TaskName `"$TaskName`""
Write-Host "  查看上次结果:  (Get-ScheduledTaskInfo -TaskName `"$TaskName`").LastTaskResult"
Write-Host "  查看运行日志:  Get-Content '$ScriptDir\_out\run_and_push.log' -Tail 30"
Write-Host "  删除任务:      powershell -File scripts\setup-task.ps1 -Unregister"
Write-Host ""
Write-Host "注意：任务需要你登录 Windows 才会执行（因为要弹出浏览器过 CF）。" -ForegroundColor Cyan
