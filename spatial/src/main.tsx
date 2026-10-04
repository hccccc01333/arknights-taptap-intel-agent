import { StrictMode, useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Canvas } from "@react-three/fiber";
import "./ui/app.css";
import { api, auth, login, type Universe } from "./lib/api";
import { corridorLength, layout } from "./lib/layout";
import { CardWall } from "./three/Cards";
import { HoverLabel, LeftRail, RightPanel, Toast, TopBar } from "./ui/Overlay";

type Me = { actor: string; role: string };

/** 分类轨道（TapTap 首页的胶囊标签） */
function buildCats(u: Universe) {
  const all = u.planets;
  return [
    { key: "all", label: "为你推荐", n: all.length },
    { key: "rising", label: "正在起势", n: all.filter((p) => p.lifecycle === "EMERGING").length },
    { key: "dec", label: "已经降温", n: all.filter((p) => p.lifecycle !== "EMERGING").length },
    { key: "multi", label: "跨平台扩散", n: all.filter((p) => p.platformCount > 1).length },
    { key: "idea", label: "有可做方案", n: all.filter((p) => p.nIdeas > 0).length },
    { key: "cover", label: "有真实封面", n: all.filter((p) => p.cover).length },
  ];
}

function Login({ onOk }: { onOk: (m: Me) => void }) {
  const [actor, setActor] = useState("");
  const [pw, setPw] = useState("demo");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="gate">
      <form className="gate-card" onSubmit={async (e) => {
        e.preventDefault(); setErr(""); setBusy(true);
        try { const r = await login(actor.trim(), pw); onOk({ actor: r.actor, role: r.role }); }
        catch (ex) { setErr((ex as Error).message); } finally { setBusy(false); }
      }}>
        <div className="gate-top">
          <img className="gate-logo" src="/taptap-logo.svg" alt="TapTap" />
          <div className="gate-title">增长情报星图</div>
        </div>
        <div className="gate-body">
          <label>你的名字
            <input value={actor} onChange={(e) => setActor(e.target.value)}
              placeholder="运营A / 评审B / 发布C / 管理D / 观察E" /></label>
          <label>密码<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} /></label>
          <div className="gate-err">{err}</div>
          <button className="btn-go" disabled={busy}>{busy ? "进入中" : "进入"}</button>
        </div>
        <p className="gate-foot">演示密码统一是 demo。不同角色能做的事不一样。</p>
      </form>
    </div>
  );
}

function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [u, setU] = useState<Universe | null>(null);
  const [cat, setCat] = useState("all");
  const [selId, setSelId] = useState<string | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [topic, setTopic] = useState<any>(null);
  const [offsetX, setOffsetX] = useState(0);
  const [toast, setToast] = useState({ msg: "", bad: false });
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (auth.get()) api.me().then(setMe).catch(() => auth.clear()); }, []);
  useEffect(() => {
    if (!me) return;
    api.universe().then(setU).catch((e) => setToast({ msg: e.message, bad: true }));
  }, [me]);

  const cats = useMemo(() => (u ? buildCats(u) : []), [u]);

  useEffect(() => { setOffsetX(0); }, [cat]);
  const shown = useMemo(() => {
    if (!u) return [];
    const all = u.planets;
    if (cat === "rising") return all.filter((p) => p.lifecycle === "EMERGING");
    if (cat === "dec") return all.filter((p) => p.lifecycle !== "EMERGING");
    if (cat === "multi") return all.filter((p) => p.platformCount > 1);
    if (cat === "idea") return all.filter((p) => p.nIdeas > 0);
    if (cat === "cover") return all.filter((p) => p.cover);
    return all;
  }, [u, cat]);

  const placed = useMemo(() => layout(shown), [shown]);
  const selected = useMemo(() => u?.planets.find((p) => p.id === selId) ?? null, [u, selId]);
  const hovered = useMemo(() => u?.planets.find((p) => p.id === hoverId) ?? null, [u, hoverId]);
  const watch = useMemo(() => {
    if (!u) return [];
    return [...u.planets]
      .filter((p) => p.nIdeas > 0 || (p.relevance ?? 0) > 0.6)
      .sort((a, b) => (b.relevance ?? 0) - (a.relevance ?? 0)).slice(0, 8);
  }, [u]);

  useEffect(() => {
    if (!selId) { setTopic(null); return; }
    api.topic(selId).then(setTopic).catch(() => setTopic(null));
  }, [selId]);

  const say = useCallback((msg: string, bad = false) => {
    setToast({ msg, bad });
    window.setTimeout(() => setToast({ msg: "", bad: false }), 4200);
  }, []);

  const runCommand = useCallback(async (input: string) => {
    setBusy(true);
    try {
      const r = await api.run(input, selId ? { event_id: selId } : undefined);
      say(r.message || "完成", !r.ok);
      if (r.ok) api.universe().then(setU).catch(() => {});
    } catch (e) { say((e as Error).message, true); }
    finally { setBusy(false); }
  }, [selId, say]);

  const search = useCallback((q: string) => {
    const hit = u?.planets.find((p) => p.title.includes(q));
    if (hit) { setSelId(hit.id); say(`跳到：${hit.title}`); }
    else say(`没找到「${q}」`, true);
  }, [u, say]);

  const length = useMemo(() => corridorLength(placed), [placed]);
  const scroll = useCallback((d: number) => {
    setOffsetX((v) => Math.max(0, Math.min(Math.max(0, length - 40), v + d)));
  }, [length]);

  const openIdea = useCallback((id: string) => {
    say(`创意 ${id}：在右侧提交评审，通过后可安排上线`);
  }, [say]);

  if (!me) return <Login onOk={setMe} />;
  if (!u) {
    return <div className="gate"><div className="loading">
      <div className="spinner" />{toast.msg || "正在拉取数据"}</div></div>;
  }

  return (
    <div className="app">
      <TopBar me={me} cats={cats} cat={cat} setCat={setCat}
        onCommand={runCommand} onSearch={search} running={busy} />
      <div className="stage">
        <Canvas camera={{ fov: 42, near: 0.5, far: 900 }} dpr={[1, 1.75]}>
          <CardWall placed={placed} ideas={u.ideas} selectedId={selId}
            hoveredId={hoverId} onSelect={setSelId} onHover={setHoverId}
            onOpenIdea={openIdea} onScroll={scroll} offsetX={offsetX}
            length={length} />
        </Canvas>
        <LeftRail u={u} watch={watch} selectedId={selId} onSelect={setSelId} />
        <RightPanel u={u} selected={selected} topic={topic}
          ideas={selId ? u.ideas[selId] || [] : []} busy={busy}
          onSelect={setSelId}
          onCommand={(c) => (c === "__back__" ? setSelId(null) : runCommand(c))} />
        {hovered && !selId && <HoverLabel planet={hovered} x={window.innerWidth * 0.44} y={78} />}
        <div className="legend">
          <span><i className="lg-l1" />相关性实测</span>
          <span><i className="lg-l2" />按档案推测</span>
          <span><i className="lg-l3" />关系未知</span>
          <span><i className="lg-idea" />有可做方案</span>
        </div>
        <div className="hintbar">滚轮上下翻动 · 点击卡片进入</div>
        {toast.msg && <Toast msg={toast.msg} bad={toast.bad} />}
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode><App /></StrictMode>
);
