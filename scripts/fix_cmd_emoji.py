#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修掉 .cmd 里的非 GBK 字符 (emoji), 消除 cmd.exe 语法错误。

★ 根因 (2026-10-23 实测定位, 花了 4 轮才找到):
  cmd.exe 读 .cmd 文件时用系统 ANSI 代码页 (GBK/936) 解析。
  GBK 编不了的字符 (✅ U+2705, ⚠ U+26A0) 会让 cmd 把这一行错位切开,
  于是 REM 注释的后半截被当成命令执行:
      'M' is not recognized as an internal or external command

★ 已排除的误判 (别再往这几个方向查):
  - 不是编码问题: 纯中文注释在 GBK 和 UTF-8 下都正常
  - 不是 chcp 65001 的问题: 它在第 2 行也救不了 (cmd 逐行读, 已在用 ANSI 解析)
  - 不是换行符问题: CRLF 文件正常, 纯 LF 才会炸 (本仓库本来就是 CRLF)
  真正的原因只有一个: 那一行里有 GBK 编不了的字符。

修法: 把 emoji 换成文字形态 (U+2705 -> "U+2705", ⚠ -> "!")。
注释本来就是给人看的, 不需要真 emoji。

★ 注意: 改写时必须保持 CRLF 和 UTF-8 无 BOM, 否则会引入新问题。

用法: python fix_cmd_emoji.py [--dry]
"""
import os
import sys

SCRIPTS = [
    "incremental-monitor.cmd",
    "gitee-monitor.cmd",
    "weekly-full-monitor.cmd",
    "daily-monitor-and-push.cmd",
]

REPL = {
    "\u2705": "U+2705",   # ✅
    "\u26a0": "!",       # ⚠
    "\u274c": "U+274C",   # ❌
    "\u2b50": "U+2B50",   # ⭐
    "\U0001f504": "U+1F504",  # 🔄
    "\U0001f6a8": "U+1F6A8",  # 🚨
}


def gbk_unsafe(txt):
    bad = set()
    for ch in txt:
        if ord(ch) < 128:
            continue
        try:
            ch.encode("gbk")
        except UnicodeEncodeError:
            bad.add(ch)
    return bad


def fix(path, dry=False):
    with open(path, "rb") as f:
        raw = f.read()
    txt = raw.decode("utf-8")
    bad = gbk_unsafe(txt)
    if not bad:
        return 0, set()
    out = txt
    for ch in bad:
        out = out.replace(ch, REPL.get(ch, "U+%04X" % ord(ch)))
    # 保持 CRLF + UTF-8 无 BOM
    data = out.encode("utf-8")
    if b"\r\n" not in data and b"\n" in data:
        data = data.replace(b"\n", b"\r\n")
    if not dry:
        with open(path, "wb") as f:
            f.write(data)
    return len(bad), bad


def main():
    dry = "--dry" in sys.argv
    here = os.path.dirname(os.path.abspath(__file__))
    for name in SCRIPTS:
        p = os.path.join(here, name)
        if not os.path.exists(p):
            continue
        n, bad = fix(p, dry)
        if n:
            print(f"  {name:34s} 替换 {n} 个非GBK字符: {sorted(bad)}")
        else:
            print(f"  {name:34s} 干净")


if __name__ == "__main__":
    main()
