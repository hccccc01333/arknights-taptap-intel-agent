/* 增长情报台 —— 全中文界面
   这份代码只做两件事：把数据画成人话、把动作按钮接上后端。
   术语翻译表在 ZH 里 —— 后端字段是英文的，人看到的必须是中文的。 */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = (v) => v == null ? "—" : Math.round(Number(v) * 100);
const num = (v) => v == null ? "—" : Number(v).toLocaleString();

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

/* ============ 迷你趋势图（真实数据：热度构成，不是编的曲线） ============ */
function sparkline(hot, mom, conf) {
  const w = 108, h = 30;
  const vals = [hot || 0, mom || 0, conf || 0];
  const mx = Math.max(...vals, 0.1);
  const bw = w / 3 - 5;
  const bars = vals.map((v, i) => {
    const bh = Math.max(3, (v / mx) * (h - 4));
    const c = ["#4a9eff", "#3ecf8e", "#a78bfa"][i];
    return `<rect x="${i * (bw + 5)}" y="${h - bh}" width="${bw}" height="${bh}"
      rx="2" fill="${c}" opacity=".85" style="--w:0%">
      <animate attributeName="opacity" from="0" to=".85" dur=".7s"
        begin="${0.15 + i * 0.12}s" fill="freeze"/></rect>`;
  }).join("");
  return `<svg class="spark" width="${w}" height="${h + 12}" viewBox="0 0 ${w} ${h + 12}">
    ${bars}
    <text x="0" y="${h + 11}" fill="#6b7789" font-size="8">热/势/信</text></svg>`;
}

/* ============ 今日情报 ============ */
async function loadToday() {
  const feed = await api("/feed?limit=40");
  S.cards = feed.cards || [];
  const hot = S.cards.filter((c) => c.group === "ACTION_NOW");
  const watch = S.cards.filter((c) => c.group === "WATCH");
  const cool = S.cards.filter((c) => c.group === "DECLINING");

  $("today-title").textContent = hot.length ? "今天先看这几件事" : "今天没有紧急的事";
  $("today-sub").textContent =
    "从 " + num(S.cards.length) + " 个正在发生的话题里，按「有多值得现在动手」挑出来的";
  $("today-stats").innerHTML =
    stat(hot.length, "值得马上看", "hl") +
    stat(watch.length, "可以看看", "") +
    stat(cool.length, "已经降温", "warn");

  const list = hot.concat(watch).concat(cool);
  $("today-list").innerHTML = list.length ? list.map(card).join("") :
    `<div class="empty">暂时没有正在发生的话题。<br>等采集器跑一轮就有了。</div>`;
  $("today-list").querySelectorAll(".card").forEach((n, i) => {
    n.style.animationDelay = Math.min(i * 0.055, 0.8) + "s";
    n.onclick = () => openDrawer(n.dataset.eid);
  });
}
function stat(n, label, kind) {
  return `<div class="stat ${kind}"><b>${n}</b><span>${label}</span></div>`;
}

