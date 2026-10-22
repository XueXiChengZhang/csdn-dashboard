#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
csdn_incremental.py — CSDN **真增量**抓取 (Ma: "数据应该是增量的啊")

════════════════════════════════════════════════════════════════════════════
为什么必须有这个脚本 (2026-10-21 根因分析)
════════════════════════════════════════════════════════════════════════════
旧的两个定时任务 (00:00 和 05:30) 都跑 `csdn-monitor-v2.py --force-now`,
也就是**两次全量重抓 385 篇文章**。实测:

    00:00 那次 -> total_views = 7,604  ✅
    05:30 那次 -> total_views = 1,297  ❌  ← 把好数据覆盖成 0

第二次必然被限流 —— 实测 CSDN 的风控响应是 **HTTP 521**:
    - 不加延迟连打 40 篇 -> 第 17 篇开始大面积 521
    - 间隔 2.0s 连打 40 篇 -> 40/40 全部 200
所以"每天全量重抓 385 篇"这个动作本身, 就是把数据抓坏的元凶。

════════════════════════════════════════════════════════════════════════════
增量的语义 (Ma 关心的核心)
════════════════════════════════════════════════════════════════════════════
增量 ≠ "省请求"。增量的正确含义是 **用可接受的成本, 让阅读量持续更新**。

以前的实现 (Bug A) 写的是:

    # 老文章: 不再标记 _needs_stats_update
    # P0 #3 修复: 老文章不重抓 stats — 重新发 HTTP = 浪费 + 容易被限流.
    pass

这是把"省请求"和"拿到新数据"搞混了。**老文章的阅读量恰恰是会增长的**
(实测: 李瑞的个人总访问量 7254 -> 7272 -> 7297, 一天涨了几十)。
不重抓 = 阅读量永远停在第一次抓到的值 = Ma 说的"阅读量不增长"。

现在每天对**每个学生**做这些事:

  A. 列表抓取 (翻页找所有可见文章)      → 用来发现新文章
  B. **新文章** (CSDN 有, data.json 没有) → 全字段 + 全 stats 抓取
  C. **最近 N 篇老文章** (按 pubDate 倒序, N 默认 4) → **必须重抓 stats**
     理由: 阅读量增长高度集中在最新发布的文章上, 这是变化最快的数据。
  D. **轮转 M 篇老文章** (M 默认 2)      → **必须重抓 stats**
     理由: 老文章增长慢, 但不能完全不更新 (那就退化成 Bug A 了)。
           按天轮转, 保证每篇老文章每隔几天都会被刷新一次, 不遗漏。
  E. **其余老文章**                     → 复用上次值 (views 原样保留)
     理由: 这些文章基本不再增长, 复用是合理的取舍; 它们会在 D 的轮转里
           周期性地被重新校准, 也会在每周日的全量兜底里被完全重刷。
  F. **个人主页**抓一次"总访问量"        → 权威的 total_views (会增长)

总请求数 ≈ 学生数 × (N + M + 1 + 列表页数), **与文章总数无关**。
34 个学生 ≈ 270 个请求 ≈ 11 分钟, 而不是 385 篇全量的 ~16 分钟 + 必然被限流。

════════════════════════════════════════════════════════════════════════════
轮转算法
════════════════════════════════════════════════════════════════════════════
对每个学生, 把"除最近 N 篇以外"的老文章按 (pubDate, link) 稳定排序得到池子,
然后:

    offset = (today_ordinal * M) % len(pool)
    picked = pool[offset:offset+M]  (环绕)

today_ordinal 用 `datetime.now().toordinal()` (连续递增的整数), 所以:
  - 同一天多次运行结果一致 (幂等)
  - 每天自动向后移动 M 格
  - 每篇文章最多 ceil(len(pool)/M) 天被轮转刷新一次
34 个学生、平均 11 篇/人的规模下, 一轮完整轮转约 4-6 天。

用法:
  python scripts/csdn_incremental.py --all
  python scripts/csdn_incremental.py --student liruisklfj
  python scripts/csdn_incremental.py --all --recent 5 --rotate 2
  python scripts/csdn_incremental.py --all --dry-run      # 只抓不写
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = SCRIPT_DIR.parent
DATA_PATH = DASHBOARD_DIR / "data.json"
LOG_PATH = DASHBOARD_DIR / "logs" / f"incremental-{datetime.now().strftime('%Y-%m-%d')}.log"

