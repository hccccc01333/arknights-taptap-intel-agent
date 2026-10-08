#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3 · Trend Intelligence Layer（热点识别与趋势智能层）—— Data Science Core。

边界一句话：**第三层回答"什么正在发生、它有多热、处于什么阶段"；
第四层回答"这对 TapTap 意味着什么"。**

★ 核心操作对象是 **Event，不是 keyword，也不是 topic**：
    Topic = 长期语义主题（黑神话：悟空，可能持续几年）
    Event = 短时间发生的具体事情（黑神话 DLC 疑似泄露）

★ 三个必须分开的评分：
    HotScore    有多热
    Momentum    还在不在继续上升
    Confidence  我们有多确定（"看起来很热，但证据还少"必须有地方表达）

★ LLM 不进热点计算主路径（§44）：只用于 Event 命名/摘要/歧义簇判定。
  本机无 key → 命名走规则（实体 + 关键词 + 事件动词），其余照常确定性计算。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_L3 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L3)
for _p in (_L3, os.path.join(_ROOT, "L2_signal"), os.path.join(_ROOT, "L1_data_source")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TREND_ENGINE_VERSION = "2026.10.1"
