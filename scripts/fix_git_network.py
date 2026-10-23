#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一修掉 CSDN 监控脚本里的两个 GitHub 网络问题。

★ 2026-10-23 实测发现:
  1. SSH fallback 用的是 github.com:22 —— **本机到 22 端口不可达**,
     只有 ssh.github.com:443 通 (见 MEMORY.md 的 GitHub 网络约定)。
     所以那个 fallback 其实是死路, push 失败后等于没重试。
  2. `git fetch origin main` 没有超时 —— 网络抖动时会永久卡住,
     整个计划任务挂到天亮 (实测 2026-10-23 卡了 13 分钟被我手动杀掉)。

对每个 .cmd 做两处替换:
  - git@github.com:...  →  ssh://git@ssh.github.com:443/...
  - git fetch origin main >nul 2>&1  →  带 60 秒超时的 PowerShell 版本

用法: python fix_git_network.py [--dry]
"""
import os
import sys

SCRIPTS = [
    "incremental-monitor.cmd",
    "gitee-monitor.cmd",
    "weekly-full-monitor.cmd",
    "daily-monitor-and-push.cmd",
]

FETCH_OLD = "git fetch origin main >nul 2>&1"
FETCH_NEW = (
    "REM fetch 带 60s 超时 —— 无超时的 fetch 会在网络抖动时永久卡住整个计划任务\n"
    "powershell -NoProfile -Command ^\n"
    "  \"$p = Start-Process git -ArgumentList 'fetch','origin','main' -NoNewWindow -PassThru; "
    "if (-not $p.WaitForExit(60000)) { $p.Kill(); exit 1 } else { exit $p.ExitCode }\" >nul 2>&1"
)

URL_OLD = "git@github.com:XueXiChengZhang/csdn-dashboard.git"
URL_NEW = "ssh://git@ssh.github.com:443/XueXiChengZhang/csdn-dashboard.git"


def fix(path, dry=False):
    with open(path, encoding="utf-8", errors="replace") as f:
        c = f.read()
    orig = c
    n_url = c.count(URL_OLD)
    c = c.replace(URL_OLD, URL_NEW)
    n_fetch = 0
    if FETCH_OLD in c and FETCH_NEW not in c:
        n_fetch = c.count(FETCH_OLD)
        c = c.replace(FETCH_OLD, FETCH_NEW)
    # 恢复原 URL 那行如果它还在 (有些脚本 push 完会把 URL 改回去)
    c = c.replace("git remote set-url origin https://github.com/XueXiChengZhang/csdn-dashboard.git",
                  "REM 保持 ssh.github.com:443 —— 本机到 github.com:443 与 :22 都不可达")
    changed = c != orig
    if changed and not dry:
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(c)
    return changed, n_url, n_fetch


def main():
    dry = "--dry" in sys.argv
    here = os.path.dirname(os.path.abspath(__file__))
    for name in SCRIPTS:
        p = os.path.join(here, name)
        if not os.path.exists(p):
            print(f"  跳过 {name} (不存在)")
            continue
        changed, n_url, n_fetch = fix(p, dry)
        if changed:
            print(f"  {'[dry] ' if dry else ''}已修 {name}: URL x{n_url}, fetch x{n_fetch}")
        else:
            print(f"  无需改 {name}")


if __name__ == "__main__":
    main()
