@echo off
chcp 65001 >nul
REM ============================================================================
REM gitee-monitor.cmd — 每日 05:30 的 Gitee 刷新 (只跑 Gitee, 不碰 CSDN)
REM
REM 为什么把 05:30 那个任务从"全量 CSDN"改成"只跑 Gitee":
REM   00:00 和 05:30 都跑全量 CSDN 是这次"阅读量归零"事故的直接原因 ——
REM   第二次全量必然被 CSDN 的 521 风控拦掉, 然后把第一次的好数据覆盖成 0。
REM   (实测: 00:00 -> total_views 7,604;  05:30 -> total_views 1,297)
REM
REM 现在两次任务的分工:
REM   00:00  增量抓 CSDN (最近 4 篇 + 轮转 2 篇 + 个人主页)
REM   05:30  只刷 Gitee (便宜, 不受 CSDN 风控影响, 数据也值得每天刷新)
REM   周日03:00  全量兜底 (修数据漂移/补漏, --no-reuse 强制全部重抓)
REM
REM --gitee-only 模式下 v2 会复用 data.json 里已有的 CSDN posts/stats 原样写回,
REM 不会把 CSDN 的数据冲掉。
REM ============================================================================
REM ★ 必须设 UTF-8 模式: 计划任务跑的时候没有 PYTHONIOENCODING/PYTHONUTF8,
REM   Python 的 stdout 会退到系统 ANSI 代码页 (GBK/936)。日志是重定向到文件的,
REM   于是 print() 遇到 openclaw 输出里的 emoji (U+2705) 就会抛
REM   UnicodeEncodeError: 'gbk' codec can't encode character 'U+2705'
REM   —— 实测 2026-10-21: 飞书其实**已经发成功了**, 只是打印日志那一步炸了,
REM   看起来像发送失败。PYTHONIOENCODING=utf-8 + PYTHONUTF8=1 一起解决。
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
setlocal enabledelayedexpansion
REM ------------------------------------------------------------------
REM Mutex lock: the three CSDN tasks must not run at the same time.
REM They all write data.json and all do git add/commit/push.
REM (2026-10-23: running 00:00 and 05:30 together made the Gitee
REM  task's commit silently lose the index race, result stuck at 267009)
REM The lock itself is done in Python because cmd has no atomic CAS:
REM mkdir/ren/copy/move all fail as a lock (5 concurrent processes all
REM "acquired" with move). Python os.open(O_CREAT|O_EXCL) is atomic.
REM ------------------------------------------------------------------
set "LOCKFILE=D:\WebArchives\csdn-dashboard\cache\monitor.lock"
set "LOCKPY=D:\WebArchives\csdn-dashboard\scripts\monitor_lock.py"
set "LOCKPYEXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
"%LOCKPYEXE%" "%LOCKPY%" acquire "%LOCKFILE%" 120
if errorlevel 1 (
    echo [%date% %time%] LOCK BUSY, another monitor task is running, exit
    endlocal
    exit /b 0
)
echo [%date% %time%] lock acquired

set SCRIPT_DIR=%~dp0
set DASHBOARD_DIR=%SCRIPT_DIR%..
set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe
set LOGDIR=%DASHBOARD_DIR%\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d "%DASHBOARD_DIR%"

REM 1. 只跑 Gitee 阶段
"%PYTHON%" "%SCRIPT_DIR%csdn-monitor-v2.py" --gitee-only --force-now >> "%LOGDIR%\gitee-cron.log" 2>&1
if errorlevel 1 (
    echo [%date% %time%] GITEE-ONLY FAILED
    "%LOCKPYEXE%" "%LOCKPY%" release "%LOCKFILE%" 2>nul
    exit /b 1
)

REM 2. 提交 + push
REM NOTE: must add all 4 files, not just data.json.
REM   The run also writes cache/csdn/snapshot.json, history-real/<date>.json
REM   and reports/csdn-report-<date>.md. Adding only data.json left them
REM   uncommitted, so the dashboard showed stale data after a successful push.
git add data.json cache/csdn/snapshot.json history-real reports
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "data: %date:~0,10% Gitee refresh"
    git -c pack.window=0 -c pack.depth=0 push origin main
    if errorlevel 1 (
        echo [%date% %time%] HTTPS PUSH FAILED, trying SSH...
        git remote set-url origin ssh://git@ssh.github.com:443/XueXiChengZhang/csdn-dashboard.git
        git -c pack.window=0 -c pack.depth=0 push origin main
        REM keep ssh.github.com:443 - both github.com:443 and :22 are unreachable from this host
    )
)

REM NOTE: fetch needs a timeout - without one a network hiccup hangs the whole scheduled task
set "FETCHCMD=$p = Start-Process git -ArgumentList 'fetch','origin','main' -NoNewWindow -PassThru; if (-not $p.WaitForExit(60000)) { $p.Kill(); exit 1 } else { exit $p.ExitCode }"
powershell -NoProfile -Command "%FETCHCMD%" >nul 2>&1
for /f "tokens=*" %%i in ('git rev-parse HEAD') do set LOCAL=%%i
for /f "tokens=*" %%i in ('git rev-parse origin/main') do set REMOTE=%%i
if not "%LOCAL%"=="%REMOTE%" (
    echo [%date% %time%] STILL OUT OF SYNC: local=%LOCAL% remote=%REMOTE%
) else (
    echo [%date% %time%] In sync OK
)

REM release lock
"%LOCKPYEXE%" "%LOCKPY%" release "%LOCKFILE%" 2>nul
endlocal
