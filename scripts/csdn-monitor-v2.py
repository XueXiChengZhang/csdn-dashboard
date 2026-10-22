#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSDN + Gitee 学生项目监控脚本 v2 (Ma 12点后自动优化版)

差异 vs v1 (csdn-monitor.py):
  - 加 Gitee 仓库抓取: API + HTML 双 fallback, 拿 commit 数 / stars / forks / 最后 commit
  - 时间窗控制: 默认只在 00:00-06:00 跑(--force-now 跳过)
  - 直接写 data.json 到 dashboard 根目录(前端可消费 gitee 字段)
  - 写 history-real/yyyy-mm-dd.json 当日快照(可累计)
  - fetch_meta 加 gitee 阶段统计
  - cron 配置: 0 0 * * * (午夜) + 30 5 * * * (凌晨 5:30 双保险)
  - token < 25% 才跑(基于 .token-watch-state.json 或外部预算)

用法:
  python scripts/csdn-monitor-v2.py                 # 默认检查时间窗
  python scripts/csdn-monitor-v2.py --force-now    # 跳过时间窗(手动测试)
  python scripts/csdn-monitor-v2.py --dry-run      # 只跑抓取, 不写文件不推飞书
  python scripts/csdn-monitor-v2.py --gitee-only   # 只跑 Gitee 抓取阶段
  python scripts/csdn-monitor-v2.py --csdn-only    # 只跑 CSDN 抓取阶段

输出:
  - data.json (前端消费, 含 gitee 字段)
  - cache/csdn/snapshot.json (历史快照, 找新文章)
  - history-real/YYYY-MM-DD.json (每日归档, 不部署)
  - reports/csdn-report-YYYY-MM-DD.md (飞书附件)
  - 飞书消息 via openclaw