sys.path.insert(0, str(SCRIPT_DIR))
from csdn_stats import (  # noqa: E402
    RateLimiter,
    article_id_from_link,
    parse_article_stats,
    parse_profile_views,
)

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

# ---------- stdout 编码兜底 (必须在 import 之后, 且在任何 print 之前) ----------
# 计划任务 / 手动运行时 stdout 可能是 GBK(936), 非 ASCII 会抛 UnicodeEncodeError。
# ★ 本脚本的 log() 里全是 emoji (⚠️👁️🆕♻️), 在 GBK 下会直接崩在抓取中途。
#   更糟的是崩之前的那几行 print 会被上层 except 吞掉 -> 静默丢数据。
def safe_print(msg, **kwargs):
    """打印任何内容都不会因为编码问题炸掉。"""
    try:
        print(msg, **kwargs)
    except UnicodeEncodeError:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"), **kwargs)
        except Exception:
            pass


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0")
CSDN_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "Referer": "https://blog.csdn.net/",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# 限流: 实测 2.0s 间隔 40/40 通过, 0s 间隔第 17 篇起大面积 521
CSDN_MIN_INTERVAL = float(os.environ.get("CSDN_MIN_INTERVAL", "2.2"))
CSDN_JITTER = float(os.environ.get("CSDN_JITTER", "0.6"))
LIMITER = RateLimiter(min_interval=CSDN_MIN_INTERVAL, jitter=CSDN_JITTER)

# ★ 521 (CSDN 风控) 命中后的冷却秒数。
#   521 是 IP 级封禁, 持续分钟级 —— 5~6 秒的冷却等于没冷却
#   (实测 6/7 篇两次尝试全挂)。默认 45 秒。
CSDN_521_COOLDOWN = float(os.environ.get("CSDN_521_COOLDOWN", "45"))

REQUESTS = 0

# 连续失败多少次就放弃这个学生的 stats (交给保护逻辑兜底, 别死磕)
MAX_CONSECUTIVE_FAILURES = 5


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    safe_print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def csdn_get(url, timeout=15, retries=1):
    """带全局限流 + 计数的 GET。521 = CSDN 风控, 会触发全局冷却。

    ★ 2026-10-22 (R2): 521 的冷却从 5~6 秒改成 **45 秒**。
      原因: CSDN 的 521 是 **IP 级风控, 封禁持续分钟级**, 不是"下个请求
      就恢复"的软限流。实测 (2026-10-22): 只冷却 5~6 秒时, 6/7 篇文章
      两次尝试**全挂** —— 封禁根本没解除就重试, 当然还是 521, 等于白烧
      请求。45 秒是分钟级封禁的量级, 冷却完再试才有意义。
      副作用: 45 秒 × 6 次 ≈ 多花 4.5 分钟, 整轮 24→29 分钟,
      任务上限 2 小时, 没有问题。
    """
    global REQUESTS
    last_err = None
    for attempt in range(retries + 1):
        LIMITER.wait()
        REQUESTS += 1
        try:
            r = requests.get(url, timeout=timeout, headers=CSDN_HEADERS)
            if r.status_code in (521, 403, 429):
                # 撞风控: 拉长基准间隔 (自适应退避) + 按封禁时长冷却
                LIMITER.escalate(1.5)
                last_err = f"HTTP {r.status_code}"
                if attempt < retries:
                    LIMITER.penalize(CSDN_521_COOLDOWN)
                    log(f"    ⚠️ {last_err} (风控), {CSDN_521_COOLDOWN:.0f}s 冷却后重试 "
                        f"url={url[:70]}")
                    continue
                # 最后一次尝试: 短一点冷却, 免得白拖慢后面的学生
                LIMITER.penalize(10.0)
                log(f"    ⚠️ {last_err} (风控), 放弃本篇 url={url[:70]}")
            else:
                LIMITER.decay()
            return r
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:60]}"
            if attempt < retries:
                LIMITER.penalize(3.0)
                continue
            break
    class _Err:
        status_code = -1
        text = str(last_err)
    return _Err()


