# CSDN Dashboard 部署脚本 (PowerShell)
# 用法:  .\deploy.ps1 -Message "commit message"
# 不传参数则默认 "Update dashboard"

param(
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ProjectDir

if ([string]::IsNullOrWhiteSpace($Message)) {
    $Message = "Update dashboard - $(Get-Date -Format 'yyyy-MM-dd_HH:mm')"
}

Write-Host "==> 项目目录: $ProjectDir" -ForegroundColor Cyan
Write-Host "==> 提交信息: $Message" -ForegroundColor Cyan
Write-Host ""

# 1. 显示状态
Write-Host "=== 当前状态 ===" -ForegroundColor Yellow
git status --short
Write-Host ""

# 2. 检查 gh CLI 登录状态
$ghStatus = gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ gh CLI 未登录。请先执行: gh auth login" -ForegroundColor Red
    exit 1
}

# 3. 暂存
git add -A

# 4. 检查是否有变更
$diff = git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "⚠️  无变更需要提交" -ForegroundColor Yellow
    exit 0
}

# 5. 提交
git commit -m "$Message"
Write-Host "✓ 已提交" -ForegroundColor Green
Write-Host ""

# 6. 检查是否有远端
$remoteUrl = git remote get-url origin 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "==> 创建 GitHub 仓库..." -ForegroundColor Yellow
    gh repo create csdn-dashboard --public --source=. --remote=origin --push `
        --description "高职学生 CSDN 监控仪表板"
    Write-Host "✓ 仓库已创建并推送" -ForegroundColor Green
} else {
    Write-Host "==> 推送到 origin..." -ForegroundColor Yellow
    git push origin main
    Write-Host "✓ 已推送" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== 部署完成 ===" -ForegroundColor Cyan
$repoInfo = gh repo view --json owner,name -q '.owner.login + "/" + .name'
Write-Host "GitHub:    https://github.com/$repoInfo" -ForegroundColor Cyan
Write-Host "Pages:     https://$($repoInfo -replace '/', '.github.io/')" -ForegroundColor Cyan
