/* 增长情报台 —— 全中文界面
   这份代码只做两件事：把数据画成人话、把动作按钮接上后端。
   术语翻译表在 ZH 里 —— 后端字段是英文的，人看到的必须是中文的。 */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = (v) => v == null ? "—" : Math.round(Number(v) * 100);
const num = (v) => v == null ? "—" : Number(v).toLocaleString();
const pill = (kind, txt) => `<span class="pill ${kind}">${txt}</span>`;

/* ============ 术语翻译（后端英文 → 用户看到的中文） ============ */
const ZH = {
  life: { EMERGING: "刚起势", GROWING: "正在升温", PEAKING: "到顶了",
          REACTIVATED: "重新活跃", DECLINING: "已经降温", DORMANT: "基本平静" },
  lifeKind: { EMERGING: "warm", GROWING: "hot", PEAKING: "hot", REACTIVATED: "warm",
              DECLINING: "cool", DORMANT: "cool" },
  urg: { high: "窗口充裕", medium: "窗口一般", low: "窗口紧张", closed: "窗口已关" },
  urgKind: { high: "good", medium: "warm", low: "hot", closed: "cool" },
  tier: { PRIMARY: "官方发布", SECONDARY: "媒体报道", COMMUNITY: "玩家讨论",
          INFERRED: "推测" },
  state: { DRAFT: "草稿", AI_READY: "待评审", REVIEWING: "评审中", APPROVED: "已通过",
           EXECUTING: "准备中", LIVE: "进行中", COMPLETED: "已完成", REJECTED: "已驳回" },
  plan: { planned: "待上线", live: "进行中", paused: "已暂停",
          stopped: "已停止", completed: "已完成", rolled_back: "已回滚" },
  exp: { planned: "待开始", running: "跑数据中", completed: "已出结论",
         stopped: "已中止", invalid: "数据无效" },
  result: { WIN: "有效", LOSS: "无效", INCONCLUSIVE: "说不清",
            STOPPED: "已中止", INVALID: "数据无效" },
};

/* ============ 状态 ============ */
const S = { token: sessionStorage.getItem("token") || "", me: null,
            cards: [], tab: "today", drawer: null, ws: null, ops: null };

async function api(path, body) {
  const res = await fetch("/api" + path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json",
               ...(S.token ? { Authorization: "Bearer " + S.token } : {}) },
    body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401) { sessionStorage.clear(); location.reload(); }
  const d = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(d.detail || "请求失败");
  return d;
}
function toast(msg, kind) {
  const d = document.createElement("div");
  d.className = "toast " + (kind || "");
  d.textContent = msg;
  $("toast").appendChild(d);
  setTimeout(() => d.remove(), 4200);
}

/* ============ 社区报告列表（L3 图社区检测 × L4 报告生成） ============ */
async function loadToday(refresh) {
  const d = await api("/communities" + (refresh ? "?refresh=1" : ""));
  const reports = d.reports || [];
  const totalEv = reports.reduce((s, r) => s + (r.n_events || 0), 0);
  const totalCt = reports.reduce((s, r) => s + (r.total_content || 0), 0);

  $("today-title").textContent = reports.length ? "这些社群正在讨论什么" : "还没有社区报告";
  $("today-sub").textContent = reports.length
    ? "话题按实体共现聚成讨论社群，一份报告 = 一个社群的来龙去脉。点话题可展开单条详情。"
    : "先跑一轮 L3 趋势引擎，话题聚成社群后这里就会出现报告。";
  $("today-stats").innerHTML =
    stat(reports.length, "讨论社群", "hl") +
    stat(totalEv, "话题") +
    stat(totalCt, "内容");

  $("today-list").innerHTML = reports.length ? reports.map(communityReport).join("") :
    `<div class="empty">还没有社区报告。<br>跑一次 L3 趋势引擎，就会把碎片话题聚成可读的社群。</div>`;
  $("today-list").querySelectorAll(".crep").forEach((n, i) => {
    n.style.animationDelay = Math.min(i * 0.07, 0.6) + "s";
  });
  $("today-list").querySelectorAll(".trow[data-eid]").forEach((n) => {
    n.onclick = () => openDrawer(n.dataset.eid);
  });
}
function stat(n, label, kind) {
  return `<div class="stat ${kind}"><b>${n}</b><span>${label}</span></div>`;
}

/* 生命周期中文 → 配色（L4 报告里的 lifecycles 键已是中文） */
const LIFE_KIND_ZH = Object.fromEntries(
  Object.entries(ZH.life).map(([k, v]) => [v, ZH.lifeKind[k]]));
/* L4 报告的用词 → 界面统一用词（同一条生命周期别在两处叫两个名字） */
const L4_LIFE_ZH = { "正在起势": "刚起势" };