"""
from __future__ import annotations
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

# 公共解析模块 (增量脚本 csdn_incremental.py 也用同一个, 避免两份正则各坏各的)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from csdn_stats import (  # noqa: E402
    RateLimiter,
    article_id_from_link,
    parse_article_stats,
    parse_profile_views,
)

# ---------- stdout 编码兜底 (必须在 import 之后, 且在任何 print 之前) ----------
# 计划任务 / 手动运行时 stdout 可能是 GBK(936), 非 ASCII 会抛 UnicodeEncodeError。
# ★ 为什么这不只是"打印崩了": 在 fetch_user_total_views / fetch_article_stats 里
#   那个异常会被 except 吞掉 → **静默丢数据** (profile_views 变 None /
#   整篇 stats 被判失败, views 冻结在旧值)。静默丢数据比崩掉更糟。
def safe_print(msg, **kwargs):
    """打印任何内容都不会因为编码问题炸掉。

    stdout 是 GBK 时, 中文/emoji 会被替换成 ASCII 占位符而不是抛异常;
    stdout 能装下时就原样打印。
    """
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

# ---------- 路径 ----------
SCRIPT_DIR = Path(__file__).resolve().parent
DASHBOARD = SCRIPT_DIR.parent  # .../csdn-dashboard
WATCHLIST_PATHS = [
    DASHBOARD / "csdn-watchlist.txt",                            # 仓库内(优先)
    DASHBOARD.parent.parent / "JARVIS-Backup" / "workspace" / "csdn-watchlist.txt",  # 旧路径
]
CACHE_DIR = DASHBOARD / "cache" / "csdn"
SNAPSHOT = CACHE_DIR / "snapshot.json"
REPORT_DIR = DASHBOARD / "reports"
HISTORY_DIR = DASHBOARD / "history-real"
DATA_JSON = DASHBOARD / "data.json"

TODAY = datetime.now().strftime("%Y-%m-%d")
NOW_ISO = datetime.now().isoformat(timespec="seconds")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
      "Edg/126.0.0.0")

# P2 #5 修复: 统一版本号 — 4 个 HTML 之前各自写 v13/v12/v10.0/v8.0
# 现在统一读 data.json.dashboard_version, 后端改动即更新
DASHBOARD_VERSION = "v22.0"

# ---------- 时间窗 / Token 控制 ----------
WINDOW_START = 0   # 00:00
WINDOW_END = 6     # 06:00
TOKEN_BUDGET_PATH = Path(os.environ.get("HERMES_HOME", str(Path.home()))) / ".token-budget.json"


def in_time_window() -> bool:
    """返回当前小时是否在 00:00-06:00 窗口"""
    return WINDOW_START <= datetime.now().hour < WINDOW_END


def token_budget_ok() -> bool:
    """读 token 预算文件, 检查剩余是否 > 75% (即使用 < 25%)
    找不到文件 = 视为 OK (兼容老环境)
    """
    if not TOKEN_BUDGET_PATH.exists():
        return True
    try:
        b = json.loads(TOKEN_BUDGET_PATH.read_text(encoding="utf-8"))
        remain = float(b.get("remaining_ratio", 1.0))
        return remain >= 0.75
    except Exception:
        return True


# ---------- HTTP ----------
def http_get(url, timeout=12, headers=None, retries=2):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, timeout=timeout, headers=h)
            if r.status_code in (521, 403, 429) and attempt < retries:
                time.sleep(2 + attempt * 2)
                continue
            return r
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1 + attempt)
                continue
            class _Err:
                status_code = -1
                text = str(last_err)
            return _Err()
    return None


# ---------- CSDN 限流 ----------
# 实测 (2026-10-21, 同一 IP):
#   - 不加延迟连打 40 篇文章 -> 第 17 篇开始大量 HTTP 521 (CSDN WAF 反爬)
#   - 间隔 3.0s 连打 30 篇     -> 30/30 全部 200, 零 521
# 521 是 CSDN 的风控响应, 所以全站共用一个节流器 (瓶颈是 IP, 不是线程数)。
# 线程数从 3 降到 2 —— 线程多只会更快撞墙, 然后在重试里白白耗掉 10 分钟。
CSDN_MIN_INTERVAL = float(os.environ.get("CSDN_MIN_INTERVAL", "2.5"))
# 测试钩子: 把 CSDN 的基址换掉, 就能在**真实跑一遍**的前提下模拟 "CSDN 完全挂掉",
# 用来端到端验证数据保护逻辑 (见 scripts/test_csdn_stats.py)。
# 生产环境不设这个变量, 行为完全不变。
CSDN_BASE = os.environ.get("CSDN_BASE_URL", "https://blog.csdn.net").rstrip("/")
CSDN_LIMITER = RateLimiter(min_interval=CSDN_MIN_INTERVAL, jitter=0.8)
CSDN_REQUESTS = 0
CSDN_REQUESTS_LOCK = threading.Lock()

# 上一次跑出来的 posts, 按 link 索引。fetch_csdn_html 用它把 stats 合并进
# 新拿到的 4 字段 post 里, 让 aggregate_user_stats 的 24h 复用生效。
PREV_POSTS_BY_LINK: dict[str, dict] = {}


def csdn_get(url, timeout=12, headers=None, retries=1):
    """CSDN 专用 GET: 全局限流 + 计数 + 521 冷却。

    返回 requests.Response (失败时返回带 status_code 的伪对象)。
    """
    global CSDN_REQUESTS
    headers = headers or CSDN_HEADERS
    last_err = None
    for attempt in range(retries + 1):
        CSDN_LIMITER.wait()
        with CSDN_REQUESTS_LOCK:
            CSDN_REQUESTS += 1
        try:
            r = requests.get(url, timeout=timeout, headers=headers)
            if r.status_code in (521, 403, 429):
                # 被风控: 全局冷却 + **拉长基准间隔** (CSDN 的风控会随请求量
                # 收紧, 固定间隔扛不住; 自适应能自己找到当下安全的速率)
                CSDN_LIMITER.escalate(1.5)
                CSDN_LIMITER.penalize(5.0 + attempt * 5)
                last_err = RuntimeError(f"HTTP {r.status_code}")
                if attempt < retries:
                    continue
            else:
                CSDN_LIMITER.decay()
            return r
        except Exception as e:
            last_err = e
            if attempt < retries:
                CSDN_LIMITER.penalize(3.0)
                continue
            class _Err:
                status_code = -1
                text = str(last_err)
            return _Err()
    class _Err:
        status_code = -1
        text = str(last_err)
    return _Err()


# ---------- Watchlist ----------
def find_watchlist() -> Path | None:
    for p in WATCHLIST_PATHS:
        if p.exists():
            return p
    return None


def parse_watchlist(path: Path):
    rows = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            parts = line.split("\t")
        else:
            parts = re.split(r"\s{2,}", line, maxsplit=4)
            if len(parts) < 4:
                m_url = re.search(r"(https?://\S+)", line)
                if not m_url:
                    continue
                url_start = m_url.start()
                head = line[:url_start].split()
                parts = head[:2] + [""] * (2 - len(head[:2])) + [line[url_start:].strip()]
        while len(parts) < 5:
            parts.append("")
        sid, name, csdn, gitee, note = [x.strip() for x in parts[:5]]
        if not re.match(r"^\d+$", sid):
            continue
        rows.append({
            "sid": sid,
            "name": name,
            "csdn": csdn,
            "gitee": gitee,
            "note": note,
        })
    return rows


def extract_csdn_username(csdn_url):
    if not csdn_url:
        return ""
    m = re.search(r"blog\.csdn\.net/([^/?#]+)", csdn_url)
    return m.group(1) if m else ""


def parse_gitee_repo(gitee_url):
    if not gitee_url:
        return "", ""
    m = re.search(r"gitee\.com/([^/]+)/([^/?#]+)", gitee_url)
    if not m:
        return "", ""
    owner, repo = m.group(1), m.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


# ---------- CSDN 抓取 (复用 v1 逻辑, 精简) ----------
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



def _parse_relative_time_to_iso(s):
    """Ma: CSDN HTML view-time-box 返回 '博文更新于 22 小时前' 或 '2026.09.17'.
    统一解析为 ISO datetime (带秒),失败返回 ''.
    Bug 修复: 之前日期格式只到 day,前端按字符串排序错乱
    """
    if not s:
        return ""
    s = s.strip()
    import datetime as _dt
    # 1. 绝对日期 YYYY.MM.DD (HH:MM:SS 可选)
    m = re.search(r"(\d{4})[.-](\d{1,2})[.-](\d{1,2})(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?", s)
    if m:
        y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
        hh = m.group(4) or "00"
        mm = m.group(5) or "00"
        ss = m.group(6) or "00"
        try:
            return _dt.datetime(int(y), int(mo), int(d),
                                int(hh), int(mm), int(ss)).isoformat(timespec="seconds")
        except Exception:
            return ""
    # 2. 相对时间 (统一带时分秒)
    now = _dt.datetime.now()
    m = re.search(r"(\d+)\s*小时前", s)
    if m:
        return (now - _dt.timedelta(hours=int(m.group(1)))).isoformat(timespec="seconds")
    m = re.search(r"(\d+)\s*分钟前", s)
    if m:
        return (now - _dt.timedelta(minutes=int(m.group(1)))).isoformat(timespec="seconds")
    m = re.search(r"(\d+)\s*天前", s)
    if m:
        # 当天12:00 作为合理估值
        dt = (now - _dt.timedelta(days=int(m.group(1))))
        return dt.replace(hour=12, minute=0, second=0).isoformat(timespec="seconds")
    m = re.search(r"(\d+)\s*周前", s)
    if m:
        dt = (now - _dt.timedelta(weeks=int(m.group(1))))
        return dt.replace(hour=12, minute=0, second=0).isoformat(timespec="seconds")
    m = re.search(r"(\d+)\s*月前", s)
    if m:
        dt = (now - _dt.timedelta(days=30 * int(m.group(1))))
        return dt.replace(hour=12, minute=0, second=0).isoformat(timespec="seconds")
    return ""


def fetch_csdn_html(username, limit=50):
    """Ma: 抓取 CSDN 用户的文章列表,支持翻页以获取更多文章.

    修复: 之前 limit=5 且只抓第 1 页,很多人显示 5 篇.
    现在通过 page 参数翻页,直到抓满 limit 或所有页都爬过.

    ★ E 修复 (2026-10-21): 列表页只能拿到 title/link/pubDate/guid 四个字段,
      拿不到 stats。但如果不把**上次抓到的 stats 合并进来**, aggregate_user_stats
      里的 "24h 内复用" 判断 (要求 posts 带 views + stats_updated_at) 永远不成立
      -> 每次全量都重抓 385 篇 -> 必然撞 521 风控。
      所以这里按 link/guid 从 PREV_POSTS_BY_LINK 里把
      views/likes/comments/collections/stats_updated_at 合并进来。
    """
    items = []
    page_size = 20  # CSDN 每页大约 20 篇
    max_pages = (limit + page_size - 1) // page_size + 2
    seen = set()

    for page in range(1, max_pages + 1):
        if len(items) >= limit:
            break
        urls = [
            f"{CSDN_BASE}/{username}?type=blog&page={page}",
            f"{CSDN_BASE}/{username}?page={page}",
        ]
        page_has_data = False
        for url in urls:
            try:
                r = csdn_get(url, timeout=12, headers=CSDN_HEADERS, retries=1)
                if r.status_code != 200 or not r.text:
                    continue
                html = r.text
                blocks = re.findall(
                    r'<article[^>]*class="blog-list-box"[^>]*>(.*?)</article>',
                    html, re.DOTALL,
                ) or re.findall(r'<article[^>]*>(.*?)</article>', html, re.DOTALL)
                if not blocks:
                    continue
                page_has_data = True
                for block in blocks:
                    if len(items) >= limit:
                        break
                    lm = re.search(
                        r'href="(https?://blog\.csdn\.net/[^/]+/article/details/\d+)"',
                        block,
                    )
                    if not lm:
                        continue
                    link = lm.group(1)
                    if link in seen:
                        continue
                    tm = re.search(r'<h[34][^>]*>(.*?)</h[34]>', block, re.DOTALL)
                    title = re.sub(r"<[^>]+>", "", tm.group(1)).strip() if tm else ""
                    if not title:
                        aid = re.search(r"/article/details/(\d+)", link)
                        title = f"文章 #{aid.group(1) if aid else '?'}"
                    if not title or len(title) < 2:
                        continue
                    seen.add(link)
                    time_match = re.search(
                        r'<div class="view-time-box"[^>]*>\s*博文更新于\s*([^<·]+?)\s*(?:·|</div>)',
                        block,
                    )
                    raw_time = time_match.group(1).strip() if time_match else ""
                    pub_iso = _parse_relative_time_to_iso(raw_time)
                    item = {"title": title, "link": link, "pubDate": pub_iso, "guid": link}
                    # ★ E: 把上次的 stats 合并进来, 让 24h 复用逻辑真正生效
                    prev = PREV_POSTS_BY_LINK.get(link)
                    if prev:
                        for key in ("views", "likes", "comments", "collections",
                                    "stats_updated_at"):
                            if key in prev and prev[key] is not None:
                                item[key] = prev[key]
                    items.append(item)
                if items:
                    break
            except Exception:
                continue
        if not page_has_data and page > 1:
            break
    return items[:limit]


def fetch_csdn_rss(username, limit=50):
    items = []
    feeds = [
        f"https://rsshub.app/csdn/blog/{username}",
        f"https://blog.csdn.net/{username}/rss/list",
    ]
    for feed_url in feeds:
        try:
            r = http_get(feed_url, timeout=10)
            if r.status_code != 200 or not r.text:
                continue
            root = ET.fromstring(r.text)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub = (item.findtext("pubDate") or "").strip()
                guid = (item.findtext("guid") or link or "").strip()
                if title and link:
                    items.append({"title": title, "link": link, "pubDate": pub, "guid": guid})
                if len(items) >= limit:
                    break
            if items:
                return items[:limit]
        except Exception:
            continue
    return items


def fetch_user_total_views(username):
    """抓个人主页的"总访问量"。

    ★ 关键语义: **失败返回 None, 不是 0**。
      旧代码失败返回 0, 上层 `total_views = profile_views` 直接把整个人的
      阅读量归零 —— 于是一次被 521 风控的请求就能把上一次的好数据覆盖成 0
      (2026-10-21 05:30 那次就是这么把李瑞 7254 冲成 0 的)。
      返回 None 后, aggregate_user_stats 会退到 article_views_sum 兜底。

    三层兜底正则在 scripts/csdn_stats.py::parse_profile_views 里
    (精确容器 -> 标签前后 200 字符 -> 双向宽松)。
    """
    if not username:
        return None
    for url in [
        f"{CSDN_BASE}/{username}?type=blog",
        f"{CSDN_BASE}/{username}",
    ]:
        try:
            r = csdn_get(url, timeout=12, headers=CSDN_HEADERS, retries=1)
            if r.status_code != 200 or not r.text:
                safe_print(f"[profile] {username} HTTP {r.status_code} url={url[:70]}")
                continue
            if len(r.text) < 2000:
                safe_print(f"[profile] {username} 响应过短 len={len(r.text)} (疑似风控)")
                continue
            val = parse_profile_views(r.text)
            if val is not None:
                return val
            safe_print(f"[profile] {username} 页面里找不到'总访问量' (len={len(r.text)})")
        except Exception as e:
            safe_print(f"[profile] {username} 异常 {type(e).__name__}: {str(e)[:60]}")
            continue
    return None


def fetch_article_stats(article_id, username=""):
    if not article_id:
        return {"ok": False, "views": 0, "comments": 0, "collections": 0, "likes": 0}
    url = f"{CSDN_BASE}/{username}/article/details/{article_id}" if username \
        else f"{CSDN_BASE}/article/details/{article_id}"
    try:
        r = csdn_get(url, timeout=12, headers=CSDN_HEADERS, retries=1)
        # P0-2: 区分 "HTTP 错误" 与 "200 但空文 (软反爬)" — 都打印并返回失败
        if r.status_code != 200:
            safe_print(f"[stats] HTTP {r.status_code} aid={article_id} url={url[:80]}")
            return {"ok": False, "views": 0, "comments": 0, "collections": 0, "likes": 0}
        if not r.text or len(r.text) < 500:
            # CSDN 偶发 200 + 空 HTML (软反爬/地域重定向)
            safe_print(f"[stats] HTTP 200 but empty/short aid={article_id} len={len(r.text or '')} url={url[:80]}")
            return {"ok": False, "views": 0, "comments": 0, "collections": 0, "likes": 0}
        html = r.text
        # 解析全部交给公共模块 (增量脚本共用同一套正则, 不会再各坏各的)
        parsed = parse_article_stats(html)
        stats = {
            "views": parsed["views"] if parsed["views"] is not None else 0,
            "comments": parsed["comments"] if parsed["comments"] is not None else 0,
            "collections": parsed["collections"] if parsed["collections"] is not None else 0,
            "likes": parsed["likes"] if parsed["likes"] is not None else 0,
            "ok": True,
        }
        # 抓到了页面但一个数都没解析出来 → 页面结构变了, 打印出来便于诊断
        if not any(stats[k] for k in ("views", "comments", "collections", "likes")):
            safe_print(f"[stats] all zeros aid={article_id} len(html)={len(html)} "
                  f"meta_desc={'文章浏览阅读' in html} view_count_format={'viewCountFormat' in html}")
            safe_print(f"[stats] html_head={html[:300]!r}")
        return stats
    except Exception as e:
        safe_print(f"[stats] exception aid={article_id} err={type(e).__name__}: {str(e)[:80]}")
        return {"ok": False, "views": 0, "comments": 0, "collections": 0, "likes": 0}


def aggregate_user_stats(username, posts, no_reuse=False):
    """P0 #2 修复: 每篇 post 都把 stats 写回, 不只算总和.
    P0 #17 修复: 同时写 stats_updated_at.
    P3 #14 优化: 复用 posts 已有的 stats_updated_at, 24h 内不重抓.
    no_reuse=True: 忽略 24h 复用, 全部重抓 (每周全量兜底用)。
    """
    article_stats = {"views": 0, "comments": 0, "collections": 0, "likes": 0}
    n_ok = 0
    n_skipped = 0
    n_failed = 0
    stats_ts = NOW_ISO  # 这次抓取的整体时间戳

    # 阈值: 24h 内已抓过的文章复用, 避免每跑全量都重抓一遍
    reuse_threshold = datetime.fromisoformat(
        (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    )

    # 连续失败 N 次就放弃这个学生的 stats, 走保护逻辑。
    # 原因: CSDN 被 521 风控后, 再死磕只会把 10 分钟耗在重试上,
    # 而且每次重试都在加深封禁 (旧代码 CSDN 阶段跑过 627 秒)。
    MAX_CONSECUTIVE_FAILURES = 5
    consecutive_failures = 0
    abandoned = False

    for p in posts or []:
        aid = article_id_from_link(p.get("link"))
        if not aid:
            continue

        # 检查是否需要重抓 (没有 stats_updated_at 或超过 24h)
        prev_ts_str = p.get("stats_updated_at")
        need_fetch = True
        if prev_ts_str and not no_reuse:
            try:
                prev_ts = datetime.fromisoformat(prev_ts_str)
                if prev_ts > reuse_threshold and isinstance(p.get("views"), int) and p["views"] > 0:
                    # 复用已有 stats
                    s = {
                        "views": p.get("views", 0),
                        "comments": p.get("comments", 0),
                        "collections": p.get("collections", 0),
                        "likes": p.get("likes", 0),
                        "ok": True,
                    }
                    n_ok += 1
                    n_skipped += 1
                    need_fetch = False
                    consecutive_failures = 0
            except (ValueError, TypeError):
                pass

        if need_fetch and abandoned:
            # 已放弃: 用上次的值 (如果有), 不要再发请求
            s = {
                "views": p.get("views", 0) or 0,
                "comments": p.get("comments", 0) or 0,
                "collections": p.get("collections", 0) or 0,
                "likes": p.get("likes", 0) or 0,
                "ok": True,
            }
            n_skipped += 1
        elif need_fetch:
            s = fetch_article_stats(aid, username=username)
            if s.get("ok"):
                n_ok += 1
                consecutive_failures = 0
                # ★ P0 #2 修复: 写回每篇 post
                p["views"] = s.get("views", 0)
                p["likes"] = s.get("likes", 0)
                p["comments"] = s.get("comments", 0)
                p["collections"] = s.get("collections", 0)
                # ★ P0 #17 修复: 标记抓取时间
                p["stats_updated_at"] = stats_ts
            else:
                n_failed += 1
                consecutive_failures += 1
                # 抓取失败: 这次没拿到新数, 但**不能把上次的值当 0 累加**,
                # 否则 article_views_sum 会凭空缩水, 触发 C 臂的保护逻辑。
                # 用 post 里已有的值 (来自 PREV_POSTS_BY_LINK 合并) 参与求和。
                s = {
                    "views": p.get("views", 0) or 0,
                    "comments": p.get("comments", 0) or 0,
                    "collections": p.get("collections", 0) or 0,
                    "likes": p.get("likes", 0) or 0,
                    "ok": False,
                }
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    abandoned = True
                    safe_print(f"[stats] {username}: 连续 {consecutive_failures} 次失败, "
                          f"放弃该学生剩余文章的抓取 (交给保护逻辑兜底)")

        for k in article_stats:
            article_stats[k] += s.get(k, 0)

    profile_views = fetch_user_total_views(username)

    # ★ C: total_views 三源择优 — profile -> 文章累加 -> 0
    #   profile 拿不到时 (None) 绝不能用 0 冒充, 否则一次风控就把人清零。
    if profile_views is not None and profile_views > 0:
        total_views = profile_views
        views_source = "profile"
    elif article_stats["views"] > 0:
        total_views = article_stats["views"]
        views_source = "articles"
    else:
        total_views = 0
        views_source = "none"

    return {
        "total_views": total_views,
        # 两个来源都单独保留, 报告里能看出这个人的阅读量到底是从哪来的
        "profile_views": profile_views,
        "article_views_sum": article_stats["views"],
        "views_source": views_source,
        "total_likes": article_stats["likes"],
        "total_comments": article_stats["comments"],
        "total_collections": article_stats["collections"],
        "articles_scanned": n_ok,
        "articles_skipped_reuse": n_skipped,
        "articles_failed": n_failed,
        "abandoned": abandoned,
        "fetched_at": stats_ts,
    }


# ---------- Gitee 抓取 ----------
GITEE_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# Gitee 的真实请求计数 (2026-10-22 修: 以前 _count_actual_requests 只认
# source=="api"/"html", markdown 源一次都不算 -> gitee-only 那一跑
# total_requests=0, 仪表盘健康度卡片显示"尚未运行抓取任务" 19 小时)。
GITEE_REQUESTS = 0
GITEE_REQUESTS_LOCK = threading.Lock()
GITEE_REQUEST_BREAKDOWN = {"api": 0, "html": 0, "markdown": 0}


def _gitee_count_request(kind):
    """记录一次 Gitee 请求 (真实计数, 不再估算)"""
    global GITEE_REQUESTS
    with GITEE_REQUESTS_LOCK:
        GITEE_REQUESTS += 1
        GITEE_REQUEST_BREAKDOWN[kind] = GITEE_REQUEST_BREAKDOWN.get(kind, 0) + 1


def _gitee_is_empty_response(r):
    """Gitee 对**空仓库**的 commits API 返回 HTTP 404 + {"message":"Repository is empty"}。

    ★ 这是 2026-10-22 实测出来的关键事实: 空仓库不是"抓取失败",
      而是"学生建了仓库但没提交任何代码"。两者必须区分, 否则:
        - 空仓库 -> 被报成 ok=True (假成功)
        - 有 commit 的仓库 -> 因为上层分不清 404 和 200, 计数器归零
    """
    try:
        body = (getattr(r, "text", "") or "")[:300].lower()
    except Exception:
        body = ""
    return "is empty" in body or '"empty"' in body or "repository is empty" in body


def fetch_gitee_repo_api(owner, repo):
    """API 抓仓库基本信息: stars, forks, language, 默认分支

    ★ 2026-10-22: 非 200 时**必须打日志** —— 以前直接 return None,
      一行日志都没有, 永远查不出为什么失败。
    """
    url = f"https://gitee.com/api/v5/repos/{owner}/{repo}"
    try:
        r = http_get(url, timeout=10, headers=GITEE_HEADERS, retries=1)
        _gitee_count_request("api")
        if r.status_code != 200:
            safe_print(f"[gitee] repo API 失败 {url} HTTP {r.status_code}")
            return None
        return r.json()
    except Exception as e:
        safe_print(f"[gitee] repo API 异常 {url} {type(e).__name__}: {str(e)[:60]}")
        return None


def fetch_gitee_last_commit_api(owner, repo):
    """API 抓最后 commit。

    返回:
      dict              正常的 commit 信息 (author/date/msg/sha)
      {"empty": True}   空仓库 (HTTP 404 + "Repository is empty") —— **不是失败**
      {"notfound": True} 仓库不存在 (HTTP 404 + 别的 message)
      None              其它失败 (网络/限流/500)
    """
    url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/commits?per_page=1"
    try:
        r = http_get(url, timeout=10, headers=GITEE_HEADERS, retries=1)
        _gitee_count_request("api")
        if r.status_code != 200:
            if r.status_code == 404:
                if _gitee_is_empty_response(r):
                    safe_print(f"[gitee] {owner}/{repo} 是空仓库 (Repository is empty)")
                    return {"empty": True}
                safe_print(f"[gitee] {owner}/{repo} 仓库不存在 (HTTP 404)")
                return {"notfound": True}
            safe_print(f"[gitee] last-commit API 失败 {url} HTTP {r.status_code}")
            return None
        data = r.json()
        if not isinstance(data, list):
            return None
        if not data:
            # 200 但列表是空的 -> 也是空仓库
            return {"empty": True}
        head = data[0]
        commit = head.get("commit", {}) or {}
        author = (commit.get("author") or {}).get("name", "?")
        date = commit.get("authored_date") or commit.get("committer", {}).get("date", "")
        msg = (commit.get("message") or "").splitlines()[0][:80]
        sha = head.get("sha", "")[:7]
        return {"author": author, "date": date, "msg": msg, "sha": sha}
    except Exception as e:
        safe_print(f"[gitee] last-commit API 异常 {url} {type(e).__name__}: {str(e)[:60]}")
        return None


def fetch_gitee_commits_count_api(owner, repo):
    """通过 commits 列表分页估算总数 (per_page=100, 取最后一页 + 之前累加)
    Gitee API 默认返回最新 commits, 我们最多取 5 页 = 500 commits 估算

    ★ 2026-10-22: 返回 (total, truncated, empty)。
      empty=True 表示"空仓库" (404 + Repository is empty), **不是失败**。
      以前对非 200 直接 return (total, truncated), 分不清"空仓库"和"抓取失败"。
    """
    total = 0
    truncated = False
    empty = False
    try:
        for page in range(1, 6):
            url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/commits?per_page=100&page={page}"
            r = http_get(url, timeout=8, headers=GITEE_HEADERS, retries=0)
            _gitee_count_request("api")
            if r.status_code != 200:
                if r.status_code == 404:
                    empty = _gitee_is_empty_response(r)
                    if not empty:
                        safe_print(f"[gitee] {owner}/{repo} commits API 404 (仓库不存在)")
                else:
                    safe_print(f"[gitee] commits API 失败 {url} HTTP {r.status_code}")
                return (total, truncated, empty)
            data = r.json()
            if not isinstance(data, list):
                return (total, truncated, empty)
            if not data:
                empty = True
                break
            total += len(data)
            if len(data) < 100:
                break
        # 跑完 5 页都没看到 < 100, 说明 > 500 被截断
        if total >= 500:
            truncated = True
        return (total, truncated, empty)
    except Exception as e:
        safe_print(f"[gitee] commits API 异常 {type(e).__name__}: {str(e)[:60]}")
        return (total, truncated, empty)



def fetch_gitee_markdown(owner, repo):
    """Gitee 在 /owner/repo.md 这个 URL 下对未登录请求返回 markdown。
    /owner/repo 主页面是 HTML (不能直接当 markdown 解析)。
    解析 markdown 拿 stars/forks/created/last_updated。

    ★ 2026-10-22 实测: Gitee 现在的 markdown 格式是 "- **Stars**: 0"
      (行首 "- " 前缀 + 冒号后有空格)。旧正则 ``\\*\\*Stars\\*\\*\\s*:?\\s*(\\d+)``
      依然能匹配 (\\s* 吃掉 ": "), 所以数字解析本身是好的。
      但**匹配不上时必须打日志** —— 以前静默留 0, 分不清"真的是 0"和"解析失败"。
    """
    url = f"https://gitee.com/{owner}/{repo}.md"
    try:
        r = http_get(url, timeout=10, headers={
            "User-Agent": UA,
            "Accept": "text/markdown,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }, retries=1)
        _gitee_count_request("markdown")
        if r.status_code != 200 or not r.text:
            safe_print(f"[gitee] markdown 抓取失败 {url} HTTP {r.status_code}")
            return None
        text = r.text
        # 必须是 markdown 格式 (gitee 锁定页面)
        if "# " not in text or "**Stars**" not in text:
            safe_print(f"[gitee] markdown 格式不对 {owner}/{repo} (len={len(text)})")
            return None
        info = {"source": "markdown"}
        # Stars
        m = re.search(r"\*\*Stars\*\*\s*:?\s*(\d+)", text)
        if m:
            info["stars"] = int(m.group(1))
        else:
            safe_print(f"[gitee] markdown 解析失败 {owner}/{repo}: 找不到 Stars 数字")
        # Forks
        m = re.search(r"\*\*Forks\*\*\s*:?\s*(\d+)", text)
        if m:
            info["forks"] = int(m.group(1))
        else:
            safe_print(f"[gitee] markdown 解析失败 {owner}/{repo}: 找不到 Forks 数字")
        # Last Updated (用来替代 commit date)
        m = re.search(r"\*\*Last Updated\*\*\s*:?\s*([\d-]+)", text)
        if m:
            info["date"] = m.group(1) + "T00:00:00+08:00"  # ISO-ish
        # Created
        m = re.search(r"\*\*Created\*\*\s*:?\s*([\d-]+)", text)
        if m:
            info["created"] = m.group(1)
        return info if len(info) > 1 else None
    except Exception:
        return None


def fetch_gitee_html_fallback(owner, repo):
    """HTML fallback: 抓 gitee.com/<owner>/<repo> 主页, 解析 commit 数 / stars
    公开页面 HTML 含: <svg class="...star">...<span class="...">N</span>
    """
    url = f"https://gitee.com/{owner}/{repo}"
    try:
        r = http_get(url, timeout=10, headers={
            "User-Agent": UA,
            "Accept": "text/html,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }, retries=1)
        _gitee_count_request("html")
        if r.status_code != 200 or not r.text:
            safe_print(f"[gitee] HTML fallback 抓取失败 {url} HTTP {r.status_code}")
            return None
        html = r.text
        info = {"source": "html"}
        # stars
        m = re.search(r'class="social-count[^"]*"[^>]*>\s*([\d,]+)\s*<', html)
        if not m:
            m = re.search(r'"stargazers_count"\s*:\s*(\d+)', html)
        if m:
            info["stars"] = int(re.sub(r"[^\d]", "", m.group(1)) or 0)
        # forks
        m = re.search(r'class="fork-count[^"]*"[^>]*>\s*([\d,]+)\s*<', html)
        if not m:
            m = re.search(r'"forks_count"\s*:\s*(\d+)', html)
        if m:
            info["forks"] = int(re.sub(r"[^\d]", "", m.group(1)) or 0)
        # commits: 找 "提交" 或 "Commits" 旁边的数字
        m = re.search(r'(\d+)\s*(?:Commits|提交)', html)
        if m:
            info["commit_count"] = int(m.group(1))
        # 最后 commit 时间: data-hover-url 或 git_log 最新一行时间戳
        m = re.search(r'<time\s+datetime="([^"]+)"', html)
        if m:
            info["date"] = m.group(1)
        return info if len(info) > 1 else None
    except Exception:
        return None


def fetch_gitee_all(owner, repo):
    """统一入口: API 优先, markdown/HTML fallback

    返回: {ok, url, stars, forks, commit_count, sha, date, msg, source, err,
           status, empty}

    ★ 2026-10-22 的核心修复 —— **如实区分三种结局**, 不再一律报 ok=True:
      status="ok"       真的抓到数据了 (有 sha / commit_count / stars / forks)
      status="empty"    空仓库: 学生建了仓库但**没提交任何代码**
                        (Gitee commits API 返回 404 + "Repository is empty")
      status="notfound" 仓库不存在 (404)
      status="failed"   其它失败 (限流/网络/格式变了)

    以前 ok 的判定是 ``... or out["source"]`` —— 只要 source 字段非空就算成功,
    于是 29 个空仓库全部报 "抓取成功", 仪表盘显示成 "⭐0 🍴0 📦commit 0"。
    """
    if not owner or not repo:
        return {"ok": False, "err": "bad gitee url", "status": "failed",
                "empty": False, "source": "", "stars": 0, "forks": 0,
                "commit_count": 0, "sha": "", "date": "", "msg": ""}
    out = {
        "ok": False,
        "url": f"https://gitee.com/{owner}/{repo}",
        "owner": owner,
        "repo": repo,
        "stars": 0,
        "forks": 0,
        "commit_count": 0,
        "sha": "",
        "date": "",
        "msg": "",
        "source": "",
        "err": "",
        "status": "failed",
        "empty": False,
    }
    empty = False
    notfound = False
    # 1) API repo info (200 = 仓库存在; 404 = 仓库不存在)
    api_repo = fetch_gitee_repo_api(owner, repo)
    if api_repo:
        out["stars"] = int(api_repo.get("stargazers_count", 0) or 0)
        out["forks"] = int(api_repo.get("forks_count", 0) or 0)
        out["source"] = "api"
    # 2) API last commit
    api_commit = fetch_gitee_last_commit_api(owner, repo)
    if api_commit:
        if api_commit.get("empty"):
            empty = True
        elif api_commit.get("notfound"):
            notfound = True
        else:
            out.update(api_commit)
            out["source"] = "api"
    # 3) commits count (仅 API)
    if api_repo and not empty and not notfound:
        cnt, truncated, c_empty = fetch_gitee_commits_count_api(owner, repo)
        if c_empty:
            empty = True
        if cnt:
            out["commit_count"] = cnt
        if truncated:
            out["commit_count_truncated"] = True
            out["commit_count_note"] = "500+ (估算被截断)"
    # 4) Markdown fallback (gitee 当前未登录访问返回 markdown)
    #    ★ 空仓库/不存在的仓库**不走 fallback**: markdown 里只有创建日期,
    #      当成 "最后提交时间" 是误导, 而且白白多花两个请求。
    if not empty and not notfound \
            and not out.get("sha") and not out.get("stars") and not out.get("commit_count"):
        md = fetch_gitee_markdown(owner, repo)
        if md:
            out["stars"] = md.get("stars", out["stars"])
            out["forks"] = md.get("forks", out["forks"])
            if md.get("date") and not out.get("date"):
                out["date"] = md["date"]
            out["source"] = out["source"] or "markdown"
    # 5) HTML fallback (legacy)
    if not empty and not notfound \
            and not out.get("sha") and not out.get("stars") and not out.get("commit_count"):
        html = fetch_gitee_html_fallback(owner, repo)
        if html:
            out["stars"] = html.get("stars", out["stars"])
            out["forks"] = html.get("forks", out["forks"])
            out["commit_count"] = html.get("commit_count", out["commit_count"])
            if html.get("date") and not out.get("date"):
                out["date"] = html["date"]
            out["source"] = out["source"] or "html"
    # ★ ok 判定改成**数据驱动**: 有任何一个真实数字才算成功。
    #   (去掉以前的 `or out["source"]` —— 那让空仓库也报成功)
    out["ok"] = bool(out.get("sha") or out.get("commit_count")
                     or out.get("stars") or out.get("forks"))
    if empty:
        out["status"] = "empty"
        out["empty"] = True
        out["err"] = "仓库为空 (未提交任何代码)"
    elif notfound:
        out["status"] = "notfound"
        out["err"] = "仓库不存在 (HTTP 404)"
    elif out["ok"]:
        out["status"] = "ok"
    else:
        out["status"] = "failed"
        out["err"] = out["err"] or "all sources failed (likely 403/rate-limited)"
    return out


# ---------- Snapshot ----------
def load_snapshot():
    if SNAPSHOT.exists():
        try:
            return json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_run": "", "students": {}}


def save_snapshot(snap):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, indent=2),
                        encoding="utf-8")


def diff_posts(prev_posts, curr_posts):
    seen = {p.get("guid") or p.get("link") for p in prev_posts or []}
    return [p for p in curr_posts if (p.get("guid") or p.get("link")) not in seen]


# ---------- Data I/O ----------
def load_existing_data():
    if DATA_JSON.exists():
        try:
            return json.loads(DATA_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_update": "", "total_students": 0, "total_posts": 0, "students": []}


def write_data_json(students_state, fetch_meta, class_stats=None):
    """写 data.json (前端消费)
    students 字段保证有: sid, name, csdn_username, csdn_url, post_count, latest_post,
    posts, total_views, total_likes, total_comments, total_collections, articles_scanned,
    gitee (子对象: ok, url, stars, forks, commit_count, sha, date, msg, source, err)
    """
    out_students = []
    for s in students_state:
        g = s.get("gitee", {}) or {}
        out_students.append({
            "sid": s["sid"],
            "name": s["name"],
            "csdn_username": s.get("csdn_username", ""),
            "csdn_url": s.get("csdn", ""),
            "post_count": len(s.get("posts", [])),
            "latest_post": (s.get("posts", [{}]) or [{}])[0] if s.get("posts") else {},
            "posts": s.get("posts", []),
            "total_views": (s.get("stats") or {}).get("total_views", 0),
            "total_likes": (s.get("stats") or {}).get("total_likes", 0),
            "total_comments": (s.get("stats") or {}).get("total_comments", 0),
            "total_collections": (s.get("stats") or {}).get("total_collections", 0),
            "articles_scanned": (s.get("stats") or {}).get("articles_scanned", 0),
            # ★ C: 三个来源都保留, 报告/前端能看出这个数字是从哪来的
            "profile_views": (s.get("stats") or {}).get("profile_views"),
            "article_views_sum": (s.get("stats") or {}).get("article_views_sum", 0),
            "views_source": (s.get("stats") or {}).get("views_source", "none"),
            "stats_updated_at": (s.get("stats") or {}).get("fetched_at", ""),
            "gitee": {
                "ok": g.get("ok", False),
                "url": g.get("url", ""),
                "owner": g.get("owner", ""),
                "repo": g.get("repo", ""),
                "stars": g.get("stars", 0),
                "forks": g.get("forks", 0),
                "commit_count": g.get("commit_count", 0),
                "sha": g.get("sha", ""),
                "date": g.get("date", ""),
                "msg": g.get("msg", ""),
                "source": g.get("source", ""),
                "err": g.get("err", ""),
                # ★ 2026-10-22: 如实区分空仓库 / 不存在 / 抓取失败
                "status": g.get("status", ""),
                "empty": bool(g.get("empty", False)),
                "fetched_at": g.get("fetched_at", ""),
            },
        })

    total_posts = sum(x["post_count"] for x in out_students)
    data = {
        "last_update": NOW_ISO,
        "total_students": len(out_students),
        "total_posts": total_posts,
        "dashboard_version": DASHBOARD_VERSION,  # P2 #5: 前端读这个显示版本
        "last_fetch_meta": fetch_meta,
        "students": out_students,
    }
    if class_stats:
        data["class_stats"] = class_stats
    DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return data


def write_history_snapshot(data):
    """写 history-real/YYYY-MM-DD.json (累计历史, 不部署)"""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / f"{TODAY}.json"
    # 用现有做底, 加当日记录
    if path.exists():
        try:
            hist = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            hist = {}
    else:
        hist = {"date": TODAY, "snapshots": []}
    hist["snapshots"].append({
        "at": NOW_ISO,
        "total_students": data["total_students"],
        "total_posts": data["total_posts"],
        "class_stats": data.get("class_stats", {}),
        "students_summary": [
            {
                "sid": s["sid"],
                "name": s["name"],
                "post_count": s["post_count"],
                "total_views": s["total_views"],
                "gitee_ok": s["gitee"]["ok"],
                "gitee_commit_count": s["gitee"]["commit_count"],
                "gitee_sha": s["gitee"]["sha"],
                "gitee_date": s["gitee"]["date"],
            }
            for s in data["students"]
        ],
    })
    path.write_text(json.dumps(hist, ensure_ascii=False, indent=2),
                   encoding="utf-8")


# ---------- 班级统计 ----------
def compute_class_stats(students_state):
    """班级整体统计"""
    total = len(students_state)
    has_csdn = sum(1 for s in students_state if s.get("csdn_username"))
    active = sum(1 for s in students_state if len(s.get("posts", [])) > 0)
    total_posts = sum(len(s.get("posts", [])) for s in students_state)
    total_views = sum((s.get("stats") or {}).get("total_views", 0) for s in students_state)
    total_comments = sum((s.get("stats") or {}).get("total_comments", 0) for s in students_state)
    total_collections = sum((s.get("stats") or {}).get("total_collections", 0) for s in students_state)
    total_likes = sum((s.get("stats") or {}).get("total_likes", 0) for s in students_state)

    g_ok = sum(1 for s in students_state if (s.get("gitee") or {}).get("ok"))
    g_total_commits = sum((s.get("gitee") or {}).get("commit_count", 0) for s in students_state)
    g_total_stars = sum((s.get("gitee") or {}).get("stars", 0) for s in students_state)
    g_total_forks = sum((s.get("gitee") or {}).get("forks", 0) for s in students_state)

    # 时间分布 (按 latest_post guid 数字? - 改用 last_fetch_meta)
    return {
        "computed_at": NOW_ISO,
        "students_total": total,
        "students_with_csdn": has_csdn,
        "students_active": active,
        "students_inactive": total - active,
        "csdn": {
            "total_posts": total_posts,
            "total_views": total_views,
            "total_likes": total_likes,
            "total_comments": total_comments,
            "total_collections": total_collections,
            "avg_posts_per_active": round(total_posts / active, 2) if active else 0,
            "avg_views_per_student": round(total_views / total, 1) if total else 0,
        },
        "gitee": {
            "students_with_repo": g_ok,
            "total_commits": g_total_commits,
            "total_stars": g_total_stars,
            "total_forks": g_total_forks,
            "avg_commits_per_student": round(g_total_commits / g_ok, 1) if g_ok else 0,
        },
    }


# ---------- 报告 & 飞书 ----------
def short(s, n=60):
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[:n] + "…"


def fmt_date(s):
    if not s:
        return ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", s)
    if m:
        return f"{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}"
    return short(s, 16)


def build_report(students_state, missing_csdn, fetch_meta, class_stats):
    lines = []
    lines.append(f"📚 **CSDN + Gitee 学生项目日报 · {TODAY}**")
    lines.append("")
    lines.append(
        f"共 {len(students_state)} 人 | CSDN: {class_stats['students_with_csdn']} | "
        f"活跃: {class_stats['students_active']} | "
        f"Gitee 抓取成功: {class_stats['gitee']['students_with_repo']}"
    )
    lines.append("")
    lines.append(
        f"📝 总文章 {class_stats['csdn']['total_posts']} | "
        f"总阅读 {class_stats['csdn']['total_views']:,} | "
        f"总 commit {class_stats['gitee']['total_commits']} | "
        f"star {class_stats['gitee']['total_stars']} | "
        f"fork {class_stats['gitee']['total_forks']}"
    )
    lines.append("")

    if missing_csdn:
        lines.append("⚠️ **缺 CSDN 博客**")
        for s in missing_csdn:
            lines.append(f"- {s['sid']} {s['name']} → 仅 Gitee: {s.get('gitee_url', '')}")
        lines.append("")

    # Gitee 今日 commit
    fresh = [s for s in students_state
             if (s.get("gitee") or {}).get("ok")
             and (s.get("gitee") or {}).get("date", "")[:10] == TODAY]
    if fresh:
        lines.append(f"💾 **今日 Gitee commit**: {len(fresh)} 个仓库")
        for s in fresh[:8]:
            g = s["gitee"]
            lines.append(f"- {s['name']}: {short(g.get('msg', ''))} ({fmt_date(g.get('date', ''))})")
        if len(fresh) > 8:
            lines.append(f"  …还有 {len(fresh)-8} 个")
        lines.append("")

    # Gitee 空仓库 (建了仓库但没提交任何代码 —— 不是抓取失败)
    g_empty = sum(1 for s in students_state
                  if (s.get("gitee") or {}).get("status") == "empty")
    g_notfound = sum(1 for s in students_state
                     if (s.get("gitee") or {}).get("status") == "notfound")
    if g_empty or g_notfound:
        lines.append(f"📦 **Gitee 空仓库 {g_empty} 个**"
                     + (f" (另有 {g_notfound} 个仓库不存在)" if g_notfound else "")
                     + " — 建了仓库但没提交任何代码, 不算抓取失败")
        lines.append("")

    # 抓取健康度 (2026-10-22 R1: 如实显示)
    #  ★ 以前写的是 "成功 {success_count}/{total_requests}" —— 66 是**学生数**,
    #    0 是被 gitee-only 覆盖掉的请求数, 两个数单位都不一样, 自相矛盾。
    #  ★ 现在分成两句话: 本次跑了什么 (人) + 发了多少请求 (次), 单位明确。
    #  ★ 数据来源是 last_run (本次运行), 不是分块 —— 分块是累计的,
    #    拿分块会把上次跑的 CSDN 数据当成本次结果。
    lr = fetch_meta.get("last_run") or {}
    csdn_ms = lr.get("csdn_ms")
    if csdn_ms is None:
        csdn_ms = fetch_meta.get("stages", {}).get("csdn", {}).get("duration_ms", 0)
    gitee_ms = lr.get("gitee_ms")
    if gitee_ms is None:
        gitee_ms = fetch_meta.get("stages", {}).get("gitee", {}).get("duration_ms", 0)
    req = lr.get("total_requests", fetch_meta.get("total_requests", 0))
    ok = lr.get("students_ok", fetch_meta.get("success_count", 0))
    tot = lr.get("students_total") or ok or 0
    if lr.get("csdn_ran") is False:
        reused = lr.get("reused_students", 0)
        lines.append(f"🩺 **抓取健康度**: CSDN 未抓取 (复用 {reused} 人) / "
                     f"Gitee {gitee_ms}ms / 成功 {ok}/{tot} 人 · 请求 {req} 次")
    else:
        lines.append(f"🩺 **抓取健康度**: CSDN {csdn_ms}ms / Gitee {gitee_ms}ms / "
                     f"成功 {ok}/{tot} 人 · 请求 {req} 次")
    lines.append("")

    lines.append("---")
    lines.append("**明细**")
    lines.append("")
    for s in students_state:
        name = f"{s['name']}({s['sid']})"
        # --gitee-only 时 csdn_status = "skipped_reused", 表示 CSDN 数据是
        # 原样复用过来的 (不是没抓到)。它必须按 "ok" 渲染, 否则整份日报
        # 会显示成 "CSDN 未抓取" 的假报告 (2026-10-21 05:30 实测踩到)。
        csdn_st = "✅" if s.get("csdn_status") in ("ok", "skipped_reused") else ("⚠️" if s.get("csdn_status") == "no_csdn" else "❌")
        g = s.get("gitee") or {}
        gitee_st = "✅" if g.get("ok") else ("⚠️" if g.get("err") and "rate" in (g.get("err") or "").lower() else "❌")
        lines.append(f"{csdn_st}{gitee_st} **{name}**")
        # CSDN
        if s.get("csdn_status") == "no_csdn":
            lines.append(f"   - CSDN: 未提供")
        elif s.get("csdn_status") in ("ok", "skipped_reused"):
            new_n = len(s.get("new_posts", []))
            suffix = " · (复用上次数据)" if s.get("csdn_status") == "skipped_reused" else ""
            lines.append(
                f"   - CSDN: @{s.get('csdn_username')} · "
                f"文章 {len(s['posts'])} · 阅读 {s.get('stats',{}).get('total_views', 0):,}"
                + (f" · **新 {new_n}**" if new_n else "")
                + suffix
            )
            for p in s.get("new_posts", [])[:3]:
                lines.append(f"     - {short(p['title'])} ({fmt_date(p.get('pubDate', ''))})")
        else:
            lines.append(f"   - CSDN: 未抓取 ({s.get('csdn_err', '?')})")
        # Gitee
        if g.get("ok"):
            lines.append(
                f"   - Gitee: ⭐{g.get('stars', 0)} 🍴{g.get('forks', 0)} "
                f"📦commit {g.get('commit_count', 0)} | {g.get('sha', '')} "
                f"{fmt_date(g.get('date', ''))} {short(g.get('msg', ''))} ({g.get('source', '')})"
            )
        else:
            # ★ 2026-10-22 (d280330): 如实区分空仓库 / 仓库不存在 / 真失败
            if g.get("status") == "empty":
                lines.append("   - Gitee: 空仓库 (建了仓库但没提交任何代码)")
            elif g.get("status") == "notfound":
                lines.append("   - Gitee: 仓库不存在 (HTTP 404)")
            else:
                lines.append(f"   - Gitee: 未抓取 ({g.get('err') or '?'})")
        lines.append("")
    return "\n".join(lines)


def build_summary(students_state, new_posts_total, class_stats):
    lines = [
        f"📚 **CSDN + Gitee 学生项目日报** · {TODAY}",
        "",
        f"📊 **{class_stats['students_total']} 人** | "
        f"活跃 {class_stats['students_active']} | "
        f"新文章 **{new_posts_total}** 篇 | "
        f"今日 Gitee commit {class_stats['gitee']['total_commits'] - sum(1 for s in students_state if (s.get('gitee') or {}).get('date','')[:10] != TODAY and (s.get('gitee') or {}).get('ok'))}",
    ]
    missing_csdn = [s for s in students_state if s.get("csdn_status") == "no_csdn"]
    # "skipped_reused" = --gitee-only 时复用了上次的 CSDN 数据, 不是失败
    failed_csdn = [s for s in students_state
                   if s.get("csdn_status") not in ("ok", "skipped_reused", "no_csdn")]
    failed_gitee = [s for s in students_state
                    if (s.get("gitee") or {}).get("status") in ("failed", "notfound")]
    # ★ 2026-10-22 (d280330): 空仓库 (建了仓库但没提交) **不是抓取失败**,
    #   以前它 ok=False 被算进 failed_gitee, 日报显示"Gitee 抓取失败 29 人"
    #   —— 实际上那 29 个人只是还没提交代码。
    g_empty = [s for s in students_state
               if (s.get("gitee") or {}).get("status") == "empty"]
    if missing_csdn:
        names = "、".join(s["name"] for s in missing_csdn[:5])
        lines.append(f"⚠️ 缺 CSDN: {names}{' 等 ' + str(len(missing_csdn)) if len(missing_csdn) > 5 else ''}")
    if failed_gitee:
        n = len(failed_gitee)
        lines.append(f"🔥 Gitee 抓取失败: {n} 人(可能限流)")
    if g_empty:
        lines.append(f"📦 Gitee 空仓库: {len(g_empty)} 个 (建了仓库但没提交代码)")
    # 今日 commit
    fresh = [s for s in students_state
             if (s.get("gitee") or {}).get("ok")
             and (s.get("gitee") or {}).get("date", "")[:10] == TODAY]
    if fresh:
        lines.append(f"💾 今日 commit: {len(fresh)} 仓库")
        for s in fresh[:6]:
            lines.append(f"  • {s['name']} · {short((s.get('gitee') or {}).get('msg',''))}")
    lines.append("")
    lines.append("📎 完整报告见附件")
    return "\n".join(lines)


def send_feishu(text, dry=False, attachment=None):
    if dry:
        safe_print(f"[DRY-RUN] feishu skipped, len={len(text)}, attachment={attachment}")
        return True
    openclaw_cmd = shutil.which("openclaw") or shutil.which("openclaw.cmd")
    if not openclaw_cmd:
        for p in [r"C:\Users\Administrator\AppData\Roaming\npm\openclaw.cmd",
                  r"C:\Users\Administrator\AppData\Roaming\npm\openclaw"]:
            if os.path.exists(p):
                openclaw_cmd = p
                break
    if not openclaw_cmd:
        safe_print("[feishu] ERROR: openclaw not found")
        return False
    # stdout 被重定向到日志文件时, 代码页可能是 GBK, 打印 emoji 会抛
    # UnicodeEncodeError —— 那会让"其实已经发成功"看起来像"发送失败"。
    # 这里用**模块级** safe_print (统一一处, 避免各写各的漏改)。

    try:
        cmd = [openclaw_cmd, "message", "send",
               "--channel", "feishu",
               "--account", "main",
               "--target", "ou_503adf68c6c9bd05f755ec39aacb2acc",
               "--message", text]
        if attachment and Path(attachment).exists():
            cmd.extend(["--media", str(attachment)])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                encoding="utf-8", errors="replace")
        out = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            safe_print(f"[feishu] failed: rc={result.returncode} out={out[:300]}")
            return False
        safe_print(f"[feishu] sent ok, {len(text)} chars; out={out[:160]}")
        # extract message_id from output if present
        m = re.search(r'(?:message_id|msg_id|"id")\s*[:=]\s*"?([A-Za-z0-9_-]+)"?', out)
        if m:
            safe_print(f"[feishu] message_id: {m.group(1)}")
        return True
    except Exception as e:
        safe_print(f"[feishu] exception: {type(e).__name__}: {e}")
        return False


# ---------- 主流程 ----------
def _count_actual_requests(students_state=None):
    """数**真实** HTTP 请求数。

    ★ 2026-10-21: CSDN 部分用**真实计数器** CSDN_REQUESTS (由 csdn_get 累加),
      不用"1 列表 + 1 profile + N 篇"去估算 —— 估算在复用/放弃/限流重试
      这些情况下会算错, 而报告里要用它对比"增量 vs 全量"的请求数, 必须准。

    ★ 2026-10-22 (R1): Gitee 部分也改成**真实计数**。
      以前是"按 source 估算" (api=3 / html=1 / **markdown=0**),
      于是 gitee-only 那一跑 total_requests=0, 仪表盘健康度卡片显示
      "尚未运行抓取任务" 整整 19 小时 (2026-10-22 05:30 那次就是它)。
      d280330 已经加了 _gitee_count_request 的真实计数器, 这里直接接上,
      **不再重复实现一套估算**。
    """
    return CSDN_REQUESTS + GITEE_REQUESTS


def fetch_csdn_for_student(st):
    """P2 #9 修复: gitee 不再存 URL, 统一用 gitee_url 字段, gitee{} 留给 API 结果."""
    rec = {
        "sid": st["sid"], "name": st["name"],
        "csdn": st["csdn"], "note": st.get("note", ""),
        "csdn_username": "",
        "csdn_status": "no_csdn" if not st["csdn"] else "pending",
        "posts": [], "new_posts": [], "stats": {}, "csdn_err": "",
        "gitee_status": "pending",
        "gitee_url": st.get("gitee", ""),
        "gitee": {},  # 仅存放 fetch_gitee_all() 的结果
    }
    if st["csdn"]:
        user = extract_csdn_username(st["csdn"])
        rec["csdn_username"] = user
        if user:
            # Ma: 修复 - 之前 limit=5 导致很多人只显示 5 篇,改为 50
            posts = fetch_csdn_html(user, limit=50)
            if not posts:
                posts = fetch_csdn_rss(user, limit=50)
            if posts:
                rec["posts"] = posts
                rec["csdn_status"] = "ok"
            else:
                rec["csdn_status"] = "failed"
                rec["csdn_err"] = "HTML+RSS empty"
    return rec


