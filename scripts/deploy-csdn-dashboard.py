#!/usr/bin/env python3
"""
deploy-csdn-dashboard.py
- git add + commit + push
- vercel deploy (production alias csdn-dashboard.vercel.app)

用法:
  python deploy-csdn-dashboard.py --message "..."
  python deploy-csdn-dashboard.py --dry-run
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent


def run(cmd, cwd=None, check=True, **kw):
    """run subprocess, return (rc, stdout, stderr)"""
    r = subprocess.run(cmd, cwd=cwd or DASHBOARD, capture_output=True,
                      text=True, encoding="utf-8", errors="replace", **kw)
    if check and r.returncode != 0:
        print(f"[FAIL] {' '.join(cmd)}\n{r.stdout}\n{r.stderr}", file=sys.stderr)
        sys.exit(r.returncode)
    return r


def git(*args, dry=False, **kw):
    if dry:
        print(f"[DRY] git {' '.join(args)}")
        return None
    return run(["git", *args], **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--message", "-m", required=True,
                    help="git commit message")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-push", action="store_true",
                    help="跳过 git push")
    ap.add_argument("--no-vercel", action="store_true",
                    help="跳过 Vercel 部署")
    args = ap.parse_args()

    print(f"[deploy] cwd: {DASHBOARD}")

    # 1) git status
    st = git("status", "--short", dry=args.dry_run)
    if st:
        print(st.stdout or "(clean)")
    # 2) git add + commit
    git("add", "-A", dry=args.dry_run)
    git("commit", "-m", args.message, dry=args.dry_run)
    # 3) git push
    if not args.no_push:
        git("push", "origin", "main", dry=args.dry_run)
    # 4) vercel deploy --prod
    if not args.no_vercel:
        vc = shutil.which("vercel")
        if not vc:
            for p in [r"C:\Users\Administrator\AppData\Roaming\npm\vercel.cmd",
                      r"C:\Users\Administrator\AppData\Roaming\npm\vercel"]:
                if os.path.exists(p):
                    vc = p
                    break
        if vc:
            if args.dry_run:
                print(f"[DRY] {vc} deploy --prod --yes")
            else:
                r = subprocess.run([vc, "deploy", "--prod", "--yes"],
                                  cwd=DASHBOARD, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=300)
                out = (r.stdout or "") + (r.stderr or "")
                print(out[-1500:])  # tail
                if r.returncode != 0:
                    print(f"[vercel] FAIL rc={r.returncode}", file=sys.stderr)
                    sys.exit(r.returncode)
        else:
            print("[WARN] vercel not found in PATH; skipping")

    print("[deploy] done")


if __name__ == "__main__":
    main()