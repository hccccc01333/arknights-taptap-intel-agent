---
name: arknights-llm-annotate-v14
description: >-
  Applies and maintains Arknights TapTap review LLM annotation schema v1.4:
  two-stage incongruity/rhetoric then intended sentiment. Use when editing
  annotate prompts, few-shots, rhetoric labels, diagnosing mislabels, or
  working under data/raw/taptap/ or data/annotations/.
license: MIT
metadata:
  version: "1.4"
  standard: agentskills.io
  project: arknights-taptap-intel-agent
---

# 标注 Schema v1.4（修辞两段式）

## 目标

把评价打成可算指标的结构化表：主题、整体情绪、可行动、修辞通道。  
**日报主 KPI 只用 `sentiment`（= intended）；修辞单独复核，不混进主指标。**

## 何时用

改标注 prompt、补 few-shot、修讽刺/高级黑误标、对齐 `annotations_v1_4.csv`、解释 v1.4 口径。

## 权威源（改口径顺序）

1. 改 `data/raw/taptap/annotate_reviews.py` 中的 `SYSTEM_PROMPT` / `FEW_SHOTS`  
2. 同步 `data/raw/taptap/数据处理提示词.md`  
3. 同步本 Skill 与 [references/](references/)  
4. 保留并递增 `prompt_version`；先小样本再扩量  

辅助资料：`data/annotations/讽刺反串高级黑-资料与口径.md`

## 强制两段式

```text
① incongruity_cues → literal_sentiment / rhetoric / rhetoric_confidence
② intended_sentiment → sentiment   （必须 sentiment == intended_sentiment）
禁止跳过①直接打正负
```

枚举与易错口径见 [references/two-stage-rules.md](references/two-stage-rules.md)。  
回归样例见 [references/regression-cases.md](references/regression-cases.md)。

## 工程约束

- API Key / Base URL：环境变量；高推理 + JSON Object  
- 失败行记 `error`，支持断点续跑  
- 运行报告记录模型名与 `prompt_version`  

## 改版检查

- [ ] 新规则有正例与反例  
- [ ] `sentiment == intended_sentiment`  
- [ ] 日报主指标仍用 `sentiment`  
- [ ] 小样本试跑通过后再扩量  

## 输出

`data/annotations/annotations_v1_4.csv`
