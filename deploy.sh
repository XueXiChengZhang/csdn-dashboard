#!/usr/bin/env bash
# CSDN Dashboard 部署脚本 (Git Bash / WSL / macOS / Linux)
# 用法:  ./deploy.sh "commit message"
# 不传参数则默认 "Update dashboard"

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# 提交信息
MSG="${1:-Update dashboard - $(date +%Y-%m-%d_%H:%M)}"

echo "==> 项目目录: $PROJECT_DIR"
echo "==> 提交信息: $MSG"
echo

# 1. 显示状态
echo "=== 当前状态 ==="
git status --short
echo

# 2. 检查 gh CLI 登录状态
if ! gh auth status >/dev/null 2>&1; then
  echo "❌ gh CLI 未登录。请先执行: gh auth login"
  exit 1
fi

# 3. 暂存
git add -A

# 4. 检查是否有变更
if git diff --cached --quiet; then
  echo "⚠️  无变更需要提交"
  exit 0
fi

# 5. 提交
git commit -m "$MSG"
echo "✓ 已提交"
echo

# 6. 检查是否有远端
if ! git remote get-url origin >/dev/null 2>&1; then
  echo "==> 创建 GitHub 仓库..."
  gh repo create csdn-dashboard --public --source=. --remote=origin --push \
    --description "高职学生 CSDN 监控仪表板"
  echo "✓ 仓库已创建并推送"
else
  echo "==> 推送到 origin..."
  git push origin main
  echo "✓ 已推送"
fi

echo
echo "=== 部署完成 ==="
echo "Pages URL: https://$(gh repo view --json owner,name -q '.owner.login + "/" + .name')"
echo "GitHub:    https://github.com/$(gh repo view --json owner,name -q '.owner.login + "/" + .name')"
