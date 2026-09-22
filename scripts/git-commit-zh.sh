#!/bin/bash
# scripts/git-commit-zh.sh — 在 Windows Git Bash 下用 UTF-8 提交中文 commit message
#
# 用法:
#   bash scripts/git-commit-zh.sh "type: 中文 message"
#   echo "type: 中文" | bash scripts/git-commit-zh.sh
#
# 注意:
#   - 此脚本确保 git commit message 以 UTF-8 编码写入
#   - 需要仓库已配 i18n.commitencoding=utf-8 (脚本会先检查并自动配置)
#   - 必须在 git repo 根目录运行
set -e

# 1. 检查并设置 i18n.commitencoding
ENC=$(git config --get i18n.commitencoding || echo "")
if [ "$ENC" != "utf-8" ]; then
    echo "⚠️  i18n.commitencoding 未设为 utf-8, 自动配置仓库级 config"
    git config i18n.commitencoding utf-8
fi

# 2. 取 message
if [ -n "$1" ]; then
    MSG="$1"
elif [ -n "$GIT_MSG" ]; then
    MSG="$GIT_MSG"
elif [ ! -t 0 ]; then
    MSG=$(cat)
else
    echo "用法: bash scripts/git-commit-zh.sh 'type: 中文 message'" >&2
    exit 1
fi

# 3. 用临时文件传递 (避免 bash 对 argv 做 byte 重排)
#    Windows 上 mktemp 可能默认 /tmp 不存在, 用 $TMP/$TEMP 兜底
TMPDIR_LOCAL="${TMP:-${TEMP:-${TMPDIR:-.}}}"
TMPF="$TMPDIR_LOCAL/csdn-zh-commit-$$.txt"
: > "$TMPF" 2>/dev/null || { echo "无法写临时文件 $TMPF" >&2; exit 1; }

# 用 python 写文件 (UTF-8 no BOM) — 最可靠
if command -v python >/dev/null 2>&1; then
    MSG="$MSG" python -c "import os; open(r'$TMPF','wb').write(os.environ['MSG'].encode('utf-8'))"
else
    printf '%s' "$MSG" > "$TMPF"
fi

# 4. 强制 UTF-8 编码并 commit
export GIT_COMMIT_ENCODING=utf-8
git commit -F "$TMPF"
RC=$?

# 5. 清理
rm -f "$TMPF"
exit $RC