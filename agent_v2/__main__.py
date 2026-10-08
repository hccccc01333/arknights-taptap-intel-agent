import argparse
import json
from uuid import uuid4

from .engine import DEFAULT_DISCOVERY_TASK, run_agent
from .ingest import ingest
from .model import model_status
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="TapTap V2 · actual evidence and tool-driven research")
    parser.add_argument("command", choices=("ingest", "collect", "status", "research", "brief"))
    parser.add_argument("--task", default=DEFAULT_DISCOVERY_TASK)
    parser.add_argument("--db", default=None)
    parser.add_argument("--days",type=int,choices=(1,7),default=7)
    parser.add_argument("--live",action="store_true",help="研究前实时采集当前渠道；会保留部分渠道失败")
    args = parser.parse_args()
    store = Store(args.db)
    owner=None
    try:
        if args.command == "ingest":
            owner="cli_import_"+uuid4().hex
            if not store.acquire(owner):raise ValueError("已有采集或研究正在执行")
            result = ingest(store)
        elif args.command == "status":
            result = {**store.overview(), "model": model_status(store.model_setting())}
        elif args.command=="brief":
            from .reports import brief
            result=brief(store,args.days)
        else:
            run_id = store.create_run(args.task)
            owner=run_id
            if not store.acquire(owner):
                store.finish(run_id,"failed",error="已有采集或研究正在执行")
                raise ValueError("已有采集或研究正在执行")
            imported = ingest(store)
            store.step(run_id, "ingest", imported)
            if args.live or args.command=="collect":
                from .connectors import refresh_sources
                live=refresh_sources(store)
                store.step(run_id,"collection",live)
            if args.command=="collect":
                store.finish(run_id,"collected" if live["status"]=="ok" else "partial",result=live)
                result=store.get_run(run_id)
            else:
                result = run_agent(store, run_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if (args.command == "research" and result["status"] != "completed") or (args.command=="collect" and result["status"]=="partial") else 0
    finally:
        if owner:store.release(owner)
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
