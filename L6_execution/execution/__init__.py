# -*- coding: utf-8 -*-
"""第六层：Application, Decision & Growth Execution Layer（应用、决策与增长执行层）。

前五层完成 Intelligence Pipeline（Capture → Understand → Detect → Reason → Learn）；
第六层是 **Decision & Action Interface**（Act）：Feed → 工作区 → 决策 → 素材 → 执行 → 实验
→ 结果回写第五层 Memory —— 三个闭环（§47）在这里闭合。

★ 设计 §21：本层 MVP 固定在 **Level 2**（AI 准备，人工审批，系统执行）——
  Publish / Campaign Launch / Push Send 一律不允许 agent 角色执行（§43 硬规则，audit 强制）。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../L6_execution/execution
_L6 = os.path.dirname(_HERE)                                # .../L6_execution
_ROOT = os.path.dirname(_L6)                                # 项目根
for _p in (_ROOT, _L6):
    if _p not in sys.path:
        sys.path.insert(0, _p)

EXECUTION_VERSION = "1.0"

# §21 执行成熟度：AI 发现/分析/生成/准备素材 → Human Approve → 系统执行。
# Level 4（闭环自主优化）不是本项目的现阶段，写死在这里防止"顺手自动化"。
EXECUTION_MATURITY_LEVEL = 2
