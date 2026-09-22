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
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

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
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://blog.csdn.net/",
}



def _parse_relative_time_to_iso(s):
    """Ma: CSDN HTML view-time-box 返回 '博文更新于 22 小时前' 或 '2026.09.17'.
    解析成 ISO 日期字符串,失败返回 ''"""
    if not s:
        return ""
    s = s.strip()
    # 1. 绝对日期 YYYY.MM.DD
    m = re.search(r"(\d{4})[.-](\d{1,2})[.-](\d{1,2})", s)
    if m:
        y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
        try:
            import datetime as _dt
            return _dt.date(int(y), int(mo), int(d)).isoformat()
        except Exception:
            return ""
    # 2. 相对时间
    import datetime as _dt
    now = _dt.datetime.now()
    m = re.search(r"(\d+)\s*小时前", s)
    if m:
        return (now - _dt.timedelta(hours=int(m.group(1)))).isoformat(timespec="seconds")
    m = re.search(r"(\d+)\s*分钟前", s)
    if m:
        return (now - _dt.timedelta(minutes=int(m.group(1)))).isoformat(timespec="seconds")
    m = re.search(r"(\d+)\s*天前", s)
    if m:
        return (now - _dt.timedelta(days=int(m.group(1)))).date().isoformat()
    m = re.search(r"(\d+)\s*周前", s)
    if m:
        return (now - _dt.timedelta(weeks=int(m.group(1)))).date().isoformat()
    m = re.search(r"(\d+)\s*月前", s)
    if m:
        return (now - _dt.timedelta(days=30 * int(m.group(1)))).date().isoformat()
    return ""


def fetch_csdn_html(username, limit=5):
    items = []
    urls = [
        f"https://blog.csdn.net/{username}?type=blog",
        f"https://blog.csdn.net/{username}",
    ]
    for url in urls:
        try:
            r = http_get(url, timeout=12, headers=CSDN_HEADERS, retries=2)
            if r.status_code != 200 or not r.text:
                continue
            html = r.text
            blocks = re.findall(
                r'<article[^>]*class="blog-list-box"[^>]*>(.*?)</article>',
                html, re.DOTALL,
            ) or re.findall(r'<article[^>]*>(.*?)</article>', html, re.DOTALL)
            seen = set()
            for block in blocks:
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
                # Ma: 提取发布时间 — 优先 view-time-box (相对或绝对时间)
                time_match = re.search(
                    r'<div class="view-time-box"[^>]*>\s*博文更新于\s*([^<·]+?)\s*(?:·|</div>)',
                    block,
                )
                raw_time = time_match.group(1).strip() if time_match else ""
                pub_iso = _parse_relative_time_to_iso(raw_time)
                items.append({"title": title, "link": link, "pubDate": pub_iso, "guid": link})
                if len(items) >= limit:
                    break
            if items:
                return items[:limit]
        except Exception:
            continue
    return items


def fetch_csdn_rss(username, limit=5):
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
    if not username:
        return 0
    for url in [
        f"https://blog.csdn.net/{username}?type=blog",
        f"https://blog.csdn.net/{username}",
    ]:
        try:
            r = http_get(url, timeout=10, headers=CSDN_HEADERS, retries=2)
            if r.status_code != 200 or not r.text:
                continue
            html = r.text
            m = re.search(
                r'user-profile-statistics-views[^>]*>\s*<div[^>]*user-profile-statistics-num[^>]*>\s*([\d,]+)\s*<',
                html, re.DOTALL,
            )
            if m:
                return int(re.sub(r"[^\d]", "", m.group(1)) or 0)
            m = re.search(
                r'user-profile-statistics-num[^>]*>\s*([\d,]+)\s*<[^>]*>\s*总访问量',
                html, re.DOTALL,
            )
            if m:
                return int(re.sub(r"[^\d]", "", m.group(1)) or 0)
        except Exception:
            continue
    return 0


