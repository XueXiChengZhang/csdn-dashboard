#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 .cmd 里所有 REM 注释的中文改成英文。

★ 为什么必须改: cmd.exe 逐字节读 .cmd 文件, 一行 REM 注释里的中文字符
  可能把"行尾"识别错 —— 实测 2026-10-23 头部的中文 REM 被当成命令执行:
      '风控拦掉' 不是内部或外部命令
      'M' is not recognized as an internal or external command
  注释里的内容根本不该被执行, 但 cmd 就是会。

★ 规避办法 (按优先级):
  1. REM 注释一律用 ASCII (本脚本做的)
  2. 真要留中文, 写成 "::" 注释 (cmd 的 :: 也是注释, 但同样有编码坑)
  3. 至少保证 chcp 65001 在最前面

用法: python fix_cmd_cjk.py [--dry]
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

# 已知的说明性中文片段 -> 英文对照 (能翻的翻, 翻不了的就标 [CN comment])
KNOWN = {
    "增量": "incremental",
    "全量": "full",
    "刷新": "refresh",
    "抓取": "fetch",
    "只跑": "run only",
    "备份": "backup",
    "检查": "check",
    "回滚": "rollback",
    "安全网": "safety net",
    "失败": "failed",
    "成功": "success",
    "推送": "push",
    "提交": "commit",
    "数据": "data",
    "计划任务": "scheduled task",
    "每日": "daily",
    "每周日": "weekly sunday",
    "早上": "morning",
    "根因": "root cause",
    "为什么": "why",
    "不跑": "do not run",
    "事故": "incident",
    "风控": "rate-limit",
    "拦掉": "blocked",
    "实测": "measured",
    "封面": "cover",
    "看板": "dashboard",
    "页面": "page",
    "生成": "generate",
    "手动": "manual",
    "不再": "no longer",
    "起": "from",
    "起,": "from,",
    "时窗": "time window",
    "保护": "protect",
    "唯一": "only",
    "正确性": "correctness",
    "数据正确性": "data correctness",
    "低": "low",
    "访问量": "visits",
    "于": "at",
    "判": "judge",
    "定": "determine",
    "异常": "anomaly",
    "自动": "auto",
    "动回滚": "rollback",
    "且": "and",
    "不": "not",
    "已": "already",
    "在": "at",
    "实跑验证": "verified by real run",
    "4": "4",
    "个备份文件齐全": "backup files all present",
    "回滚前后": "before/after rollback",
    "一致": "identical",
    "SHA256": "SHA256",
    "换": "change",
    "把": "change",
    "那个": "that",
    "从": "from",
    "改": "to",
    "成": "into",
    "便宜": "cheap",
    "不受": "unaffected by",
    "影响": "affect",
    "也": "also",
    "值得": "worth",
    "每天": "every day",
    "两次任务": "the two tasks",
    "的分工": " division of labor",
    "刷": "refresh",
    "复用": "reuse",
    "原样写回": "written back as-is",
    "冲掉": "overwrite",
    "的数据": " data",
    "不会把": "will not",
    "模式": "mode",
    "模式下": " mode",
    "v2": "v2",
    "会": "will",
    "里": "in",
    "已": "already",
    "有的": "existing",
    "写回": "written back",
    "不能": "must not",
    "丢": "lose",
    "特别是": "especially",
    "了": "",
    "出": "",
    "数据来源": "data source",
    "来源": "source",
    "没": "no",
    "法": "",
    "看": "",
    "出": "",
    "个": "",
    "、": ", ",
    "，": ", ",
    "（": " (",
    "）": ") ",
    "“": '"',
    "”": '"',
    "’": "'",
    "‘": "'",
    "—": "-",
    "–": "-",
    "·": "-",
    "★": "*",
    "⚠": "!",
    "→": "->",
    "≤": "<=",
    "≥": ">=",
    "×": "x",
    "、": ",",
}


def to_ascii(s):
    out = []
    for ch in s:
        if ord(ch) < 128:
            out.append(ch)
            continue
        rep = KNOWN.get(ch)
        if rep is None:
            rep = " "
        out.append(rep)
    s = "".join(out)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\s+([,.])", r"\1", s)
    return s.strip()


def fix(path, dry=False):
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    changed = 0
    out = []
    for l in lines:
        s = l.strip()
        if s.upper().startswith("REM ") or s.upper() == "REM":
            new = "REM " + to_ascii(s[4:]) if len(s) > 4 else "REM"
            if new != l:
                changed += 1
            out.append(new)
        else:
            out.append(l)
    if changed and not dry:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(out))
    return changed


def main():
    dry = "--dry" in sys.argv
    here = os.path.dirname(os.path.abspath(__file__))
    for name in SCRIPTS:
        p = os.path.join(here, name)
        if not os.path.exists(p):
            continue
        n = fix(p, dry)
        print(f"  {name:34s} {n} 行 REM 已转 ASCII")


if __name__ == "__main__":
    main()