def fetch_stats_for(rec, no_reuse=False):
    """P2 #6 修复: 单学生失败时 fallback 到上次的 stats, 而不是清零."""
    if rec["csdn_status"] == "ok" and rec.get("csdn_username"):
        try:
            rec["stats"] = aggregate_user_stats(rec["csdn_username"], rec["posts"],
                                                no_reuse=no_reuse)
        except Exception as e:
            # 单学生失败 - fallback 到上次 data
            prev = load_existing_data()
            prev_by_sid = {s["sid"]: s for s in prev.get("students", [])}
            p = prev_by_sid.get(rec["sid"], {})
            rec["stats"] = {
                "total_views": p.get("total_views", 0),
                "total_likes": p.get("total_likes", 0),
                "total_comments": p.get("total_comments", 0),
                "total_collections": p.get("total_collections", 0),
                "profile_views": p.get("profile_views"),
                "article_views_sum": p.get("article_views_sum", 0),
                "views_source": p.get("views_source", "preserved"),
                "articles_scanned": p.get("articles_scanned", 0),
                "fetched_at": p.get("last_fetch_meta", {}).get("fetched_at", ""),
                "error": str(e)[:100],
            }
            # P0-3: 保护旧 posts (含 stats_updated_at) — 否则兜底分支
            # 写入的 4 字段 posts 会让前端全部显示 "—"
            prev_posts = p.get("posts") or []
            has_stale_stats = any(
                isinstance(post, dict) and post.get("stats_updated_at")
                for post in prev_posts
            )
            if has_stale_stats:
                rec["posts"] = prev_posts
                rec["csdn_err"] = f"stats_failed_posts_preserved: {str(e)[:80]}"
                safe_print(f"[stats-fallback-preserve] {rec.get('name')}: kept {len(prev_posts)} old posts with stats_updated_at")
            else:
                rec["csdn_err"] = f"stats_failed: {str(e)[:80]}"
            safe_print(f"[stats-fallback] {rec.get('name')}: {e}")
    else:
        rec["stats"] = {
            "total_views": 0, "total_likes": 0, "total_comments": 0,
            "total_collections": 0, "profile_views": 0, "articles_scanned": 0,
        }
    return rec


