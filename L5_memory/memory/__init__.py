# -*- coding: utf-8 -*-
"""第五层：Knowledge & Growth Memory Layer（知识与增长记忆层）。

前四层回答「什么在爆、对 TapTap 意味着什么」；第五层回答
「TapTap 过去知道什么、做过什么、什么有效、什么无效，以及这些经验如何被第四层复用」。

★ 它不是普通 RAG，也不是一个 Vector DB（设计 §4）：
  结构化事实 + 半结构化业务知识 + 历史案例 + 实验结果 + 语义向量 + 时序数据。

★ 读路径与写路径分离（§3）：
  写 → memory.store（受 Write Policy 治理，design §18-§20 / §53）
  读 → memory.retrieval（统一 Retrieval Service，第四层不得直查库，design §24）
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../L5_memory/memory
_L5 = os.path.dirname(_HERE)                                # .../L5_memory
_ROOT = os.path.dirname(_L5)                                # 项目根
for _p in (_ROOT, _L5):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MEMORY_VERSION = "1.0"

# 八个子系统（设计 §63）→ 模块映射（§61 目录结构的务实收编）：
#   Business/Entity Knowledge → knowledge 侧（store + governance）
#   Trend/Creative/Experiment/Decision Memory → store
#   Hybrid Retrieval → retrieval
#   Learning Engine（Case/Playbook/Anti-pattern 沉淀）→ cases
#   Context Builder → context / Memory Evaluation → evaluation / Governance → governance
