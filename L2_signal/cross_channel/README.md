# 09 跨渠道 AI（旗舰）

> 定位：在 TapTap 主链 + B站/抖音/微博对照切片上，用 **facts 锁数 + LLM** 产出跨渠道简报与议题对齐。

## 为什么单独做这一层

- 四渠共用 v1.4 schema，但场景不同：商店评价 ≠ 视频弹幕 ≠ 短视频梗 ≠ 微博热议。
- 重点产出是 **议题对齐**（同一槽点在哪几渠出现、强度差多少）与 **渠道人设**，而不是再堆一张饼图。
- **数字只允许来自代码聚合的 facts**；模型只写定性综合，引用对不上就重试或模板降级。

## 产物

| 文件 | 说明 |
|------|------|
| `build_channel_facts.py` | 聚合四渠 → `facts_cross_channel.json` |
| `synthesize_cross_channel.py` | DeepSeek 高 reasoning → 简报 + 议题对齐 |
| `facts_cross_channel.json` | 锁数 facts（可无 Key 刷新） |
| `latest_summary.json` | 看板可读的摘要四句 |
| `reports/cross_channel_brief_YYYYMMDD.md` | 综合简报：四句摘要 / 人设 / 值班建议 |
| `reports/topic_alignment_YYYYMMDD.md` | 议题对齐详表 + 原话 id |

## 快速跑通

```bash
# 1) 锁数（无需 API Key）
python L2_signal/cross_channel/build_channel_facts.py

# 2) LLM 综合（需 DEEPSEEK_API_KEY；无 Key 自动模板降级）
python L2_signal/cross_channel/synthesize_cross_channel.py

# 仅模板（演示/CI）：
# python L2_signal/cross_channel/synthesize_cross_channel.py --template-only
```

`scripts/refresh_demo.py` 会重建 facts + 看板，**不会**自动跑 synthesize（避免无 Key 时整链失败）。需要 AI 简报时单独执行上列第 2 步。

## 输入数据

| 渠道 | 标注 | 文本源 |
|------|------|--------|
| TapTap | `data/annotations/annotations_v1_4.csv` | `data/processed/reviews/reviews_clean.csv` |
| B站 | `data/raw/bilibili/annotations_bili_sample.csv` | `comments_sample.csv` |
| 抖音 | `data/raw/douyin/annotations_douyin_sample.csv` | `comments_sample.csv` |
| 微博 | `data/raw/weibo/annotations_weibo_sample.csv` | `comments_sample.csv` |

facts 会标记 `data_quality.is_degraded`（如抖音 `fallback_seed`、微博 `degraded_sample` 占比过高）。

## 边界

- 不把对照渠百分比写进 TapTap KPI /「全网口碑」。
- 不修改 `annotations_v1_4.csv`，不触及 qc 盲标表。
- 降级样本只作方法演示，叙事必须标明。
