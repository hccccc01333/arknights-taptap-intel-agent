import { platformName } from '../lib/presentation';
import { stableId, type Brief, type Claim, type Evidence, type Session, type TopicSnapshot } from './domain';

export type CreativeAsset = { id:string; kind:'visual'|'script'|'copy'; label:string; title:string; body:string; lines:string[]; evidenceIds:string[]; tone:'aqua'|'peach'|'iris' };
export type Opportunity = { id:string; topicId:string; record:TopicSnapshot; title:string; pitch:string; reason:string; goal:string; audience:string; touchpoint:string; evidence:Evidence[]; claims:Claim[]; brief:Brief; assets:CreativeAsset[]; variant:'discovery'|'participation' };
type Recipe={audience:string; question:string; action:string; metric:string; poster:string; hook:string; flow:string[]};
const recipes:Record<string,Recipe>={
 '新品发现':{audience:'想发现小众作品、愿意尝试新品的玩家',question:'你愿意把下一次试玩，留给哪种玩法？',action:'策划按玩法与体验时长组织的发现清单，邀请体验者补充一条推荐理由和一条体验限制。',metric:'记录专题浏览→游戏详情访问→可用试玩入口访问，以及有内容的体验反馈；与相同窗口的常规推荐比较。',poster:'下一款心动，先试玩。',hook:'这次不按热度选游戏。用一个玩法，找到你的下一次心动。',flow:['按玩法选一款作品','核实平台与试玩条件','试过之后，留一句具体的体验']},
 '版本动态':{audience:'准备回归或正在探索新版本的玩家',question:'新版本里，你最想先弄明白哪一个问题？',action:'整理可核实的版本准备清单，按机制、路线与资源分组；邀请创作者回答高频问题并标注适用版本。',metric:'观察攻略收藏、问题被回答的比例和专题→游戏详情访问；与同游戏上一轮专题在相同观察窗口比较。',poster:'新版本，从容一点。',hook:'信息很多，先从你最关心的那一步开始。',flow:['核实官方版本信息','收藏自己的准备路线','把问题留给社区一起回答']},
 '跨圈传播':{audience:'对美术、角色表达或创作感兴趣的玩家与泛兴趣用户',question:'哪个创作细节让你想了解这款游戏？',action:'将授权可用的作品整理成主题合集，补充对应游戏与体验入口；征集带创作过程的投稿，保留不同玩家观点。',metric:'观察合集→游戏详情访问、有效投稿与首次社区参与；区分已有玩家和首次参与者，不将播放量直接视为增长。',poster:'从一份创作，认识一种热爱。',hook:'你先看见了作品，也许还想认识它背后的游戏。',flow:['确认作品授权与署名','看见作品背后的玩法','留下自己的创作或体验']},
 '社区创作':{audience:'愿意分享经验、寻找同好或参与共建的玩家',question:'你的一条经验，可以帮到哪个新玩家？',action:'建立有适用版本和有效期的玩家共建专题；提供清晰的投稿模板与纠错入口，让贡献可以被看见。',metric:'观察有效贡献、问题解决与参与者在后续窗口的再次参与；排除重复投稿，按专题记录比较。',poster:'把你的经验，交给下一位玩家。',hook:'一句具体的经验，比一句“很好玩”更有帮助。',flow:['写下场景与版本','分享一条可复现的经验','邀请同好补充与纠错']},
 '行业事件':{audience:'寻找新作、关心实际游玩体验的玩家',question:'现场看见的新作，哪一款值得继续关注？',action:'按游戏建立新作体验索引，区分现场观察和已确认信息；提供可核实的游戏详情或预约入口。',metric:'观察体验索引→游戏详情访问、可用预约入口访问和有效玩家笔记；注明渠道与观察窗口。',poster:'把现场的新鲜感，带回社区。',hook:'现场很热闹。我们把值得继续关注的新作整理给你。',flow:['区分观察和官方信息','挑选感兴趣的新作','补充一份有依据的体验笔记']},
 '体验反馈':{audience:'关心设备、设置与实际游戏体验的玩家',question:'在你的设备上，怎样设置玩得更舒服？',action:'发布包含设备、版本、设置的体验反馈模板，汇总可复现的问题与调整方法，邀请玩家补充验证。',metric:'观察完整反馈数量、可复现问题比例和问题解决后的再次参与；按设备与版本分组，不推断整体性能。',poster:'让每一份体验，都说得更清楚。',hook:'同一个游戏，不同的体验。先把设备、版本和设置写清楚。',flow:['记录设备与游戏版本','描述设置和复现场景','补充调整后的体验']}
};
const fallback:Recipe={audience:'对这个游戏话题感兴趣的玩家',question:'关于这个话题，你最想核实什么？',action:'整理原始来源和玩家问题，先核实话题与游戏的关联；形成可查来源的社区内容草案。',metric:'明确内容触点与观察窗口，记录有效讨论和游戏详情访问；在小范围验证后再决定是否扩大。',poster:'从一个问题，遇见更多同好。',hook:'把话题的来龙去脉和玩家的问题放在一起。',flow:['核实来源','整理玩家问题','邀请社区补充']};
export function opportunitiesOf(session:Session):Opportunity[]{
 const records=session.records.filter(r=>!r.error).sort((a,b)=>(b.topic.velocity??-1)-(a.topic.velocity??-1));
 return records.slice(0,6).flatMap(record=>{
  const recipe=recipes[record.topic.category||'']||fallback,game=record.topic.gameName||'该游戏',evidence=record.materials.slice(0,3);
  return (['discovery','participation'] as const).map((variant,index)=>{
   const id='op-'+stableId(record.id+'|'+variant),idea=record.ideas[index]||record.ideas[0];
   const title=idea?.name||(variant==='discovery'?game+'的玩家发现专题':game+'的玩家共创话题');
   const goal=variant==='discovery'?'游戏发现与体验':'社区参与与内容沉淀';
   const touchpoint=variant==='discovery'?'建议触点：TapTap 游戏详情页关联内容、发现专题；需运营确认可用入口。':'建议触点：TapTap 对应游戏社区、话题与创作者内容；需运营确认活动能力。';
   const pitch=variant==='discovery'?recipe.action:`围绕“${recipe.question}”组织玩家参与，将有用回答沉淀为可查来源的社区内容。`;
   const claimId='proposal-'+id,claim:Claim={id:claimId,text:`增长假设：${game}的“${record.topic.category||'当前话题'}”可以通过${goal}承接。建议先小范围验证：${pitch}`,evidenceIds:evidence.map(m=>m.id),kind:'hypothesis',provenance:'rule',updatedAt:record.readAt};
   const brief:Brief={title:title+' · TapTap 增长提案',objective:goal+'：验证热点关注是否能带来有价值的玩家行动。',audience:recipe.audience,angle:title+'\n'+pitch,channel:touchpoint,window:'建议先做 48 小时小范围内容验证。该时长是策划建议，需核实热点是否仍活跃及活动/试玩的实际有效期。',nextAction:`1. 核对 ${evidence.length} 条关联来源，确认事实与素材使用条件。\n2. ${record.recommendation||recipe.action}\n3. 发布小范围内容并收集玩家反馈；根据下方指标决定继续、修改或停止。`,measurement:recipe.metric,selectedIdea:idea?record.topic.id+'|'+idea.id:'',claimIds:[claimId]};
   const assets:CreativeAsset[]=[
    {id:id+'-visual',kind:'visual',label:'专题封面',title:recipe.poster,body:game+' / '+goal,lines:recipe.flow,evidenceIds:evidence.map(m=>m.id),tone:variant==='discovery'?'aqua':'iris'},
    {id:id+'-script',kind:'script',label:'15 秒视频脚本',title:'给玩家一个继续了解的理由',body:recipe.hook,lines:['0–3 秒 / '+recipe.hook,'3–10 秒 / '+recipe.flow[0]+'。画面素材需确认授权。','10–15 秒 / '+recipe.question+' 引导到已核实的 TapTap 内容入口。'],evidenceIds:evidence.map(m=>m.id),tone:'peach'},
    {id:id+'-copy',kind:'copy',label:'社区发布文案',title:recipe.question,body:`${game}，你最关心哪一步体验？\n${recipe.action}\n分享时请注明游戏版本与体验条件。`,lines:['讨论问题：'+recipe.question,'参与方式：留下具体体验、理由或问题。','审核：核实信息，保留署名，不使用未经授权的原始媒体。'],evidenceIds:evidence.map(m=>m.id),tone:'iris'}
   ];
   return {id,topicId:record.topic.id,record,title,pitch,reason:record.summary||'当前记录缺少摘要，请先核实原始来源。',goal,audience:recipe.audience,touchpoint,evidence,claims:[claim],brief,assets,variant};
  });
 });
}
export function assetText(asset:CreativeAsset,op:Opportunity){return `${asset.label}：${asset.title}\n\n${asset.body}\n\n${asset.lines.join('\n')}\n\n依据：\n${op.evidence.map((m,i)=>`[${i+1}] ${m.title} / ${platformName(m.platform)} / ${m.url||'未提供原文链接'}`).join('\n')}\n\n前端规则编排草稿，非模型生成。原始素材与执行入口需核实。`;
}
export function packageMarkdown(op:Opportunity){return `# ${op.title} · 创作素材包\n\n基于保存的${op.record.origin==='demo'?'合成演示':'已有'}情报快照，使用前端规则编排，未调用真实 AI。\n\n${op.assets.map(a=>'## '+a.label+'\n\n'+assetText(a,op)).join('\n\n')}\n\n## 来源使用条件\n${op.evidence.map(m=>'- '+m.title+'：'+m.usage).join('\n')}\n`;}
