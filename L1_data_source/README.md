# L1 · 信号采集层（Data Source Layer）

> **这一层只回答一个问题：全网发生了什么。**
> 它是 Data Engineering，**不放 Agent**：不做判断、不打可发酵度分、不做语义理解。
> 判断在 L4，生成在 L5。这一层做多了，后面每一层都得替它擦屁股。

按用户给的 L1 完整版架构（22 条）重建。入口从一次性跑批 `normalize.py`
升级为**采集系统** `pipeline.py`。

```bash
python L1_data_source/pipeline.py --seed      # 初始化数据源注册表
python L1_data_source/pipeline.py --plan      # 采集频率计划（谁该采了）
python L1_data_source/pipeline.py --once --all# 跑一轮
python L1_data_source/pipeline.py --health    # 数据源健康
python L1_data_source/pipeline.py --stats     # 事件总线 / 快照统计
python L1_data_source/pipeline.py --velocity tap_hot_hashtags   # 速度 / 加速度
python L1_data_source/pipeline.py --priority tap_topic_feed --level high --ttl 7200
```

---

## 1. 数据流与控制流

```
                       Internet（Social / Content / Community / Search / News / Official）
                                          │
                              ┌───────────▼────────────┐
   Control Plane ────────────▶│     Source Registry     │  数据源注册中心（Platform ≠ Source）
   Source Registry            └───────────┬────────────┘
   Scheduler / Planner                    ▼
   Rate Limit                  ┌────────────────────────┐
   Credentials                 │      Crawl Planner     │  频率 = f(优先级,变化率,健康,反馈,成本)
   Parser Version              └───────────┬────────────┘
   Feature Flags                           ▼
                                ┌────────────────────────┐
                                │    Connector Runtime   │  API │ RSS │ Web │ Webhook │ Internal
                                └───────────┬────────────┘
                                            ▼
                                ┌────────────────────────┐
                                │    Protocol Adapter    │  协议标准化（parser_version）
                                └───────┬────────┬───────┘
                                        ▼        ▼
                          ┌──────────────┐   ┌────────────────┐
                          │Content Event │   │ MetricSnapshot │  ★ 时间 × 指标
                          └──────┬───────┘   └────────┬───────┘
                                 └──────────┬─────────┘
                                            ▼
                                     ┌────────────┐
                                     │ Event Bus  │  raw.content.* / raw.metric.*
                                     └─────┬──────┘
                       ┌───────────────────┼───────────────────┐
                       ▼                   ▼                   ▼
                  Raw Lake           Metadata DB          Monitoring
                （不可变原始）      （索引/游标/健康）     （health/freshness）
                                            │
                                            └──────────▶ L2 及之后
```

**数据流与控制流分开**：Source Registry / Scheduler / Rate Limit 属于 Control Plane，
不参与数据搬运。第三层要加速采集，发的是**事件**（`acquisition.priority.command`），
不是 RPC 调用 —— 任一方挂掉，另一方照常跑。

---

## 2. 能力对照（规格 22 条 → 落地状态）

