# 04 日报周报

基于 `03标注结果/annotations_v1_4.csv` 的 TapTap《明日方舟》舆情日/周报。

## Skill（写作口径）

- 权威：[`skills/arknights-taptap-yuqing-report/SKILL.md`](../skills/arknights-taptap-yuqing-report/SKILL.md)
- 目录副本：[`skill.MD`](./skill.MD)

要点：四问法、摘要四句式、事件四段论、弱对照禁用 Δpp、薄样本滚动窗。

## 一键出终稿（推荐）

```bash
python 04日报周报/build_skill_reports.py
python 04日报周报/build_skill_reports.py --day 2026-07-20 --week-end 2026-07-20
```

默认：锚定日 = 库内最新发布日；若该日 n&lt;30，自动用近 3 个有数据日滚动窗；对照不足则不写 Δpp。

可选参数：`--min-daily-n 30`、`--roll-days 3`、`--min-brief-n 15`、`--daily-only`、`--weekly-only`。

## 数字底稿（可选）

```bash
python 04日报周报/build_period_reports.py --day 2026-07-20 --week-end 2026-07-20
```

输出 `*_sample_*`，仅信息层半成品，**勿当终稿**。

## 最新终稿

- 日报：`reports/daily_YYYYMMDD.md`（文件名用锚定日；元数据写清是否滚动窗）
- 周报：`reports/weekly_YYYYMMDD_YYYYMMDD.md`
