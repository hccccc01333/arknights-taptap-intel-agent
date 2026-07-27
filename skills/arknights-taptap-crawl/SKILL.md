---
name: arknights-taptap-crawl
description: >-
  Implements or fixes TapTap Arknights review crawling under a verified field
  contract (app_id=70253, list-by-app, checkpoint, compliance). Use when
  writing/debugging the crawler, mapping JSON fields, handling pagination,
  X-UA env, 403/429, or QC reports under 01爬虫/ or 02数据/.
license: MIT
metadata:
  version: "1.0"
  standard: agentskills.io
  project: arknights-taptap-yuqing
---

# TapTap 评价爬虫（字段契约）

## 目标

按**已验证接口样本**采集明日方舟评价，写入结构化 CSV；禁止臆造接口中不存在的字段。

## 何时用

用户提到：爬虫、TapTap、`list-by-app`、翻页、`X-UA`、checkpoint、字段映射、采集质检。

## 权威输入

| 项 | 路径 |
|----|------|
| 完整字段契约 | `01爬虫/爬取提示词构建.md` |
| 接口样本 | `01爬虫/TAPTAP网页数据json` |
| 环境变量示例 | `01爬虫/config.example.env` |
| 生产脚本 | `01爬虫/crawl_taptap_reviews.py` |

细则见 [references/contract-checklist.md](references/contract-checklist.md)。

## 工作流

1. **先读契约与样本 JSON**，确认字段路径；没有证据的字段 → 置空，禁止编造。  
2. **实现/修改脚本**时遵守：
   - `GET .../webapiv2/review/v2/list-by-app`
   - `app_id=70253`，`limit=10`（不改），`sort=new`，`from` 翻页
   - `TAPTAP_X_UA` 只读环境变量 / `.env`（勿提交密钥）
3. **停止条件**：空列表、不足 10 条、早于起始日、达上限、撞水位线。  
4. **合规**：403/429/验证码 → 停或降速，不绕过；公开评价 only。  
5. **验收**：去重、checkpoint 续跑、run_log、质检报告；输出到 `02数据/reviews.csv`。

## 禁止

- 把接口 `total` 当全站评价总量  
- 下载图片 / 做用户画像  
- 把真实昵称、完整 user_id 公开到仓库  

## 验收清单

- [ ] 每个入库字段在样本 JSON 有路径，或明确「派生/置空」  
- [ ] 无密钥写入仓库  
- [ ] 中断后续跑不丢水位  
- [ ] 有 crawl QC 报告
