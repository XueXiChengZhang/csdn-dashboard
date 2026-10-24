@echo off
chcp 65001 >nul
REM ============================================================================
REM csdn-catchup.cmd - run missed CSDN crawl after machine boots
REM
REM WHY (learned 2026-10-24):
REM   10-24 09:24 machine was shut down -> the 00:00 CSDN job never ran.
REM   After boot, Windows only re-ran the 05:30 job (gitee-monitor.cmd, Gitee only).
REM   Result: that day's report showed all 34 CSDN students as "reused" (no fresh data).
REM
REM   Windows Task Scheduler does NOT replay missed daily triggers by default,
REM   so we register this as an ONLOGON task that checks and back-fills.
REM
REM LOGIC:
REM   1. Compare snapshot.json mtime date against today
REM   2. If not today -> run incremental-monitor.cmd once
REM   3. incremental-monitor.cmd has its own lock, so no conflict with running jobs
REM
REM NOTE: this file is intentionally ASCII-only. cmd.exe decodes .cmd as GBK on
REM       this machine, and UTF-8 Chinese comments in a .cmd get parsed as
REM       commands (the "---------" bug). Keep all comments in English.
REM ============================================================================
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
setlocal enabledelayedexpansion

set "DASH=D:\WebArchives\csdn-dashboard"
set "LOGDIR=%DASH%\logs"
set "CATCHUP_LOG=%LOGDIR%\catchup.log"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

echo [%date% %time%] ==== catchup start ==== >> "%CATCHUP_LOG%"

set "SNAP=%DASH%\cache\csdn\snapshot.json"
set "TODAY="

if exist "%SNAP%" (
  for /f "usebackq delims=" %%a in (`powershell -NoProfile -Command "(Get-Item '%SNAP%').LastWriteTime.ToString('yyyy-MM-dd')"`) do set "SNAPDAY=%%a"
  for /f "usebackq delims=" %%a in (`powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"`) do set "TODAY=%%a"
  if "!SNAPDAY!"=="!TODAY!" (
    echo [%date% %time%] snapshot already today !SNAPDAY! - skip >> "%CATCHUP_LOG%"
    echo [%date% %time%] ==== catchup skip ==== >> "%CATCHUP_LOG%"
    exit /b 0
  )
  echo [%date% %time%] snapshot is !SNAPDAY!, today is !TODAY! - need backfill >> "%CATCHUP_LOG%"
) else (
  echo [%date% %time%] snapshot.json missing - backfill once >> "%CATCHUP_LOG%"
)

echo [%date% %time%] running incremental-monitor.cmd >> "%CATCHUP_LOG%"
call "%DASH%\scripts\incremental-monitor.cmd" >> "%CATCHUP_LOG%" 2>&1
set RC=%errorlevel%
echo [%date% %time%] backfill finished rc=!RC! >> "%CATCHUP_LOG%"
echo [%date% %time%] ==== catchup done ==== >> "%CATCHUP_LOG%"
exit /b %RC%
