# 游戏档案（Game Profile）

一个 **游戏档案** = 一款游戏跑通全链路所需的全部可变参数。管线代码不含任何游戏专属常量——换档案即换游戏。

## 字段

| 字段 | 含义 | 使用方 |
|------|------|--------|
| `key` | 档案标识（默认取文件名） | 全部脚本 `--game` |
| `name` | 游戏名（进报告标题 / 标注 prompt / 看板标题） | 标注、日报周报、看板 |
| `app_id` | TapTap 游戏 ID（采集入口） | `01爬虫`、日报元数据 |
| `high_hours` | 高投入阈值（小时），展示口径 | `11情报Agent/risk_insight` |
| `aliases` | 等价名（对照渠软相关过滤） | `06对照_B站`、`08对照_微博` |
| `cross_channel.bili_keywords` | B 站检索关键词（env 兜底） | `06对照_B站` |
| `cross_channel.weibo_uids` | 微博官号 uid（时间线降级） | `08对照_微博` |

## 接入新游戏（三步）

1. 复制 `arknights.json` 为 `<新游戏>.json`，改 `key` / `name` / `app_id` / `aliases`（其余可留默认）；
2. 采集：`python 01爬虫/crawl_taptap_reviews.py --game <新游戏>`；
3. 依次跑清洗 → 标注 → 情报 Agent（`--game <新游戏>`），产出与报告自动带新游戏名。

```bash
# 列出已接入游戏
python -c "import sys; sys.path.insert(0,'games'); from game_profile import available; print(available())"
```

## 字段缺失时的行为

- 档案缺字段 → 由 `game_profile.DEFAULTS` 补默认值，不报错；
- **档案不存在 → 抛 `FileNotFoundError`**（不静默兜底到其它游戏，避免张冠李戴）。

> 注：`skills/arknights-*` 的目录名是 Skill 功能标识（不是游戏绑定），新增游戏无需改名。