function communityReport(r, i) {
  const pill = (kind, txt) => `<span class="pill ${kind}">${txt}</span>`;
  const HEAT_KIND = { "爆": "hot", "热": "warm", "温": "info", "冷": "cool" };
  const title = r.headline || (r.entity_labels || []).slice(0, 3).join(" · ") || "未命名社群";

  const life = Object.entries(r.lifecycles || {});
  const lmx = Math.max(...life.map(([, v]) => v), 1);
  const lifeBars = life.map(([k, v]) => {
    const zh = L4_LIFE_ZH[k] || k;
    const kind = LIFE_KIND_ZH[zh] || "cool";
    return `<div class="crep-life"><span class="n">${v}</span>
      <span class="bar"><i style="--w:${v / lmx * 100}%" data-kind="${kind}"></i></span>
      <span class="k">${esc(zh)}</span></div>`;
  }).join("");

  const plats = Object.entries(r.platforms || {}).slice(0, 4)
    .map(([p, n]) => pill("cool", esc(p) + " " + n)).join("");
  const acts = (r.actions || [])
    .map((a) => `<div class="crep-act"><span>▸</span>${esc(a)}</div>`).join("");
  const evs = (r.events || []).map((e) => {
    const lk = ZH.life[e.lifecycle] || "—";
    const hk = e.heat_kind || HEAT_KIND[e.heat] || "cool";
    return `<div class="trow" data-eid="${esc(e.event_id)}" title="${esc(e.title || "")}">
      <span class="tname">${esc(e.title || "（无标题话题）")}</span>
      <span>${num(e.content_count)}</span>
      <span>${pill(hk, e.heat || "—")}</span>
      <span>${pill(ZH.lifeKind[e.lifecycle] || "cool", lk)}</span></div>`;
  }).join("");
  const more = r.hidden_topics > 0
    ? `<div class="crep-more">另有 ${r.hidden_topics} 个零散小话题，信息量太少没列出</div>` : "";

  return `<div class="crep">
    <div class="crep-head">
      <div class="crep-rank">${String(i + 1).padStart(2, "0")}</div>
      <div style="min-width:0">
        <h3>${esc(title)}</h3>
        <div class="card-sub">${num(r.n_events)} 个话题 · ${num(r.total_content)} 条内容 · 最热话题「${esc(r.max_heat_label || "—")}」档</div>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">${plats}</div>
    </div>
    ${r.narrative ? `<p class="crep-narr">${esc(r.narrative)}</p>` : ""}
    ${lifeBars || acts ? `<div class="crep-mid">
      ${lifeBars ? `<div class="crep-lifes">${lifeBars}</div>` : ""}
      ${acts ? `<div class="crep-acts"><b>可以做什么</b>${acts}</div>` : ""}
    </div>` : ""}
    ${evs ? `<div class="crep-events">
      <div class="trow head"><span>社群内的话题（点开看详情）</span><span>内容</span><span>热度</span><span>阶段</span></div>${evs}</div>` : ""}
    ${more}
  </div>`;
}

/* ============ 详情抽屉：来龙去脉 + 动画可视化 ============ */
async function openDrawer(eid) {
  const d = $("drawer");
  d.classList.add("open");
  $("drawer-inner").innerHTML =
    `<div class="d-sec" style="text-align:center;padding:40px">
      <div class="load" style="margin:0 auto 12px"></div>
      <div class="hint">正在把这条话题的来龙去脉拼出来…</div></div>`;
  try {
    const [ws, studio] = await Promise.all([
      api("/events/" + encodeURIComponent(eid) + "/workspace"),
      api("/events/" + encodeURIComponent(eid) + "/studio").catch(() => null),
    ]);
    const card0 = S.cards.find((c) => c.event_id === eid) || {};
    $("drawer-inner").innerHTML = detailHtml(eid, card0, ws, studio);
    wireDrawer(eid);
  } catch (e) {
    $("drawer-inner").innerHTML =
      `<div class="d-sec"><div class="emptyviz"><div class="big">😶</div>
       <div class="t">${esc(e.message)}<br>这条话题可能还没有跑过深度分析。</div></div></div>`;
  }
}
function closeDrawer() { $("drawer").classList.remove("open"); S.drawer = null; }

