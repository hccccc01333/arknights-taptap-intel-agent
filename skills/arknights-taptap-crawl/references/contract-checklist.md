# 爬虫契约速查

## 接口

| 项 | 值 |
|----|-----|
| 方法 | GET |
| 路径 | `https://www.taptap.cn/webapiv2/review/v2/list-by-app` |
| app_id | `70253` |
| limit | `10`（固定） |
| sort | `new` |
| from | `0, 10, 20, …` |
| X-UA | 环境变量 `TAPTAP_X_UA` |

评论主体通常在 `data.list[].moment.review`。

## MVP 字段

`review_id`、正文、评分、发布时间、平台、游玩时长（可空）、点赞。

完整映射表以仓库 `L1_data_source/collectors/taptap/爬取提示词构建.md` 为准。

## 模式

- **全量**：从新到旧翻，按上限/日期/空页停  
- **增量**：撞上次水位或已入库且未变更则停；正文/评分/编辑时间变化则更新