function card(c) {
  const life = c.lifecycle || "";
  const w = c.window || {};
  const own = c.workflow && c.workflow.owner;
  const pill = (kind, txt) => `<span class="pill ${kind}">${txt}</span>`;
  return `<div class="card" data-eid="${esc(c.event_id)}">
    <div class="card-top">
      <div style="min-width:0">
        <h3>${esc(c.title || "（无标题话题）")}</h3>
        <div class="card-sub">${num(c.content_count)} 条讨论 · 出现在 ${num(c.platform_count)} 个平台</div>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
        ${pill(ZH.lifeKind[life] || "cool", ZH.life[life] || "状态未知")}
        ${w.urgency ? pill(ZH.urgKind[w.urgency] || "cool", ZH.urg[w.urgency]) : ""}
      </div>
    </div>
    <div class="card-mid">
      <div class="metrics">
        ${metric("热度", c.hot_score, "#4a9eff")}
        ${metric("势头", c.momentum_score, "#3ecf8e")}
        ${metric("可信", c.confidence_score, "#a78bfa")}
        ${c.relevance != null ? metric("跟我们的关系", c.relevance, "#f5b544") : ""}
      </div>
      ${sparkline(c.hot_score, c.momentum_score, c.confidence_score)}
    </div>
    ${c.ai_judgement ? `<div class="card-ai"><span class="ai">💡</span>
      <div><b>系统怎么看：</b>${esc(c.ai_judgement)}</div></div>` : ""}
    <div class="card-foot">
      ${own ? pill("info", "负责人：" + esc(own)) : pill("cool", "还没人负责")}
      ${c.n_opportunities ? pill("good", c.n_opportunities + " 个增长机会") : ""}
      <span class="sp"></span>
      <span class="hint">点开看看来龙去脉 →</span>
    </div>
  </div>`;
}
function metric(label, v, color) {
  const p = Math.max(2, Math.min(100, (Number(v) || 0) * 100));
  return `<div class="metric"><div class="lab">${label}</div>
    <div class="val">${pct(v)}</div>
    <div class="track"><i style="--w:${p}%;background:${color}"></i></div></div>`;
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

/* ============ 趋势分析 ============ */
async function loadTrend() {
  await loadToday();
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
async function loadExec() {
  const [plans, exps, funnel] = await Promise.all([
    api("/plans"), api("/experiments"), api("/funnel").catch(() => null)]);
  const act = (p, txt) => (CAN.act || {})[p];
  $("exec-plans").innerHTML = plans.length ? plans.map((p) =>
    `<div class="item"><div class="item-main">
      <div class="item-title">${esc(p.creative_id)}</div>
      <div class="item-sub">渠道：${esc((p.channels || []).join(" / "))}</div></div>
      <div class="item-act"><span class="pill ${p.status === "live" ? "good" : "cool"}">
        ${ZH.plan[p.status] || p.status}</span></div></div>`).join("")
    : `<div class="empty">还没有活动上线。<br>在话题详情里审批一个方案就能排期。</div>`;
  $("exec-exps").innerHTML = exps.length ? exps.map((e) => {
    const r = e.result_state ? ZH.result[e.result_state] : "还没出结论";
    const kind = e.result_state === "WIN" ? "good" : e.result_state === "LOSS" ? "hot"
      : e.result_state ? "warn" : "cool";
    return `<div class="item"><div class="item-main">
      <div class="item-title">${esc(e.name)}</div>
      <div class="item-sub">${esc(e.result_reason || ("目标指标：" + (e.primary_metric || "—")))}</div></div>
      <span class="pill ${kind}">${ZH.exp[e.status] || e.status} · ${r}</span></div>`;
  }).join("") : `<div class="empty">还没有实验数据。<br>上线活动时可以顺便开一个对照实验。</div>`;

  const f = funnel || {};
  const steps = [["原始信号", f.signals], ["发现话题", f.events], ["深度分析", f.analyzed_events],
                 ["增长机会", f.opportunities], ["方案", f.creatives], ["人工采纳", f.adopted],
                 ["真正上线", f.launched], ["实验有效", f.won]];
  const mx = Math.max(...steps.map((s) => s[1] || 0), 1);
  $("funnel").innerHTML = steps.map(([k, v], i) =>
    `<div class="frow"><span class="fl">${k}</span>
     <span class="ft"><i style="--w:${Math.max(2, (v || 0) / mx * 100)}%"></i></span>
     <span class="fv">${num(v)}</span></div>`).join("");
}

/* ============ 团队记忆 ============ */
async function loadMemory() {
  const [val, tta] = await Promise.all([
    api("/value").catch(() => null), api("/tta").catch(() => null)]);
  const v = val || {};
  const cards = [
    { n: (v.funnel || {}).adopted, cap: "人工采纳过的方案", note: "被人真正点头的创意" },
    { n: (v.funnel || {}).won, cap: "实验证明有效", note: "有对照组和数据的胜利" },
    { n: tta ? (tta.n_action || 0) : null, cap: "走完上线流程", note: "从发现到上线的次数" },
    { n: tta && tta.time_to_action_h != null ? tta.time_to_action_h + "h" : null,
      cap: "平均决策耗时", note: "越短说明团队越快" },
  ];
  $("mem-cards").innerHTML = cards.map((c, i) =>
    `<div class="mem" style="animation-delay:${i * 0.06}s">
      <div class="num">${c.n == null ? "—" : c.n}</div>
      <div class="cap">${c.cap}</div><div class="note">${c.note}</div></div>`).join("");
  if (!$("mem-result").innerHTML.trim()) {
    $("mem-result").innerHTML = `<div class="empty">搜一下以前的做法，
      比如「联动」「排行榜」「投稿」。</div>`;
  }
}
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
const PAGES = { today: loadToday, trend: loadTrend, exec: loadExec, memory: loadMemory };
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
$("btn-refresh-exec").onclick = () => loadExec().catch((e) => toast(e.message, "err"));
$("btn-refresh-mem").onclick = () => loadMemory().catch((e) => toast(e.message, "err"));
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