function detailHtml(eid, c, ws, studio) {
  const sig = ws.trend_signal || {};
  const ep = ws.evidence_panel || {};
  const creatives = (studio && studio.creatives) || [];
  const left = (studio && studio.left_panel) || {};
  const hist = left.historical_cases || [];
  const life = c.lifecycle || sig.lifecycle || "";
  const w = c.window || {};
  const pill = (kind, txt) => `<span class="pill ${kind}">${txt}</span>`;

  let h = `<div class="d-head">
    <div style="min-width:0">
      <button class="d-back" onclick="closeDrawer()">← 返回列表</button>
      <div class="d-title" style="margin-top:10px">${esc(c.title || sig.title || "话题详情")}</div>
      <div class="d-sub">${num(sig.content_count ?? c.content_count)} 条讨论 ·
        ${num(sig.platform_count ?? c.platform_count)} 个平台 ·
        ${esc((ws.platform_diffusion || []).join(" / ") || "平台未知")}</div>
      <div class="d-pills">
        ${pill(ZH.lifeKind[life] || "cool", ZH.life[life] || "状态未知")}
        ${w.urgency ? pill(ZH.urgKind[w.urgency] || "cool",
            "还能跟：" + ZH.urg[w.urgency]) : ""}
        ${c.workflow && c.workflow.owner ? pill("info", "负责人 " + esc(c.workflow.owner)) : ""}
      </div>
    </div>
  </div>`;

  /* 1) 现在有多热 —— 环形仪表 + 信号拆解（真实数值，带动画） */
  const hot = pct(c.hot_score ?? sig.hot_score);   // 0~1 → 人看的百分制
  const mom = num(c.momentum_score ?? sig.momentum_score);
  const conf = num(c.confidence_score ?? sig.confidence_score);
  const rel = c.relevance;
  h += `<div class="d-sec" style="animation-delay:.05s">
    <h3>现在有多热？</h3>
    <div class="gauge">
      <div class="gauge-main">
        <svg width="132" height="132" viewBox="0 0 132 132">
          <circle cx="66" cy="66" r="54" fill="none" stroke="#263041" stroke-width="11"/>
          <circle cx="66" cy="66" r="54" fill="none" stroke="#4a9eff" stroke-width="11"
            stroke-linecap="round" pathLength="100" stroke-dasharray="100"
            stroke-dashoffset="100" style="--len:100">
            <animate attributeName="stroke-dashoffset" from="100"
              to="${100 - (Number(c.hot_score ?? sig.hot_score) || 0) * 100}"
              dur="1.2s" fill="freeze"/></circle>
        </svg>
        <div class="gauge-num"><div><b>${hot}</b><span>热度分（满分100）</span></div></div>
      </div>
      <div class="gauge-side">
        ${gbar("势头（涨得多快）", mom, "#3ecf8e", "势头低说明讨论没在继续涨")}
        ${gbar("可信度（有多少真证据）", conf, "#a78bfa", "可信度低说明证据还不扎实")}
        ${rel != null ? gbar("跟我们有多大关系", rel, "#f5b544", "相关性低说明跟 TapTap 关系不大") : ""}
      </div>
    </div></div>`;

  /* 2) 来龙去脉 —— 真实时间戳时间线（没有的数据不编） */
  h += `<div class="d-sec" style="animation-delay:.12s">
    <h3>来龙去脉 <span class="sec-tip">时间线来自真实采集时间</span></h3>
    ${timelineHtml(sig, ws)}</div>`;

  /* 3) 系统判断 + 动作 */
  const ai = (ws.what_happened && (ws.what_happened.why_now || ws.what_happened.summary))
          || c.ai_judgement;
  h += `<div class="d-sec" style="animation-delay:.18s">
    <h3>系统怎么看？</h3>
    <div class="ai-box">
      <div class="t">${ai ? esc(ai) : "（这条话题还没有跑过深度分析）"}</div>
      <div class="acts" id="ai-acts"></div>
    </div></div>`;

  /* 4) 证据 —— 事实/推测/不确定分开摆 */
  h += `<div class="d-sec" style="animation-delay:.24s">
    <h3>凭什么这么说？ <span class="sec-tip">事实和推测分开摆，别混着看</span></h3>
    ${evidenceHtml(ep)}</div>`;

  /* 5) 能做的方案 */
  if (creatives.length) {
    h += `<div class="d-sec" style="animation-delay:.3s">
      <h3>可以做的方案 <span class="sec-tip">点一下能看到具体玩法</span></h3>
      <div class="creatives">${creatives.map(creativeCard).join("")}</div></div>`;
  }

  /* 6) 团队以前怎么处理类似情况 */
  if (hist.length) {
    h += `<div class="d-sec" style="animation-delay:.36s">
      <h3>团队以前怎么处理类似情况？</h3>
      ${hist.map((x) => `<div class="item"><div class="item-main">
        <div class="item-title">${esc(x.title || x.memory_id)}</div>
        <div class="item-sub">做法：${esc(x.strategy || "—")}</div></div>
        <span class="pill ${x.reliability > 0.5 ? "good" : "cool"}">
          可信度 ${pct(x.reliability)}</span></div>`).join("")}</div>`;
  }
  return h;
}

function gbar(label, v, color, tip) {
  const p = Math.max(2, Math.min(100, (Number(v) || 0) * 100));
  return `<div class="gbar-row" title="${esc(tip || "")}">
    <span class="gl">${label}</span>
    <span class="gt"><i style="--w:${p}%;background:${color}"></i></span>
    <span class="gv">${pct(v)}</span></div>`;
}

