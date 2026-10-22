#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weekly_guard.py — 周日全量任务的**安全网** (2026-10-22, R7)

═══════════════════════════════════════════════════════════════════════════
为什么要这个文件
═══════════════════════════════════════════════════════════════════════════
计划任务 \\csdn-dashboard-monitor-weekly (每周日 03:00) 跑的是:

    csdn-monitor-v2.py --force-now --no-reuse     # 385 篇全部重抓
    git add -A && git commit && git push           # 直接推 GitHub Pages

`--no-reuse` 关掉了 24 小时复用保护 —— 而这正是 2026-10-21 造成
"阅读量归零" (7604 → 1297) 的那个动作。它用的是 `git add -A`,
所以**任何残缺结果都会被直接 commit + push 出去**。

这个故障不是"周日任务特有风险", 而是同一条写入路径的长期系统性故障 ——
从 history-real/ 的历史快照里能逐日对比出 7 次:

    2026-09-22  00:00 增量 20,130 → 05:30 gitee-only  6,729   (-67%)
    2026-09-30  00:00 增量 21,946 → 05:30 gitee-only  7,157   (-67%)
    2026-10-01  00:00 增量 31,151 → 05:30 gitee-only 19,035   (-39%)
    2026-10-02  00:00 增量 27,438 → 05:30 gitee-only 18,457   (-33%)
    2026-10-12  00:00 增量 13,967 → 05:30 gitee-only  0       (-100%)
    2026-10-19  00:00 增量  1,352 → 05:30 gitee-only  0       (-100%)
    2026-10-21  00:00 增量  7,604 → 05:30 gitee-only  1,297   (-83%)

最典型的是 10-16: 00:00 增量算出 50,686, 05:30 覆盖成 9,609, 仪表盘少了 81%。

所以本文件提供三件事 (都在 push **之前**做):

    1. backup    跑之前把 data.json / snapshot.json / 当日归档存一份 (带时间戳, 留最近 N 份)
    2. check     跑完校验, 不合格 → 返回非 0
    3. rollback  把备份原样恢复回去

校验规则 (比 apply_data_protection 的 C 臂更严 —— C 臂只挡"总阅读 < 30%",
"只掉 40%" 它挡不住, 残缺数据照样会写进 data.json):

    R1 绝对下限: 总阅读 < 上次 60%        → 回滚
    R2 学生级:   失败学生占比 > 20%        → 回滚
    R3 文章数:   总文章 < 上次 70%          → 回滚

★ 本文件**不联网**, 不碰 CSDN / Gitee, 只读写本地文件。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# ---------- 阈值 ----------
# 总阅读下限: 新值 < 上次 * 这个比例 → 回滚
VIEW_FLOOR_RATIO = 0.6
# 失败学生占比上限: 超过 → 回滚
STUDENT_FAIL_RATIO = 0.2
# 单个学生算"失败"的阈值: 新阅读 < 上次 * 这个比例 (含归零)
STUDENT_DROP_RATIO = 0.5
# 总文章数下限: 新值 < 上次 * 这个比例 → 回滚
POST_FLOOR_RATIO = 0.7
# 上次总阅读小于这个数就不做下限判断 (小数字噪声大, 容易误杀)
MIN_VIEWS_FOR_FLOOR = 1000
# 保留最近几份备份
KEEP_BACKUPS = 8

BACKUP_ROOT = Path("cache") / "backup"
LATEST_FILE = BACKUP_ROOT / "LATEST.txt"


# ---------- 小工具 ----------
def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _dump_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def class_reading(data):
    """班级总阅读量 = 各学生 total_views 之和 (data.json 落盘态)。"""
    students = (data or {}).get("students") or []
    total = 0
    for s in students:
        try:
            total += int(s.get("total_views") or 0)
        except (TypeError, ValueError):
            continue
    if total:
        return total
    # 没有 students 时退回 class_stats
    try:
        return int(((data or {}).get("class_stats") or {}).get("csdn", {})
                   .get("total_views") or 0)
    except (TypeError, ValueError):
        return 0


def class_posts(data):
    """班级总文章数 = 各学生文章数之和。"""
    students = (data or {}).get("students") or []
    total = 0
    for s in students:
        posts = s.get("posts")
        if isinstance(posts, list):
            total += len(posts)
        else:
            try:
                total += int(s.get("post_count") or 0)
            except (TypeError, ValueError):
                continue
    if total:
        return total
    try:
        return int(((data or {}).get("class_stats") or {}).get("csdn", {})
                   .get("total_posts") or 0)
    except (TypeError, ValueError):
        return 0


def _student_reading(s):
    try:
        return int(s.get("total_views") or 0)
    except (TypeError, ValueError):
        return 0


