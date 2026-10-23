@echo off
REM ============================================================================
REM setup-schedule-tasks.cmd — 重装三个定时任务 (需要管理员权限)
REM
REM 2026-10-21 之前的配置 (事故现场):
REM   \csdn-dashboard-monitor-0000  -> daily-monitor-and-push.cmd  (全量 CSDN)
REM   \csdn-dashboard-monitor-0530  -> daily-monitor-and-push.cmd  (全量 CSDN)
REM   两个都跑全量, 第二次必然被 CSDN 的 521 风控限流, 于是:
REM     00:00 -> total_views 7,604 (好)
REM     05:30 -> total_views 1,297 (被限流, 把好数据覆盖成 0)
REM
REM 现在的配置 (三次任务分工):
REM   00:00        -> incremental-monitor.cmd    增量抓 CSDN (阅读量每天都会涨)
REM   05:30        -> gitee-monitor.cmd          只刷 Gitee, 不碰 CSDN
REM   周日 03:00   -> weekly-full-monitor.cmd    全量兜底 (--no-reuse 强制全刷)
REM
REM ! schtasks 的坑 (来自 memory): /tr 路径含空格或特殊字符时,
REM   直接传 python 路径会报 "无效参数"。所以这里一律用 .cmd 包装文件,
REM   .cmd 里面再写绝对路径的 python.exe。
REM ============================================================================
chcp 65001 >nul
setlocal

set DASH=D:\WebArchives\csdn-dashboard\scripts

net session >nul 2>&1
if errorlevel 1 (
    echo [!] 需要管理员权限, 请用管理员身份运行
    exit /b 1
)

echo [1/3] 00:00 增量抓取 (原来跑的是全量, 这是阅读量归零的元凶之一)
schtasks /change /tn "\csdn-dashboard-monitor-0000" /tr "%DASH%\incremental-monitor.cmd"

echo [2/3] 05:30 只刷 Gitee (原来跑的是全量)
schtasks /change /tn "\csdn-dashboard-monitor-0530" /tr "%DASH%\gitee-monitor.cmd"

echo [3/3] 周日 03:00 全量兜底 (新增)
schtasks /create /tn "\csdn-dashboard-monitor-weekly" /tr "%DASH%\weekly-full-monitor.cmd" /sc weekly /d SUN /st 03:00 /f

echo.
echo === 现在的任务指向 ===
schtasks /query /fo csv /v | findstr /i "csdn-dashboard"
endlocal