# ---------- 列表抓取 ----------
def fetch_blog_list(username, max_pages=20):
    """翻 CSDN 列表, 拿到所有可见文章 (增量模式用)"""
    all_articles = []
    for page in range(1, max_pages + 1):
        url = f"https://blog.csdn.net/{username}?type=blog&page={page}"
        r = csdn_get(url, timeout=15)
        if r.status_code != 200 or not r.text:
            log(f"  ⚠️ 抓列表 page {page} 失败 (HTTP {r.status_code})")
            break
        html = r.text

        blocks = re.findall(
            r'<article[^>]*class="blog-list-box"[^>]*>(.*?)</article>',
            html, re.DOTALL,
        )
        if not blocks:
            break

        for block in blocks:
            m = re.search(
                r'href="(https?://blog\.csdn\.net/[^/]+/article/details/(\d+))"',
                block,
            )
            if not m:
                continue
            link, article_id = m.group(1), m.group(2)

            m_t = re.search(r'<h[34][^>]*>(.*?)</h[34]>', block, re.DOTALL)
            title = re.sub(r"<[^>]+>", "", m_t.group(1)).strip() if m_t else ""

            m_d = re.search(
                r'<div class="view-time-box"[^>]*>\s*博文更新于\s*([^<·]+?)\s*(?:·|</div>)',
                block,
            )
            pub_raw = m_d.group(1).strip() if m_d else ""

            all_articles.append({
                "article_id": article_id,
                "link": link,
                "title": title,
                "pub_raw": pub_raw,
            })

        # 少于 15 篇说明是最后一页
        if len(blocks) < 15:
            break

    return all_articles


def parse_relative_date(raw):
    """解析 CSDN 的 '3 天前' / '2026.09.20' 格式"""
    if not raw:
        return None
    raw = raw.strip()

    m = re.search(r"(\d{4})[.-](\d{1,2})[.-](\d{1,2})", raw)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    now = datetime.now()
    for unit, delta in (("小时前", timedelta(hours=1)), ("分钟前", timedelta(minutes=1)),
                        ("天前", timedelta(days=1)), ("周前", timedelta(weeks=1)),
                        ("月前", timedelta(days=30))):
        m = re.search(rf"(\d+)\s*{unit}", raw)
        if m:
            return now - delta * int(m.group(1))
    return None


# ---------- 单篇 stats ----------
def fetch_article_stats(article_id, username):
    """抓单篇文章的 views/likes/comments/collections。

    用公共解析模块 scripts/csdn_stats.py (和 v2 共用同一套正则)。
    旧版这里用的是 `class="read-count"` / `like-count` / `comment-count` /
    `collect-count` 四个正则, 实测后三个**一次都没匹配上**,
    而 `read-count` 只在约 1/3 的页面上出现 —— 已全部废弃。
    """
    url = f"https://blog.csdn.net/{username}/article/details/{article_id}"
    r = csdn_get(url, timeout=15)
    if r.status_code != 200 or not r.text or len(r.text) < 500:
        log(f"    ⚠️ 抓 {article_id} 失败 (HTTP {r.status_code}, len={len(r.text or '')})")
        return None
    parsed = parse_article_stats(r.text)
    if parsed["views"] is None:
        log(f"    ⚠️ 抓 {article_id} 页面里没解析出阅读量 (len={len(r.text)})")
        return None
    return {
        "views": parsed["views"],
        "likes": parsed["likes"] or 0,
        "comments": parsed["comments"] or 0,
        "collections": parsed["collections"] or 0,
    }


def fetch_profile_views(username):
    """抓个人主页的总访问量。失败返回 None (不是 0!)"""
    for url in (f"https://blog.csdn.net/{username}?type=blog",
                f"https://blog.csdn.net/{username}"):
        r = csdn_get(url, timeout=15)
        if r.status_code != 200 or not r.text:
            continue
        if len(r.text) < 2000:
            continue
        val = parse_profile_views(r.text)
        if val is not None:
            return val
    return None


# ---------- 增量主逻辑 ----------
def _post_sort_key(p):
    """稳定排序键: pubDate 倒序, 相同则 link 倒序"""
    return (p.get("pubDate") or "", p.get("link") or "")


