# 一键刷新演示：Skill 日/周报 + 跨渠 facts + 离线看板（不爬、不标、不调 LLM 合成）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "`n>>> 04日报周报/build_skill_reports.py"
python "04日报周报/build_skill_reports.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n>>> 09跨渠道AI/build_channel_facts.py"
python "09跨渠道AI/build_channel_facts.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n>>> 05展示页/build_dashboard.py"
python "05展示页/build_dashboard.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n完成：reports/、facts_cross_channel.json 与 05展示页/index.html 已刷新。"
Write-Host "跨渠道 AI 简报需单独运行（需 Key）：python 09跨渠道AI/synthesize_cross_channel.py"
Write-Host "无 Key 模板演示：python 09跨渠道AI/synthesize_cross_channel.py --template-only"
