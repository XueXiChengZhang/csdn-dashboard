"""
csdn_incremental.py
Ma: 增量抓取 — 老文章只更新 views/likes, 新文章全抓

用法:
  python csdn_incremental.py --all          # 所有学生增量抓
  python csdn_incremental.py --student name # 单个学生
  python csdn_incremental.py --since 7days  # 只抓最近 7 天新文章
"""
import json
import re
import sys
import argparse
import urllib.request
import urllib.error
import time
from pathlib import Path
from datetime import datetime, timedelta

# 路径
SCRIPT_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = SCRIPT_DIR.parent
DATA_PATH = DASHBOARD_DIR / "data.json"
LOG_PATH = DASHBOARD_DIR / "logs" / f"incremental-{datetime.now().strftime('%Y-%m-%d')}.log"

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def http_get(url, timeout=15, retries=2):
    """带重试的 HTTP GET"""
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(2)
    raise last_err


def fetch_article_stats(article_id, username):
    """抓单篇文章的 views/likes/comments/collections

    Ma: 这是单次 HTTP 请求,比抓全列表便宜
    """
    url = f"https://blog.csdn.net/{username}/article/details/{article_id}"
    try:
        r = http_get(url, timeout=15)
        html = r.read().decode("utf-8", errors="ignore")

        # CSDN 文章页的统计
        stats = {"views": 0, "likes": 0, "comments": 0, "collections": 0}

        # 1. 阅读量: data-v-xxx 或 class="read-count"
        m = re.search(r'<span[^>]*class="read-count"[^>]*>\s*(\d+)', html)
        if m:
            stats["views"] = int(m.group(1))

        # 2. 点赞数
        m = re.search(r'<span[^>]*class="like-count"[^>]*>\s*(\d+)', html)
        if m:
            stats["likes"] = int(m.group(1))

        # 3. 评论数
        m = re.search(r'<span[^>]*class="comment-count"[^>]*>\s*(\d+)', html)
        if m:
            stats["comments"] = int(m.group(1))

        # 4. 收藏数
        m = re.search(r'<span[^>]*class="collect-count"[^>]*>\s*(\d+)', html)
        if m:
            stats["collections"] = int(m.group(1))

        # 备选: 从 script 里找
        if stats["views"] == 0:
            m = re.search(r'"view_count"\s*:\s*(\d+)', html)
            if m:
                stats["views"] = int(m.group(1))

        return stats
    except Exception as e:
        log(f"  ⚠️ 抓 {article_id} 失败: {str(e)[:50]}")
        return None


def fetch_new_posts(username, since_days=7):
    """抓取最近 N 天的文章列表 (轻量)

    只翻 1-2 页,找新文章
    """
    new_posts = []
    for page in [1, 2]:
        url = f"https://blog.csdn.net/{username}?type=blog&page={page}"
        try:
            r = http_get(url, timeout=15)
            html = r.read().decode("utf-8", errors="ignore")

            # 找文章块
            blocks = re.findall(
                r'<article[^>]*class="blog-list-box"[^>]*>(.*?)</article>',
                html, re.DOTALL,
            )

            if not blocks:
                break

            for block in blocks:
                # 提取 link
                m = re.search(r'href="(https?://blog\.csdn\.net/[^/]+/article/details/(\d+))"', block)
                if not m:
                    continue
                link = m.group(1)
                article_id = m.group(2)

                # 提取 title
                m_t = re.search(r'<h[34][^>]*>(.*?)</h[34]>', block, re.DOTALL)
                title = re.sub(r"<[^>]+>", "", m_t.group(1)).strip() if m_t else ""

                # 提取发布时间
                m_d = re.search(
                    r'<div class="view-time-box"[^>]*>\s*博文更新于\s*([^<·]+?)\s*(?:·|</div>)',
                    block,
                )
                pub_raw = m_d.group(1).strip() if m_d else ""

                new_posts.append({
                    "article_id": article_id,
                    "link": link,
                    "title": title,
                    "pub_raw": pub_raw,
                })

        except Exception as e:
            log(f"  ⚠️ 抓列表 page {page} 失败: {str(e)[:50]}")
            break

    return new_posts


def parse_relative_date(raw):
    """解析 CSDN 的 '3 天前' / '2026.09.20' 格式"""
    if not raw:
        return None
    raw = raw.strip()

    # 1. 绝对日期
    m = re.search(r"(\d{4})[.-](\d{1,2})[.-](\d{1,2})", raw)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except:
            pass

    # 2. 相对时间
    now = datetime.now()
    m = re.search(r"(\d+)\s*小时前", raw)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    m = re.search(r"(\d+)\s*天前", raw)
    if m:
        return now - timedelta(days=int(m.group(1)))

    return None


