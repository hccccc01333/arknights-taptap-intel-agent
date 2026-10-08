"""Source enrichment, compact references and resumable creative production."""
import copy
import json
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator,ValidationError
from agent_v2.store import now_iso
from agent_v3 import work,enrichment
from agent_v3.store import Store
from agent_v3.discovery import scan,queue,read_topic
from agent_v3.pipeline import process
from agent_v3.engine import run_agent
from agent_v3.task_packets import topic_packet,intelligence_schema,intelligence_result,PLAN_SCHEMA
from agent_v3.tests.test_v3 import source,output,approve_test_topic


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(':memory:');self.item=source()
        self.item['published_at']=now_iso()
        self.item['body']='假期越来越多人希望放慢生活节奏，减少打卡和任务压力。这里是实际测试来源的正文语境，用来验证生活需求可以成为待测试的增长假设。'
        self.store.upsert_evidence(self.item);self.store.conn.commit();scan(self.store)
        self.tid=queue(self.store)[0]['topic_id'];self.eid=self.item['evidence_id'];approve_test_topic(self.store,self.tid)
        work.enqueue(self.store);self.rid=self.store.create_run('compact pipeline test')

    def tearDown(self):self.store.close()

    def intel(self):
        return {'summary':'假期慢生活需求可尝试低压力参与内容，关联效果待测试。','fact_refs':[1],
            'emotions':['放松需求是基于正文的推断'],'needs':['减少任务压力'],
            'spread_mechanics':['个人生活选择表达可能带动参与，尚未核实评论'],
            'content_forms':['短文'],'reusable_angles':['以低压力参与承接假期情绪'],
            'unknowns':['承接位置、流量和发布资源未确认'],'source_asset_refs':[],'patterns':[],
            'opportunity':{'decision':'opportunity','reason':'正文有慢生活需求，平台承接属于待测试假设',
                'audience_need':'假期希望减轻任务压力的人','taptap_bridge':'通过自主参与表达进入平台，入口需确认',
                'growth_goal':'验证感兴趣人群进入平台并参与内容',
                'hypothesis':'若用低压力参与内容吸引慢生活人群，则可能带来平台访问和内容参与',
                'validation_plan':'先用一条内容测试入口访问和参与，若无人进入平台则停止并修正桥梁',
                'prerequisites':['发布资源和真实承接入口先确认']}}

    def model(self,fail_production=False):
        outer=self;calls=[]
        class Native:
            supports_tasks=True;compact_tasks=True;model='test-compact';last_session='ses_test';production_failed=fail_production
            def run_task(self,stage,packet,schema,system,**kwargs):
                calls.append(stage)
                if stage=='intelligence':result=outer.intel()
                elif stage=='creative_plan':
                    c=output(outer.store,outer.tid)['assessments'][0]['creatives'][0]
                    result={k:copy.deepcopy(c[k]) for k in PLAN_SCHEMA['properties'] if k in c}
                    result.update({'steps':['先确认资源与入口','制作一条内容','记录进入和参与并根据结果修改'],
                                   'reuse_material_refs':[],'reuse_source_asset_refs':[]})
                else:
                    if self.production_failed:raise RuntimeError('test-only production interruption')
                    result={'copy':'假期没有打卡任务，给自己留一点轻松的时间。选一件你愿意慢慢做的事，分享给同样想放松的人。',
                            'script':'0–3秒：镜头拍摄自己写满任务的行程单，字幕“假期也要交作业？”；3–10秒：划掉任务，展示在家放松的自摄画面；10–20秒：邀请观众选一个无压力的放松方式；20–25秒：展示经确认的TapTap入口，邀请分享选择并进入讨论。',
                            'rights_notes':'使用自行拍摄画面和原创文字；平台入口及发布资源上线前确认。'}
                return {'model':self.model,'result':result,'usage':{'total_tokens':10},'seconds':0.1,
                    'transport':'opencode-agent','session_id':self.last_session,'input_file':'test-only'}
        result=Native();result.calls=calls;return result

    def test_non_game_demand_routes_to_hypothesis_plan_and_actual_production(self):
        model=self.model();result=process(self.store,self.rid,model=model)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(model.calls,['intelligence','creative_plan','creative_production'])
        c=self.store.overview()['creatives'][0]['payload']
        self.assertEqual({a['kind'] for a in c['deliverables']},{'copy','script'})
        self.assertEqual(c['readiness'],'unreviewed_draft')
        self.assertTrue(c['growth_hypothesis']);self.assertTrue(c['validation_plan'])
        self.assertEqual(self.store.conn.execute('SELECT status FROM creative_draft').fetchone()[0],'completed')

    def test_failed_production_preserves_plan_and_next_run_reuses_it(self):
        model=self.model(True);result=process(self.store,self.rid,model=model)
        self.assertEqual(result['status'],'ai_deferred')
        self.assertEqual(self.store.overview()['counts']['creative'],0)
        self.assertEqual(len(self.store.growth_drafts()),1)
        model.production_failed=False
        rid=self.store.create_run('resume production')
        result=run_agent(self.store,rid,model,assigned_topic=self.tid)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(model.calls.count('creative_plan'),1)
        self.assertEqual(self.store.growth_drafts(),[])

    def test_integer_source_references_decode_to_exact_stored_quotes(self):
        packet=topic_packet(read_topic(self.store,self.tid),self.store.context())
        value=intelligence_result(self.intel(),packet)
        self.assertEqual(value['facts'][0]['quote'],packet['quote_candidates'][1]['quote'])
        self.assertEqual(value['facts'][0]['evidence_id'],self.eid)
        bad=self.intel();bad['fact_refs']=[99]
        with self.assertRaises(ValidationError):intelligence_result(bad,packet)

    def test_empty_assets_cannot_fabricate_a_reference(self):
        packet=topic_packet(read_topic(self.store,self.tid),self.store.context());packet['source_assets']=[]
        bad=self.intel();bad['source_asset_refs']=[0]
        with self.assertRaises(ValidationError):Draft202012Validator(intelligence_schema(packet)).validate(bad)

    def test_title_only_cannot_become_an_opportunity(self):
        self.store.conn.execute("UPDATE evidence SET body='' WHERE evidence_id=?",(self.eid,));self.store.conn.commit();scan(self.store);work.enqueue(self.store)
        model=self.model();original=model.run_task
        def task(stage,*args,**kwargs):
            result=original(stage,*args,**kwargs);result['result']['fact_refs']=[0];return result
        model.run_task=task
        result=process(self.store,self.rid,model=model)
        self.assertEqual(result['status'],'ai_deferred');self.assertEqual(self.store.overview()['counts']['topic_intelligence'],0)

    def test_enrichment_changes_version_and_saves_actual_source_assets(self):
        self.store.conn.execute("UPDATE evidence SET platform='gamemedia' WHERE evidence_id=?",(self.eid,));self.store.conn.commit()
        old=read_topic(self.store,self.tid)['fingerprint']
        def fetch(store,eid):
            store.upsert_evidence({**self.item,'platform':'gamemedia','source_path':'v2:read_source','body':self.item['body']+'这段是补读所得正文。'})
            store.conn.commit();return {'status':'ok'}
        with patch('agent_v3.enrichment.read_source',side_effect=fetch) as reader:
            result=enrichment.prepare(self.store,topic_id=self.tid);self.assertEqual(result['calls'],1)
            self.assertEqual(enrichment.prepare(self.store,topic_id=self.tid)['calls'],0)
            reader.assert_called_once()
        scan(self.store);work.enqueue(self.store)
        self.assertNotEqual(read_topic(self.store,self.tid)['fingerprint'],old)
        self.assertEqual(self.store.evidence([self.eid])[0]['content_scope'],'article_excerpt')
        self.assertTrue(self.store.source_assets())

    def test_feed_keeps_fresh_detail_and_changed_article_requires_new_read(self):
        from datetime import datetime,timedelta,timezone
        item={**self.item,'platform':'gamemedia'}
        self.store.conn.execute("UPDATE evidence SET platform='gamemedia' WHERE evidence_id=?",(self.eid,));self.store.conn.commit()
        def fetch(store,eid):
            store.upsert_evidence({**item,'source_path':'v2:read_source','body':item['body']+'补读的新闻原文。'})
            store.conn.commit();return {'status':'ok'}
        with patch('agent_v3.enrichment.read_source',side_effect=fetch):enrichment.prepare(self.store,topic_id=self.tid)
        before=self.store.evidence([self.eid])[0]
        newer=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat(timespec='seconds')
        self.store.upsert_evidence({**item,'body':'下一轮渠道只返回较短的摘要内容。','last_seen_at':newer,'metrics':{'views':123}})
        self.store.conn.commit()
        retained=self.store.evidence([self.eid])[0]
        self.assertEqual(retained['body'],before['body'])
        self.assertEqual(retained['content_hash'],before['content_hash'])
        self.assertEqual(retained['content_scope'],'article_excerpt')
        self.assertEqual(retained['observations'][-1]['metrics']['views'],123)
        with patch('agent_v3.enrichment.read_source') as reader:
            self.assertEqual(enrichment.prepare(self.store,topic_id=self.tid)['calls'],0);reader.assert_not_called()
        self.store.upsert_evidence({**item,'title':item['title']+'（更新）','body':'更新后的渠道摘要','last_seen_at':newer})
        self.store.conn.commit()
        self.assertNotEqual(self.store.evidence([self.eid])[0]['content_hash'],before['content_hash'])
        self.assertEqual(self.store.evidence([self.eid])[0]['body'],'更新后的渠道摘要')

    def test_failed_read_preserves_summary_and_backoff(self):
        self.store.conn.execute("UPDATE evidence SET platform='gamemedia' WHERE evidence_id=?",(self.eid,));self.store.conn.commit()
        with patch('agent_v3.enrichment.read_source',side_effect=RuntimeError('private text')):
            result=enrichment.prepare(self.store,topic_id=self.tid)
            self.assertEqual(result['results'][0]['status'],'failed')
            self.assertNotIn('private text',result['results'][0]['error'])
            self.assertEqual(enrichment.prepare(self.store,topic_id=self.tid)['calls'],0)
        self.assertEqual(self.store.evidence([self.eid])[0]['body'],self.item['body'])


if __name__=='__main__':unittest.main()