def incremental_update_student(student, max_pages=20, recent_n=4, rotate_m=2,
                               full=False, dry_run=False, deadline=None,
                               backfill_per_student=3):
    """对一个学生做增量更新。

    full=True 时忽略所有复用 (每周日全量兜底用)。
    """
    username = student.get("csdn_username")
    if not username:
        return {"skipped": "no_username"}

    stats = {
        "new": 0, "new_posts": 0, "updated": 0, "rotated": 0, "reused": 0,
        "missing": 0, "fetched": 0, "failed": 0, "skipped": None,
    }

    # --- A. 列表抓取 ---
    articles = fetch_blog_list(username, max_pages=max_pages)
    if not articles:
        stats["skipped"] = "fetch_failed"
        return stats
    csdn_by_id = {a["article_id"]: a for a in articles}
    csdn_ids = set(csdn_by_id.keys())

    existing_posts = list(student.get("posts", []) or [])
    existing_by_id = {}
    for p in existing_posts:
        aid = article_id_from_link(p.get("link"))
        if aid:
            existing_by_id[aid] = p

    # --- B. 新文章 (CSDN 有, data.json 没有) ---
    new_posts_list = []
    for article_id, cdn_article in csdn_by_id.items():
        if article_id in existing_by_id:
            continue
        pub_date = parse_relative_date(cdn_article["pub_raw"])
        pub_iso = pub_date.isoformat() if pub_date else ""
        new_posts_list.append({
            "title": cdn_article["title"] or f"文章 #{article_id}",
            "link": cdn_article["link"],
            "pubDate": pub_iso,
            "guid": cdn_article["link"],
            "views": None, "likes": None, "comments": None, "collections": None,
            "is_new": True,
            "_needs_stats_update": True,
        })
        stats["new"] += 1

    # --- C/D. 老文章分三类: 最近 N 篇 (重抓) / 轮转 M 篇 (重抓) / 其余 (复用) ---
    old_posts = [p for aid, p in existing_by_id.items() if aid in csdn_ids]
    old_posts.sort(key=_post_sort_key, reverse=True)

    if full:
        # 全量: 所有老文章都重抓
        recent_set = {id(p) for p in old_posts}
        rotate_set = set()
    else:
        recent_set = {id(p) for p in old_posts[:recent_n]}
        # 轮转池 = 除去最近 N 篇的老文章
        pool = old_posts[recent_n:]
        if pool and rotate_m > 0:
            offset = (datetime.now().toordinal() * rotate_m) % len(pool)
            picked = pool[offset:offset + rotate_m]
            if len(picked) < rotate_m:          # 环绕
                picked += pool[:rotate_m - len(picked)]
            rotate_set = {id(p) for p in picked}
        else:
            rotate_set = set()

    # --- 组装要抓 stats 的清单 (按优先级分层) ---
    #  层级 1 new      : 全新文章, 必须全抓
    #  层级 2 recent   : 最近 N 篇, 阅读量增长最快, 必须重抓
    #  层级 3 rotate   : 轮转 M 篇, 保证老文章也会更新
    #  层级 4 backfill : 从没抓到过 stats 的文章 (views 是 None)
    #        —— 这是历史遗留的坑: 2026-10-21 时 385 篇里有 371 篇 views=None,
    #           前端全部显示 "—"。这一层用当天剩余的时间预算慢慢补齐,
    #           补不完的留到下天 (它们还是 None, 所以还在队列里, 会持续推进)。
    to_fetch = []          # (post, 类别)
    for p in new_posts_list:
        to_fetch.append((p, "new"))
    for p in old_posts:
        if id(p) in recent_set:
            to_fetch.append((p, "recent"))
        elif id(p) in rotate_set:
            to_fetch.append((p, "rotate"))
    backfill_posts = [
        p for p in old_posts
        if id(p) not in recent_set and id(p) not in rotate_set
        and not isinstance(p.get("views"), int)
    ]
    # ★ 每个学生每次最多补 N 篇历史。
    #   为什么要限: 2026-10-21 时 385 篇里有 371 篇从来没抓到过 stats
    #   (前端的 "—" 就是这么来的)。如果一次全补, 371 篇 × 2.5s ≈ 15 分钟,
    #   整个跑批会超时。限成每人 3 篇 -> 34 × 3 = 102 个请求 ≈ 4.3 分钟,
    #   剩下的分摊到后面几天补完 (它们的 views 还是 None, 所以一直在队列里)。
    if backfill_per_student > 0:
        backfill_posts = backfill_posts[:backfill_per_student]
    for p in backfill_posts:
        to_fetch.append((p, "backfill"))

    stats["reused"] = len(old_posts) - len(recent_set) - len(rotate_set) - len(backfill_posts)
    stats["backfill"] = len(backfill_posts)

    # --- 抓 stats (带限流 + 连续失败放弃 + 全局时间预算) ---
    consecutive_failures = 0
    abandoned = False
    now_iso = datetime.now().isoformat(timespec="seconds")
    for i, (p, kind) in enumerate(to_fetch, 1):
        if abandoned:
            # 放弃了: 保留旧值 (老文章) / 标 None (新文章)
            continue
        # ★ 全局时间预算 (不是每个学生的!) —— 只对 backfill 层设限,
        #   保证 recent/rotate 一定能跑完 (那是"阅读量要涨"的部分)。
        #   没补完的 backfill 留到下天: 它们的 views 还是 None,
        #   所以还在队列里, 每天都会继续往前推进。
        if kind == "backfill" and deadline and time.time() > deadline:
            stats["budget_exhausted"] = stats.get("budget_exhausted", 0) + 1
            continue
        aid = article_id_from_link(p.get("link"))
        if not aid:
            continue
        s = fetch_article_stats(aid, username)
        if s:
            consecutive_failures = 0
            old_views = p.get("views")
            p["views"] = s["views"]
            p["likes"] = s["likes"]
            p["comments"] = s["comments"]
            p["collections"] = s["collections"]
            p["stats_updated_at"] = now_iso
            stats["fetched"] += 1
            if kind == "new":
                stats["new_posts"] += 1
            elif kind == "recent":
                stats["updated"] += 1
            elif kind == "rotate":
                stats["rotated"] += 1
            else:
                stats["backfilled"] = stats.get("backfilled", 0) + 1
            if old_views is not None and old_views != s["views"]:
                log(f"    [{i}/{len(to_fetch)}] {aid} ({kind}): views {old_views}→{s['views']}")
            elif kind == "new":
                log(f"    [{i}/{len(to_fetch)}] {aid} (new): views={s['views']}")
        else:
            consecutive_failures += 1
            stats["failed"] += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                abandoned = True
                log(f"    ⚠️ 连续 {consecutive_failures} 次失败, 放弃 {username} 剩余文章")

    # --- E. 复用: 其余老文章原样保留 (views 不变) ---
    #     这里不做任何修改 —— 这正是 Bug A 的错误做法;
    #     区别在于: 上面的 recent/rotate 已经把"会增长"的部分重抓了。

    # --- 组装最终 posts ---
    all_posts = new_posts_list + existing_posts
    # 清理内部标记
    for p in all_posts:
        p.pop("_needs_stats_update", None)
        p.pop("_missing_on_csdn", None)
    # 标记 CSDN 上已经删掉的 (不删数据, 只标记, 避免误伤)
    for p in existing_posts:
        aid = article_id_from_link(p.get("link"))
        if aid and aid not in csdn_ids:
            p["_missing_on_csdn"] = True
            stats["missing"] += 1

    all_posts.sort(key=_post_sort_key, reverse=True)

    # --- F. 个人主页总访问量 ---
    profile_views = fetch_profile_views(username)

    # --- total_views 三源择优 (同 v2) ---
    article_views_sum = sum(
        (p.get("views") or 0) for p in all_posts if isinstance(p.get("views"), int)
    )
    if profile_views is not None and profile_views > 0:
        total_views = profile_views
        views_source = "profile"
    elif article_views_sum > 0:
        total_views = article_views_sum
        views_source = "articles"
    else:
        total_views = 0
        views_source = "none"

    if not dry_run:
        student["posts"] = all_posts
        student["post_count"] = len(all_posts)
        student["total_views"] = total_views
        student["profile_views"] = profile_views
        student["article_views_sum"] = article_views_sum
        student["views_source"] = views_source
        student["total_likes"] = sum((p.get("likes") or 0) for p in all_posts
                                     if isinstance(p.get("likes"), int))
        student["total_comments"] = sum((p.get("comments") or 0) for p in all_posts
                                         if isinstance(p.get("comments"), int))
        student["total_collections"] = sum((p.get("collections") or 0) for p in all_posts
                                           if isinstance(p.get("collections"), int))
        student["articles_scanned"] = stats["fetched"]
        student["stats_updated_at"] = now_iso

    stats["total_views"] = total_views
    stats["profile_views"] = profile_views
    stats["article_views_sum"] = article_views_sum
    stats["views_source"] = views_source
    stats["abandoned"] = abandoned
    return stats