function timelineHtml(sig, ws) {
  const wh = ws.what_happened || {};
  const items = [];
  const push = (title, sub, detail, kind) => items.push({ title, sub, detail, kind });
  if (wh.trigger) push("这是什么", "一句话说清", esc(wh.trigger), "");
  (wh.narratives || []).slice(0, 2).forEach((n) =>
    push("大家在说", "玩家视角", esc(n), ""));
  if (sig.temporal_resolution && sig.temporal_resolution.includes("unavailable")) {
    push("时间上的遗憾", "暂时看不到变化曲线",
        "采集数据里没有连续的时间点，所以画不出「什么时候开始涨」的曲线。" +
        "等采集器连续跑一段时间，这里就会出现真实的增长曲线。", "mute");
  }
  (wh.key_uncertainties || []).slice(0, 2).forEach((u) =>
    push("还不确定", "需要更多信息", esc(u.text || u), "hot"));
  if (!items.length) push("暂无内容", "这条话题还没被分析过", "先跑一次第四层的情报分析", "mute");
  return `<div class="tl">` + items.map((it, i) => `
    <div class="tl-item" style="animation-delay:${0.1 + i * 0.13}s">
      <div class="tl-dot ${it.kind}"></div>
      <div class="tl-t">${it.title}</div>
      <div class="tl-s">${esc(it.sub)}</div>
      <div class="tl-d">${it.detail}</div>
    </div>`).join("") + `</div>`;
}

function evidenceHtml(ep) {
  const row = (kind, icon, tag, text) =>
    `<div class="evi ${kind}"><div class="ic">${icon}</div><div class="evi-b">
      <div class="evi-tag">${tag}</div><div class="evi-tx">${text}</div></div></div>`;
  let h = "";
  const facts = (ep.facts || []).slice(0, 4);
  const comm = (ep.community || []).slice(0, 2);
  const inf = (ep.inferences || []).slice(0, 2);
  const unk = (ep.unknowns || []).slice(0, 2);
  facts.forEach((f, i) => h += row("f", "✓", "已确认 · " + (ZH.tier[f.tier] || "信源"),
    esc(f.excerpt || "")));
  comm.forEach((f, i) => h += row("f", "·", "玩家讨论", esc(f.excerpt || "")));
  inf.forEach((f, i) => h += row("i", "△", "系统推测（别当事实）",
    esc(f.text || f.excerpt || "")));
  unk.forEach((f, i) => h += row("u", "?", "还不确定", esc(f.text || f.excerpt || "")));
  if (!h) h = `<div class="empty">这条话题还没有采集到证据。</div>`;
  return h;
}

function creativeCard(c) {
  return `<div class="creative" data-cid="${esc(c.idea_id)}">
    <span class="score">${pct(c.score)}</span>
    <h4>${esc(c.idea_name || "未命名方案")}</h4>
    <div class="cm">类型：${esc(c.creative_type_name || c.creative_type || "—")}<br>
      ${c.within_window === false ? "⚠️ 时间上来不及" : "✅ 时间上可行"}<br>
      目标：${esc(c.primary_metric || "—")}</div></div>`;
}

/* ============ 抽屉内的动作按钮（全部走命令层，权限服务端判） ============ */
const CAN = {
  follow: ["operator", "analyst", "reviewer", "admin", "publisher"],
  decide: ["reviewer", "admin"],
  assign: ["reviewer", "admin"],
};
const mine = (act) => S.me && CAN[act] && CAN[act].indexOf(S.me.role) >= 0;

function wireDrawer(eid) {
  const box = $("ai-acts");
  if (!box) return;
  const btns = [];
  btns.push(['<button class="btn-go" id="a-follow">我要跟进</button>', mine("follow")]);
  btns.push(['<button class="btn-line" id="a-assign">指派给同事</button>', mine("assign")]);
  btns.push(['<button class="btn-line" id="a-pass">交给评审</button>', mine("follow")]);
  btns.push(['<span class="hint">只有评审和管理员能审批</span>', mine("decide")]);
  box.innerHTML = btns.filter((x) => x[1]).map((x) => x[0]).join("");

  const on = (id, fn) => { const n = $(id); if (n) n.onclick = fn; };
  on("a-follow", () => cmd("/follow " + eid, "已加入跟进清单"));
  on("a-assign", async () => {
    const who = prompt("指派给谁？");
    if (who) cmd("/assign " + eid + " --owner " + who, "已指派给 " + who);
  });
  on("a-pass", async () => {
    const first = (document.querySelector(".creative") || {}).dataset;
    if (first && first.cid) cmd("/submit " + first.cid, "已提交评审");
    else toast("这条话题还没有方案可提交", "err");
  });
  document.querySelectorAll(".creative").forEach((n) => n.onclick = () => {
    toast("方案 " + n.dataset.cid + "：在「执行与实验」里可以排期上线");
  });
}

async function cmd(input, okMsg) {
  try {
    const r = await api("/command", { input, selected: { event_id: S.drawer } });
    if (r.ok) { toast(okMsg + "：" + r.message, "ok"); refreshAll(); }
    else toast(r.message, "err");
  } catch (e) { toast(e.message, "err"); }
}

