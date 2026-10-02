# 第四层：AI Intelligence & Growth Reasoning Layer

> **第三层负责发现机会，第四层负责理解和设计机会。**
> LLM 不决定"什么是热点"，只负责把已可靠识别的热点转成**可验证的增长假设**。

边界：第三层回答"什么正在发生、多热、什么阶段"；第四层回答"这对 TapTap 意味着什么、能做什么"。
**不重新计算** Hot Score / Velocity / Acceleration / 聚类 / 指标归一（仍属第三层）。

---

## 1. 架构（§51）

```
START → Evidence Builder → Trend Analyst → Relevance ──┬─ LOW(<.40) → ARCHIVE
                                                       ├─ 中(.40~.65) → light_analysis
                                                       └─ HIGH(≥.65) → Evidence Gate
                                                              ├─ enough → Audience
                                                              └─ 不足 → Research → Audience
Audience → Opportunity → Strategist → Creative → Evaluator ─┬─ pass(≥.75) → Risk
                                    ↑                       └─ revise → (回到 Evaluator，上限 2 次)
                                    └──────────────────────────────┘
Risk → Human Review → [approve / edit / reject / need_research] → 第五层 Memory
```

7 个核心节点 + 3 个 Gate + 1 个有界修订环 + 1 个人工闸门。
**Workflow first, Agent second**：主干是确定性流程，只有 Research 允许自主探索。

---

## 2. State（§7）—— 原文不进 State

`IntelligenceState` 只放：引用 / 摘要 / 结构化结果 / 状态。
原始文本永远从 `upstream.read_full_text(content_id)` 按 id 取，**在节点内部用完即弃**。

`state.assert_state_clean()` 是**体积闸门**（字段名像原文 / 单字段 >300 字符），
真正把文本挡在外面的是：Evidence 只写 ≤120 字的 `excerpt` + `content_id`。

---

## 3. 三个 Gate

| Gate | 判据 | 分支 |
|---|---|---|
| Relevance（§14） | 五维加权分 | `<.40 archive` / `<.65 light_analysis` / `≥.65 opportunity` |
| Evidence（§15） | 证据条数、confidence、有无 PRIMARY、未解问题数 | enough → Audience；不足 → **Research** |
| Quality（§31） | creative_score | `<.75 revise`；**有界，MAX_ITERATION=2** |

★ Hot Score 高 ≠ Relevance 高（社会热点可以 Hot .99 / Relevance .12），低相关直接归档，不浪费 Agent Token。

---

## 4. 关键实现点

- **Evidence 事实层级**（§9）：`PRIMARY > SECONDARY > COMMUNITY > INFERRED`，防止把玩家猜测写成官方事实。
- **facts / inferences / unknowns 三分**（§11）：每个节点输出都带，不许把推断写成事实。
- **TapTap 资产知识层**（§13）：资产 / 增长目标 / 创意类型 / 用户动机全是**数据**，不写死进 prompt；产品能力变了只改 `knowledge.py`。
- **Relevance 五维**（§12）：`0.30U + 0.25C + 0.20A + 0.15G + 0.10T`。
- **Opportunity 评分**（§24）：`0.25R + 0.20M + 0.20F + 0.15W + 0.10D + 0.10E`；**没有 growth_mechanism 不进创意阶段**（§22）。
- **Creative Rubric**（§29）：`0.20R + 0.15U + 0.15T + 0.15G + 0.15F + 0.10N + 0.10D`；风险**单独打**，不混进 creative_score（§32）。
- **Fact Check**（§33）：claim → evidence lookup → `SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED / CONFLICTING`。
- **溯源**（项目纪律 ①）：每条创意必带 `event_id + opportunity_id + evidence_ids + facts 数字`，数字可在 Evidence Pack 原样找回。
- **版本化**（§43/§44）：prompt_version / model_version / knowledge_version 进 State；同一 Event 多次分析**不覆盖**，各存一个 `analysis_id`。
- **缓存**（§46）：cache key = `event_id + input_hash`，只对实质变化（lifecycle/体量/分数）重跑。
- **人工反馈**（§35）：adopt / reject / edit + reason 全部落库，作为第五层学习数据。

---

## 5. 实测（2026-10-02，真实数据）

```
python L4_intelligence/intelligence/pipeline.py --run --limit 5
python L4_intelligence/intelligence/pipeline.py --event evt_xxx --package
python L4_intelligence/intelligence/pipeline.py --stats
```

