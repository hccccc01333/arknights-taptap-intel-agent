/**
 * DOM 浮层 —— 沿用 TapTap 首页的排版：左栏导航 / 顶部分类轨道 / 右侧详情。
 * 锚点 Industrial：等宽字、1px 硬线、0 圆角、0 阴影。
 * 内容纪律：无装饰文案、无假数据、无 unicode 图标。
 */

import { useEffect, useRef, useState } from "react";
import type { Idea, Planet, Universe } from "../lib/api";

const pct = (v: number | null | undefined) => (v == null ? "—" : Math.round(v * 100));
const num = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString());

const LIFE: Record<string, string> = {
  EMERGING: "正在起势", GROWING: "正在升温", PEAKING: "到顶",
  REACTIVATED: "重新活跃", DECLINING: "已经降温", DORMANT: "基本平静",
};
const REL: Record<string, { tag: string; hint: string }> = {
  L1: { tag: "实测", hint: "AI 分析得出" },
  L2: { tag: "推测", hint: "按游戏档案推测" },
  L3: { tag: "未知", hint: "还不清楚" },
};

/* ---------------- 顶栏：Logo + 搜索 + 分类轨道（TapTap 首页的核心交互） ---------------- */
export function TopBar({ me, cats, cat, setCat, onCommand, onSearch, running }: {
  me: { actor: string; role: string };
  cats: { key: string; label: string; n: number }[];
  cat: string; setCat: (c: string) => void;
  onCommand: (c: string) => void; onSearch: (q: string) => void; running: boolean;
}) {
  const [q, setQ] = useState("");
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault(); ref.current?.focus();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  return (
    <header className="top">
      <div className="brand">
        <img src="/taptap-logo.svg" alt="TapTap" />
        <span>情报星图</span>
      </div>
      <div className="search">
        <input ref={ref} value={q} placeholder="搜话题，或输入指令（例：/help）"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && q.trim()) {
              q.startsWith("/") ? onCommand(q.trim()) : onSearch(q.trim());
              if (!q.startsWith("/")) setQ("");
            }
            if (e.key === "Escape") setQ("");
          }} />
      </div>
      <div className="right">
        {running && <span className="running">执行中</span>}
        <span className="who">{me.actor} · {me.role}</span>
      </div>
      <div className="cats">
        {cats.map((c) => (
          <button key={c.key} className={`cat ${cat === c.key ? "on" : ""}`}
            onClick={() => setCat(c.key)}>
            {c.label}<em>{c.n}</em>
          </button>
        ))}
      </div>
    </header>
  );
}

