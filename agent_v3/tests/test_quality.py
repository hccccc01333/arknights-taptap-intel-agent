"""Presentation and grounded interpretation boundaries; all sources are fixtures."""
import copy
import json
import unittest
from agent_v2.store import dump,now_iso
from agent_v3 import discussion,research_child,work
from agent_v3.discovery import read_topic
from agent_v3.pipeline import process
from agent_v3.presentation import recognized_captures
from agent_v3.tests import test_main_agent as fixtures


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MainAgentTests();self.f.setUp();self.s=self.f.store;self.tid=self.f.tid;self.rid=self.f.rid
    def tearDown(self):self.f.tearDown()

    def test_unrecognized_captures_are_hidden_but_audit_row_is_retained(self):
        self.s.conn.execute('INSERT INTO research_capture VALUES(?,?,?,?,?,?)',
            ('capture_test',self.tid,self.f.item['evidence_id'],now_iso(),'ok',dump({'captures':[{'ocr':{'status':'not_requested'}}]})))
        self.s.conn.commit()
        self.assertEqual(read_topic(self.s,self.tid)['research_captures'],[])
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM research_capture').fetchone()[0],1)

    def test_filtered_frames_keep_original_image_index_and_do_not_mutate_audit(self):
        row={'status':'ok','capture_id':'test','payload':{'captures':[{'ocr':{'status':'unavailable'}},
            {'ocr':{'status':'ok','text':'有效正文'}},{'ocr':{'status':'ok','text':' 123 !!! '}}]}}
        original=copy.deepcopy(row);result=recognized_captures([row])
        self.assertEqual(result[0]['payload']['captures'][0]['frame_index'],1)
        self.assertEqual(len(result[0]['payload']['captures']),1);self.assertEqual(row,original)

    def test_blocked_page_ocr_is_not_presented(self):
        row={'status':'blocked','payload':dump({'captures':[{'ocr':{'status':'ok','text':'请通过验证码'}}]})}
        self.assertEqual(recognized_captures([row]),[])

    def test_short_specific_requests_survive_but_laughter_is_low_information(self):
        self.assertEqual(discussion.quality_flags('找普通人'),[])
        self.assertEqual(discussion.quality_flags('想要联机'),[])
        self.assertIn('low_information',discussion.quality_flags('哈哈哈哈哈哈哈哈哈[笑哭]'))
        self.assertIn('low_information',discussion.quality_flags('[doge][doge]'))

    def test_duplicate_filter_is_scoped_to_parent_post(self):
        parent=self.s.evidence([self.f.item['evidence_id']])[0]
        other={**self.f.item,'evidence_id':'e_other','external_id':'other','url':'https://www.taptap.cn/moment/other'}
        self.s.upsert_evidence(other)
        for p in (parent,self.s.evidence(['e_other'])[0]):
            discussion.save_samples(self.s,self.tid,p,[{'id':'1','text':'想要联机','published_at':now_iso()}],'hot')
        samples=discussion.read_samples(self.s,self.tid)
        self.assertEqual(len(samples),2);self.assertTrue(all(not e['sample']['flags'] for e in samples))

    def test_old_length_flag_is_reassessed_without_rewriting_original_comment(self):
        parent=self.s.evidence([self.f.item['evidence_id']])[0]
        discussion.save_samples(self.s,self.tid,parent,[{'id':'1','text':'找普通人','published_at':now_iso()}],'hot')
        self.s.conn.execute("UPDATE discussion_sample SET flags='[\"low_information\"]'");self.s.conn.commit()
        e=discussion.read_samples(self.s,self.tid)[0]
        self.assertEqual(e['body'],'找普通人');self.assertEqual(e['sample']['flags'],[])

    def test_interpretation_upgrade_preserves_previous_revision_and_then_reuses(self):
        fp=read_topic(self.s,self.tid)['fingerprint'];old={'headline':'旧测试解读','status':'ready'}
        self.s.conn.execute('INSERT INTO topic_interpretation VALUES(?,?,?,?,?)',(self.tid,fp,self.rid,now_iso(),dump(old)))
        self.s.conn.commit();m=self.f.model()
        research_child.interpret(self.s,self.rid,m,self.tid);research_child.interpret(self.s,self.rid,m,self.tid)
        self.assertEqual(m.calls,['interpretation','graph_extraction'])
        self.assertEqual(json.loads(self.s.conn.execute('SELECT payload FROM interpretation_revision').fetchone()[0]),old)

    def comment_model(self,invalid=False):
        p=self.s.evidence([self.f.item['evidence_id']])[0]
        discussion.save_samples(self.s,self.tid,p,[{'id':'1','text':'想要联机','published_at':now_iso()}],'hot')
        m=self.f.model();run=m.run_task
        def response(stage,packet,*args,**kwargs):
            out=run(stage,packet,*args,**kwargs)
            if stage=='interpretation':
                ref=1 if invalid else next(q['ref'] for q in packet['quote_candidates'] if q['quote']=='想要联机')
                out['result']['discussion_review']=[{'basis_refs':[ref],'decision':'keep','reason':'表达具体联机需求'}]
                out['result']['views']=[{'text':'一条采样评论希望支持联机。','kind':'actual_comment','basis_refs':[ref]}]
            return out
        m.run_task=response;return m

    def test_comment_theme_retains_exact_quote_and_computed_sample_count(self):
        result=research_child.interpret(self.s,self.rid,self.comment_model(),self.tid)['payload']
        self.assertEqual(result['views'][0]['sample_count'],1)
        self.assertEqual(result['views'][0]['facts'][0]['quote'],'想要联机')
        self.assertEqual(result['discussion_review'][0]['decision'],'keep')

    def test_comment_review_cannot_use_article_as_comment(self):
        with self.assertRaises(ValueError):
            research_child.interpret(self.s,self.rid,self.comment_model(invalid=True),self.tid)
        self.assertIsNone(self.s.interpretation(self.tid,read_topic(self.s,self.tid)['fingerprint']))

    def test_comment_quote_candidates_exclude_generated_parent_title(self):
        from agent_v3.task_packets import topic_packet
        self.comment_model();packet=topic_packet(read_topic(self.s,self.tid),self.s.context())
        comments={e['evidence_id']:e for e in packet['evidence'] if e['role']=='discussion'}
        for q in packet['quote_candidates']:
            if q['evidence_id'] in comments:self.assertIn(q['quote'],comments[q['evidence_id']]['body'])

    def test_material_only_has_distinct_current_outcomes(self):
        m=self.f.model();m.signals=False
        process(self.s,self.rid,model=m,topic_id=self.tid)
        output=read_topic(self.s,self.tid)['business_outputs']
        self.assertEqual(output['game_signals'],[]);self.assertEqual(len(output['materials']),1)
        self.assertEqual(output['creatives'],[]);self.assertEqual(output['opportunity']['decision'],'watch')
        self.assertIn('没有',output['assessments']['intelligence'])

    def test_direct_creative_does_not_require_game_signal_or_material(self):
        m=self.f.model('opportunity');m.signals=False;m.patterns=False
        process(self.s,self.rid,model=m,topic_id=self.tid)
        o=read_topic(self.s,self.tid)['business_outputs']
        self.assertEqual(o['game_signals'],[]);self.assertEqual(o['materials'],[]);self.assertEqual(len(o['creatives']),1)

    def test_reusing_same_material_preserves_both_business_revision_links(self):
        process(self.s,self.rid,model=self.f.model(),topic_id=self.tid)
        self.s.set_context({**self.s.context(),'goal':'测试第二种运营目标'})
        rid=self.s.create_run('changed context quality fixture')
        process(self.s,rid,model=self.f.model(),selected_topics=[self.tid])
        o=read_topic(self.s,self.tid)['business_outputs']
        self.assertEqual(len(o['materials']),1);self.assertEqual(len(o['game_signals']),1)
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM material_application_run').fetchone()[0],2)

    def test_consumer_copy_rejects_internal_setup_text_and_repairs_once(self):
        m=self.f.model('opportunity');run=m.run_task;attempts=[]
        def response(stage,*args,**kwargs):
            out=run(stage,*args,**kwargs)
            if stage=='creative_production':
                attempts.append(stage)
                if len(attempts)==1:out['result']['copy']='欢迎参与地图挑战。上线前配置经确认的TapTap入口再来看完整地图。'
            return out
        m.run_task=response
        r=process(self.s,self.rid,model=m,topic_id=self.tid)
        self.assertEqual(r['status'],'completed');self.assertEqual(len(attempts),2)
        c=read_topic(self.s,self.tid)['business_outputs']['creatives'][0]['payload']
        self.assertNotIn('上线前配置',c['copy']);self.assertIn('{{TapTap承接链接}}',c['copy'])

    def test_production_revision_preserves_old_copy_and_does_not_inflate_idea_count(self):
        from agent_v3.production_revision import revise
        m=self.f.model('opportunity');process(self.s,self.rid,model=m,topic_id=self.tid)
        cid=self.s.conn.execute('SELECT creative_id FROM creative').fetchone()[0]
        old=json.loads(self.s.conn.execute('SELECT payload FROM creative').fetchone()[0])
        m2=self.f.model('opportunity');run=m2.run_task
        def response(*args,**kwargs):
            out=run(*args,**kwargs);out['result']['copy']='一起选地图，邀请朋友来挑战。来TapTap晒出你的选择：{{TapTap承接链接}}。';return out
        m2.run_task=response;rid=self.s.create_run('copy revision fixture');revise(self.s,rid,m2,cid)
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM creative').fetchone()[0],1)
        archived=json.loads(self.s.conn.execute('SELECT payload FROM creative_content_revision').fetchone()[0])
        self.assertEqual(archived['copy'],old['copy'])
        self.assertNotEqual(read_topic(self.s,self.tid)['business_outputs']['creatives'][0]['payload']['copy'],old['copy'])
        self.assertTrue(all(m.get('revision_state')!='historical' for m in self.s.usable_materials()))


if __name__=='__main__':unittest.main()
