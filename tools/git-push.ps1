<#
.SYNOPSIS
    GitHub 推送封装脚本：自动转换 wikilinks → md links 后推送，推送成功后还原。

.DESCRIPTION
    执行流程：
      1. convert_links.py --to-md      (wikilinks → MD links)
      2. git add -A && git commit       (提交转换结果)
      3. git push                       (推送至远端)
      4. convert_links.py --to-wikilinks (还原为 wikilinks)
      5. git add -A && git commit       (提交还原结果)

.PARAMETER Proxy
    通过代理推送（7890 端口，适合 GitHub 访问不畅时使用）。
    使用方式:  .\tools\git-push.ps1 -Proxy origin main

.EXAMPLE
    .\tools\git-push.ps1 origin main
    .\tools\git-push.ps1 -Proxy
    .\tools\git-push.ps1 -Proxy origin main
#>

param(
    [switch]$Proxy
)

$ErrorActionPreference = "Stop"
$python = "C:\Program\anaconda3\python.exe"
$script = "tools\convert_links.py"
$root = git rev-parse --show-toplevel 2>$null
if (-not $root) {
    Write-Error "当前目录不在 Git 仓库中"
    exit 1
}

Push-Location $root

# ── Step 1: 正向转换 ──────────────────────────────────────────────────────
Write-Host "`n[Step 1/5] 正向转换: wikilinks -> MD links ..." -ForegroundColor Cyan
& $python $script --to-md
$forwardOk = $LASTEXITCODE -eq 0
if (-not $forwardOk) {
    Write-Error "正向转换失败，中止推送"
    Pop-Location
    exit 1
}

# ── Step 2: 提交转换结果 ───────────────────────────────────────────────────
Write-Host "`n[Step 2/5] 提交转换结果 ..." -ForegroundColor Cyan
git add -A
$hasChanges = git diff --cached --quiet 2>$null; $LASTEXITCODE -ne 0
if ($hasChanges) {
    git commit -m "chore: pre-push wikilinks -> md conversion"
    Write-Host "  已创建提交" -ForegroundColor Green
} else {
    Write-Host "  无变更，跳过提交" -ForegroundColor Yellow
}

# ── Step 3: 推送 ───────────────────────────────────────────────────────────
Write-Host "`n[Step 3/5] 推送至 GitHub ..." -ForegroundColor Cyan
$pushArgs = $args -join " "
if (-not $pushArgs) { $pushArgs = "origin" }

if ($Proxy) {
    Write-Host "  使用代理 socks5h://127.0.0.1:7890" -ForegroundColor Yellow
    $proxyConfig = "-c http.proxy=socks5h://127.0.0.1:7890 -c https.proxy=socks5h://127.0.0.1:7890"
    $pushResult = Invoke-Expression "git $proxyConfig push $pushArgs 2>&1"
} else {
    $pushResult = Invoke-Expression "git push $pushArgs 2>&1"
}

Write-Host $pushResult
$pushOk = $LASTEXITCODE -eq 0

# ── Step 4: 反向转换 ──────────────────────────────────────────────────────
Write-Host "`n[Step 4/5] 反向转换: MD links -> wikilinks ..." -ForegroundColor Cyan
& $python $script --to-wikilinks
if ($LASTEXITCODE -ne 0) {
    Write-Warning "反向转换异常，请检查 tools/_convert_state.json"
}

# ── Step 5: 提交还原结果 ───────────────────────────────────────────────────
Write-Host "`n[Step 5/5] 提交还原结果 ..." -ForegroundColor Cyan
git add -A
$hasChanges = git diff --cached --quiet 2>$null; $LASTEXITCODE -ne 0
if ($hasChanges) {
    git commit -m "chore: post-push md -> wikilinks restore"
    Write-Host "  已创建提交" -ForegroundColor Green
} else {
    Write-Host "  无变更，跳过提交" -ForegroundColor Yellow
}

Pop-Location

if ($pushOk) {
    Write-Host "`n推送成功!" -ForegroundColor Green
    exit 0
} else {
    Write-Host "`n推送失败，请检查网络或尝试 -Proxy 参数" -ForegroundColor Red
    exit 1
}
