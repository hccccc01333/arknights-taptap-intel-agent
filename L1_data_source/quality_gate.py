#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1 数据质量闸门 —— 在**采集层**拦掉低价值内容，不让它落 raw_lake。

★ 为什么在采集层拦（而不是只靠 L2 清洗）：
  实测 3811 条里，"好玩" 38 条、"不好玩" 11 条、≤2 字标题 72 条 —— 这些都是
  **真实存在的低信息量内容**（external_id 各不相同，不是重复入库）。
  它们对"这个话题热不热"没有任何信息量，却会稀释聚类、污染热度分。
  采集层拦 = 脏数据不落盘，后面每一层都干净。

★ 判据是**信息量**，不是唯一性（这是实测踩出来的）：
  曾经想按"标题重复"去重，但 38 条「好玩」是 38 个不同用户的真实评论 —— 去重会
  误杀。真正该拦的是"太短 / 无实体 / 纯情绪词 / 只有搜索关键词"。

用法：
    from quality_gate import gate
    verdict = gate(title="好玩", content="好玩", platform="taptap",
                   source_type="comment")
    if verdict.reject:
        ...  # 不写入
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

MIN_TITLE_LEN = 4           # 标题最短有效长度（中文字符）
MIN_CONTENT_LEN = 8         # 正文最短有效长度
COMMENT_MIN_LEN = 6         # 评论比正文更宽松一点

# 纯情绪/无信息量词：单独出现时不含任何情报价值
LOW_INFO_WORDS = {
    "好玩", "不好玩", "厉害", "牛逼", "nb", "笑死", "顶", "赞", "沙发", "前排",
    "好看", "妙啊", "牛", "绝了", "泪目", "打卡", "签到", "路过", "蹲", "求",
    "好好玩", "好玩爱玩", "我爱玩", "真香", "有内味了", "爷青回",
}

# 表情占位符（采集器没剥掉的表情标签）
_EMOJI_PAT = re.compile(r"^\[[^\]]{1,12}\]$")
_ONLY_SYMBOLS = re.compile(r"^[\W_]+$", re.UNICODE)

# 实体/游戏线索：标题里出现这些，说明它指向真实事物而非情绪表达
_ENTITY_PAT = re.compile(
    r"[A-Za-z]{3,}"                       # 英文词（游戏名/版本名）
    r"|[0-9]{1,4}[.版本季]"                # 数字+版本/季
    r"|明日方舟|原神|崩坏|王者|吃鸡|开服|公测|上线|联动|版本|更新|活动|公测"
    r"|三角洲|鸣潮|米哈游|腾讯|网易|鹰角", re.IGNORECASE)

# 搜索词回填的痕迹：标题 == 纯关键词 + 数字序号（如「明日方舟1」）
_KEYWORD_NUM_PAT = re.compile(r"^[\u4e00-\u9fff A-Za-z]{2,8}[0-9]{1,3}$")


def title_is_valid(title: Optional[str]) -> bool:
    """标题是否携带信息量。"""
    t = (title or "").strip()
    if len(t) < MIN_TITLE_LEN:
        return False
    if _ONLY_SYMBOLS.match(t) or _EMOJI_PAT.match(t):
        return False
    if t.replace(" ", "") in LOW_INFO_WORDS:
        return False
    return True


def content_is_valid(content: Optional[str], source_type: str = "post") -> bool:
    """正文/评论是否携带信息量（比标题要求略宽）。"""
    c = (content or "").strip()
    floor = COMMENT_MIN_LEN if source_type == "comment" else MIN_CONTENT_LEN
    if len(c) < floor:
        return False
    if _ONLY_SYMBOLS.match(c) or _EMOJI_PAT.match(c):
        return False
    if c.replace(" ", "") in LOW_INFO_WORDS:
        return False
    return True


def looks_like_keyword_only(title: Optional[str], source_type: str = "post") -> bool:
    """搜索词回填：标题是「关键词+数字」，信息量等同关键词本身。

    采集端已经做了相关性过滤，但像「明日方舟1」「明日方舟3」这类仍会漏进来 ——
    它们确实是真实存在的视频标题，只是没有区分度。
    """
    if source_type != "video":
        return False
    return bool(_KEYWORD_NUM_PAT.match((title or "").strip()))


def gate(title: Optional[str], content: Optional[str] = None,
         platform: str = "", source_type: str = "post",
         extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """总闸门。返回 {reject, reason, code, kept_title}。

    ★ 只拦"确定无价值"的；对边界情况一律放过 —— 宁可留噪声，
      也不要误杀真实情报（这是 L1 反复被强调的取舍）。
    """
    t = (title or "").strip()
    c = (content or "").strip()

    if not title_is_valid(t):
        return {"reject": True, "code": "low_info_title",
                "reason": f"标题无信息量（{t[:20]!r}）"}
    if looks_like_keyword_only(t, source_type):
        return {"reject": True, "code": "keyword_only_title",
                "reason": f"标题只是搜索词+序号（{t[:20]!r}）"}
    # 评论必须有正文；视频/文章允许"只有标题"（B 站视频本就无正文）
    if source_type == "comment":
        if not content_is_valid(c, "comment"):
            return {"reject": True, "code": "low_info_content",
                    "reason": "评论为空或过短，无信息量"}
    elif c and not content_is_valid(c, source_type):
        return {"reject": True, "code": "low_info_content",
                "reason": "正文过短或无信息量"}

    # 标题有效但正文极短（如 B 站视频没正文）→ 放过，只记一条提示
    note = None
    if source_type == "video" and not c:
        note = "视频无正文，仅标题入档"
    return {"reject": False, "code": "ok", "reason": "", "note": note,
            "kept_title": t, "has_entity": bool(_ENTITY_PAT.search(t + " " + c))}