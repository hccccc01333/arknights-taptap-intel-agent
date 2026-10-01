---
name: arknights-daily-intel-agent
description: >-
  Runs the daily intel Agent for Arknights TapTap舆情: perceive (facts locked by
  code) → decide (rule engine fallback + optional LLM) → act (deep_dive/routine
  routing) → brief. Use for 每日情报简报, 流失风险分层, 修辞伪装负向识别,
  agent 架构讲解, or work under L3_trend/.
license: MIT
metadata:
  version: "1.0"
  standard: agentskills.io
  project: arknights-taptap-intel-agent
---

# 每日情报 Agent · 感知→决策→行动→简报

## 目标

把「采集→标注→分析→报告」的手动 pipeline 升级为**每天跑一次的情报 Agent**。
数字全部由代码算好锁进 facts；LLM 只做决策与定性，**不能生成任何数字**——
数字幻觉被架构性排除，而非靠 prompt 恳求。

## 何时用

每日情报简报、流失风险分层、修辞伪装负向（讽刺/高级黑/反串）识别、`L3_trend/` 下任何工作。

## 四步架构

| 步 | 模块 | 职责 | 失败处理 |
|----|------|------|----------|
| 1 感知 | `risk_insight.py` + `anomaly_lite.py` | 代码算 facts | tool 失败 → 因果短句显式降级（traceback 只进本地日志），不崩溃 |
| 2 决策 | `rule_decision` / `llm_decision` | 路由 deep_dive/routine | LLM 无 key/解析失败 → 规则兜底 |
| 3 行动 | `act()` | 组装深挖素材 | 按决策分支，样例全部脱敏 |
| 4 简报 | `render_brief` | Markdown 简报 | 无 key → 模板（数字仍由 facts 填充） |

> 感知层统计（两比例 z 检验 / Wilson CI）在 `intel_stats.py` 纯标准库实现，
> 与 L2_signal/lab口径一致；完整 Kitagawa 三分解仍在分析实验室（需 pandas）。

## 工作流

```bash
# 全链路（无 key：规则决策 + 模板简报）
python L6_delivery/briefing/daily_agent.py

# LLM 决策与定性（可选增强，非依赖）
DEEPSEEK_API_KEY=sk-... python L6_delivery/briefing/daily_agent.py

# 单独跑风险分层（第 2 职责：用户行为洞察）
python L5_generation/risk_insight.py
```

产出：
- `outputs/risk_insight.json` — 风险 facts（供看板/Agent 消费）
- `reports/risk_insight_latest.md` — 分层报告（含干预方向映射）
- `reports/daily_intel_YYYYMMDD.md` — 当日情报简报

## 风险信号口径（诚实边界）

| 信号 | 定义 |
|------|------|
| high_investment_negative | `played_hours_num >= 100` 且 sentiment == 负 |
| high_investment_not_rec  | 同上且 `is_recommend == False` |
| rhetoric_disguised       | rhetoric ∈ {sarcasm, gaoji_hei, fanchuan}：字面/意图极性分离 |
| actionable_negative      | sentiment == 负 且 actionable == 是 |
| 传播维度                 | support_count 全 0 时显式标「不可用」，不编造 |

三条铁律：
1. 这是**舆情侧风险信号**，不是流失预测——无留存/回流数据不做因果归因。
2. 干预建议是方向性规则映射，不承诺效果。
3. 任何整数/百分比必须能在 facts JSON 找回；找不到 → 重试或模板降级。
