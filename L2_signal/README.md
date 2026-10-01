# L2 · 数据加工与语义标准化层（Data Processing & Semantic Normalization）

> **边界一句话**：第二层理解「**一条内容是什么**」，第三层理解「一群内容正在形成什么事件」。
>
> 所以这一层**不做**：是不是热点、是不是同一个事件、TapTap 该不该追、该做什么动作。

```bash
python L2_signal/processing/pipeline.py --run              # 消费 L1 事件总线
python L2_signal/processing/pipeline.py --run --limit 300
python L2_signal/processing/pipeline.py --replay           # 从 Raw Lake 重放（换 processor 版重建）
python L2_signal/processing/pipeline.py --stats            # 数据质量 + 处理状态分布
python L2_signal/processing/pipeline.py --entities         # 实体链接命中统计
python L2_signal/processing/pipeline.py --dlq              # 处理层死信
python L2_signal/processing/pipeline.py --sample 5         # 看几条处理结果
```

---

## 1. 处理链

```
raw.content.created (L1 Event Bus)
        │
        ▼
  Schema Validator ──invalid──▶ processing DLQ（单条坏记录，不杀整批）
        │
        ▼
  Canonical Mapping（各平台字段 → 统一模型；公共指标归一 + 平台特有保留）
        │
        ▼
  Cleaning（HTML/URL/Mention/Hashtag/Emoji/Unicode/空白；★ 原文永存）
        │
        ▼
  Dedup（精确：content_id / 文本哈希；近似：字符 n-gram Jaccard）
        │
        ├── sim ≥ 0.97 → near_duplicate（搬运）
        └── 0.7 ≤ sim < 0.97 → 保留，交给第三层判断是否同事件
        │
        ▼
  Language → Entity（抽取+链接+消歧）→ Classification → Quality/Spam
        │
        ▼
  Embedding（本机未接模型 → None，**不生产假向量**）
        │
        ▼
  Feature Builder（平台内百分位 / 速度 / 质量分）
        │
        ▼
  CanonicalContent → Processed Store → processed.content.created → L3
```

**两遍执行**：第 1 遍只建平台指标基线（百分位需要同平台分布），第 2 遍才完整处理并落库。
**deterministic + versioned + replayable**：所有 processor 都有版本号，换版可 `--replay` 重建。

---

## 2. 能力对照（规格 37 条 → 落地状态）

| 能力 | 本实现 | 状态 |
|---|---|---|
| Schema Validation | `validation.py`：必填/类型/时间/空内容；DLQ 带 error_field | ✅ |
| Data Cleaning | `cleaning.py`：7 个独立 processor | ✅ |
| Normalization | `normalize.py`：时间 / 指标 / URL / 作者 / 标签 | ✅ |
| Deduplication | `dedup.py`：精确 + 近似（双层，阈值 0.97 / 0.70） | ✅ |
| Language Processing | `language.py`：zh/ja/ko/en/ru 规则判定，含简繁标注 | ✅ |
| Content Parsing | hashtag / mention / url 抽取进 `extracted` | ✅ |
| Entity Extraction | `entities.py`：词典 + 游戏档案别名 | ✅ |
| **Entity Linking** | Game Knowledge Registry（`games/*.json` aliases + 内置表） | ✅ |
| Entity Resolution | 歧义词（LOL / 吃鸡）+ 上下文 P(entity\|context) 规则近似 | ✅ |
| Semantic Embedding | 接口 + 版本化存储；**本机无模型 → 不产出向量** | ⚠️ 未接 |
| Content Classification | `classification.py`：domain + 游戏细分 + gaming_probability | ✅ |
| Spam / Noise Filter | `quality.py`：只打分不删除 | ✅ |
| Metric Normalization | 公共 5 项归一 + **平台内百分位** | ✅ |
| Feature Engineering | `features.py`：ContentFeatures（对齐 §23） | ✅ |
| Data Quality | 11 项指标 + ProcessingLag | ✅ |
| Processed Storage | `storage.py`：SQLite（content/entity/content_entity/job/dlq/quality） | ✅ |
| Processing State | RECEIVED→…→READY/FAILED，逐条可追 | ✅ |
| Processor Versioning | `PROCESSOR_VERSIONS` 逐条记录进库 | ✅ |
| Replay | `--replay` 从 Raw Lake 重放，不重爬 | ✅ |
| Streaming + Batch | 流：消费 L1 事件总线；批：replay | ✅ |

### ★ 你点名最值得建设的四项

| 项 | 落地情况 |
|---|---|
| **Entity Linking** | Registry 从 `games/*.json` 读 aliases（明日方舟/方舟/arknights、鸣潮…）+ 内置 9 个常见游戏；mention→entity_id，并写入 `content_entity`（带 mention_text 与 confidence） |
| **跨平台去重** | `text_fingerprint` = 归一化文本 + 规范 URL；近似去重输出 `dup_type`（exact / near / event_candidate） |
| **Metric Normalization** | 平台内百分位（view/like/comment_percentile）+ 平台特有指标原样保留（`platform_metrics`） |
| **Embedding Versioning** | `content_embedding` 主键 = (content_id, model_name, model_version)，换模型**并存不覆盖**；注册表记录 status |