/* ---------------- 左栏：TapTap 式导航 ---------------- */
export function LeftRail({ u, watch, onSelect, selectedId }: {
  u: Universe; watch: Planet[]; selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const c = u.relevanceCensus;
  const withCover = u.planets.filter((p) => p.cover).length;
  return (
    <aside className="rail">
      <section className="nav">
        <p className="nav-h">情报</p>
        <p className="nav-i on">全部话题</p>
        <p className="nav-i">正在起势</p>
        <p className="nav-i">已经降温</p>
        <p className="nav-i">跨平台扩散</p>
      </section>
      <section>
        <p className="nav-h">我的跟进</p>
        {watch.length ? watch.map((p) => (
          <button key={p.id} className={`nav-i link ${selectedId === p.id ? "on" : ""}`}
            onClick={() => onSelect(p.id)}>
            {p.title.slice(0, 12)}
          </button>
        )) : <p className="nav-i">还没有跟进任何话题</p>}
      </section>
      <section className="facts">
        <p className="nav-h">数据说明</p>
        <p>相关性：{c.real} 实测 · {c.proxy} 推测 · {c.unknown} 未知</p>
        <p>真实封面：{withCover}/{u.planets.length} 张，其余卡片无图</p>
        <p>跨平台话题：{u.core.multiPlatformCount}/{u.core.topicCount} 个</p>
        <p className="dim">热度真实值只在 0.13–0.36 之间，区分不出高低，所以卡片按排名分带。</p>
      </section>
    </aside>
  );
}

/* ---------------- 右栏：今日必做 / 话题详情 ---------------- */
export function RightPanel({ u, selected, topic, ideas, busy, onSelect, onCommand }: {
  u: Universe; selected: Planet | null; topic: any; ideas: Idea[];
  busy: boolean; onSelect: (id: string) => void; onCommand: (cmd: string) => void;
}) {
  if (selected) {
    return <TopicDetail p={selected} topic={topic} ideas={ideas} busy={busy}
      onCommand={onCommand} />;
  }
  const ranked = [...u.planets]
    .filter((p) => p.relevance != null)
    .sort((a, b) => (b.relevance! - a.relevance!) || (b.heat! - a.heat!))
    .slice(0, 8);
  return (
    <aside className="side">
      <section>
        <p className="side-h">今天最该看</p>
        <table className="list">
          <thead><tr><th>#</th><th>话题</th><th>相关性</th><th>热度</th></tr></thead>
          <tbody>
            {ranked.map((p, i) => (
              <tr key={p.id} onClick={() => onSelect(p.id)}>
                <td className="idx">{i + 1}</td>
                <td>{p.title}</td>
                <td>{pct(p.relevance)}<span className={`tag ${p.relevanceLevel}`}>
                  {REL[p.relevanceLevel].tag}</span></td>
                <td>{pct(p.heat)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="steps">
        <p className="side-h">怎么用</p>
        <p>1. 点一张卡片，看它为什么值得关注</p>
        <p>2. 确认后点「跟进」，进入工作流</p>
        <p>3. 有方案就提交评审，通过后安排上线</p>
      </section>
    </aside>
  );
}

function TopicDetail({ p, topic, ideas, busy, onCommand }: {
  p: Planet; topic: any; ideas: Idea[]; busy: boolean; onCommand: (cmd: string) => void;
}) {
  const rel = REL[p.relevanceLevel];
  const ep = topic?.evidence_panel || {};
  const wh = topic?.what_happened || {};
  return (
    <aside className="side">
      <div className="detail-cover">
        {p.cover
          ? <img src={p.cover} alt={p.title} />
          : <div className="no-cover">无封面</div>}
      </div>
      <h2 className="d-title">{p.title}</h2>
      <p className="d-meta">
        {p.gameName ? `${p.gameName} · ` : ""}{num(p.contentCount)} 条讨论
        · {p.platformCount} 个平台 · 首次发现 {(p.firstSeen || "").slice(5, 16)}
      </p>
      <p className="tags">
        <span className="tagbox">{LIFE[p.lifecycle || ""] || "状态未知"}</span>
        <span className={`tagbox ${p.relevanceLevel}`}>相关性{rel.tag}</span>
      </p>
      <section>
        <p className="side-h">指标</p>
        <table className="metrics">
          <tr><td>热度</td><td className="bar"><i style={{ width: `${pct(p.heat)}%` }} /></td><td>{pct(p.heat)}</td></tr>
          <tr><td>势头</td><td className="bar"><i style={{ width: `${pct(p.velocity)}%` }} /></td><td>{pct(p.velocity)}</td></tr>
          <tr><td>可信</td><td className="bar"><i style={{ width: `${pct(p.confidence)}%` }} /></td><td>{pct(p.confidence)}</td></tr>
          <tr><td>相关性</td><td className="bar"><i style={{ width: `${pct(p.relevance)}%` }} /></td><td>{pct(p.relevance)}</td></tr>
        </table>
        <p className="dim small">{p.relevanceNote}。热度按排名分带。</p>
      </section>
      <section>
        <p className="side-h">为什么值得关注</p>
        <p className="quote">{wh.trigger || wh.summary || "这条话题还没跑过深度分析。"}</p>
        <div className="acts">
          <button className="btn-go" disabled={busy}
            onClick={() => onCommand(`/follow ${p.id}`)}>跟进</button>
          <button className="btn-line" disabled={busy}
            onClick={() => onCommand(`/submit ${p.id}`)}>提交评审</button>
        </div>
      </section>
      <section>
        <p className="side-h">证据</p>
        {(ep.facts || []).slice(0, 3).map((f: any, i: number) => (
          <p key={i} className="evi"><b>{f.tier === "PRIMARY" ? "官方发布" : "媒体报道"}</b>{f.excerpt}</p>
        ))}
        {(ep.community || []).slice(0, 2).map((f: any, i: number) => (
          <p key={"c" + i} className="evi"><b>玩家讨论</b>{f.excerpt}</p>
        ))}
        {(ep.unknowns || []).slice(0, 2).map((x: any, i: number) => (
          <p key={"u" + i} className="evi unknown"><b>还不确定</b>{x.text || x}</p>
        ))}
        {!(ep.facts || []).length && !(ep.community || []).length &&
          <p className="dim small">还没有采集到证据。</p>}
      </section>
      {ideas.length > 0 && (
        <section>
          <p className="side-h">可做的方案 <span className="dim small">（环绕卡片的线框）</span></p>
          <table className="list">
            <tbody>{ideas.slice(0, 6).map((it) => (
              <tr key={it.id}><td>{it.name}</td><td>{it.type}</td><td>{pct(it.score)}</td></tr>
            ))}</tbody>
          </table>
        </section>
      )}
    </aside>
  );
}

/* ---------------- 悬停读数 ---------------- */
export function HoverLabel({ planet, x, y }: { planet: Planet; x: number; y: number }) {
  const rel = REL[planet.relevanceLevel];
  return (
    <div className="readout" style={{ left: x, top: y }}>
      <b>{planet.title}</b>
      {planet.cover && <img className="ro-cover" src={planet.cover} alt="" />}
      <table>
        <tr><td>热度</td><td>{pct(planet.heat)}</td></tr>
        <tr><td>势头</td><td>{pct(planet.velocity)}</td></tr>
        <tr><td>讨论</td><td>{num(planet.contentCount)}</td></tr>
        <tr><td>相关性</td><td>{pct(planet.relevance)} <em>{rel.tag}</em></td></tr>
        {planet.nIdeas > 0 && <tr><td>方案</td><td>{planet.nIdeas} 个</td></tr>}
      </table>
      <p className="hint">点击进入</p>
    </div>
  );
}

export function Toast({ msg, bad }: { msg: string; bad?: boolean }) {
  if (!msg) return null;
  return <div className={`toast ${bad ? "bad" : ""}`}>{msg}</div>;
}
