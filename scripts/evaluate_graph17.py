"""Offline labelled retrieval mechanics, NOT model-quality or growth evaluation."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent_v3.tests.test_graph17 import GraphTests
from agent_v3 import semantic,tracking,knowledge_graph as kg,graph_retrieval as retrieval,graph_entities


def evaluate():
    fixture=GraphTests();fixture.setUp()
    try:
        s=fixture.s;a,b,key,quotes=fixture.pair()
        fixture.document('明日方舟公布周边活动');fixture.document('原神音乐彩排日常记录')
        tracking.decide(s,key,'development','离线标注：两条来源描述发布与后续回应','fixture',quotes=quotes)
        kg.refresh(s)
        documents=[dict(r) for r in s.conn.execute('SELECT t.*,e.body FROM tracked_event t JOIN evidence e ON e.evidence_id=t.anchor_evidence_id WHERE t.merged_into IS NULL ORDER BY t.tracked_id')]
        pairs,state=semantic.pairs(s,documents)
        gold={frozenset((a[2],b[2]))}
        baseline={frozenset((documents[i]['tracked_id'],documents[j]['tracked_id'])) for _,i,j in pairs}
        context=retrieval.local_context(s,topic_id=a[1])
        predicted={frozenset((r['subject_id'],r['object_id'])) for r in context['relations']}
        def scores(values):return {'precision':len(values&gold)/len(values) if values else None,'recall':len(values&gold)/len(gold),'returned_pairs':len(values)}
        cases=[('方舟','海上方舟',None),('方舟','明日方舟干员','game_arknights'),('悟空','西游记故事',None),('悟空','黑神话游戏科学','game_black_myth_wukong'),('LOL','lolcat',None),('LOL','英雄联盟赛事','game_lol'),('崩铁','','game_star_rail')]
        entity_correct=sum((graph_entities.resolve(s,name,context)['entity'] or {}).get('entity_id')==gold_id for name,context,gold_id in cases)
        linked=sum(graph_entities.resolve(s,name,context)['status']=='linked' for name,context,_ in cases)
        before=s.conn.execute('SELECT COUNT(*) FROM tracked_event WHERE merged_into IS NULL').fetchone()[0]
        x=fixture.document('明日方舟玩家分享组队想法');y=fixture.document('原神玩家分享结伴游玩想法');fixture.need(x);fixture.need(y);kg.refresh(s)
        themes=retrieval.global_context(s)['communities'];expected={frozenset((a[2],b[2])),frozenset((x[2],y[2]))}
        return {'dataset':'synthetic-labelled-graph17-v1','documents':len(documents),'paid_model_calls':0,
            'entity_resolution':{'cases':len(cases),'correct':entity_correct,'accuracy':entity_correct/len(cases),'linked':linked,'abstained':len(cases)-linked},
            'baseline':{'method':state['method'],**scores(baseline)},'grounded_graph':scores(predicted),
            'identity':{'separate_events':before,'false_development_merges':int(tracking.root_id(s,a[2])==tracking.root_id(s,b[2]))},
            'themes':{'count':len(themes),'pure_groups':sum(frozenset(c['event_ids']) in expected for c in themes),'promoted_to_hotspot':sum(c['heat']['qualified_as_hotspot'] for c in themes)},
            'limitations':['人工给定正确的事件关系和需求标签；检验检索及边界，未评估真实 LLM 抽取准确率。','图检索使用额外结构化证据，不能以此声称优于 BGE 或向量模型。','小型合成样本不能证明真实社区纯度、热点识别质量或增长效果。']}
    finally:fixture.tearDown()


if __name__=='__main__':print(json.dumps(evaluate(),ensure_ascii=False,indent=2))
