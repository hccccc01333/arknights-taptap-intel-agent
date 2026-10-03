#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第五层主入口。

    写：ingest（回填 L1-L4 真实数据）/ feedback（人工决策）/ promote（知识转正）
    读：retrieve（统一检索服务）/ context（按 Agent 组装上下文）
    学：distill（Case 蒸馏）/ playbook（归纳 + 人工审批）
    看：stats / evaluate

用法：
    python L5_memory/memory/pipeline.py --init
    python L5_memory/memory/pipeline.py --seed                 # games + L4 知识
    python L5_memory/memory/pipeline.py --ingest-l3 --ingest-l4
    python L5_memory/memory/pipeline.py --retrieve "角色捏脸 UGC 分享" --top-k 5
    python L5_memory/memory/pipeline.py --context opportunity_agent --query "角色捏脸"
    python L5_memory/memory/pipeline.py --distill --event evt_xxx [--force]
    python L5_memory/memory/pipeline.py --playbook collab       # 归纳 candidate
    python L5_memory/memory/pipeline.py --approve-pb pb_xxx --by 运营A
    python L5_memory/memory/pipeline.py --feedback cre_xxx approve "理由"
    python L5_memory/memory/pipeline.py --promote kb_xxx --by 运营A
    python L5_memory/memory/pipeline.py --stats
    python L5_memory/memory/pipeline.py --evaluate
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_L5 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L5)
for _p in (_ROOT, _L5, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from memory import MEMORY_VERSION                     # noqa: E402
from memory.store import MemoryStore                  # noqa: E402
from memory.retrieval import RetrievalEngine          # noqa: E402
from memory import cases, evaluation, ingest          # noqa: E402


def _j(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="L5 Knowledge & Growth Memory Layer")
    ap.add_argument("--db", help="记忆库路径（默认 data/state/l5_memory.sqlite3）")
    ap.add_argument("--init", action="store_true", help="初始化 schema")
    ap.add_argument("--seed", action="store_true",
                    help="回填 games 档案 + L4 TapTap 知识（不碰 L3/L4 业务数据）")
    ap.add_argument("--ingest-l3", action="store_true", help="回填 L3 事件 → Trend/短期记忆")
    ap.add_argument("--ingest-l4", action="store_true", help="回填 L4 创意与人工反馈")
    ap.add_argument("--retrieve", metavar="QUERY")
    ap.add_argument("--memory-type",
                    choices=("case", "experiment", "trend", "business", "entity",
                             "creative", "anti_pattern", "playbook"))
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--caller", default="human", help="调用方（RBAC，§54）")
    ap.add_argument("--context", metavar="AGENT_TYPE",
                    help="按 Agent 组装上下文（§44）")
    ap.add_argument("--query", default="", help="--context 的查询词")
    ap.add_argument("--distill", action="store_true", help="Case 蒸馏（§30）")
    ap.add_argument("--event", metavar="EVENT_ID")
    ap.add_argument("--force", action="store_true",
                    help="越界蒸馏：事件未闭合/实验未完也压 Case（产物标 forced）")
    ap.add_argument("--playbook", metavar="TREND_TYPE", help="从 Case 归纳 Playbook（candidate）")
    ap.add_argument("--approve-pb", metavar="PLAYBOOK_ID", help="人工审批 Playbook（§42）")
    ap.add_argument("--by", default="operator", help="操作人（promote / approve / feedback）")
    ap.add_argument("--feedback", nargs="+", metavar=("OBJECT_ID", "DECISION"),
                    help="记录人工决策：--feedback cre_x approve 理由文本")
    ap.add_argument("--promote", metavar="ITEM_ID", help="candidate → verified（§53，需 --by）")
    ap.add_argument("--sweep", action="store_true", help="清扫过期短期记忆")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--evaluate", action="store_true", help="§50 Memory Evaluation")
    args = ap.parse_args(argv)

    store = MemoryStore(args.db)
    try:
        if args.stats:
            _j({"memory_version": MEMORY_VERSION, **store.stats()})
            return 0
        if args.init or args.seed or args.ingest_l3 or args.ingest_l4:
            out: Dict[str, Any] = {"schema": store.db.meta("schema_version")}
            if args.seed:
                out["seed"] = ingest.ingest_all(store, with_l3=False, with_l4=False)
            if args.ingest_l3:
                out["l3"] = ingest.ingest_l3_trends(store)
            if args.ingest_l4:
                out["l4"] = ingest.ingest_l4(store)
            _j(out)
            return 0
        if args.retrieve is not None:
            engine = RetrievalEngine(store)
            res = engine.retrieve(args.retrieve, memory_type=args.memory_type,
                                  top_k=args.top_k, caller=args.caller)
            _j(res)
            return 0
        if args.context:
            from memory.context import ContextBuilder
            cb = ContextBuilder(store)
            _j(cb.build(args.context, query=args.query))
            return 0
        if args.distill:
            if not args.event:
                print("[warn] --distill 需要 --event evt_xxx")
                return 1
            _j(cases.distill_event(store, args.event, force=args.force))
            return 0
        if args.playbook:
            _j(cases.propose_playbook(store, args.playbook))
            return 0
        if args.approve_pb:
            _j(cases.approve_playbook(store, args.approve_pb, approved_by=args.by))
            return 0
        if args.feedback:
            if len(args.feedback) < 2:
                print("[warn] 用法：--feedback OBJECT_ID approve|reject|edit [理由文本]")
                return 1
            obj_id, decision = args.feedback[0], args.feedback[1]
            reason = " ".join(args.feedback[2:]) if len(args.feedback) > 2 else ""
            _j(store.record_decision(object_type="creative", object_id=obj_id,
                                     decision=decision, reason_text=reason,
                                     reviewer_role=args.by))
            return 0
        if args.promote:
            _j(store.promote_knowledge(args.promote, verified_by=args.by))
            return 0
        if args.sweep:
            _j({"removed": store.sweep_expired()})
            return 0
        if args.evaluate:
            _j(evaluation.summary(store))
            return 0
        ap.print_help()
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
