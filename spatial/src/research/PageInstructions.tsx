import { Icon } from '../ui/Icon';
import type { Page } from './navigation';
import { resourceNavigation } from './navigation';
const purpose:Partial<Record<Page,string>>={signals:'先筛选一个热点，再打开来源。决定继续做方案时，点击“加入研究”。',games:'按游戏或已有明确品类比较热点，选中话题后继续查看情报。',library:'浏览已有来源素材与策略，选中后预览、收藏或下载；创作编辑在项目中进行。',timeline:'查看收录记录何时出现，按话题、日期和平台筛选；这是记录时间线。'};
export function ResourceOrientation({page}:{page:Page}){return <div className="v8-resource-orientation"><p><Icon name="orbit" size={18}/><span><b>找机会 / 资料工具</b>{purpose[page]}</span></p><nav aria-label="机会与资料工具"><a href="#/signals" aria-current={page==='signals'?'page':undefined}>热点动态</a>{resourceNavigation.map(n=><a href={'#/'+n.id} key={n.id} aria-current={page===n.id?'page':undefined}>{n.label}</a>)}</nav></div>;}
const workbenchInstructions:Record<string,{title:string;steps:string[]}>= {
 today:{title:'这里用来挑选当天要跟进的机会',steps:['先在关注范围选择游戏、品类或热点类型。','阅读线索，点击“读取并建立行动”确认资料与负责人。','打开行动跟进，完善任务、截止日与执行记录。']},
 actions:{title:'这里用来推进已选定的方案',steps:['打开一条行动，填写负责人、截止日与游戏运营阶段。','在任务清单中核对来源、准备素材，记录一次实际进展。','按实际情况更新状态；执行后进入“观察与复盘”。']},
 schedule:{title:'这里按截止日检查安排',steps:['行动详情中先设置计划截止日，行动才会出现在日历。','用上一周和下一周查看安排，点击日历里的行动继续处理。','检查下方逾期与尚未排期的行动，调整真实计划。']},
 reports:{title:'这里整理已有记录，形成日报或周报',steps:['先在行动详情填写执行记录、观察与复盘结论。','选择日报或周报，设置报告日期并检查选入的行动。','下载报告；它展示当前记录状态，并不代表正式审批。']},
 preferences:{title:'这里设置今日机会的关注范围',steps:['选择游戏、明确的品类或热点类型；未选时显示全部。','返回今日机会查看筛选结果，再标记关注、暂缓或忽略。','需要比较快照时手动记录基线；当前没有后台推送提醒。']},
};
export function WorkbenchInstructions({view}:{view:string}){const info=workbenchInstructions[view]||workbenchInstructions.today;return <details className="v8-workbench-instructions" key={view}><summary><Icon name="file" size={17}/><span>这个板块怎么用</span><small>{info.title}</small><Icon name="chevron" size={15}/></summary><ol>{info.steps.map(s=><li key={s}>{s}</li>)}</ol></details>;}
