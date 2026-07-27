# 质检样本元信息

- 生成时间：2026-07-22T14:34:09+08:00
- 标注源：`annotations_v1_4.csv`
- 样本量：**100**（seed=42）
- 其中 rhetoric≠none：**47**
- 其中高星负向（模型）：**31**
- 情绪分布（模型）：{'负': 68, '正': 23, '中': 9}
- 修辞分布（模型）：{'none': 53, 'sarcasm': 32, 'gaoji_hei': 11, 'template_praise': 4}
- strata：{'rhetoric_sarcasm': 28, 'sent_pos': 22, 'sent_neg': 18, 'hi_star_neg': 15, 'rhetoric_gaoji_hei': 8, 'sent_mid': 5, 'rhetoric_other': 4}

## 文件

| 文件 | 用途 |
|------|------|
| `qc_blind_to_label.xlsx` | **推荐：带下拉选项，你来填** |
| `qc_blind_to_label.csv` | 同内容纯文本备份 |
| `qc_with_model.csv` | 打分对照（标注时勿看） |

填完后运行：`python 03标注结果/qc/score_qc.py`
