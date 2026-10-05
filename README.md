# 全网热点追踪 → TapTap 增长创意系统

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Tests](https://img.shields.io/badge/tests-323%20passing-34d399)
![Status](https://img.shields.io/badge/状态-V1%20可用-38bd8f)

**AI 自动化情报与素材系统**：从全网捕捉正在形成的热点，判定它与 TapTap 社区的相关性，
产出可直接执行的增长创意与素材清单。

> **系统边界**：本系统**只负责产出** —— 情报报告、增长创意、素材清单。
> 运营跟进、投放执行、效果验证在外部系统完成。

---

## 它做什么

```
① 全网热点采集 → ② 热点识别/形成判定 → ③ 与 TapTap 相关性匹配 → ④ 筛选高价值 → ⑤ AI 生成创意/素材
```

不只告诉你"这个话题热了"，而是进一步回答：

- **这个热点和 TapTap 用户相关吗？** → 三档判定：`related`（进看板）/ `adjacent`（背景参考）/ `irrelevant`（丢弃）
- **能转成拉新/促活/内容传播/下载吗？** → 增长机会 + 机制链（如 `热点 → 话题讨论 → 社区沉淀 → 长期活跃`）
- **该做什么、什么时候做？** → 位置 + 步骤 + 文案 + 素材清单 + 风险 + 提前期 + KPI 目标

---

## 当前能力

### 数据采集（L1）

| 渠道 | 采什么 | 状态 |
|---|---|---|
| **百度热搜**（综合） | 搜索意图，51 条/轮 | ✅ |
| **百度游戏热搜** | **游戏专属榜 30 条/轮**（纯度最高） | ✅ |
| **微博热搜** | 破圈话题，49 条/轮，带「新」标记 | ✅ |
| **B站游戏区 + 热门** | 创作者生产，126 条/轮，播放增速 | ✅ |
| **TapTap S1** | 2357 个游戏社区索引（app_id→group_id + 四要素） | ✅ |
| **TapTap S2** | 单游戏帖子流 + 计数快照（thread 发现） | ✅ |
| **TapTap S3** | 帖子评论（全量首抓 + 增量补，parent_id 贯通） | ✅ |
| **TapTap S4/S5/S6** | 热榜（辅助）+ 发现流 + 话题帖 | ✅ |
| **TapTap 评分区/公告** | 口碑信号 + 官方动作归因 | ✅ |
| 抖音话题 / 贴吧 / 知乎 | — | ❌ 需登录态或风控严格 |

**关键设计**：热榜是**存量榜**（词上榜时已过峰值），所以只报 `forming` 档（新进榜 / 在涨 / 排名升）。

### 分析与产出（L3/L4）

| 能力 | 做什么 |
|---|---|
| **形成中判定** | 以采集时刻为锚点算发帖速率，四档：forming / rising / peaking / fading |
| **三层相关性过滤** | 词表 117 词 → bge 语义召回 → LLM 三档判定 |
| **跨平台事件合并** | 同一件事在多平台的重复上报合并成一个 Event（bge 召回 + LLM 判定） |
| **事件图** | 站外热点 ↔ 站内话题 ↔ 游戏节点，含传播路径 |
| **游戏标签库** | 2357 个游戏模糊匹配（含中点/别名），外部热点 → TapTap 社区寻址 |
| **结构化创意生成** | 位置/步骤/文案/素材/风险/提前期/KPI，LLM 按类型逐条生成 |
| **时效闸门** | 热点年龄 vs 创意提前期（Push 2h / 专题 4h / 厂商合作 72h），赶不上就标「窗口已过」 |
| **KPI 基线** | 社区内容分位数（如互动率 P75 = 6.91%），标注「参照值，非承诺」 |

### Agent 自主调度（V2）

Agent 每轮读完数据自己决定下一轮怎么采，规则可解释：

```
信号：15 个帖评论增长≥10 → 评论预算翻倍
信号：覆盖率 11% < 30%   → 增加评论页数
输出：max_comment_calls 150→225, comment_pages_per_post 10→15
```

每轮从**基准值**出发（不是上轮结果叠加，防复合膨胀）。

---

## 快速开始

```bash
# 1. 采集（单个渠道）
python L1_data_source/collectors/baidu/crawl_baidu_hot.py --tabs realtime,game
python L1_data_source/collectors/weibo/crawl_weibo_hot.py     # 需 WEIBO_COOKIE
python L1_data_source/collectors/taptap/crawl_taptap_community.py --game arknights

# 2. 常驻采集（每 10-15 分钟一轮）
python -u L1_data_source/collectors/baidu/crawl_baidu_hot.py --watch --interval 600
python -u L1_data_source/collectors/weibo/crawl_weibo_hot.py --watch --interval 600
python -u L1_data_source/collectors/bilibili/crawl_bili_game_hot.py --watch --interval 900

# 3. 入库 → 加工
python L1_data_source/pipeline.py --once --all
python L2_signal/processing/pipeline.py --run

# 4. 分析 → 创意
python L3_trend/forming_report.py            # 正在形成的热点
python L3_trend/hotspot_filter.py            # 相关性三档判定
python L3_trend/event_resolver.py            # 跨平台事件合并
python L4_intelligence/intelligence/hotspot_to_creative.py   # 结构化创意

# 5. Agent 自主调参
python L3_trend/strategy.py                  # 决定下轮参数
python L3_trend/feedback.py                  # 评估上轮效果

# 6. Webapp（社区报告 / 增长创意 / 数据源）
python -m uvicorn webapp.main:app --port 8200    # 演示密码 demo
```

---

## 配置

### 登录态 Cookie（可选，不填也能跑）

```bash
# 各平台的 .env 已在 .gitignore 保护下
L1_data_source/collectors/taptap/.env    # TAPTAP_X_UA（必需）+ TAPTAP_COOKIE（可选，深翻用）
L1_data_source/collectors/weibo/.env     # WEIBO_COOKIE
L1_data_source/collectors/douyin/.env    # DOUYIN_COOKIE
L1_data_source/collectors/baidu/.env     # BAIDU_COOKIE
```

### 采集参数（前端可调，落盘 `data/state/crawl_config.json`）

| 参数 | 默认 | 作用 |
|---|---|---|
| `fresh_hours` | 72 | 新帖窗口：只收最近 N 小时发布的帖 |
| `max_pages` | 12 | 每轮翻页深度 |
| `max_comment_calls` | 150 | 每轮评论接口预算 |
| `comment_pages_per_post` | 10 | 新帖评论抓全页数（每页 20 条） |
| `max_age_days` | 14 | 超期且无增长的帖子停止监测 |
| `hot_ups_threshold` | 30 | 点赞阈值：达到就抓全评论 + 重点监测 |

---

## 项目结构

```
L1_data_source/          采集层：各平台爬虫 + 注册表 + 事件总线
L2_signal/               加工层：清洗、去重、质量闸门、实体
L3_trend/                趋势层
  ├── thread_feed.py     S3 响应式服务（LLM 按需调）
  ├── forming.py         形成中判定（四档）
  ├── hotspot_filter.py  三层相关性过滤
  ├── event_resolver.py  跨平台事件合并
  ├── event_graph.py     事件图
  ├── game_tags.py       游戏标签库
  ├── kpi_baseline.py    KPI 基线
  ├── strategy.py        Agent 采集策略（V2）
  └── feedback.py        策略效果回溯
L4_intelligence/         情报层：LLM 报告 + 增长创意
L5_memory/               记忆层（保留，不在主链路）
L6_execution/            执行层（保留，运营在外部系统做）
webapp/                  Web 界面：社区报告 / 增长创意 / 数据源
docs/                    架构设计文档
```

---

## 设计文档

- [AI 情报系统架构](docs/AI情报系统架构.md) —— Agent + 工具 + 记忆 + 调度，含 V2 自主控制设计
- [热点检测架构](docs/热点检测架构设计.md) —— LLM + GraphRAG 地基、A-E 五类热点信号

---

## 已知边界

- `topic_count`（社区累计帖数）有缓存延迟 → 只能测小时级增长
- 推荐流有马太效应（热帖反复出现、冷帖被推走）→ 采集样本有偏
- 百度热搜是**搜索意图**，不是讨论热度
- 微博无公开趋势指数 → 用跨轮变化率代替
- 抖音话题 / 贴吧 / 知乎未接入（登录态/风控）
- 免费模型结构化输出有条数上限（8 条/次）

---

## 开发

```bash
python -m unittest discover -s L3_trend/tests -p "test_*.py"
python -m unittest discover -s L4_intelligence/tests -p "test_*.py"
python -m unittest discover -s runtime/tests -p "test_*.py"
# ...共 7 层，323 个测试
```

## 许可证

[MIT License](./LICENSE)