def write_snapshot(d):
    """写 cache/csdn/snapshot.json (和 v2 的格式一致)

    ★ 为什么增量脚本也要写这个文件:
      v2 靠 snapshot.json + data.json 建 "上次 post" 索引来做 24h 复用。
      如果只有 v2 写 snapshot, 那么增量脚本跑完之后 snapshot 还是旧的
      (实测: 增量跑完后 snapshot 里只有 14/385 篇有 stats),
      v2 全量时就会把 371 篇文章全部重抓一遍 -> 复用形同虚设 -> 又撞 521。
      (实测踩过: 修好索引后, 全量的 CSDN 请求数从 665 降到 ~230)
    """
    snap_dir = DASHBOARD_DIR / "cache" / "csdn"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap = {
        "last_run": d.get("last_update"),
        "students": {
            s.get("sid"): {
                "name": s.get("name"),
                "posts": s.get("posts", []),
                "gitee": s.get("gitee", {}),
                "stats": {
                    "total_views": s.get("total_views", 0),
                    "total_likes": s.get("total_likes", 0),
                    "total_comments": s.get("total_comments", 0),
                    "total_collections": s.get("total_collections", 0),
                    "profile_views": s.get("profile_views"),
                    "article_views_sum": s.get("article_views_sum", 0),
                    "views_source": s.get("views_source", "none"),
                    "articles_scanned": s.get("articles_scanned", 0),
                    "fetched_at": s.get("stats_updated_at", ""),
                },
            }
            for s in d.get("students", [])
        },
    }
    (snap_dir / "snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")


