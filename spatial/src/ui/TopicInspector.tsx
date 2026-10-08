import { useEffect, useState } from 'react';
import { api, isDemoMode, type Planet, type Universe } from '../lib/api';
import { formatNumber, formatScore, lifecycleColor, lifecycleNames, platformColors, platformName } from '../lib/presentation';
import { Icon } from './Icon';

export function TopicInspector({ planet, universe, saved, focused, onSave, onFocus, onClose, onExpand, onOpportunity, expanded = false }: {
  planet: Planet; universe: Universe; saved: boolean; focused: boolean; expanded?: boolean;
  onSave: () => void; onFocus: () => void; onClose: () => void; onExpand: () => void; onOpportunity: () => void;
}) {
  const [result, setResult] = useState<{ id: string; detail: any; error: string; loading: boolean }>({ id: planet.id, detail: null, error: '', loading: true });
  useEffect(() => {
    let current = true;
    const id = planet.id;
    setResult({ id, detail: null, error: '', loading: true });
    api.topic(id).then(detail => { if (current) setResult({ id, detail, error: '', loading: false }); })
      .catch(error => { if (current) setResult({ id, detail: null, error: error instanceof Error ? error.message : '话题详情读取失败，请稍后重试。', loading: false }); });
    return () => { current = false; };
  }, [planet.id, universe]);
  // 切换话题的首次 render 也不显示上一话题的详情，异步返回仅写入对应 id。
  const detail = result.id === planet.id ? result.detail : null;
  const error = result.id === planet.id ? result.error : '';
  const loading = result.id !== planet.id || result.loading;
  const evidence = (Array.isArray(detail?.evidence) ? detail.evidence : [
    ...(Array.isArray(detail?.evidence_panel?.facts) ? detail.evidence_panel.facts : []),
    ...(Array.isArray(detail?.evidence_panel?.community) ? detail.evidence_panel.community : []),
  ]).filter((item: unknown) => item && typeof item === 'object');
  const ideas = Array.isArray(universe.ideas?.[planet.id]) ? universe.ideas[planet.id] : [];
  return <aside className={`v2-inspector ${expanded ? 'expanded' : ''}`} aria-label="热点情报详情">
    <div className="inspector-heading"><span className="eyebrow">SIGNAL EXPLORER</span><div>{!expanded && <button className="icon-button" aria-label="展开热点详情" onClick={onExpand}><Icon name="expand" size={16} /></button>}<button className="icon-button" aria-label="关闭热点详情" onClick={onClose}><Icon name="close" size={17} /></button></div></div>
    <div className="inspector-body"><span className="v2-life-tag" style={{ color: lifecycleColor(planet.lifecycle) }}><i style={{ background: lifecycleColor(planet.lifecycle) }} />{lifecycleNames[planet.lifecycle || ''] || '未知状态'}</span>
    <h2>{planet.title}</h2><div className="inspector-platforms">{planet.platforms.map(p => <span key={p}><i style={{ background: platformColors[p] || '#88aeb0' }} />{platformName(p)}</span>)}</div>
    <div className="inspector-metrics">{[{ label: '热度', value: planet.heat }, { label: '势头', value: planet.velocity }, { label: '相关性', value: planet.relevance }].map(item => <div key={item.label}><strong>{formatScore(item.value)}</strong><span>{item.label} / 100</span></div>)}</div>
    <p className="inspector-count"><Icon name="layers" size={14} />{formatNumber(planet.contentCount)} 条关联内容 · {planet.platformCount} 个平台</p>
    <p className="inspector-relevance"><Icon name="shield" size={14} /><span>{planet.relevanceLevel === 'L1' ? '分析值' : planet.relevanceLevel === 'L2' ? '档案推测' : '关系未知'} · {planet.relevanceNote}</span></p>
    <section className="inspector-section"><h3><Icon name="eye" size={15} />发生了什么</h3><p>{loading ? '正在读取话题分析…' : detail?.summary || detail?.what_happened?.trigger || detail?.what_happened?.summary || '暂无深度分析结果，可先从平台覆盖与指标判断下一步研究方向。'}</p>{error && <p className="error-text" role="alert">{error}</p>}</section>
    <section className="inspector-section"><h3><Icon name="spark" size={15} />增长切入点<span>{ideas.length} 个方案</span></h3>{ideas.slice(0, expanded ? 6 : 2).map(idea => <button className="inspector-idea" key={idea.id} onClick={onOpportunity}><span>{idea.name}</span><Icon name="arrow" size={14} /></button>)}{!ideas.length && <p className="muted">尚未产出方案，建议先核验相关性。</p>}</section>
    {expanded && <><section className="inspector-section"><h3><Icon name="link" size={15} />来源与证据</h3>{evidence.map((item: any, i: number) => <article className="evidence-card" key={i}><span>{platformName(item.platform || item.tier || '来源')}</span><b>{item.title || '采集证据'}</b><p>{item.excerpt}</p></article>)}{!evidence.length && <p>暂无详细证据记录。</p>}</section>{detail?.recommendation && <section className="inspector-section"><h3>下一步建议</h3><p>{detail.recommendation}</p></section>}</>}
    </div><div className="inspector-actions"><button className={`primary-button ${saved ? 'is-saved' : ''}`} onClick={onSave}><Icon name={saved ? 'check' : 'bookmark'} size={16} />{saved ? '已关注 · 点击取消' : '关注热点'}</button>{!expanded && <button className="secondary-button" onClick={onFocus}><Icon name="orbit" size={16} />{focused ? '回到全局' : '聚焦星球'}</button>}</div>
    {!expanded && <button className="inspector-expand-link" onClick={onExpand}>查看完整证据与建议<Icon name="arrow" size={14} /></button>}
    {isDemoMode && <p className="inspector-disclaimer">合成演示情境 · 供体验界面，未经真实采集验证</p>}
  </aside>;
}
