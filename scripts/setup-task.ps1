# ============================================================
#  注册 Windows 定时任务：每天自动抓取 + 推送
#
#  在管理员 PowerShell 里运行一次即可：
#      powershell -ExecutionPolicy Bypass -File scripts\setup-task.ps1
#
#  带参数：
#      -Time "09:00"        每天执行时间（默认 09:00）
#      -TaskName "..."      任务名
#      -Unregister          删除任务
#
#  为什么用 PowerShell 而不是 schtasks 命令行：
#  中文路径、引号嵌套、StartIn（工作目录）这三样在 schtasks 里都容易出错。
#  这里用 Register-ScheduledTask 显式设置，避免路径被拆错。
# ============================================================

param(
    [string]$Time = "09:00",
    [string]$TaskName = "Arena Leaderboard Fetch",
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

# python 必须在 PATH 里；这里先验证，免得任务跑起来才失败
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "找不到 python。请先把 Python 加进 PATH。" -ForegroundColor Red
    exit 1
}
Write-Host "Python: $($py.Source)"

$Action = New-ScheduledTaskAction `
    -Execute $BatchPath `
    -WorkingDirectory $ScriptDir

# 每天固定时间触发。错开整点（下面 -Time 默认 09:00 可以自己改）
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time

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
Write-Host "  每天 $Time 执行"
Write-Host "  脚本: $BatchPath"
Write-Host ""
Write-Host "常用命令:"
Write-Host "  立即测试运行:  Start-ScheduledTask -TaskName `"$TaskName`""
Write-Host "  查看上次结果:  (Get-ScheduledTaskInfo -TaskName `"$TaskName`").LastTaskResult"
Write-Host "  查看运行日志:  Get-Content '$ScriptDir\_out\run_and_push.log' -Tail 30"
Write-Host "  删除任务:      powershell -File scripts\setup-task.ps1 -Unregister"
Write-Host ""
Write-Host "注意：任务需要你登录 Windows 才会执行（因为要弹出浏览器过 CF）。" -ForegroundColor Cyan