---

## 3. ★ 三条硬规则

1. **不删除原始文本**：`raw_text` 与 `normalized_text` 两个版本都存。
   清洗只产出新字段 —— 本项目在素材层吃过"原文丢失无法溯源"的亏。
2. **不生产假向量**：本机没有 embedding 模型，`embedding_ref = None`。
   用哈希/随机向量冒充语义向量，会让第三层聚类"看起来有结果、实际全是噪音"，
   这是最坏的一种数字污染。
3. **不判断热点**：velocity / percentile 都算，但**不产生**"这是热点"的结论。

---

## 4. 实测（2026-10-01，全量 3825 条真实数据）

| 指标 | 数值 |
|---|---|
| 输入 / 处理成功 | **3825 / 3825** |
| Schema 失败 | 0（第一层已校验过一次） |
| 识别为重复（exact + near） | **104**（2.72%） |
| 有实体命中 | 1882（49.2%） |
| 游戏内容（p≥0.5） | **2298**（60.1%） |
| **游戏内容实体覆盖率** | **74.85%**（SLO 要求 >85%，**未达标**） |
| spam 率（score≥0.5） | 0.08% |
| 吞吐 | 20.5 条/秒（全量 187 秒） |

对照 SLO（规格 §32）：

| SLO | 目标 | 本机 | 差距原因 |
|---|---|---|---|
| Schema valid | >99.5% | **100%** | ✅ |
| Entity linking coverage（游戏内容） | >85% | **74.85%** ❌ | 词典只覆盖 `games/*.json` + 9 个内置游戏；TapTap 话题里大量游戏名不在词典内 |
| Duplicate precision | >95% | 词汇级（0.97） | 无语义向量，改写型搬运抓不到 |
| Embedding success | >99% | **0%** ❌ | 未接模型，如实标注 |
| Replay capability | 100% | ✅ | `--replay` 可用 |
| Data lineage | 100% | ✅ | 逐条带 raw_ref + processing_version |

**实体覆盖率未达标怎么补**：往 `games/<key>.json` 的 `aliases` 里加别名，或往
`entities.py::EXTRA_GAMES` 加游戏。这是纯数据工作，不需要改代码 —— 也是 Registry 设计的用意。

---

## 5. 生产替换点

| 规格 | 生产 | 本机 | 替换方式 |
|---|---|---|---|
| Canonical Store | PostgreSQL | SQLite | 换 `ProcessedStore` 连接 |
| Vector Store | pgvector / Qdrant | SQLite（独立库，模型未接） | 换 `EmbeddingEngine` |
| Batch Processing | Polars | 标准库 | 数据量到百万级再换 |
| Workflow | Dagster | CLI 手动 | — |
| Metric 时序库 | ClickHouse | 复用 L1 SQLite | 千万/亿级再考虑 |

---

## 6. 已知限制

1. **没有 embedding**：`embedding_ref` 全为 null，第三层只能用词汇相似度聚类。
2. **近似去重是词汇级**：字符 3-gram Jaccard。改写过的搬运（同义替换）抓不到，需要语义向量。
3. **去重用了滑动窗口（最近 500 条）**：两两比较是 O(n²)，3825 条单机跑不动。
   搬运通常时间邻近，召回损失小；生产版换 ANN 向量库后无此限制。
4. **实体词典有限**：只覆盖 `games/*.json` + 9 个内置游戏。新游戏要往 `EXTRA_GAMES` 或游戏档案里加别名。
5. **LLM 兜底未接**：Entity Linking confidence < 0.5 时按规格应交给 LLM，本机返回 unresolved（不瞎猜）。

---

## 7. 目录

```
L2_signal/
├── processing/              ★ 第二层核心（对齐规格 §35）
│   ├── canonical.py         CanonicalContent + ContentFeatures + 指纹
│   ├── validation.py        Schema Validator + DLQ 记录
│   ├── cleaning.py          HTML/URL/Mention/Hashtag/Emoji/Unicode/空白
│   ├── normalize.py         时间 / 指标（公共+平台特有）/ URL / 作者 / 标签
│   ├── dedup.py             精确 + 近似 + 跨平台指纹
│   ├── language.py          语种判定（规则）
│   ├── entities.py          Registry + 抽取 + 链接 + 消歧
│   ├── classification.py    domain + 游戏细分 + gaming_probability
│   ├── quality.py           quality_score / spam_score（只打分不删）
│   ├── embedding.py         接口 + 版本化存储（模型未接）
│   ├── features.py          百分位 / 速度 / ContentFeatures
│   ├── storage.py           Processed Store + Processing State + 质量指标
│   └── pipeline.py          ★ 入口（处理链 + replay + CLI）
├── features.py              既有：平台特征计算
├── preprocess_reviews.py    既有：评论清洗切片
├── cross_channel/           既有：跨渠道 facts
├── lab/                     既有：分析实验室
└── tests/
```
