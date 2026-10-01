# 08 对照 · 微博轻量样本

> 定位：TapTap 主链之外的**公开热议对照切片**。

## 为什么要有这一层

- TapTap 评价回答「玩家是否推荐 / 是否继续玩」；
- 微博回答「公开热议与话题切片里在吵什么」；
- 场景不同，只做结构对照与定性互证，**禁止**把微博百分比直接当 KPI 异动。

## 产物

| 文件 | 说明 |
|------|------|
| `crawl_weibo_posts.py` | 关键词检索博文 + 评论切片（限速 / UA / 可选 Cookie） |
| `annotate_weibo_sample.py` | 限量标注（优先 DeepSeek v1.4 schema；无 Key 则弱规则并标明） |
| `config.example.env` | 环境变量模板（**勿提交真实 Cookie**） |
| `comments_sample.csv` | 原始样本（博文+评论，`item_type` 区分） |
| `posts_sample.csv` | 命中博文清单 |
| `annotations_weibo_sample.csv` | 对照标注结果（schema 同 v1.4） |
| `reports/crawl_weibo_*.md` / `annotate_weibo_*.md` | 采集/标注报告 |

## 快速跑通

```bash
# 1) 可选：复制配置（不要把真实 Cookie 提交进仓库）
copy config.example.env .env

# 2) 采集（默认目标约 350 条；风控下可能降级补样本）
python L1_data_source/collectors/weibo/crawl_weibo_posts.py --target 350

# 3) 标注约 120 条（需 DEEPSEEK_API_KEY；否则加 --weak-only）
python L3_semantic/channels/weibo/annotate_weibo_sample.py --limit 120
```

## 采集说明与降级

1. **搜索**：`m.weibo.cn` container 综合/实时检索。  
2. **降级**：超话 `WEIBO_TOPIC_CONTAINERIDS` → 用户时间线 `WEIBO_UIDS` → 仍不足则写入 `source=degraded_sample` 可演示样本并记报告。  
3. **评论**：优先 `comments/hotflow`，失败则 `api/comments/show`。  
4. **限速**：`WEIBO_SLEEP_MIN/MAX`；请勿高频打满。  
5. **Cookie**：仅本地 `.env`，已在模块 `.gitignore`；仓库只保留 `config.example.env`。

## 字段要点

- `source=weibo`（或 `m_search` / `hotflow` / `degraded_sample` 等）
- `mid` / `cid` / `comment_id` / `text` / `publish_time` / `url`
- 标注侧无星级：`score_raw` 为空

## 边界

- 样本是「关键词相关博文+评论切片」，含梗与二创语境，**不等于**游戏商店口碑。  
- 标注与 TapTap 共用 v1.4 schema，但不修改 `data/annotations/annotations_v1_4.csv`，不触及 qc 盲标表。  
- 限量演示样本，不作全量历史与实时告警。

## 怎么打开看

1. 读采集/标注报告：`reports/`  
2. 用 Excel / VS Code 打开 `comments_sample.csv`、`annotations_weibo_sample.csv`  
3. 回到主看板：`L6_delivery/dashboard/index.html`（TapTap 主链）