def failed_students(old_data, new_data):
    """哪些学生这次"废掉了"。

    判定: 上次有阅读量, 这次 (a) 整个人不见了, 或 (b) 阅读量掉到上次的一半以下
    (含归零)。阅读量只涨不跌, 掉一半 = 抓取残废, 不是真实数据。
    """
    old = {s.get("sid"): s for s in (old_data or {}).get("students") or []}
    new = {s.get("sid"): s for s in (new_data or {}).get("students") or []}
    bad = []
    for sid, o in old.items():
        o_read = _student_reading(o)
        if o_read <= 0:
            continue
        if sid not in new:
            bad.append((sid, o.get("name", sid), o_read, 0))
            continue
        n_read = _student_reading(new[sid])
        if n_read < STUDENT_DROP_RATIO * o_read:
            bad.append((sid, o.get("name", sid), o_read, n_read))
    return bad


# ---------- 校验 ----------
def evaluate(new_data, old_data):
    """校验新数据能不能接受。

    返回 {"ok": bool, "reasons": [...], "stats": {...}}
    ★ ok=False 就必须回滚 (宁可留旧数据, 也不能把残缺数据推上去)。
    """
    reasons = []
    old_total = class_reading(old_data)
    new_total = class_reading(new_data)
    old_posts = class_posts(old_data)
    new_posts = class_posts(new_data)
    bad = failed_students(old_data, new_data)
    n_students = len((old_data or {}).get("students") or []) or 1
    bad_ratio = len(bad) / n_students

    # R1 绝对下限: 总阅读腰斩
    floor = VIEW_FLOOR_RATIO * old_total
    if old_total > MIN_VIEWS_FOR_FLOOR and new_total < floor:
        reasons.append(
            f"R1 总阅读塌方: {new_total:,} < 上次 {old_total:,} 的 "
            f"{int(VIEW_FLOOR_RATIO * 100)}% (门槛 {int(floor):,})")

    # R2 学生级: 失败学生占比
    if bad_ratio > STUDENT_FAIL_RATIO:
        detail = ", ".join(f"{n}({o:,}→{n2:,})" for _, n, o, n2 in bad[:6])
        reasons.append(
            f"R2 失败学生 {len(bad)}/{n_students} 人 ({bad_ratio * 100:.0f}%) "
            f"> {int(STUDENT_FAIL_RATIO * 100)}% : {detail}")

    # R3 文章数塌方
    post_floor = POST_FLOOR_RATIO * old_posts
    if old_posts > 0 and new_posts < post_floor:
        reasons.append(
            f"R3 总文章塌方: {new_posts} < 上次 {old_posts} 的 "
            f"{int(POST_FLOOR_RATIO * 100)}% (门槛 {int(post_floor)})")

    return {
        "ok": not reasons,
        "reasons": reasons,
        "stats": {
            "old_total_views": old_total,
            "new_total_views": new_total,
            "old_total_posts": old_posts,
            "new_total_posts": new_posts,
            "failed_students": len(bad),
            "students_total": n_students,
            "failed_ratio": round(bad_ratio, 4),
            "view_floor": int(VIEW_FLOOR_RATIO * old_total),
            "post_floor": int(post_floor),
        },
    }


# ---------- 备份 ----------
def _dashboard_paths(dashboard_dir):
    """要备份/恢复的文件 (相对 dashboard 根目录)。"""
    d = Path(dashboard_dir)
    today = datetime.now().strftime("%Y-%m-%d")
    return [
        d / "data.json",
        d / "cache" / "csdn" / "snapshot.json",
        d / "history-real" / f"{today}.json",
    ]


def backup(dashboard_dir, keep=KEEP_BACKUPS, when=None):
    """备份 data.json / snapshot.json / 当日归档。

    返回备份目录路径 (Path)。备份目录里有一个 manifest.json,
    记录了哪些文件当时存在 —— 回滚时据此决定"恢复"还是"删掉新建的"。
    """
    d = Path(dashboard_dir)
    stamp = when or datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    dest = d / BACKUP_ROOT / stamp
    dest.mkdir(parents=True, exist_ok=True)

    manifest = {"created_at": stamp, "files": []}
    for src in _dashboard_paths(d):
        if not src.exists():
            continue
        rel = src.relative_to(d)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        manifest["files"].append(str(rel).replace("\\", "/"))

    _dump_json(dest / "manifest.json", manifest)

    # 只留最近 keep 份 (按目录名排序 = 按时间排序)
    root = d / BACKUP_ROOT
    dirs = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name)
    for old in dirs[:-keep] if len(dirs) > keep else []:
        shutil.rmtree(old, ignore_errors=True)

    # 给 .cmd 用的"最新备份路径"指针 (一行, 无 BOM, CRLF 结尾 —— cmd 的
    # `set /p VAR=<file` 对 LF-only 的文件会把换行符一起读进变量)
    root.mkdir(parents=True, exist_ok=True)
    with open(d / LATEST_FILE, "w", encoding="utf-8", newline="") as f:
        f.write(str(dest.resolve()) + "\r\n")
    return dest


