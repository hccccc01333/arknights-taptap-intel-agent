# -*- coding: utf-8 -*-
"""离线工作台（§35：只做 5 个核心页面）。

    Intelligence Feed / Trend Workspace / Opportunity & Creative /
    Execution & Experiment / Learning Dashboard

双击即开（离线纪律：单文件、零 CDN、数据 JSON 内嵌；与项目原有离线看板同款）。
**只读镜像**：所有写操作走 CLI（权限矩阵在写路径强制，静态页面不掌权限）——
页面顶部如实标明。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict

WORKBENCH_VERSION = "workbench-1.0"

_OUT_DIR_NAME = "workbench"
_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Growth Intelligence OS · 工作台</title>
<style>
  :root { --bg:#0f172a; --card:#1e293b; --ink:#e2e8f0; --dim:#94a3b8;
          --hot:#f87171; --good:#34d399; --warn:#fbbf24; --accent:#38bdf8; }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--bg); color:var(--ink);
         font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif; padding:16px; }
  nav { display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }
  nav button { background:var(--card); color:var(--ink); border:1px solid #334155;
               padding:8px 14px; border-radius:8px; cursor:pointer; }
  nav button.active { border-color:var(--accent); color:var(--accent); }
  .note { color:var(--dim); font-size:12px; margin-bottom:12px; }
  .card { background:var(--card); border:1px solid #334155; border-radius:10px;
          padding:12px 14px; margin-bottom:10px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:10px; }
  h1 { font-size:18px; margin-bottom:4px; } h3 { font-size:14px; margin:8px 0 4px; }
  .dim { color:var(--dim); } .hot { color:var(--hot);} .good{color:var(--good);}
  .warn { color:var(--warn);} .accent { color:var(--accent); }
  .row { display:flex; gap:12px; flex-wrap:wrap; }
  .pill { border:1px solid #334155; border-radius:999px; padding:1px 10px;
          font-size:12px; color:var(--dim); display:inline-block; margin:2px 4px 2px 0; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  td,th { padding:4px 8px; border-bottom:1px solid #334155; text-align:left; }
  select, input { background:#0f172a; color:var(--ink); border:1px solid #334155;
                  border-radius:6px; padding:3px 6px; }
</style>
</head>
<body>
<h1>Growth Intelligence OS <span class="dim">· Level 2（AI 准备 · 人工审批 · 系统执行）</span></h1>
<div class="note">只读镜像：写操作（决策/发布/实验）走 L6_execution/execution/pipeline.py ——
权限矩阵在写路径强制（§43），静态页面不掌权限。生成时间：__GENERATED_AT__</div>
<nav>
  <button data-v="feed" class="active">Intelligence Feed</button>
  <button data-v="workspace">Trend Workspace</button>
  <button data-v="creative">Opportunity &amp; Creative</button>
  <button data-v="execution">Execution &amp; Experiment</button>
  <button data-v="learning">Learning Dashboard</button>
</nav>
<div id="app"></div>
<script>
const D = __DATA__;
const $ = (h) => { const d = document.createElement('div'); d.innerHTML = h; return d; };
const esc = (s) => String(s ?? '—').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const num = (v, digits=0) => (v==null ? '—' : (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString() : Number(v).toFixed(digits)));

function feed() {
  const f = D.feed || {cards:[]};
  const group = (g, label) => {
    const cards = f.cards.filter(c => c.group === g);
    if (!cards.length) return '';
    return `<h3>${label}</h3><div class="grid">` + cards.map(c => `
      <div class="card">
        <b>${esc(c.title)}</b> <span class="pill">${esc(c.lifecycle)}</span>
        ${c.workflow.owner ? `<span class="pill">Owner: ${esc(c.workflow.owner)}</span>` : '<span class="pill warn">Owner: 未分配</span>'}
        <div class="row dim" style="margin:4px 0">
          <span>Hot ${num(c.hot_score,2)}</span><span>Momentum ${num(c.momentum_score,2)}</span>
          <span>Confidence ${num(c.confidence_score,2)}</span>
          <span class="accent">Relevance ${c.relevance==null?'—':num(c.relevance,2)}</span>
          <span>ActionPriority ${num(c.priority,3)}</span>
        </div>
        <div class="dim">窗口: ${esc(c.window.urgency)} · 可上线类型 ≤${num(c.window.max_lead_time_hours)}h
          ${c.window.basis?'<span class="pill">降级口径</span>':''}</div>
        <div>${(c.allowed_creatives||[]).slice(0,4).map(t=>`<span class="pill">${esc(t.name)}</span>`).join('')}</div>
        ${c.top_opportunities.length?`<div class="dim">机会: ${c.top_opportunities.map(o=>esc(o.name)).join(' / ')}</div>`:''}
      </div>`).join('') + '</div>';
  };
  return `<h1>今天先干什么</h1>` +
    group('ACTION_NOW','🔥 ACTION NOW') + group('WATCH','👀 WATCH') + group('DECLINING','📉 DECLINING');
}

function workspace() {
  const sel = `<select id="ws-sel">` + (D.events||[]).map(e =>
    `<option value="${esc(e.event_id)}">${esc(e.title)}</option>`).join('') + `</select>`;
  const render = () => {
    const id = document.getElementById('ws-sel').value;
    const w = (D.workspaces||{})[id];
    if (!w) { document.getElementById('ws-body').innerHTML = '<div class="card dim">没有该事件的分析（L4 未跑）</div>'; return; }
    const ep = w.evidence_panel || {};
    document.getElementById('ws-body').innerHTML = `
      <div class="card"><b>${esc(w.what_happened.title)}</b>
        <div class="dim">${esc(w.what_happened.summary)}</div>
        <div class="row" style="margin-top:6px">
          <span class="pill">${esc(w.trend_signal.lifecycle)}</span>
          <span class="pill">Hot ${num(w.trend_signal.hot_score,2)}</span>
          <span class="pill">Momentum ${num(w.trend_signal.momentum_score,2)}</span>
          <span class="pill">Confidence ${num(w.trend_signal.confidence_score,2)}</span>
          <span class="pill">内容 ${num(w.trend_signal.content_count)}</span>
          <span class="pill">平台 ${(w.platform_diffusion||[]).join('/')||'—'}</span>
        </div>
        <div class="dim" style="margin-top:4px">${esc(w.trend_signal.temporal_resolution)}</div>
      </div>
      <div class="card"><h3>Evidence Panel（事实 / 推断 / 未知）</h3>
        <div class="dim good">✓ 官方/权威事实: ${(ep.facts||[]).length} 条 · 社区证据 ${(ep.community||[]).length} 条</div>
        ${(ep.facts||[]).slice(0,3).map(e=>`<div class="dim">· [${esc(e.tier)}] ${esc(e.excerpt)}</div>`).join('')}
        <div class="dim warn">△ 推断（不当事实用）: ${(ep.inferences||[]).length} 条</div>
        ${(ep.inferences||[]).slice(0,2).map(e=>`<div class="dim">· ${esc(e.text||e)}</div>`).join('')}
        <div class="dim hot">⚠ 未知/未解: ${(ep.unknowns||[]).length} 条</div>
        ${(ep.unknowns||[]).slice(0,3).map(e=>`<div class="dim">· ${esc(e.text||e)}</div>`).join('')}
      </div>
      <div class="card"><h3>受众</h3>${(w.audience||[]).map(a=>`<span class="pill">${esc(a.segment||a)}</span>`).join('')}</div>`;
  };
  document.getElementById('app').innerHTML = `<h1>Trend Workspace</h1>${sel}<div id="ws-body"></div>`;
  document.getElementById('ws-sel').onchange = render; render();
}

function creative() {
  const rows = (D.studios||[]).map(s => {
    const cs = (s.creatives||[]).map(c => `
      <tr><td>${esc(c.idea_name)}</td><td>${esc(c.creative_type_name||c.creative_type)}</td>
      <td>${num(c.score,2)}</td><td>${c.within_window?'<span class="good">窗口内</span>':'<span class="hot">超窗</span>'}</td>
      <td class="dim">${esc((c.growth_hypothesis||'')).slice(0,60)}</td></tr>`).join('');
    const hist = (s.left_panel.historical_cases||[]).slice(0,3).map(h=>
      `<div class="dim">· ${esc(h.title||h.memory_id)} ${h.reliability!=null?`(可靠性 ${num(h.reliability,2)})`:''}</div>`).join('');
    return `<div class="card"><b>${esc(s.event_id)}</b>
      <div class="dim">约束: 研发 ${esc(s.left_panel.constraints.ops_resources?.dev ?? '—')} ·
        最长 lead_time ${num(s.left_panel.constraints.max_lead_time_hours)}h
        <span class="pill">ops_context §39</span></div>
      ${hist?`<div style="margin:4px 0"><h3>历史案例（L5）</h3>${hist}</div>`:''}
      <table><tr><th>创意</th><th>类型</th><th>评分</th><th>窗口</th><th>假设</th></tr>${cs}</table></div>`;
  }).join('');
  return `<h1>Opportunity &amp; Creative Studio</h1>${rows || '<div class="card dim">L4 暂无创意产出</div>'}`;
}

function execution() {
  const plans = (D.plans||[]).map(p => `<tr><td>${esc(p.plan_id)}</td>
    <td>${esc(p.creative_id)}</td><td>${esc((p.channels||[]).join('/'))}</td>
    <td>${esc(p.status)}</td></tr>`).join('');
  const exps = (D.experiments||[]).map(e => {
    const st = {'WIN':'good','LOSS':'hot','STOPPED':'warn','INVALID':'dim'}[e.result_state] || 'dim';
    return `<tr><td>${esc(e.name)}</td><td>${esc(e.status)}</td>
      <td class="${st}">${esc(e.result_state||'—')}</td>
      <td class="dim">${esc(e.result_reason||'')}</td></tr>`;
  }).join('');
  return `<h1>Execution &amp; Experiment</h1>
    <div class="card"><h3>执行计划</h3><table><tr><th>Plan</th><th>创意</th><th>渠道</th><th>状态</th></tr>${plans||'<tr><td colspan=4 class=dim>暂无</td></tr>'}</table></div>
    <div class="card"><h3>实验（失败也是结论，§30）</h3><table><tr><th>实验</th><th>状态</th><th>结果</th><th>说明</th></tr>${exps||'<tr><td colspan=4 class=dim>暂无</td></tr>'}</table></div>
    <div class="card"><h3>告警</h3>${(D.alerts||[]).map(a=>`
      <div class="card"><b class="${a.tier==='P0'?'hot':'warn'}">${esc(a.body&&a.body.headline||a.title)}</b>
      <div class="dim">${esc(a.body&&a.body.recommended_action||'')}</div>
      <div class="pill">${esc(a.basis||'')}</div></div>`).join('') || '<div class="dim">暂无待确认告警</div>'}</div>`;
}

function learning() {
  const v = D.value || {};
  const f = v.funnel || {};
  const steps = [["信号",f.signals],["事件",f.events],["已分析事件",f.analyzed_events],
                 ["机会",f.opportunities],["创意",f.creatives],["采纳",f.adopted],
                 ["上线",f.launched],["WIN",f.won]];
  return `<h1>Learning Dashboard —— 系统创造了什么价值</h1>
    <div class="card"><h3>转化漏斗（§34，全真实数据）</h3><div class="row">
      ${steps.map(([k,n],i)=>`<div><div class="accent" style="font-size:20px">${n==null?'—':num(n)}</div>
        <div class="dim">${k}</div></div>${i<steps.length-1?'<div class="dim">→</div>':''}`).join('')}
    </div></div>
    <div class="card"><h3>时间与质量（§13/§33）</h3><table>
      <tr><th>Median Time-to-Insight</th><td>${v.median_time_to_insight_h==null?'样本不足':v.median_time_to_insight_h+' h'}</td></tr>
      <tr><th>Median Time-to-Decision</th><td>${v.median_time_to_decision_h==null?'样本不足':v.median_time_to_decision_h+' h'}</td></tr>
      <tr><th>Median Time-to-Action</th><td>${v.median_time_to_action_h==null?'样本不足':v.median_time_to_action_h+' h'}</td></tr>
      <tr><th>Creative Adoption</th><td>${v.creative_adoption==null?'—':(v.creative_adoption*100).toFixed(1)+'%'}</td></tr>
      <tr><th>Positive Experiment Rate</th><td>${v.positive_experiment_rate==null?'—':(v.positive_experiment_rate*100).toFixed(1)+'%'}</td></tr>
    </table>
    <div class="dim" style="margin-top:6px">${esc(v.incremental_note||'')}</div></div>`;
}

const views = {feed, workspace, creative, execution, learning};
// 统一渲染入口：视图函数返回 HTML（workspace 自行渲染并返回空），
// 分发器负责写入 #app —— 避免"return 了却没人挂载"的静默空白。
function show(name) {
  document.querySelectorAll('nav button').forEach(x => x.classList.remove('active'));
  const btn = document.querySelector('nav button[data-v="' + name + '"]');
  if (btn) btn.classList.add('active');
  const html = views[name]();
  if (html) document.getElementById('app').innerHTML = html;
}
document.querySelectorAll('nav button').forEach(b => b.onclick = () => show(b.dataset.v));
show('feed');
</script>
</body>
</html>
"""


