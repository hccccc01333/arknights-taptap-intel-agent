# 第五层：Knowledge & Growth Memory Layer

> **前四层负责发现和理解机会，第五层负责记住"过去知道什么、做过什么、什么有效、什么无效"。**
> 设计文档：[`docs/知识增长记忆层设计.md`](../docs/知识增长记忆层设计.md)（§1-§63）

它不是一个普通 RAG，更不是"切 chunk → embedding → 向量库"（§4 明确反对）。
它是 **Intelligence Memory System**：6 类 Memory + 混合检索 + 学习引擎 + 治理管道。

---

## 1. 在整个系统里的位置

```
L3 趋势（什么在爆） → L4 情报（对 TapTap 意味着什么）
                              │
              读：Retrieval Service（§24 统一入口，L4 不许直查库）
                              │
                        ┌─────┴─────┐
                        │  第五层记忆 │
                        └─────┬─────┘
              写：Write Policy 治理管道（§18-§20/§53）
                              │
        L4 人工闸门（adopt/reject/edit）→ 回写 Decision/Creative Memory → Case 蒸馏 → 下次更聪明
```

**读路径与写路径分离（§3）**：写全走 `store.py`（PII 拦截 + 写入策略强制生效）；
读全走 `retrieval.py`（评分/重排/多样性/权限/观测都在这一处，换检索算法不动第四层）。

---

## 2. 六类 Memory + 三个沉淀物

