# TapTap《明日方舟》舆情日/周报 · Skill 副本

> **权威源（Agent Skills 开源标准）：** [`skills/arknights-taptap-yuqing-report/SKILL.md`](../skills/arknights-taptap-yuqing-report/SKILL.md)  
> 本文件仅为目录内速查副本；冲突以 `skills/` 为准。可用任意兼容 Agent Skills 的工具加载。

---

name: arknights-taptap-yuqing-report

本项目边界：单渠道 TapTap 评价（app_id=70253），不是全网舆情。字段以 v1.4 为准。

## 数据

- 标注：`03标注结果/annotations_v1_4.csv`
- 时间：`02数据/processed/reviews_clean.csv` → `publish_time_cn`
- `sentiment` = 主指标；`rhetoric` = 复核通道；`actionable` = 可行动候选

## 薄样本

- 锚定日 = 最新发布日；`n<30` → 滚动近 3 个有数据日
- 对照不足 → 禁止 Δpp /「大幅异动」

## 质量

四层：信息 → 分析 → 判断 → 建议  
四问：发生了什么 → 为什么 → 接下来怎样 → 建议怎么做  
摘要四句：【数据洞察】【热点追踪】【风险预警】【需要决策】

## 建议句式

`建议{角色}针对{诉求}在{时机}采取{动作}。`

## 结构

日报：摘要 → 态势 → 异常≤2 → 可行动≤3 → 修辞复核 → 边界  
周报：摘要 → 态势 → 事件弧2–3 → 情感弧线 → 双表 → 下周行动 → 边界

## 输出

`04日报周报/reports/daily_*.md` / `weekly_*.md`

```bash
python 04日报周报/build_skill_reports.py
```
