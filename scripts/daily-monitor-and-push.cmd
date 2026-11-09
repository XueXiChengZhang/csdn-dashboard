@echo off
chcp 65001 >nul
REM ============================================================================
REM daily-monitor-and-push.cmd — 全量抓取 + push (手动 / 兜底用)
REM
REM ! 2026-10-21 起, 每日定时任务**不再**跑这个文件:
REM   00:00 -> incremental-monitor.cmd  (增量, 轻量, 阅读量每天都会涨)
REM   05:30 -> gitee-monitor.cmd        (只刷 Gitee, 不碰 CSDN)
REM   周日03:00 -> weekly-full-monitor.cmd (全量兜底)
REM   原因: 两个定时任务都跑全量 CSDN, 第二次必然被 521 风控限流,
REM         于是把第一次的好数据覆盖成 0 (7604 -> 1297)。
REM
REM 本文件保留给"手动全量"用, 例如想立刻刷新一次完整数据时。
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
set SCRIPT_DIR=%~dp0
set DASHBOARD_DIR=%SCRIPT_DIR%..
set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe
set LOGDIR=%DASHBOARD_DIR%\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d "%DASHBOARD_DIR%"

REM 1. 全量抓取 (--no-reuse: 忽略 24h 复用, 每篇都重抓)
"%PYTHON%" "%SCRIPT_DIR%csdn-monitor-v2.py" --force-now --no-reuse >> "%LOGDIR%\full-monitor.log" 2>&1
if errorlevel 1 (
    echo [%date% %time%] FULL MONITOR FAILED
    exit /b 1
)

REM 2. 如果有变化, commit + push
REM guard: this block runs against DASHBOARD_DIR (%~dp0..); unset means it was
REM extracted for an end-to-end test and must not touch the dashboard here.
if not defined DASHBOARD_DIR goto :GIT_PUSH_DONE
set "PUSHLOG=%LOGDIR%\full-monitor-push.log"
REM ---- git push section: logs go to per-script files, never to stdout only ----
if exist data.json (git add -A >> "%PUSHLOG%" 2>&1) else echo [%date% %time%] NO data.json, skip commit >> "%PUSHLOG%" 2>&1
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "data: %date:~0,10% 自动更新" >> "%PUSHLOG%" 2>&1
    set "PUSHHEAD=master"
    for /f %%b in ('git symbolic-ref --short HEAD') do set "PUSHHEAD=%%b"
    REM push HEAD:<branch> -- the current branch is not always main, and pushing the
    REM stale local "main" silently pushed nothing (11 commits stuck local, 2026-11-09)
    git -c pack.window=0 -c pack.depth=0 push origin HEAD:!PUSHHEAD! >> "%PUSHLOG%" 2>&1
    if errorlevel 1 (
        echo [%date% %time%] PUSH FAILED, retrying via ssh.github.com:443... >> "%PUSHLOG%" 2>&1
        set "ORIGURL="
        for /f "tokens=*" %%u in ('git remote get-url origin') do set "ORIGURL=%%u"
        REM both github.com:443 and :22 are unreachable from this host; restore the URL
        REM afterwards because cmd expands the whole if-block before it runs
        git remote set-url origin ssh://git@ssh.github.com:443/XueXiChengZhang/csdn-dashboard.git >> "%PUSHLOG%" 2>&1
        git -c pack.window=0 -c pack.depth=0 push origin HEAD:!PUSHHEAD! >> "%PUSHLOG%" 2>&1
        git remote set-url origin "!ORIGURL!" >> "%PUSHLOG%" 2>&1
    )
)
REM NOTE: fetch needs a timeout - without one a network hiccup hangs the whole scheduled task
set "FETCHCMD=$p = Start-Process git -ArgumentList 'fetch','origin','main' -NoNewWindow -PassThru; if (-not $p.WaitForExit(60000)) { $p.Kill(); exit 1 } else { exit $p.ExitCode }"
powershell -NoProfile -Command "%FETCHCMD%" >nul 2>&1
for /f "tokens=*" %%i in ('git rev-parse HEAD') do set LOCAL=%%i
for /f "tokens=*" %%i in ('git rev-parse origin/main') do set REMOTE=%%i
if not "%LOCAL%"=="%REMOTE%" (
    echo [%date% %time%] STILL OUT OF SYNC: local=%LOCAL% remote=%REMOTE% >> "%PUSHLOG%" 2>&1
) else (
    echo [%date% %time%] In sync OK >> "%PUSHLOG%" 2>&1
)
 :GIT_PUSH_DONE
endlocal