| 项 | 实测 |
|---|---|
| 引擎 | `stdlib_fallback`（langgraph 未安装，见下） |
| Event Gate 依据 | `confidence_volume(degraded: 无时间分辨率)` |
| 5 个事件 | → 18 条创意，14 条过 Rubric（≥0.75） |
| 修订环 | 触发并跑到上限 2 次后停止（trace 可见） |
| 证据层级 | **PRIMARY 0 / SECONDARY 少量 / COMMUNITY 为主** |
| Fact Check | `PARTIALLY_SUPPORTED`（无官方信源，不许判 SUPPORTED） |
| 风险 | 多数标 high（无官方信源）+ medium（生命周期 DECLINING / 检索未全完成） |
| 缓存 | 第二次运行 5/5 命中 |

### ★ 三条实测教训（都改了代码）

1. **"提到官方" ≠ "官方发布"**：3811 条内容里 `is_official=True` 只有 30 条，
   但含"官方/官宣"关键词的有 108 条，几乎全是玩家/媒体**转述**。
   最初把关键词命中判成 PRIMARY，结果 Evidence Pack 的 `trigger` 变成了一条玩家提问。
   → **PRIMARY 只认 `is_official` 结构字段**，关键词命中降为 SECONDARY。

2. **照抄规格的 T0 门槛会把整层掐死**：规格写 `hot < .40` 不调 LLM，
   但本项目现有数据上 hot_score 是**结构性失效**的（第三层已证明：数据无时间分辨率）→
   **211 个事件全部被 T0 挡掉，产出 0 条**。
   → 改为：有时间分辨率时用规格口径；没有时用 `confidence + 体量`（第三层结论：这两个当前可信），
   并在产物里标 `gate_basis`，**不掩盖用了哪种口径**。

3. **继承守卫的假阳性**：`RAW_TEXT_MARKS` 含 `"content"`，子串匹配把 `content_id` 也拦了。
   但 `content_id` 正是"引用而非搬运"的载体 —— 回传 id 才能溯源，回传原文才失控。
   → `*_id` 放行（TODO：这条应上移到 `runtime/task_contracts.py` 统一口径）。

---

## 6. 已知限制（不粉饰）

1. **langgraph 未安装** → 走纯标准库执行器 `run_stdlib()`。
   它与真 LangGraph 版本**共用同一批节点函数与路由函数**（`graph/routing.py`），
   所以业务规则只有一套；产物 `engine` 字段如实标 `stdlib_fallback`，**不假装跑过图**。
   装了 langgraph 后 `build_langgraph()` 直接可用（条件边 / 有界环 / `interrupt()` 人工闸门）。
2. **无 LLM key** → 所有节点 `mode="rule"`，`llm_used=False`。
   prompt 已版本化写好（`prompts.py`），接 key 后节点代码不用改；
   规则实现与 LLM 共用同一套输出契约，避免接模型时结构漂移。
3. **Research 外部检索不可用** → `search_web` / `search_social` 抛 `ToolUnavailable`，
   Research 结果标 `status=incomplete` 并把缺口传给 Risk，**不返回假检索结果**。
4. **历史实验库为空** → `search_experiments` 返回空列表。§37 的"在 TapTap 历史实验数据上推理"
   目前无法兑现，创意仍是规则组合而非历史案例检索。
5. **Evidence 全部 COMMUNITY** → 事实风险恒为 high，创意不能对外用断言式措辞。

---

## 7. 目录

```
L4_intelligence/
├── intelligence/
│   ├── knowledge.py     TapTap 资产 / 增长目标 / 创意类型 / 用户动机（§13/§19/§23/§26）
│   ├── state.py         IntelligenceState + State 守卫（§7）
│   ├── llm.py           模型路由 + 无 key 显式降级（§39）
│   ├── prompts.py       版本化 prompt（§43）
│   ├── tools.py         只读工具 + text_access 声明（§17）
│   ├── upstream.py      L3 事件 / L2 内容只读访问（原文不进 State）
│   ├── store.py         产物版本化 + 人工反馈 + 缓存 + 分级闸门（§35/§44/§45/§46）
│   ├── retrieval.py     （待建）混合检索：历史案例（§37/§38）
│   ├── nodes/
│   │   ├── evidence.py      Evidence Builder + 事实层级（§8/§9）
│   │   ├── analysis.py      Trend Analyst / Relevance / Audience（§10-§19）
│   │   ├── research.py      Evidence Gate + 有界研究（§15-§17）
│   │   ├── opportunity.py   Opportunity + Strategist（§20-§25）
│   │   ├── creative.py      Creative Generator（§26-§28）
│   │   └── evaluation.py    Evaluator + Risk/FactCheck（§29-§33）
│   ├── graph/
│   │   ├── graph.py     LangGraph 图 + 标准库等价执行器（§51）
│   │   └── routing.py   三个 Gate 的判定（与引擎无关）
│   └── pipeline.py      ★ 入口（--run / --event --package / --stats / --feedback / --history）
└── tests/
```
