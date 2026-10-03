#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第六层主入口 —— Growth Intelligence Operating System 的命令行工作台。

用法（全部写操作带 --actor/--role，§43 权限矩阵强制）：
    python L6_execution/execution/pipeline.py --feed --limit 10
    python L6_execution/execution/pipeline.py --workspace evt_xxx
    python L6_execution/execution/pipeline.py --ingest-l4-creatives          # L4 产出 → 工作流
    python L6_execution/execution/pipeline.py --decide creative idea_1 approve \
        --actor 运营A --role reviewer --note 机制清晰
    python L6_execution/execution/pipeline.py --assign event evt_x --owner 社区运营 \
        --deadline 2026-10-04T18:00 --actor 负责人B --role reviewer
    python L6_execution/execution/pipeline.py --asset-gen idea_1
    python L6_execution/execution/pipeline.py --plan idea_1 --channels community_feed,push \
        --audience active_users --start ... --end ... --experiment
    python L6_execution/execution/pipeline.py --launch pln_x --actor 发布C --role publisher
    python L6_execution/execution/pipeline.py --kill pln_x stop 舆情风险 --actor D --role reviewer
    python L6_execution/execution/pipeline.py --observe-proportion exp_x ugc_rate primary 68 1000 42 1000
    python L6_execution/execution/pipeline.py --finish exp_x --actor 评审E --role reviewer
    python L6_execution/execution/pipeline.py --alerts-sweep / --alerts-pending
    python L6_execution/execution/pipeline.py --funnel / --value / --tta / --trace evt_x
    python L6_execution/execution/pipeline.py --ops-set --design 1 --dev 0 --ops 3
    python L6_execution/execution/pipeline.py --workbench                    # 生成离线工作台
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_L6 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L6)
for _p in (_ROOT, _L6, _HERE, os.path.join(_ROOT, "L4_intelligence"),
           os.path.join(_ROOT, "L5_memory")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from execution import EXECUTION_MATURITY_LEVEL, EXECUTION_VERSION  # noqa: E402
from execution.db import ExecutionDB                               # noqa: E402
from execution.audit import AuditLog, PermissionDenied, require_role  # noqa: E402
from execution.workflow import Workflow                            # noqa: E402
from execution.feed import IntelligenceFeed                        # noqa: E402
from execution.workspace import Workspace                          # noqa: E402
from execution.execution_center import AssetStudio, ExecutionCenter  # noqa: E402
from execution.experiments import ExperimentEngine                 # noqa: E402
from execution.alerts import AlertSystem                           # noqa: E402
from execution.lineage import Lineage                              # noqa: E402
from execution import ops_context                                  # noqa: E402


def _j(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


class App:
    """组装：L3/L4 上游 + L5 记忆（可选）+ L6 全家桶。库缺失时如实降级。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db = ExecutionDB(db_path)
        self.audit = AuditLog(self.db)
        self.l4 = None
        self.up = None
        self.memory = None
        try:
            from intelligence.upstream import Upstream
            from intelligence.store import IntelligenceStore
            self.up = Upstream()
            self.l4 = IntelligenceStore()
        except Exception as e:
            self.up_error = f"L3/L4 上游不可用：{e}"
        l5_db = os.environ.get("L5_MEMORY_DB") or os.path.join(
            _ROOT, "data", "state", "l5_memory.sqlite3")
        if os.path.exists(l5_db):
            try:
                from memory.store import MemoryStore
                self.memory = MemoryStore(l5_db)
            except Exception:
                self.memory = None
        self.wf = Workflow(self.db, self.audit, l5_store=self.memory, l4_store=self.l4)
        self.feed = IntelligenceFeed(self.up, self.l4, self.wf) \
            if (self.up and self.l4) else None
        self.ws = Workspace(self.up, self.l4, self.wf, self.memory) \
            if (self.up and self.l4) else None
        self.assets = AssetStudio(self.db, self.audit)
        self.center = ExecutionCenter(self.db, self.audit, self.wf)
        self.experiments = ExperimentEngine(self.db, self.audit, self.memory)
        self.alerts = AlertSystem(self.db, self.audit)
        self.lineage = Lineage(self.l4, self.db, self.memory) if self.l4 else None

    def close(self) -> None:
        self.db.close()
        if self.l4:
            self.l4.conn.close()
        if self.memory:
            self.memory.close()


def _creative_from_l4(app: App, idea_id: str) -> Dict[str, Any]:
    from execution.feed import l4_one, l4_json
    row = l4_one(app.l4,
        "SELECT * FROM intelligence_creative WHERE idea_id=?", (idea_id,))
    if not row:
        raise ValueError(f"L4 创意不存在：{idea_id}")
    # payload 是完整创意对象，但 event_id / analysis_id 是表列 —— 合并（表列不覆盖 payload）
    creative = l4_json(app.l4, row["payload"]) or {}
    for k in ("event_id", "analysis_id", "opportunity_id"):
        if creative.get(k) is None and row.get(k):
            creative[k] = row[k]
    if creative.get("score") is None and row.get("score") is not None:
        creative["score"] = row["score"]
    return creative


def _ensure_creative_item(app: App, creative: Dict[str, Any]) -> Dict[str, Any]:
    event_id = creative.get("event_id") or ""
    detected_at = ""
    if event_id and app.up is not None:
        try:
            ev = app.up.event(event_id)
            if ev:
                detected_at = ev.get("first_detected_at") or ev.get("started_at") or ""
        except Exception:
            pass                            # L3 不可达 → TTA 缺起点，如时间为空
    item = app.wf.ensure_item(
        object_type="creative", object_id=creative.get("idea_id") or "",
        title=creative.get("idea_name") or "",
        event_id=event_id,
        analysis_id=creative.get("analysis_id") or "",
        opportunity_id=creative.get("opportunity_id") or "",
        detected_at=detected_at,
        payload={"lead_time_hours": creative.get("lead_time_hours"),
                 "primary_metric": creative.get("primary_metric")})
    if item["state"] == "DRAFT":
        app.wf.apply("creative", item["object_id"], "evaluate_ready",
                     actor="agent", role="agent")          # AI 产出 → AI_READY
    return app.wf.ensure_item("creative", item["object_id"])


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="L6 Application, Decision & Growth Execution")
    ap.add_argument("--db", help="执行库路径（默认 data/state/l6_execution.sqlite3）")
    ap.add_argument("--actor", default="operator_cli")
    ap.add_argument("--role", default="operator")
    # ---- 读 ----
    ap.add_argument("--feed", action="store_true")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--workspace", metavar="EVENT_ID")
    ap.add_argument("--opportunities", metavar="EVENT_ID")
    ap.add_argument("--studio", metavar="EVENT_ID")
    ap.add_argument("--opportunity", help="--studio 配套：围绕指定机会")
    ap.add_argument("--alerts-pending", action="store_true")
    ap.add_argument("--funnel", action="store_true")
    ap.add_argument("--value", action="store_true")
    ap.add_argument("--tta", action="store_true")
    ap.add_argument("--trace", metavar="EVENT_ID")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--audit", action="store_true")
    # ---- 工作流写 ----
    ap.add_argument("--ingest-l4-creatives", action="store_true",
                    help="L4 通过评审的创意 → 工作流（AI_READY，等人工领取）")
    ap.add_argument("--ensure-event", metavar="EVENT_ID", help="把 Feed 事件建入工作流")
    ap.add_argument("--decide", nargs=3, metavar=("OBJECT_TYPE", "OBJECT_ID", "ACTION"))
    ap.add_argument("--note", default="")
    ap.add_argument("--assign", nargs=2, metavar=("OBJECT_TYPE", "OBJECT_ID"))
    ap.add_argument("--owner"), ap.add_argument("--reviewer")
    ap.add_argument("--deadline")
    # ---- 素材 / 执行 ----
    ap.add_argument("--asset-gen", metavar="CREATIVE_ID")
    ap.add_argument("--kinds", help="逗号分隔素材类型（默认全六种）")
    ap.add_argument("--asset-approve", metavar="ASSET_ID")
    ap.add_argument("--asset-publish", metavar="ASSET_ID")
    ap.add_argument("--plan", metavar="CREATIVE_ID")
    ap.add_argument("--channels", default="community_feed")
    ap.add_argument("--audience", default="active_users")
    ap.add_argument("--start"), ap.add_argument("--end")
    ap.add_argument("--experiment", action="store_true", help="--plan 时同时建实验")
    ap.add_argument("--launch", metavar="PLAN_ID")
    ap.add_argument("--kill", nargs=3, metavar=("PLAN_ID", "ACTION", "REASON"))
    ap.add_argument("--complete", metavar="PLAN_ID")
    # ---- 实验 ----
    ap.add_argument("--experiment-create", metavar="CREATIVE_ID",
                    help="从 L4 创意字段建实验（hypothesis/主指标自动带入）")
    ap.add_argument("--plan-id", help="--experiment-create 配套")
    ap.add_argument("--observe-proportion", nargs=6,
                    metavar=("EXP", "METRIC", "CLASS", "X_T", "N_T", "X_C"))
    ap.add_argument("--observe-value", nargs=5,
                    metavar=("EXP", "METRIC", "CLASS", "BASE", "TREAT"))
    ap.add_argument("--n-c", type=int, default=0)
    ap.add_argument("--finish", metavar="EXP_ID")
    ap.add_argument("--monitor", metavar="EXP_ID")
    # ---- 告警 / 运营约束 / 工作台 ----
    ap.add_argument("--alerts-sweep", action="store_true")
    ap.add_argument("--ack", metavar="ALERT_ID")
    ap.add_argument("--ops-show", action="store_true")
    ap.add_argument("--ops-set", action="store_true")
    ap.add_argument("--design", type=int), ap.add_argument("--dev", type=int)
    ap.add_argument("--ops", type=int)
    ap.add_argument("--workbench", action="store_true")
    args = ap.parse_args(argv)

    app = App(args.db)
    try:
        role, actor = args.role, args.actor
        if args.stats:
            counts = {t: app.db.table_count(t) for t in
                      ("workflow_item", "workflow_event", "production_asset",
                       "execution_plan", "experiment", "experiment_metric", "alert")}
            return _j({"execution_version": EXECUTION_VERSION,
                       "maturity_level": EXECUTION_MATURITY_LEVEL,
                       "schema": app.db.meta("schema_version"),
                       "l4_upstream": bool(app.l4), "l5_memory": bool(app.memory),
                       **counts}) or 0
        if not any([args.feed, args.workspace, args.opportunities, args.studio,
                    args.alerts_pending, args.funnel, args.value, args.tta, args.trace,
                    args.audit, args.ingest_l4_creatives, args.ensure_event, args.decide,
                    args.assign, args.asset_gen, args.asset_approve, args.asset_publish,
                    args.plan, args.launch, args.kill, args.complete,
                    args.experiment_create, args.observe_proportion, args.observe_value,
                    args.finish, args.monitor, args.alerts_sweep, args.ack,
                    args.ops_show, args.ops_set, args.workbench]):
            ap.print_help()
            return 0
        if not app.feed and any([args.feed, args.workspace, args.opportunities, args.studio,
                                 args.funnel, args.value, args.trace,
                                 args.ingest_l4_creatives, args.alerts_sweep]):
            print(f"[error] {getattr(app, 'up_error', '上游不可用')} —— "
                  f"先跑 L3/L4 产出事件与分析")
            return 1

        if args.feed:
            _j(app.feed.build(limit=args.limit)); return 0
        if args.workspace:
            _j(app.ws.trend(args.workspace)); return 0
        if args.opportunities:
            _j(app.ws.opportunities(args.opportunities)); return 0
        if args.studio:
            _j(app.ws.creative_studio(args.studio, args.opportunity)); return 0
        if args.alerts_pending:
            _j(app.alerts.pending()); return 0
        if args.audit:
            _j(app.audit.tail(100)); return 0
        if args.tta:
            _j(app.wf.time_to_action()); return 0
        if args.trace:
            _j(app.lineage.trace_event(args.trace)); return 0
        if args.funnel:
            _j(app.lineage.funnel(app.up)); return 0
        if args.value:
            _j(app.lineage.value(app.wf, app.up)); return 0
        if args.ops_show:
            _j(ops_context.load()); return 0
        if args.ops_set:
            require_role(role, "configure")
            path = ops_context.save(
                resources={"design": args.design if args.design is not None else 1,
                           "dev": args.dev if args.dev is not None else 0,
                           "ops": args.ops if args.ops is not None else 3},
                slots_24h={"push": 2, "homepage_banner": 1, "activity_page": 0})
            _j({"saved": path, "constraints": ops_context.load()}); return 0
        if args.alerts_sweep:
            _j(app.alerts.sweep(app.feed.build(limit=args.limit)["cards"])); return 0
        if args.ack:
            _j(app.alerts.acknowledge(args.ack, actor, role)); return 0
        if args.ingest_l4_creatives:
            from execution.feed import l4_query
            rows = l4_query(app.l4,
                "SELECT idea_id FROM intelligence_creative WHERE passed=1")
            made = []
            for r in rows:
                try:
                    made.append(_ensure_creative_item(app, _creative_from_l4(app, r["idea_id"])))
                except (ValueError, PermissionDenied) as e:
                    made.append({"idea_id": r["idea_id"], "error": str(e)})
            _j({"ingested": len(made), "items": made}); return 0
        if args.ensure_event:
            ev = app.up.event(args.ensure_event)
            if not ev:
                print(f"[warn] 没有事件 {args.ensure_event}"); return 1
            item = app.wf.ensure_item(
                "event", ev["event_id"], title=ev.get("canonical_title"),
                event_id=ev["event_id"],
                detected_at=ev.get("first_detected_at") or "")
            _j(item); return 0
        if args.decide:
            obj_t, obj_id, action = args.decide
            _j(app.wf.apply(obj_t, obj_id, action, actor, role, note=args.note,
                            deadline=args.deadline)); return 0
        if args.assign:
            obj_t, obj_id = args.assign
            _j(app.wf.assign(obj_t, obj_id, owner=args.owner, reviewer=args.reviewer,
                             deadline=args.deadline, actor=actor, role=role)); return 0
        if args.asset_gen:
            creative = _creative_from_l4(app, args.asset_gen)
            kinds = args.kinds.split(",") if args.kinds else None
            _j(app.assets.generate(creative, kinds, actor=actor, role=role)); return 0
        if args.asset_approve:
            _j(app.assets.approve(args.asset_approve, actor, role)); return 0
        if args.asset_publish:
            _j(app.assets.mark_published(args.asset_publish, actor, role)); return 0
        if args.plan:
            creative = _creative_from_l4(app, args.plan)
            _ensure_creative_item(app, creative)
            res = app.center.create_plan(
                creative_id=args.plan, channels=args.channels.split(","),
                audience=args.audience, start_at=args.start or "",
                end_at=args.end or "", asset_ids=[],
                experiment_enabled=args.experiment,
                control="existing_feed" if args.experiment else "",
                treatment=creative.get("idea_name") or args.plan,
                actor=actor, role=role)
            if args.experiment:
                exp = app.experiments.create(
                    creative_id=args.plan, plan_id=res["plan_id"],
                    event_id=creative.get("event_id") or "",
                    name=creative.get("idea_name") or args.plan,
                    hypothesis=creative.get("growth_hypothesis") or
                    f"{creative.get('idea_name')} 能提升 {creative.get('primary_metric')}",
                    population=args.audience, treatment=creative.get("idea_name") or args.plan,
                    control="existing_feed",
                    primary_metric=creative.get("primary_metric") or "primary_metric",
                    secondary_metrics=creative.get("secondary_metrics") or [],
                    actor=actor, role=role)
                res["experiment"] = exp
            _j(res); return 0
        if args.launch:
            _j(app.center.launch(args.launch, actor, role)); return 0
        if args.kill:
            plan_id, action, reason = args.kill
            _j(app.center.kill_switch(plan_id, action, reason, actor, role)); return 0
        if args.complete:
            _j(app.center.complete(args.complete, actor, role)); return 0
        if args.experiment_create:
            creative = _creative_from_l4(app, args.experiment_create)
            _ensure_creative_item(app, creative)
            _j(app.experiments.create(
                creative_id=args.experiment_create, plan_id=args.plan_id,
                event_id=creative.get("event_id") or "",
                name=creative.get("idea_name") or args.experiment_create,
                hypothesis=creative.get("growth_hypothesis") or "（L4 未给出假设）",
                population=args.audience,
                treatment=creative.get("idea_name") or args.experiment_create,
                control="existing_feed",
                primary_metric=creative.get("primary_metric") or "primary_metric",
                secondary_metrics=creative.get("secondary_metrics") or [],
                actor=actor, role=role)); return 0
        if args.observe_proportion:
            exp, metric, cls, x_t, n_t, x_c = args.observe_proportion
            _j(app.experiments.observe(exp, metric, cls, x_t=int(x_t), n_t=int(n_t),
                                       x_c=int(x_c), n_c=args.n_c, actor=actor, role=role))
            return 0
        if args.observe_value:
            exp, metric, cls, base, treat = args.observe_value
            _j(app.experiments.observe(exp, metric, cls, baseline_value=float(base),
                                       treatment_value=float(treat), actor=actor, role=role))
            return 0
        if args.finish:
            _j(app.experiments.finish(args.finish, actor, role)); return 0
        if args.monitor:
            _j(app.experiments.monitor(args.monitor)); return 0
        if args.workbench:
            from execution import workbench
            _j(workbench.generate(app)); return 0
        return 0
    except PermissionDenied as e:
        print(f"[权限拒绝] {e}")
        return 2
    except ValueError as e:
        print(f"[状态机拒绝] {e}")
        print("  提示：审批前需 --decide ... submit 把对象送入 REVIEWING（§12 状态机）")
        return 1
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
