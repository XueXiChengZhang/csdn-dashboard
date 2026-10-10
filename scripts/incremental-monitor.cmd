@echo off
REM Ma: 增量抓取脚本 (新文章全抓, 老文章只更新 views/likes)
REM 比全量抓取快 10x

setlocal
set SCRIPT_DIR=%~dp0
set DASHBOARD_DIR=%SCRIPT_DIR%..
set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe

REM 1. 增量抓取 (新文章 + 更新 views).
REM    Ma: 增量模式遍历所有页(老文章也持续更新 views),不加 --since 窗口 —
REM         "--since 7" 之前传给脚本但脚本只接受 --all/--student/--max-pages,
REM         导致 cron 启动即抛 SystemExit。已移除。
"%PYTHON%" "%SCRIPT_DIR%csdn_incremental.py" --all

REM 2. 提交 + push (HTTPS -> SSH 兜底,与 daily-monitor-and-push.cmd 保持一致)
cd /d "%DASHBOARD_DIR%"
git add data.json
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "data: %date:~0,10% 增量抓取"
    git push origin main
    if errorlevel 1 (
        echo [%date% %time%] HTTPS PUSH FAILED, trying SSH...
        git remote set-url origin git@github.com:XueXiChengZhang/csdn-dashboard.git
        git push origin main
        git remote set-url origin https://github.com/XueXiChengZhang/csdn-dashboard.git
    )
)

endlocal
