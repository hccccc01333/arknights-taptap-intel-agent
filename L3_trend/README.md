# L3 · Trend Intelligence Layer（热点识别与趋势智能层）

> **边界一句话**：第三层回答「什么正在发生、它有多热、处于什么阶段」；
> 第四层回答「这对 TapTap 意味着什么」。
>
> ★ 核心操作对象是 **Event，不是 keyword，也不是 topic**：
> Topic = 长期主题（黑神话：悟空，可能持续几年）；Event = 短时间发生的具体事情（DLC 疑似泄露）。

```bash
python L3_trend/trend_engine/pipeline.py --run                    # 跑一轮
python L3_trend/trend_engine/pipeline.py --top 15                 # Top N（默认按 Opportunity）
python L3_trend/trend_engine/pipeline.py --top 15 --by hot_score
python L3_trend/trend_engine/pipeline.py --show evt_xxxx          # 单个事件 + 分数历史
python L3_trend/trend_engine/pipeline.py --stats                  # 事件与生命周期分布
```

---

## 1. 架构

```
        L2 Processed Content
                │
                ▼
        Candidate Filter ────── 静态门槛 + 动态条件（成本控制，不是判热点）
                │
                ▼
        Event Clustering ─ 在线匹配（六因子相似度）→ attach / create
                │           离线纠错：Merge（留 merge_history）/ Split 只报警不自动拆
                ▼
        Time-Series ── 5m/15m/1h/6h/24h 多尺度 + 实体级 baseline
                │
   ┌────────────┼─────────────┬────────────┐
   ▼            ▼             ▼            ▼
Velocity   Acceleration    Burst       Novelty
Diffusion  Engagement   Diversity   Credibility
   └────────────┼─────────────┴────────────┘
                ▼
        Scoring：Hot / Momentum / Confidence（★ 三套分必须分开）
                │
                ▼
        Lifecycle：Emerging → Growing → Peaking → Declining → Dormant → Reactivated
                │
        ┌───────┴────────┐
        ▼                ▼
   第四层 AI        第一层反馈
  (high_priority)  (acquisition.priority.command，带 budget 与 TTL)
```

---

## 2. ★ 三条最重要的设计

### ① Topic ≠ Event
系统聚出来的是 Event（有时限的具体事情），不是长期主题。TapTap 话题榜是 Topic，
它不等于"正在发生的事" —— 本项目最大的教训就是**把话题页曝光当成了社区讨论**。

### ② Hot / Momentum / Confidence 必须分开
```
Hot 0.91 + Confidence 0.48 → Emerging Alert（看起来很热，但证据还少）
Hot 0.88 + Confidence 0.94 → Confirmed Trend
```
混成一个分数，就没法表达"证据不足"这件事，第四层会拿着薄证据做重决策。

### ③ 分数只追加，不 UPDATE（§37）
`event_score_history` 保存每一次打分。否则无法回答：
**"系统第一次该报警是什么时候？"** —— 这是评估整个系统价值的唯一办法。

---

## 3. 六因子相似度（§7/§8）—— 不要只靠 Embedding

```
S = 0.45·Semantic + 0.25·Entity + 0.15·Temporal + 0.10·Lexical + 0.05·Source
```

为什么不能只用语义：**"黑神话 DLC 泄露"** 和 **"黑神话年度销量突破"** 都在讲黑神话，
纯语义会把它们错误聚到一起 —— 靠 Entity + Temporal 才能分开。

★ **诚实降级**：本机没有 embedding 模型，`Semantic` 用词汇相似度（字符 n-gram Jaccard）代替，
结果里标 `semantic_source = "lexical_fallback"`。接入模型只需替换 `semantic_fn`，权重与下游不改。
**没有用假向量冒充语义向量** —— 那会让聚类看起来有结果、实际全是噪音。

---

## 4. 生命周期（§31/§32）

| 状态 | 判据 |
|---|---|
| EMERGING | 加速度高、体量小（**最有行动价值的窗口**） |
| GROWING | 速度快 + 加速度为正 + 扩散未减 |
| PEAKING | 体量高 + 加速度≈0 |
| DECLINING | 速度下降 + 加速度为负 |
| DORMANT | 体量与速度都近零 |
| REACTIVATED | 休眠后再次增长 |

