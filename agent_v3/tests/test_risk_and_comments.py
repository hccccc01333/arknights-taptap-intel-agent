"""Policy denial before generation, retained monitoring and bounded visual provenance."""
import copy
import json
import unittest
from unittest.mock import patch
from agent_v2.store import dump,now_iso
from agent_v3 import risk,work,browser_tools,discussion,research
from agent_v3.discovery import read_topic
from agent_v3.engine import run_agent
from agent_v3.pipeline import process
from agent_v3.presentation import recognized_captures
from agent_v3.reports import brief
from agent_v3.task_packets import topic_packet
from agent_v3.tests import test_main_agent as fixtures


class RiskAndCommentsTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MainAgentTests();self.f.setUp();self.s=self.f.store;self.tid=self.f.tid;self.rid=self.f.rid
        self.eid=self.f.item['evidence_id'];self.fp=read_topic(self.s,self.tid)['fingerprint']
    def tearDown(self):self.f.tearDown()
    def mark(self,polarity='negative',level='medium',stage='business_main'):
        return risk.save(self.s,self.tid,self.fp,self.rid,{'polarity':polarity,'level':level,'reason':'测试争议事件，停止借势',
            'facts':[{'evidence_id':self.eid,'quote':'玩家可分享地图并组队挑战。'}]},stage=stage)

    def test_unknown_negative_mixed_or_medium_risk_never_call_creative_model(self):
        for polarity,level in [('unknown','unknown'),('negative','medium'),('mixed','low'),('positive','medium')]:
            self.mark(polarity,level);m=self.f.model('opportunity')
            with self.assertRaisesRegex(ValueError,'禁止生成'):run_agent(self.s,self.rid,m,assigned_topic=self.tid)
            self.assertEqual(m.calls,[])
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM creative').fetchone()[0],0)

    def test_child_negative_cannot_be_overridden_by_positive_business_wording(self):
        m=self.f.model('opportunity');original=m.run_task
        def response(stage,*args,**kwargs):
            r=original(stage,*args,**kwargs)
            if stage=='interpretation':r['result']['risk_assessment'].update(polarity='negative',level='medium',reason='负面热点测试，营销禁止')
            if stage=='intelligence':
                r['result']['patterns']=[];o=r['result']['opportunity'];o['decision']='watch';o['prerequisites']=[]
                for k in ('audience_need','taptap_bridge','growth_goal','hypothesis','validation_plan'):o[k]=''
            return r
        m.run_task=response
        r=process(self.s,self.rid,model=m,topic_id=self.tid)
        self.assertEqual(r['status'],'intelligence_ready');self.assertNotIn('creative_plan',m.calls)
        self.assertEqual(len(self.s.game_signals()),1) # Neutral monitoring survives.
        self.assertEqual(self.s.usable_materials(),[])
        record=self.s.intelligence(self.tid)['payload']
        self.assertEqual(record['opportunity']['decision'],'watch');self.assertEqual(record['patterns'],[])
        self.assertEqual(record['opportunity']['hypothesis'],'')

    def test_new_negative_business_assessment_quarantines_noncompliant_patterns(self):
        m=self.f.model('opportunity');original=m.run_task
        def response(stage,*args,**kwargs):
            r=original(stage,*args,**kwargs)
            if stage=='intelligence':r['result']['risk_assessment'].update(polarity='negative',level='medium',reason='首次发现负面风险')
            return r
        m.run_task=response;process(self.s,self.rid,model=m,topic_id=self.tid)
        p=self.s.intelligence(self.tid)['payload']
        self.assertEqual(len(p['held_patterns']),1);self.assertEqual(p['patterns'],[])
        self.assertNotIn('creative_plan',m.calls)

    def test_old_creative_and_material_stay_saved_but_leave_promotional_views_and_report(self):
        process(self.s,self.rid,model=self.f.model('opportunity'),topic_id=self.tid)
        self.assertEqual(len(self.s.creative_feed()),1)
        title=self.s.creative_feed()[0]['payload']['title'];before=self.s.conn.execute('SELECT COUNT(*) FROM material').fetchone()[0]
        self.mark('negative')
        self.assertEqual(self.s.creative_feed(),[]);self.assertEqual(len(self.s.creative_feed(held=True)),1)
        self.assertEqual(self.s.usable_materials(),[])
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM material').fetchone()[0],before)
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM creative').fetchone()[0],1)
        self.assertNotIn(title,brief(self.s,1)['markdown'])
        self.assertEqual(read_topic(self.s,self.tid)['business_outputs']['creatives'],[])

    def test_only_child_assessment_does_not_authorize_growth(self):
        self.mark('positive','low','research_child')
        self.assertFalse(risk.policy(self.s,self.tid,self.fp)['growth_allowed'])

    def test_research_refresh_cannot_erase_known_negative_risk_with_same_source_version(self):
        self.mark('negative','medium','safety_review');self.mark('positive','low','research_child')
        p=risk.policy(self.s,self.tid,self.fp)
        self.assertEqual(p['polarity'],'negative');self.assertFalse(p['growth_allowed'])
        for _ in range(12):self.mark('positive','low','research_child')
        p=risk.policy(self.s,self.tid,self.fp)
        self.assertLess(len(dump(p)),2500)

    def test_same_input_reassessment_updates_current_intelligence_and_retains_previous(self):
        with self.s.conn:
            self.s.save_intelligence(self.tid,self.fp,'fixture-contract','r_one',{'summary':'旧判断'})
            self.s.save_intelligence(self.tid,self.fp,'fixture-contract','r_two',{'summary':'纠正判断'})
        self.assertEqual(self.s.intelligence(self.tid)['run_id'],'r_two')
        row=self.s.conn.execute('SELECT run_id,payload FROM topic_intelligence_revision').fetchone()
        self.assertEqual(row['run_id'],'r_one');self.assertEqual(json.loads(row['payload'])['summary'],'旧判断')

    def test_risk_source_search_hit_not_grounded(self):
        p=topic_packet(read_topic(self.s,self.tid),self.s.context());p['evidence'][0]['role']='background_unverified'
        with self.assertRaises(ValueError):risk.grounded({'polarity':'neutral','level':'low','reason':'测试','basis_refs':[1]},p)

    def test_held_job_not_claimed_and_can_resume_after_positive_reassessment(self):
        self.mark('neutral','low');self.s.conn.execute('INSERT INTO work_item(job_id,stage,topic_id,fingerprint,prompt_version,bucket,priority,status,attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('job_test','creative',self.tid,self.fp,'creative-v3.2:fixture','fixture',1,'pending',0,now_iso(),now_iso()));self.s.conn.commit()
        self.mark('negative');self.assertIsNone(work.claim(self.s,self.rid,'creative'))
        self.mark('neutral','low');self.assertIsNotNone(work.claim(self.s,self.rid,'creative'))

    def test_ocr_uses_comment_bbox_excludes_ad_text_and_keeps_frame_identity(self):
        frames=[{'ocr':{'status':'ok','lines':[{'words':[
            {'text':'广告','x':0,'y':5,'width':30,'height':20},
            {'text':'想要联机','x':100,'y':250,'width':150,'height':20}]}]}}]
        card={'frame_index':0,'text':'','bbox':{'x':80,'y':220,'width':400,'height':100},'date_text':''}
        r=browser_tools.transcribe_cards([card,card],frames)
        self.assertEqual(len(r),1);self.assertEqual(r[0]['text'],'想要联机');self.assertEqual(r[0]['method'],'screenshot_ocr')
        self.assertEqual(browser_tools.transcribe_cards([{'frame_index':2,'text':'冒充评论'}],frames),[])

    def visual_capture(self,region='found'):
        return {'capture_id':'capture_'+'a'*32,'status':'ok','tool':'browser_comments','comment_region':{'status':region},
            'captures':[{'ocr':{'status':'ok','text':'广告和导航'}}],
            'comment_cards':[{'id':'testcomment','text':'想要蓝图收藏','method':'screenshot_ocr','frame_index':0,
                'bbox':{'x':100,'y':300,'width':500,'height':90},'date_text':'昨天','published_at':None}]}

    def test_visual_comment_is_independent_evidence_and_unknown_date_not_promoted(self):
        original=self.s.evidence([self.eid])[0]['body']
        with patch.object(browser_tools,'browse',return_value=self.visual_capture()) as tool:
            r=browser_tools.read_comments(self.s,self.tid,self.eid)
        self.assertEqual(r['saved'],1);self.assertEqual(tool.call_args.kwargs,{'visual':True,'region':'comments'})
        self.assertEqual(self.s.evidence([self.eid])[0]['body'],original)
        sample=discussion.read_samples(self.s,self.tid)[0]
        self.assertIsNone(sample['published_at']);self.assertIsNone(sample['sample']['author_hash'])
        self.assertEqual(sample['content_scope'],'comment_ocr_sample');self.assertEqual(sample['reading_metadata']['frame_index'],0)
        packet=topic_packet(read_topic(self.s,self.tid),self.s.context())
        self.assertEqual(packet['evidence'][1]['role'],'discussion')

    def test_no_comment_boundary_keeps_capture_and_no_fabricated_comments(self):
        with patch.object(browser_tools,'browse',return_value=self.visual_capture('not_found')):
            r=browser_tools.read_comments(self.s,self.tid,self.eid)
        self.assertEqual(r['saved'],0);self.assertEqual(discussion.read_samples(self.s,self.tid),[])
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM research_capture').fetchone()[0],1)
        self.assertEqual(read_topic(self.s,self.tid)['research_captures'],[])

    def test_cached_visual_comment_attaches_to_another_topic_without_browsing_again(self):
        with patch.object(browser_tools,'browse',return_value=self.visual_capture()) as tool:
            first=browser_tools.read_comments(self.s,self.tid,self.eid)
            second=browser_tools.read_comments(self.s,'another_fixture_topic',self.eid)
        self.assertEqual(tool.call_count,1);self.assertEqual(second['calls'],0)
        self.assertEqual(discussion.read_samples(self.s,'another_fixture_topic')[0]['evidence_id'],first['evidence_ids'][0])

    def test_interface_failure_escalates_to_scrolled_visual_comments_within_budget(self):
        self.s.conn.execute("UPDATE evidence SET platform='bilibili'");self.s.conn.commit()
        m=self.f.model();run=m.run_task
        def task(stage,*args,**kwargs):
            if stage=='research_plan':return {'result':{'reason':'测试评论接口优先','actions':[{'tool':'sample_discussion','source_ref':0,'query':''}]},'usage':{},'transport':'fixture'}
            return run(stage,*args,**kwargs)
        m.run_task=task
        with patch.object(discussion,'sample',return_value={'calls':1,'status':'partial','results':[{'status':'failed'}]}),patch.object(browser_tools,'read_comments',return_value={'calls':1,'status':'not_found','saved':0}) as tool:
            r=research.run(self.s,delegations=[{'topic_id':self.tid}],max_calls=2,model=m,run_id=self.rid)
        self.assertEqual(r['calls'],2);self.assertEqual(tool.call_count,1)


if __name__=='__main__':unittest.main()
