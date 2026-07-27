---
name: arknights-taptap-yuqing-report
description: >-
  Produces decision-ready TapTap Arknights daily/weekly舆情 reports from
  annotations_v1_4 (executive summary 四句, 四问法, thin-sample and contrast
  rules). Use when writing 日报/周报/异动/舆情报告 or working under 04日报周报/.
license: MIT
metadata:
  version: "1.0"
  standard: agentskills.io
  project: arknights-taptap-yuqing
---

# TapTap《明日方舟》舆情日/周报

## 目标

产出**可决策**的日/周报：有数字、有主簇、有风险等级、有可执行建议。  
边界：**单渠道 TapTap 评价**（app_id=70253），不是全网舆情。字段以 v1.4 为准。

## 何时用

日报、周报、异动摘要、舆情报告、`04日报周报/`。

## 数据

| 用途 | 路径 |
|------|------|
| 标注 | `03标注结果/annotations_v1_4.csv` |
| 时间 | `02数据/processed/reviews_clean.csv` → `publish_time_cn` |
| 修辞口径 | `03标注结果/讽刺反串高级黑-资料与口径.md` |

- `sentiment` = 主指标  
- `rhetoric` / `incongruity_cues` = 复核通道  
- `actionable` = 可行动候选（须改写成动作句）  

结构模板见 [assets/daily-outline.md](assets/daily-outline.md)、[assets/weekly-outline.md](assets/weekly-outline.md)。  
薄样本细则见 [references/thin-sample-rules.md](references/thin-sample-rules.md)。

## 质量标准（缺一不合格）

```text
信息（n/情绪/主题/原话）
 → 分析（是否单簇）
 → 判断（风险与影响）
 → 建议（角色 × 时机 × 动作）
```

四问串全文：发生了什么 → 为什么 → 接下来怎样 → 建议怎么做。

## 执行摘要（固定四句）

1. **【数据洞察】** 核心指标  
2. **【热点追踪】** 主簇 / 事件  
3. **【风险预警】** 一般关注｜重点关注｜紧急预警  
4. **【需要决策】** 待处理项  

紧急：登录/闪退扎堆或劝退卸载潮。重点：单簇负向突出或高星负向+可行动。

## 建议句式

`建议{产品/运营/社区/客服}针对{诉求}在{时机}采取{动作}。`  
重要项给 A/B + 利弊；弱化「必须」。日报可行动 ≤3，周报 ≤4。

## 禁止

星级字典开篇；弱对照硬写 Δpp；摘录墙当正文；编造语录；把 rhetoric 当全站崩盘叙事。

## 输出与复现

- `04日报周报/reports/daily_YYYYMMDD.md`  
- `04日报周报/reports/weekly_YYYYMMDD_YYYYMMDD.md`  

优先跑脚本保证口径一致：

```bash
python 04日报周报/build_skill_reports.py
```

人工改写时仍须满足本 Skill 的质量标准与禁止项。
