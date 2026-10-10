"""Isolated graph fixtures: provenance, identity, abstention and useful retrieval."""
import copy
import json
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch

from agent_v2.store import now_iso,dump,stable_id
from agent_v3.store import Store
from agent_v3 import knowledge_graph as kg,graph_retrieval as retrieval,graph_entities,graph_ai,tracking
from agent_v3.discovery import scan,read_topic
from agent_v3.runtime_guard import basis,StaleBasis
from agent_v3.tool_executor import ToolExecutor,history
from agent_v3.contracts import AgentContract
from agent_v3.task_packets import topic_packet
from agent_v3.tests.test_v3 import source
from agent_v3.tests import test_main_agent as main_fixtures


class GraphTests(unittest.TestCase):
    def setUp(self):self.s=Store(':memory:');self.rid=self.s.create_run('isolated graph fixture')
    def tearDown(self):self.s.close()

    def document(self,title,body=None):
        item=source(title);item.update(evidence_id=stable_id('evidence_',title),body=body or title+'。这是离线测试中的具体游戏事件正文，描述主体、行动与用户需求，不代表真实热点或全体玩家。',
            published_at=now_iso(),url='https://example.test/'+stable_id('',title),source_path='v3:read_source:web',kind='news')
        self.s.upsert_evidence(item)
        with self.s.conn:self.s.conn.execute('INSERT INTO channel_observation VALUES(?,?,?,?,?)',('baidu:game',item['evidence_id'],now_iso(),1,'{}'))
        scan(self.s);tracking.observe(self.s);kg.refresh(self.s)
        tid=self.s.conn.execute('SELECT topic_id FROM topic_member WHERE evidence_id=? AND active=1',(item['evidence_id'],)).fetchone()[0]
        event=self.s.conn.execute('SELECT tracked_id FROM tracked_member WHERE evidence_id=?',(item['evidence_id'],)).fetchone()[0]
        return item['evidence_id'],tid,event

    def pair(self):
        a=self.document('明日方舟公布游戏活动');b=self.document('明日方舟回应活动规则')
        left,right=sorted((a[2],b[2]));versions=[self.s.conn.execute('SELECT revision FROM tracked_event WHERE tracked_id=?',(e,)).fetchone()[0] for e in (left,right)]
        key=stable_id('pair_',dump([left,right,*versions]));stamp=now_iso()
        with self.s.conn:self.s.conn.execute('INSERT INTO event_relation VALUES(?,?,?,?,?,?,?,?,?,?,?)',(key,left,right,*versions,.91,'fixture','pending','离线关系夹具',stamp,stamp))
        quotes=[self.s.evidence([self.s.conn.execute('SELECT anchor_evidence_id FROM tracked_event WHERE tracked_id=?',(e,)).fetchone()[0]])[0]['body'] for e in (left,right)]
        return a,b,key,quotes

    def need(self,doc,label='find_players'):
        item=kg.source(self.s,doc[0]);fact=kg.proof(item,item['body'])
        kg.ingest_interpretation(self.s,doc[1],read_topic(self.s,doc[1])['fingerprint'],{'knowledge':{'needs':[{'label':label,'text':'该来源中的玩家寻求共同参与的伙伴','facts':[fact]}]}},self.rid)
        self.s.conn.commit()

    def community(self):
        a=self.document('明日方舟玩家分享组队想法');b=self.document('原神玩家分享结伴游玩想法')
        self.need(a);self.need(b);kg.refresh(self.s)
        return a,b,retrieval.global_context(self.s)['communities'][0]

    def test_ambiguous_alias_abstains_without_game_context(self):
        cases=[('方舟','海上方舟',None),('方舟','明日方舟干员','game_arknights'),('悟空','西游记故事',None),
            ('悟空','黑神话游戏科学','game_black_myth_wukong'),('LOL','lolcat',None),('LOL','英雄联盟赛事','game_lol'),('崩铁','','game_star_rail')]
        for name,context,wanted in cases:
            with self.subTest(name=name,context=context):self.assertEqual((graph_entities.resolve(self.s,name,context)['entity'] or {}).get('entity_id'),wanted)

    def test_alias_collisions_remain_unresolved(self):
        with self.s.conn:self.s.conn.execute('INSERT INTO kg_alias VALUES(?,?,0)',('崩铁','game_genshin'))
        self.assertEqual(graph_entities.resolve(self.s,'崩铁')['status'],'unresolved')

    def test_long_names_win_and_ascii_boundaries_apply(self):
        for name in ('黑神话：悟空','崩坏：星穹铁道','明日方舟：终末地','Android','库洛游戏'):
            self.assertEqual(graph_entities.resolve(self.s,name)['status'],'linked')
        mentions=graph_entities.mentions(self.s,'明日方舟游戏中提到方舟。lolcat只是名字，LOL 英雄联盟赛事。')
        self.assertEqual(sum(m['surface']=='明日方舟' for m in mentions),1)
        self.assertFalse(any(m['surface']=='lol' for m in mentions))
        self.assertTrue(any(m['surface']=='LOL' for m in mentions))

    def test_document_index_is_incremental_and_registry_versioned(self):
        doc=self.document('崩铁版本内容公开');first=kg.refresh(self.s)
        mentions=self.s.conn.execute('SELECT COUNT(*) FROM kg_mention').fetchone()[0]
        self.assertEqual(first['updated_documents'],0);self.assertEqual(first['graph_version'],kg.refresh(self.s)['graph_version'])
        with self.s.conn:self.s.conn.execute("UPDATE kg_index_state SET version='fixture-registry-change' WHERE name='registry'")
        self.assertEqual(kg.refresh(self.s)['updated_documents'],1)
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM kg_mention').fetchone()[0],mentions)

    def test_proof_rejects_invented_quote(self):
        doc=self.document('原神公布新版本');self.assertRaises(ValueError,kg.proof,kg.source(self.s,doc[0]),'来源根本不存在的句子')

    def test_body_url_and_date_each_invalidate_provenance_immediately(self):
        for field in ('body','url','published_at'):
            with self.subTest(field=field):
                doc=self.document('原神来源版本测试'+field);item=kg.source(self.s,doc[0]);fact=kg.proof(item,item['body'])
                kg.store_claim(self.s,doc[2],'来源归属主张',[fact]);self.s.conn.commit()
                self.s.conn.execute('UPDATE evidence SET '+field+'=? WHERE evidence_id=?',('更改后的值'+item[field],doc[0]));self.s.conn.commit()
                self.assertFalse(kg.current(self.s,fact));self.assertFalse(retrieval.local_context(self.s,topic_id=doc[1])['events'])

    def test_development_is_edge_not_identity_and_withdraw_revokes_it(self):
        a,b,key,quotes=self.pair();tracking.decide(self.s,key,'development','来源明确描述后续回应','fixture',quotes=quotes,development_from='left')
        kg.refresh(self.s);context=retrieval.local_context(self.s,topic_id=a[1])
        self.assertEqual(len(context['events']),2);self.assertEqual(context['relations'][0]['predicate'],'FOLLOWED_BY')
        self.assertEqual(tracking.root_id(self.s,a[2]),a[2]);self.assertEqual(tracking.root_id(self.s,b[2]),b[2])
        self.assertFalse(any(r['predicate']=='CAUSED_BY' for r in context['relations']))
        tracking.withdraw(self.s,key,'来源判断需要重新核查','fixture')
        self.assertFalse(retrieval.local_context(self.s,topic_id=a[1])['relations'])
        kg.refresh(self.s)
        self.assertFalse(retrieval.local_context(self.s,topic_id=a[1])['relations'])

    def test_development_unknown_direction_never_invents_sequence(self):
        a,b,key,quotes=self.pair();tracking.decide(self.s,key,'development','后续关系方向尚待核查','fixture',quotes=quotes)
        kg.refresh(self.s);self.assertEqual(retrieval.local_context(self.s,topic_id=a[1])['relations'][0]['predicate'],'DEVELOPMENT')

    def test_unquoted_manual_relation_is_not_used_for_retrieval_or_community(self):
        a,b,key,quotes=self.pair();tracking.decide(self.s,key,'related','主题关联没有逐字引文','fixture');kg.refresh(self.s)
        self.assertFalse(retrieval.local_context(self.s,topic_id=a[1])['relations']);self.assertFalse(retrieval.global_context(self.s)['communities'])
        self.assertTrue(retrieval.local_context(self.s,topic_id=a[1])['pending_relations'])

    def test_legacy_development_merge_is_split_automatically_and_idempotently(self):
        a,b,key,quotes=self.pair();tracking.decide(self.s,key,'same','模拟旧版后续进展合并','fixture',quotes=quotes)
        with self.s.conn:self.s.conn.execute("UPDATE event_relation SET status='development' WHERE pair_id=?",(key,))
        self.assertEqual(tracking.root_id(self.s,a[2]),tracking.root_id(self.s,b[2]))
        self.assertEqual(tracking.migrate_development(self.s,self.rid)['split_development'],1)
        self.assertNotEqual(tracking.root_id(self.s,a[2]),tracking.root_id(self.s,b[2]))
        self.assertEqual(tracking.migrate_development(self.s,self.rid)['status'],'current')
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0],2)

    def test_shared_game_is_not_event_merge_or_theme(self):
        a=self.document('原神发布音乐会');b=self.document('原神玩家测评手机性能')
        self.assertNotEqual(a[2],b[2]);self.assertFalse(retrieval.global_context(self.s)['communities'])

    def test_cross_game_need_forms_theme_but_not_hotspot(self):
        a,b,context=self.community()
        self.assertEqual(set(context['event_ids']),{a[2],b[2]});self.assertFalse(context['heat']['qualified_as_hotspot'])
        self.assertEqual(context['summary_kind'],'structured_index');self.assertIsNone(context['ai_summary'])
        self.assertTrue(all(kg.current(self.s,f) for f in context['facts']))

    def test_changed_source_hides_community_before_rebuild(self):
        a,b,context=self.community()
        with self.s.conn:self.s.conn.execute('UPDATE evidence SET body=? WHERE evidence_id=?',('来源正文变化',a[0]))
        self.assertFalse(retrieval.global_context(self.s)['communities'])

    def test_expired_event_is_excluded_and_theme_superseded(self):
        a,b,context=self.community();old=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat(timespec='seconds')
        with self.s.conn:
            self.s.conn.execute('UPDATE kg_event SET last_seen_at=? WHERE event_id=?',(old,a[2]))
        self.assertFalse(retrieval.global_context(self.s)['communities']);retrieval.rebuild_communities(self.s)
        self.assertEqual(self.s.conn.execute('SELECT state FROM kg_community WHERE community_id=?',(context['community_id'],)).fetchone()[0],'superseded')

    def test_local_tools_are_readonly_bounded_and_audited_with_zero_network_units(self):
        doc=self.document('原神分享创作活动');self.s.acquire(self.rid);executor=ToolExecutor(self.s,self.rid,limit=0)
        before={t:self.s.conn.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('kg_entity','kg_relation','tracked_event','evidence')}
        for name in retrieval.TOOLS:
            result=executor.execute(name,{'topic_id':doc[1],'query':'原神'});self.assertEqual(result['calls'],0)
        self.assertEqual(len(history(self.s,self.rid)),5);self.assertTrue(all(r['charged_units']==0 for r in history(self.s,self.rid)))
        self.assertTrue(any(r['sources'] for r in history(self.s,self.rid)))
        self.assertEqual(before,{t:self.s.conn.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in before})
        self.assertRaises(ValueError,executor.execute,'write_sql',{'topic_id':doc[1]})
        self.assertRaises(ValueError,retrieval.local_context,self.s,topic_id=doc[1],limit=100)
        self.s.release(self.rid)

    def test_unknown_entity_is_source_scoped_proposal_not_global_alias(self):
        doc=self.document('原神创作者小海展示玩法');item=kg.source(self.s,doc[0]);fact=kg.proof(item,item['body'])
        value={'knowledge':{'entities':[{'surface':'小海','canonical_name':'小海','kind':'CREATOR','facts':[fact]}]}}
        kg.ingest_interpretation(self.s,doc[1],'fixture',value);self.s.conn.commit()
        self.assertEqual(graph_entities.resolve(self.s,'小海')['status'],'unresolved')
        self.assertEqual(self.s.conn.execute("SELECT COUNT(*) FROM kg_entity WHERE origin='proposed'").fetchone()[0],1)
        value['knowledge']['entities'][0]['canonical_name']='某真实知名作者'
        self.assertRaises(ValueError,kg.ingest_interpretation,self.s,doc[1],'fixture',value)

    def test_entity_and_relation_refs_are_checked_beyond_provider_schema(self):
        doc=self.document('原神活动公开');fact=kg.proof(kg.source(self.s,doc[0]),'原神活动公开')
        value={'knowledge':{'entities':[{'surface':'原神','canonical_name':'原神','kind':'GAME','facts':[fact]}],
            'relations':[{'subject_ref':9,'object_ref':0,'target':'event','predicate':'ABOUT','facts':[fact]}]}}
        self.assertRaises(ValueError,kg.ingest_interpretation,self.s,doc[1],'fixture',value)
        value['knowledge']['relations'][0].update(subject_ref=0,predicate='CAUSED_BY')
        self.assertRaises(ValueError,kg.ingest_interpretation,self.s,doc[1],'fixture',value)

    def test_conflicting_relation_review_retained_without_certifying_model_guess(self):
        doc=self.document('原神活动延期回应');fact=kg.proof(kg.source(self.s,doc[0]),'原神活动延期回应')
        key=kg.relation(self.s,'game_genshin','ABOUT',doc[2],'proposed',[fact]);self.s.conn.commit()
        kg.ingest_interpretation(self.s,doc[1],'fixture',{'knowledge':{'relation_reviews':[{'relation_id':key,'decision':'contradicted','reason':'来源反驳旧假设','facts':[fact]}]}});self.s.conn.commit()
        self.assertEqual(self.s.conn.execute('SELECT status FROM kg_relation WHERE relation_id=?',(key,)).fetchone()[0],'contradicted')
        self.assertFalse(retrieval.local_context(self.s,topic_id=doc[1])['relations'])

    def test_graph_source_versions_fence_delivery_even_without_scan(self):
        doc=self.document('原神版本发布');packet=topic_packet(read_topic(self.s,doc[1]),self.s.context());retrieval.attach(self.s,packet,doc[1])
        expected=basis(self.s,doc[1],packet=packet)
        with self.s.conn:self.s.conn.execute("UPDATE evidence SET url='https://example.test/changed' WHERE evidence_id=?",(doc[0],))
        self.assertRaises(StaleBasis,self.s.check_basis,expected)

    def test_community_summary_is_grounded_cached_and_child_role(self):
        a,b,context=self.community()
        class Model:
            model='offline-fixture';calls=0
            def run_task(inner,stage,packet,schema,system,**kwargs):
                self.assertEqual(stage,'community_summary');inner.calls+=1
                return {'result':{'headline':'两个游戏来源出现结伴参与需求','one_line':'有限来源中均提到寻求共同参与的伙伴，不能外推全体玩家。','findings':[{'text':'两条来源提供共同需求线索','basis_refs':[0,1]}],'unknowns':['需求普遍程度未确认']}}
        model=Model();graph_ai.summarize(self.s,self.rid,model);graph_ai.summarize(self.s,self.rid,model)
        self.assertEqual(model.calls,1);saved=retrieval.global_context(self.s)['communities'][0]['ai_summary']
        self.assertEqual(saved['agent_role'],'research_child');self.assertTrue(saved['findings'][0]['facts'])

    def test_invalid_summary_reference_is_not_persisted(self):
        self.community()
        class Model:
            model='offline-fixture'
            def run_task(inner,*args,**kwargs):return {'result':{'headline':'两个游戏来源出现结伴参与需求','one_line':'仅为测试摘要，不代表真实需求。','findings':[{'text':'虚构引用','basis_refs':[999]}],'unknowns':[]}}
        self.assertRaises(ValueError,graph_ai.summarize,self.s,self.rid,Model())
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM kg_community_summary').fetchone()[0],0)

    def test_community_rebuild_invalidates_summary_versions(self):
        a,b,context=self.community();guard=retrieval.guard(self.s,{'communities':[context]})
        self.need(a,'creation');kg.refresh(self.s)
        # A changed source definitely invalidates the report even if an extra
        # non-shared need leaves the theme membership unchanged.
        with self.s.conn:self.s.conn.execute('UPDATE evidence SET published_at=? WHERE evidence_id=?',('2026-01-01T00:00:00+00:00',a[0]))
        self.assertRaises(StaleBasis,self.s.check_basis,guard)

    def test_public_projection_has_short_sources_and_no_proposed_entity_claims(self):
        from agent_v3.public_site import graph_context
        doc=self.document('原神资料与来源公开');value=retrieval.local_context(self.s,topic_id=doc[1])
        value['pending_relations']=[{'actor':'private','quote':'x'*300}]
        projected=graph_context(value)
        self.assertNotIn('pending_relations',projected);self.assertTrue(projected['sources'])
        self.assertNotIn('body',projected['sources'][0]);self.assertNotIn('content_hash',dump(projected))

    def test_existing_main_child_contract_accepts_explicit_empty_extraction(self):
        fixture=main_fixtures.MainAgentTests();fixture.setUp()
        try:
            model=fixture.model();original=model.run_task;packets=[]
            def run(stage,packet,schema,system,**kwargs):
                packets.append((stage,copy.deepcopy(packet)))
                result=original(stage,packet,schema,system,**kwargs)
                if stage=='graph_extraction':result['result']={'entities':[],'relations':[],'needs':[]}
                return result
            model.run_task=run
            from agent_v3.pipeline import process
            with patch('agent_v3.research.search_news',side_effect=AssertionError('no network')):result=process(fixture.store,fixture.rid,model=model,topic_id=fixture.tid)
            self.assertEqual(result['status'],'intelligence_ready')
            self.assertTrue(all('graph_context' in p for s,p in packets if s in ('main_plan','interpretation','intelligence')))
            self.assertEqual(AgentContract.create('community_summary',{}, {'type':'object'}).role,'research_child')
        finally:fixture.tearDown()

    def test_independent_child_extraction_requires_explicit_arrays(self):
        fixture=main_fixtures.MainAgentTests();fixture.setUp()
        try:
            from agent_v3.graph_ai import extraction_schema
            from jsonschema import ValidationError
            packet=topic_packet(read_topic(fixture.store,fixture.tid),fixture.store.context());contract_schema=extraction_schema(packet)
            value=fixture.model().run_task('graph_extraction',packet,contract_schema,'fixture')['result'];value.pop('needs')
            self.assertRaises(ValidationError,AgentContract.create('graph_extraction',packet,contract_schema).validate,value,packet)
        finally:fixture.tearDown()

    def test_transient_db_lock_does_not_kill_scheduler(self):
        import sqlite3
        from unittest.mock import MagicMock
        from agent_v3.service import scheduler_iteration
        store=MagicMock()
        with patch('agent_v3.service.Store',side_effect=[sqlite3.OperationalError('database is locked'),store]),patch('agent_v3.public_site.tick'),patch('agent_v3.service.scheduler_tick',return_value='waiting_provider'):
            with self.assertLogs('agent_v3.service',level='WARNING'):self.assertEqual(scheduler_iteration(),'retry')
            self.assertEqual(scheduler_iteration(),'waiting_provider')
        store.close.assert_called_once()


if __name__=='__main__':unittest.main()
