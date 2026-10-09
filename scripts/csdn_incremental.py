"""
csdn_incremental.py
Ma: 真正的增量抓取 — 老文章只更新 views/likes, 新文章全抓

策略:
  1. 翻 CSDN 列表 (1-20 页), 拿到所有可见文章
  2. 跟 data.json 里的对比:
     - 已存在 (老文章): 只更新 views/likes/comments/collections
     - 不存在 (新文章): 全字段抓取 + 加进去
  3. 老文章不重抓 title/link/pubDate (已经存了)

用法:
  python csdn_incremental.py --all           # 所有学生
  python csdn_incremental.py --student name  # 单个学生
"""
import json
import re
import sys
import argparse
import urllib.request
import urllib.error
import time
import ssl
from pathlib import Path
from datetime import datetime, timedelta

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

        stats = {"views": 0, "likes": 0, "comments": 0, "collections": 0}

        # 1. 阅读量
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

        # 备选
        if stats["views"] == 0:
            m = re.search(r'"view_count"\s*:\s*(\d+)', html)
            if m:
                stats["views"] = int(m.group(1))

        return stats
    except Exception as e:
        log(f"  ⚠️ 抓 {article_id} 失败: {str(e)[:50]}")
        return None


def fetch_blog_list(username, max_pages=20):
    """Ma: 翻 CSDN 列表, 找到所有可见文章 (增量模式用)

    跟 fetch_new_posts 不同:
    - fetch_new_posts: 只翻 1-2 页,找最近 N 天
    - fetch_blog_list: 翻 1-20 页, 找到所有老文章为止 (增量抓)
    """
    all_articles = []
    for page in range(1, max_pages + 1):
        url = f"https://blog.csdn.net/{username}?type=blog&page={page}"
        try:
            r = http_get(url, timeout=15)
            html = r.read().decode("utf-8", errors="ignore")

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
                link = m.group(1)
                article_id = m.group(2)

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

        except Exception as e:
            log(f"  ⚠️ 抓列表 page {page} 失败: {str(e)[:50]}")
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
        except:
            pass

    now = datetime.now()
    m = re.search(r"(\d+)\s*小时前", raw)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    m = re.search(r"(\d+)\s*分钟前", raw)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    m = re.search(r"(\d+)\s*天前", raw)
    if m:
        return now - timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s*周前", raw)
    if m:
        return now - timedelta(weeks=int(m.group(1)))
    m = re.search(r"(\d+)\s*月前", raw)
    if m:
        return now - timedelta(days=30 * int(m.group(1)))

    return None


