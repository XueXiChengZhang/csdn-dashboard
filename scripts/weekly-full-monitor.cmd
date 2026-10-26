@echo off
chcp 65001 >nul
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
REM
REM ★★★ R7 安全网 (2026-10-22) ★★★
REM   这个任务**从未跑过一次** (上次运行 1999/11/30), 下次 2026/10/25 03:00。
REM   而 --no-reuse 关掉的正是 24h 复用保护 —— 那是 2026-10-21 把
REM   阅读量从 7,604 打成 1,297 的那个动作。同一条写入路径的故障已经
REM   发生 7 次 (09-22 -67% / 09-30 -67% / 10-01 -39% / 10-02 -33% /
REM   10-12 -100% / 10-19 -100% / 10-21 -83%), 10-16 那次 00:00 算出
REM   50,686 被覆盖成 9,609, 仪表盘少了 81%。
REM   它还用的是 `git add -A` —— 任何残缺结果都会被直接 commit + push。
REM
REM   所以本文件加了三道闸 (全部在 push 之前):
REM     0) 备份   —— 跑之前把 data.json / snapshot.json / 当日归档存一份
REM                  (带时间戳, 留最近 8 份, 在 cache/backup/)
REM     1) 校验   —— 总阅读 < 上次 60% / 失败学生占比 > 20% /
REM                  总文章 < 上次 70%  -> 任一成立就判不合格
REM                  (比 apply_data_protection 的 C 臂严: C 臂只挡
REM                   "总阅读 < 30%", 掉 40% 它挡不住)
REM     2) 回滚   —— 校验不过 (或抓取直接报错) 就把备份原样恢复,
REM                  **并且不 commit、不 push**
REM
REM   为什么**保留** --no-reuse 全量 (而不是改成限范围 --recent 8 --rotate 4):
REM     全量能修复长期滞后的文章数据 (增量只碰最近 4 篇 + 轮转 2 篇,
REM     老文章的 views 可能停在几周前), 限范围只是回避问题 —— 真出事了
REM     数据还是错的, 而且错得没人知道。全量 + 严格的回滚校验 =
REM     "敢跑全量, 但跑坏了自动退回去", 这才是根治。
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
set GUARDLOG=%LOGDIR%\weekly-guard.log
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d "%DASHBOARD_DIR%"

REM ---------------------------------------------------------------------------
REM 0. R7 安全网 —— 备份 (没有备份就不敢跑)
REM ---------------------------------------------------------------------------
if exist "%DASHBOARD_DIR%\cache\backup\LATEST.txt" del /q "%DASHBOARD_DIR%\cache\backup\LATEST.txt"
"%PYTHON%" "%SCRIPT_DIR%weekly_guard.py" --dir "%DASHBOARD_DIR%" backup --keep 8 >> "%GUARDLOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] WEEKLY BACKUP FAILED - ABORT "(没有备份就不跑全量)"
    "%LOCKPYEXE%" "%LOCKPY%" release "%LOCKFILE%" 2>nul
    exit /b 1
)
set BACKUP_DIR=
if exist "%DASHBOARD_DIR%\cache\backup\LATEST.txt" set /p BACKUP_DIR=<"%DASHBOARD_DIR%\cache\backup\LATEST.txt"
if not defined BACKUP_DIR (
    echo [%date% %time%] WEEKLY BACKUP PATH UNKNOWN - ABORT
    "%LOCKPYEXE%" "%LOCKPY%" release "%LOCKFILE%" 2>nul
    exit /b 1
)
echo [%date% %time%] weekly backup: %BACKUP_DIR%

REM ---------------------------------------------------------------------------
REM 1. 全量抓取 (含 Gitee), --no-reuse 强制每篇重抓
REM ---------------------------------------------------------------------------
"%PYTHON%" "%SCRIPT_DIR%csdn-monitor-v2.py" --force-now --no-reuse >> "%LOGDIR%\weekly-full-cron.log" 2>&1
if errorlevel 1 (
    echo [%date% %time%] WEEKLY FULL FAILED - 回滚到备份
    "%PYTHON%" "%SCRIPT_DIR%weekly_guard.py" --dir "%DASHBOARD_DIR%" rollback --backup "%BACKUP_DIR%" >> "%GUARDLOG%" 2>&1
    "%LOCKPYEXE%" "%LOCKPY%" release "%LOCKFILE%" 2>nul
    exit /b 1
)

REM ---------------------------------------------------------------------------
REM 2. R7 安全网 —— 校验 (必须在 push 之前!)
REM ---------------------------------------------------------------------------
"%PYTHON%" "%SCRIPT_DIR%weekly_guard.py" --dir "%DASHBOARD_DIR%" check --backup "%BACKUP_DIR%" >> "%GUARDLOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] WEEKLY VALIDATION FAILED - 回滚到备份, 不 commit 不 push
    "%PYTHON%" "%SCRIPT_DIR%weekly_guard.py" --dir "%DASHBOARD_DIR%" rollback --backup "%BACKUP_DIR%" >> "%GUARDLOG%" 2>&1
    "%LOCKPYEXE%" "%LOCKPY%" release "%LOCKFILE%" 2>nul
    exit /b 1
)

REM ---------------------------------------------------------------------------
REM 3. 提交 + push (过了校验才轮到这一步)
REM ---------------------------------------------------------------------------
REM NOTE: add the 4 data files explicitly, NOT "git add -A".
REM   "git add -A" sweeps in stray files (0-byte junk left by a crashed
REM   cmd.exe parse, editor backups, .pyc) and pushes them to the repo.
REM NOTE: do NOT use %date:~0,10% -- the region format on this host is
REM   "2026/10/26 <weekday>" (date first), but older builds assumed
REM   "<weekday> 2026/10/26" (weekday first). Slicing 10 chars then gives
REM   "<weekday> 2026/10" -- half weekday, half date. Seen in real commits.
REM   Use PowerShell ISO date: independent of region settings.
REM   Must be OUTSIDE the if-block, otherwise %CDATE% will not expand.
set "CDATE="
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "CDATE=%%d"
if not defined CDATE set "CDATE=unknown-date"

git add data.json cache/csdn/snapshot.json history-real reports
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "data: %CDATE% weekly full scrape"
    git -c pack.window=0 -c pack.depth=0 push origin main
    if errorlevel 1 (
        echo [%date% %time%] PUSH FAILED, retrying via ssh.github.com:443...
        REM keep ssh.github.com:443 - both github.com:443 and :22 are unreachable from this host
        git remote set-url origin ssh://git@ssh.github.com:443/XueXiChengZhang/csdn-dashboard.git
        git -c pack.window=0 -c pack.depth=0 push origin main
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