def fetch_gitee_for(rec):
    owner, repo = parse_gitee_repo(rec.get("gitee_url", ""))
    g = fetch_gitee_all(owner, repo)
    g["fetched_at"] = NOW_ISO
    rec["gitee"] = g
    rec["gitee_status"] = "ok" if g.get("ok") else "failed"
    return rec


STATS_KEYS = ("views", "likes", "comments", "collections", "stats_updated_at")


def merge_posts_stats(new_posts, prev_posts):
    """把上一轮的 stats 合并进这一轮新拿到的 posts。

    为什么要这个: 列表抓取成功但**文章详情被限流**时, 新拿到的 posts 只有
    title/link/pubDate/guid 四个字段 (没有 stats), 前端会全部显示 "—"。
    skill student-data-monitor 说得很清楚:
        "The fallback must preserve posts, not just stats."
    这里做得比"整个替换成 prev 数组"更好一点:
      - 新发现的文章保留 (prev 里没有的)
      - prev 里有、新一轮列表里没了的文章也保留 (不因为一次抓取异常就丢文章)
      - 同一篇文章的 stats 用 prev 的补上 (如果新的没有)
    """
    prev_by_link = {}
    for p in (prev_posts or []):
        link = p.get("link") or p.get("guid")
        if link:
            prev_by_link[link] = p

    merged = []
    seen_links = set()
    for p in (new_posts or []):
        link = p.get("link") or p.get("guid")
        seen_links.add(link)
        prev = prev_by_link.get(link)
        if prev:
            has_stat = any(prev.get(k) is not None for k in STATS_KEYS)
            if has_stat:
                for k in STATS_KEYS:
                    if p.get(k) is None and prev.get(k) is not None:
                        p[k] = prev[k]
        merged.append(p)

    # prev 里有、这一轮没抓到的文章也保留 (防止一次异常导致文章数塌方)
    for p in (prev_posts or []):
        link = p.get("link") or p.get("guid")
        if link and link not in seen_links:
            merged.append(p)
    return merged


