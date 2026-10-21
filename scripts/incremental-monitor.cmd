@echo off
REM ============================================================================
REM incremental-monitor.cmd — 每日 00:00 的 CSDN **增量**抓取 (Ma: 数据应该是增量的)
REM
REM 为什么不跑全量 (2026-10-21 根因):
REM   旧的两个定时任务 (00:00 和 05:30) 都跑 csdn-monitor-v2.py --force-now,
REM   也就是每天两次全量重抓 385 篇文章。实测 CSDN 的风控响应是 HTTP 521:
REM     - 不加延迟连打 40 篇 -> 第 17 篇开始大面积 521
REM     - 间隔 2.0s 连打 40 篇 -> 40/40 全部 200
REM   所以第二次全量必然被限流, 于是:
REM     00:00 那次 total_views = 7,604  (好)
REM     05:30 那次 total_views = 1,297  (被限流, 把好数据覆盖成 0)
REM
REM 现在 00:00 跑增量:
REM   - 每个学生重抓最近 4 篇 (阅读量增长最快的地方)
REM   - 每个学生轮转重抓 2 篇老文章 (保证老文章也会更新)
REM   - 其余文章复用上次值, 按天轮转刷新
REM   - 顺带抓一次个人主页总访问量 (权威的 total_views)
REM   请求数与文章总数无关, 34 个学生约 280 个请求。
REM ============================================================================
chcp 65001 >nul
REM ★ 必须设 UTF-8 模式: 计划任务跑的时候没有 PYTHONIOENCODING/PYTHONUTF8,
REM   Python 的 stdout 会退到系统 ANSI 代码页 (GBK/936)。日志是重定向到文件的,
REM   于是 print() 遇到 openclaw 输出里的 emoji (U+2705) 就会抛
REM   UnicodeEncodeError: 'gbk' codec can't encode character '✅'
REM   —— 实测 2026-10-21: 飞书其实**已经发成功了**, 只是打印日志那一步炸了,
REM   看起来像发送失败。PYTHONIOENCODING=utf-8 + PYTHONUTF8=1 一起解决。
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
setlocal
set SCRIPT_DIR=%~dp0
set DASHBOARD_DIR=%SCRIPT_DIR%..
set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe
set LOGDIR=%DASHBOARD_DIR%\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d "%DASHBOARD_DIR%"

REM 1. 增量抓取 (--max-minutes 18 是给"补历史"层的全局时间预算, 保证不会跑飞)
"%PYTHON%" "%SCRIPT_DIR%csdn_incremental.py" --all --recent 4 --rotate 2 --max-minutes 18 >> "%LOGDIR%\incremental-cron.log" 2>&1
if errorlevel 1 (
    echo [%date% %time%] INCREMENTAL FAILED, exit=%%errorlevel%%
    exit /b 1
)

REM 2. 提交 + push (HTTPS -> SSH 兜底; pack flags 避开 Windows 上的 push OOM)
git add data.json
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "data: %date:~0,10% 增量抓取"
    git -c pack.window=0 -c pack.depth=0 push origin main
    if errorlevel 1 (
        echo [%date% %time%] HTTPS PUSH FAILED, trying SSH...
        git remote set-url origin git@github.com:XueXiChengZhang/csdn-dashboard.git
        git -c pack.window=0 -c pack.depth=0 push origin main
        git remote set-url origin https://github.com/XueXiChengZhang/csdn-dashboard.git
    )
)

REM 3. 推送后检查是否同步 (skill: push 静默失败是常见坑)
git fetch origin main >nul 2>&1
for /f "tokens=*" %%i in ('git rev-parse HEAD') do set LOCAL=%%i
for /f "tokens=*" %%i in ('git rev-parse origin/main') do set REMOTE=%%i
if not "%LOCAL%"=="%REMOTE%" (
    echo [%date% %time%] STILL OUT OF SYNC: local=%LOCAL% remote=%REMOTE%
) else (
    echo [%date% %time%] In sync OK
)
endlocal