/* ============ 采集参数（前端可调） ============ */
async function loadCrawlConfig() {
  const d = await api("/crawl-config");
  S.cfg = d;
  $("src-cfg").innerHTML = Object.entries(d.limits || {}).map(([k, [lo, hi]]) => {
    const v = (d.values || {})[k];
    const step = (k === "sleep_min" || k === "sleep_max") ? 0.1 : 1;
    return `<label class="cfg-row"><span>${esc((d.labels || {})[k] || k)}</span>
      <input type="number" data-cfg="${esc(k)}" value="${v}" min="${lo}" max="${hi}" step="${step}">
      <i class="hint">${lo}~${hi}</i></label>`;
  }).join("");
}

async function saveCrawlConfig(reset) {
  const values = {};
  document.querySelectorAll("[data-cfg]").forEach((n) => {
    values[n.dataset.cfg] = n.value;
  });
  if (reset) {
    const d = await api("/crawl-config");
    values = Object.assign({}, d.defaults);
  }
  try {
    const out = await api("/crawl-config", { values });
    S.cfg = out;
    loadCrawlConfig();
    toast("采集参数已保存，下一轮采集自动生效", "ok");
  } catch (e) { toast(e.message, "err"); }
}


/* ============ 增长创意（结构化 schema 渲染） ============ */
async function loadCreatives() {
  const d = await api("/growth-creatives");
  const cs = d.creatives || [];
  const chans = d.channels || {};
  $("cre-stats").innerHTML =
    stat(cs.length, "条创意", "hl") +
    stat(Object.keys(chans).length, "监控渠道") +
    stat((d.forming_game || []).length, "游戏相关形成中", "warn");

  const fg = d.forming_game || [];
  $("cre-forming").innerHTML = fg.length ? fg.map((f) =>
    `<div class="item"><div class="item-main">
       <div class="item-title">${esc(f.word || "")}</div>
       <div class="item-sub">${esc(f.stage_label || "")} · ${f.trend_rate != null ? f.trend_rate + "%" : "新进榜"} · 在榜 ${f.on_board_min || 0} 分</div></div>
       ${pill("hot", "#" + (f.rank || "?"))}</div>`).join("")
    : `<div class="empty">还没有形成中的游戏相关热点<br>采集需要多轮快照（每 10-15 分钟一轮）才能判趋势</div>`;

  $("cre-list").innerHTML = cs.length ? cs.map(creativeCard).join("")
    : `<div class="empty">还没有生成创意。<br>
       先跑 <code>python L4_intelligence/intelligence/hotspot_to_creative.py</code></div>`;
}

function creativeCard(c) {
  const ex = c.execution || {}, cp = c.copy || {};
  const steps = (ex.steps || []).map((s, i) =>
    `<li>${esc(s)}</li>`).join("");
  const assets = (c.assets || []).map((a) =>
    `<span class="pill info">${esc(a.type || "素材")}：${esc(a.desc || "")}</span>`).join("");
  const risks = (c.risks || []).map((r) =>
    `<div class="risk-row ${r.kind || ""}"><span class="lv">${r.level === "high" ? "高" : r.level === "medium" ? "中" : "低"}</span>${esc(r.warning)}</div>`).join("");
  return `<div class="cre-card">
    <div class="cre-head">
      <div class="cre-type">${esc(c.type_zh || c.creative_type)}</div>
      <div style="min-width:0"><h3>${esc(c.name)}</h3>
        <div class="card-sub">面向 ${esc(c.audience || "—")} ·
          ${ex.cost ? (ex.cost === "low" ? "低成本" : ex.cost === "medium" ? "中成本" : "高成本") : "成本未定"} ·
          提前 ${ex.lead_time_hours || "?"}h
          ${ex.window_missed ? pill("hot", "窗口已过") : ""}</div></div>
      <div style="text-align:right">
        ${c.kpi_target ? `<div class="kpi-val">${esc(c.kpi_target)}</div>
          <div class="kpi-lab">目标（${esc(c.primary_metric || "")}）</div>` : ""}
        ${pill("cool", c.hotspot?.game || c.hotspot?.platform || "")}</div>
    </div>
    ${ex.where ? `<div class="cre-where">📍 执行位置：${esc(ex.where)}${ex.owner ? " · 负责：" + esc(ex.owner) : ""}</div>` : ""}
    ${steps ? `<div class="cre-steps"><b>怎么做</b><ol>${steps}</ol></div>` : ""}
    ${cp.headline ? `<div class="cre-copy"><b>文案</b><div class="quote">${esc(cp.headline)}</div>
       ${cp.push_title ? `<div class="push-t">Push 标题：${esc(cp.push_title)}</div>` : ""}</div>` : ""}
    ${assets ? `<div class="cre-assets"><b>需要素材</b><div>${assets}</div></div>` : ""}
    ${risks ? `<div class="cre-risks"><b>风险</b>${risks}</div>` : ""}
    ${c.kpi_basis ? `<div class="cre-kpi">📐 目标依据：${esc(c.kpi_basis)}
        <span class="hint">（参照值，非承诺）</span></div>` : ""}
    ${c.kpi_note ? `<div class="cre-kpi warn">⚠ ${esc(c.kpi_note)}</div>` : ""}
    ${(c.evidence || []).length ? `<div class="cre-src">依据：${c.evidence.map(esc).join(" ／ ")}</div>` : ""}
  </div>`;
}

