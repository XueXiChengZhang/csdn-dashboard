@echo off
REM Ma: 增量抓取脚本 (新文章全抓, 老文章只更新 views/likes)
REM 比全量抓取快 10x

setlocal
set SCRIPT_DIR=%~dp0
set DASHBOARD_DIR=%SCRIPT_DIR%..
set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe

REM 1. 增量抓取 (新文章 + 更新 views)
"%PYTHON%" "%SCRIPT_DIR%csdn_incremental.py" --all --since 7

REM 2. 提交 + push
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
