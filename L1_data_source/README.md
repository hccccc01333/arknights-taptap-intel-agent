# L1 · 信号采集层（Data Source Layer）

> **这一层只回答一个问题：全网发生了什么。**
> 它是 Data Engineering，**不放 Agent**：不做判断、不打可发酵度分、不做语义理解。
> 判断在 L4，生成在 L5。这一层做多了，后面每一层都会被迫替它擦屁股。

## 1. 输入输出

```
data/raw/<platform>/  ──适配器──▶  ContentEvent  ──▶  data/events/*.jsonl + _manifest.json
   （各平台原始 CSV/JSON）          （全网统一结构）        （带条数与坏行统计）
```

跑批入口：

```bash
python L1_data_source/normalize.py                 # 全量
python L1_data_source/normalize.py --only taptap   # 单平台
python L1_data_source/normalize.py --verbose       # 打印坏行样例
```

## 2. Content Event（统一结构）

前 8 项是跨平台公共面，其余是工程必需字段：

| 字段 | 含义 | 缺失时 |
|---|---|---|
| `platform` | 平台标识（取自注册表） | 必填 |
| `title` / `content` | 标题 / 正文 | 至少有一个，否则丢弃 |
| `author` | 作者（**已脱敏哈希**） | `unknown` |
| `published_at` | 发布时间（ISO8601，东八区） | `null` |
| `views` / `likes` / `comments` / `shares` | 播放 / 赞 / 评论数 / 转发 | `null` |
| `event_id` | `平台:类型:原生id` | 自动生成 |
| `source_type` | post / comment / video / review / hashtag … | 见注册表 |
| `parent_id` | 评论所属帖/视频 | `null` |
| `raw_ref` | **溯源**：`文件名:行号` | 必填 |

## 3. ★ 三条硬规则（踩过坑才立的）

1. **缺失 = `null`，绝不填 0。**
   0 是"有这个数且为 0"，null 是"平台没给"。本项目踩过：TapTap `posts.supports` 恒为 0，
   被下游当成传播度用，排序直接失效。
2. **作者一律脱敏。** 只收 hash（采集侧 hash 或本层 sha1），明文昵称会被校验器拦下并计入坏行。
3. **可溯源。** 每条事件必须能追回 `文件:行号`；说不出来源的数据不算数。

## 4. 平台覆盖（诚实登记，登记在册 ≠ 已接入）

| 状态 | 平台 |
|---|---|
| **active**（4，已有适配器 + 真实数据） | TapTap、B站、抖音、微博 |
| **planned**（10，仅登记） | 小红书、知乎、百度指数、微信、新闻媒体、Reddit、X、YouTube、Google Trends、Steam |

TapTap 覆盖 5 类中的 4 类：评论(`review`)、动态(`moment`)、话题(`hashtag`)、评分(随评论带出)；
**论坛(`forum`) 与榜单(`rank`) 尚无落盘数据**，已在注册表里显式留位，不假装接了。

## 5. 真实跑批结果（2026-10-01 实测，不是估算）

| 平台 | 数据集 | 读入 | 通过 | 丢弃 |
|---|---|---|---|---|
| taptap | 动态 / 评论 / 话题 / 游戏评论 | 3274 | 3273 | 1 |
| bilibili | 视频 / 评论 | 455 | 455 | 0 |
| douyin | 视频 / 评论 | 330 | 330 | 0 |
| weibo | 微博 / 评论 | 393 | 393 | 0 |
| **合计** | | **4452** | **4451** | **1** |

唯一被丢弃的是一条正文为空的评论（`discovery_comments.csv:82`），已计入 manifest 的 `problems`。

## 6. 已知数据缺陷（不粉饰，写在这里避免下游重复踩）

- **抖音/微博首批是 `degraded_sample`**：点赞/评论数大量为空 → 归一化后是 `null`。
  下游排序必须先过滤 `null`，否则会退化成"按空排序"（这正是原金句判据失效的机制）。
- **TapTap `pv_total` 常为 0**（采集侧拿不到），不能当曝光排序依据。
- **话题的 `page_view` 是话题页曝光，不等于社区在讨论** —— 这是本项目最大的认知陷阱
  （见 `docs/目标倒推与重构.md` §3）。
- `data/raw/taptap/reviews.csv` 里的 `raw_json_path` 仍指向重排前的旧路径：
  这是**当时的采集事实**，故意不改，以保证可回溯。

## 7. 目录

```
L1_data_source/
├── schema/content_event.py   # 统一结构 + 校验 + PII 闸门
├── adapters/
│   ├── base.py               # Dataset / Adapter 契约
│   ├── taptap.py  bilibili.py  douyin.py  weibo.py
│   └── __init__.py           # 平台注册表（active / planned）
├── collectors/               # 采集脚本（按平台分，从旧 01爬虫 / 06~08对照 迁入）
├── normalize.py              # 跑批入口，输出 data/events/
└── README.md
```