def apply_data_protection(students_state, prev_data, verbose=True):
    """★ D: 数据保护 —— 如果这次抓取明显残废, 保留上一次的 posts/stats。

    ═══════════════════════════════════════════════════════════════════════
    为什么要加 "stats 维度" 的触发臂 (2026-10-21)
    ═══════════════════════════════════════════════════════════════════════
    原来的保护只有一个触发臂:
        csdn_fail_count > 0.3 * len(students_state)
    而 csdn_fail_count **只统计列表抓取失败**。列表抓取 34/34 全成功,
    只是**文章详情被 CSDN 的 521 风控拦了** -> csdn_fail_count = 0
    -> 保护永远不触发 -> 00:00 那次的好数据 (total_views 7604)
    被 05:30 那次的零值 (1297) 覆盖掉。

    触发臂 (任一成立即视为"这次抓取明显残废"):
      A) 列表维度 (原有): 列表失败率 > 30% 且 总文章数 < 上次 70%
      B) stats 维度 1: sum(articles_scanned) < 上次 60% 且 上次 > 10
      C) stats 维度 2: sum(total_views) == 0 或 < 上次 30%, 且 上次 > 1000
      D) stats 维度 3: 总 posts < 上次 70%

    触发后的保留范围:
      - **整个 post 数组** (含 views/likes/comments/collections/stats_updated_at),
        不能只保 stats —— 只保 stats 的话前端每篇文章还是显示 "—"
        (见 skill student-data-monitor: "The fallback must preserve posts,
        not just stats")。
      - 上次 total_views > 0 而这次是 0 时, 保留上次的 total_views。
    """
    prev_by_sid = {s["sid"]: s for s in prev_data.get("students", [])}
    csdn_fail_count = sum(1 for s in students_state if s.get("csdn_status") == "failed")
    prev_total_posts = prev_data.get("total_posts", 0)
    new_total_posts_est = sum(len(s.get("posts", [])) for s in students_state)

    def _sum_stat(key):
        return sum((s.get("stats") or {}).get(key, 0) or 0 for s in students_state)

    prev_scanned = sum((s.get("articles_scanned", 0) or 0)
                       for s in prev_data.get("students", []))
    prev_views = sum((s.get("total_views", 0) or 0)
                     for s in prev_data.get("students", []))
    new_scanned = _sum_stat("articles_scanned")
    new_views = _sum_stat("total_views")

    should_preserve = False
    reasons = []
    if prev_total_posts > 0:
        # A) 原有臂: 列表抓取失败
        if (csdn_fail_count > 0.3 * len(students_state)
                and new_total_posts_est < 0.7 * prev_total_posts):
            should_preserve = True
            reasons.append(
                f"CSDN 列表失败率 {csdn_fail_count}/{len(students_state)} > 30% "
                f"AND 新文章 {new_total_posts_est} < 70% of 上次 {prev_total_posts}")
        # B) stats 维度: 抓到的文章数骤降
        if prev_scanned > 10 and new_scanned < 0.6 * prev_scanned:
            should_preserve = True
            reasons.append(
                f"articles_scanned {new_scanned} < 60% of 上次 {prev_scanned}")
        # C) stats 维度: 阅读量塌方
        if prev_views > 1000 and (new_views == 0 or new_views < 0.3 * prev_views):
            should_preserve = True
            reasons.append(
                f"total_views {new_views} 塌方 (上次 {prev_views}, "
                f"门槛 {int(0.3 * prev_views)})")
        # D) stats 维度: 总文章数骤降
        if new_total_posts_est < 0.7 * prev_total_posts:
            should_preserve = True
            reasons.append(
                f"总文章数 {new_total_posts_est} < 70% of 上次 {prev_total_posts}")

    if verbose and reasons:
        safe_print(f"[protect] 触发数据保护 ({len(reasons)} 条):")
        for r in reasons:
            safe_print(f"[protect]   - {r}")
        safe_print("[protect] preserving previous posts/stats per student")

    if not should_preserve:
        return False

    for rec in students_state:
        p = prev_by_sid.get(rec["sid"])
        if not p:
            continue
        prev_posts = p.get("posts") or []
        prev_total = p.get("total_views", 0) or 0
        rec_total = (rec.get("stats") or {}).get("total_views", 0) or 0
        rec_posts = rec.get("posts") or []

        # 需要保 posts: 这次没抓到文章, 或者列表抓取失败,
        #              或者新抓到的 posts 全都没有 stats (详情被限流了)
        prev_has_stats = any(
            isinstance(pp, dict) and pp.get("stats_updated_at") is not None
            for pp in prev_posts)
        new_has_stats = any(
            isinstance(pp, dict) and pp.get("stats_updated_at") is not None
            for pp in rec_posts)
        need_posts = ((not rec_posts)
                      or (rec.get("csdn_status") == "failed")
                      or (prev_has_stats and not new_has_stats))

        # 需要保 stats: 这次 stats 缺失, 或者这次是 0 而上次不是 0,
        #              或者这次比上次腰斩 (阅读量只涨不跌, 腰斩=抓取残废)
        need_stats = ((not rec.get("stats"))
                      or (rec_total == 0 and prev_total > 0)
                      or (prev_total > 100 and rec_total < 0.5 * prev_total))

        if need_posts and prev_posts:
            # ★ 保留整个 post 数组 (含 views/likes/comments/collections/
            #   stats_updated_at), 不能只保 stats —— 只保 stats 的话前端
            #   每篇文章还是显示 "—"。
            rec["posts"] = merge_posts_stats(rec_posts, prev_posts)
            rec["csdn_username"] = p.get("csdn_username", rec.get("csdn_username", ""))
            rec["latest_post"] = p.get("latest_post", {})
        if need_stats and prev_total > 0:
            rec["stats"] = {
                "total_views": prev_total,
                "total_likes": p.get("total_likes", 0),
                "total_comments": p.get("total_comments", 0),
                "total_collections": p.get("total_collections", 0),
                "profile_views": p.get("profile_views"),
                "article_views_sum": p.get("article_views_sum", 0),
                "views_source": p.get("views_source", "preserved"),
                "articles_scanned": p.get("articles_scanned", 0),
                "articles_skipped_reuse": 0,
                "fetched_at": p.get("stats_updated_at", ""),
                "preserved": True,
            }
    return True