def write_history_snapshot(d):
    """写 history-real/YYYY-MM-DD.json (本地历史归档, 不部署)

    跟 v2 的格式保持一致, 这样每天 00:00 的增量结果和 05:30 的 Gitee 刷新
    会落在同一条时间线上, 事后能直接看到"阅读量每天涨了多少"。
    """
    hist_dir = DASHBOARD_DIR / "history-real"
    hist_dir.mkdir(parents=True, exist_ok=True)
    path = hist_dir / f"{datetime.now().strftime('%Y-%m-%d')}.json"
    try:
        if path.exists():
            hist = json.loads(path.read_text(encoding="utf-8"))
        else:
            hist = {"date": datetime.now().strftime("%Y-%m-%d"), "snapshots": []}
    except (OSError, ValueError):
        hist = {"date": datetime.now().strftime("%Y-%m-%d"), "snapshots": []}

    students = d.get("students", [])
    total_posts = sum(len(s.get("posts", [])) for s in students)
    total_views = sum(s.get("total_views") or 0 for s in students)
    hist["snapshots"].append({
        "at": d.get("last_update"),
        "total_students": len(students),
        "total_posts": total_posts,
        "class_stats": {
            "computed_at": d.get("last_update"),
            "students_total": len(students),
            "students_with_csdn": sum(1 for s in students if s.get("csdn_username")),
            "students_active": sum(1 for s in students if len(s.get("posts", [])) > 0),
            "csdn": {
                "total_posts": total_posts,
                "total_views": total_views,
                "total_likes": sum(s.get("total_likes") or 0 for s in students),
                "total_comments": sum(s.get("total_comments") or 0 for s in students),
                "total_collections": sum(s.get("total_collections") or 0 for s in students),
            },
        },
        "students_summary": [
            {
                "sid": s.get("sid"),
                "name": s.get("name"),
                "post_count": len(s.get("posts", [])),
                "total_views": s.get("total_views"),
                "profile_views": s.get("profile_views"),
                "article_views_sum": s.get("article_views_sum"),
                "views_source": s.get("views_source"),
            }
            for s in students
        ],
    })
    path.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")