★ §33：对增长团队最有价值的是 **Emerging，不是 Peaking**。
等它登上热搜 Top1，创意窗口可能已经关了 —— 所以默认排序用
**Opportunity Score**（对 Emerging 加成 1.35），而不是 Hot Score。

---

## 5. 闭环（§40/§41）

第三层发现事件在加速但置信不足 → 发 `acquisition.priority.command` → 第一层提频 →
更多数据 → Confidence 上升。**用事件，不用 API 调用**，两边解耦。

但不能无限加速：budget **P0 ≤ 10、P1 ≤ 50**，TTL 2 小时，到期自动恢复。

---

## 6. 实测（2026-10-01，全量真实数据）

| 指标 | 数值 |
|---|---|
| 输入内容 | 3811 |
| 候选（Candidate Filter 后） | **1198（31.4%）** |
| 落选原因 | gaming_probability 不足 1525 / 未命中动态条件 1019 / 质量不足 303 / 重复 104 |
| 创建事件 → 合并后 | 263 → **211 活跃事件**（合并 52 个，相似度 0.86–0.93） |
| 生命周期分布 | DECLINING 179 / EMERGING 32 |
| 耗时 | 47 秒 |

**最大的事件**（按成员数）：

| 成员 | 平台 | 生命周期 | Hot | Momentum | Confidence | 标题 |
|---:|---:|---|---:|---:|---:|---|
| 629 | 4 | DECLINING | 0.307 | 0.200 | **0.776** | arknights 发布 |
| 65 | 2 | DECLINING | 0.209 | 0.133 | 0.607 | endfield |
| 43 | 2 | DECLINING | 0.182 | 0.133 | 0.564 | endfield |
| 33 | 2 | DECLINING | 0.238 | 0.133 | 0.531 | 明日方舟 |

### ★ 这组数字里最该看的一件事

**85% 的事件被判为 DECLINING，且 Hot Score 普遍偏低** —— 原因是数据本身：

> 现有数据是一次性采集的**历史快照**（评论跨越数月），没有真实的连续增速。
> Velocity / Acceleration 在这种数据上算出来必然接近零甚至为负，
> 于是生命周期判为 Declining、Hot Score 上不去。

**这不是代码的 bug，是数据前提不成立**。Spec 里的 Hot Score 本就是"增长指标"而非"体量指标"
（velocity + acceleration 占 42%），体量大但不在增长的事件拿不到高分——这是设计如此。

要让它真正工作，需要第一层**连续采集**（每小时/每 15 分钟一次），让 MetricSnapshot
积累出多个时点。届时同一批内容的判定会完全不同。**这个结论对第四层的意义是：
在拿到连续数据之前，不要用现在的 Hot Score 做决策依据，只能看 Confidence 和体量。**

Confidence 是可信的（0.776 / 0.607）：它衡量"证据够不够"，不依赖增速。

---

## 6.5 评估（§4 / §38 / §39）—— `evaluation.py`

`python L3_trend/trend_engine/pipeline.py --eval`

### ★ 先做前置条件体检：这批数据有没有"时间分辨率"

**实测结论：没有。1198 条候选内容的观测时间跨度 = 0.011 小时（40 秒），去重后只有 8 个时刻。**
整批数据是一次性抓完的。因此：

| 结构性失效（inert） | 仍然可用 |
|---|---|
| velocity / acceleration / burst / 生命周期 / 检测延迟 / Lead Time | confidence / 体量 / 平台数 / 扩散静态占比 / engagement / credibility / novelty |

这比"Hot Score 偏低"更进一步：**不是评得不准，是时间维度根本不存在**。
模块会在 `temporal_resolution.status = no_temporal_resolution` 时把上述指标**整体标为不可用**，
而不是输出一片 0 让人误读成"系统反应极快、全程无异常"。

### 检测延迟（§4）两种口径必须分开报

