# 模型评估报告（P0-2）【PROXY】

## 元数据

| 项 | 内容 |
|----|------|
| 生成时间 | 2026-07-24T10:45:57+08:00 |
| 评估模式 | **`proxy_star_weak_gold`** |
| n_gold | **0** |
| 金标说明 | PROXY：人工金标 n=0；以星级映射为弱金标，held-out n=200 (seed=42)。金标源=qc_blind_to_label.xlsx；已填情绪 0 / 盲标表 100 |
| 模型侧 | `annotations_v1_4.csv (+ qc_with_model 若有)` |
| 明细 JSON | `outputs/model_eval.json` |
| 错例 | `outputs/error_cases.csv` |

> **PROXY 声明**：当前人工金标为空/不足，本报告以「星级→情绪映射」为弱参照，指标**不可**当作正式模型精度。填完 QC 盲标后请重跑本脚本得到正式结果。

## 情绪：LLM vs 星级基线

| 系统 | n | Accuracy | Macro-F1 |
|------|---|----------|----------|
| LLM_v1.4 | 200 | 80.5% | 0.7937 |
| star_map_baseline | 200 | 100.0% | 1.0000 |

### LLM 情绪混淆矩阵（行=金标/弱金标，列=预测）

| 金标\\预测 | 正 | 中 | 负 |
|---|---|---|---|
| 正 | 63 | 3 | 2 |
| 中 | 0 | 32 | 34 |
| 负 | 0 | 0 | 66 |

### 星级基线混淆矩阵

| 金标\\预测 | 正 | 中 | 负 |
|---|---|---|---|
| 正 | 68 | 0 | 0 |
| 中 | 0 | 66 | 0 |
| 负 | 0 | 0 | 66 |

## 修辞

- 无可用修辞金标（正式评估需在盲标表填写 `gold_rhetoric`）。

## 错例摘要

- 情绪错例导出：**39** 条 → `outputs/error_cases.csv`

## 金标填完后如何重跑（正式评估）

1. 打开 `03标注结果/qc/qc_blind_to_label.xlsx`「盲标」页，用下拉填 `gold_sentiment` / `gold_rhetoric`（勿看 `qc_with_model.csv`）。
2. 可选：同步备份到 `qc_blind_to_label.csv`；也可只维护 xlsx（本脚本优先读 xlsx）。
3. 运行：`python 10分析实验室/model_eval.py` → 自动切到 `human_gold` 模式，写出 Acc / Macro-F1 / 混淆矩阵 / 错例。
4. 亦可跑 `python 03标注结果/qc/score_qc.py` 生成质检一致率报告（与本评估互补）。
5. 勿清空或覆盖已填盲标表；重新抽样前请先备份。

## 边界

- 修辞 QC 过采样 → 修辞指标不可直接外推全库。
- 星级基线在星文冲突样本上会系统性偏乐观/偏悲观；LLM 价值正在于此。
- PROXY 模式下 Acc/F1 衡量的是「相对星级映射的一致度」，不是人工正确率。

---

*由 `10分析实验室/model_eval.py` 自动生成*
