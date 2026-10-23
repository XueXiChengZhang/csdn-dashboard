#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 fetch 超时那段改成 cmd 能正确解析的写法。

★ 问题: 多行 `powershell -NoProfile -Command ^` + 换行 + `)` 会让 cmd 的
  括号配对错乱 —— 后面的 `)` 被当成命令的一部分, 报
  "'xxx' is not recognized as an internal or external command"。

★ 另外: REM 注释里有中文时, cmd 在某些代码页下会把整行当命令执行。

修法:
  - PowerShell 命令放进一个变量, 单行调用 (不用 ^ 续行)
  - 注释全部改成 ASCII
  - 三层嵌套 if 改成 goto 标签 (cmd 的 else 只能跟最近的 if 配对, 很容易错)
"""
import os
import re
import sys

SCRIPTS = [
    "incremental-monitor.cmd",
    "gitee-monitor.cmd",
    "weekly-full-monitor.cmd",
    "daily-monitor-and-push.cmd",
]

FETCH_OLD = (
    "REM fetch \u5e26 60s \u8d85\u65f6 \u2014\u2014 \u65e0\u8d85\u65f6\u7684 fetch \u4f1a\u5728\u7f51\u7edc\u6296\u52a8\u65f6\u6c38\u4e45\u5361\u4f4f\u6574\u4e2a\u8ba1\u5212\u4efb\u52a1\n"
    "powershell -NoProfile -Command ^\n"
    "  \"$p = Start-Process git -ArgumentList 'fetch','origin','main' -NoNewWindow -PassThru; "
    "if (-not $p.WaitForExit(60000)) { $p.Kill(); exit 1 } else { exit $p.ExitCode }\" >nul 2>&1"
)
FETCH_NEW = (
    "REM NOTE: fetch needs a timeout - without one a network hiccup hangs the whole scheduled task\n"
    "set \"FETCHCMD=$p = Start-Process git -ArgumentList 'fetch','origin','main' -NoNewWindow -PassThru; "
    "if (-not $p.WaitForExit(60000)) { $p.Kill(); exit 1 } else { exit $p.ExitCode }\"\n"
    "powershell -NoProfile -Command \"%FETCHCMD%\" >nul 2>&1"
)

# 常见的中文 REM 注释 -> ASCII
REM_FIXES = {
    "REM \u2605 \u5fc5\u987b\u662f ssh.github.com:443 \u2014\u2014 \u672c\u673a\u5230 github.com:22 \u4e0d\u53ef\u8fbe":
        "REM NOTE: must be ssh.github.com:443 - port 22 and github.com:443 are unreachable from this host",
    "REM   (\u53ea\u6709 443 \u901a, \u89c1 MEMORY.md \u7684 GitHub \u7f51\u7edc\u7ea6\u5b9a)":
        "REM   (see GitHub network notes in MEMORY.md)",
    "REM \u4fdd\u6301 ssh.github.com:443 \u2014\u2014 \u672c\u673a\u5230 github.com:443 \u4e0e :22 \u90fd\u4e0d\u53ef\u8fbe":
        "REM keep ssh.github.com:443 - both github.com:443 and :22 are unreachable from this host",
    "git remote set-url origin https://github.com/XueXiChengZhang/csdn-dashboard.git":
        "REM keep ssh.github.com:443 - both github.com:443 and :22 are unreachable from this host",
}

# 三层嵌套 if 换成 goto
SYNC_OLD = (
    'for /f "tokens=*" %%i in (\'git rev-parse HEAD\') do set LOCAL=%%i\n'
    'for /f "tokens=*" %%i in (\'git rev-parse origin/main\') do set REMOTE=%%i\n'
    'if not "%LOCAL%"=="" if not "%REMOTE%" if "%LOCAL%"=="%REMOTE%" (\n'
    '    echo [%date% %time%] In sync OK\n'
    ') else (\n'
    '    echo [%date% %time%] NOT IN SYNC: local=%LOCAL% remote=%REMOTE%\n'
    ')\n'
    'endlocal'
)
SYNC_NEW = (
    'set "LOCAL="\n'
    'set "REMOTE="\n'
    'for /f "tokens=*" %%i in (\'git rev-parse HEAD\') do set "LOCAL=%%i"\n'
    'for /f "tokens=*" %%i in (\'git rev-parse origin/main\') do set "REMOTE=%%i"\n'
    'if "%LOCAL%"=="" goto nosync\n'
    'if "%REMOTE%"=="" goto nosync\n'
    'if "%LOCAL%"=="%REMOTE%" (\n'
    '    echo [%date% %time%] In sync OK\n'
    '    endlocal\n'
    '    exit /b 0\n'
    ')\n'
    ':nosync\n'
    'echo [%date% %time%] NOT IN SYNC: local=%LOCAL% remote=%REMOTE%\n'
    'endlocal\n'
    'exit /b 0'
)


def fix(path, dry=False):
    with open(path, encoding="utf-8", errors="replace") as f:
        c = f.read()
    orig = c
    n = 0
    if FETCH_OLD in c:
        c = c.replace(FETCH_OLD, FETCH_NEW); n += 1
    for a, b in REM_FIXES.items():
        if a in c:
            c = c.replace(a, b); n += 1
    if SYNC_OLD in c:
        c = c.replace(SYNC_OLD, SYNC_NEW); n += 1
    if c != orig and not dry:
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(c)
    return n


def main():
    dry = "--dry" in sys.argv
    here = os.path.dirname(os.path.abspath(__file__))
    for name in SCRIPTS:
        p = os.path.join(here, name)
        if not os.path.exists(p):
            continue
        n = fix(p, dry)
        print(f"  {name:34s} {n} 处修改" if n else f"  {name:34s} 无需改")


if __name__ == "__main__":
    main()
