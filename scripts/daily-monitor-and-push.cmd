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
        REM 3. push 优先 HTTPS,失败回退 SSH (Ma: 家里 HTTPS 443 经常被屏蔽,SSH 更稳)
        git push origin main
        if errorlevel 1 (
            echo [%date% %time%] HTTPS PUSH FAILED, trying SSH...
            git remote set-url origin git@github.com:XueXiChengZhang/csdn-dashboard.git
            git push origin main
            git remote set-url origin https://github.com/XueXiChengZhang/csdn-dashboard.git
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
