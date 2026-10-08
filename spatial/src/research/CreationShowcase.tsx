import { useState } from 'react';
import { Icon } from '../ui/Icon';
import { creativeSVG, type CreativeDraft } from './creative';
const expressions=[{name:'海盐透镜',tone:'aqua',pattern:'lens'},{name:'雾蓝曲线',tone:'iris',pattern:'ribbon'},{name:'杏色像素',tone:'peach',pattern:'pixels'}] as const;
export function CreationShowcase(){
 const [expression,setExpression]=useState(0),[square,setSquare]=useState(false);const current=expressions[expression];
 const draft:CreativeDraft={id:'home-preview',opportunityKey:'',topicId:'',topicTitle:'创作演示',kind:'visual',title:'下一款心动，\n先试玩。',subtitle:'独立游戏 / 发现与体验',footer:'这里，遇见下一份热爱',body:'',format:square?'square':'portrait',tone:current.tone,pattern:current.pattern,scenes:[],sourceIds:[],origin:'demo',editedAt:''};
 return <div className="v6-creation-showcase" aria-label="首页创作效果演示"><div className="v6-showcase-orbit" aria-hidden="true"><i/><i/><i/></div><div className={'v6-showcase-poster '+(square?'square':'')} key={expression+'-'+square} dangerouslySetInnerHTML={{__html:creativeSVG(draft)}}/><div className="v6-showcase-controls"><div><Icon name="spark" size={16}/><span>一种想法，三种表达</span><button onClick={()=>setSquare(v=>!v)} aria-pressed={square} aria-label="切换创作演示比例">{square?'1:1':'4:5'}<Icon name="expand" size={13}/></button></div><nav aria-label="首页创作配色与图形">{expressions.map((e,i)=><button key={e.name} className={e.tone} aria-pressed={expression===i} onClick={()=>setExpression(i)}><i/>{e.name}</button>)}</nav><small>可交互的原创封面演示。研究中可继续编辑、关联来源和导出。</small></div></div>;
}