| Memory | 存什么 | 关键纪律 |
|---|---|---|
| Business Knowledge（§5） | TapTap 资产/目标/动机/创意类型 | P2+candidate：公开形态描述 ≠ 内部验证，如实定级 |
| Entity Knowledge（§6） | 游戏/厂商/别名（games/*.json 灌入） | P1+verified（项目维护档案） |
| Trend Memory（§8-§9） | 历史事件全生命周期 + 传播路径 | **生命周期闭合才入长期**（§19）；进行中进短期（§18） |
| Creative Memory（§10-§11） | 第四层全部创意，**连被拒的都存** | agent_generated/P4 起步，人工采纳才 verified |
| Experiment Memory（§12-§15） | 真实实验 + 相对 lift + **Experiment Context** | Creative ≠ Experiment；reliability 单独打分 |
| Decision Memory（§16-§17） | 人工 adopt/reject/edit + 结构化拒因 | taxonomy 归一只对 reject 做 |

沉淀物：**Growth Case**（§29/§60，最有价值的记忆单元）、**Anti-pattern**（§40 失败模式）、
**Playbook**（§41，agent 只能提 candidate，**人工审批才能 approved**，§42）。

---

## 3. 混合检索（§24-§28 / §45 / §46）

```
retrieve(query, memory_type, filters, top_k, ranking_strategy, caller)
  → Query Understanding（实体别名归一 + 目标词面）
  → 候选集（白名单过滤 + §22 时效过滤 + §27 performance filter）
  → 六维评分：0.30 semantic + 0.20 entity + 0.20 metadata
            + 0.10 recency(§37 按 λ 衰减) + 0.10 performance(§35) + 0.10 trust(§39)
  → §46 多样性（同类别每轮最多 2 条，不许"五条捏脸挑战"）
  → §28 Case Package（context/strategy/result/lessons，不是裸 chunk）
  → §52 观测日志（query/candidates/returned/used/decision）
```

**语义后端如实标注**：`backend="lexical_bigram"`（字符 bigram 余弦）。
没有装假向量——L2 的 embedding 库有数据后换 backend 即可，接口与产物不变。

---

## 4. 治理（这一层真正的护城河是"不藏污纳垢"）

- **写入策略（§18-§20）**：`write_route(kind)` 决定长期/短期。LLM 推断 → 只进短期、
  agent_generated，**永不自动转正**；实验结果/人工确认 → 长期 verified。
- **知识污染防护（§53）**：三级知识 verified / candidate / agent_generated；
  升级必须 `--promote --by 某人`（无凭据拒绝）。
- **PII（§55）**：写入前 `scan_pii` 递归扫描，原始 id 一律 `WriteRejected`；
  games 档案里的平台账号 uid 在回填时已剥离（记忆会被检索进 Agent Context，塞进去就收不回）。
- **时效（§22/§23）**：valid_from/valid_to + version——不是 `active=true`；
  知识出新版旧版收口不删除（历史可复现"当时的能力"）。
- **冲突消解（§38/§39）**：最新 + 权威（P0-P4）+ 仍有效者优先。
- **RBAC（§54）**：每类 L4 Agent 有自己的记忆白名单（§44 不做超级 RAG）；未登记 caller 默认拒绝。
- **评估（§50-§52）**：utilization / freshness / grounding_rate / case_reuse / A/B lift——
  数据不够返回 `insufficient_data`，**不返回 0 冒充**。

---

## 5. 实测（2026-10-03，真实数据）

```
python L5_memory/memory/pipeline.py --seed         # games 2 + L4 知识 35 条
python L5_memory/memory/pipeline.py --ingest-l3    # 长期 0 条 / 短期 211 条 / 合并跳过 52 条
python L5_memory/memory/pipeline.py --ingest-l4    # 创意 10 条（agent_generated/P4）
python L5_memory/memory/pipeline.py --retrieve "明日方舟 联动 活动" --top-k 3 --caller relevance_agent
python L5_memory/memory/pipeline.py --distill --event evt_xxx [--force]
python L5_memory/memory/pipeline.py --stats / --evaluate
```

- L4 全部 211 个事件 `ended_at=NULL`、生命周期无一闭合 → **长期 Trend Memory 0 条是正确结果**；
  进行中热点按 §18 进短期记忆（TTL 14 天，可检索，recency 占优）。
- L4 已产出的 10 条创意入 Creative Memory，全部 `pending` + `agent_generated`——
  人工在 L4 或本层 `--feedback` 后才会升级。
- 蒸馏一个真实事件（--force 越界）产出 1 个 Growth Case：lesson 如实写
  "创意停留在 Agent 产出、未进入人工决策——无任何结论可沉淀"，reliability 0.2。

## 6. 已知限制（不粉饰）

1. **实验库为空**：Experiment Memory 结构/可靠性分/performance filter 全就位，
   但还没有任何真实 Campaign 数据——"在历史实验上推理"（§37）暂不可兑现，
   L4 的 `search_experiments` 会如实返回空。
2. **语义检索是词面 bigram**：同义不同形的表述匹配弱（与 L3 话题归一同一限制）。
   升级路径：L2 embedding 库灌数据 → 换 backend，调用方零改动。
3. **无 LLM 蒸馏**：Case Distillation 默认规则版（lesson 必带 evidence id）；
   `distill_event(..., lesson_llm=回调)` 预留升级位，回调失败自动回退。
4. **A/B Evaluation（§51）未运行**：`improvement_lift_ab()` 如实返回 insufficient_data，
   需要同批事件"有/无记忆"两组对照跑过才有结论。
5. **传播路径留空**：L3 数据无平台时序，`diffusion_path` 不编造（§9 的传播先验等有数据再攒）。

---

## 7. 目录

```
L5_memory/
├── memory/
│   ├── db.py            SQLite schema（11 张表）+ 连接（WAL/busy_timeout）
│   ├── governance.py    写入策略 / PII / 时效版本 / 冲突消解 / RBAC / 衰减（§17-§23/§37-§39/§53-§55）
│   ├── store.py         写路径：6 类 Memory + 可靠性分 + 决策回写 + 短期 TTL
│   ├── retrieval.py     读路径：混合检索引擎 + Case Package + 多样性 + 观测（§24-§28/§45/§46/§52）
│   ├── cases.py         学习引擎：Case 蒸馏 / Anti-pattern / Playbook（§29-§31/§40-§42/§48）
│   ├── context.py       Context Builder：按 L4 Agent 分配记忆（§43/§44）
│   ├── evaluation.py    Memory Evaluation：utilization/freshness/grounding/AB（§50-§52）
│   ├── ingest.py        回填：games / L4 知识 / L3 事件 / L4 创意与反馈
│   └── pipeline.py      ★ 入口（--seed/--ingest-l3/--ingest-l4/--retrieve/--context/
│                        --distill/--playbook/--approve-pb/--feedback/--promote/--stats/--evaluate）
└── tests/               75 个用例（stdlib unittest）
```

## 8. 与第四层的接线（2026-10-03）

- `L4_intelligence/intelligence/retrieval.py`（原「待建」§37/§38）：五个标准请求
  （similar_trends / similar_experiments / product_capabilities / user_segments / failure_cases）
  + entity_profile + context_for，全部经 `RetrievalEngine.retrieve()`；L5 库缺失时
  `available()=False`、返回空，**不编造**。
- `tools.py`：`search_experiments` / `get_game_profile` 接真记忆；
  新增只读工具 `search_similar_cases`（Case Memory）。
- `research.py`：检索计划中记忆工具先于外部检索（§43：历史经验免费、结构化、可溯源）。
