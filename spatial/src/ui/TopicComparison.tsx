import type { Planet } from '../lib/api';
import { formatNumber, formatScore, lifecycleNames, platformName } from '../lib/presentation';
import { Icon } from './Icon';

export function TopicComparison({ planets, onRemove, onExplore }: { planets: Planet[]; onRemove: (id: string) => void; onExplore: (id: string) => void }) {
  const rows: { label: string; key: 'heat' | 'velocity' | 'confidence' | 'relevance' }[] = [{ label: '热度', key: 'heat' }, { label: '传播势头', key: 'velocity' }, { label: '可信度', key: 'confidence' }, { label: 'TapTap 相关性', key: 'relevance' }];
  const meterWidth = (value: number | null) => value == null || !Number.isFinite(value) ? 0 : Math.max(0, Math.min(100, value * 100));
  return <div className="compare-content"><div className="compare-scroll"><table><thead><tr><th>当前快照</th>{planets.map(p => <th key={p.id}><div className="compare-topic"><b>{p.title}</b><button className="icon-button" onClick={() => onRemove(p.id)} aria-label={`从对比移除${p.title}`}><Icon name="close" size={14} /></button></div><span>{lifecycleNames[p.lifecycle || ''] || '状态未知'}</span></th>)}</tr></thead><tbody>
    {rows.map(row => { const valid = planets.map(p => p[row.key]).filter((v): v is number => v != null && Number.isFinite(v)); const max = valid.length ? Math.max(...valid) : null; return <tr key={row.key}><td>{row.label}</td>{planets.map(p => <td key={p.id}><strong className={p[row.key] != null && p[row.key] === max ? 'compare-best' : ''}>{formatScore(p[row.key])}</strong><small> / 100</small><div className="compare-meter"><i style={{ width: `${meterWidth(p[row.key])}%` }} /></div></td>)}</tr>; })}
    <tr><td>关联内容量</td>{planets.map(p => <td key={p.id}>{formatNumber(p.contentCount)} 条</td>)}</tr><tr><td>覆盖平台</td>{planets.map(p => <td key={p.id}>{p.platforms.map(platformName).join(' / ')}</td>)}</tr><tr><td>相关性来源</td>{planets.map(p => <td key={p.id}>{p.relevanceLevel === 'L1' ? '分析值' : p.relevanceLevel === 'L2' ? '档案推测' : '未知'}<p className="compare-note">{p.relevanceNote}</p></td>)}</tr><tr><td>增长方案</td>{planets.map(p => <td key={p.id}>{p.nIdeas} 个</td>)}</tr><tr><td>继续探索</td>{planets.map(p => <td key={p.id}><button className="secondary-button" onClick={() => onExplore(p.id)}>查看热点<Icon name="arrow" size={14} /></button></td>)}</tr>
  </tbody></table></div><p className="compare-disclaimer">高亮表示当前所选热点的指标最大值。相关性推测仍需核验，数值不代表未来增长结果。</p></div>;
}
