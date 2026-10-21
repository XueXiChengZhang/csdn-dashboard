#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_csdn_limits.py — 实测 CSDN 的可承受请求速率 (CSDN 改版后要重测)

为什么要这个工具:
  CSDN 的反爬响应是 **HTTP 521** (不是 404/500), 而且它会随着请求量收紧。
  2026-10-21 实测 (同一 IP, 单线程, 连打同一批文章):

      间隔 0.0s  -> 40 篇里第 17 篇起大面积 521   (ok=22/40)
      间隔 1.5s  -> 40/40 全部 200
      间隔 2.0s  -> 40/40 全部 200
      间隔 3.0s  -> 30/30 全部 200

  增量脚本和 v2 现在都用 CSDN_MIN_INTERVAL (默认 2.2s) + 0.6s 抖动。
  如果哪天 CSDN 收紧规则, 表现就是"很多文章抓不到" —— 那时候跑这个工具
  重新测一遍, 把 CSDN_MIN_INTERVAL 调大就行。

用法:
  python scripts/probe_csdn_limits.py --delays 0,1.5,2.0 --n 40
  python scripts/probe_csdn_limits.py --profile-only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

DASHBOARD_DIR = Path(__file__).resolve().parent.parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0")
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9", "Referer": "https://blog.csdn.net/"}


def views_of(html):
    m = re.search(r"viewCountFormat\s*=\s*(\d+)", html or "")
    return int(m.group(1)) if m else None


def load_links(n):
    data = json.loads((DASHBOARD_DIR / "data.json").read_text(encoding="utf-8"))
    links = [p["link"] for s in data.get("students", []) for p in s.get("posts", [])]
    return links[:n]


def probe(delay, urls, label=""):
    sess = requests.Session()
    sess.headers.update(H)
    ok = first_521 = None
    first_521 = None
    t0 = time.time()
    for i, u in enumerate(urls):
        try:
            r = sess.get(u, timeout=15)
            code = r.status_code
            v = views_of(r.text) if code == 200 else None
        except Exception:
            code, v = "EXC", None
        if code == 200 and v is not None:
            ok = (ok or 0) + 1
        elif code == 521 and first_521 is None:
            first_521 = i
            print(f"    [{i}] FIRST 521", flush=True)
        if delay:
            time.sleep(delay)
    el = time.time() - t0
    print(f"  {label}delay={delay}s  ok={ok}/{len(urls)}  "
          f"first521={first_521}  elapsed={el:.0f}s", flush=True)
    return ok, first_521, el


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delays", default="0,1.5,2.0,3.0",
                    help="逗号分隔的间隔秒数, 每个之间会冷却 45 秒")
    ap.add_argument("--n", type=int, default=40, help="每个间隔测多少篇")
    ap.add_argument("--cooldown", type=int, default=45, help="两次实验之间的冷却秒数")
    ap.add_argument("--profile-only", action="store_true", help="只测主页端点")
    args = ap.parse_args()

    delays = [float(x) for x in args.delays.split(",") if x.strip()]
    links = load_links(args.n)

    if args.profile_only:
        print("=== 主页端点 (连打 12 次) ===")
        sess = requests.Session(); sess.headers.update(H)
        good = 0
        for i in range(12):
            r = sess.get("https://blog.csdn.net/liruisklfj?type=blog", timeout=15)
            has = "总访问量" in (r.text or "")
            good += 1 if (r.status_code == 200 and has) else 0
            print(f"  [{i}] status={r.status_code} len={len(r.text or '')} ok={has}")
            time.sleep(0.15)
        print(f"  主页端点: {good}/12 正常")
        return 0

    print(f"=== 文章端点限流实测 (每个间隔 {args.n} 篇) ===")
    results = []
    for i, d in enumerate(delays):
        if i:
            print(f"  (冷却 {args.cooldown}s)")
            time.sleep(args.cooldown)
        results.append((d, probe(d, links, label=f"[{i}] ")))
    print("\n=== 结论 ===")
    for d, (ok, first521, el) in results:
        verdict = "安全" if first521 is None else f"第 {first521} 篇开始被限流"
        print(f"  间隔 {d}s: {ok}/{len(links)} 成功, {verdict}")
    safe = [d for d, (ok, f, el) in results if f is None]
    if safe:
        print(f"\n  建议 CSDN_MIN_INTERVAL 不低于 {min(safe)} 秒")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
