# -*- coding: utf-8 -*-
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
