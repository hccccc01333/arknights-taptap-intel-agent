"""One auditable tool boundary with durable, pre-reserved research budgets."""
import json
import sqlite3
import time
import uuid

from agent_v2.store import dump,now_iso
from .runtime_guard import LeaseLost


def initialize(store):
    store.conn.executescript('''
      CREATE TABLE IF NOT EXISTS tool_budget(
        run_id TEXT,scope TEXT,budget INTEGER,spent INTEGER,reserved INTEGER,
        PRIMARY KEY(run_id,scope));
      CREATE TABLE IF NOT EXISTS tool_execution(
        call_id TEXT PRIMARY KEY,run_id TEXT,scope TEXT,tool TEXT,arguments TEXT,
        started_at TEXT,finished_at TEXT,status TEXT,reserved_units INTEGER,
        charged_units INTEGER,seconds REAL,result TEXT,sources TEXT,error TEXT,
        monetary_cost REAL,cost_status TEXT);
      CREATE INDEX IF NOT EXISTS tool_execution_run ON tool_execution(run_id,started_at);
    ''')


class ToolExecutor:
    def __init__(self,store,run_id=None,*,limit=6,scope='research'):
        if type(limit) is not int or not 0<=limit<=100:
            raise ValueError('工具预算须为 0 至 100 的整数')
        self.store=store
        self.run_id=run_id or 'tools_'+uuid.uuid4().hex
        self.scope=scope
        with store.conn:
            store.conn.execute('INSERT OR IGNORE INTO tool_budget VALUES(?,?,?,0,0)',(self.run_id,scope,limit))

    @property
    def remaining(self):
        row=self.store.conn.execute('SELECT * FROM tool_budget WHERE run_id=? AND scope=?',(self.run_id,self.scope)).fetchone()
        return max(0,row['budget']-row['spent']-row['reserved'])

    def invoke(self,name,arguments,handler,*,maximum=0):
        if not isinstance(arguments,dict) or type(maximum) is not int or maximum<0:
            raise ValueError('工具参数和预算无效')
        store=self.store
        if store.conn.atomic_depth:
            raise RuntimeError('工具调用不能占用交付事务')
        if store._run_owner:store.heartbeat(store._run_owner)
        call_id='tool_'+uuid.uuid4().hex
        store.conn.commit()
        store.conn.execute('BEGIN IMMEDIATE')
        try:
            units=min(maximum,self.remaining)
            blocked=maximum>0 and units==0
            store.conn.execute('INSERT INTO tool_execution VALUES(?,?,?,?,?,?,NULL,?,?,0,NULL,NULL,NULL,NULL,NULL,?)',
                (call_id,self.run_id,self.scope,name,dump(arguments),now_iso(),'budget_exhausted' if blocked else 'running',units,'not_reported'))
            if units:store.conn.execute('UPDATE tool_budget SET reserved=reserved+? WHERE run_id=? AND scope=?',(units,self.run_id,self.scope))
            store.conn.commit()
        except BaseException:
            store.conn.rollback();raise
        if blocked:
            with store.conn:store.conn.execute('UPDATE tool_execution SET finished_at=?,seconds=0,result=? WHERE call_id=?',
                (now_iso(),dump({'status':'budget_exhausted'}),call_id))
            return {'calls':0,'status':'budget_exhausted','tool_call_id':call_id}
        started=time.monotonic();error=None;conservative=False;outcome='completed'
        try:
            result=handler(units)
            if not isinstance(result,dict):raise ValueError('工具必须返回对象')
            actual=result.get('calls',units)
            if type(actual) is not int or not 0<=actual<=units:
                raise ValueError('工具报告用量超过已预留预算')
            charged=actual
            if result.get('status') in ('failed','error','unavailable','access_denied'):
                outcome='failed';error='tool_reported_failure'
            source_ids=[]
            candidates=[arguments.get('evidence_id')]
            ids=result.get('evidence_ids',[])
            if not isinstance(ids,list):raise ValueError('工具来源编号须为列表')
            candidates.extend(ids)
            for key in ('results','evidence'):
                rows=result.get(key,[])
                if not isinstance(rows,list):raise ValueError('工具来源结果须为列表')
                candidates.extend(r.get('evidence_id') for r in rows if isinstance(r,dict))
            for eid in candidates:
                if eid is None:continue
                if not isinstance(eid,str):raise ValueError('工具来源编号须为字符串')
                if eid and eid not in source_ids:source_ids.append(eid)
            if store._run_owner:store.assert_owner(store._run_owner)
        except Exception as failure:
            error=type(failure).__name__
            charged=units;conservative=True;outcome='stale' if isinstance(failure,LeaseLost) else 'failed'
            result={'calls':charged,'status':outcome,'error':error}
            source_ids=[arguments['evidence_id']] if isinstance(arguments.get('evidence_id'),str) else []
            if isinstance(failure,LeaseLost):lost=failure
            else:lost=None
        else:lost=None
        sources=[]
        for eid in source_ids:
            if not eid:continue
            row=store.conn.execute('SELECT evidence_id,url,title,published_at FROM evidence WHERE evidence_id=?',(eid,)).fetchone()
            if row:sources.append(dict(row))
        summary={k:result[k] for k in ('status','calls','saved','count','capture_id','evidence_ids','limit') if k in result}
        summary['usage_basis']='conservative_after_exception' if conservative else 'tool_report'
        # Audit finalisation remains possible after losing the worker fence. It
        # cannot create evidence or business outputs and uses this call_id only.
        store.conn.commit()
        sqlite3.Connection.execute(store.conn,'BEGIN IMMEDIATE')
        try:
            cursor=sqlite3.Connection.execute(store.conn,'''UPDATE tool_execution SET finished_at=?,status=?,charged_units=?,seconds=?,result=?,sources=?,error=?,cost_status=?
              WHERE call_id=? AND status IN ('running','interrupted') ''',(now_iso(),outcome,charged,round(time.monotonic()-started,3),dump(summary),dump(sources),error,
              'unknown_after_exception' if conservative else 'not_reported',call_id))
            if cursor.rowcount:
                sqlite3.Connection.execute(store.conn,'UPDATE tool_budget SET reserved=reserved-?,spent=spent+? WHERE run_id=? AND scope=?',
                    (units,charged,self.run_id,self.scope))
            store.conn.commit()
        except BaseException:
            store.conn.rollback();raise
        if lost:raise lost
        return {**result,'tool_call_id':call_id}

    def execute(self,name,args):
        store=self.store;tid=args['topic_id'];eid=args.get('evidence_id');query=args.get('query','')
        from .graph_retrieval import TOOLS,tool
        if name in TOOLS:
            handler=lambda units:tool(store,name,args);maximum=0
        elif name=='read_detail':
            from .enrichment import prepare
            handler=lambda units:prepare(store,topic_id=tid,evidence_id=eid,max_calls=units)
            maximum=1
        elif name=='search_news':
            from .research import background
            handler=lambda units:background(store,tid,query);maximum=1
        elif name=='search_web':
            from .web_research import background
            handler=lambda units:background(store,tid,query,max_calls=units);maximum=3
        elif name in ('browse_page','screenshot_ocr'):
            from .browser_tools import read
            handler=lambda units:read(store,tid,eid,visual=name=='screenshot_ocr');maximum=1
        elif name=='read_comments_visual':
            from .browser_tools import read_comments
            handler=lambda units:read_comments(store,tid,eid);maximum=1
        elif name=='sample_discussion':
            from .discussion import sample
            handler=lambda units:sample(store,tid,eid,max_calls=units);maximum=2
        else:raise ValueError('研究工具未注册')
        return self.invoke(name,args,handler,maximum=maximum)


def history(store,run_id):
    return [{**dict(row),'arguments':json.loads(row['arguments']),
        'result':json.loads(row['result']) if row['result'] else None,
        'sources':json.loads(row['sources']) if row['sources'] else []}
        for row in store.conn.execute('SELECT * FROM tool_execution WHERE run_id=? ORDER BY started_at,rowid',(run_id,))]
