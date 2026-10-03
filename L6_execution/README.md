# 第六层：Application, Decision & Growth Execution Layer

> **前五层回答"什么在爆、能做什么、过去什么有效"；第六层回答"现在真正做什么、谁来负责、效果怎么样"。**
> 设计文档：[`docs/应用决策执行层设计.md`](../docs/应用决策执行层设计.md)（§1-§47）

它不是 Dashboard，是 **Growth Intelligence Operating System** 的决策与执行界面：
Feed → 工作区 → 决策 → 素材 → 执行 → 实验 → 结果回写第五层 Memory ——
三个闭环（§47：Human Feedback / Growth Experiment / Operational）在这里闭合。

---

## 1. 执行成熟度：Level 2（§21，写死在代码里）

```text
AI 发现 / 分析 / 生成 / 准备素材
        ↓
Human Approve          ← reviewer 角色（agent 永远无权，§43 硬规则）
        ↓
系统执行               ← launch 只有 publisher/admin 能按
```

`EXECUTION_MATURITY_LEVEL = 2` 常量防止"顺手自动化"；Kill Switch（§29：pause/stop/rollback）
同样不交给 Agent。

## 2. 五个核心页面（§35 克制 UI）

| 页面 | 模块 | 干什么 |
|---|---|---|
| Intelligence Feed | `feed.py` | **最值得行动**的事件（§5 ActionPriority 六维乘积只用于排序，维度全部展示）；§6 窗口约束把超 lead_time 的创意类型直接排除 |
| Trend Workspace | `workspace.py` | What happened / 趋势信号 / **Evidence Panel 常驻**（facts/inferences/unknowns 三分透出，§8 推断不当事实卖） |
| Opportunity & Creative | `workspace.py` | §9 机会卡 + §10/§11 约束重生成（资源/上线时间/预算来自 ops_context，不是拍脑袋） |
| Execution & Experiment | `execution_center.py` `experiments.py` | §22 Execution Plan、素材版本化（§20）、实验引擎 |
| Learning Dashboard | `lineage.py` `workbench.py` | §32 lineage + §34 漏斗 + §33 价值看板 |

离线工作台：`python L6_execution/execution/pipeline.py --workbench` →
[`L6_execution/workbench/index.html`](./workbench/index.html)（单文件、零 CDN、**只读镜像**——
写操作走 CLI，权限矩阵在写路径强制）。

## 3. 决策工作流（§12-§14 / §37 / §38）

```text
DRAFT → AI_READY → REVIEWING → APPROVED → EXECUTING → LIVE → COMPLETED
                       ├─ reject / not_relevant / too_late → REJECTED
                       └─ need_more_research → AI_READY
```

- **所有状态变化落 `workflow_event`**（可回放）→ §13 Time-to-Action（中位数，样本不足如实 null）。
- §37 决策动作不止 approve/reject：follow / assign / need_more_research / **too_late**
  （too_late 是给上游的重要信号：发现速度或窗口判断要优化）。
- §38 每个决策自动回写 **L5 Decision Memory**（taxonomy 归一）+ **L4 human_feedback**
  （adoption 统计）——监督信号不能只存在 UI 里。
- §14 Owner / Reviewer / Deadline / Status。

## 4. 实验引擎（§23-§31）

- **没有 control 不叫实验**（§23），**没有 hypothesis 不建实验**（§24——hypothesis 直接取
  L4 creative 的 `growth_hypothesis`，AI Reasoning → Testable Hypothesis → Experiment）。
- 指标三类分开记账（§28）：primary / secondary / **guardrail**（默认 report_rate / hide_rate /
  d1_retention，护栏击穿 → 不许判 WIN）。
- 统计复用 L3 `intel_stats.two_prop_ztest`（stdlib，与全项目零依赖纪律一致）。
- §30 结果五态：**WIN / LOSS / INCONCLUSIVE / STOPPED / INVALID** —— 失败是合法结论，
  "活动取得良好效果"这种总结在本层是语法错误。
