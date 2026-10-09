@echo off
chcp 65001 >nul
cd /d D:\WebArchives\csdn-dashboard

REM 1. 抓数据
C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe scripts\csdn-monitor-v2.py --force-now

REM 2. 如果有变化, commit + push
if exist data.json (
    git add -A
    git diff --cached --quiet
    if errorlevel 1 (
        git commit -m "data: %date:~0,10% 自动更新"
        REM 3. push 带重试 (网络不稳定时)
        git push origin main
        if errorlevel 1 (
            echo [%date% %time%] PUSH FAILED, retrying in 30s...
            timeout /t 30 /nobreak >nul
            git push origin main
        )
    )
)

REM 4. 推送后, 检查是否同步
git fetch origin main >nul 2>&1
for /f "tokens=*" %%i in ('git rev-parse HEAD') do set LOCAL=%%i
for /f "tokens=*" %%i in ('git rev-parse origin/main') do set REMOTE=%%i
if not "%LOCAL%"=="%REMOTE%" (
    echo [%date% %time%] STILL OUT OF SYNC: local=%LOCAL% remote=%REMOTE%
) else (
    echo [%date% %time%] In sync OK
)