| # | 能力 | 本实现 | 状态 |
|---|---|---|---|
| 一 | Source Management | `registry/source_registry.py`，14 个 Source，SQLite 存储 | ✅ |
| 一 | Acquisition | `connectors/`：internal 可用；api/rss/web/webhook 接口已钉死 | ⚠️ 部分 |
| 一 | Scheduling | `planner/crawl_planner.py`：频率算出来，不是 cron | ✅ |
| 一 | Rate Control | 按注册表 `rate_limit_per_minute` / `max_concurrency` / `timeout_seconds` | ✅ |
| 一 | Raw Protocol | `schema/content_event.py` RawContentEvent **v1.0** | ✅ |
| 一 | Snapshot | `MetricSnapshot` + `analytics.py`（velocity / acceleration） | ✅ |
| 一 | Raw Storage | `storage/raw_lake.py`：按 `year/month/day/platform/source` 分区，不可变 | ✅ |
| 一 | Event Delivery | `bus/event_bus.py`：9 个 topic，第二层只订阅 `raw.content.*` `raw.metric.*` | ✅ |
| 一 | Data Lineage | 每条带 `source_id / request_id / crawl_run_id / raw_ref / parser_version / schema_version` | ✅ |
| 一 | Source Health | `source_health` 表：成功率/延迟/条数/新鲜度/失败数/熔断 | ✅ |
| 一 | Failure Recovery | 失败分类 + 指数退避 + DLQ 可 replay + checkpoint 续采 | ✅ |
| 一 | Governance | `compliance_config`：allowed / retention_days / contains_pii / raw_content_storage | ✅ |
| 四 | **Platform ≠ Source** | 微博 2 个、TapTap 5 个 Source，各自独立频率与成本 | ✅ |
| 五 | Signal Type 分类 | 8 类（search/social/content/community/news/official/market/internal） | ✅ |
| 六 | Connector 统一接口 | `SourceConnector.fetch(cursor, since)` → `FetchResult` | ✅ |
| 七 | 三时间戳分开 | `published_at` / `observed_at` / `crawled_at` | ✅ |
| 八 | Snapshot 独立 | 只追加不 UPDATE | ✅ |
| 九 | 原始数据不覆盖 | Raw Lake 每次请求一个文件，含 parser_version，可换 parser 重放 | ✅ |
| 十 | Metadata 与 Raw 分离 | SQLite 只存索引与 `raw_ref` 指针 | ✅ |
| 十一 | 幂等 | `content_key = platform:external_id`；`snapshot_id = hash(source+ext+observed_at)` | ✅ |
| 十二 | Closed-loop Adaptive | `--priority` 发事件 → 频率 300s→60s（实测） | ✅ |
| 十三 | 不与第三层强耦合 | 只通过事件总线，无直接 API 调用 | ✅ |
| 十四 | Topic 设计 | 9 个 topic 与规格一致 | ✅ |
| 十五 | Checkpoint / Cursor | `source_checkpoint` 表；崩了从游标续 | ✅ |
| 十六 | Failure Model | 7 类失败分别处理（详见下表） | ✅ |
| 十七 | Source Health 一等公民 | 独立表 + `source.health` 事件 | ✅ |
| 十八 | Freshness SLO | 每 Source 配 `freshness_slo_seconds` + lag 计算 | ✅ |
| 二十 | Schema Versioning | `schema_version` 必填，不匹配即报警 | ✅ |
| 二十二 | 合规优先 | 官方 API/授权 → RSS/公共 Feed → 公开网页；PII 默认关闭 | ✅ |

### 失败分类与处理

| 错误类型 | 处理 |
|---|---|
| Timeout | 重试（最多 3 次） |
| 5xx | 指数退避 |
| Rate Limit | 延长间隔（**不是**更凶地重试）+ 计数 |
| Authentication | 告警，不盲目 retry |
| Parser Error / Invalid Record | 进 DLQ（可 replay） |
| Schema Drift | 发 `source.schema.error` |
| Source Down | 连续 5 次失败 → 熔断（circuit open） |

---

## 3. 实测结果（2026-10-01，真实数据）

**首次全量采集**：9 个启用 Source，3812 条记录 → **3811 条 Content Event + 3811 条 Snapshot**，
1 条空正文进 DLQ（`tap_moment_comments`，正文为空确实无法分析）。

| Source | 记录 | 事件 | 快照 | 说明 |
|---|---|---|---|---|
| tap_hot_hashtags | 14 | 14 | 14 | 话题热榜 |
| tap_topic_feed | 150 | 150 | 150 | 话题下帖子 |
| tap_moment_comments | 110 | 109 | 109 | 1 条空正文丢弃 |
| tap_official | 30 | 30 | 30 | 官方公告（official 信号） |
| tap_reviews | 3000 | 3000 | 3000 | 游戏评论 |
| bili_search / bili_comments | 40 / 415 | 40 / 415 | 同 | content 信号 |
| douyin_search | 10 | 10 | 10 | content 信号 |
| weibo_keyword | 43 | 43 | 43 | social 信号 |