def fetch_article_stats(article_id, username=""):
    if not article_id:
        return {"ok": False, "views": 0, "comments": 0, "collections": 0, "likes": 0}
    url = f"https://blog.csdn.net/{username}/article/details/{article_id}" if username \
        else f"https://blog.csdn.net/article/details/{article_id}"
    try:
        r = http_get(url, timeout=10, headers=CSDN_HEADERS, retries=1)
        if r.status_code != 200 or not r.text:
            return {"ok": False, "views": 0, "comments": 0, "collections": 0, "likes": 0}
        html = r.text
        stats = {"views": 0, "comments": 0, "collections": 0, "likes": 0, "ok": True}
        m = re.search(r"viewCountFormat\s*=\s*(\d+)", html)
        if m:
            stats["views"] = int(m.group(1))
        else:
            m = re.search(r"文章浏览阅读\s*([\d,]+)\s*次", html)
            if m:
                stats["views"] = int(re.sub(r"[^\d]", "", m.group(1)) or 0)
        m = re.search(r"commentscount\s*=\s*(\d+)", html)
        if m:
            stats["comments"] = int(m.group(1))
        m = re.search(r'id="get-collection"\s+[^>]*data-num="(\d+)"', html)
        if not m:
            m = re.search(r'data-num="(\d+)"\s+id="get-collection"', html)
        if m:
            stats["collections"] = int(m.group(1))
        return stats
    except Exception:
        return {"ok": False, "views": 0, "comments": 0, "collections": 0, "likes": 0}


def aggregate_user_stats(username, posts):
    article_stats = {"views": 0, "comments": 0, "collections": 0, "likes": 0}
    n_ok = 0
    for p in posts or []:
        m = re.search(r"/article/details/(\d+)", p.get("link") or "")
        if not m:
            continue
        s = fetch_article_stats(m.group(1), username=username)
        if s.get("ok"):
            n_ok += 1
        for k in article_stats:
            article_stats[k] += s.get(k, 0)
    profile_views = fetch_user_total_views(username)
    return {
        "total_views": profile_views,
        "profile_views": profile_views,
        "article_views_sum": article_stats["views"],
        "total_likes": article_stats["likes"],
        "total_comments": article_stats["comments"],
        "total_collections": article_stats["collections"],
        "articles_scanned": n_ok,
    }


# ---------- Gitee 抓取 ----------
GITEE_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def fetch_gitee_repo_api(owner, repo):
    """API 抓仓库基本信息: stars, forks, language, 默认分支"""
    url = f"https://gitee.com/api/v5/repos/{owner}/{repo}"
    try:
        r = http_get(url, timeout=10, headers=GITEE_HEADERS, retries=1)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def fetch_gitee_last_commit_api(owner, repo):
    """API 抓最后 commit"""
    url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/commits?per_page=1"
    try:
        r = http_get(url, timeout=10, headers=GITEE_HEADERS, retries=1)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, list) or not data:
            return None
        head = data[0]
        commit = head.get("commit", {}) or {}
        author = (commit.get("author") or {}).get("name", "?")
        date = commit.get("authored_date") or commit.get("committer", {}).get("date", "")
        msg = (commit.get("message") or "").splitlines()[0][:80]
        sha = head.get("sha", "")[:7]
        return {"author": author, "date": date, "msg": msg, "sha": sha}
    except Exception:
        return None


def fetch_gitee_commits_count_api(owner, repo):
    """通过 commits 列表分页估算总数 (per_page=100, 取最后一页 + 之前累加)
    Gitee API 默认返回最新 commits, 我们最多取 5 页 = 500 commits 估算
    """
    total = 0
    try:
        for page in range(1, 6):
            url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/commits?per_page=100&page={page}"
            r = http_get(url, timeout=8, headers=GITEE_HEADERS, retries=0)
            if r.status_code != 200:
                return total or None
            data = r.json()
            if not isinstance(data, list) or not data:
                break
            total += len(data)
            if len(data) < 100:
                break
        return total
    except Exception:
        return total or None



