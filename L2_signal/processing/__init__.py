#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L2 · 数据加工与语义标准化层（processing 子系统）。

边界一句话：**第二层理解"一条内容是什么"，第三层理解"一群内容正在形成什么事件"。**
所以这一层不做：是不是热点、是不是同一个事件、该不该追、该做什么动作。

九个子系统（对齐规格 §37）：
    Schema Validator / Normalization / Dedup / Entity / Classification
    / Embedding / Quality / Feature / Processed Store

★ 默认不用 LLM（规格 §33）：规则 + 词典 + 确定性算法。
只有 Entity Linking 低置信度且内容重要时，才交给 LLM 兜底（接口已留，本机未接）。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_L2 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L2)
_L1 = os.path.join(_ROOT, "L1_data_source")
for _p in (_L1, _L2):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PROCESSING_VERSION = "2026.10.1"
