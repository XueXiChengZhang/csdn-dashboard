@echo off
cd /d D:\WebArchives\csdn-dashboard
C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe scripts\csdn-monitor-v2.py --force-now
if exist data.json (
    git add -A
    git diff --cached --quiet
    if errorlevel 1 (
        git commit -m "data: %date:~0,10% ??????"
        git push origin main
    )
)