- `first_seen`（first_detected_at − started_at）：**批量重放下结构性恒为 0**，
  检测到后标 `first_seen_degenerate=True` 并给出告警。报"平均延迟 0 小时"是自欺。
- `recognition`（started_at → 第 k 条内容到达，默认 k=3）：语义是"多久聚集到足以称为事件的证据量"，
  不依赖是否实时采集。本数据上也是 0（max 0.01h），与时间分辨率体检的结论一致。

真提前量（Lead Time）必须对照**外部峰值时刻**，见下。

### 聚类评估（§39）

- 无标注的代理指标（现在能算）：单例率 **77.6%**（按事件数）、**83.0% 的内容在多成员事件里**（按内容数）、
  簇内平均相似度 **0.49**、跨平台率 4.9%。
  两个口径差很多：单例事件数量多，但它们只占 17% 的内容。
- 需要人工标注才算：`clustering_metrics()` 给 pairwise P/R/F1、**ARI**、NMI、Purity。
  传 `--cluster-truth` 标注 JSON（`{content_id: 事件标}`）即可算。

### 检测评估（§38）

Precision / Recall / **Lead Time** 依赖外部真值（`{event_key: 爆发时刻}`）。
**没有真值就不给数字** —— 用系统自己的 hot_score 去证明"系统发现得准"是循环论证。
未传 `--detection-truth` 时返回 `no_ground_truth` 并列出需要什么，不产出任何分数。

---

## 7. 已知限制

1. **无 embedding**：相似度是词汇级，改写过的同一事件抓不到。
1.1 **单例率偏高**：77.6% 的事件只有 1 条内容（在无 embedding、词汇级相似度的降级模式下，
    大量内容找不到同事件伙伴）。按内容数看 83% 已进入多成员事件，但**聚类质量仍需人工标注裁定**。
2. **baseline 样本不足**：只有一次采集，`EntityBaseline` 多数实体样本 < 20 → 标 `sufficient=False`，
   置信度自动 ×0.8。相对速度（§16）在真实连续采集下才有意义。
3. **Detection Lead Time 无法计算**：需要外部峰值时间（如微博热搜进入 Top10 的时刻），本机没有。
   字段（`started_at` / `first_detected_at`）已分开存好，函数（`detection_eval`）已实现，
   接了外部基准传 `--detection-truth` 就能算。**现在不给数字，也不估算。**
4. **Split 只报警不自动拆**（§11）：规则乱拆比不拆更糟，MVP 只产出证据（`needs_split`）。
5. **Event 命名走规则**（§13）：无 LLM key，用「主实体 + 关键词 + 事件动词」生成，标 `title_source=rule`。
   规格说这里可以用轻量 LLM —— 接 key 后替换即可。
6. **LLM 不进热点计算主路径**（§44）：命名/摘要/歧义簇判定才用，本机全走规则。

---

## 8. 目录

```
L3_trend/
├── trend_engine/
│   ├── candidate.py     Candidate Filter（§5）
│   ├── similarity.py    六因子相似度（§7/§8）
│   ├── clustering.py    在线匹配 + Merge/Split + Centroid + 命名 + 父子事件（§9-§13, §42）
│   ├── timeseries.py    多尺度窗口 + 实体级 baseline（§14/§15/§17）
│   ├── signals.py       V/A/Burst/Novelty/Diffusion/Engagement/Diversity/Credibility（§16-§27）
│   ├── scoring.py       Hot / Momentum / Confidence / Rank（§28-§35）
│   ├── lifecycle.py     状态机 + Opportunity Window（§31-§33）
│   ├── store.py         Event Store + Score History + Lifecycle History（§4/§36/§37）
│   ├── feedback.py      闭环调频 + 第四层触发门（§40/§41/§46/§47）
│   ├── evaluation.py    时间分辨率体检 + 检测延迟 + 聚类/检测评估（§4/§38/§39）
│   └── pipeline.py      ★ 入口（--run / --eval / --top / --show / --stats）
├── intel_stats.py       统计底座（两比例 z 检验 / Wilson CI；L6 实验引擎复用）
└── tests/
```
