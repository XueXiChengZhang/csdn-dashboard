#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 CSDN 的 .cmd 脚本转成 GBK (系统 ANSI 代码页) + CRLF。

★ 真正的根因 (2026-10-23 定位, 中间误判过两次):
  cmd.exe 读 .cmd 时用**当前**代码页解析每一行。`chcp 65001` 只对它
  **之后**的行生效, 所以把 chcp 放在文件中部救不了前面的中文注释。

  UTF-8 中文被 GBK 错位切开的后果 (实测):
  - `'风控拦掉' 不是内部或外部命令`  (注释碎片被当命令执行)
  - `REM   (实测: 00:00 -> total_views 7,604)` 里的 `->` 被当成
    **输出重定向符** -> 在仓库根目录生成 0 字节的 `total_views` 文件

★ 修法 (三选一, 本脚本用第 3 种):
  1. 注释全部改 ASCII —— 会丢失可读性
  2. 注释全部改英文 —— 同上
  3. **把 .cmd 存成 GBK + CRLF** —— cmd 的原生代码页, 中文注释完整保留,
     零语法错误, 不产生垃圾文件。★ 实测这是唯一可靠的方案。

★ 副作用与对策:
  - commit message 里的中文会变乱码 (git 按 UTF-8 存 blob, 终端按 UTF-8 显示,
    而 cmd 传的是 GBK 字节)。对策: commit message 一律用英文。
  - `git diff` 里 .cmd 显示乱码是正常的 —— blob 就是 GBK 字节, 检出后仍是 GBK。
  - `.gitattributes` 里 `*.cmd text eol=crlf` 保证换行是 CRLF, 不影响编码。

★ 同时会做: 把中文 commit message 换成英文。

★ 第二个根因 (GBK 转换解决后仍剩 2 条语法错误):
  `echo` 行里的**裸括号**会被 cmd 当成块语法:
      echo ... ABORT (没有备份就不跑全量)
  -> "( was unexpected at this time."
  中文和 ASCII 都一样, 与编码无关。修法: 用引号把括号内容包起来
      echo ... ABORT "(没有备份就不跑全量)"
  本脚本会把 echo 行里的裸括号自动加引号。


用法: python fix_cmd_encoding.py [--dry]
"""
import os
import sys

SCRIPTS = [
    "incremental-monitor.cmd",
    "gitee-monitor.cmd",
    "weekly-full-monitor.cmd",
    "daily-monitor-and-push.cmd",
    "setup-schedule-tasks.cmd",
]

# 中文 commit message -> 英文 (GBK 下会乱码)
MSG_FIX = [
    ("data: %date:~0,10% Gitee 刷新", "data: %date:~0,10% Gitee refresh"),
    ("data: %date:~0,10% 增量抓取", "data: %date:~0,10% incremental scrape"),
    ("data: %date:~0,10% 周全量兜底", "data: %date:~0,10% weekly full scrape"),
    ("data: %date:~0,10% 全量抓取", "data: %date:~0,10% full scrape"),
]


def decode_any(raw):
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8/replace"


def convert(path, dry=False):
    with open(path, "rb") as f:
        raw = f.read()
    txt, was = decode_any(raw)

    n_msg = 0
    for a, b in MSG_FIX:
        if a in txt:
            txt = txt.replace(a, b)
            n_msg += 1

    # 修 echo 行的裸括号 (cmd 会当块语法)
    import re
    def _fix_echo(m):
        body = m.group(2)
        stripped = re.sub(r'"[^"]*"', "", body)
        if "(" not in stripped and ")" not in stripped:
            return m.group(0)
        # 把裸括号内容用引号包住
        body2 = re.sub(r"\(([^()]*)\)", r'"(\1)"', body)
        return m.group(1) + body2
    txt = re.sub(r"(?mi)^(\s*echo\s+)(.*)$", _fix_echo, txt)

    # CRLF
    txt = txt.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")

    try:
        out = txt.encode("gbk")
    except UnicodeEncodeError:
        # 有 GBK 编不了的字符 (emoji 等) -> 换成文字形态
        out = txt.encode("gbk", errors="replace")

    if not dry:
        with open(path, "wb") as f:
            f.write(out)
    return was, n_msg, len(out)


def main():
    dry = "--dry" in sys.argv
    here = os.path.dirname(os.path.abspath(__file__))
    for name in SCRIPTS:
        p = os.path.join(here, name)
        if not os.path.exists(p):
            continue
        was, n_msg, size = convert(p, dry)
        print("  %-34s 原编码=%-6s 改message=%d 新大小=%d" % (name, was, n_msg, size))


if __name__ == "__main__":
    main()
