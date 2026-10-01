# 分析实验室（10）

诊断模块：显著性、贡献分解、模型评估、事件影响。  
**不参与主链 KPI**；产物供 `L6_delivery/dashboard` 的「分析实验室」面板嵌入（`#boot.lab`）。

## 模块

| ID | 脚本 | 输出 JSON | 回答什么 |
|----|------|-----------|----------|
| P0-1 | `anomaly_diagnosis.py` | `outputs/anomaly_diagnosis.json` | 变了多少？是否显著？谁贡献？ |
| P0-2 | `model_eval.py`（另建） | `outputs/model_eval.json` | 标签准不准？ |
| P0-3 | `event_impact.py`（另建） | `outputs/event_impact.json` | 事件前后差多少？ |

刷新看板：先跑实验室脚本，再 `python L6_delivery/dashboard/build_dashboard.py`。

---

## P0-1 异动诊断

### 做什么

对 TapTap 已标注评价做：

1. **时段对照**：本周（7 日窗）vs 上周；可选滚动 3 个有数据日 vs 再往前 3 日  
2. **负向率 + Wilson 95% CI**  
3. **两比例 z 检验**（pooled SE，双侧 α=0.05）+ `significant` 标记  
4. **样本警告**：任一侧 n&lt;30 或对照 &lt;0.5×本期 → 禁止「大幅异动」叙事  
5. **主题贡献瀑布**：Kitagawa 三分解（结构 / 主题内 / 交互）还原 Δneg_rate

### 运行

```bash
python L2_signal/lab/anomaly_diagnosis.py
# 可选
python L2_signal/lab/anomaly_diagnosis.py --week-end 2026-07-20
python L2_signal/lab/anomaly_diagnosis.py --no-rolling3
```

### 产物

| 路径 | 说明 |
|------|------|
| `outputs/anomaly_diagnosis.json` | 看板 `lab.anomaly` 主输入 |
| `reports/anomaly_diagnosis_YYYYMMDD_YYYYMMDD.md` | 带方法说明的可读报告 |
| `reports/anomaly_diagnosis_latest.md` | 同上，固定文件名便于链接 |

### JSON 字段（UI）

```text
available, headline, primary_comparison_id
comparisons[]   period/prior/neg_rate/wilson_ci/delta_pp/z_stat/p_value/significant/verdict/sample_warning
table[]         扁平行（指标表）
waterfall[]     {label, delta_pp, cumulative_pp, kind}  瀑布条
contributions[] 分主题 structure_pp / within_pp / interaction_pp / total_pp
decomposition   完整分解 + steps + totals
methods         公式与门槛说明
```

### 方法口径（短）

- 负向：`sentiment == '负'`（v1.4）  
- Δ = Σ(Δshare·r₀) + Σ(share₀·Δr) + Σ(Δshare·Δr)  
- **未显著就是未显著**；样本不足只报绝对水平与 CI

### 边界

- 基于已标注切片，非正式全站 KPI  
- 分解是会计恒等式，不是因果  
- 不改动 `data/annotations/qc/`，不重跑标注  
