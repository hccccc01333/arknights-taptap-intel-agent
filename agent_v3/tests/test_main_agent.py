"""Business control, delegated research, provenance and separate product outputs."""
import copy
import json
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch

from agent_v2.store import now_iso
from agent_v3.store import Store
from agent_v3.discovery import scan,queue,read_topic
from agent_v3.pipeline import process
from agent_v3.main_agent import plan,analyze,signal_validation
from agent_v3.opencode_zen import StructuredDeliveryError
from agent_v3.research_child import interpret
from agent_v3 import work,research
from agent_v3.task_packets import PLAN_SCHEMA
from agent_v3.tests.test_v3 import source,output


class MainAgentTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(':memory:');self.item=source('玩家分享组队挑战玩法')
        self.item['published_at']=now_iso()
        self.item['body']='玩家可分享地图并组队挑战。一位创作者展示了地图分享与组队挑战的玩法，参与者可以邀请朋友体验。这是用于验证处理架构的测试正文，不代表真实用户需求统计。'
        self.store.upsert_evidence(self.item)
        self.store.conn.execute('INSERT INTO channel_observation VALUES(?,?,?,?,?)',('baidu:game',self.item['evidence_id'],now_iso(),1,'{}'))
        self.store.conn.commit();scan(self.store)
        self.tid=queue(self.store)[0]['topic_id'];self.rid=self.store.create_run('main/child fixture')

    def tearDown(self):self.store.close()

    def model(self,decision='watch'):
        outer=self
        class Native:
            model='test-main';last_session=None;supports_tasks=True;compact_tasks=True;supports_main_agent=True
            def __init__(self):self.calls=[];self.route='game_direct';self.action='delegate_research';self.bad_view=False;self.bad_quote=False;self.signals=True;self.patterns=True
            def run_task(self,stage,packet,schema,system,**kwargs):
                self.calls.append(stage)
                if stage=='main_plan':
                    value={'decisions':[{'ref':c['ref'],'route':self.route,'action':self.action,
                        'research_goal':'game_change' if self.action in ('delegate_research','analyze') and self.route!='unrelated' else 'none','reason':'需要核对玩法和表达语境',
                        'questions':['玩法和表达分别有哪些实际依据？'],'query':c['title'][:80]} for c in packet['candidates']]}
                elif stage=='research_plan':value={'reason':'测试中正文已充分，无联网动作','actions':[]}
                elif stage=='interpretation':
                    value={'status':'ready','headline':'创作者展示地图分享与组队挑战玩法','one_line':'来源介绍邀请朋友分享地图并参与组队挑战。',
                        'background':[],'core':[{'text':'来源展示地图分享与组队挑战','basis_refs':[1]}],
                        'timeline':[{'text':'来源描述创作者展示玩法','time_text':'','time_kind':'unknown','basis_refs':[1]}],
                        'views':[{'text':'玩家喜欢这种玩法','kind':'actual_comment','basis_refs':[1]}] if self.bad_view else [],
                        'controversies':[],'unknowns':['没有取得评论样本，不能推断总体需求'],'discussion_review':[],
                        'risk_assessment':{'polarity':'positive','level':'low','reason':'夹具为正常玩法分享，没有负面争议依据','basis_refs':[1]},
                        'recency':{'kind':'unknown','date_iso':'','time_text':'','basis_refs':[],'reason':'测试仅提供近期来源日期'},'source_matches':[]}
                    if getattr(self,'bad_date',False):value['recency']={'kind':'recent_event','date_iso':'2099-10-08','time_text':'2099年10月8日','basis_refs':[1],'reason':'模型不可靠日期测试'}
                elif stage=='graph_extraction':value={'entities':[],'relations':[],'needs':[]}
                elif stage=='intelligence':
                    value={'summary':'玩法表达可转成邀请朋友一起参与的游戏内容，效果待验证。','fact_refs':[1],
                        'emotions':[],'needs':[],'spread_mechanics':[],'content_forms':['玩法介绍'],
                        'reusable_angles':['邀请朋友一起挑战'],'unknowns':['未采集评论'],'source_asset_refs':[],
                        'patterns':[{'kind':'source_quote' if self.bad_quote else 'game_adaptation','title':'邀请朋友挑战句式',
                            'content':'这句并没有出现在原文' if self.bad_quote else '地图我来选，队友你来当，一起挑战这关吧。',
                            'basis_refs':[1],'rights_status':'改编草案，游戏能力上线前确认',
                            'game_application':'适合支持组队挑战与地图分享的游戏内容','usage_example':'邀请朋友分享地图并参与挑战的短视频字幕',
                            'delivery':{'format':'dialogue','audience':'寻求共同挑战的玩家','placement':'游戏论坛原创内容',
                              'body':'地图我来选，队友你来当。你有哪张地图想和朋友一起挑战？把你喜欢的关卡和一起参与的理由写下来，来游戏论坛分享你的选择。玩法依据来自创作者展示，具体游戏能力还需要确认。',
                              'adaptation_steps':['替换为实际游戏与关卡名称','确认地图分享和组队玩法确实支持'],
                              'usage_boundary':'原创邀请内容，不声称评论统计或保证玩法可用'}}] if self.patterns else [],
                        'game_signals':[{'title':'来源展示地图分享与组队玩法','game_context':'组队挑战内容',
                            'observed_change':'创作者公开展示邀请朋友体验的玩法','why_it_matters':'可以关注共同参与场景的传播表达',
                            'next_watch':'后续补查实际评论和参与行为','basis_refs':[1],'hypothesis':'传播价值尚待测试'}] if self.signals else [],
                        'research_questions':[],'risk_assessment':{'polarity':'positive','level':'low','reason':'夹具为正常游戏玩法分享，无负面事件','basis_refs':[1]},'output_assessments':{'intelligence':'展示玩法属于带来源的游戏线索' if self.signals else '没有适合保存的游戏变化','materials':'可以原创改编邀请句式' if self.patterns else '没有适合保存的表达素材'},
                        'opportunity':{'decision':decision,'reason':'有玩法表达可小规模验证',
                            'audience_need':'寻求共同参与内容的人群' if decision=='opportunity' else '',
                            'taptap_bridge':'以经确认的入口承接玩法讨论' if decision=='opportunity' else '',
                            'growth_goal':'验证平台进入与参与行为' if decision=='opportunity' else '',
                            'hypothesis':'邀请朋友共同挑战的表达可能吸引相关人群进入平台参与',
                            'validation_plan':'发布一条内容核对进入和参与，未形成参与则停止修改',
                            'prerequisites':['确认游戏能力与真实入口']}}
                elif stage=='creative_plan':
                    c=output(outer.store,outer.tid)['assessments'][0]['creatives'][0]
                    value={k:copy.deepcopy(c[k]) for k in PLAN_SCHEMA['properties'] if k in c}
                    value.update(steps=['确认游戏能力及入口','制作内容','发布并记录进入与参与'],reuse_material_refs=[0] if packet['reusable_materials'] else [],reuse_source_asset_refs=[])
                elif stage=='creative_production':
                    value={'copy':'地图我来选，队友你来当。邀请朋友一起体验共同挑战的乐趣，来TapTap分享你的选择：{{TapTap承接链接}}。',
                        'script':'0–3秒：展示自制地图示意图，字幕“地图我来选”；3–10秒：展示两位玩家相约挑战的自摄画面；10–20秒：用原创动画示意共同参与；20–25秒：邀请观众在经确认的TapTap入口分享选择并发起讨论。',
                        'rights_notes':'使用自行制作画面，游戏能力、入口和发布资源上线前确认。'}
                else:raise AssertionError(stage)
                return {'model':self.model,'result':value,'usage':{'total_tokens':10},'seconds':0.1,'transport':'test-main','session_id':None}
        return Native()

    def test_one_main_controls_business_and_delegates_child(self):
        model=self.model('opportunity')
        with patch('agent_v3.research.search_news',side_effect=AssertionError('fixture must not use network')):
            result=process(self.store,self.rid,model=model,topic_id=self.tid)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(model.calls,['main_plan','research_plan','interpretation','graph_extraction','intelligence','creative_plan','creative_production'])
        self.assertEqual(result['usage']['total_tokens'],70)
        self.assertEqual(len(self.store.game_signals()),1);self.assertEqual(len(self.store.hotspot_feed()),1)
        self.assertEqual(len(self.store.usable_materials()),3)
        self.assertEqual(self.store.interpretation(self.tid,read_topic(self.store,self.tid)['fingerprint'])['payload']['agent_role'],'research_child')
        self.assertEqual(self.store.intelligence(self.tid)['payload']['agent_role'],'business_main')

    def test_watch_saves_signal_and_material_without_forcing_creative(self):
        result=process(self.store,self.rid,model=self.model(),topic_id=self.tid)
        self.assertEqual(result['status'],'intelligence_ready');self.assertEqual(len(self.store.game_signals()),1)
        self.assertEqual(self.store.overview()['counts']['creative'],0)

    def test_unrelated_is_archived_before_research(self):
        model=self.model();model.route='unrelated';model.action='archive'
        self.assertEqual(process(self.store,self.rid,model=model,topic_id=self.tid)['status'],'no_selected_work')
        self.assertEqual(model.calls,['main_plan']);self.assertFalse(self.store.hotspot_feed())

    def test_decision_and_interpretation_cache_prevents_duplicate_calls(self):
        model=self.model();plan(self.store,self.rid,model,self.tid);plan(self.store,self.rid,model,self.tid)
        interpret(self.store,self.rid,model,self.tid);interpret(self.store,self.rid,model,self.tid)
        self.assertEqual(model.calls,['main_plan','interpretation','graph_extraction'])

    def test_changed_source_does_not_display_old_interpretation(self):
        interpret(self.store,self.rid,self.model(),self.tid)
        self.store.upsert_evidence({**self.item,'body':self.item['body']+'后续正文发生变化。'});self.store.conn.commit();scan(self.store)
        self.assertFalse(self.store.hotspot_feed())
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM topic_interpretation').fetchone()[0],1)

    def test_news_publication_is_not_hotspot_heat(self):
        self.store.conn.execute('DELETE FROM channel_observation')
        self.store.conn.execute("UPDATE evidence SET kind='news',published_at=?",(now_iso(),));self.store.conn.commit();scan(self.store)
        interpret(self.store,self.rid,self.model(),self.tid)
        self.assertFalse(self.store.hotspot_feed())

    def test_ordinary_game_feed_position_is_not_a_hot_board(self):
        self.store.conn.execute('DELETE FROM channel_observation')
        self.store.conn.execute("UPDATE evidence SET kind='post'")
        self.store.conn.execute('INSERT INTO channel_observation VALUES(?,?,?,?,?)',('taptap:discover',self.item['evidence_id'],now_iso(),1,'{}'))
        self.store.conn.commit();scan(self.store);interpret(self.store,self.rid,self.model(),self.tid)
        self.assertFalse(self.store.hotspot_feed())

    def test_title_only_cannot_be_complete_interpretation(self):
        self.store.conn.execute("UPDATE evidence SET body=''");self.store.conn.commit();scan(self.store)
        with self.assertRaises(ValueError):interpret(self.store,self.rid,self.model(),self.tid)
        self.assertFalse(self.store.hotspot_feed())

    def test_report_cannot_be_misrepresented_as_real_comment(self):
        model=self.model();model.bad_view=True
        with self.assertRaises(ValueError):interpret(self.store,self.rid,model,self.tid)

    def test_literal_material_quote_is_checked_against_source(self):
        model=self.model();model.bad_quote=True
        interpret(self.store,self.rid,model,self.tid);work.enqueue(self.store)
        job=work.claim(self.store,self.rid,'intelligence',topic_id=self.tid)
        with self.assertRaises(ValueError):analyze(self.store,self.rid,job,model)
        self.assertFalse(self.store.usable_materials());self.assertFalse(self.store.game_signals())

    def test_material_can_exist_without_game_signal(self):
        model=self.model();model.signals=False;process(self.store,self.rid,model=model,topic_id=self.tid)
        self.assertFalse(self.store.game_signals());self.assertEqual(len(self.store.usable_materials()),1)

    def test_signal_can_exist_without_material(self):
        model=self.model();model.patterns=False;process(self.store,self.rid,model=model,topic_id=self.tid)
        self.assertEqual(len(self.store.game_signals()),1);self.assertFalse(self.store.usable_materials())

    def test_old_reference_patterns_are_not_counted_as_usable_material(self):
        self.store.save_material({'kind':'expression_pattern','origin':'inferred','title':'旧模式','content':'仅说明一个泛领域表达',
            'evidence_ids':[self.item['evidence_id']],'tags':[],'rights_status':'未转化'},self.rid);self.store.conn.commit()
        self.assertFalse(self.store.usable_materials())

    def test_no_delegations_prevents_unsolicited_research(self):
        with patch('agent_v3.research.search_news') as search:
            result=research.run(self.store,delegations=[],model=self.model(),run_id=self.rid)
        self.assertEqual(result['calls'],0);search.assert_not_called()

    def test_selected_topics_cannot_claim_unselected_job(self):
        work.enqueue(self.store)
        self.assertIsNone(work.claim(self.store,self.rid,'intelligence',topic_ids=['missing_topic']))
        self.assertIsNotNone(work.claim(self.store,self.rid,'intelligence',topic_ids=[self.tid]))

    def test_completed_watch_does_not_starve_new_candidates(self):
        model=self.model();process(self.store,self.rid,model=model,topic_id=self.tid)
        self.assertEqual(plan(self.store,self.rid,model,self.tid),[])
        self.assertEqual(model.calls.count('main_plan'),1)

    def test_changed_business_context_reuses_research_and_updates_business_record(self):
        model=self.model();process(self.store,self.rid,model=model,topic_id=self.tid)
        self.store.set_context({**self.store.context(),'goal':'验证新的共同参与内容'})
        work.enqueue(self.store,topic_ids=[self.tid])
        rid=self.store.create_run('context change')
        result=process(self.store,rid,model=model,selected_topics=[self.tid])
        self.assertEqual(result['status'],'intelligence_ready')
        self.assertEqual(model.calls.count('interpretation'),1)
        self.assertEqual(model.calls.count('intelligence'),2)
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM topic_intelligence').fetchone()[0],2)

    def test_old_content_date_is_visible_in_headline_and_summary(self):
        stamp=(datetime.now(timezone.utc)-timedelta(days=400)).isoformat(timespec='seconds')
        self.store.conn.execute('UPDATE evidence SET published_at=?',(stamp,));self.store.conn.commit();scan(self.store)
        p=interpret(self.store,self.rid,self.model(),self.tid)['payload']
        self.assertIn('历史背景',p['headline']);self.assertIn(stamp[:10],p['one_line']);self.assertFalse(self.store.hotspot_feed())

    def test_creative_without_signal_or_material(self):
        model=self.model('opportunity');model.signals=False;model.patterns=False
        result=process(self.store,self.rid,model=model,topic_id=self.tid)
        self.assertEqual(result['status'],'completed');self.assertFalse(self.store.game_signals())
        self.assertEqual(len(self.store.usable_materials()),2)  # creative's new copy/script

    def test_four_day_publication_stays_in_current_week(self):
        self.store.conn.execute('UPDATE evidence SET published_at=?',((datetime.now(timezone.utc)-timedelta(days=4)).isoformat(timespec='seconds'),));self.store.conn.commit();scan(self.store)
        interpret(self.store,self.rid,self.model(),self.tid)
        self.assertEqual(len(self.store.hotspot_feed()),1)

    def test_invalid_event_date_is_held_without_discarding_valid_core(self):
        model=self.model();model.bad_date=True
        p=interpret(self.store,self.rid,model,self.tid)['payload']
        self.assertEqual(p['recency']['kind'],'unknown');self.assertEqual(p['recency']['date_iso'],'')
        self.assertFalse(p['recency']['time_verified']);self.assertEqual(p['status'],'ready')

    def test_screening_advances_past_first_window(self):
        for i in range(45):
            item=source('测试候选游戏 '+str(i));item['published_at']=now_iso();item['evidence_id']='fixture_'+str(i);self.store.upsert_evidence(item)
        self.store.conn.commit();scan(self.store)
        model=self.model();model.route='unrelated';model.action='archive'
        plan(self.store,self.rid,model);first=self.store.conn.execute('SELECT COUNT(*) FROM main_decision').fetchone()[0]
        plan(self.store,self.rid,model);second=self.store.conn.execute('SELECT COUNT(*) FROM main_decision').fetchone()[0]
        self.assertEqual(first,18);self.assertEqual(second,36)

    def test_system_reobservation_without_change_is_background(self):
        status=signal_validation({'observed_change':'旧内容被系统重新观察，未观察到新的内容版本变化'},{'signals':[]})
        self.assertEqual(status['status'],'background_only')

    def test_invalid_child_plan_still_records_received_token_usage(self):
        model=self.model()
        def bad(*args,**kwargs):raise StructuredDeliveryError('test JSON failure',{'usage':{'total_tokens':12},'model':'test-main','transport':'test','result':None})
        model.run_task=bad
        with patch('agent_v3.research.background',return_value={'calls':0,'status':'deferred'}),patch('agent_v3.web_research.background',return_value={'calls':0,'status':'deferred'}):
            research.run(self.store,run_id=self.rid,model=model,delegations=[{'topic_id':self.tid,'query':'待查问题'}])
        paid=[s for s in self.store.get_run(self.rid)['steps'] if s['kind']=='research_plan_model']
        self.assertEqual(paid[0]['payload']['usage']['total_tokens'],12)


if __name__=='__main__':unittest.main()