def build_fetch_meta(students_state, prev_meta, run_csdn=True, run_gitee=True,
                     csdn_ms=0, gitee_ms=0, total_ms=0, no_reuse=False):
    """构造 last_fetch_meta (2026-10-22 R1: **按模式分块**, 互不覆盖)。

    根因回顾: 增量脚本和 v2 抢同一个 last_fetch_meta, 后写的赢 ——
    v2 的 gitee-only 会把整个 last_fetch_meta 换成新字典, 增量那一跑的
    438 个请求被彻底抹掉 (仪表盘健康度卡片显示"尚未运行抓取任务" 19 小时)。

    现在的结构:
        {
          "fetched_at":  最近一次运行时间,
          "incremental": {...CSDN 抓取那一路...},
          "gitee":       {...Gitee 抓取那一路...},
          "last_run":    {...本次运行的明细 (日报"抓取健康度"行读它)...},
          "total_requests" / "success_count" / "fail_count": 两个分块之和,
          "stages" / "method_breakdown" / ...: 旧字段 (旧版前端兼容),
        }
    每一块只由**自己那条链**写, 谁都不动别人的块。
    """
    csdn_ok = sum(1 for s in students_state
                  if s.get("csdn_status") in ("ok", "skipped_reused"))
    csdn_fail = sum(1 for s in students_state
                    if s.get("csdn_status") not in ("ok", "skipped_reused", "no_csdn"))
    gitee_ok = sum(1 for s in students_state if (s.get("gitee") or {}).get("ok"))
    gitee_fail = sum(1 for s in students_state if not (s.get("gitee") or {}).get("ok"))
    gitee_empty = sum(1 for s in students_state
                      if (s.get("gitee") or {}).get("status") == "empty")
    gitee_notfound = sum(1 for s in students_state
                          if (s.get("gitee") or {}).get("status") == "notfound")
    gitee_failed = max(0, gitee_fail - gitee_empty - gitee_notfound)
    reused_students = sum(1 for s in students_state
                           if s.get("csdn_status") == "skipped_reused")
    new_posts_total = sum(len(s.get("new_posts", [])) for s in students_state)

    meta = dict(prev_meta or {})
    meta["fetched_at"] = NOW_ISO
    meta["duration_ms"] = total_ms
    meta["mode"] = ("both" if (run_csdn and run_gitee)
                    else ("csdn" if run_csdn else "gitee"))

    if run_csdn:
        meta["incremental"] = {
            "fetched_at": NOW_ISO,
            "mode": "full" if no_reuse else "monitor",
            "duration_ms": csdn_ms,
            "total_requests": CSDN_REQUESTS,
            "students_ok": csdn_ok,
            "students_total": len(students_state),
            "students_skipped": csdn_fail,
            "new_posts": new_posts_total,
            "class_total_views": sum((s.get("stats") or {}).get("total_views", 0) or 0
                                     for s in students_state),
        }
    if run_gitee:
        meta["gitee"] = {
            "fetched_at": NOW_ISO,
            "mode": "gitee",
            "duration_ms": gitee_ms,
            "total_requests": GITEE_REQUESTS,
            "breakdown": dict(GITEE_REQUEST_BREAKDOWN),
            "ok": gitee_ok,
            "empty": gitee_empty,
            "notfound": gitee_notfound,
            "failed": gitee_failed,
            "students_total": len(students_state),
            "total_commits": sum((s.get("gitee") or {}).get("commit_count", 0) or 0
                                 for s in students_state),
            "total_stars": sum((s.get("gitee") or {}).get("stars", 0) or 0
                               for s in students_state),
            "total_forks": sum((s.get("gitee") or {}).get("forks", 0) or 0
                               for s in students_state),
        }

    # 顶层汇总 = 两个分块之和 (仪表盘一次能看到"增量刷了"和"Gitee 刷了")
    _inc = meta.get("incremental") or {}
    _git = meta.get("gitee") or {}
    meta["total_requests"] = ((_inc.get("total_requests") or 0)
                              + (_git.get("total_requests") or 0))
    meta["success_count"] = ((_inc.get("students_ok") or 0)
                             + (_git.get("ok") or 0))
    meta["fail_count"] = ((_inc.get("students_skipped") or 0)
                          + (_git.get("failed") or 0))
    # 本次运行的明细 (日报的"抓取健康度"行读它 —— 不能读分块,
    # 因为分块是**累计**的, 会把上次跑的 CSDN 数据当成本次结果)
    meta["last_run"] = {
        "fetched_at": NOW_ISO,
        "mode": meta["mode"],
        "csdn_ran": bool(run_csdn),
        "csdn_ms": csdn_ms if run_csdn else 0,
        "gitee_ms": gitee_ms if run_gitee else 0,
        "duration_ms": total_ms,
        "total_requests": ((CSDN_REQUESTS if run_csdn else 0)
                           + (GITEE_REQUESTS if run_gitee else 0)),
        "students_ok": csdn_ok if run_csdn else reused_students,
        "students_total": len(students_state),
        "gitee_ok": gitee_ok,
        "gitee_empty": gitee_empty,
        "reused_students": reused_students,
    }
    meta["method_breakdown"] = {
        "csdn_html": sum(1 for s in students_state if s.get("csdn_status") == "ok"),
        "csdn_rss": 0,
        "gitee_api": sum(1 for s in students_state
                         if (s.get("gitee") or {}).get("source") == "api"),
        "gitee_html": sum(1 for s in students_state
                          if (s.get("gitee") or {}).get("source") == "html"),
        "gitee_markdown": sum(1 for s in students_state
                              if (s.get("gitee") or {}).get("source") == "markdown"),
    }
    meta["csdn_requests"] = CSDN_REQUESTS
    meta["gitee_requests"] = GITEE_REQUESTS
    meta["rate_limiter"] = CSDN_LIMITER.stats()
    # 旧字段保留 (前端旧版 + 历史数据兼容)
    meta["stages"] = {
        "csdn": {"duration_ms": csdn_ms, "ok": csdn_ok, "fail": csdn_fail},
        "gitee": {"duration_ms": gitee_ms, "ok": gitee_ok, "fail": gitee_fail},
    }
    return meta


