#!/usr/bin/env bash
# install-cron.sh - 安装 CSDN 监控 cron job
# - 每日 00:00 (午夜): 抓取 + 写 data.json
# - 每日 05:30 (凌晨): 双保险补抓
# - 每次跑后: git commit + push (部署到 GitHub Pages / Vercel)
#
# 用法: bash scripts/install-cron.sh [--dry-run]
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DASHBOARD="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="python"
# Windows Git Bash: python -> python.exe
if ! command -v python >/dev/null 2>&1; then
  if command -v python.exe >/dev/null 2>&1; then PYTHON="python.exe"; fi
fi

CRON_LINE_CSDN="0 0 * * * cd $DASHBOARD && $PYTHON scripts/csdn-monitor-v2.py --force-now 2>>logs/cron-err.log"
CRON_LINE_CSDN_2="30 5 * * * cd $DASHBOARD && $PYTHON scripts/csdn-monitor-v2.py --force-now 2>>logs/cron-err.log"

DRY=0
for arg in "$@"; do
  case $arg in
    --dry-run) DRY=1 ;;
  esac
done

mkdir -p "$DASHBOARD/logs"

echo "Cron entries to add:"
echo "  $CRON_LINE_CSDN"
echo "  $CRON_LINE_CSDN_2"

if [ "$DRY" = "1" ]; then
  echo "[DRY-RUN] not actually installing"
  exit 0
fi

# 读当前 crontab, 过滤掉旧的 csdn-monitor 行
TEMP_CRON=$(mktemp)
crontab -l 2>/dev/null | grep -v 'csdn-monitor-v2\.py' > "$TEMP_CRON" || true
echo "$CRON_LINE_CSDN" >> "$TEMP_CRON"
echo "$CRON_LINE_CSDN_2" >> "$TEMP_CRON"
crontab "$TEMP_CRON"
rm -f "$TEMP_CRON"
echo "✓ Cron installed. Check with: crontab -l"