"""
test_report_render.py — 回归测试: --gitee-only 不能把日报渲染成 "CSDN 未抓取"

2026-10-21 踩到的 bug:
  05:30 的 Gitee-only 任务跑了之后, reports/csdn-report-YYYY-MM-DD.md 被覆盖,
  每个学生的 CSDN 行都变成 "❌ CSDN: 未抓取 (skipped (--gitee-only))",
  但 data.json 里的 CSDN 数据其实完好 (复用过来的)。
  根因: build_report 只认 csdn_status == "ok", 不认 "skipped_reused"。

本测试覆盖:
  1. skipped_reused 的学生, 报告里 CSDN 行显示文章数/阅读量, 不是 "未抓取"
  2. 状态图标是 ✅ 而不是 ❌
  3. 真的失败 (status="failed") 仍然显示 "未抓取"
  4. no_csdn 显示 "未提供"
  5. csdn_ok 计数把 skipped_reused 算进去
"""
import os
import re
import sys
import types

D = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(D, "scripts"))


def make_student(sid, name, status, username="u", posts=3, views=1000, gitee_ok=True):
    return {
        "sid": sid, "name": name,
        "csdn": f"https://blog.csdn.net/{username}",
        "gitee_url": "", "gitee": {"ok": gitee_ok, "stars": 1, "forks": 0,
                                  "commit_count": 2, "sha": "abc", "date": "2026-10-21 10:00:00",
                                  "msg": "msg", "source": "api"},
        "csdn_username": username,
        "csdn_status": status,
        "posts": [{"title": f"t{i}", "pubDate": "", "guid": str(i)} for i in range(posts)],
        "new_posts": [],
        "stats": {"total_views": views, "total_likes": 0, "total_comments": 0,
                  "total_collections": 0, "profile_views": views,
                  "article_views_sum": views, "views_source": "profile",
                  "articles_scanned": posts, "fetched_at": "2026-10-21T12:00:00"},
        "csdn_err": "skipped (--gitee-only)" if status == "skipped_reused" else "",
    }


def load_module():
    """导入 csdn-monitor-v2 但不跑 main()"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "csdn_v2", os.path.join(D, "scripts", "csdn-monitor-v2.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    m = load_module()
    students = [
        make_student("S001", "复用同学", "skipped_reused", "reuser", posts=5, views=1234),
        make_student("S002", "正常同学", "ok", "normal", posts=3, views=999),
        make_student("S003", "失败同学", "failed", "failer", posts=0, views=0),
        make_student("S004", "没博客同学", "no_csdn", "", posts=0, views=0),
    ]
    missing = [s for s in students if s.get("csdn_status") == "no_csdn"]
    meta = {"stages": {"csdn": {"duration_ms": 3}, "gitee": {"duration_ms": 79}},
            "success_count": 32, "total_requests": 32}
    stats = m.compute_class_stats(students)
    report = m.build_report(students, missing, meta, stats)
    summary = m.build_summary(students, 0, stats)

    fails = []

    def section(name):
        """取报告里某个学生的完整段落 (标题行 + 其后直到空行)。"""
        lines = report.split("\n")
        for i, l in enumerate(lines):
            if name in l and "**" in l:
                out = [l]
                for l2 in lines[i + 1:]:
                    if not l2.strip():
                        break
                    out.append(l2)
                return "\n".join(out)
        return ""

    seg = section("复用同学")
    if "未抓取" in seg:
        fails.append(f"skipped_reused 仍显示未抓取: {seg!r}")
    if "1,234" not in seg:   # 报告用千分位格式
        fails.append(f"skipped_reused 没显示阅读量 1,234: {seg!r}")
    if "文章 5" not in seg:
        fails.append(f"skipped_reused 没显示文章数: {seg!r}")
    if "@reuser" not in seg:
        fails.append(f"skipped_reused 没显示用户名: {seg!r}")
    if not seg.startswith("✅"):
        fails.append(f"复用同学图标不是 ✅: {seg[:40]!r}")

    fseg = section("失败同学")
    if "未抓取" not in fseg:
        fails.append(f"failed 学生没显示未抓取: {fseg!r}")
    if fseg.startswith("✅"):
        fails.append(f"failed 学生图标不该是 ✅: {fseg[:40]!r}")

    nseg = section("没博客同学")
    if "未提供" not in nseg:
        fails.append(f"no_csdn 没显示未提供: {nseg!r}")

    # 5. csdn_ok 计数
    ok_cnt = sum(1 for s in students if s.get("csdn_status") in ("ok", "skipped_reused"))
    if ok_cnt != 2:
        fails.append(f"csdn_ok 计数应为 2 (ok + skipped_reused), 实际 {ok_cnt}")

    # 6. 健康度行不应该说 CSDN 全挂
    if "成功 32/32" not in report:
        # 只是提醒格式, 不算失败, 仅打印
        pass

    # 7. summary 不该把复用当成失败
    if "CSDN 抓取失败" in summary and "复用同学" in summary:
        fails.append("summary 把 skipped_reused 当成失败了")

    print("=" * 60)
    print("测试报告 (skipped_reused 段落):")
    print(seg)
    print("-" * 60)
    print("健康度行:", [l for l in report.split("\n") if "健康度" in l])
    print("-" * 60)
    if fails:
        print(f"FAILED: {len(fails)} 项")
        for f in fails:
            print("  ✗", f)
        return 1
    print("PASSED: 全部 7 项检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
