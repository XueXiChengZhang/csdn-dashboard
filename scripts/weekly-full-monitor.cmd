@echo off
REM ============================================================================
REM weekly-full-monitor.cmd — 每周日 03:00 的**全量兜底**
REM
REM 增量抓取的取舍: 为了不撞 CSDN 的 521 风控, 平时每天只重抓
REM   最近 4 篇 + 轮转 2 篇 + 个人主页, 其余文章复用上次值。
REM   好处: 请求数与文章总数无关, 每天 280 个请求就够。
REM   代价: 老文章的数据会慢慢变旧 (轮转一圈大概 4-6 天), 偶尔会有
REM         漏掉的新文章。
REM 所以每周日跑一次全量, 用来:
REM   - 修数据漂移 (把轮转漏掉的都刷一遍)
REM   - 补漏 (发现增量期间漏掉的新文章 / 已删除的文章)
REM   - 校准 (把全部 385 篇的 views/likes/comments/collections 重刷一遍)
REM
REM --no-reuse: 忽略 24h 复用, 强制**每篇都重抓**。没有这个开关的话,
REM   周日全量会把周日增量刚抓过的那几篇当成"24h 内已抓过"而跳过,
REM   全量就名存实亡了。
REM ============================================================================
chcp 65001 >nul
setlocal
set SCRIPT_DIR=%~dp0
set DASHBOARD_DIR=%SCRIPT_DIR%..
set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe
set LOGDIR=%DASHBOARD_DIR%\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d "%DASHBOARD_DIR%"

REM 1. 全量抓取 (含 Gitee), --no-reuse 强制每篇重抓
"%PYTHON%" "%SCRIPT_DIR%csdn-monitor-v2.py" --force-now --no-reuse >> "%LOGDIR%\weekly-full-cron.log" 2>&1
if errorlevel 1 (
    echo [%date% %time%] WEEKLY FULL FAILED
    exit /b 1
)

REM 2. 提交 + push
git add -A
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "data: %date:~0,10% 周全量兜底"
    git -c pack.window=0 -c pack.depth=0 push origin main
    if errorlevel 1 (
        echo [%date% %time%] HTTPS PUSH FAILED, trying SSH...
        git remote set-url origin git@github.com:XueXiChengZhang/csdn-dashboard.git
        git -c pack.window=0 -c pack.depth=0 push origin main
        git remote set-url origin https://github.com:XueXiChengZhang/csdn-dashboard.git
    )
)

git fetch origin main >nul 2>&1
for /f "tokens=*" %%i in ('git rev-parse HEAD') do set LOCAL=%%i
for /f "tokens=*" %%i in ('git rev-parse origin/main') do set REMOTE=%%i
if not "%LOCAL%"=="%REMOTE%" (
    echo [%date% %time%] STILL OUT OF SYNC: local=%LOCAL% remote=%REMOTE%
) else (
    echo [%date% %time%] In sync OK
)
endlocal