def incremental_update_student(student, since_days=7, update_stats=True):
    """增量更新单个学生

    Args:
        student: data.json 里的学生 dict
        since_days: 只找 N 天内新文章
        update_stats: 是否更新已有文章的 views/likes

    Returns:
        dict: 统计信息
    """
    username = student.get("csdn_username")
    if not username:
        return {"new": 0, "updated": 0, "skipped": "no_username"}

    # 1. 拿到现有文章的 ID 集合
    existing_ids = set()
    for p in student.get("posts", []):
        m = re.search(r"/article/details/(\d+)", p.get("link", ""))
        if m:
            existing_ids.add(m.group(1))

    # 2. 抓新文章列表
    candidates = fetch_new_posts(username, since_days=since_days)

    # 3. 找新的 (不在 existing_ids 里)
    new_count = 0
    for c in candidates:
        if c["article_id"] in existing_ids:
            continue
        # 添加到 posts 列表
        pub_date = parse_relative_date(c["pub_raw"])
        pub_iso = pub_date.isoformat() if pub_date else ""

        student.setdefault("posts", []).insert(0, {
            "title": c["title"],
            "link": c["link"],
            "pubDate": pub_iso,
            "guid": c["link"],
            "views": 0,
            "likes": 0,
            "comments": 0,
            "collections": 0,
            "is_new": True,
        })
        existing_ids.add(c["article_id"])
        new_count += 1

    # 4. 更新已有文章的 views/likes (只取前 10 篇节省请求)
    updated_count = 0
    if update_stats:
        for p in student.get("posts", [])[:10]:  # 只更新最新 10 篇
            m = re.search(r"/article/details/(\d+)", p.get("link", ""))
            if not m:
                continue
            article_id = m.group(1)

            # 跳过 30 天以上没新数据的 (避免浪费)
            try:
                pub = datetime.fromisoformat(p.get("pubDate", "").replace("Z", ""))
                age_days = (datetime.now() - pub).days
                if age_days > 90:  # 90 天前的文章不再追
                    continue
            except:
                pass

            stats = fetch_article_stats(article_id, username)
            if stats:
                old_views = p.get("views", 0)
                p["views"] = stats["views"]
                p["likes"] = stats["likes"]
                p["comments"] = stats["comments"]
                p["collections"] = stats["collections"]
                p["stats_updated_at"] = datetime.now().isoformat()
                if old_views != stats["views"]:
                    updated_count += 1
            time.sleep(0.5)  # 礼貌

    # 5. 排序 (按发布时间倒序)
    student["posts"].sort(
        key=lambda x: x.get("pubDate", "") or "",
        reverse=True,
    )

    # 6. 更新 post_count
    student["post_count"] = len(student.get("posts", []))

    return {"new": new_count, "updated": updated_count}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="所有学生")
    parser.add_argument("--student", help="单个学生名")
    parser.add_argument("--since", type=int, default=7, help="新文章窗口(天)")
    parser.add_argument("--no-stats", action="store_true", help="不更新 views/likes")
    args = parser.parse_args()

    # 加载 data.json
    with open(DATA_PATH, encoding="utf-8") as f:
        d = json.load(f)

    log(f"=== 增量抓取启动 ===")
    log(f"data.json 加载: {len(d.get('students', []))} 个学生")

    if args.student:
        targets = [s for s in d.get("students", []) if s.get("name") == args.student]
        if not targets:
            log(f"❌ 找不到学生: {args.student}")
            return
    else:
        targets = d.get("students", [])

    total_new = 0
    total_updated = 0
    success = 0

    for i, s in enumerate(targets, 1):
        name = s.get("name", "?")
        log(f"[{i}/{len(targets)}] {name}...")

        result = incremental_update_student(
            s,
            since_days=args.since,
            update_stats=not args.no_stats,
        )

        total_new += result.get("new", 0)
        total_updated += result.get("updated", 0)
        if result.get("new", 0) > 0 or result.get("updated", 0) > 0:
            success += 1
        log(f"  +{result.get('new', 0)} 新文章, ↻{result.get('updated', 0)} 更新统计")

    # 更新 last_update
    d["last_update"] = datetime.now().isoformat(timespec="seconds")

    # 重新计算 total_posts
    d["total_posts"] = sum(len(s.get("posts", [])) for s in d.get("students", []))

    # 保存
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    log(f"")
    log(f"=== 抓取完成 ===")
    log(f"✅ {success} 个学生有数据")
    log(f"🆕 {total_new} 篇新文章")
    log(f"🔄 {total_updated} 篇文章统计更新")
    log(f"📊 总文章: {d['total_posts']}")
    log(f"💾 data.json 已保存")


if __name__ == "__main__":
    main()
