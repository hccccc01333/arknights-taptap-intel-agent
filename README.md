# 明日方舟 · TapTap Growth Intelligence OS

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

一套从互联网趋势发现，到 TapTap 增长机会判断、创意生产、人工决策、实验验证，再到经验学习的
**Growth Intelligence Operating System**（2026-10 重构定稿；前身为舆情日/周报分析平台，
旧存量已随分层重排移除，git 历史 `a358a06..561347c` 可回溯）。

六个动词贯穿六层：**Capture → Understand → Detect → Reason → Learn → Act**。

| 项 | 内容 |
|----|------|
| 主链 | TapTap《明日方舟》评价与社区（app_id=70253）+ B站/抖音/微博对照采集，四通道分目录 |
| 隐私 | 用户标识加盐哈希（HMAC-SHA256，盐不入库）；分析与产出只出聚合 |
| facts 锁数 | 数字由代码计算，LLM 不生成任何数字；无依赖/无 Key 时显式降级并标注口径，不假装 |
| 游戏档案 | [`games/<key>.json`](./games/README.md) 驱动 app_id / 阈值 / 等价名——换档案即换游戏 |
| 质量 | 600+ 个 stdlib 单元测试（各层 tests/），零第三方依赖可跑全部核心链 |

**快速入口**：[六层总览](#六层总览) · [运行方式](#运行方式) ·
[L5 记忆层](./L5_memory/README.md) · [L6 执行层](./L6_execution/README.md)

---

## 六层总览

```
① L1_data_source   采集        哪里出现了新信号？（Source Registry / Planner / Connector / Event Bus）
② L2_signal        加工        每条信号是什么意思？（Canonical Model：清洗/去重/实体/分类/质量）
③ L3_trend         趋势        什么事件正在爆？（聚类 + 八信号 + Hot/Momentum/Confidence + 生命周期）
   L3_semantic     语义        公告结构化（L1 internal connector 消费）
④ L4_intelligence  情报        对 TapTap 意味着什么？（LangGraph 推理图：Evidence→Relevance→
                               Opportunity→Creative→Evaluator→Risk + 人工闸门）
⑤ L5_memory        记忆        过去什么有效？（6 类 Memory + 混合检索 + Case 蒸馏 + 治理管道）
⑥ L6_execution     执行        现在做什么、效果如何？（Feed/工作流/素材/实验/告警/漏斗 + RBAC 审计）
   runtime         控制面      时钟驱动的任务链（契约 + harness + LangGraph 巡检图 + 调度器）
```

各层设计文档：[`docs/`](./docs/)（Agent-v2 架构、任务契约、第五层/第六层设计等）。

## 运行方式

```bash
# 全链手动跑一遍（无 LLM key 时规则兜底，产物如实标注）
python L1_data_source/pipeline.py --once                       # L1 采集到期数据源
python L2_signal/processing/pipeline.py --run                  # L2 事件加工与标准化
python L3_trend/trend_engine/pipeline.py --run                 # L3 事件聚类/评分/生命周期
python L4_intelligence/intelligence/pipeline.py --run --limit 5  # L4 情报推理
python L5_memory/memory/pipeline.py --ingest-l3 --ingest-l4    # L5 记忆回填（幂等）
python L6_execution/execution/pipeline.py --feed               # L6 Intelligence Feed
python L6_execution/execution/pipeline.py --workbench          # 离线工作台（5 页面，双击即开）

# 决策与执行（第六层，全部带角色权限矩阵）
python L6_execution/execution/pipeline.py --decide creative idea_x approve --actor 评审 --role reviewer
python L6_execution/execution/pipeline.py --plan idea_x --channels community_feed --experiment
python L6_execution/execution/pipeline.py --funnel             # 转化漏斗（全真实计数）
python L6_execution/execution/pipeline.py --value              # Time-to-Action / 采用率 / 实验胜率

# 自动化：时钟驱动的六层主链（探测 15min + 下游幂等跟随）
python runtime/scheduler.py --once --dry-run
python runtime/scheduler.py --status

# 记忆检索 / 记忆评估
python L5_memory/memory/pipeline.py --retrieve "明日方舟 联动 UGC" --top-k 5
python L5_memory/memory/pipeline.py --evaluate
```

## 测试

```bash
python -m unittest discover -s L3_trend/tests -p "test_*.py"
python -m unittest discover -s L4_intelligence/tests -p "test_*.py"
python -m unittest discover -s L5_memory/tests -p "test_*.py"
python -m unittest discover -s L6_execution/tests -p "test_*.py"
python -m unittest discover -s runtime/tests -p "test_*.py"
```

## 目录结构

```text
L1_data_source/   采集系统（registry/planner/connectors/adapters/bus/storage；四通道爬虫）
L2_signal/        数据加工与语义标准化（processing/：Canonical Model 9 子系统）
L3_semantic/      公告结构化（L1 internal connector 消费）
L3_trend/         趋势智能（trend_engine/ + intel_stats 统计底座）
L4_intelligence/  AI 情报与增长推理（LangGraph 编排；含 L5 记忆检索适配器）
L5_memory/        知识与增长记忆（6 类 Memory + 混合检索 + Case 蒸馏 + 治理）
L6_execution/     应用、决策与增长执行（Feed/工作流/素材/实验/告警/漏斗 + RBAC 审计）
runtime/          控制面（任务契约 + harness + LangGraph 巡检图 + 调度器）
games/            游戏档案（参数化入口：换档案即换游戏）
common/, paths.py 跨层工具与唯一路径出口
docs/             架构与各层设计文档
data/             raw / events / state（运行时库不入 git）
```

## 边界说明（不粉饰）

- 主数据面是 TapTap 切片；B站/抖音/微博为对照采集，不当作全网 KPI
- 当前 L3 历史数据无时间分辨率 → 小时级窗口/速度信号降级口径（各产物如实标 `basis`）
- 风险分层与实验结论是舆情侧信号与 A/B 结果，不做因果归因（§31 归因未落地前
  价值看板 Incremental 指标如实为 None）
- 话题状态阈值、调度频率等未校准参数在契约/报告里逐处标注

## 路线图

- [x] 六层分层架构落地（L1 采集 → L6 执行 + runtime 控制面）
- [x] L4 LangGraph 推理图（7 节点 + 3 Gate + 有界修订环 + 人工闸门）+ 真 LLM 接入
- [x] L5 知识与增长记忆（6 类 Memory + 混合检索 + Case 蒸馏 + 写入策略/PII/RBAC）
- [x] L6 应用决策执行（Feed + 决策状态机 + 素材版本化 + 实验引擎五态结果 + 告警 + 漏斗）
- [x] 控制面重接线：调度器任务契约 = 六层主链（旧探测链移除）
- [ ] 连续采集运行数周 → 校准频率/阈值，验证检测提前量与 Time-to-Action
- [ ] L6 通知渠道出网（飞书/Slack/企微）与真实发布渠道接入
- [ ] L6 §31 归因落地（A/B / Holdout / DiD / Matching）→ Incremental 指标
- [ ] L2 processing 子系统单元测试补齐

## 许可证

本项目采用 [MIT License](./LICENSE)。