/* ============ 数据源（L1 采集层全貌） ============ */
async function loadSources() {
  const [d] = await Promise.all([api("/sources"), loadCrawlConfig()]);
  const t = d.totals || {};
  const pair = d.pairing || [];
  const pairTot = pair.reduce((s, p) => s + p.total, 0);
  const pairCov = pair.reduce((s, p) => s + p.covered, 0);
  $("src-stats").innerHTML =
    stat(t.enabled || 0, "启用源", "hl") +
    stat(t.registered || 0, "已注册") +
    stat(num(t.content || 0), "入库内容") +
    stat(num(t.post_like || 0), "帖子/视频") +
    stat(num(t.comment || 0), "评论") +
    stat(pairTot ? Math.round(pairCov / pairTot * 100) + "%" : "—", "配对覆盖", "warn");

  const gi = d.game_index || {};
  $("src-gameindex").innerHTML = gi.covered ? `
    <div class="item"><div class="item-main">
      <div class="item-title">已索引 ${num(gi.total)} 个游戏社区，其中 ${num(gi.addressable)} 个可直接寻址</div>
      <div class="item-sub">app_id → group_id 是 S2 进社区抓帖子的门牌号；接口声明全量 2208，
        无翻页权限时深翻上限约 1016（from ≥ 1010 返回 400）</div>
    </div></div>
    <div class="gi-table">
      <div class="trow head grow"><span>社区</span><span>app_id</span><span>group_id</span><span>关注</span><span>帖子</span><span>近期</span><span>官方</span></div>
      ${(gi.top || []).map((g) => `<div class="trow grow">
        <span class="tname">${esc(g.title)}</span><span>${esc(g.app_id)}</span>
        <span>${esc(g.group_id)}</span><span>${num(g.fav)}</span>
        <span>${num(g.topics)}</span><span>${num(g.recent)}</span>
        <span>${num(g.official)}</span></div>`).join("")}
    </div>` : `<div class="empty">还没有 S1 社区索引<br>跑一次社区爬虫即可生成</div>`;

  const th = Object.entries(d.threads || {});
  $("src-threads").innerHTML = th.length ? th.map(([label, t]) => {
    const cov = t.total ? Math.round(t.comment_covered / t.total * 100) : 0;
    const grow = (t.growing || []).map((g) =>
      `<div class="crep-act"><span>▲</span>${esc(g.title)} <b class="hot-delta">+${g.delta} 评论</b>
        <span class="hint">（现 ${num(g.comments)}）</span></div>`).join("");
    return `<div class="item"><div class="item-main">
      <div class="item-title">${esc(label)}</div>
      <div class="item-sub">${num(t.total)} 个 thread · 在监测 ${num(t.active)} · 计数快照 ${num(t.snapshots)} 条
        · 评论 ${num(t.comments)} 条（覆盖 ${cov}% 的 thread）</div>
      ${grow ? `<div class="grow-box">${grow}</div>` : `<div class="item-sub" style="margin-top:4px">本轮没有计数增长的 thread</div>`}
    </div></div>`;
  }).join("") : `<div class="empty">还没有 thread 监测数据<br>跑一次社区/发现流爬虫即可</div>`;

  $("src-table").innerHTML =
    `<div class="trow head srow"><span>状态</span><span>采集源</span><span>抓什么</span><span>入库</span><span>类型</span><span>最近运行</span></div>` +
    (d.sources || []).map((s) => {
      const run = s.last_run;
      const runTxt = run ? `${(run.started_at || "").slice(5, 16).replace("T", " ")} · ${run.records ?? 0} 条`
                         : "从未运行";
      const types = Object.entries(s.types || {}).map(([k, v]) => `${k} ${v}`).join(" / ") || "—";
      return `<div class="trow srow">
        <span>${pill(s.enabled ? "good" : "cool", s.enabled ? "启用" : "停用")}</span>
        <span class="tname">${esc(s.name)}<i class="src-id">${esc(s.source_id)} · ${esc(s.platform)}</i></span>
        <span class="tname">${esc(s.dataset || "—")}</span>
        <span>${num(s.ingested)}</span>
        <span class="tname">${esc(types)}</span>
        <span class="tname">${esc(runTxt)}</span></div>`;
    }).join("") || `<div class="empty">还没有注册的采集源</div>`;

  $("src-pairing").innerHTML = pair.map((p) => {
    const ratio = p.covered / (p.total || 1);
    return `<div class="brow"><span class="bl">${esc(p.label)}</span>
      <span class="bt"><i style="--w:${Math.max(2, ratio * 100)}%;background:${ratio < 0.5 ? "var(--rd)" : "var(--gn)"}"></i></span>
      <span class="bv">${p.covered}/${p.total}</span></div>`;
  }).join("");
  const cov = t.comment_with_parent || 0, cmt = t.comment || 0;
  $("src-pair-note").innerHTML =
    `评论必须挂在产生它的帖子/视频上（parent_id），否则答不了"这个帖子下面大家在吵什么"。` +
    `当前入库的 <b>${num(cmt)}</b> 条评论里，parent_id 非空的只有 <b>${num(cov)}</b> 条 —— ` +
    `评论在入库时没有连回帖子，且原始抓取的评论覆盖率也低（上面红条）。` +
    `这是第一层优化的第一刀：①抓取时逐帖/逐视频配对拉评论；②入库时把 parent_id 贯通。`;

  $("src-runs").innerHTML = (d.crawl_runs || []).map((r) =>
    `<div class="item"><div class="item-main">
      <div class="item-title">${esc(r.source_id)} · ${r.records ?? 0} 条</div>
      <div class="item-sub">${esc((r.started_at || "").slice(0, 19).replace("T", " "))}${r.error_type ? " · " + esc(r.error_type) : ""}</div></div>
      ${pill(r.status === "ok" ? "good" : "hot", esc(r.status || "—"))}</div>`).join("")
    || `<div class="empty">还没有运行记录</div>`;
}

