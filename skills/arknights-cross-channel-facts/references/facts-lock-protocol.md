# facts 锁数协议

## 原则

1. **数字只由代码产生**（`build_channel_facts.py`）  
2. LLM 输入 = 整份 facts JSON（或等价摘要，但数字集合不变）  
3. 输出中出现的整数与百分比，必须 ∈ `allowed_numbers`  
4. 校验失败：再请求一次；仍失败 → 模板降级，数字填回 facts  

## 建议校验步骤

```text
validate → 扫描输出中的整数与 x.x%
retry    → 校验失败可再请求一次
fallback → 模板降级，数字全来自 facts
```

## 与主链关系

- 日/周报 Skill **不**把跨渠百分比写进 TapTap KPI 终稿  
- 本 Skill 负责多源结构对照与值班建议，并标明样本边界