def build_incremental_meta(d, elapsed, agg, students_ok, students_total,
                            full=False, recent=4, rotate=2):
    """构造 last_fetch_meta 的**增量块** (2026-10-22 R1: 按模式分块)。

    ★ 关键约定: 本函数**只写 incremental 块**, **绝不碰 gitee 块**。
      gitee 块是 csdn-monitor-v2.py 写的 —— 以前两个脚本抢同一个
      last_fetch_meta 整个字典, 后写的赢, 增量那一跑的 438 个请求
      被 gitee-only 整个抹掉 (仪表盘 19 小时假告警的根因)。

    顶层汇总字段 (total_requests / success_count / fail_count) =
    两个分块之和, 这样仪表盘一次就能同时看到"增量刷了"和"Gitee 刷了"。
    """
    meta = d.get("last_fetch_meta") or {}
    git_block = meta.get("gitee") or {}
    now_iso = d.get("last_update") or datetime.now().isoformat(timespec="seconds")
    duration_ms = int(elapsed * 1000)

    meta["fetched_at"] = now_iso
    meta["duration_ms"] = duration_ms
    meta["mode"] = "full" if full else "incremental"
    meta["incremental"] = {
        "fetched_at": now_iso,
        "mode": "full" if full else "incremental",
        "duration_ms": duration_ms,
        "total_requests": REQUESTS,
        "recent_per_student": recent,
        "rotate_per_student": rotate,
        "students_ok": students_ok,
        "students_total": students_total,
        "students_skipped": agg.get("skipped", 0),
        "new_posts": agg.get("new", 0),
        "updated_recent": agg.get("updated", 0),
        "updated_rotated": agg.get("rotated", 0),
        "backfilled": agg.get("backfilled", 0),
        "reused": agg.get("reused", 0),
        "failed": agg.get("failed", 0),
        "class_total_views": agg.get("class_total_views", 0),
        "class_total_views_all": agg.get("class_total_views_all", 0),
    }
    # 顶层汇总 = 两个分块之和 (仪表盘一次能同时看到增量和 Gitee)
    meta["total_requests"] = REQUESTS + (git_block.get("total_requests") or 0)
    meta["success_count"] = students_ok + (git_block.get("ok") or 0)
    meta["fail_count"] = agg.get("skipped", 0) + (git_block.get("failed") or 0)
    # 本次运行的明细 (日报"抓取健康度"行读它)
    meta["last_run"] = {
        "fetched_at": now_iso,
        "mode": meta["mode"],
        "csdn_ran": True,
        "csdn_ms": duration_ms,
        "gitee_ms": 0,
        "duration_ms": duration_ms,
        "total_requests": REQUESTS,
        "students_ok": students_ok,
        "students_total": students_total,
        "gitee_ok": 0,
        "gitee_empty": 0,
        "reused_students": agg.get("skipped", 0),
    }
    return meta