**幂等验证**：`--reset-cursor` 后重采 `tap_hot_hashtags` → 14 条**全部识别为已存在**（dup=14），
快照变成 28 条（同一内容 2 个时点），`Content` 没有重复创建。

**速度计算**：两个时点 → `velocity = 0.0/min`（本地文件指标未变，不是 bug）。
加速度需要 ≥3 个时点 → 当前显示 `None`。

**闭环调频**：发 `high` 指令后，`tap_topic_feed` 间隔 300s → **60s**（触到 min 下限）。

---

## 4. ★ 生产替换点（本机等价物 ≠ 生产版）

| 规格 | 生产 | 本机等价 | 替换方式 |
|---|---|---|---|
| Event Bus | Kafka / Redpanda | append-only JSONL（按 topic 分目录） | 换 `EventBus.publish/consume` |
| Raw Lake | S3 / OSS | 本地分区目录 | 换 `RawLake.write_request` |
| Metadata DB | PostgreSQL | SQLite（stdlib） | 换 `MetadataStore` 连接 |
| Monitoring | Prometheus | `source_health` 表 + CLI | 接 exporter |

topic 名、表结构、字段语义与规格一致，替换时不改上层。

---

## 5. 已知限制（不粉饰）

1. **api / rss / web / webhook 四类 Connector 只有接口骨架**。
   本机没有外网采集凭据，也不该在无授权下写爬虫（§22：技术上能抓 ≠ 应该抓）。
   真正跑得起来的是 `internal`（读已落盘数据）。`weibo_hot_search` 等 5 个 Source 登记但 `enabled=0`。
2. **velocity 目前恒为 0**：数据源是静态 CSV，两次采集之间指标不会变。
   机制已通（快照累积 + 差分），需要真实连续采集才有意义。
3. **分页靠 `batch_size`**：默认 500 条/轮，大源（如 reviews）需多轮游标续采（已在配置里调为 3000）。
4. **原始数据不入库**：`data/raw_lake` 与 `data/event_bus` 含明文作者名与原始响应，已加入 `.gitignore`。
5. 旧的一次性跑批 `normalize.py` 保留可用（输出 `data/events/`），但**新功能请走 `pipeline.py`**。

---

## 6. 目录

```
L1_data_source/
├── schema/content_event.py   RawContentEvent v1.0 + MetricSnapshot + 校验（PII/时间/lineage）
├── registry/source_registry.py  ★ 数据源注册中心（Platform ≠ Source，含 signal_type 与合规配置）
├── connectors/
│   ├── base.py               SourceConnector / FetchResult / 失败分类
│   ├── internal.py           读本地已采集数据（本机唯一可用）
│   ├── api_web.py            api / rss / web / webhook 骨架
│   └── runtime.py            ★ 运行时：限流/超时/重试退避/熔断/DLQ/checkpoint/幂等
├── planner/crawl_planner.py  ★ 频率计算 + 闭环调频（事件驱动）
├── storage/
│   ├── raw_lake.py           不可变原始数据（分区，可换 parser 重放）
│   └── metadata_db.py        SQLite：registry / checkpoint / health / run / snapshot / dlq
├── bus/event_bus.py          9 个 topic，第二层的唯一入口
├── analytics.py              velocity / acceleration / freshness
├── adapters/                 协议标准化（各平台字段映射 + parser_version）
├── collectors/               采集脚本（旧 01爬虫 / 06~08 对照迁入）
├── normalize.py              旧跑批（保留，新功能走 pipeline）
└── pipeline.py               ★ 入口
```
