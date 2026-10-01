# 06 对照 · B 站（传播对照第一渠）

> 定位：相对 TapTap 评价主链的**传播与议题对照渠道**；不替代商店口碑 KPI。

## 为什么要有这一层

- TapTap 评价回答「玩家是否推荐 / 是否继续玩」；
- B 站评论回答「视频场景下即时在吵什么」——是本仓库**主用的社交传播对照渠**；
- 两边**场景不同**，只做结构对照与定性互证，**禁止**把 B 站百分比直接当 KPI 异动。

## 产物

| 文件 | 说明 |
|------|------|
| `crawl_bili_comments.py` | 搜索视频 + reply 评论采集（限速 / UA / 可选 Cookie） |
| `annotate_bili_sample.py` | 限量标注（优先 DeepSeek v1.4 schema；无 Key 则弱规则并标明） |
| `build_contrast_report.py` | 生成与 TapTap 的对照报告 |
| `config.example.env` | 环境变量模板（**勿提交真实 Cookie**） |
| `comments_sample.csv` | 原始评论样本 |
| `videos_sample.csv` | 命中视频清单 |
| `annotations_bili_sample.csv` | 对照标注结果（schema 同 v1.4，约 150 条 LLM） |
| `reports/contrast_taptap_bili_YYYYMMDD.md` | 对照报告 |

## 快速跑通

```bash
# 1) 可选：复制配置（不要把真实 Cookie 提交进仓库）
copy config.example.env .env

# 2) 采集（默认目标约 350 条，受风控影响可能更少）
python L1_data_source/collectors/bilibili/crawl_bili_comments.py --target 350

# 3) 标注约 150 条（需 DEEPSEEK_API_KEY；否则加 --weak-only）
python L3_semantic/channels/bilibili/annotate_bili_sample.py --limit 150

# 4) 对照报告
python L6_delivery/contrast/bilibili/build_contrast_report.py
```

## 采集说明与降级

1. **搜索**：优先 WBI `wbi/search/type`；失败则 legacy search，再降级到 `BILI_UP_MIDS` 稿件列表。  
2. **评论**：优先 `x/v2/reply/main` 的 `next` 游标；WBI reply 易 `-403` 时自动降级；并拉取部分楼中楼。  
3. **限速**：`BILI_SLEEP_MIN/MAX`；请勿高频打满。  
4. **Cookie**：仅本地 `.env`，已在 `.gitignore`；仓库只保留 `config.example.env`。

## 边界

- 样本是「相关视频评论切片」，含弹幕式梗、二创语境，**不等于**游戏商店口碑。  
- 标注与 TapTap 共用 schema，但 B 站无星级（`score_raw` 为空）。  
- 不修改 `data/annotations/annotations_v1_4.csv`，不触及 qc 盲标表。

## 怎么打开看

1. 读对照报告：`reports/contrast_taptap_bili_*.md`  
2. 用 Excel / VS Code 打开 `comments_sample.csv`、`annotations_bili_sample.csv`  
3. 回到主看板：`L6_delivery/dashboard/index.html`（TapTap 主链）