/* ============ 趋势分析 ============ */
async function loadTrend() {
  const feed = await api("/feed?limit=40");
  S.cards = feed.cards || [];
  const all = S.cards;
  // 热度分布：分档
  const buckets = [
    { lab: "很热 (≥60)", n: 0, c: "#f2555a" },
    { lab: "偏热 (40-60)", n: 0, c: "#f5b544" },
    { lab: "一般 (20-40)", n: 0, c: "#4a9eff" },
    { lab: "偏冷 (<20)", n: 0, c: "#6b7789" },
  ];
  all.forEach((c) => {
    const h = (Number(c.hot_score) || 0) * 100;
    if (h >= 60) buckets[0].n++;
    else if (h >= 40) buckets[1].n++;
    else if (h >= 20) buckets[2].n++;
    else buckets[3].n++;
  });
  const mx = Math.max(...buckets.map((b) => b.n), 1);
  $("trend-bars").innerHTML = buckets.map((b, i) =>
    `<div class="brow"><span class="bl">${b.lab}</span>
     <span class="bt"><i style="--w:${b.n / mx * 100}%;background:${b.c}"></i></span>
     <span class="bv">${b.n}</span></div>`).join("");

  // 温度计：按生命周期分组
  const groups = {};
  all.forEach((c) => {
    const k = ZH.life[c.lifecycle] || "其他";
    groups[k] = (groups[k] || 0) + 1;
  });
  const order = ["刚起势", "正在升温", "到顶了", "重新活跃", "已经降温", "基本平静", "其他"];
  const colors = { "刚起势": "#f5b544", "正在升温": "#f2555a", "到顶了": "#a78bfa",
                   "重新活跃": "#4a9eff", "已经降温": "#6b7789", "基本平静": "#475569",
                   "其他": "#475569" };
  const gmx = Math.max(...Object.values(groups), 1);
  $("trend-thermo").innerHTML = order.filter((k) => groups[k]).map((k, i) =>
    `<div class="trow2"><span class="tl2">${k}</span>
     <span class="tt"><i style="--w:${groups[k] / gmx * 100}%;background:${colors[k]}"></i></span>
     <span class="tv">${groups[k]}</span></div>`).join("") ||
    `<div class="empty">暂无数据</div>`;

  $("trend-table").innerHTML =
    `<div class="trow head"><span>话题</span><span>热度</span><span>势头</span><span>跟我们的关系</span></div>` +
    all.slice(0, 30).map((c, i) =>
      `<div class="trow" data-eid="${esc(c.event_id)}" style="animation:rise .4s ${i * 0.03}s backwards">
        <span class="tname">${esc(c.title || c.event_id)}</span>
        <span>${pct(c.hot_score)}</span><span>${pct(c.momentum_score)}</span>
        <span>${c.relevance == null ? "—" : pct(c.relevance)}</span></div>`).join("");
  $("trend-table").querySelectorAll(".trow[data-eid]").forEach((n) =>
    n.onclick = () => openDrawer(n.dataset.eid));
}

