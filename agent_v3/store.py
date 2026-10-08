from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_v2.store import Store as EvidenceStore, ROOT, dump, now_iso, stable_id
from . import __version__


class Store(EvidenceStore):
    def context(self):
        from .taptap_profile import PROFILE
        return {**super().context(), 'product_profile': PROFILE}

    def upsert_evidence(self,item):
        organic_kind=item.get('kind') if item.get('source_path','').startswith('v3:live:') else None
        # Keep a fresh detail read when the same article's feed only supplies
        # its summary; channel observations still retain their new metrics.
        if item.get("source_path")!="v2:read_source" and not item.get('source_path','').startswith('v3:read_source:'):
            previous=self.conn.execute("""SELECT e.title,e.body,e.url,e.source_path FROM evidence e
              JOIN source_read r USING(evidence_id) WHERE e.evidence_id=? AND r.status='ok'
              AND r.retry_at>? AND (e.source_path='v2:read_source' OR e.source_path LIKE 'v3:read_source:%')""",(item["evidence_id"],now_iso())).fetchone()
            if previous and previous["title"]==item.get("title") and previous["url"]==item.get("url"):
                item={**item,"body":previous["body"],"source_path":previous["source_path"]}
        inserted=super().upsert_evidence(item)
        # Search and feed observations share original identities. Once the
        # actual channel supplies a document, apply its type and date rules;
        # a later background search must not downgrade it back to a search hit.
        if organic_kind in ('news','content','ranking','post'):
            self.conn.execute("UPDATE evidence SET kind=? WHERE evidence_id=? AND kind='search'",(organic_kind,item['evidence_id']))
        return inserted

    def reasoning_setting(self):
        from .deepseek import ALIASES
        key="deepseek_reasoning_effort" if self.model_setting() in ALIASES else "space_bunny_reasoning_effort" if self.model_setting()=="spacebunny/space-bunny-alpha" else "zen_reasoning_effort"
        row=self.conn.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
        return json.loads(row[0]) if row else "low"

    def set_reasoning(self,value):
        from .opencode_zen import REASONING_EFFORTS
        from .deepseek import ALIASES, EFFORTS as DEEPSEEK_EFFORTS
        deepseek=self.model_setting() in ALIASES
        bunny=self.model_setting()=="spacebunny/space-bunny-alpha"
        if deepseek:allowed=DEEPSEEK_EFFORTS
        elif bunny:
            from .space_bunny import EFFORTS
            allowed=EFFORTS
        else:allowed=REASONING_EFFORTS
        if not isinstance(value,str) or value not in allowed:
            raise ValueError("DeepSeek 推理强度需为关闭、低、高、最大或模型默认" if deepseek else "太空兔推理强度需为默认、低、中、高、极高或最大" if bunny else "推理强度需为默认、低、中或高")
        key="deepseek_reasoning_effort" if deepseek else "space_bunny_reasoning_effort" if bunny else "zen_reasoning_effort"
        self.conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,dump(value)))
        self.conn.commit()
        return {"effort":value}

    def output_setting(self):
        from .deepseek import DEFAULT_OUTPUT_LIMIT
        row=self.conn.execute("SELECT value FROM settings WHERE key='deepseek_output_limit'").fetchone()
        return json.loads(row[0]) if row else DEFAULT_OUTPUT_LIMIT

    def set_output(self,value):
        if type(value) is not int or not 256<=value<=32768:
            raise ValueError("DeepSeek 输出总预算需为 256 至 32768 token")
        self.conn.execute("INSERT INTO settings VALUES('deepseek_output_limit',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dump(value),))
        self.conn.commit()
        return {"max_tokens":value}

    def set_model(self,value):
        if not isinstance(value,str):raise ValueError("请选择已支持的模型配置")
        from .deepseek import ALIASES, status as deepseek_status
        if value in ALIASES:
            result=deepseek_status(value)
            if not result['configured']:raise ValueError('未配置 DeepSeek 官方环境变量')
            self.conn.execute("INSERT INTO settings VALUES('model',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dump(value),));self.conn.commit()
            return result
        if value=="spacebunny/space-bunny-alpha":
            from .space_bunny import status
            result=status()
            if not result["configured"]:raise ValueError("未配置太空兔环境变量")
            self.conn.execute("INSERT INTO settings VALUES('model',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dump(value),));self.conn.commit()
            return result
        if not value.startswith("opencode/"):return super().set_model(value)
        from .opencode_zen import ZEN_MODELS
        from .model import model_status
        if value.split("/",1)[1] not in ZEN_MODELS:raise ValueError("仅支持已列出的 Zen 免费模型")
        result=model_status(value)
        if not result["configured"]:raise ValueError("请先安装官方 OpenCode CLI 或设置 V3_OPENCODE_BIN")
        self.conn.execute("INSERT INTO settings VALUES('model',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dump(value),))
        self.conn.commit()
        return result

    def __init__(self, path=None):
        super().__init__(path if path is not None else ROOT / "data/v3/agent.sqlite3")
        from .followups import initialize
        initialize(self)
        from .retention import initialize as initialize_retention
        initialize_retention(self)
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS channel_observation(
          channel_id TEXT, evidence_id TEXT, observed_at TEXT, position REAL,
          metrics TEXT, PRIMARY KEY(channel_id,evidence_id,observed_at)
        );
        CREATE TABLE IF NOT EXISTS channel_health(
          channel_id TEXT PRIMARY KEY, attempted_at TEXT, succeeded_at TEXT,
          status TEXT, count INTEGER, failures INTEGER, error TEXT, retry_after TEXT
        );
        CREATE TABLE IF NOT EXISTS discovery_cursor(
          evidence_id TEXT PRIMARY KEY, observed_at TEXT, content_hash TEXT
        );
        CREATE TABLE IF NOT EXISTS topic(
          topic_id TEXT PRIMARY KEY, title TEXT, first_seen_at TEXT, last_seen_at TEXT,
          fingerprint TEXT, reviewed_fingerprint TEXT, priority REAL,
          signals TEXT, decision TEXT, event_id TEXT, reviewed_at TEXT, eligible INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS topic_member(
          topic_id TEXT, evidence_id TEXT, active INTEGER DEFAULT 1,
          PRIMARY KEY(topic_id,evidence_id)
        );
        CREATE TABLE IF NOT EXISTS topic_history(
          topic_id TEXT, fingerprint TEXT, observed_at TEXT, payload TEXT,
          PRIMARY KEY(topic_id,fingerprint)
        );
        CREATE TABLE IF NOT EXISTS insight(
          evidence_id TEXT, content_hash TEXT, prompt_version TEXT, created_at TEXT,
          run_id TEXT, payload TEXT, PRIMARY KEY(evidence_id,content_hash,prompt_version)
        );
        CREATE TABLE IF NOT EXISTS material(
          material_id TEXT PRIMARY KEY, kind TEXT, origin TEXT, title TEXT,
          content TEXT, tags TEXT, evidence_ids TEXT, source_versions TEXT,
          rights_status TEXT, created_at TEXT, run_id TEXT, event_id TEXT, creative_id TEXT
        );
        CREATE TABLE IF NOT EXISTS material_use(
          material_id TEXT, event_id TEXT, creative_id TEXT, run_id TEXT,
          PRIMARY KEY(material_id,event_id,creative_id,run_id)
        );
        CREATE INDEX IF NOT EXISTS material_identity ON material(kind,origin,title);
        CREATE TABLE IF NOT EXISTS cycle(
          cycle_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT,
          status TEXT, collection TEXT, discovery TEXT, run_ids TEXT, error TEXT
        );
        CREATE TABLE IF NOT EXISTS source_asset(
          asset_id TEXT PRIMARY KEY, evidence_id TEXT, source_version TEXT, kind TEXT,
          title TEXT, content TEXT, url TEXT, scope TEXT, rights_status TEXT,
          first_seen_at TEXT, last_seen_at TEXT
        );
        CREATE INDEX IF NOT EXISTS source_asset_evidence ON source_asset(evidence_id,source_version);
        CREATE TABLE IF NOT EXISTS source_asset_channel(
          asset_id TEXT, channel_id TEXT, PRIMARY KEY(asset_id,channel_id)
        );
        CREATE TABLE IF NOT EXISTS source_asset_use(
          asset_id TEXT, creative_id TEXT, run_id TEXT, PRIMARY KEY(asset_id,creative_id,run_id)
        );
        CREATE TABLE IF NOT EXISTS topic_intelligence(
          topic_id TEXT, fingerprint TEXT, prompt_version TEXT, run_id TEXT,
          created_at TEXT, payload TEXT, PRIMARY KEY(topic_id,fingerprint,prompt_version)
        );
        CREATE TABLE IF NOT EXISTS topic_intelligence_revision(
          revision_id TEXT PRIMARY KEY,topic_id TEXT,fingerprint TEXT,prompt_version TEXT,run_id TEXT,created_at TEXT,payload TEXT
        );
        CREATE TABLE IF NOT EXISTS work_item(
          job_id TEXT PRIMARY KEY, stage TEXT, topic_id TEXT, fingerprint TEXT,
          prompt_version TEXT, bucket TEXT, priority REAL, status TEXT,
          attempts INTEGER, created_at TEXT, updated_at TEXT, run_id TEXT,
          lease_until TEXT, retry_at TEXT, result TEXT, error TEXT
        );
        CREATE INDEX IF NOT EXISTS work_due ON work_item(stage,status,retry_at);
        CREATE TABLE IF NOT EXISTS model_gate(
          scope TEXT PRIMARY KEY, reason TEXT, observed_at TEXT, retry_at TEXT
        );
        CREATE TABLE IF NOT EXISTS source_read(
          evidence_id TEXT PRIMARY KEY,status TEXT,attempted_at TEXT,succeeded_at TEXT,
          retry_at TEXT,error TEXT,source_version TEXT,scope TEXT,characters INTEGER
        );
        CREATE TABLE IF NOT EXISTS creative_draft(
          draft_id TEXT PRIMARY KEY,topic_id TEXT,fingerprint TEXT,context_version TEXT,
          model TEXT,run_id TEXT,created_at TEXT,status TEXT,payload TEXT
        );
        CREATE TABLE IF NOT EXISTS source_read_meta(
          evidence_id TEXT PRIMARY KEY, source_version TEXT, payload TEXT
        );
        CREATE TABLE IF NOT EXISTS source_read_request(
          evidence_id TEXT PRIMARY KEY, requested_url TEXT, attempted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS source_locator(evidence_id TEXT PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS research_task(
          task_id TEXT PRIMARY KEY,topic_id TEXT,fingerprint TEXT,created_at TEXT,updated_at TEXT,status TEXT,payload TEXT
        );
        CREATE TABLE IF NOT EXISTS research_link(
          topic_id TEXT,evidence_id TEXT,role TEXT,state TEXT,reason TEXT,created_at TEXT,
          PRIMARY KEY(topic_id,evidence_id)
        );
        CREATE TABLE IF NOT EXISTS research_capability(
          capability TEXT PRIMARY KEY,status TEXT,attempted_at TEXT,succeeded_at TEXT,retry_at TEXT,error TEXT,payload TEXT
        );
        CREATE TABLE IF NOT EXISTS discussion_sample(
          evidence_id TEXT PRIMARY KEY,parent_evidence_id TEXT,comment_id TEXT,platform TEXT,
          author_hash TEXT,text_hash TEXT,flags TEXT,published_at TEXT,observed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS discussion_observation(
          evidence_id TEXT,method TEXT,observed_at TEXT,PRIMARY KEY(evidence_id,method,observed_at)
        );
        CREATE TABLE IF NOT EXISTS tracked_event(
          tracked_id TEXT PRIMARY KEY,anchor_evidence_id TEXT,title TEXT,first_observed_at TEXT,last_observed_at TEXT,
          status TEXT,merged_into TEXT,revision TEXT
        );
        CREATE TABLE IF NOT EXISTS tracked_member(
          evidence_id TEXT PRIMARY KEY,tracked_id TEXT,relation TEXT,reason TEXT,updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS tracked_change(
          change_id TEXT PRIMARY KEY,tracked_id TEXT,kind TEXT,observed_at TEXT,payload TEXT
        );
        CREATE TABLE IF NOT EXISTS event_relation(
          pair_id TEXT PRIMARY KEY,left_id TEXT,right_id TEXT,left_version TEXT,right_version TEXT,
          score REAL,method TEXT,status TEXT,reason TEXT,created_at TEXT,updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS relation_history(
          history_id TEXT PRIMARY KEY,pair_id TEXT,status TEXT,reason TEXT,actor TEXT,created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS relation_effect(
          sequence INTEGER PRIMARY KEY AUTOINCREMENT,pair_id TEXT UNIQUE,status TEXT,payload TEXT,created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS semantic_embedding(
          content_hash TEXT,model_version TEXT,vector TEXT,PRIMARY KEY(content_hash,model_version)
        );
        CREATE TABLE IF NOT EXISTS main_decision(
          topic_id TEXT,fingerprint TEXT,context_version TEXT,run_id TEXT,created_at TEXT,payload TEXT,
          PRIMARY KEY(topic_id,fingerprint,context_version)
        );
        CREATE TABLE IF NOT EXISTS topic_interpretation(
          topic_id TEXT,fingerprint TEXT,run_id TEXT,created_at TEXT,payload TEXT,
          PRIMARY KEY(topic_id,fingerprint)
        );
        CREATE TABLE IF NOT EXISTS interpretation_revision(
          revision_id TEXT PRIMARY KEY,topic_id TEXT,fingerprint TEXT,run_id TEXT,created_at TEXT,payload TEXT
        );
        CREATE TABLE IF NOT EXISTS creative_content_revision(
          revision_id TEXT PRIMARY KEY,creative_id TEXT,event_id TEXT,run_id TEXT,created_at TEXT,payload TEXT
        );
        CREATE TABLE IF NOT EXISTS topic_risk(
          topic_id TEXT,fingerprint TEXT,run_id TEXT,created_at TEXT,payload TEXT,PRIMARY KEY(topic_id,fingerprint)
        );
        CREATE TABLE IF NOT EXISTS topic_risk_revision(
          revision_id TEXT PRIMARY KEY,topic_id TEXT,fingerprint TEXT,run_id TEXT,created_at TEXT,payload TEXT
        );
        CREATE TABLE IF NOT EXISTS game_signal(
          signal_id TEXT PRIMARY KEY,topic_id TEXT,fingerprint TEXT,run_id TEXT,created_at TEXT,payload TEXT
        );
        CREATE TABLE IF NOT EXISTS material_context(
          material_id TEXT PRIMARY KEY,topic_id TEXT,fingerprint TEXT,run_id TEXT,payload TEXT
        );
        CREATE TABLE IF NOT EXISTS material_application_run(
          material_id TEXT,topic_id TEXT,fingerprint TEXT,run_id TEXT,created_at TEXT,payload TEXT,
          PRIMARY KEY(material_id,topic_id,fingerprint,run_id)
        );
        INSERT OR IGNORE INTO material_application_run
          SELECT c.material_id,c.topic_id,c.fingerprint,c.run_id,m.created_at,c.payload
          FROM material_context c JOIN material m USING(material_id);
        CREATE TABLE IF NOT EXISTS research_capture(
          capture_id TEXT PRIMARY KEY,topic_id TEXT,evidence_id TEXT,created_at TEXT,status TEXT,payload TEXT
        );
        """)
        if "eligible" not in {r[1] for r in self.conn.execute("PRAGMA table_info(topic)")}:
            self.conn.execute("ALTER TABLE topic ADD COLUMN eligible INTEGER DEFAULT 0")
            self.conn.commit()

    def source_assets(self,query="",kind="",limit=60,*,evidence_ids=None):
        clauses,params=[],[]
        if kind:
            clauses.append("kind=?");params.append(kind)
        if evidence_ids is not None:
            if not evidence_ids:return []
            ids=list(dict.fromkeys(evidence_ids[:20]))
            clauses.append("evidence_id IN ("+",".join("?" for _ in ids)+")");params.extend(ids)
        for term in query.split()[:6]:
            pattern="%"+term.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"
            clauses.append("(title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')");params.extend([pattern]*2)
        where=" WHERE "+" AND ".join(clauses) if clauses else ""
        result=[]
        for row in self.conn.execute("SELECT * FROM source_asset"+where+" ORDER BY last_seen_at DESC,asset_id LIMIT ?",[*params,max(1,min(100,int(limit)))]):
            item=dict(row)
            item["channels"]=[r[0] for r in self.conn.execute("SELECT channel_id FROM source_asset_channel WHERE asset_id=?",(row["asset_id"],))]
            result.append(item)
        return result

    def intelligence(self,topic_id,fingerprint=None):
        clause=" AND fingerprint=?" if fingerprint else ""
        params=[topic_id,fingerprint] if fingerprint else [topic_id]
        row=self.conn.execute("SELECT * FROM topic_intelligence WHERE topic_id=?"+clause+" ORDER BY created_at DESC,rowid DESC LIMIT 1",params).fetchone()
        return {**dict(row),"payload":json.loads(row["payload"])} if row else None

    def save_intelligence(self,topic_id,fingerprint,prompt_version,run_id,payload):
        """Called in the delivery transaction; retain a replaced business judgment."""
        old=self.conn.execute('SELECT * FROM topic_intelligence WHERE topic_id=? AND fingerprint=? AND prompt_version=?',
            (topic_id,fingerprint,prompt_version)).fetchone()
        if old and old['run_id']!=run_id:
            self.conn.execute('INSERT OR IGNORE INTO topic_intelligence_revision VALUES(?,?,?,?,?,?,?)',
                (stable_id('intel_revision_',dump(dict(old))),topic_id,fingerprint,prompt_version,old['run_id'],old['created_at'],old['payload']))
        self.conn.execute('INSERT INTO topic_intelligence VALUES(?,?,?,?,?,?) ON CONFLICT(topic_id,fingerprint,prompt_version) DO UPDATE SET run_id=excluded.run_id,created_at=excluded.created_at,payload=excluded.payload',
            (topic_id,fingerprint,prompt_version,run_id,now_iso(),dump(payload)))

    def intelligence_feed(self,limit=30):
        return [{**dict(r),"payload":json.loads(r["payload"]),'risk_assessment':self.event_risk({'topic_id':r['topic_id'],'topic_fingerprint':r['fingerprint']})} for r in self.conn.execute("""SELECT i.*,t.title,
          t.fingerprint AS current_fingerprint FROM topic_intelligence i JOIN topic t USING(topic_id)
          ORDER BY i.created_at DESC LIMIT ?""",(max(1,min(100,int(limit))),))]

    def interpretation(self,topic_id,fingerprint):
        row=self.conn.execute('SELECT * FROM topic_interpretation WHERE topic_id=? AND fingerprint=?',
            (topic_id,fingerprint)).fetchone()
        return {**dict(row),'payload':json.loads(row['payload'])} if row else None

    def hotspot_feed(self,limit=30,domain=''):
        rows=self.conn.execute("""SELECT i.*,t.fingerprint AS current_fingerprint,t.last_seen_at FROM topic_interpretation i
          JOIN topic t USING(topic_id) WHERE t.eligible=1 AND i.fingerprint=t.fingerprint
          ORDER BY t.last_seen_at DESC LIMIT 100""")
        result=[]
        for row in rows:
            item={**dict(row),'payload':json.loads(row['payload'])}
            if item['payload']['status']!='ready' or not item['payload'].get('heat_evidence'):continue
            from .freshness import delivery_current
            from .discovery import read_topic
            freshness=delivery_current(self,read_topic(self,item['topic_id'],include_tracking=False),item['payload'])
            if not freshness['business_eligible'] or not freshness['heat_evidence']:continue
            item['payload']['freshness_assessment']=freshness
            if domain:
                from .connectors import CHANNELS
                channels={c['id'] for c in CHANNELS if domain in c.get('domains',[c['domain']])}
                if not any(h.get('channel_id') in channels for h in item['payload']['heat_evidence']):continue
            result.append(item)
            if len(result)>=limit:break
        return result

    def game_signals(self,limit=30):
        return [{**dict(r),'payload':json.loads(r['payload']),'risk_assessment':self.event_risk({'topic_id':r['topic_id'],'topic_fingerprint':r['fingerprint']})} for r in self.conn.execute("""SELECT s.*,t.fingerprint AS current_fingerprint
          FROM game_signal s JOIN topic t USING(topic_id) WHERE s.fingerprint=t.fingerprint AND
          s.run_id=(SELECT i.run_id FROM topic_intelligence i WHERE i.topic_id=s.topic_id AND i.fingerprint=s.fingerprint ORDER BY i.created_at DESC,i.rowid DESC LIMIT 1)
          AND COALESCE(json_extract(s.payload,'$.validation.status'),'accepted')='accepted'
          ORDER BY s.created_at DESC LIMIT ?""",(limit,))]

    def usable_materials(self,query='',limit=60,*,kind=''):
        return [m for m in self.assets(query,kind,limit=100) if m.get('revision_state')!='historical' and m.get('risk_assessment',{}).get('material_allowed')
                and (m.get('application') or m['kind'] in ('copy','script','visual_brief','production_checklist'))][:limit]

    def event_risk(self,assessment):
        from .risk import policy,verdict
        tid=assessment.get('topic_id');fp=assessment.get('topic_fingerprint')
        current=self.conn.execute('SELECT fingerprint FROM topic WHERE topic_id=?',(tid,)).fetchone()
        if not current or current[0]!=fp:
            return verdict({'polarity':'unknown','level':'unknown','reason':'当前来源版本与创意依据不一致，待重新评估'})
        return policy(self,tid,fp)

    def get_event(self,event_id):
        event=super().get_event(event_id)
        if event:
            event['risk_assessment']=self.event_risk(event['assessment'])
            if not event['risk_assessment']['growth_allowed'] and event['verdict']=='related':event['verdict']='adjacent'
        return event

    def events(self,limit=50):
        result=super().events(limit)
        for event in result:
            event['risk_assessment']=self.event_risk(event['assessment'])
            if not event['risk_assessment']['growth_allowed'] and event['verdict']=='related':event['verdict']='adjacent'
        return result

    def creative_basis(self,row):
        version=self.conn.execute('SELECT assessment FROM event_version WHERE event_id=? AND run_id=?',
            (row['event_id'],row['run_id'])).fetchone()
        if version:return json.loads(version[0])
        current=self.conn.execute('SELECT assessment FROM event WHERE event_id=?',(row['event_id'],)).fetchone()
        return json.loads(current[0]) if current else {}

    def creative_feed(self,limit=100,*,held=False):
        result=[]
        for row in self.conn.execute('SELECT c.*,e.assessment FROM creative c JOIN event e USING(event_id) ORDER BY c.created_at DESC'):
            safety=self.event_risk(self.creative_basis(row))
            if safety['growth_allowed']==held:continue
            item=dict(row);item.pop('assessment');item.update(payload=json.loads(row['payload']),risk_assessment=safety)
            result.append(item)
            if len(result)>=limit:break
        return result

    def assets(self, query="", kind="", limit=60):
        clauses, params = [], []
        if kind:
            clauses.append("kind=?"); params.append(kind)
        for term in query.split()[:6]:
            pattern="%"+term.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"
            clauses.append("(title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\')")
            params.extend([pattern]*3)
        where=" WHERE "+" AND ".join(clauses) if clauses else ""
        rows=self.conn.execute("SELECT * FROM material"+where+" ORDER BY created_at DESC LIMIT ?",[*params,max(1,min(100,int(limit)))])
        result=[]
        for row in rows:
            item={**dict(row),'tags':json.loads(row['tags']),'evidence_ids':json.loads(row['evidence_ids']),'source_versions':json.loads(row['source_versions'])}
            application=self.conn.execute('SELECT * FROM material_application_run WHERE material_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(row['material_id'],)).fetchone()
            if application:item['application']=json.loads(application['payload'])
            from .risk import policy,verdict
            if application:
                item['risk_assessment']=self.event_risk({'topic_id':application['topic_id'],'topic_fingerprint':application['fingerprint']})
            else:item['risk_assessment']=verdict({'polarity':'unknown','level':'unknown','reason':'素材尚未关联通过风险评估的事件'})
            if row['creative_id']:
                current=self.conn.execute('SELECT c.* FROM creative c WHERE creative_id=?',(row['creative_id'],)).fetchone()
                if current:
                    item['risk_assessment']=self.event_risk(self.creative_basis(current))
                    item['revision_state']='current' if any(d.get('kind')==row['kind'] and d.get('content')==row['content']
                        for d in json.loads(current['payload']).get('deliverables',[])) else 'historical'
            result.append(item)
        return result

    def evidence(self,ids):
        rows=super().evidence(ids)
        from .public_sources import SCOPES
        for row in rows:
            if row['kind']=='comment':row['content_scope']='comment_sample'
            elif row['platform']=='taptap' and row['kind'] in ('post','content'):row['content_scope']='post_excerpt'
            if row['source_path'] in SCOPES:row['content_scope']=SCOPES[row['source_path']]
            if row['source_path']=='v3:research:web_search':row['content_scope']='search_result_excerpt'
            if row['source_path']=='v3:read_source:web':row['content_scope']='article_excerpt'
            if row['source_path']=='v3:read_source:browser':row['content_scope']='browser_page_excerpt'
            if row["source_path"]=="v2:read_source":
                row["content_scope"]="video_description" if row["platform"]=="bilibili" else "article_excerpt"
            read=self.conn.execute('SELECT * FROM source_read WHERE evidence_id=?',(row['evidence_id'],)).fetchone()
            row['reading']=dict(read) if read else None
            metadata=self.conn.execute('SELECT * FROM source_read_meta WHERE evidence_id=?',(row['evidence_id'],)).fetchone()
            detail=json.loads(metadata['payload']) if metadata and metadata['source_version']==row['content_hash'] else None
            row['reading_metadata']=detail if detail and detail.get('requested_url',detail.get('resolved_url'))==row['url'] else None
            if row['kind']=='comment' and row['reading_metadata']:row['content_scope']=detail['scope']
            if row['reading_metadata'] and row['source_path']=='v3:read_source:browser':row['content_scope']=detail['scope']
            for observation in row["observations"]:
                observation["metrics"].pop("rank",None)
                observation["metrics"].pop("hot_score",None)
        return rows

    def growth_drafts(self,limit=12):
        return [i for r in self.conn.execute("""SELECT d.*,t.title AS topic_title,
          t.fingerprint AS current_fingerprint FROM creative_draft d JOIN topic t USING(topic_id)
          WHERE d.status<>'completed' ORDER BY d.created_at DESC LIMIT ?""",(max(1,min(30,int(limit))),))
          if (i:={**dict(r),'payload':json.loads(r['payload'])}) and self.event_risk({'topic_id':r['topic_id'],'topic_fingerprint':r['fingerprint']})['growth_allowed']]

    def save_material(self, item, run_id, *, event_id=None, creative_id=None):
        ids=item.get("evidence_ids",[])
        versions={eid:self.snapshot(eid) for eid in sorted(ids)}
        key=stable_id("material_",dump([item["kind"],item["origin"],item["title"],item["content"],sorted(ids),versions,item["rights_status"]]))
        # Retain IDs created during earlier alpha builds when identity gains fields.
        for previous in self.conn.execute("SELECT material_id,evidence_ids,source_versions FROM material WHERE kind=? AND origin=? AND title=? AND content=? AND rights_status=?",
                                          (item["kind"],item["origin"],item["title"],item["content"],item["rights_status"])):
            if sorted(json.loads(previous["evidence_ids"]))==sorted(ids) and json.loads(previous["source_versions"])==versions:
                key=previous["material_id"];break
        self.conn.execute("INSERT OR IGNORE INTO material VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (key,item["kind"],item["origin"],item["title"],item["content"],dump(item.get("tags",[])),dump(ids),dump(versions),item["rights_status"],now_iso(),run_id,event_id,creative_id))
        if event_id:
            self.conn.execute("INSERT OR IGNORE INTO material_use VALUES(?,?,?,?)",(key,event_id,creative_id or "",run_id))
        return key

    def insight(self, evidence_id, content_hash, version="insight-v3.1"):
        row=self.conn.execute("SELECT payload FROM insight WHERE evidence_id=? AND content_hash=? AND prompt_version=?",(evidence_id,content_hash,version)).fetchone()
        return json.loads(row[0]) if row else None

    def save_assessments(self, run_id, assessments):
        from .risk import require_growth
        for assessment in assessments:
            if assessment.get('creatives'):require_growth(self,assessment.get('topic_id'),assessment.get('topic_fingerprint'))
        with self.conn:
            events=[]
            for assessment in assessments:
                event_id=super().save_assessment(run_id,assessment)
                events.append(event_id)
                for creative in assessment.get("creatives",[]):
                    creative_id=stable_id("creative_",event_id+"|"+json.dumps(creative,sort_keys=True,ensure_ascii=False))
                    for material in creative["deliverables"]:
                        self.save_material(material,run_id,event_id=event_id,creative_id=creative_id)
                    for mid in creative.get("reuse_material_ids",[]):
                        self.conn.execute("INSERT OR IGNORE INTO material_use VALUES(?,?,?,?)",(mid,event_id,creative_id,run_id))
                    for sid in creative.get("reuse_source_asset_ids",[]):
                        self.conn.execute("INSERT OR IGNORE INTO source_asset_use VALUES(?,?,?)",(sid,creative_id,run_id))
                topic_id=assessment.get("topic_id")
                if topic_id:
                    self.conn.execute("UPDATE topic SET reviewed_fingerprint=?,decision=?,event_id=?,reviewed_at=? WHERE topic_id=?",
                                      (assessment["topic_fingerprint"],assessment["verdict"],event_id,now_iso(),topic_id))
            return events

    def overview(self):
        data=super().overview()
        data["version"]=__version__
        for table in ("topic","insight","material","cycle","source_asset","topic_intelligence","creative_draft","game_signal","topic_interpretation"):
            data["counts"][table]=self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        from datetime import datetime, timedelta, timezone
        cutoff=(datetime.now(timezone.utc)-timedelta(hours=168)).isoformat(timespec="seconds")
        context_version=stable_id('context_',dump(self.context()))
        pending=self.conn.execute('''SELECT COUNT(*) FROM topic t LEFT JOIN main_decision d ON d.topic_id=t.topic_id
          AND d.fingerprint=t.fingerprint AND d.context_version=? WHERE t.eligible=1 AND t.last_seen_at>=? AND d.topic_id IS NULL''',(context_version,cutoff)).fetchone()[0]
        data['counts']['pending_topics']=pending
        decisions={r['action']:r['n'] for r in self.conn.execute('''SELECT json_extract(d.payload,'$.action') AS action,COUNT(*) AS n
          FROM main_decision d JOIN topic t ON t.topic_id=d.topic_id AND t.fingerprint=d.fingerprint
          WHERE d.context_version=? AND t.eligible=1 AND t.last_seen_at>=? GROUP BY action''',(context_version,cutoff))}
        data['automatic_progress']={'unscreened':pending,'decisions':decisions,'window_days':7,
            'note':'候选先自动筛选，近期且有价值的内容才研究；情报、素材、创意分别判断，允许为空'}
        data["counts"]["assisted_deliveries"]=self.conn.execute("SELECT COUNT(*) FROM run WHERE status='completed' AND json_extract(result,'$.execution_mode')='interactive_assisted'").fetchone()[0]
        data["counts"]["autonomous_deliveries"]=self.conn.execute("SELECT COUNT(*) FROM run WHERE status='completed' AND COALESCE(json_extract(result,'$.execution_mode'),'autonomous')<>'interactive_assisted'").fetchone()[0]
        data['counts']['autonomous_creatives']=self.conn.execute("SELECT COUNT(*) FROM creative WHERE COALESCE(json_extract(payload,'$.execution_mode'),'autonomous')<>'interactive_assisted'").fetchone()[0]
        data['counts']['assisted_creatives']=self.conn.execute("SELECT COUNT(*) FROM creative WHERE json_extract(payload,'$.execution_mode')='interactive_assisted'").fetchone()[0]
        data['creatives']=self.creative_feed(30)
        data['held_creatives']=[{k:v for k,v in c.items() if k!='payload'}|{'title':c['payload']['title']} for c in self.creative_feed(100,held=True)]
        data['counts']['held_creatives']=len(self.creative_feed(100,held=True))
        data['counts']['autonomous_creatives']=sum(c['payload'].get('execution_mode')!='interactive_assisted' for c in self.creative_feed())
        data['counts']['assisted_creatives']=sum(c['payload'].get('execution_mode')=='interactive_assisted' for c in self.creative_feed())
        data["cycles"]=[{**dict(r),"collection":json.loads(r["collection"] or "{}"),"discovery":json.loads(r["discovery"] or "{}"),"run_ids":json.loads(r["run_ids"] or "[]")} for r in self.conn.execute("SELECT * FROM cycle ORDER BY started_at DESC LIMIT 12")]
        data["intelligence"]=self.intelligence_feed()
        data['game_signals']=self.game_signals()
        data['counts']['game_signal_records']=data['counts']['game_signal']
        data['counts']['game_signal']=len(self.game_signals(1000))
        data['hotspots']=self.hotspot_feed()
        from .presentation import game_coverage
        data['game_coverage']=game_coverage(self)
        data['counts']['hotspots']=len(self.hotspot_feed(limit=100))
        data['counts']['usable_materials']=len(self.usable_materials(limit=100))
        data["growth_drafts"]=self.growth_drafts()
        data["source_reads"]=[dict(r) for r in self.conn.execute("SELECT * FROM source_read ORDER BY attempted_at DESC LIMIT 12")]
        data['reading_summary']=[dict(r) for r in self.conn.execute('''SELECT e.platform,r.status,r.scope,COUNT(*) AS count
          FROM source_read r JOIN evidence e USING(evidence_id) GROUP BY e.platform,r.status,r.scope''')]
        data['counts']['discussion_samples']=self.conn.execute('SELECT COUNT(*) FROM discussion_sample').fetchone()[0]
        return data


def migrate_v2(store, path=None):
    """Read V2 through a read-only connection; migrate evidence, never old guesses."""
    import sqlite3
    source=Path(path) if path else ROOT / "data/v2/agent.sqlite3"
    if not source.is_file():
        return {"status":"missing","inserted":0}
    previous=sqlite3.connect(source.resolve().as_uri()+"?mode=ro",uri=True)
    previous.row_factory=sqlite3.Row
    inserted=0
    try:
        with store.conn:
            for row in previous.execute("SELECT * FROM evidence"):
                observation=previous.execute("SELECT metrics FROM observation WHERE evidence_id=? ORDER BY observed_at DESC LIMIT 1",(row["evidence_id"],)).fetchone()
                inserted+=store.upsert_evidence({**dict(row),"metrics":json.loads(observation[0]) if observation else {}})
            for row in previous.execute("SELECT * FROM observation"):
                store.conn.execute("INSERT OR IGNORE INTO observation VALUES(?,?,?)",tuple(row))
        return {"status":"ok","inserted":inserted,"source":"V2 evidence only; V2 research/creatives remain in V2"}
    finally:
        previous.close()
