# 03 标注结果

LLM 结构化标注产物目录（与 `02数据` 原始/清洗数据分离）。

## 目录

```text
03标注结果/
  annotations_v1_4.csv         # 全量结构化标注主表（v1.4）
  raw_llm/{review_id}.json     # 模型原始响应
  reports/                     # 标注运行报告 / 质检报告
  qc/                          # 人工金标抽检（见 qc/README.md）
  讽刺反串高级黑-资料与口径.md
  README.md
```

## 质检（人工金标）

```bash
python 03标注结果/qc/make_qc_sample.py   # 已生成可跳过
# 填写 qc/qc_blind_to_label.csv 后：
python 03标注结果/qc/score_qc.py
```

详见 [`qc/README.md`](./qc/README.md)。

## 输入依赖

- 清洗表：`02数据/processed/reviews_clean.csv`
- 标注脚本：`02数据/annotate_reviews.py`（输出已指向本目录）
- 报告脚本：`02数据/build_sample_report.py`
- 日/周报：`04日报周报/build_period_reports.py`（读本目录 `annotations_v1_4.csv`）

## 续跑

```bash
python 02数据/annotate_reviews.py --limit 50 --reasoning-effort high --model deepseek-v4-pro
python 02数据/build_sample_report.py
```