/* ============ 执行与实验 ============ */
async function doMemorySearch() {
  const q = $("mem-q").value.trim();
  if (!q) return;
  try {
    const r = await api("/retrieve?q=" + encodeURIComponent(q) + "&top-k=5");
    const items = r.items || [];
    $("mem-result").innerHTML = items.length ? items.map((it) =>
      `<div class="item"><div class="item-main">
        <div class="item-title">${esc(it.title || it.memory_id)}</div>
        <div class="item-sub">${esc((it.lessons || [])[0]?.lesson || it.strategy || "")}</div></div>
        <span class="pill cool">可信 ${pct(it.reliability)}</span></div>`).join("")
      : `<div class="empty">记忆里还没有「${esc(q)}」相关的经验。<br>
         团队第一次遇到这类话题——做完之后系统会自动记下来。</div>`;
  } catch (e) { $("mem-result").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* ============ 设置 ============ */
async function loadOps() {
  S.ops = await api("/ops-context").catch(() => null);
  const o = S.ops || {}, r = o.resources || {};
  $("set-dev").value = r.dev != null ? r.dev : 0;
  $("set-design").value = r.design != null ? r.design : 1;
  $("set-ops").value = r.ops != null ? r.ops : 3;
  $("set-budget").value = o.budget_level || "low";
  $("set-me").textContent = S.me ? S.me.actor : "—";
  $("set-role").textContent = S.me ? S.me.role : "—";
  const dev = Number(r.dev || 0), design = Number(r.design || 0);
  $("set-derive").innerHTML = dev <= 0
    ? "当前<b>没有研发</b>，系统只会推荐「发内容 / 社群活动 / 推送」这类当天能上的方案。" +
      "<br>要做产品功能的话，得先加人。"
    : design <= 0
      ? "有研发但<b>没设计</b>，可以做功能，但外观类方案会受限。"
      : "资源齐全，系统可以推荐完整方案（含产品功能）。";
}
async function saveOps() {
  try {
    await api("/ops-context", { resources: { dev: +$("set-dev").value,
      design: +$("set-design").value, ops: +$("set-ops").value },
      budget_level: $("set-budget").value, ttl_hours: 24 });
    $("set-saved").textContent = "已保存 ✓";
    toast("团队资源已更新，下一轮分析就会用新配置", "ok");
    loadOps();
  } catch (e) { toast(e.message, "err"); }
}

/* ============ 路由与启动 ============ */
const PAGES = { today: loadToday, creatives: loadCreatives, sources: loadSources, trend: loadTrend };
async function refreshAll() {
  try {
    for (const k of ["today", "exec"]) await PAGES[k]();
  } catch (e) { console.warn(e); }
}
function switchTab(name) {
  S.tab = name;
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".page").forEach((p) =>
    p.classList.toggle("hidden", !p.id.endsWith(name)));
  PAGES[name]().catch((e) => toast(e.message, "err"));
}

function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  S.ws = new WebSocket(proto + "://" + location.host + "/ws");
  S.ws.onopen = () => $("live-dot").classList.add("on");
  S.ws.onclose = () => { $("live-dot").classList.remove("on"); setTimeout(connectWS, 3000); };
  S.ws.onmessage = () => { if (!S.drawer) loadToday().catch(() => {}); };
}

function showApp() {
  $("login").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("me-name").textContent = S.me.actor;
  $("me-role").textContent = S.me.role;
  connectWS();
  loadToday().catch((e) => toast(e.message, "err"));
  loadOps();
}

$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  $("li-err").textContent = "";
  try {
    const r = await fetch("/api/login", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor: $("li-actor").value.trim(),
                             password: $("li-pass").value }) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || "登录失败");
    S.token = d.token; S.me = { actor: d.actor, role: d.role };
    sessionStorage.setItem("token", S.token);
    showApp();
  } catch (err) { $("li-err").textContent = err.message; }
};

document.querySelectorAll(".tab").forEach((t) => t.onclick = () => switchTab(t.dataset.tab));
$("btn-set").onclick = () => { $("setpanel").classList.remove("hidden"); loadOps(); };
$("btn-me").onclick = () => $("setpanel").classList.toggle("hidden");
$("setpanel").onclick = (e) => { if (e.target.id === "setpanel") $("setpanel").classList.add("hidden"); };
$("set-save").onclick = saveOps;
$("btn-logout").onclick = () => { sessionStorage.clear(); location.reload(); };
$("btn-refresh-exec-unused").onclick = () => loadExec().catch((e) => toast(e.message, "err"));
$("btn-refresh-today").onclick = () => loadToday(true).catch((e) => toast(e.message, "err"));
$("btn-refresh-creatives").onclick = () => loadCreatives().catch((e) => toast(e.message, "err"));
$("btn-refresh-sources").onclick = () => loadSources().catch((e) => toast(e.message, "err"));
$("btn-save-cfg").onclick = () => saveCrawlConfig(false);
$("btn-reset-cfg").onclick = () => saveCrawlConfig(true);
$("btn-refresh-mem-unused").onclick = () => loadMemory().catch((e) => toast(e.message, "err"));
$("mem-go").onclick = doMemorySearch;
$("mem-q").addEventListener("keydown", (e) => { if (e.key === "Enter") doMemorySearch(); });
$("drawer").addEventListener("click", (e) => {
  if (e.target.id === "drawer" || e.target.classList.contains("d-back")) closeDrawer();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

(async function boot() {
  if (!S.token) return;
  try { S.me = await api("/me"); showApp(); } catch (_) { sessionStorage.clear(); }
})();
