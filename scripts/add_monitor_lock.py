#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 CSDN 的三个 .cmd 加互斥锁 (用 Python 做锁)。

★ 为什么要加 (2026-10-23 实测):
  05:30 的 Gitee 任务和 00:00 的增量任务如果同时跑, 会互相踩:
  - 两者都写 data.json / snapshot.json (后写的覆盖先写的)
  - 两者都做 git add / commit / push (index 竞争, 后一个的 commit 落空)
  实测 16:42 同时跑: Gitee 任务的业务逻辑全部完成 (980ms, 飞书已发),
  但它的 git commit 被增量任务抢走, 数据没进仓库, 计划任务结果卡在 267009。

★ 为什么锁用 Python 而不是 cmd:
  cmd 里没有可靠的原子 CAS。逐一实测:
  - `mkdir` 目录已存在时仍返回成功 -> 不是 CAS
  - `ren` 目录重命名直接失败 (对 . 开头的名字也不行) -> 不可用
  - `copy` 会覆盖目标 -> 不是 CAS
  - `move` 看起来像 CAS, 但并发实测 5 个进程全部 "acquired" -> 不是原子
  Python 的 `os.open(path, O_CREAT|O_EXCL)` 是真正的原子创建,
  并发 5 个进程实测 1 acquired / 4 busy。

★ 锁的行为:
  - 锁文件放 cache/ 下 (已被 gitignore, 不进仓库)
  - 锁已存在时等待, 最多 2 分钟; 超时则判定为上一次异常退出留下的僵死锁,
    强制接管 (记录 PID, 确认进程真死了才接管)
  - 正常结束时释放

★ 注意: 插入到 .cmd 的注释一律用 ASCII, 保持 CRLF + UTF-8 无 BOM。
  cmd.exe 用系统 ANSI 代码页 (GBK) 解析 .cmd, 非 GBK 字符会让行错位。

用法: python add_monitor_lock.py [--dry]
"""
import os
import sys

SCRIPTS = [
    "incremental-monitor.cmd",
    "gitee-monitor.cmd",
    "weekly-full-monitor.cmd",
]

LOCK_PY = '''# -*- coding: utf-8 -*-
"""互斥锁 (供 CSDN 三个 .cmd 任务共用)。

用法:
    python monitor_lock.py acquire <lockfile> <wait_seconds>
    python monitor_lock.py release <lockfile>

退出码:
    0 = 拿到锁 / 释放成功
    3 = 锁被别人占着 (调用方应直接退出)
"""
import os
import sys
import time

STALE_AFTER = 900  # 15 分钟没释放视为僵死


def _pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            h = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return bool(ok) and code.value == STILL_ACTIVE
        except Exception:
            return True   # 查不到就当活着, 保守
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire(path, wait_seconds):
    deadline = time.time() + wait_seconds
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return 0
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(path)
                pid = int(open(path).read().strip() or 0)
            except (OSError, ValueError):
                age, pid = 0, 0
            if age > STALE_AFTER and not _pid_alive(pid):
                try:
                    os.unlink(path)
                    continue
                except OSError:
                    pass
            if time.time() >= deadline:
                return 3
            time.sleep(5)


def release(path):
    try:
        os.unlink(path)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1]
    p = sys.argv[2]
    if cmd == "acquire":
        sys.exit(acquire(p, int(sys.argv[3]) if len(sys.argv) > 3 else 120))
    elif cmd == "release":
        sys.exit(release(p))
    sys.exit(2)
'''

LOCK_BLOCK = '''REM ------------------------------------------------------------------
REM Mutex lock: the three CSDN tasks must not run at the same time.
REM They all write data.json and all do git add/commit/push.
REM (2026-10-23: running 00:00 and 05:30 together made the Gitee
REM  task's commit silently lose the index race, result stuck at 267009)
REM The lock itself is done in Python because cmd has no atomic CAS:
REM mkdir/ren/copy/move all fail as a lock (5 concurrent processes all
REM "acquired" with move). Python os.open(O_CREAT|O_EXCL) is atomic.
REM ------------------------------------------------------------------
set "LOCKFILE=D:\\WebArchives\\csdn-dashboard\\cache\\monitor.lock"
set "LOCKPY=D:\\WebArchives\\csdn-dashboard\\scripts\\monitor_lock.py"
set "LOCKPYEXE=C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python314\\python.exe"
"%LOCKPYEXE%" "%LOCKPY%" acquire "%LOCKFILE%" 120
if errorlevel 1 (
    echo [%date% %time%] LOCK BUSY, another monitor task is running, exit
    endlocal
    exit /b 0
)
echo [%date% %time%] lock acquired
'''

UNLOCK_BLOCK = '''REM release lock
"%LOCKPYEXE%" "%LOCKPY%" release "%LOCKFILE%" 2>nul
'''


def add_lock(path, dry=False):
    with open(path, "rb") as f:
        raw = f.read()
    txt = raw.decode("utf-8")

    if "LOCKFILE" in txt:
        return "已有锁"

    lines = txt.split("\r\n")
    try:
        idx = next(i for i, l in enumerate(lines) if l.strip().lower().startswith("setlocal"))
    except StopIteration:
        return "找不到 setlocal"

    if "enabledelayedexpansion" not in txt.lower():
        lines[idx] = "setlocal enabledelayedexpansion"

    ins = idx + 1
    while ins < len(lines) and (not lines[ins].strip() or lines[ins].strip().startswith("REM")):
        ins += 1

    new_lines = (lines[:ins]
                 + LOCK_BLOCK.replace("\n", "\r\n").split("\r\n")
                 + lines[ins:])

    out = []
    for l in new_lines:
        if l.strip().lower() == "endlocal":
            out.extend(UNLOCK_BLOCK.replace("\n", "\r\n").split("\r\n"))
        out.append(l)

    data = "\r\n".join(out).encode("utf-8")
    if not dry:
        with open(path, "wb") as f:
            f.write(data)
    return "已加锁"


def main():
    dry = "--dry" in sys.argv
    here = os.path.dirname(os.path.abspath(__file__))

    lockp = os.path.join(here, "monitor_lock.py")
    if not dry:
        with open(lockp, "w", encoding="utf-8", newline="\n") as f:
            f.write(LOCK_PY)
        print("  monitor_lock.py 已写入 (%d chars)" % len(LOCK_PY))

    for name in SCRIPTS:
        p = os.path.join(here, name)
        if not os.path.exists(p):
            continue
        r = add_lock(p, dry)
        print("  %-34s %s" % (name, r))


if __name__ == "__main__":
    main()