def fetch_gitee_markdown(owner, repo):
    """Gitee 在 /owner/repo.md 这个 URL 下对未登录请求返回 markdown。
    /owner/repo 主页面是 HTML (不能直接当 markdown 解析)。
    解析 markdown 拿 stars/forks/created/last_updated。
    """
    url = f"https://gitee.com/{owner}/{repo}.md"
    try:
        r = http_get(url, timeout=10, headers={
            "User-Agent": UA,
            "Accept": "text/markdown,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }, retries=1)
        if r.status_code != 200 or not r.text:
            return None
        text = r.text
        # 必须是 markdown 格式 (gitee 锁定页面)
        if "# " not in text or "**Stars**" not in text:
            return None
        info = {"source": "markdown"}
        # Stars
        m = re.search(r"\*\*Stars\*\*\s*:?\s*(\d+)", text)
        if m:
            info["stars"] = int(m.group(1))
        # Forks
        m = re.search(r"\*\*Forks\*\*\s*:?\s*(\d+)", text)
        if m:
            info["forks"] = int(m.group(1))
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
        if r.status_code != 200 or not r.text:
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
    """统一入口: API 优先, HTML fallback
    返回: {ok, url, stars, forks, commit_count, sha, date, msg, source, err}
    """
    if not owner or not repo:
        return {"ok": False, "err": "bad gitee url"}
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
    }
    # 1) API repo info
    api_repo = fetch_gitee_repo_api(owner, repo)
    if api_repo:
        out["stars"] = int(api_repo.get("stargazers_count", 0) or 0)
        out["forks"] = int(api_repo.get("forks_count", 0) or 0)
        out["source"] = "api"
    # 2) API last commit
    api_commit = fetch_gitee_last_commit_api(owner, repo)
    if api_commit:
        out.update(api_commit)
        out["source"] = "api"
    # 3) commits count (仅 API)
    if api_repo:
        cnt = fetch_gitee_commits_count_api(owner, repo)
        if cnt is not None:
            out["commit_count"] = cnt
    # 4) Markdown fallback (gitee 当前未登录访问返回 markdown)
    if not out.get("sha") and not out.get("stars") and not out.get("commit_count"):
        md = fetch_gitee_markdown(owner, repo)
        if md:
            out["stars"] = md.get("stars", out["stars"])
            out["forks"] = md.get("forks", out["forks"])
            if md.get("date") and not out.get("date"):
                out["date"] = md["date"]
            out["source"] = out["source"] or "markdown"
    # 5) HTML fallback (legacy)
    if not out.get("sha") and not out.get("stars") and not out.get("commit_count"):
        html = fetch_gitee_html_fallback(owner, repo)
        if html:
            out["stars"] = html.get("stars", out["stars"])
            out["forks"] = html.get("forks", out["forks"])
            out["commit_count"] = html.get("commit_count", out["commit_count"])
            if html.get("date") and not out.get("date"):
                out["date"] = html["date"]
            out["source"] = out["source"] or "html"
    # 判定 ok — markdown fallback 也可以成功
    out["ok"] = bool(out.get("sha") or out.get("date") or out.get("commit_count") > 0 or out.get("stars") > 0 or out.get("forks") > 0 or out["source"])
    if not out["ok"]:
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
                "fetched_at": g.get("fetched_at", ""),
            },
        })

    total_posts = sum(x["post_count"] for x in out_students)
    data = {
        "last_update": NOW_ISO,
        "total_students": len(out_students),
        "total_posts": total_posts,
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
            lines.append(f"- {s['sid']} {s['name']} → 仅 Gitee: {s['gitee']}")
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

    # 抓取健康度
    csdn_ms = fetch_meta.get("stages", {}).get("csdn", {}).get("duration_ms", 0)
    gitee_ms = fetch_meta.get("stages", {}).get("gitee", {}).get("duration_ms", 0)
    lines.append(f"🩺 **抓取健康度**: CSDN {csdn_ms}ms / Gitee {gitee_ms}ms / "
                f"成功 {fetch_meta.get('success_count', 0)}/{fetch_meta.get('total_requests', 0)}")
    lines.append("")

    lines.append("---")
    lines.append("**明细**")
    lines.append("")
    for s in students_state:
        name = f"{s['name']}({s['sid']})"
        csdn_st = "✅" if s.get("csdn_status") == "ok" else ("⚠️" if s.get("csdn_status") == "no_csdn" else "❌")
        g = s.get("gitee") or {}
        gitee_st = "✅" if g.get("ok") else ("⚠️" if g.get("err") and "rate" in (g.get("err") or "").lower() else "❌")
        lines.append(f"{csdn_st}{gitee_st} **{name}**")
        # CSDN
        if s.get("csdn_status") == "no_csdn":
            lines.append(f"   - CSDN: 未提供")
        elif s.get("csdn_status") == "ok":
            new_n = len(s.get("new_posts", []))
            lines.append(
                f"   - CSDN: @{s.get('csdn_username')} · "
                f"文章 {len(s['posts'])} · 阅读 {s.get('stats',{}).get('total_views', 0):,}"
                + (f" · **新 {new_n}**" if new_n else "")
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
    failed_csdn = [s for s in students_state if s.get("csdn_status") not in ("ok", "no_csdn")]
    failed_gitee = [s for s in students_state if not (s.get("gitee") or {}).get("ok")]
    if missing_csdn:
        names = "、".join(s["name"] for s in missing_csdn[:5])
        lines.append(f"⚠️ 缺 CSDN: {names}{' 等 ' + str(len(missing_csdn)) if len(missing_csdn) > 5 else ''}")
    if failed_gitee:
        n = len(failed_gitee)
        lines.append(f"🔥 Gitee 抓取失败: {n} 人(可能限流)")
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
        print(f"[DRY-RUN] feishu skipped, len={len(text)}, attachment={attachment}")
        return True
    openclaw_cmd = shutil.which("openclaw") or shutil.which("openclaw.cmd")
    if not openclaw_cmd:
        for p in [r"C:\Users\Administrator\AppData\Roaming\npm\openclaw.cmd",
                  r"C:\Users\Administrator\AppData\Roaming\npm\openclaw"]:
            if os.path.exists(p):
                openclaw_cmd = p
                break
    if not openclaw_cmd:
        print("[feishu] ERROR: openclaw not found")
        return False
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
            print(f"[feishu] failed: rc={result.returncode} out={out[:300]}")
            return False
        print(f"[feishu] sent ok, {len(text)} chars; out={out[:160]}")
        # extract message_id from output if present
        m = re.search(r'(?:message_id|msg_id|"id")\s*[:=]\s*"?([A-Za-z0-9_-]+)"?', out)
        if m:
            print(f"[feishu] message_id: {m.group(1)}")
        return True
    except Exception as e:
        print(f"[feishu] exception: {e}")
        return False


# ---------- 主流程 ----------
def fetch_csdn_for_student(st):
    rec = {
        "sid": st["sid"], "name": st["name"],
        "csdn": st["csdn"], "gitee": st["gitee"], "note": st.get("note", ""),
        "csdn_username": "",
        "csdn_status": "no_csdn" if not st["csdn"] else "pending",
        "posts": [], "new_posts": [], "stats": {}, "csdn_err": "",
        "gitee_status": "pending",
        "gitee": {},  # 用 watchlist 的 gitee URL
    }
    # Gitee URL 保留在 rec['gitee_url'] 用作抓取
    rec["gitee_url"] = st["gitee"]
    rec["gitee"] = {}  # 占位,抓完填
    if st["csdn"]:
        user = extract_csdn_username(st["csdn"])
        rec["csdn_username"] = user
        if user:
            posts = fetch_csdn_html(user, limit=5)
            if not posts:
                posts = fetch_csdn_rss(user, limit=5)
            if posts:
                rec["posts"] = posts
                rec["csdn_status"] = "ok"
            else:
                rec["csdn_status"] = "failed"
                rec["csdn_err"] = "HTML+RSS empty"
    return rec


def fetch_stats_for(rec):
    if rec["csdn_status"] == "ok" and rec.get("csdn_username"):
        rec["stats"] = aggregate_user_stats(rec["csdn_username"], rec["posts"])
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


def run(args):
    wl = find_watchlist()
    if not wl:
        print(f"[FATAL] watchlist not found in any of: {[str(p) for p in WATCHLIST_PATHS]}", file=sys.stderr)
        sys.exit(1)
    print(f"[watchlist] {wl}")
    students = parse_watchlist(wl)
    print(f"[watchlist] parsed {len(students)} students")
    prev = load_snapshot()
    prev_students = prev.get("students", {})

    run_csdn = not args.gitee_only
    run_gitee = not args.csdn_only

    t_start = time.time()

    # --- Stage 1: CSDN ---
    csdn_t = time.time()
    students_state = []
    if run_csdn:
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(fetch_csdn_for_student, st): st for st in students}
            for fut in as_completed(futs):
                students_state.append(fut.result())
        # stats
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(fetch_stats_for, rec): rec for rec in students_state}
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
                rec["stats"] = {
                    "total_views": p.get("total_views", 0),
                    "total_likes": p.get("total_likes", 0),
                    "total_comments": p.get("total_comments", 0),
                    "total_collections": p.get("total_collections", 0),
                    "profile_views": p.get("total_views", 0),
                    "articles_scanned": p.get("articles_scanned", 0),
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

    # --- fetch_meta ---
    csdn_ok = sum(1 for s in students_state if s.get("csdn_status") == "ok")
    csdn_fail = sum(1 for s in students_state if s.get("csdn_status") == "failed")
    gitee_ok = sum(1 for s in students_state if (s.get("gitee") or {}).get("ok"))
    gitee_fail = sum(1 for s in students_state if not (s.get("gitee") or {}).get("ok"))
    fetch_meta = {
        "fetched_at": NOW_ISO,
        "duration_ms": total_ms,
        "method_breakdown": {
            "csdn_html": sum(1 for s in students_state if s.get("csdn_status") == "ok"),
            "csdn_rss": 0,
            "gitee_api": sum(1 for s in students_state if (s.get("gitee") or {}).get("source") == "api"),
            "gitee_html": sum(1 for s in students_state if (s.get("gitee") or {}).get("source") == "html"),
        },
        "total_requests": len(students_state) * 2,
        "success_count": csdn_ok + gitee_ok,
        "fail_count": csdn_fail + gitee_fail,
        "stages": {
            "csdn": {"duration_ms": csdn_ms, "ok": csdn_ok, "fail": csdn_fail},
            "gitee": {"duration_ms": gitee_ms, "ok": gitee_ok, "fail": gitee_fail},
        },
    }

    # --- 班级统计 ---

    # Ma: 数据保护 — 如果这次抓取明显失败（如 CSDN 全失败）,
    # 保留之前 data.json 里的 posts/stats/csdn_username 而不是覆盖为空。
    # 否则限流/网络故障一次,所有 129 篇博客就全没了。
    prev_data = load_existing_data()
    prev_by_sid = {s["sid"]: s for s in prev_data.get("students", [])}
    csdn_fail_count = sum(1 for s in students_state if s.get("csdn_status") == "failed")
    csdn_ok_count = sum(1 for s in students_state if s.get("csdn_status") == "ok")
    # 触发条件: CSDN 失败率 > 30% 且成功数 < 之前的 70%
    prev_total_posts = prev_data.get("total_posts", 0)
    new_total_posts_est = sum(len(s.get("posts", [])) for s in students_state)
    should_preserve = False
    if prev_total_posts > 0:
        if csdn_fail_count > 0.3 * len(students_state) and new_total_posts_est < 0.7 * prev_total_posts:
            should_preserve = True
            print(f"[protect] CSDN fail rate {csdn_fail_count}/{len(students_state)} > 30% AND "
                  f"new posts {new_total_posts_est} < 70% of prev {prev_total_posts}: "
                  f"preserving previous posts/stats per student")
    if should_preserve:
        for rec in students_state:
            p = prev_by_sid.get(rec["sid"])
            if p and (not rec.get("posts") or rec.get("csdn_status") == "failed"):
                rec["posts"] = p.get("posts", [])
                rec["csdn_username"] = p.get("csdn_username", rec.get("csdn_username", ""))
                rec["latest_post"] = p.get("latest_post", {})
                if not rec.get("stats") or rec["stats"].get("total_views", 0) == 0:
                    rec["stats"] = {
                        "total_views": p.get("total_views", 0),
                        "total_likes": p.get("total_likes", 0),
                        "total_comments": p.get("total_comments", 0),
                        "total_collections": p.get("total_collections", 0),
                        "profile_views": p.get("total_views", 0),
                        "articles_scanned": p.get("articles_scanned", 0),
                    }

    class_stats = compute_class_stats(students_state)

    # --- 写 data.json ---
    data = write_data_json(students_state, fetch_meta, class_stats)
    print(f"[data.json] written: {DATA_JSON} ({len(data['students'])} students, "
          f"{data['total_posts']} posts)")

    # --- history snapshot ---
    write_history_snapshot(data)
    print(f"[history] appended to {HISTORY_DIR / (TODAY + '.json')}")

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
    print(f"[snapshot] saved -> {SNAPSHOT}")

    # --- 报告 ---
    report = build_report(students_state, missing_csdn, fetch_meta, class_stats)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rep_path = REPORT_DIR / f"csdn-report-{TODAY}.md"
    rep_path.write_text(report, encoding="utf-8")
    print(f"[report] saved -> {rep_path}")

    new_posts_total = sum(len(s.get("new_posts", [])) for s in students_state)
    summary = build_summary(students_state, new_posts_total, class_stats)

    # --- 推飞书 (除非 dry) ---
    if not args.dry_run:
        send_feishu(summary, dry=False, attachment=rep_path)
    else:
        print("[dry-run] feishu skipped")
        print("---SUMMARY---")
        print(summary)
        print("---REPORT (head)---")
        print("\n".join(report.splitlines()[:50]))

    # --- token 用量回报 ---
    print(f"[timing] total {total_ms}ms = CSDN {csdn_ms}ms + Gitee {gitee_ms}ms")
    print(f"[stats] csdn_ok={csdn_ok}/{len(students_state)}, "
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
    args = ap.parse_args()

    # 时间窗
    if not args.force_now and not in_time_window():
        print(f"[SKIP] 当前时间 {datetime.now().strftime('%H:%M')} 不在 "
              f"{WINDOW_START:02d}:00-{WINDOW_END:02d}:00 窗口内. "
              f"用 --force-now 跳过.")
        return 0

    # token 预算
    if not args.skip_token_check and not token_budget_ok():
        print("[SKIP] token 剩余不足 75%, 跳过.")
        return 0

    return run(args)


if __name__ == "__main__":
    sys.exit(main())