- §25 实验结束自动回写 L5 Experiment Memory（含 §15 Experiment Context）→ 可直接 Case 蒸馏。
- §31 归因（A/B / Holdout / DiD / Matching）是增量数字的前置条件——没有归因设计，
  `incremental_outcomes` 如实返回 None。

## 5. 运营约束（§39/§40）

`data/state/ops_context.json`（TTL 默认 24h，过期自动回退 defaults 并标注）：
设计/研发人数、资源位、预算、允许渠道 → 折算成 `max_lead_time_hours`。
闭环：L4 新增只读工具 `get_ops_context` —— **AI 不该在真空里做增长策略**：
无研发时不会推荐"大型互动产品"，会推荐 内容 + UGC模板 + Push。

## 6. 告警（§16/§17）

- P0 立即通知 / P1 进 Feed / P2 只上 Dashboard；同事件同级别同日去重。
- **通知本身可行动**：维度 + 窗口 + 推荐行动（来自窗口×资源约束的真实可选类型）。
- 口径诚实（与 L4 `gate_basis` 同款）：规格阈值（hot>.85 ∧ mom>.9 ∧ rel>.85 ∧ window<12h）
  在无时间分辨率数据上无法完整评估 → 降级口径（relevance+confidence+体量+lifecycle）
  如实标 `basis=degraded`。

## 7. 治理（§43/§44）

- 角色矩阵：viewer / analyst / operator / reviewer / admin / publisher / **agent**——
  `require_role()` 在写路径强制，**agent 拿不到 decide / publish / launch / kill_switch /
  acknowledge_alert**；未登记角色默认拒绝。
- Audit Log：谁/何时/批了/发了什么 + 当时的模型版本 / prompt 版本（§44）。

## 8. 实测（2026-10-03，真实数据）

```text
漏斗（§34，全真实库计数）：
signals 3811 → events 211 → analyzed 2 → opportunities 12 → creatives 10
→ adopted 1 → launched 1 → won 0
Time-to-Action（§13）：43.8h（检测 → 决策 → 上线，回填操作的真实时延）
实验演示：主指标 +61.9%（p=0.011 显著）但护栏 report_rate +150% 击穿 → INCONCLUSIVE（§28）
```

已知限制（不粉饰）：
1. 现有数据无时间分辨率 → 小时级窗口不可得，§6 用 lifecycle+资源 降级口径（已标注）。
2. 真实发布渠道（飞书/推送/社区 API）未接入：素材止于"已发布"状态记录，执行是台账级的。
3. 归因未落地 → 价值看板的 Incremental 指标如实为 None。
4. 告警目前落库不出网（通知渠道待接）；工作台是只读镜像，协作（§15）待接 IM。

## 9. 目录

```
L6_execution/
├── execution/
│   ├── db.py                SQLite schema（8 张表 + lineage 外键链，§32）
│   ├── audit.py             角色权限矩阵 + 审计日志（§43/§44）
│   ├── ops_context.py       运营约束（TTL 保鲜，§39/§40）
│   ├── workflow.py          决策状态机 + 指派 + TTA + 回写 L5/L4（§12-§14/§37/§38）
│   ├── feed.py              ActionPriority + 窗口约束 + Feed 分组（§4-§6/§36）
│   ├── workspace.py         Trend Workspace / Opportunity / Creative Studio（§7-§11）
│   ├── execution_center.py  素材版本化 + Execution Plan + Kill Switch（§18-§22/§29）
│   ├── experiments.py       实验引擎：对照/护栏/z检验/五态/回写（§23-§31）
│   ├── alerts.py            P0/P1/P2 分级 + 可行动通知（§16/§17）
│   ├── lineage.py           lineage 链 + 漏斗 + 价值看板（§32-§34）
│   ├── workbench.py         离线工作台（5 页面，只读镜像，§35）
│   └── pipeline.py          ★ CLI 入口（--feed/--workspace/--decide/--plan/--launch/
│                            --kill/--experiment-create/--finish/--funnel/--value/--workbench …）
├── workbench/index.html     生成产物
└── tests/                   33 个用例（stdlib unittest）
```
