# 07 对照 · 抖音轻量样本

> 定位：TapTap 主链之外的**短视频评论对照切片**；不替代商店口碑 KPI。

## 为什么要有这一层

- TapTap 评价回答「玩家是否推荐 / 是否继续玩」；
- 抖音评论回答「短视频场景下即时在吵什么、梗与议题如何传播」；
- 两边**场景不同**：抖音更短、更梗、更情绪化，只做结构对照与定性互证。

## 产物

| 文件 | 说明 |
|------|------|
| `crawl_douyin_comments.py` | 搜索/固定 aweme + 评论采集（限速 / UA / 可选 Cookie；失败可降级） |
| `annotate_douyin_sample.py` | 限量标注（优先 DeepSeek v1.4 schema；无 Key 则弱规则并标明） |
| `config.example.env` | 环境变量模板（**勿提交真实 Cookie**） |
| `comments_sample.csv` | 原始评论样本 |
| `videos_sample.csv` | 命中/种子视频清单 |
| `annotations_douyin_sample.csv` | 对照标注结果（schema 同 v1.4） |
| `reports/crawl_douyin_*.md` | 采集报告（含直播/降级数量） |
| `reports/annotate_douyin_*.md` | 标注报告 |

## 快速跑通

```bash
# 1) 可选：复制配置（不要把真实 Cookie 提交进仓库）
copy config.example.env .env

# 2) 采集（默认目标约 320 条；风控时自动/可强制种子降级）
python 07对照_抖音/crawl_douyin_comments.py --target 320

# 仅演示种子（跳过直播）：
# python 07对照_抖音/crawl_douyin_comments.py --force-fallback --target 320

# 3) 标注约 120 条（需 DEEPSEEK_API_KEY；否则加 --weak-only）
python 07对照_抖音/annotate_douyin_sample.py --limit 120
```

## 采集说明与降级

1. **搜索**：Web `general/search/single`；无 Cookie 时常返回「请先登录」或验证码页。  
2. **固定 aweme**：`DOUYIN_AWEME_IDS` 使用公开 `www.douyin.com/video/<id>` 列表作为保底视频源。  
3. **评论**：优先 `aweme/v1/web/comment/list`，失败再试 ies 评论接口。  
4. **限速**：`DOUYIN_SLEEP_MIN/MAX`；请勿高频打满。  
5. **Cookie**：仅本地 `.env`；仓库只保留 `config.example.env`。  
6. **fallback_seed**：直播条数不足时写入演示语料（`source=fallback_seed`），绑定真实公开 aweme_id + 短视频评论风格文本；**不宣称实时抓取**。采集报告会分开统计直播/种子条数。

## 边界

- 样本是「相关短视频评论切片 / 演示种子」，含梗与二创语境，**不等于**游戏商店口碑。  
- 标注与 TapTap 共用 v1.4 schema，但抖音无星级（`score_raw` 为空）。  
- 不修改 `03标注结果/annotations_v1_4.csv`，不触及 qc 盲标表。

## 怎么打开看

1. 读采集/标注报告：`reports/`  
2. 用 Excel / VS Code 打开 `comments_sample.csv`、`annotations_douyin_sample.csv`  
3. 回到主看板：`05展示页/index.html`（TapTap 主链；四渠矩阵后续接入）
