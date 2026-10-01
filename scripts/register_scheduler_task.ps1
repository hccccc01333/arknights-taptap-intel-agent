# 注册/查看/卸载「全天热点追踪」Windows 计划任务
#
# 为什么用计划任务而不是常驻进程：
#   调度器设计成 --once（跑一轮就退出，幂等可重入），由系统定时唤起。
#   比常驻进程可靠：崩了下一分钟自动重来；重启电脑不用记着手动拉起。
#
# 为什么触发间隔（默认 5 分钟）小于探测间隔（15 分钟）：
#   调度器自己会节流（probe_log 里记着上次探测时间），所以调得勤不会多采；
#   但一旦某轮失败，能很快重试，而不是干等一整个 15 分钟。
#
# ★ 为什么用 pythonw.exe 而不是 python.exe（2026-10-01 用户反馈「时不时弹出来」）：
#   python.exe 是**控制台子系统** → 计划任务会为它分配一个控制台 → 每 5 分钟弹一次窗口。
#   pythonw.exe 是 **GUI 子系统** → 无控制台、不弹窗。
#   ⚠️ 只换解释器**会把事情搞更糟**：pythonw 没有控制台可继承，它 spawn 的每个子进程
#      都会被新建一个控制台 → 一轮探测 6 个子进程 = 6 次弹窗。所以还配套了两处改动：
#        · scheduler.py / harness.py 的 subprocess 都带 CREATE_NO_WINDOW（win_env.py）
#        · 三个入口补了标准流兜底（pythonw 下 sys.stdout 为 None，print 会抛异常）
#      两处都在 win_env.py 里，有 tests/test_win_env.py 锁着。
#
# 用法：
#   注册（默认每 5 分钟检查一次）： powershell -ExecutionPolicy Bypass -File scripts/register_scheduler_task.ps1
#   自定义间隔：                     ... -IntervalMinutes 15
#   查看状态：                       ... -Status
#   先干跑验证：                     ... -DryRun
#   卸载：                           ... -Remove

param(
    [string]$TaskName = "ArkIntelScheduler",
    [int]$IntervalMinutes = 5,
    [string]$Python = "",
    [switch]$Remove,
    [switch]$Status,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# 编码统一 —— 不设这两行，Python 子进程的中文输出在 Windows PowerShell 5.1 下会变乱码：
#   Python 写 UTF-8 字节 → PowerShell 按控制台代码页(GBK)解 → 鎺㈡祴鏈埌鏃堕棿…
# 对 -Status 影响最大（报告直接不可读）。任务本身不依赖控制台输出，但本地调试全靠它。
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

# 路径从脚本位置推导 —— 不硬编码中文路径，避免编码损坏
$Root = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $Root "11情报Agent\scheduler.py"

if (-not (Test-Path $Script)) {
    Write-Error "找不到调度器：$Script"
    exit 2
}

# ---- 选解释器：优先用显式传入的，否则用装了依赖的隔离环境 ----
if (-not $Python) {
    $candidates = @(
        "$env:USERPROFILE\.workbuddy\binaries\python\envs\default\Scripts\python.exe",
        (Join-Path $Root ".venv\Scripts\python.exe")
    )
    foreach ($c in $candidates) { if (Test-Path $c) { $Python = $c; break } }
}

if (-not $Python -or -not (Test-Path $Python)) {
    Write-Host "[错误] 未找到可用的 Python 解释器。" -ForegroundColor Red
    Write-Host "  调度器需要 requests 等依赖，不能用手上只有标准库的解释器。"
    Write-Host "  请用 -Python 指定，例如："
    Write-Host "    -Python `"$env:USERPROFILE\.workbuddy\binaries\python\envs\default\Scripts\python.exe`""
    exit 2
}

# ---- 无窗口运行：优先用 pythonw.exe ----
#   ⚠️ 这里只负责「选对解释器」；子进程不弹窗靠 win_env.no_window_kwargs()（见文件头注释）。
$Runner = $Python
$Pw = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
if (Test-Path $Pw) { $Runner = $Pw }

# ---- 查看状态 ----
if ($Status) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) { Write-Host "任务「$TaskName」未注册。"; exit 0 }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "任务名   : $($t.TaskName)"
    Write-Host "状态     : $($t.State)"
    Write-Host "上次运行 : $($info.LastRunTime)  结果码 $($info.LastTaskResult)"
    Write-Host "下次运行 : $($info.NextRunTime)"
    Write-Host ""
    Write-Host "—— 采样健康度 ——"
    & $Python $Script --status
    exit 0
}

# ---- 卸载 ----
if ($Remove) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) { Write-Host "任务「$TaskName」本来就没注册。"; exit 0 }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已卸载任务「$TaskName」。" -ForegroundColor Green
    exit 0
}

# ---- 干跑：只验证调度器本身能不能跑通，不注册 ----
if ($DryRun) {
    Write-Host "解释器 : $Python"
    Write-Host "调度器 : $Script"
    Write-Host "`n>>> 干跑一轮（不真跑爬虫、不写日志）"
    & $Python $Script --once --dry-run
    exit $LASTEXITCODE
}

# ---- 注册 ----
Write-Host "解释器 : $Runner"
Write-Host "（Python : $Python）"
Write-Host "调度器 : $Script"
Write-Host "间隔   : 每 $IntervalMinutes 分钟检查一次（调度器内部按 15 分钟节流）"
if ($Runner -ne $Python) {
    Write-Host "无窗口 : 是（pythonw.exe）—— 不再弹控制台" -ForegroundColor Green
} else {
    Write-Host "无窗口 : 否（未找到 pythonw.exe，每轮会弹一次控制台）" -ForegroundColor Yellow
}
Write-Host "输出   : 无控制台时转到 11情报Agent\state\console.log`n"

$action = New-ScheduledTaskAction `
    -Execute $Runner `
    -Argument "`"$Script`" --once" `
    -WorkingDirectory $Root

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# 默认设置：仅在用户登录时运行（个人项目不需要存密码/提权）
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "全天热点追踪：探测 15min + 深采自适应（11情报Agent/scheduler.py）" `
    -Force | Out-Null

Write-Host "已注册任务「$TaskName」。" -ForegroundColor Green
Write-Host "`n下一步："
Write-Host "  查看状态（含采样健康度）：powershell -ExecutionPolicy Bypass -File scripts/register_scheduler_task.ps1 -Status"
Write-Host "  卸载：                    ... -Remove"
Write-Host "`n提示：跑几轮后用 -Status 看「有变化」列 —— 全是 0 说明采得太密，可放宽到 30min。"
Write-Host "提示：无控制台运行时的输出在 11情报Agent\state\console.log（有控制台时不写）。"