def read_latest(dashboard_dir):
    """读 LATEST.txt, 拿到最近一次备份的目录。"""
    p = Path(dashboard_dir) / LATEST_FILE
    if not p.exists():
        return None
    txt = p.read_text(encoding="utf-8").strip()
    return Path(txt) if txt else None


# ---------- 回滚 ----------
def rollback(dashboard_dir, backup_dir):
    """把备份恢复回去。

    - 备份里有的文件 → 原样恢复
    - 这次跑出来、但备份里没有的 (新建的当日归档) → 删掉
    """
    d = Path(dashboard_dir)
    b = Path(backup_dir)
    if not b.exists():
        return False, [f"备份目录不存在: {b}"]
    manifest_path = b / "manifest.json"
    if not manifest_path.exists():
        return False, [f"备份里没有 manifest.json: {manifest_path}"]
    manifest = _load_json(manifest_path)
    restored, problems = [], []
    for rel in manifest.get("files", []):
        src = b / rel
        dst = d / rel
        if not src.exists():
            problems.append(f"备份里少了 {rel}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        restored.append(rel)
    # 这次跑出来的新文件 (备份时不存在) → 删掉, 别把残缺结果留在归档里
    for src in _dashboard_paths(d):
        rel = src.relative_to(d).as_posix()
        if rel in manifest.get("files", []):
            continue
        if src.exists():
            try:
                src.unlink()
                problems.append(f"删掉这次跑出来的新文件: {rel}")
            except OSError as e:
                problems.append(f"删不掉 {rel}: {e}")
    return True, restored + problems


# ---------- CLI ----------
def _cmd_backup(args):
    dest = backup(args.dir, keep=args.keep)
    print(f"[guard] 备份完成: {dest}")
    print(str(dest.resolve()))
    return 0


def _cmd_check(args):
    d = Path(args.dir)
    data_path = d / "data.json"
    if not data_path.exists():
        print("[guard] ❌ data.json 不存在, 无法校验")
        return 1
    if args.backup:
        b = Path(args.backup)
        old_path = b / "data.json"
        if not old_path.exists():
            print(f"[guard] ❌ 备份里没有 data.json: {old_path}")
            return 1
    else:
        b = read_latest(d)
        if not b:
            print("[guard] ❌ 找不到备份 (cache/backup/LATEST.txt), 无法校验")
            return 1
        old_path = b / "data.json"
        if not old_path.exists():
            print(f"[guard] ❌ 备份里没有 data.json: {old_path}")
            return 1

    try:
        new_data = _load_json(data_path)
    except (OSError, ValueError) as e:
        print(f"[guard] ❌ data.json 读不出来 (可能写坏了): {type(e).__name__}: {e}")
        return 1
    try:
        old_data = _load_json(old_path)
    except (OSError, ValueError) as e:
        print(f"[guard] ❌ 备份的 data.json 读不出来: {type(e).__name__}: {e}")
        return 1

    res = evaluate(new_data, old_data)
    st = res["stats"]
    print(f"[guard] 校验: 总阅读 {st['old_total_views']:,} → {st['new_total_views']:,} | "
          f"总文章 {st['old_total_posts']} → {st['new_total_posts']} | "
          f"失败学生 {st['failed_students']}/{st['students_total']}")
    if res["ok"]:
        print("[guard] ✅ 校验通过")
        return 0
    print("[guard] ❌ 校验不通过:")
    for r in res["reasons"]:
        print(f"[guard]    - {r}")
    return 1


def _cmd_rollback(args):
    b = Path(args.backup) if args.backup else read_latest(args.dir)
    if not b:
        print("[guard] ❌ 找不到备份, 无法回滚")
        return 1
    ok, msgs = rollback(args.dir, b)
    if ok:
        print(f"[guard] ✅ 已回滚到备份: {b}")
        for m in msgs:
            print(f"[guard]    - {m}")
        return 0
    print(f"[guard] ❌ 回滚失败: {b}")
    for m in msgs:
        print(f"[guard]    - {m}")
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="周日全量任务的安全网 (备份 / 校验 / 回滚)")
    ap.add_argument("--dir", default=".",
                    help="dashboard 根目录 (默认: 当前目录)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("backup", help="备份 data.json / snapshot.json / 当日归档")
    p.add_argument("--keep", type=int, default=KEEP_BACKUPS, help="保留最近几份")
    p.set_defaults(func=_cmd_backup)

    p = sub.add_parser("check", help="校验新数据 (不合格返回 1)")
    p.add_argument("--backup", default="", help="备份目录 (默认: 读 LATEST.txt)")
    p.set_defaults(func=_cmd_check)

    p = sub.add_parser("rollback", help="把备份恢复回去")
    p.add_argument("--backup", default="", help="备份目录 (默认: 读 LATEST.txt)")
    p.set_defaults(func=_cmd_rollback)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