def main():
    ap = argparse.ArgumentParser(description="CSDN 增量抓取 (Ma: 数据应该是增量的)")
    ap.add_argument("--all", action="store_true", help="所有学生")
    ap.add_argument("--student", help="单个学生名")
    ap.add_argument("--max-pages", type=int, default=20, help="最大翻页数")
    ap.add_argument("--recent", type=int, default=4,
                    help="每人每天重抓的最近文章数 (阅读量主要增长在这里)")
    ap.add_argument("--rotate", type=int, default=2,
                    help="每人每天轮转重抓的老文章数 (保证老文章也会更新)")
    ap.add_argument("--full", action="store_true",
                    help="全量重抓所有文章 (忽略复用, 每周日兜底用)")
    ap.add_argument("--dry-run", action="store_true", help="只抓不写 data.json")
    ap.add_argument("--backfill-per-student", type=int, default=3,
                    help="每个学生每次最多补几篇'从没抓到过 stats'的老文章 "
                         "(3 -> 34 人每天补 102 篇, 约 4.3 分钟)")
    ap.add_argument("--max-minutes", type=float, default=18,
                    help="整个跑批的时间预算 (分钟), 只约束 backfill 层, "
                         "保证 recent/rotate 一定能跑完; 0 = 不限")
    args = ap.parse_args()

    if not DATA_PATH.exists():
        log(f"❌ 找不到 {DATA_PATH}")
        return 1

    with open(DATA_PATH, encoding="utf-8") as f:
        d = json.load(f)

    log("=== 增量抓取启动 ===")
    mode = "全量兜底" if args.full else f"增量(最近{args.recent}篇 + 轮转{args.rotate}篇)"
    log(f"模式: {mode}")
    log(f"data.json 加载: {len(d.get('students', []))} 个学生")

    if args.student:
        targets = [s for s in d.get("students", []) if s.get("name") == args.student]
        if not targets:
            log(f"❌ 找不到学生: {args.student}")
            return 1
    else:
        targets = d.get("students", [])

    t0 = time.time()
    # ★ 全局时间预算: 整个跑批的上限 (默认 18 分钟, 留 7 分钟余量给 25 分钟上限)
    deadline = (t0 + args.max_minutes * 60) if args.max_minutes else None
    agg = {"new": 0, "new_posts": 0, "updated": 0, "rotated": 0, "reused": 0,
           "fetched": 0, "failed": 0, "skipped": 0, "backfill": 0, "backfilled": 0,
           "budget_exhausted": 0}
    total_views_sum = 0
    students_ok = 0

    for i, s in enumerate(targets, 1):
        name = s.get("name", "?")
        log(f"[{i}/{len(targets)}] {name}...")
        r = incremental_update_student(
            s,
            max_pages=args.max_pages,
            recent_n=args.recent,
            rotate_m=args.rotate,
            full=args.full,
            dry_run=args.dry_run,
            deadline=deadline,
            backfill_per_student=args.backfill_per_student,
        )
        if r.get("skipped"):
            agg["skipped"] += 1
            log(f"  ⏭️  跳过 ({r['skipped']})")
            continue
        students_ok += 1
        for k in agg:
            agg[k] += r.get(k, 0) or 0
        total_views_sum += r.get("total_views", 0) or 0
        log(f"  🆕新{r['new']} 全抓{r['new_posts']} | ↻最近更新{r['updated']} "
            f"轮转{r['rotated']} | 🧹补历史{r.get('backfilled', 0)} | "
            f"♻️复用{r['reused']} | 失败{r['failed']}")
        log(f"  👁️ total_views={r['total_views']} (来源 {r['views_source']}, "
            f"profile={r['profile_views']}, 文章累加={r['article_views_sum']})")

    elapsed = time.time() - t0
    log("")
    log("=== 抓取完成 ===")
    log(f"⏱️  耗时 {elapsed:.0f} 秒")
    log(f"🌐 CSDN 请求数: {REQUESTS}")
    log(f"👥 成功 {students_ok}/{len(targets)} 个学生 (跳过 {agg['skipped']})")
    log(f"🆕 新文章 {agg['new']} (抓到 stats 的 {agg['new_posts']})")
    log(f"🔄 重抓 stats: 最近 {agg['updated']} + 轮转 {agg['rotated']} + "
        f"补历史 {agg['backfilled']} = {agg['fetched']} 篇")
    log(f"♻️  复用旧值 {agg['reused']} 篇 (未重抓, 会按天轮转刷新)")
    log(f"❌ 失败 {agg['failed']} 篇")
    if agg.get("budget_exhausted"):
        log(f"⏱️  时间预算用尽, {agg['budget_exhausted']} 篇'补历史'留到下次 "
            f"(它们的 views 还是 None, 明天会继续补)")
    log(f"👁️  班级 total_views 合计 = {total_views_sum}")

    if args.dry_run:
        log("🧪 --dry-run: 没有写 data.json")
        return 0

    d["last_update"] = datetime.now().isoformat(timespec="seconds")
    d["total_posts"] = sum(len(s.get("posts", [])) for s in d.get("students", []))
    # ★ 2026-10-22 (R1): last_fetch_meta 改成**按模式分块**
    #   - incremental 块 = 本脚本写的 (CSDN 抓取这一路)
    #   - gitee 块       = csdn-monitor-v2.py 写的 (Gitee 抓取那一路)
    #   两个脚本各写各的那块, **互不覆盖** —— 以前本脚本写的 438 个请求
    #   会被 v2 的 gitee-only 整个抹掉 (仪表盘 19 小时假告警的根因)。
    agg["class_total_views"] = total_views_sum
    agg["class_total_views_all"] = sum(s.get("total_views") or 0
                                       for s in d.get("students", []))
    meta = build_incremental_meta(d, elapsed, agg, students_ok, len(targets),
                                  full=args.full, recent=args.recent,
                                  rotate=args.rotate)
    d["last_fetch_meta"] = meta

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    for _fn, _label in ((write_snapshot, "cache/csdn/snapshot.json"),
                        (write_history_snapshot, "history-real 快照")):
        try:
            _fn(d)
            log(f"🗂  {_label} 已写入")
        except Exception as e:
            log(f"⚠️  {_label} 写入失败: {type(e).__name__}: {str(e)[:60]}")

    log(f"📊 总文章: {d['total_posts']}")
    log("💾 data.json 已保存")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