def run(args):
    wl = find_watchlist()
    if not wl:
        print(f"[FATAL] watchlist not found in any of: {[str(p) for p in WATCHLIST_PATHS]}", file=sys.stderr)
        sys.exit(1)
    safe_print(f"[watchlist] {wl}")
    students = parse_watchlist(wl)
    safe_print(f"[watchlist] parsed {len(students)} students")
    prev = load_snapshot()
    prev_students = prev.get("students", {})

    # ★ E: 建 link -> 上次 post 的索引, 供 fetch_csdn_html 合并 stats。
    #   来源两个: cache/csdn/snapshot.json (历史快照) 和 data.json (当前),
    #   snapshot 优先 (它更新), data.json 兜底。
    PREV_POSTS_BY_LINK.clear()

    def _index_posts(posts):
        for _p in (posts or []):
            _link = _p.get("link") or _p.get("guid")
            if not _link:
                continue
            _old = PREV_POSTS_BY_LINK.get(_link)
            if _old is None:
                PREV_POSTS_BY_LINK[_link] = _p
            elif (_old.get("stats_updated_at") is None
                  and _p.get("stats_updated_at") is not None):
                PREV_POSTS_BY_LINK[_link] = _p

    for _sid, _stu in prev_students.items():
        _index_posts(_stu.get("posts"))
    for _pdata in load_existing_data().get("students", []):
        _index_posts(_pdata.get("posts"))
    _with_stats = sum(1 for _p in PREV_POSTS_BY_LINK.values()
                      if _p.get("stats_updated_at") is not None)
    safe_print(f"[prev-index] 载入 {len(PREV_POSTS_BY_LINK)} 篇历史 post "
          f"(其中 {_with_stats} 篇带 stats, 可复用)")

    run_csdn = not args.gitee_only
    run_gitee = not args.csdn_only

    t_start = time.time()

    # --- Stage 1: CSDN ---
    csdn_t = time.time()
    students_state = []
    if run_csdn:
        # 线程数 3 -> 2: 瓶颈是 CSDN 的 IP 级风控, 线程多只会更快撞 521
        # 然后在重试里白白烧掉 10 分钟 (实测旧代码 CSDN 阶段 627 秒)。
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = {ex.submit(fetch_csdn_for_student, st): st for st in students}
            for fut in as_completed(futs):
                students_state.append(fut.result())
        # stats
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = {ex.submit(fetch_stats_for, rec, args.no_reuse): rec
                    for rec in students_state}
            for fut in as_completed(futs):
                fut.result()
    else:
        # skip CSDN, 但保留 students_state 结构
        for st in students:
            students_state.append({
                "sid": st["sid"], "name": st["name"],
                "csdn": st["csdn"], "gitee_url": st["gitee"], "gitee": {},
                "csdn_username": "",
                "csdn_status": "skipped",
                "posts": [], "new_posts": [], "stats": {}, "csdn_err": "skipped (--gitee-only)",
            })
        # 复用旧 data 的 stats (如果有)
        prev_data = load_existing_data()
        prev_by_sid = {s["sid"]: s for s in prev_data.get("students", [])}
        for rec in students_state:
            p = prev_by_sid.get(rec["sid"])
            if p:
                rec["posts"] = p.get("posts", [])
                rec["csdn_username"] = p.get("csdn_username", "")
                rec["csdn_status"] = "skipped_reused"
                # --gitee-only: CSDN 的数据原样复用, 一个字段都不能丢
                # (特别是 profile_views / article_views_sum / views_source,
                #  丢了就没法看出阅读量的数据来源了)
                rec["stats"] = {
                    "total_views": p.get("total_views", 0),
                    "total_likes": p.get("total_likes", 0),
                    "total_comments": p.get("total_comments", 0),
                    "total_collections": p.get("total_collections", 0),
                    "profile_views": p.get("profile_views"),
                    "article_views_sum": p.get("article_views_sum", 0),
                    "views_source": p.get("views_source", "reused"),
                    "articles_scanned": p.get("articles_scanned", 0),
                    "fetched_at": p.get("stats_updated_at", ""),
                }
    csdn_ms = int((time.time() - csdn_t) * 1000)

    # --- Stage 2: Gitee ---
    gitee_t = time.time()
    if run_gitee:
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(fetch_gitee_for, rec): rec for rec in students_state}
            for fut in as_completed(futs):
                fut.result()
    gitee_ms = int((time.time() - gitee_t) * 1000)

    total_ms = int((time.time() - t_start) * 1000)

    # --- diff new posts ---
    for rec in students_state:
        if rec.get("csdn_status") == "ok":
            prev_posts = prev_students.get(rec["sid"], {}).get("posts", [])
            rec["new_posts"] = diff_posts(prev_posts, rec["posts"])

    missing_csdn = [s for s in students_state if s.get("csdn_status") == "no_csdn"]

    # --- fetch_meta (2026-10-22 R1: 按模式分块, 互不覆盖) ---
    #  ★ 两个脚本各写各的块: 增量脚本写 incremental 块, v2 写 gitee 块,
    #    谁都不覆盖谁 —— 以前是"后写的赢", 增量那一跑的 438 个请求会被
    #    gitee-only 整个抹掉 (仪表盘 19 小时假告警的根因)。
    prev_meta = (load_existing_data().get("last_fetch_meta") or {})
    new_posts_total = sum(len(s.get("new_posts", [])) for s in students_state)
    fetch_meta = build_fetch_meta(students_state, prev_meta,
                                   run_csdn=run_csdn, run_gitee=run_gitee,
                                   csdn_ms=csdn_ms, gitee_ms=gitee_ms,
                                   total_ms=total_ms, no_reuse=args.no_reuse)

    # --- 班级统计 ---

    # Ma: 数据保护 — 如果这次抓取明显失败（如 CSDN 全失败）,
    # 保留之前 data.json 里的 posts/stats/csdn_username 而不是覆盖为空。
    # 否则限流/网络故障一次,所有 129 篇博客就全没了。
    prev_data = load_existing_data()
    apply_data_protection(students_state, prev_data)

    class_stats = compute_class_stats(students_state)

    # --- 写 data.json ---
    data = write_data_json(students_state, fetch_meta, class_stats)
    safe_print(f"[data.json] written: {DATA_JSON} ({len(data['students'])} students, "
          f"{data['total_posts']} posts)")

    # --- history snapshot ---
    write_history_snapshot(data)
    safe_print(f"[history] appended to {HISTORY_DIR / (TODAY + '.json')}")

    # --- 更新 cache snapshot ---
    new_snap = {
        "last_run": NOW_ISO,
        "students": {
            s["sid"]: {
                "name": s["name"],
                "posts": s["posts"],
                "gitee": s.get("gitee", {}),
                "stats": s.get("stats", {}),
            } for s in students_state
        },
    }
    save_snapshot(new_snap)
    safe_print(f"[snapshot] saved -> {SNAPSHOT}")

    # --- 报告 ---
    report = build_report(students_state, missing_csdn, fetch_meta, class_stats)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rep_path = REPORT_DIR / f"csdn-report-{TODAY}.md"
    rep_path.write_text(report, encoding="utf-8")
    safe_print(f"[report] saved -> {rep_path}")

    summary = build_summary(students_state, new_posts_total, class_stats)

    # --- 推飞书 (除非 dry) ---
    if not args.dry_run:
        send_feishu(summary, dry=False, attachment=rep_path)
    else:
        safe_print("[dry-run] feishu skipped")
        safe_print("---SUMMARY---")
        safe_print(summary)
        safe_print("---REPORT (head)---")
        safe_print("\n".join(report.splitlines()[:50]))

    # --- token 用量回报 ---
    safe_print(f"[timing] total {total_ms}ms = CSDN {csdn_ms}ms + Gitee {gitee_ms}ms")
    safe_print(f"[stats] csdn_ok={csdn_ok}/{len(students_state)}, "
          f"gitee_ok={gitee_ok}/{len(students_state)}, "
          f"new_posts={new_posts_total}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="CSDN + Gitee monitor v2")
    ap.add_argument("--force-now", action="store_true",
                    help="跳过 00:00-06:00 时间窗检查")
    ap.add_argument("--skip-token-check", action="store_true",
                    help="跳过 token 预算检查")
    ap.add_argument("--dry-run", action="store_true",
                    help="只跑抓取不推飞书")
    ap.add_argument("--gitee-only", action="store_true",
                    help="只跑 Gitee 抓取阶段")
    ap.add_argument("--csdn-only", action="store_true",
                    help="只跑 CSDN 抓取阶段")
    ap.add_argument("--no-reuse", action="store_true",
                    help="忽略 24h stats 复用, 全部文章重抓 (每周日全量兜底用)")
    args = ap.parse_args()

    # 时间窗
    if not args.force_now and not in_time_window():
        safe_print(f"[SKIP] 当前时间 {datetime.now().strftime('%H:%M')} 不在 "
              f"{WINDOW_START:02d}:00-{WINDOW_END:02d}:00 窗口内. "
              f"用 --force-now 跳过.")
        return 0

    # token 预算
    if not args.skip_token_check and not token_budget_ok():
        safe_print("[SKIP] token 剩余不足 75%, 跳过.")
        return 0

    return run(args)


if __name__ == "__main__":
    sys.exit(main())