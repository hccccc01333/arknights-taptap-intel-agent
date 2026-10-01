---
name: arknights-cross-channel-facts
description: >-
  Builds or synthesizes Arknights cross-channel舆情 with facts-locked numbers
  across TapTap, Bilibili, Douyin, and Weibo. Use for 跨渠道简报, 议题对齐,
  facts锁数, anti-hallucination checks, or work under L2_signal/cross_channel/.
license: MIT
metadata:
  version: "1.0"
  standard: agentskills.io
  project: arknights-taptap-intel-agent
---

# 跨渠道 AI · facts 锁数

## 目标

用**代码算出的 facts** 做多源对照；LLM 只写定性判断。  
任何整数 / `x.x%` 必须能在 `facts.allowed_numbers` 找回，否则重试或模板降级。

## 何时用

跨渠道简报、议题对齐、facts 锁数、防幻觉验收、`L2_signal/cross_channel/`。

## 渠道角色

| 渠道 | 角色 |
|------|------|
| TapTap | 主 KPI |
| B站 / 抖音 / 微博 | 结构对照，**不作全网口碑考核句** |

`data_quality.is_degraded=true`（或 fallback / degraded）必须在叙事中标明「采集降级/演示样本」。

## 工作流

1. `python L2_signal/cross_channel/build_channel_facts.py` → `facts_cross_channel.json`  
2. `python L2_signal/cross_channel/synthesize_cross_channel.py`（无 Key 可用 `--template-only`）  
3. 可选：`python L6_delivery/dashboard/build_dashboard.py`  

锁数协议见 [references/facts-lock-protocol.md](references/facts-lock-protocol.md)。

## Prompt 纪律（给任何模型）

```text
只能使用 facts JSON 中的数字与原话。
跨渠道比例只作结构对照，禁止写成全网 KPI。
降级样本必须标明。
整数与 x.x% 必须属于 allowed_numbers。
校验失败 → 重试一次 → 模板降级（数字仍来自 facts）。
```

## 写作落点

- 摘要四句：【数据洞察】【渠道人设】【议题对齐】【值班建议】  
- 值班建议：角色 × 时机 × 动作，并写清依据渠道  
- 议题 `topic` 必须是 facts 已有 key  

## 禁止

- 跨渠平均写成「全网负向率」  
- 把降级演示样本写成直播监测结果  
- 未生成 facts 时让模型估算数字  

## 输出

`L2_signal/cross_channel/reports/`（综合简报 + 议题对齐）
