#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""api / rss / web / webhook 四类 Connector —— 目前是**接口骨架**，不是已实现。

★ 为什么只给骨架：这四类都需要外网访问与凭据（API key / 登录态 / 回调地址），
本机没有，也不该在没有授权的情况下写爬虫去抓（见 §22 合规：技术上能抓 ≠ 应该抓）。
它们的存在价值是**把接口钉死**：将来接入时只要填 fetch()，Runtime 与下游一行都不用改。
业务代码永远不关心数据到底是 API 还是网页来的。

优先级（合规）：官方 API / 授权数据 → RSS / 公共 Feed → 公开网页（遵守 robots 与条款）。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional

_L1 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _L1 not in sys.path:
    sys.path.insert(0, _L1)

from connectors.base import SourceConnector, FetchResult, ConnectorError, FailureType  # noqa: E402


class _UnimplementedConnector(SourceConnector):
    """共同骨架：把「未实现」这件事显式化，而不是让调用方撞 AttributeError。"""

    def fetch(self, cursor: Optional[str] = None, since: Optional[str] = None) -> FetchResult:
        raise ConnectorError(
            FailureType.NOT_IMPLEMENTED,
            f"[{self.acquisition_mode}] {self.source_id} 尚未接入：需要外网访问与凭据，"
            f"当前环境不具备。接口已定义，接入时只需实现 fetch() 与 parse()。",
            {"source_id": self.source_id, "acquisition_mode": self.acquisition_mode},
        )

    def parse(self, record: Dict[str, Any], ctx: Dict[str, Any]):
        raise ConnectorError(FailureType.NOT_IMPLEMENTED,
                             f"[{self.acquisition_mode}] parse 未实现")

    def health_check(self) -> Dict[str, Any]:
        return {"ok": False, "reason": "not_implemented", "checked_at": ""}


class ApiConnector(_UnimplementedConnector):
    """官方 / 授权 API。需 config: {endpoint, auth_env, params}。"""
    acquisition_mode = "api"


class RSSConnector(_UnimplementedConnector):
    """RSS / 公共 Feed。需 config: {feeds: [...]}。"""
    acquisition_mode = "rss"


class WebConnector(_UnimplementedConnector):
    """公开网页抓取。需 config: {entry_url, selectors} + 遵守 robots 与站点条款。"""
    acquisition_mode = "web"


class WebhookConnector(_UnimplementedConnector):
    """被动接收（平台推送）。需 config: {callback_path, verify_token_env}。"""
    acquisition_mode = "webhook"

    def fetch(self, cursor: Optional[str] = None, since: Optional[str] = None) -> FetchResult:
        raise ConnectorError(
            FailureType.NOT_IMPLEMENTED,
            "webhook 是被动接收，不轮询：应由 HTTP 服务收到推送后直接写 Raw Lake 并发事件。",
        )


CONNECTORS = {
    "api": ApiConnector,
    "rss": RSSConnector,
    "web": WebConnector,
    "webhook": WebhookConnector,
    "internal": None,   # 由 runtime 单独引入，避免循环导入
}


def build_connector(source_cfg: Dict[str, Any]) -> SourceConnector:
    """按 acquisition_mode 造连接器。新增一种接入方式只需在这里加一行。"""
    from connectors.internal import InternalDataConnector
    mode = (source_cfg.get("acquisition_mode") or "internal").lower()
    if mode == "internal":
        return InternalDataConnector(source_cfg)
    cls = CONNECTORS.get(mode)
    if cls is None:
        raise ConnectorError(FailureType.NOT_IMPLEMENTED, f"未知采集方式: {mode}")
    return cls(source_cfg)