def generate(app: Any) -> Dict[str, Any]:
    """从真实库装配五个页面的数据并写出 HTML。返回产物路径。"""
    data: Dict[str, Any] = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    if app.feed:
        data["feed"] = app.feed.build(limit=50)
        data["events"] = [{"event_id": c["event_id"], "title": c["title"]}
                          for c in data["feed"]["cards"]]
        workspaces = {}
        studios = []
        for c in data["feed"]["cards"][:20]:
            if app.ws and c.get("has_analysis"):
                w = app.ws.trend(c["event_id"])
                if w:
                    workspaces[c["event_id"]] = w
                s = app.ws.creative_studio(c["event_id"])
                if s and (s.get("creatives") or []):
                    studios.append(s)
        data["workspaces"] = workspaces
        data["studios"] = studios
    plans = app.db.query("SELECT plan_id, creative_id, channels, status FROM execution_plan"
                         " ORDER BY updated_at DESC LIMIT 50")
    for p in plans:
        p["channels"] = app.db.loads(p.get("channels"))
    data["plans"] = plans
    data["experiments"] = app.db.query(
        "SELECT name, status, result_state, result_reason FROM experiment"
        " ORDER BY updated_at DESC LIMIT 50")
    data["alerts"] = app.alerts.pending()
    if app.lineage and app.up:
        try:
            data["value"] = app.lineage.value(app.wf, app.up)
        except Exception as e:
            data["value"] = {"error": str(e)}
    html = (_HTML.replace("__GENERATED_AT__", str(data.get("generated_at")))
            .replace("__DATA__", json.dumps(data, ensure_ascii=False, default=str)))
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           _OUT_DIR_NAME)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return {"workbench": out, "pages": ["feed", "workspace", "creative",
                                        "execution", "learning"],
            "cards": len((data.get("feed") or {}).get("cards") or [])}