def incremental_update_student(student, max_pages=20):
    """Ma 真正想要的增量抓取:

    1. 翻 CSDN 列表 (1-20 页), 拿到所有可见文章
    2. 跟 data.json 对比:
       - 老文章: 只更新 views/likes
       - 新文章: 全字段抓取 + views
    3. 老文章不重抓 title/link/pubDate
    """
    username = student.get("csdn_username")
    if not username:
        return {"new": 0, "updated_stats": 0, "missing": 0, "skipped": "no_username"}

    # 1. 抓 CSDN 列表 (翻多页, 找到所有可见文章)
    articles = fetch_blog_list(username, max_pages=max_pages)
    if not articles:
        return {"new": 0, "updated_stats": 0, "missing": 0, "skipped": "fetch_failed"}

    csdn_by_id = {a["article_id"]: a for a in articles}
    csdn_ids = set(csdn_by_id.keys())

    # 2. 当前 data.json 里的 ID
    existing_posts = student.get("posts", [])
    existing_ids = set()
    for p in existing_posts:
        m = re.search(r"/article/details/(\d+)", p.get("link", ""))
        if m:
            existing_ids.add(m.group(1))

    new_count = 0
    updated_stats_count = 0
    missing_count = 0

    # 3. 找出新文章 (CSDN 有, data.json 没有)
    new_posts_list = []
    for article_id, cdn_article in csdn_by_id.items():
        if article_id in existing_ids:
            # 老文章: 标记需要更新统计
            for p in existing_posts:
                if f"/article/details/{article_id}" in p.get("link", ""):
                    p["_needs_stats_update"] = True
                    break
        else:
            # 新文章: 全字段抓取
            pub_date = parse_relative_date(cdn_article["pub_raw"])
            pub_iso = pub_date.isoformat() if pub_date else ""

            new_posts_list.append({
                "title": cdn_article["title"] or f"文章 #{article_id}",
                "link": cdn_article["link"],
                "pubDate": pub_iso,
                "guid": cdn_article["link"],
                "views": 0,
                "likes": 0,
                "comments": 0,
                "collections": 0,
                "is_new": True,
                "_needs_stats_update": True,
            })
            new_count += 1

    # 4. 标记 CSDN 上没有的 (data.json 里有但 CSDN 删了)
    for p in existing_posts:
        m = re.search(r"/article/details/(\d+)", p.get("link", ""))
        if m and m.group(1) not in csdn_ids:
            p["_missing_on_csdn"] = True
            missing_count += 1

    # 5. 添加新文章
    if new_posts_list:
        existing_posts = new_posts_list + existing_posts
        log(f"  🆕 {new_count} 篇新文章")

    # 6. 抓所有需要更新统计的文章 (老 + 新)
    posts_to_update = [p for p in existing_posts if p.get("_needs_stats_update")]
    log(f"  🔄 更新 {len(posts_to_update)} 篇文章的统计...")

    for i, p in enumerate(posts_to_update, 1):
        m = re.search(r"/article/details/(\d+)", p.get("link", ""))
        if not m:
            continue
        article_id = m.group(1)

        stats = fetch_article_stats(article_id, username)
        if stats:
            old_views = p.get("views", 0)
            p["views"] = stats["views"]
            p["likes"] = stats["likes"]
            p["comments"] = stats["comments"]
            p["collections"] = stats["collections"]
            p["stats_updated_at"] = datetime.now().isoformat()
            if old_views != stats["views"]:
                updated_stats_count += 1
            log(f"    [{i}/{len(posts_to_update)}] {article_id}: views {old_views}→{stats['views']}")

        p.pop("_needs_stats_update", None)
        time.sleep(0.3)

    # 7. 清理
    for p in existing_posts:
        p.pop("_missing_on_csdn", None)

    # 8. 排序 (按 pubDate 倒序)
    existing_posts.sort(
        key=lambda x: x.get("pubDate", "") or "",
        reverse=True,
    )

    student["posts"] = existing_posts
    student["post_count"] = len(existing_posts)

    return {
        "new": new_count,
        "updated_stats": updated_stats_count,
        "missing": missing_count,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="所有学生")
    parser.add_argument("--student", help="单个学生名")
    parser.add_argument("--max-pages", type=int, default=20, help="最大翻页数")
    args = parser.parse_args()

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
    success_count = 0

    for i, s in enumerate(targets, 1):
        name = s.get("name", "?")
        log(f"[{i}/{len(targets)}] {name}...")

        result = incremental_update_student(
            s,
            max_pages=args.max_pages,
        )

        if result.get("skipped"):
            log(f"  ⏭️  跳过 ({result['skipped']})")
            continue

        new = result.get("new", 0)
        upd = result.get("updated_stats", 0)
        mis = result.get("missing", 0)
        total_new += new
        total_updated += upd
        if new > 0 or upd > 0:
            success_count += 1
        log(f"  +{new} 新 | ↻{upd} 更新统计 | 缺{mis}")

    d["last_update"] = datetime.now().isoformat(timespec="seconds")
    d["total_posts"] = sum(len(s.get("posts", [])) for s in d.get("students", []))

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    log(f"")
    log(f"=== 抓取完成 ===")
    log(f"✅ {success_count} 个学生有数据")
    log(f"🆕 {total_new} 篇新文章")
    log(f"🔄 {total_updated} 篇文章统计更新")
    log(f"📊 总文章: {d['total_posts']}")
    log(f"💾 data.json 已保存")


if __name__ == "__main__":
    main()
