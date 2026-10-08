import argparse
import json
import uuid

from agent_v2.store import now_iso, dump
from .store import Store
from .engine import TASK
from .service import execute_cycle


def main():
    parser=argparse.ArgumentParser(description="TapTap V3 discovery and growth packs")
    parser.add_argument("command",choices=("collect","research","status"))
    parser.add_argument("--task",default=TASK);parser.add_argument("--db")
    parser.add_argument("--live",action="store_true")
    args=parser.parse_args();store=Store(args.db)
    if args.command=="status":
        try:print(dump(store.overview()))
        finally:store.close()
        return 0
    run_id=store.create_run(args.task);cycle_id="cycle_"+uuid.uuid4().hex
    if not store.acquire(run_id,ttl=1200):
        store.finish(run_id,"failed",error="已有任务运行");store.close();return 1
    store.conn.execute("INSERT INTO cycle VALUES(?,?,?,?,?,?,?,?)",(cycle_id,now_iso(),None,"queued",None,None,dump([run_id]),None));store.conn.commit();store.close()
    execute_cycle(run_id,cycle_id,args.command=="research",args.live or args.command=="collect",store_path=args.db)
    store=Store(args.db)
    try:
        result=store.get_run(run_id);print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result["status"] in ("completed","collected","no_pending_topics","intelligence_ready","no_pending_work") else 1
    finally:store.close()


if __name__=="__main__":raise SystemExit(main())
